# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""spec_hygiene now writes the shared liveness-vs-success heartbeat schema, so a
crash-looping maintenance loop is catchable by the external controller-liveness
check (surface 10) — not just when fully dead."""

from __future__ import annotations

from pathlib import Path

from operations_center.entrypoints.controller_liveness import check_controller_liveness
from operations_center.entrypoints.heartbeat import read_heartbeat
from operations_center.entrypoints.spec_hygiene.main import _write_heartbeat


def test_success_writes_new_schema(tmp_path: Path):
    _write_heartbeat(tmp_path, success=True)
    hb = read_heartbeat(tmp_path, "spec_hygiene")
    assert hb["consecutive_failures"] == 0
    assert hb["last_success_at"] == hb["at"]
    assert hb["last_error"] is None


def test_failure_ages_progress_and_is_stall_detectable(tmp_path: Path):
    _write_heartbeat(tmp_path, success=True)
    for _ in range(6):
        _write_heartbeat(tmp_path, success=False, error="loop wedged")
    hb = read_heartbeat(tmp_path, "spec_hygiene")
    assert hb["consecutive_failures"] == 6
    assert hb["last_error"] == "loop wedged"
    # the external check can now see a LIVE-but-stalled spec_hygiene
    from datetime import UTC, datetime, timedelta

    verdict = check_controller_liveness(
        tmp_path,
        role="spec_hygiene",
        now=datetime.now(UTC) + timedelta(hours=1),
        max_liveness_seconds=999999,  # still "live"
    )
    assert verdict.status == "stalled"
    assert verdict.needs_restart


def test_none_status_dir_is_noop():
    assert _write_heartbeat(None, success=True) is None  # must not raise
