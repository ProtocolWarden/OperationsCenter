# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.maintenance.contracts import (
    MaintenanceContext,
    MaintenanceResult,
)
from operations_center.maintenance.registry import (
    _DEFAULT_STATE_PATH,
    MaintenanceRegistry,
)


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class FakeTask:
    """Minimal MaintenanceTask implementation for tests."""

    def __init__(
        self,
        name: str,
        *,
        interval_seconds: int = 60,
        enabled: bool = True,
        status: str = "ok",
        raise_exc: Exception | None = None,
    ) -> None:
        self.name = name
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._status = status
        self._raise_exc = raise_exc
        self.run_calls = 0

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        self.run_calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return MaintenanceResult(
            name=self.name,
            status=self._status,  # type: ignore[arg-type]
            duration_seconds=0.0,
            details={"cycle": ctx.cycle_id},
        )


def _ctx(now: datetime | None = None, cycle_id: str = "c1") -> MaintenanceContext:
    return MaintenanceContext(
        cycle_id=cycle_id,
        now=now or datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------------
# Construction / state path
# --------------------------------------------------------------------------


def test_default_state_path_used_when_none(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reg = MaintenanceRegistry()
    assert reg.state_path == _DEFAULT_STATE_PATH


def test_explicit_state_path(tmp_path):
    p = tmp_path / "state.json"
    reg = MaintenanceRegistry(state_path=p)
    assert reg.state_path == p


def test_init_loads_existing_state(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(
        json.dumps({"last_run": {"alpha": 100.0, "beta": "200"}}),
        encoding="utf-8",
    )
    reg = MaintenanceRegistry(state_path=p)
    # beta coerced from string -> float
    assert reg._last_run == {"alpha": 100.0, "beta": 200.0}


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def test_register_and_list(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a")
    reg.register(t)
    tasks = reg.list_tasks()
    assert tasks == [t]
    # list_tasks returns a copy
    tasks.append(FakeTask("x"))
    assert len(reg.list_tasks()) == 1


def test_register_duplicate_name_raises(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    reg.register(FakeTask("dup"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(FakeTask("dup"))


def test_register_many(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    reg.register_many([FakeTask("a"), FakeTask("b")])
    assert [t.name for t in reg.list_tasks()] == ["a", "b"]


# --------------------------------------------------------------------------
# _is_due
# --------------------------------------------------------------------------


def test_is_due_disabled_task(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a", enabled=False)
    assert reg._is_due(t, 1000.0) is False


def test_is_due_never_run(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a")
    assert reg._is_due(t, 1000.0) is True


def test_is_due_interval_elapsed(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a", interval_seconds=60)
    reg._last_run["a"] = 1000.0
    assert reg._is_due(t, 1061.0) is True


def test_is_due_interval_not_elapsed(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a", interval_seconds=60)
    reg._last_run["a"] = 1000.0
    assert reg._is_due(t, 1030.0) is False


def test_is_due_interval_exactly_elapsed(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a", interval_seconds=60)
    reg._last_run["a"] = 1000.0
    assert reg._is_due(t, 1060.0) is True


# --------------------------------------------------------------------------
# run_due
# --------------------------------------------------------------------------


def test_run_due_happy_path_runs_and_persists(tmp_path):
    p = tmp_path / "s.json"
    reg = MaintenanceRegistry(state_path=p)
    t = FakeTask("a")
    reg.register(t)
    now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    results = reg.run_due(_ctx(now=now))
    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].details == {"cycle": "c1"}
    assert t.run_calls == 1
    # last_run recorded with the cycle's epoch
    assert reg._last_run["a"] == now.timestamp()
    # persisted to disk
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["last_run"]["a"] == now.timestamp()
    assert "written_at" in saved


def test_run_due_skips_not_due(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a", interval_seconds=3600)
    reg.register(t)
    now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    reg._last_run["a"] = now.timestamp() - 10.0  # ran 10s ago, interval 3600
    results = reg.run_due(_ctx(now=now))
    assert results == []
    assert t.run_calls == 0


def test_run_due_skips_disabled(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    t = FakeTask("a", enabled=False)
    reg.register(t)
    results = reg.run_due(_ctx())
    assert results == []
    assert t.run_calls == 0


def test_run_due_failing_task_recorded_and_continues(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "s.json")
    bad = FakeTask("bad", raise_exc=RuntimeError("boom"))
    good = FakeTask("good")
    reg.register(bad)
    reg.register(good)
    now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    results = reg.run_due(_ctx(now=now))
    assert len(results) == 2
    by_name = {r.name: r for r in results}
    assert by_name["bad"].status == "failed"
    assert by_name["bad"].error == "boom"
    assert by_name["bad"].details == {}
    assert by_name["bad"].duration_seconds >= 0.0
    assert by_name["good"].status == "ok"
    # both marked as run even though one failed
    assert reg._last_run["bad"] == now.timestamp()
    assert reg._last_run["good"] == now.timestamp()


def test_run_due_empty_registry_saves_state(tmp_path):
    p = tmp_path / "s.json"
    reg = MaintenanceRegistry(state_path=p)
    results = reg.run_due(_ctx())
    assert results == []
    assert p.exists()


# --------------------------------------------------------------------------
# _load_state edge cases
# --------------------------------------------------------------------------


def test_load_state_missing_file(tmp_path):
    reg = MaintenanceRegistry(state_path=tmp_path / "nope.json")
    assert reg._last_run == {}


def test_load_state_non_dict_top_level(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    reg = MaintenanceRegistry(state_path=p)
    assert reg._last_run == {}


def test_load_state_malformed_values_dropped(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps({"last_run": {"a": "notafloat", "b": None, "c": 5}}),
        encoding="utf-8",
    )
    reg = MaintenanceRegistry(state_path=p)
    assert reg._last_run == {"c": 5.0}


def test_load_state_invalid_json(tmp_path, caplog):
    p = tmp_path / "s.json"
    p.write_text("{ not valid json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        reg = MaintenanceRegistry(state_path=p)
    assert reg._last_run == {}
    assert any("load failed" in m for m in caplog.messages)


def test_load_state_oserror(tmp_path, monkeypatch, caplog):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"last_run": {}}), encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "read_text", boom)
    with caplog.at_level("WARNING"):
        reg = MaintenanceRegistry(state_path=p)
    assert reg._last_run == {}
    assert any("load failed" in m for m in caplog.messages)


def test_load_state_no_last_run_key(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"written_at": "x"}), encoding="utf-8")
    reg = MaintenanceRegistry(state_path=p)
    assert reg._last_run == {}


# --------------------------------------------------------------------------
# _save_state edge cases
# --------------------------------------------------------------------------


def test_save_state_creates_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "s.json"
    reg = MaintenanceRegistry(state_path=p)
    reg._last_run["a"] = 1.0
    reg._save_state()
    assert p.exists()
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["last_run"] == {"a": 1.0}


def test_save_state_oserror_swallowed(tmp_path, monkeypatch, caplog):
    p = tmp_path / "s.json"
    reg = MaintenanceRegistry(state_path=p)

    def boom(*_a, **_k):
        raise OSError("readonly fs")

    monkeypatch.setattr(Path, "write_text", boom)
    with caplog.at_level("WARNING"):
        reg._save_state()  # should not raise
    assert any("save failed" in m for m in caplog.messages)


# --------------------------------------------------------------------------
# round trip persistence across instances
# --------------------------------------------------------------------------


def test_state_survives_new_instance(tmp_path):
    p = tmp_path / "s.json"
    now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    reg1 = MaintenanceRegistry(state_path=p)
    reg1.register(FakeTask("a"))
    reg1.run_due(_ctx(now=now))

    reg2 = MaintenanceRegistry(state_path=p)
    assert reg2._last_run["a"] == now.timestamp()
    # Newly loaded registry treats the task as not-due within interval
    t = FakeTask("a", interval_seconds=3600)
    assert reg2._is_due(t, now.timestamp() + 10.0) is False
