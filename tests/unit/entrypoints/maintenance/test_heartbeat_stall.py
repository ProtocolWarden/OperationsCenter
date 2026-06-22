# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the controller-tier heartbeat-stall detector.

Reproduces the 2026-06-21 reviewer outage shape: a watcher that is LIVE (fresh
`at`) but has not had a successful cycle in a long time must be flagged, where
the PID watchdog and the old heartbeat both read it as healthy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.entrypoints.heartbeat import write_heartbeat
from operations_center.entrypoints.maintenance.heartbeat_stall import HeartbeatStallTask
from operations_center.maintenance.contracts import MaintenanceContext

_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def _ctx(now=_NOW, plane_client=None) -> MaintenanceContext:
    resources = {"plane_client": plane_client} if plane_client is not None else {}
    return MaintenanceContext(cycle_id="c", now=now, resources=resources)


class _FakePlane:
    def __init__(self, existing: list[dict] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict] = []

    def list_issues(self) -> list[dict]:
        return self.existing

    def create_issue(self, *, name, description, label_names=None):
        self.created.append({"name": name, "description": description, "labels": label_names})
        return {"id": f"new-{len(self.created)}", "name": name}


def _task(tmp_path: Path, plane=None, **over):
    return HeartbeatStallTask(
        settings=None,
        status_dir=tmp_path,
        roles=("review", "goal"),
        plane_client=plane,
        **over,
    )


def test_no_heartbeats_is_ok(tmp_path: Path):
    result = _task(tmp_path).run_once(_ctx())
    assert result.status == "ok"
    assert result.details["observed_roles"] == []


def test_healthy_idle_lane_is_ok(tmp_path: Path):
    # Recent success (idle cycles count as success) => not stalled.
    write_heartbeat(tmp_path, "review", success=True, now=_NOW - timedelta(seconds=30))
    result = _task(tmp_path).run_once(_ctx())
    assert result.status == "ok"
    assert result.details["stalled"] == []


def test_live_but_stalled_lane_opens_fix_task(tmp_path: Path):
    # Last success 1h ago, then a run of failing-but-live cycles => the outage shape.
    write_heartbeat(tmp_path, "review", success=True, now=_NOW - timedelta(hours=1))
    for i in range(6):
        write_heartbeat(
            tmp_path,
            "review",
            success=False,
            error="no GitHub token",
            now=_NOW - timedelta(seconds=60 - i),
        )
    plane = _FakePlane()
    result = _task(tmp_path, plane).run_once(_ctx())
    assert result.status == "failed"
    assert "review" in result.error
    assert len(plane.created) == 1
    assert plane.created[0]["name"].startswith("[heartbeat-stall]")
    assert result.details["fix_task"].startswith("created:")


def test_dead_process_stale_at_is_not_a_stall(tmp_path: Path):
    # `at` itself is stale => the process is hung/dead => supervisor's concern,
    # NOT a "live but not succeeding" stall. We must not flag it here.
    write_heartbeat(tmp_path, "goal", success=True, now=_NOW - timedelta(hours=2))
    for _ in range(6):
        write_heartbeat(tmp_path, "goal", success=False, error="boom", now=_NOW - timedelta(hours=1))
    result = _task(tmp_path, _FakePlane()).run_once(_ctx())
    assert result.status == "ok"
    assert result.details["stalled"] == []


def test_transient_blip_not_flagged(tmp_path: Path):
    # Few consecutive failures, recent success => transient, not a stall.
    write_heartbeat(tmp_path, "goal", success=True, now=_NOW - timedelta(seconds=90))
    write_heartbeat(tmp_path, "goal", success=False, error="x", now=_NOW - timedelta(seconds=30))
    result = _task(tmp_path, _FakePlane()).run_once(_ctx())
    assert result.status == "ok"


def test_existing_open_fault_not_duplicated(tmp_path: Path):
    write_heartbeat(tmp_path, "review", success=True, now=_NOW - timedelta(hours=1))
    for _ in range(6):
        write_heartbeat(tmp_path, "review", success=False, error="x", now=_NOW - timedelta(seconds=30))
    plane = _FakePlane(
        existing=[
            {
                "id": "abc",
                "name": "[heartbeat-stall] watcher live but not succeeding: review",
                "state": {"name": "In Progress"},
            }
        ]
    )
    result = _task(tmp_path, plane).run_once(_ctx())
    assert result.status == "failed"
    assert plane.created == []
    assert result.details["fix_task"] == "exists:abc"


def test_satisfies_maintenance_task_protocol(tmp_path: Path):
    from operations_center.maintenance.contracts import MaintenanceTask

    task = _task(tmp_path)
    assert isinstance(task, MaintenanceTask)
    assert task.name == "heartbeat_stall"
    assert task.interval_seconds > 0
    assert task.enabled is True
