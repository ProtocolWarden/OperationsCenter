# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.scheduled_tasks import runner as mod
from operations_center.scheduled_tasks.runner import (
    ScheduledTaskRunner,
    _is_due,
    _parse_at,
    _parse_every,
    _task_key,
    due_tasks,
)


# ── lightweight stand-ins for pydantic models ────────────────────────────────


@dataclass
class FakeTask:
    every: str = "1d"
    title: str = "Weekly audit"
    goal: str = "audit deps"
    repo_key: str = "owner/repo"
    kind: str = "goal"
    at: str | None = None
    on_days: list[str] | None = None


@dataclass
class FakeSettings:
    scheduled_tasks: list = field(default_factory=list)


# ── _parse_every ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30m", 30 * 60),
        ("6h", 6 * 3600),
        ("1d", 86400),
        ("1w", 604800),
        ("  2H ", 2 * 3600),  # whitespace + case-insensitive
        ("0m", 0),
    ],
)
def test_parse_every_valid(text, expected):
    assert _parse_every(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "5x", "m", "5", "-3h", "1.5h"])
def test_parse_every_invalid(bad):
    with pytest.raises(ValueError):
        _parse_every(bad)


# ── _parse_at ───────────────────────────────────────────────────────────────


def test_parse_at_valid():
    assert _parse_at("09:30") == (9, 30)
    assert _parse_at(" 0:00 ") == (0, 0)
    assert _parse_at("23:59") == (23, 59)


@pytest.mark.parametrize("bad", ["9", "0930", "9:3", "abc"])
def test_parse_at_malformed(bad):
    with pytest.raises(ValueError, match="at must be HH:MM"):
        _parse_at(bad)


@pytest.mark.parametrize("bad", ["24:00", "12:60", "99:99"])
def test_parse_at_out_of_range(bad):
    with pytest.raises(ValueError, match="out of range"):
        _parse_at(bad)


# ── _task_key ─────────────────────────────────────────────────────────────────


def test_task_key_stable_and_short():
    t = FakeTask(title="T", repo_key="r")
    k1 = _task_key(t)
    k2 = _task_key(FakeTask(title="T", repo_key="r"))
    assert k1 == k2
    assert len(k1) == 16


def test_task_key_changes_with_title_or_repo():
    base = _task_key(FakeTask(title="A", repo_key="r"))
    assert base != _task_key(FakeTask(title="B", repo_key="r"))
    assert base != _task_key(FakeTask(title="A", repo_key="r2"))


# ── _is_due ───────────────────────────────────────────────────────────────────

NOW = datetime(2026, 6, 2, 9, 0, 0, tzinfo=UTC)  # 2026-06-02 is a Tuesday


def test_is_due_malformed_every_returns_false(caplog):
    with caplog.at_level("WARNING"):
        assert _is_due(FakeTask(every="nope"), None, NOW) is False
    assert "skipping malformed task" in caplog.text


def test_is_due_first_run_no_last_run():
    assert _is_due(FakeTask(every="1d"), None, NOW) is True


def test_is_due_interval_not_elapsed():
    last = NOW.replace(hour=8)  # only 1h ago, interval is 1d
    assert _is_due(FakeTask(every="1d"), last, NOW) is False


def test_is_due_interval_elapsed():
    last = NOW.replace(day=1)  # >1d ago
    assert _is_due(FakeTask(every="1d"), last, NOW) is True


def test_is_due_weekday_match():
    # Tuesday = "tue"
    assert _is_due(FakeTask(every="1d", on_days=["tue"]), None, NOW) is True


def test_is_due_weekday_no_match():
    assert _is_due(FakeTask(every="1d", on_days=["mon"]), None, NOW) is False


def test_is_due_weekday_unknown_name_logs_and_filtered(caplog):
    with caplog.at_level("WARNING"):
        # only an unknown day -> normalized&known is empty -> no match
        result = _is_due(FakeTask(every="1d", on_days=["xyz"]), None, NOW)
    assert result is False
    assert "unknown day name" in caplog.text


def test_is_due_weekday_mixed_known_unknown(caplog):
    # "tuesday" -> "tue" matches; "xyz" unknown but logged
    with caplog.at_level("WARNING"):
        assert _is_due(FakeTask(every="1d", on_days=["Tuesday", "xyz", ""]), None, NOW) is True
    assert "unknown day name" in caplog.text


def test_is_due_at_anchor_match_within_slack():
    # now is 09:00, anchor 09:00 -> delta 0
    assert _is_due(FakeTask(every="1d", at="09:00"), None, NOW) is True


