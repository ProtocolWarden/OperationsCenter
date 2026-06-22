# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for liveness-vs-success heartbeats (the 2026-06-21 outage gap).

The core property: a FAILED cycle keeps liveness (`at`) fresh but ages
`last_success_at`, so a crash-looping watcher no longer hides behind a fresh
"active" heartbeat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.entrypoints.heartbeat import (
    is_live,
    read_heartbeat,
    success_stalled,
    write_heartbeat,
)


def _at(hb: dict) -> datetime:
    return datetime.fromisoformat(hb["at"])


class TestWriteRead:
    def test_success_stamps_last_success_at(self, tmp_path: Path):
        write_heartbeat(tmp_path, "goal", status="idle", success=True)
        hb = read_heartbeat(tmp_path, "goal")
        assert hb["last_success_at"] == hb["at"]
        assert hb["consecutive_failures"] == 0
        assert hb["last_error"] is None

    def test_failure_preserves_prior_success_and_bumps_counter(self, tmp_path: Path):
        t0 = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
        write_heartbeat(tmp_path, "review", success=True, now=t0)
        # three failing cycles later
        for i in range(1, 4):
            write_heartbeat(
                tmp_path,
                "review",
                status="error",
                success=False,
                error="no GitHub token",
                now=t0 + timedelta(minutes=i),
            )
        hb = read_heartbeat(tmp_path, "review")
        # liveness advanced...
        assert _at(hb) == t0 + timedelta(minutes=3)
        # ...but the success timestamp is frozen at the last good cycle
        assert hb["last_success_at"] == t0.isoformat()
        assert hb["consecutive_failures"] == 3
        assert hb["last_error"] == "no GitHub token"

    def test_success_after_failures_resets(self, tmp_path: Path):
        t0 = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
        write_heartbeat(tmp_path, "goal", success=False, error="boom", now=t0)
        write_heartbeat(tmp_path, "goal", success=False, error="boom", now=t0 + timedelta(minutes=1))
        write_heartbeat(tmp_path, "goal", success=True, now=t0 + timedelta(minutes=2))
        hb = read_heartbeat(tmp_path, "goal")
        assert hb["consecutive_failures"] == 0
        assert hb["last_error"] is None
        assert hb["last_success_at"] == (t0 + timedelta(minutes=2)).isoformat()

    def test_read_missing_returns_none(self, tmp_path: Path):
        assert read_heartbeat(tmp_path, "nope") is None


class TestStallDetection:
    def _hb(self, **over):
        base = {
            "role": "review",
            "at": datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC).isoformat(),
            "status": "error",
            "last_success_at": datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC).isoformat(),
            "consecutive_failures": 0,
            "last_error": None,
        }
        base.update(over)
        return base

    def test_live_when_at_recent(self):
        now = datetime(2026, 6, 22, 12, 1, 0, tzinfo=UTC)
        assert is_live(self._hb(), now=now, max_liveness_seconds=600)

    def test_not_live_when_at_stale(self):
        now = datetime(2026, 6, 22, 12, 30, 0, tzinfo=UTC)
        assert not is_live(self._hb(), now=now, max_liveness_seconds=600)

    def test_stalled_when_failing_and_success_old(self):
        now = datetime(2026, 6, 22, 13, 0, 0, tzinfo=UTC)  # 1h after last success
        hb = self._hb(consecutive_failures=10)
        assert success_stalled(
            hb, now=now, max_success_age_seconds=1800, min_consecutive_failures=5
        )

    def test_not_stalled_when_few_failures(self):
        now = datetime(2026, 6, 22, 13, 0, 0, tzinfo=UTC)
        hb = self._hb(consecutive_failures=2)
        assert not success_stalled(
            hb, now=now, max_success_age_seconds=1800, min_consecutive_failures=5
        )

    def test_not_stalled_when_success_recent(self):
        # Failing right now, but last success was a minute ago — a transient blip.
        now = datetime(2026, 6, 22, 12, 1, 0, tzinfo=UTC)
        hb = self._hb(consecutive_failures=10)
        assert not success_stalled(
            hb, now=now, max_success_age_seconds=1800, min_consecutive_failures=5
        )

    def test_stalled_when_never_succeeded(self):
        now = datetime(2026, 6, 22, 12, 1, 0, tzinfo=UTC)
        hb = self._hb(consecutive_failures=8, last_success_at=None)
        assert success_stalled(
            hb, now=now, max_success_age_seconds=1800, min_consecutive_failures=5
        )
