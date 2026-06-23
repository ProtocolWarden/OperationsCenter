# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the external controller-liveness check (Phase D2, surface 10).

The detector that catches a live-but-stalled controller must itself run outside
the controller — these tests pin the classification it returns to the watchdog.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.entrypoints.controller_liveness import (
    check_controller_liveness,
    main,
)
from operations_center.entrypoints.heartbeat import write_heartbeat

_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def test_absent_is_healthy(tmp_path: Path):
    v = check_controller_liveness(tmp_path, role="spec_hygiene", now=_NOW)
    assert v.status == "absent"
    assert not v.needs_restart


def test_live_and_succeeding_is_healthy(tmp_path: Path):
    write_heartbeat(tmp_path, "spec_hygiene", success=True, now=_NOW - timedelta(seconds=30))
    v = check_controller_liveness(tmp_path, role="spec_hygiene", now=_NOW)
    assert v.status == "healthy"
    assert not v.needs_restart


def test_stale_at_is_dead(tmp_path: Path):
    write_heartbeat(tmp_path, "spec_hygiene", success=True, now=_NOW - timedelta(hours=2))
    v = check_controller_liveness(tmp_path, role="spec_hygiene", now=_NOW)
    assert v.status == "dead"
    assert v.needs_restart


def test_live_but_stalled_needs_restart(tmp_path: Path):
    # The #386 shape applied to the controller itself: fresh `at`, no success.
    write_heartbeat(tmp_path, "spec_hygiene", success=True, now=_NOW - timedelta(hours=1))
    for i in range(6):
        write_heartbeat(
            tmp_path,
            "spec_hygiene",
            success=False,
            error="loop wedged",
            now=_NOW - timedelta(seconds=60 - i),
        )
    v = check_controller_liveness(tmp_path, role="spec_hygiene", now=_NOW)
    assert v.status == "stalled"
    assert v.needs_restart


def test_main_returns_nonzero_when_unhealthy(tmp_path: Path):
    write_heartbeat(tmp_path, "spec_hygiene", success=True, now=_NOW - timedelta(hours=2))
    rc = main(["--status-dir", str(tmp_path), "--role", "spec_hygiene"])
    assert rc == 1


def test_main_returns_zero_when_absent(tmp_path: Path):
    rc = main(["--status-dir", str(tmp_path), "--role", "spec_hygiene"])
    assert rc == 0