def test_is_due_at_anchor_within_slack_window():
    now = NOW.replace(minute=4)  # 09:04, anchor 09:00 -> 240s < 300 slack
    assert _is_due(FakeTask(every="1d", at="09:00"), None, now) is True


def test_is_due_at_anchor_outside_slack():
    now = NOW.replace(minute=10)  # 600s > 300
    assert _is_due(FakeTask(every="1d", at="09:00"), None, now) is False


def test_is_due_at_custom_slack():
    now = NOW.replace(minute=10)
    assert _is_due(FakeTask(every="1d", at="09:00"), None, now, slack_seconds=700) is True


def test_is_due_at_malformed_returns_false(caplog):
    with caplog.at_level("WARNING"):
        assert _is_due(FakeTask(every="1d", at="99:99"), None, NOW) is False
    assert "bad `at`" in caplog.text


# ── due_tasks ─────────────────────────────────────────────────────────────────


def test_due_tasks_empty_settings():
    assert due_tasks(FakeSettings(scheduled_tasks=[])) == []


def test_due_tasks_none_scheduled():
    s = FakeSettings()
    s.scheduled_tasks = None
    assert due_tasks(s) == []


def test_due_tasks_no_state_file(tmp_path):
    s = FakeSettings(scheduled_tasks=[FakeTask(every="1d")])
    out = due_tasks(s, state_file=tmp_path / "missing.json", now=NOW)
    assert len(out) == 1


def test_due_tasks_uses_default_state_file(monkeypatch, tmp_path):
    # Point module default at a non-existent path under tmp.
    monkeypatch.setattr(mod, "_STATE_FILE", tmp_path / "default.json")
    s = FakeSettings(scheduled_tasks=[FakeTask(every="1d")])
    out = due_tasks(s, now=NOW)
    assert len(out) == 1


def test_due_tasks_uses_now_default(monkeypatch, tmp_path):
    s = FakeSettings(scheduled_tasks=[FakeTask(every="1d")])
    out = due_tasks(s, state_file=tmp_path / "x.json")
    assert len(out) == 1  # now defaults to datetime.now(UTC); first-run fires


def test_due_tasks_respects_last_run(tmp_path):
    task = FakeTask(every="1d")
    key = _task_key(task)
    state = tmp_path / "state.json"
    # last run was just now -> not due
    state.write_text(json.dumps({key: NOW.isoformat()}), encoding="utf-8")
    s = FakeSettings(scheduled_tasks=[task])
    assert due_tasks(s, state_file=state, now=NOW) == []


def test_due_tasks_last_run_z_suffix(tmp_path):
    task = FakeTask(every="1d")
    key = _task_key(task)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({key: "2026-06-02T09:00:00Z"}), encoding="utf-8")
    s = FakeSettings(scheduled_tasks=[task])
    assert due_tasks(s, state_file=state, now=NOW) == []


def test_due_tasks_bad_last_run_iso_treated_as_first_run(tmp_path):
    task = FakeTask(every="1d")
    key = _task_key(task)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({key: "not-a-date"}), encoding="utf-8")
    s = FakeSettings(scheduled_tasks=[task])
    # unparseable -> last_run stays None -> due
    assert len(due_tasks(s, state_file=state, now=NOW)) == 1


def test_due_tasks_unreadable_state_file(tmp_path, caplog):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    s = FakeSettings(scheduled_tasks=[FakeTask(every="1d")])
    with caplog.at_level("WARNING"):
        out = due_tasks(s, state_file=state, now=NOW)
    assert len(out) == 1
    assert "state file unreadable" in caplog.text


def test_due_tasks_state_null_json(tmp_path):
    # json "null" -> falls back to {} via `or {}`
    state = tmp_path / "state.json"
    state.write_text("null", encoding="utf-8")
    s = FakeSettings(scheduled_tasks=[FakeTask(every="1d")])
    assert len(due_tasks(s, state_file=state, now=NOW)) == 1


# ── ScheduledTaskRunner.tick ─────────────────────────────────────────────────


def _runner(client, tasks, state_file):
    return ScheduledTaskRunner(client, FakeSettings(scheduled_tasks=tasks), state_file=state_file)


def test_tick_no_due_returns_empty(tmp_path):
    client = MagicMock()
    task = FakeTask(every="1d")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({_task_key(task): NOW.isoformat()}), encoding="utf-8")
    r = _runner(client, [task], state)
    assert r.tick(now=NOW) == []
    client.create_issue.assert_not_called()


