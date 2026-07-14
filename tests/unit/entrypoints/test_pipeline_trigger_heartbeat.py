# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Heartbeat coverage for the propose watcher pipeline trigger."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from operations_center.entrypoints.heartbeat import read_heartbeat
from operations_center.entrypoints.pipeline_trigger import main as trigger_main


def test_run_pipeline_updates_propose_heartbeat_during_execution(monkeypatch, tmp_path: Path) -> None:
    seen_midrun: dict[str, object] = {}

    def fake_run(cmd: list[str], timeout: int, capture_output: bool) -> subprocess.CompletedProcess[str]:
        assert capture_output is False
        assert timeout == 600
        time.sleep(0.03)
        hb = read_heartbeat(tmp_path, "propose")
        assert hb is not None
        seen_midrun["status"] = hb["status"]
        seen_midrun["last_success_at"] = hb["last_success_at"]
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(trigger_main, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(trigger_main.subprocess, "run", fake_run)

    ok = trigger_main._run_pipeline("config.yaml", execute=True, status_dir=tmp_path)

    assert ok is True
    assert seen_midrun == {"status": "executing", "last_success_at": None}
    hb = read_heartbeat(tmp_path, "propose")
    assert hb is not None
    assert hb["status"] == "idle"
    assert hb["last_success_at"] == hb["at"]
    assert hb["consecutive_failures"] == 0


def test_run_trigger_loop_marks_idle_heartbeat_on_quiet_cycles(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[bool, str]] = []
    sleep_calls = 0

    def fake_write_heartbeat(
        status_dir: Path | None, *, success: bool, status: str, error: str | None = None
    ) -> None:
        assert status_dir == tmp_path
        assert error is None
        calls.append((success, status))

    def fake_sleep(_seconds: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(trigger_main, "_get_trigger_sources", lambda _config: [])
    monkeypatch.setattr(trigger_main, "_load_state", lambda: {})
    monkeypatch.setattr(trigger_main, "_snapshot_mtimes", lambda _sources: {})
    monkeypatch.setattr(trigger_main, "_write_heartbeat", fake_write_heartbeat)
    monkeypatch.setattr(trigger_main.time, "sleep", fake_sleep)

    try:
        trigger_main.run_trigger_loop(
            "config.yaml", execute=False, min_interval_seconds=300, poll_interval_seconds=1, status_dir=tmp_path
        )
    except KeyboardInterrupt:
        pass

    assert calls[:2] == [(True, "idle"), (True, "idle")]
