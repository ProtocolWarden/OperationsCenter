# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""QueueHealingTask — run the deterministic blocked-queue healer inside the live
maintenance loop, so the controller recycles stuck/blocked tasks every cycle with
NO human in the loop (inventory #4).

The 5-rule engine in ``queue_healing/engine.py`` plus the issue→task mapping in
``triage_scan.py`` were complete and tested, but reachable only via the
``operations-center-triage-scan`` operator CLI — which is registered nowhere in
``spec_hygiene`` maintenance and triggered by no systemd unit. In practice the
healer never ran. This wraps the SAME tested helpers
(``_queue_healing_actions`` + ``apply_queue_healing_actions``) in the
``MaintenanceTask`` contract.

NON-DESTRUCTIVE + IDEMPOTENT by construction. The engine only ever returns one
of: NONE (no-op), ESCALATE (comment only), BLOCKED→READY_FOR_AI, or
BLOCKED→BACKLOG — and only for ``retry_safe`` tasks within the recovery/retry
budgets. No task is deleted. Re-running on an already-recycled task is a no-op
(it is no longer Blocked, so it doesn't match).

FAIL-SAFE-DEFAULT: ``enabled`` defaults to False so the maintenance registry
never schedules this task (``MaintenanceRegistry._is_due`` returns False for a
disabled task). Operators activate it via ``settings.queue_healing_enabled:
true``. When enabled it still no-ops on a board with no retry-safe Blocked tasks.
"""

from __future__ import annotations

import logging
import time

from operations_center.adapters.plane import PlaneClient
from operations_center.config.settings import Settings
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceResult

from .triage_scan import _queue_healing_actions, apply_queue_healing_actions

logger = logging.getLogger(__name__)

# 10 minutes — mirrors board_unblock: prompt enough to recycle a stuck task,
# loose enough not to hammer Plane.
DEFAULT_INTERVAL_SECONDS = 600


class QueueHealingTask:
    """MaintenanceTask: run the deterministic blocked-queue healer each cycle."""

    name = "queue_healing"

    def __init__(
        self,
        settings: Settings,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool | None = None,
        apply: bool = True,
        plane_client: PlaneClient | None = None,
    ) -> None:
        self._settings = settings
        self.interval_seconds = interval_seconds
        # Fail-safe: disabled unless the operator flips queue_healing_enabled.
        self.enabled = (
            bool(getattr(settings, "queue_healing_enabled", False))
            if enabled is None
            else bool(enabled)
        )
        self._apply = apply
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
        details: dict[str, object] = {"apply": self._apply}

        client = ctx.resources.get("plane_client") or self._make_plane_client()
        owns_client = "plane_client" not in ctx.resources and self._plane_client is None
        try:
            try:
                issues = client.list_issues()
            except Exception as exc:  # noqa: BLE001 — uniform failure surface
                return MaintenanceResult(
                    name=self.name,
                    status="failed",
                    duration_seconds=time.monotonic() - started,
                    details=details,
                    error=f"plane_fetch_failed: {exc}",
                )

            decisions = _queue_healing_actions(issues, now=ctx.now)
            results = apply_queue_healing_actions(client, decisions, apply=self._apply)

            applied = [
                r
                for r in results
                if r.get("action") in {"transitioned", "escalation_commented"}
            ]
            details["scanned"] = len(issues)
            details["decisions"] = len(results)
            details["applied"] = len(applied)
            details["actions"] = results[:50]
            return MaintenanceResult(
                name=self.name,
                status="ok",
                duration_seconds=time.monotonic() - started,
                details=details,
            )
        finally:
            if owns_client:
                client.close()


__all__ = ["DEFAULT_INTERVAL_SECONDS", "QueueHealingTask"]