def test_tick_creates_issue_and_persists_state(tmp_path):
    client = MagicMock()
    client.create_issue.return_value = {"id": "ISSUE-1"}
    task = FakeTask(every="1d", title="Audit", repo_key="o/r", kind="goal")
    state = tmp_path / "sub" / "state.json"
    r = _runner(client, [task], state)
    ids = r.tick(now=NOW)
    assert ids == ["ISSUE-1"]
    # verify create_issue args
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["name"] == "Audit"
    assert kwargs["state"] == "Ready for AI"
    assert "## Goal" in kwargs["description"]
    assert "every: 1d" in kwargs["description"]
    assert "source: autonomy" in kwargs["label_names"]
    # state file written
    assert state.exists()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved[_task_key(task)] == NOW.isoformat()
    assert not state.with_suffix(".tmp").exists()


def test_tick_empty_id_not_recorded(tmp_path):
    client = MagicMock()
    client.create_issue.return_value = {}  # no id
    state = tmp_path / "state.json"
    r = _runner(client, [FakeTask(every="1d")], state)
    assert r.tick(now=NOW) == []
    # nothing created -> state not written
    assert not state.exists()


def test_tick_create_issue_exception_logged_and_retries(tmp_path, caplog):
    client = MagicMock()
    client.create_issue.side_effect = RuntimeError("boom")
    state = tmp_path / "state.json"
    r = _runner(client, [FakeTask(every="1d", title="X")], state)
    with caplog.at_level("WARNING"):
        assert r.tick(now=NOW) == []
    assert "failed to create" in caplog.text
    assert not state.exists()


def test_tick_partial_failure_persists_success(tmp_path):
    client = MagicMock()
    good = FakeTask(every="1d", title="Good", repo_key="a")
    bad = FakeTask(every="1d", title="Bad", repo_key="b")

    def side(*, name, **_):
        if name == "Bad":
            raise RuntimeError("nope")
        return {"id": "GOOD-1"}

    client.create_issue.side_effect = side
    state = tmp_path / "state.json"
    r = _runner(client, [good, bad], state)
    ids = r.tick(now=NOW)
    assert ids == ["GOOD-1"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert _task_key(good) in saved
    assert _task_key(bad) not in saved


def test_tick_loads_existing_state_and_merges(tmp_path):
    client = MagicMock()
    client.create_issue.return_value = {"id": "NEW"}
    new_task = FakeTask(every="1d", title="New", repo_key="n")
    state = tmp_path / "state.json"
    # pre-existing unrelated key should survive
    state.write_text(json.dumps({"oldkey": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
    r = _runner(client, [new_task], state)
    r.tick(now=NOW)
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "oldkey" in saved
    assert _task_key(new_task) in saved


def test_tick_existing_state_unreadable_resets(tmp_path):
    client = MagicMock()
    client.create_issue.return_value = {"id": "NEW"}
    task = FakeTask(every="1d")
    state = tmp_path / "state.json"
    # due_tasks tolerates bad json; tick re-reads and resets to {}
    state.write_text("garbage{", encoding="utf-8")
    r = _runner(client, [task], state)
    assert r.tick(now=NOW) == ["NEW"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert _task_key(task) in saved


def test_tick_persist_oserror_logged(tmp_path, monkeypatch, caplog):
    client = MagicMock()
    client.create_issue.return_value = {"id": "ID1"}
    state = tmp_path / "state.json"
    r = _runner(client, [FakeTask(every="1d")], state)

    real_write = Path.write_text

    def boom(self, *a, **k):
        if self.suffix == ".tmp":
            raise OSError("disk full")
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", boom)
    with caplog.at_level("WARNING"):
        ids = r.tick(now=NOW)
    # ids still returned even though persistence failed
    assert ids == ["ID1"]
    assert "failed to persist last_run state" in caplog.text


def test_tick_default_now(tmp_path):
    client = MagicMock()
    client.create_issue.return_value = {"id": "ID"}
    state = tmp_path / "state.json"
    r = _runner(client, [FakeTask(every="1d")], state)
    # now omitted -> defaults to datetime.now(UTC); first run fires
    assert r.tick() == ["ID"]


def test_runner_default_state_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_STATE_FILE", tmp_path / "def.json")
    client = MagicMock()
    r = ScheduledTaskRunner(client, FakeSettings(scheduled_tasks=[]))
    assert r._state_file == tmp_path / "def.json"
