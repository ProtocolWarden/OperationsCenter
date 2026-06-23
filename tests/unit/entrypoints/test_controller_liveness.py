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


# ── F2: enforcement (SIGTERM stalled supervisor so the watchdog revives it) ─────


def test_signal_restart_sends_sigterm(tmp_path, monkeypatch):
    import signal as _signal

    from operations_center.entrypoints.controller_liveness import signal_restart

    pidf = tmp_path / "spec.pid"
    pidf.write_text("4242")
    killed = {}
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.update(pid=pid, sig=sig))
    assert signal_restart(pidf) is True
    assert killed == {"pid": 4242, "sig": _signal.SIGTERM}


def test_signal_restart_missing_pidfile_is_noop(tmp_path):
    from operations_center.entrypoints.controller_liveness import signal_restart

    assert signal_restart(tmp_path / "nope.pid") is False


def test_main_enforce_kills_on_stall(tmp_path, monkeypatch):
    # live-but-stalled heartbeat + --enforce → returns 1 AND signals the pid
    write_heartbeat(tmp_path, "spec_hygiene", success=True, now=_NOW - timedelta(hours=1))
    for i in range(6):
        write_heartbeat(
            tmp_path, "spec_hygiene", success=False, error="wedged",
            now=_NOW - timedelta(seconds=60 - i),
        )
    pidf = tmp_path / "spec.pid"
    pidf.write_text("999")
    killed = {}
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.update(pid=pid))
    # freeze now so the stall is observed
    import operations_center.entrypoints.controller_liveness as cl
    monkeypatch.setattr(cl, "check_controller_liveness",
                        lambda *a, **k: cl.LivenessVerdict("spec_hygiene", "stalled", "x"))
    rc = cl.main(["--status-dir", str(tmp_path), "--role", "spec_hygiene",
                  "--enforce", "--pid-file", str(pidf)])
    assert rc == 1
    assert killed == {"pid": 999}


def test_main_enforce_healthy_does_not_kill(tmp_path, monkeypatch):
    # main() uses real wall-clock now, so write a genuinely-recent heartbeat.
    from datetime import datetime as _dt

    write_heartbeat(tmp_path, "spec_hygiene", success=True, now=_dt.now(UTC))
    pidf = tmp_path / "spec.pid"
    pidf.write_text("999")
    killed = {}
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.update(pid=pid))
    rc = main(["--status-dir", str(tmp_path), "--role", "spec_hygiene",
               "--enforce", "--pid-file", str(pidf)])
    assert rc == 0 and killed == {}
