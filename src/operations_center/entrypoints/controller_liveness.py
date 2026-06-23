# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""External controller-liveness check (Phase D2, determinism surface 10).

``HeartbeatStallTask`` catches a worker/reviewer lane that is live-but-not-
succeeding — but it runs INSIDE the spec_hygiene maintenance loop and does not
monitor the loop that hosts it. If spec_hygiene itself crash-loops (the exact
failure #386 was built to catch), the stall detector dies with it and files
nothing.

This module closes that blind spot by being designed to run OUTSIDE spec_hygiene
— from the shell watchdog / a cron tick. It reads the maintenance-loop heartbeat
and exits non-zero when the controller is dead (stale ``at``) or live-but-stalled
(fresh ``at``, no success in too long), so the supervisor can restart it.

    python -m operations_center.entrypoints.controller_liveness [--status-dir DIR] [--role R]
    # exit 0 = healthy/absent, 1 = needs restart
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from operations_center.entrypoints.heartbeat import is_live, read_heartbeat, success_stalled

logger = logging.getLogger(__name__)

_DEFAULT_ROLE = "spec_hygiene"
_DEFAULT_STATUS_DIR = Path("logs/local/watch-all")
_DEFAULT_MAX_LIVENESS_SECONDS = 900
_DEFAULT_MAX_SUCCESS_AGE_SECONDS = 1800
_DEFAULT_MIN_CONSECUTIVE_FAILURES = 5


@dataclass(frozen=True)
class LivenessVerdict:
    role: str
    status: str  # "healthy" | "absent" | "dead" | "stalled"
    detail: str

    @property
    def needs_restart(self) -> bool:
        return self.status in {"dead", "stalled"}


def check_controller_liveness(
    status_dir: Path,
    *,
    role: str = _DEFAULT_ROLE,
    now: datetime | None = None,
    max_liveness_seconds: int = _DEFAULT_MAX_LIVENESS_SECONDS,
    max_success_age_seconds: int = _DEFAULT_MAX_SUCCESS_AGE_SECONDS,
    min_consecutive_failures: int = _DEFAULT_MIN_CONSECUTIVE_FAILURES,
) -> LivenessVerdict:
    """Classify the controller heartbeat. Absent => healthy (nothing observed yet,
    not this check's job to start it); stale ``at`` => dead; fresh-but-not-
    succeeding => stalled. Both dead and stalled call for a supervisor restart."""

    now = now or datetime.now(timezone.utc)
    hb = read_heartbeat(status_dir, role)
    if hb is None:
        return LivenessVerdict(role, "absent", "no heartbeat file")
    if not is_live(hb, now=now, max_liveness_seconds=max_liveness_seconds):
        return LivenessVerdict(role, "dead", f"at is stale (> {max_liveness_seconds}s); process hung")
    if success_stalled(
        hb,
        now=now,
        max_success_age_seconds=max_success_age_seconds,
        min_consecutive_failures=min_consecutive_failures,
    ):
        return LivenessVerdict(
            role,
            "stalled",
            f"live but no success in > {max_success_age_seconds}s "
            f"(consecutive_failures={hb.get('consecutive_failures')}, "
            f"last_error={hb.get('last_error')!r})",
        )
    return LivenessVerdict(role, "healthy", "live and succeeding")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="controller-liveness")
    parser.add_argument("--status-dir", type=Path, default=_DEFAULT_STATUS_DIR)
    parser.add_argument("--role", default=_DEFAULT_ROLE)
    parser.add_argument("--max-liveness-seconds", type=int, default=_DEFAULT_MAX_LIVENESS_SECONDS)
    args = parser.parse_args(argv)

    verdict = check_controller_liveness(
        args.status_dir, role=args.role, max_liveness_seconds=args.max_liveness_seconds
    )
    if verdict.needs_restart:
        logger.error(
            "controller_liveness: %s %s — %s "
            '{"event": "controller_unhealthy", "role": "%s", "status": "%s"}',
            verdict.role,
            verdict.status.upper(),
            verdict.detail,
            verdict.role,
            verdict.status,
        )
        return 1
    logger.info("controller_liveness: %s %s — %s", verdict.role, verdict.status, verdict.detail)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
