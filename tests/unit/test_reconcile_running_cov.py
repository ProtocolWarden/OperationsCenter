# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from operations_center.reconcile_running import (
    StaleRunningCandidate,
    _label_value,
    reconcile_stale_running_issues,
)

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _issue(
    *,
    state="running",
    updated_at=None,
    created_at=None,
    labels=None,
    issue_id="ID-1",
    name="Some task",
):
    issue: dict = {}
    if state is not None:
        issue["state"] = {"name": state} if isinstance(state, str) else state
    if updated_at is not None:
        issue["updated_at"] = updated_at
    if created_at is not None:
        issue["created_at"] = created_at
    if labels is not None:
        issue["labels"] = labels
    issue["id"] = issue_id
    issue["name"] = name
    return issue


# --------------------------------------------------------------------------
# _label_value
# --------------------------------------------------------------------------


def test_label_value_dict_match_case_insensitive():
    labels = [{"name": "Task-Kind: Test"}]
    assert _label_value(labels, "task-kind") == "Test"


def test_label_value_string_label_match():
    labels = ["task-kind:goal"]
    assert _label_value(labels, "task-kind") == "goal"


def test_label_value_no_match_returns_empty():
    labels = [{"name": "priority:high"}]
    assert _label_value(labels, "task-kind") == ""


def test_label_value_none_labels_returns_empty():
    assert _label_value(None, "task-kind") == ""


def test_label_value_empty_list_returns_empty():
    assert _label_value([], "task-kind") == ""


def test_label_value_strips_whitespace():
    labels = [{"name": "  task-kind:  improve  "}]
    assert _label_value(labels, "task-kind") == "improve"


def test_label_value_first_match_wins():
    labels = [{"name": "task-kind:test"}, {"name": "task-kind:goal"}]
    assert _label_value(labels, "task-kind") == "test"


def test_label_value_dict_without_name_key():
    labels = [{"other": "x"}]
    assert _label_value(labels, "task-kind") == ""


# --------------------------------------------------------------------------
# reconcile_stale_running_issues
# --------------------------------------------------------------------------


def test_non_running_state_skipped():
    issue = _issue(state="done", updated_at=_iso(_NOW - timedelta(hours=10)))
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out == []


