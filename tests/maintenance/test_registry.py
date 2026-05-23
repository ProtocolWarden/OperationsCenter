# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the MaintenanceRegistry (ADR 0007 follow-up D)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from operations_center.maintenance import (
    MaintenanceContext,
    MaintenanceRegistry,
    MaintenanceResult,
    MaintenanceTask,
)


@dataclass
class _StubTask:
    name: str = "stub"
    interval_seconds: int = 60
    enabled: bool = True
    calls: int = 0
    raise_exc: Exception | None = None
    detail_payload: dict = field(default_factory=dict)

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return MaintenanceResult(
            name=self.name,
            status="ok",
            duration_seconds=0.001,
            details=dict(self.detail_payload),
        )


def _ctx(now: datetime) -> MaintenanceContext:
    return MaintenanceContext(cycle_id=f"c-{now.timestamp():.0f}", now=now)


def test_register_and_list(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "state.json")
    t = _StubTask(name="a")
    reg.register(t)
    assert reg.list_tasks() == [t]


def test_register_rejects_duplicate(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "state.json")
    reg.register(_StubTask(name="dup"))
    with pytest.raises(ValueError):
        reg.register(_StubTask(name="dup"))


def test_run_due_respects_interval(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "state.json")
    t = _StubTask(name="x", interval_seconds=60)
    reg.register(t)

    t0 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    # First cycle — never run, should fire.
    results = reg.run_due(_ctx(t0))
    assert [r.name for r in results] == ["x"]
    assert t.calls == 1

    # 10s later — under interval, should NOT fire.
    results = reg.run_due(_ctx(t0 + timedelta(seconds=10)))
    assert results == []
    assert t.calls == 1

    # 70s later — over interval, should fire.
    results = reg.run_due(_ctx(t0 + timedelta(seconds=70)))
    assert [r.name for r in results] == ["x"]
    assert t.calls == 2


def test_disabled_task_never_runs(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "state.json")
    t = _StubTask(name="off", enabled=False)
    reg.register(t)
    results = reg.run_due(_ctx(datetime.now(timezone.utc)))
    assert results == []
    assert t.calls == 0


def test_failing_task_does_not_block_others(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "state.json")
    bad = _StubTask(name="bad", raise_exc=RuntimeError("boom"))
    good = _StubTask(name="good")
    reg.register(bad)
    reg.register(good)

    results = reg.run_due(_ctx(datetime.now(timezone.utc)))
    by_name = {r.name: r for r in results}

    assert set(by_name) == {"bad", "good"}
    assert by_name["bad"].status == "failed"
    assert by_name["bad"].error == "boom"
    assert by_name["good"].status == "ok"
    assert good.calls == 1


def test_last_run_state_persists_across_registry_instances(tmp_path):
    state = tmp_path / "state.json"
    t0 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)

    reg1 = MaintenanceRegistry(state_path=state)
    reg1.register(_StubTask(name="p", interval_seconds=60))
    assert len(reg1.run_due(_ctx(t0))) == 1

    # New registry, same state file — last-run for "p" should be remembered.
    reg2 = MaintenanceRegistry(state_path=state)
    t2 = _StubTask(name="p", interval_seconds=60)
    reg2.register(t2)
    # 5s later — interval not elapsed, should NOT fire.
    assert reg2.run_due(_ctx(t0 + timedelta(seconds=5))) == []
    assert t2.calls == 0


def test_stub_satisfies_protocol():
    # Runtime-checkable Protocol — confirms our contract is structural.
    assert isinstance(_StubTask(), MaintenanceTask)
