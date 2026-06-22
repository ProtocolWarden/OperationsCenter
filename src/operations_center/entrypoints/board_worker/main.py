# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Board worker — Plane-polling watcher for goal, test, improve, and spec-author roles.

Polls the Plane board for "Ready for AI" issues with a matching task-kind label,
claims one, drives the planning → execution pipeline, then transitions board
state and creates follow-up tasks per the lifecycle contract.

Each role runs as a separate process. The shell launcher passes:
    --config              path to operations_center.local.yaml
    --role                goal | test | improve | spec-author
    --poll-interval-seconds N
    --status-dir          directory for heartbeat_{role}.json

Task-kind label mapping:
    goal    → task-kind: goal
    test    → task-kind: test  OR  task-kind: test_campaign
    improve → task-kind: improve  OR  task-kind: improve_campaign

Follow-up creation per lifecycle contract:
    goal success + verification needed → creates task-kind: test (Ready for AI)
    goal success + no verification     → transitions to Review (or Done)
    goal failure                       → transitions to Blocked
    test success                       → transitions to Done
    test failure                       → creates task-kind: goal (Ready for AI)
    improve any outcome                → creates bounded follow-up or Blocked
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

from operations_center.entrypoints.heartbeat import write_heartbeat

from .claim import claim_next
from .dispatch import dispatch_issue
from .labels import ROLE_KINDS

logger = logging.getLogger(__name__)


# ── Settings / client factories ───────────────────────────────────────────────


def _load_settings(config_path: Path):
    from operations_center.config import load_settings

    return load_settings(config_path)


def _plane_client(settings):
    from operations_center.adapters.plane import PlaneClient

    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


# ── Heartbeat ─────────────────────────────────────────────────────────────────


def _write_heartbeat(
    status_dir: Path,
    role: str,
    status: str = "idle",
    *,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Write the role heartbeat, separating liveness (``at``) from progress
    (``last_success_at``). ``success=False`` keeps the loop's liveness fresh but
    lets ``last_success_at`` age, so a crash-looping lane becomes detectable by
    ``HeartbeatStallTask`` instead of hiding behind a fresh 'active' heartbeat."""
    write_heartbeat(status_dir, role, status=status, success=success, error=error)


def _heartbeat_loop(status_dir: Path, role: str, stop_event: threading.Event) -> None:
    """Write 'executing' heartbeat every 60 s while a task runs."""
    while not stop_event.is_set():
        _write_heartbeat(status_dir, role, status="executing")
        stop_event.wait(60)


def _reconcile_in_flight_at_startup(config_path: Path, role: str) -> None:
    """Reclaim in-flight slots leaked by the restart that just (re)started us.

    A code-pull restart SIGTERMs the watchers mid-dispatch, so the executor that
    was running can leave an ``execution_started`` event with no matching
    ``execution_finished`` — a leaked per-backend concurrency slot. board_unblock
    Rule 10 would clear it, but only on its next watchdog cycle. Running the same
    reconciliation here closes the gap immediately on restart. Best-effort: a
    failure must never stop the worker from coming up.
    """
    try:
        from operations_center.execution.usage_store import UsageStore
        from operations_center.in_flight_reconcile import reconcile_in_flight_on_startup

        settings = _load_settings(config_path)
        client = _plane_client(settings)
        try:
            cleared = reconcile_in_flight_on_startup(UsageStore(), client)
        finally:
            client.close()
        if cleared:
            logger.info(
                "board_worker[%s]: startup reconcile cleared %d orphaned in-flight slot(s)",
                role,
                len(cleared),
            )
    except Exception as exc:  # noqa: BLE001 — startup must not fail on reconciliation
        logger.warning("board_worker[%s]: startup in-flight reconcile skipped — %s", role, exc)


# ── Main poll loop ────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OperationsCenter board worker — polls Plane and executes tasks by role",
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=list(ROLE_KINDS))
    parser.add_argument("--poll-interval-seconds", type=int, default=30, dest="poll_interval")
    parser.add_argument("--status-dir", type=Path, default=None, dest="status_dir")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{args.role}] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    role = args.role
    status_dir = args.status_dir or (
        Path(__file__).resolve().parents[4] / "logs" / "local" / "watch-all"
    )

    logger.info("board_worker[%s]: starting — poll_interval=%ds", role, args.poll_interval)

    _reconcile_in_flight_at_startup(args.config, role)

    while True:
        try:
            settings = _load_settings(args.config)
            client = _plane_client(settings)
            try:
                issue = claim_next(client, role, settings)
                if issue:
                    stop_event = threading.Event()
                    hb_thread = threading.Thread(
                        target=_heartbeat_loop,
                        args=(status_dir, role, stop_event),
                        daemon=True,
                    )
                    hb_thread.start()
                    try:
                        dispatch_issue(issue, role, args.config, settings, client)
                    finally:
                        stop_event.set()
                        hb_thread.join(timeout=5)
                else:
                    logger.debug("board_worker[%s]: nothing ready", role)
                _write_heartbeat(status_dir, role)
            finally:
                client.close()
        except Exception as exc:
            logger.error("board_worker[%s]: unhandled error — %s", role, exc, exc_info=True)
            # Record a FAILED cycle: liveness (`at`) stays fresh so the lane looks
            # alive, but `last_success_at` ages — the signal HeartbeatStallTask
            # uses to catch a crash-looping lane the PID watchdog can't see.
            _write_heartbeat(status_dir, role, status="error", success=False, error=str(exc))

        if args.once:
            return 0

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
