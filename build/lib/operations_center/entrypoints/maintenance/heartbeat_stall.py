# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Controller-tier stall detector for the watcher fleet (liveness-blind gap).

The bash watchdog only checks ``kill -0 $pid`` — a live PID reads as healthy. The
old heartbeats only recorded ``at`` (written every cycle, success or failure), so
a watcher that catches-and-continues on every poll wrote a perfectly fresh
"active" heartbeat while being 100% broken. That combination hid the 2026-06-21
reviewer outage (813 identical token failures, 0 restarts, heartbeat still
"active") from every supervisor in the system.

``heartbeat.py`` now records ``last_success_at`` separately from ``at``. This task
reads each role's heartbeat each cycle and flags the **live-but-not-succeeding**
state — ``at`` fresh (the loop is iterating) but ``last_success_at`` stale and a
run of consecutive failures — that neither the PID watchdog nor the old heartbeat
could see. A stall auto-opens a deduplicated board fix task (same pattern as
``EgressProbeTask``); a healthy or idle fleet returns ``ok``.

Fail-open (§0.1): a missing heartbeat is treated as "not yet observed", not a
stall (a just-restarted lane has no heartbeat); a stale ``at`` is a dead/hung
process and is the supervisor's concern, not a stall.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from operations_center.entrypoints.heartbeat import (
    is_live,
    read_heartbeat,
    success_stalled,
)
from operations_center.maintenance.contracts import MaintenanceResult

if TYPE_CHECKING:
    from operations_center.adapters.plane.client import PlaneClient
    from operations_center.maintenance.contracts import MaintenanceContext

DEFAULT_INTERVAL_SECONDS = 300
# Roles whose heartbeats we monitor. "review" is the reviewer; the rest are the
# board_worker lanes + the spec_hygiene/spec_trigger maintenance watchers.
DEFAULT_ROLES = ("review", "goal", "test", "improve", "propose", "spec-author")
# A lane is "live" if its loop iterated within this window. Generous: the slowest
# poll interval is ~120s; 10min tolerates a long in-flight dispatch.
DEFAULT_MAX_LIVENESS_SECONDS = 600
# Flag a stall when the lane has not had a successful cycle in this long despite
# running. 30min is well past any single dispatch/CI wait.
DEFAULT_MAX_SUCCESS_AGE_SECONDS = 1800
DEFAULT_MIN_CONSECUTIVE_FAILURES = 5

_FIX_TITLE_PREFIX = "[heartbeat-stall] watcher live but not succeeding"
_FIX_LABELS = ("kind:improve", "repo:OperationsCenter", "source:heartbeat-stall")
_TERMINAL_STATES = {"done", "cancelled", "canceled"}


class HeartbeatStallTask:
    """Detect watchers that are running but making no progress, and alert."""

    name = "heartbeat_stall"

    def __init__(
        self,
        settings: Any,
        *,
        status_dir: Any = None,
        roles: tuple[str, ...] = DEFAULT_ROLES,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
        max_liveness_seconds: float = DEFAULT_MAX_LIVENESS_SECONDS,
        max_success_age_seconds: float = DEFAULT_MAX_SUCCESS_AGE_SECONDS,
        min_consecutive_failures: int = DEFAULT_MIN_CONSECUTIVE_FAILURES,
        plane_client: PlaneClient | None = None,
    ) -> None:
        self._settings = settings
        self._status_dir = status_dir
        self._roles = roles
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._max_liveness = max_liveness_seconds
        self._max_success_age = max_success_age_seconds
        self._min_failures = min_consecutive_failures
        self._plane_client = plane_client

    def _resolve_status_dir(self):
        from pathlib import Path

        if self._status_dir is not None:
            return Path(self._status_dir)
        # board_worker/main.py and pr_review_watcher resolve the same default.
        return Path(__file__).resolve().parents[3] / "logs" / "local" / "watch-all"

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()
        status_dir = self._resolve_status_dir()
        now = ctx.now

        stalled: list[dict[str, object]] = []
        observed: list[str] = []
        for role in self._roles:
            hb = read_heartbeat(status_dir, role)
            if hb is None:
                continue  # never observed / just restarted — not a stall
            observed.append(role)
            if not is_live(hb, now=now, max_liveness_seconds=self._max_liveness):
                continue  # stale `at` => dead/hung process => supervisor's concern
            if success_stalled(
                hb,
                now=now,
                max_success_age_seconds=self._max_success_age,
                min_consecutive_failures=self._min_failures,
            ):
                stalled.append(
                    {
                        "role": role,
                        "last_success_at": hb.get("last_success_at"),
                        "consecutive_failures": hb.get("consecutive_failures"),
                        "last_error": hb.get("last_error"),
                    }
                )

        details: dict[str, object] = {
            "status_dir": str(status_dir),
            "observed_roles": observed,
            "stalled": stalled,
        }
        if not stalled:
            return self._result("ok", started, details)

        details["fix_task"] = self._open_fix_task(ctx, stalled)
        roles_str = ", ".join(str(s["role"]) for s in stalled)
        return self._result(
            "failed", started, details, error=f"watcher(s) live but stalled: {roles_str}"
        )

    def _make_plane_client(self) -> PlaneClient:
        if self._plane_client is not None:
            return self._plane_client
        from operations_center.adapters.plane.client import PlaneClient

        p = self._settings.plane
        return PlaneClient(
            base_url=p.base_url,
            api_token=self._settings.plane_token(),
            workspace_slug=p.workspace_slug,
            project_id=p.project_id,
        )

    def _open_fix_task(
        self, ctx: MaintenanceContext, stalled: list[dict[str, object]]
    ) -> str:
        client = ctx.resources.get("plane_client") or self._make_plane_client()
        try:
            for issue in client.list_issues():
                name = str(issue.get("name", ""))
                if not name.startswith(_FIX_TITLE_PREFIX):
                    continue
                state = issue.get("state")
                state_name = (
                    state.get("name", "") if isinstance(state, dict) else str(state or "")
                )
                if state_name.strip().lower() not in _TERMINAL_STATES:
                    return f"exists:{issue.get('id')}"

            lines = [
                f"- **{s['role']}**: {s['consecutive_failures']} consecutive "
                f"failures, last success {s['last_success_at'] or 'never'}; "
                f"last error: {s['last_error']}"
                for s in stalled
            ]
            body = (
                "The controller-tier heartbeat-stall detector found one or more "
                "watchers that are RUNNING (fresh liveness) but have made no "
                "successful cycle in a long time — the failure mode the PID "
                "watchdog and the old heartbeat both missed (cf. the 2026-06-21 "
                "reviewer token outage):\n\n"
                + "\n".join(lines)
                + "\n\nInvestigate the lane(s): check the watcher log for the "
                "repeated error, and the fleet token/credentials and config. "
                "Auto-filed by HeartbeatStallTask."
            )
            created = client.create_issue(
                name=f"{_FIX_TITLE_PREFIX}: {stalled[0]['role']}"[:200],
                description=body,
                label_names=list(_FIX_LABELS),
            )
            return f"created:{created.get('id')}"
        except Exception as exc:  # noqa: BLE001 — never let task-filing halt the loop
            return f"file_failed:{exc}"

    def _result(
        self,
        status: Literal["ok", "skipped", "failed"],
        started: float,
        details: dict[str, object],
        *,
        error: str | None = None,
    ) -> MaintenanceResult:
        return MaintenanceResult(
            name=self.name,
            status=status,
            duration_seconds=time.monotonic() - started,
            details=details,
            error=error,
        )