def test_state_as_dict_running_detected():
    # goal default ttl 240min; age 5h => stale
    issue = _issue(
        state={"name": "Running"},
        updated_at=_iso(_NOW - timedelta(hours=5)),
        labels=[{"name": "task-kind:goal"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert len(out) == 1
    assert out[0].task_kind == "goal"


def test_state_as_plain_string():
    issue = {
        "state": "running",
        "updated_at": _iso(_NOW - timedelta(hours=2)),
        "labels": [{"name": "task-kind:test"}],
        "id": "X",
        "name": "n",
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    # test ttl 45 min, age 120 => stale
    assert len(out) == 1
    assert out[0].ttl_minutes == 45


def test_state_none_skipped():
    issue = {"id": "X", "name": "n", "updated_at": _iso(_NOW)}
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out == []


def test_default_now_used_when_not_injected(monkeypatch):
    # An issue updated far in the past must be stale even without now=
    issue = _issue(
        state="running",
        updated_at="2000-01-01T00:00:00+00:00",
        labels=[{"name": "task-kind:test"}],
    )
    out = reconcile_stale_running_issues([issue])
    assert len(out) == 1


def test_test_kind_under_ttl_not_stale():
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(minutes=30)),
        labels=[{"name": "task-kind:test"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out == []


def test_test_kind_at_exact_ttl_is_stale():
    # age >= ttl (45). Build updated_at so age == 45 exactly.
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(minutes=45)),
        labels=[{"name": "task-kind:test"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert len(out) == 1
    assert out[0].age_minutes == 45


def test_unknown_kind_uses_fallback():
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(hours=3)),
        labels=[{"name": "task-kind:weird"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW, fallback_minutes=120)
    assert len(out) == 1
    assert out[0].ttl_minutes == 120
    assert out[0].task_kind == "weird"


def test_unknown_kind_under_fallback_not_stale():
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(minutes=30)),
        labels=[{"name": "task-kind:weird"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW, fallback_minutes=120)
    assert out == []


def test_no_labels_defaults_to_goal_kind():
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(hours=5)),
        labels=[],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out[0].task_kind == "goal"
    assert out[0].ttl_minutes == 240


def test_missing_labels_key_defaults_to_goal():
    issue = {
        "state": "running",
        "updated_at": _iso(_NOW - timedelta(hours=5)),
        "id": "X",
        "name": "n",
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out[0].task_kind == "goal"


def test_custom_ttls_override_defaults():
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(minutes=20)),
        labels=[{"name": "task-kind:test"}],
    )
    # raise test ttl to 600 -> not stale anymore
    out = reconcile_stale_running_issues([issue], now=_NOW, ttls={"test": 600})
    assert out == []


def test_custom_ttls_lower_makes_stale():
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(minutes=20)),
        labels=[{"name": "task-kind:goal"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW, ttls={"goal": 10})
    assert len(out) == 1
    assert out[0].ttl_minutes == 10


def test_falls_back_to_created_at_when_no_updated_at():
    issue = {
        "state": "running",
        "created_at": _iso(_NOW - timedelta(hours=5)),
        "labels": [{"name": "task-kind:goal"}],
        "id": "X",
        "name": "n",
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert len(out) == 1


def test_z_suffix_timestamp_parsed():
    z_ts = (_NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue = _issue(
        state="running",
        updated_at=z_ts,
        labels=[{"name": "task-kind:goal"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert len(out) == 1


def test_unparseable_timestamp_skipped():
    issue = _issue(
        state="running",
        updated_at="not-a-date",
        labels=[{"name": "task-kind:goal"}],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out == []


def test_empty_timestamp_skipped():
    issue = {
        "state": "running",
        "updated_at": "",
        "created_at": "",
        "labels": [],
        "id": "X",
        "name": "n",
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out == []


def test_missing_timestamp_keys_skipped():
    issue = {"state": "running", "labels": [], "id": "X", "name": "n"}
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out == []


def test_title_truncated_to_80_chars():
    long_name = "a" * 200
    issue = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(hours=5)),
        labels=[],
        name=long_name,
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out[0].title == "a" * 80


def test_missing_name_yields_empty_title():
    issue = {
        "state": "running",
        "updated_at": _iso(_NOW - timedelta(hours=5)),
        "labels": [],
        "id": "X",
        "name": None,
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out[0].title == ""


def test_missing_id_yields_empty_task_id():
    issue = {
        "state": "running",
        "updated_at": _iso(_NOW - timedelta(hours=5)),
        "labels": [],
        "name": "n",
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out[0].task_id == ""


def test_task_id_stringified():
    issue = {
        "state": "running",
        "updated_at": _iso(_NOW - timedelta(hours=5)),
        "labels": [],
        "id": 12345,
        "name": "n",
    }
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert out[0].task_id == "12345"


def test_empty_issue_list():
    assert reconcile_stale_running_issues([], now=_NOW) == []


def test_multiple_issues_mixed():
    stale = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(hours=5)),
        labels=[{"name": "task-kind:goal"}],
        issue_id="STALE",
    )
    fresh = _issue(
        state="running",
        updated_at=_iso(_NOW - timedelta(minutes=1)),
        labels=[{"name": "task-kind:goal"}],
        issue_id="FRESH",
    )
    done = _issue(state="done", updated_at=_iso(_NOW - timedelta(hours=99)))
    out = reconcile_stale_running_issues([stale, fresh, done], now=_NOW)
    assert [c.task_id for c in out] == ["STALE"]


def test_candidate_is_frozen_dataclass():
    c = StaleRunningCandidate(
        task_id="i", title="t", task_kind="goal", age_minutes=1, ttl_minutes=2
    )
    with pytest.raises(Exception):
        c.task_id = "other"  # type: ignore[misc]


def test_running_label_state_case_insensitive():
    issue = _issue(
        state="RUNNING",
        updated_at=_iso(_NOW - timedelta(hours=5)),
        labels=[],
    )
    out = reconcile_stale_running_issues([issue], now=_NOW)
    assert len(out) == 1
