# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""ParkedUnparkTask — the oversight-loop consumer for the parked-state machine
(inventory #5).

``recovery/parked.py`` (ParkedState, should_unpark), ``recovery/parked_store.py``
(ParkedStateStore), and ``recovery_policies/budget.py`` (RecoveryBudgetTracker)
were built and test-covered but had no consumer: nothing ever loaded a parked
state, re-evaluated it, or unparked it. A root cause that got parked (e.g. the
oversight loop stopped re-attempting a SIGKILL-looping lineage to avoid burning
budget) would stay parked forever even after the underlying condition cleared.

This task closes that loop. Each cycle it:
  1. loads the single ParkedState from the store (path from settings),
  2. recomputes the current semantic evidence fingerprint from the live board
     (the Blocked-task subset — the same signal the parking decision is about),
  3. calls ``should_unpark`` with that fingerprint, and
  4. records a cycle against a ``RecoveryBudgetTracker``.

When ``should_unpark`` says the item is no longer parked (semantic evidence
changed, or an unpark condition was met), the store is cleared — the next
oversight pass is free to re-attempt the lineage. When it stays parked, the
state is re-saved with ``unchanged_cycles`` incremented and ``last_evidence_hash``
refreshed so the next cycle compares against the latest evidence.

FAIL-SAFE-DEFAULT:
  * EMPTY STORE → no-op. With no parked state on disk the task returns
    ``ok`` with ``parked=False`` and mutates nothing — the universal case on a
    healthy fleet (no producer parks anything today).
  * ``enabled`` defaults to False so the maintenance registry never schedules
    this task. Operators activate it via ``settings.parked_unpark_enabled: true``.
  * It only ever CLEARS or REFRESHES the parked-state sidecar; it never touches
    Plane issues, never deletes board work, and never escalates on its own.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config.settings import Settings
from operations_center.evidence_fingerprints import evidence_fingerprint
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceResult
from operations_center.recovery import ParkedStateStore, should_unpark
from operations_center.recovery_policies import RecoveryBudgetTracker

logger = logging.getLogger(__name__)

# 10 minutes — re-evaluate a parked root cause promptly without hammering Plane.
DEFAULT_INTERVAL_SECONDS = 600

# Default location of the parked-state sidecar, relative to the OC repo root.
DEFAULT_PARKED_STORE_PATH = Path("state/recovery/parked.json")


def _board_evidence(issues: list[dict]) -> dict:
    """Build the semantic-evidence payload the parking decision is about.

    The parked-state machine cares about the Blocked-task shape of the board
    (the thing the oversight loop gave up re-attempting). We project each
    Blocked issue to ``{id, state}`` and let ``evidence_fingerprint`` canonicalize
    away ordering + timestamp noise, so a fingerprint changes only when the
    *semantic* blocked set changes — exactly the "semantic evidence changed"
    unpark trigger.
    """
    blocked = []
    for issue in issues:
        state = issue.get("state")
        name = state.get("name") if isinstance(state, dict) else state
        if str(name or "").strip().lower() == "blocked":
            blocked.append({"id": str(issue.get("id")), "state": "Blocked"})
    return {"blocked": blocked}


class ParkedUnparkTask:
    """MaintenanceTask: re-evaluate + unpark the parked-state machine each cycle."""

    name = "parked_unpark"

    def __init__(
        self,
        settings: Settings,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool | None = None,
        store_path: Path | None = None,
        plane_client: PlaneClient | None = None,
    ) -> None:
        self._settings = settings
        self.interval_seconds = interval_seconds
        # Fail-safe: disabled unless the operator flips parked_unpark_enabled.
        self.enabled = (
            bool(getattr(settings, "parked_unpark_enabled", False))
            if enabled is None
            else bool(enabled)
        )
        self._store_path = store_path or DEFAULT_PARKED_STORE_PATH
        self._plane_client = plane_client

    def _make_plane_client(self) -> PlaneClient:
        if self._plane_client is not None:
            return self._plane_client
        p = self._settings.plane
        return PlaneClient(
            base_url=p.base_url,
            api_token=self._settings.plane_token(),
            workspace_slug=p.workspace_slug,
            project_id=p.project_id,
        )

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()

        store = ParkedStateStore(self._store_path)
        try:
            parked = store.load()
        except Exception as exc:  # noqa: BLE001 — a corrupt sidecar must not crash the loop
            return MaintenanceResult(
                name=self.name,
                status="failed",
                duration_seconds=time.monotonic() - started,
                details={"store_path": str(self._store_path)},
                error=f"parked_store_load_failed: {exc}",
            )

        # FAIL-SAFE no-op: nothing is parked. The healthy-fleet case.
        if parked is None:
            return MaintenanceResult(
                name=self.name,
                status="ok",
                duration_seconds=time.monotonic() - started,
                details={"parked": False, "store_path": str(self._store_path)},
            )

        client = ctx.resources.get("plane_client") or self._make_plane_client()
        owns_client = "plane_client" not in ctx.resources and self._plane_client is None
        try:
            try:
                issues = client.list_issues()
            except Exception as exc:  # noqa: BLE001
                return MaintenanceResult(
                    name=self.name,
                    status="failed",
                    duration_seconds=time.monotonic() - started,
                    details={"parked": True, "store_path": str(self._store_path)},
                    error=f"plane_fetch_failed: {exc}",
                )

            current_hash = evidence_fingerprint(_board_evidence(issues))
            decision = should_unpark(parked, current_evidence_hash=current_hash)

            tracker = RecoveryBudgetTracker()
            evidence_changed = current_hash != parked.last_evidence_hash
            budget = tracker.record_cycle(evidence_changed=evidence_changed)

            details: dict[str, object] = {
                "store_path": str(self._store_path),
                "root_cause_signature": parked.root_cause_signature,
                "evidence_changed": evidence_changed,
                "unpark_reason": decision.reason,
                "budget_allowed": budget.allowed,
                "budget_escalate": budget.escalate,
            }

            if not decision.parked:
                # Unparked: clear the sidecar so the oversight loop re-attempts.
                store.clear()
                details["parked"] = False
                details["unparked"] = True
                logger.info(
                    '{"event": "parked_state_unparked", "root_cause": %r, "reason": %r}',
                    parked.root_cause_signature,
                    decision.reason,
                )
            else:
                # Still parked: refresh evidence hash + bump unchanged_cycles so
                # the next cycle compares against the latest board evidence.
                from dataclasses import replace

                store.save(
                    replace(
                        parked,
                        unchanged_cycles=decision.unchanged_cycles,
                        last_evidence_hash=current_hash,
                    )
                )
                details["parked"] = True
                details["unparked"] = False
                details["unchanged_cycles"] = decision.unchanged_cycles

            return MaintenanceResult(
                name=self.name,
                status="ok",
                duration_seconds=time.monotonic() - started,
                details=details,
            )
        finally:
            if owns_client:
                client.close()


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_PARKED_STORE_PATH",
    "ParkedUnparkTask",
]
