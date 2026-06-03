# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from operations_center.priority_scans import (
    AwaitingInputResult,
    PriorityRescoreCandidate,
    handle_awaiting_input_scan,
    handle_priority_rescore_scan,
    issue_urgency_score,
    signal_stale,
)

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ── issue_urgency_score ──────────────────────────────────────────────────────


def test_urgency_score_zero_for_empty_issue():
    assert issue_urgency_score({}, now=_NOW) == 0.0


def test_urgency_score_age_component_caps_at_06():
    old = _iso(_NOW - timedelta(days=365))
    score = issue_urgency_score({"created_at": old}, now=_NOW)
    assert score == 0.6


def test_urgency_score_age_partial():
    # 15 days old -> 15/30 = 0.5
    ts = _iso(_NOW - timedelta(days=15))
    score = issue_urgency_score({"created_at": ts}, now=_NOW)
    assert score == 0.5


def test_urgency_score_uses_updated_at_when_no_created_at():
    ts = _iso(_NOW - timedelta(days=30))
    score = issue_urgency_score({"updated_at": ts}, now=_NOW)
    assert score == 0.6


def test_urgency_score_invalid_timestamp_ignored():
    # ValueError path: bad iso string, no age contribution
    score = issue_urgency_score({"created_at": "not-a-date"}, now=_NOW)
    assert score == 0.0


def test_urgency_score_non_string_timestamp_attribute_error():
    # AttributeError path: int has no .replace returning iso-able value
    score = issue_urgency_score({"created_at": 12345}, now=_NOW)
    assert score == 0.0


def test_urgency_score_escalated_label_boost():
    score = issue_urgency_score({"labels": [{"name": "lifecycle: escalated"}]}, now=_NOW)
    assert score == 0.3


def test_urgency_score_string_labels_supported():
    score = issue_urgency_score({"labels": ["lifecycle: escalated"]}, now=_NOW)
    assert score == 0.3


def test_urgency_score_retry_count_boost_capped():
    # retry-count: 5 -> 0.1 * min(5,3) = 0.3
    score = issue_urgency_score({"labels": ["retry-count: 5"]}, now=_NOW)
    assert score == 0.3


def test_urgency_score_retry_count_single():
    score = issue_urgency_score({"labels": ["retry-count: 1"]}, now=_NOW)
    assert score == 0.1


def test_urgency_score_retry_count_zero_no_boost():
    # N < 1 -> no boost
    score = issue_urgency_score({"labels": ["retry-count: 0"]}, now=_NOW)
    assert score == 0.0


def test_urgency_score_retry_count_invalid_value():
    # ValueError on int() parse -> ignored, break still hit
    score = issue_urgency_score({"labels": ["retry-count: abc"]}, now=_NOW)
    assert score == 0.0


def test_urgency_score_total_caps_at_one():
    old = _iso(_NOW - timedelta(days=365))
    score = issue_urgency_score(
        {
            "created_at": old,
            "labels": ["lifecycle: escalated", "retry-count: 9"],
        },
        now=_NOW,
    )
    # 0.6 + 0.3 + 0.3 = 1.2 -> capped to 1.0
    assert score == 1.0


def test_urgency_score_labels_none_value():
    # labels explicitly None -> "or []" branch
    score = issue_urgency_score({"labels": None}, now=_NOW)
    assert score == 0.0


def test_urgency_score_default_now(monkeypatch):
    # now=None -> uses datetime.now(UTC); recent ts -> tiny score
    score = issue_urgency_score({"created_at": _iso(datetime.now(UTC))})
    assert 0.0 <= score < 0.1


# ── handle_priority_rescore_scan ─────────────────────────────────────────────


def test_rescore_skips_non_backlog():
    issues = [{"id": "1", "state": {"name": "In Progress"}, "priority": "low"}]
    assert handle_priority_rescore_scan(issues, now=_NOW) == []


def test_rescore_high_urgency_low_priority_promotes():
    old = _iso(_NOW - timedelta(days=365))
    issues = [
        {
            "id": "abc",
            "name": "Old urgent task",
            "state": {"name": "Backlog"},
            "priority": "low",
            "created_at": old,
        }
    ]
    out = handle_priority_rescore_scan(issues, now=_NOW)
    assert len(out) == 1
    cand = out[0]
    assert isinstance(cand, PriorityRescoreCandidate)
    assert cand.proposed_priority == "high"
    assert cand.current_priority == "low"
    assert cand.task_id == "abc"
    assert "urgency_score" in cand.reason


def test_rescore_low_urgency_high_priority_demotes():
    issues = [
        {
            "id": "x",
            "name": "Stale high",
            "state": {"name": "backlog"},
            "priority": "urgent",
        }
    ]
    out = handle_priority_rescore_scan(issues, now=_NOW)
    assert len(out) == 1
    assert out[0].proposed_priority == "low"
    assert out[0].current_priority == "urgent"


def test_rescore_no_change_when_aligned():
    # high urgency, already high priority -> no candidate
    old = _iso(_NOW - timedelta(days=365))
    issues = [
        {
            "id": "y",
            "state": {"name": "Backlog"},
            "priority": "high",
            "created_at": old,
        }
    ]
    assert handle_priority_rescore_scan(issues, now=_NOW) == []


def test_rescore_medium_urgency_no_change():
    # score 0.5 (15 days), priority low -> neither branch fires
    ts = _iso(_NOW - timedelta(days=15))
    issues = [
        {
            "id": "z",
            "state": {"name": "Backlog"},
            "priority": "low",
            "created_at": ts,
        }
    ]
    assert handle_priority_rescore_scan(issues, now=_NOW) == []


def test_rescore_state_as_string():
    old = _iso(_NOW - timedelta(days=365))
    issues = [
        {
            "id": "s",
            "state": "Backlog",
            "priority": "none",
            "created_at": old,
        }
    ]
    out = handle_priority_rescore_scan(issues, now=_NOW)
    assert len(out) == 1
    assert out[0].proposed_priority == "high"


def test_rescore_missing_priority_defaults_none():
    old = _iso(_NOW - timedelta(days=365))
    issues = [{"id": "n", "state": {"name": "Backlog"}, "created_at": old}]
    out = handle_priority_rescore_scan(issues, now=_NOW)
    assert out[0].current_priority == "none"
    assert out[0].proposed_priority == "high"


def test_rescore_state_none():
    # state None -> str(None or "") -> "" -> not backlog
    assert handle_priority_rescore_scan([{"id": "q", "state": None}], now=_NOW) == []


def test_rescore_title_truncated_to_80():
    old = _iso(_NOW - timedelta(days=365))
    long_name = "T" * 200
    issues = [
        {
            "id": "t",
            "name": long_name,
            "state": {"name": "Backlog"},
            "priority": "low",
            "created_at": old,
        }
    ]
    out = handle_priority_rescore_scan(issues, now=_NOW)
    assert len(out[0].title) == 80


def test_rescore_default_now_branch():
    # now=None path exercised; recent backlog low -> no change
    issues = [
        {
            "id": "d",
            "state": {"name": "Backlog"},
            "priority": "low",
            "created_at": _iso(datetime.now(UTC)),
        }
    ]
    assert handle_priority_rescore_scan(issues) == []


# ── handle_awaiting_input_scan ───────────────────────────────────────────────


def test_awaiting_skips_other_states():
    client = MagicMock()
    issues = [{"id": "1", "state": {"name": "Backlog"}}]
    out = handle_awaiting_input_scan(issues, client)
    assert out == []
    client.list_comments.assert_not_called()


def test_awaiting_returns_operator_comments():
    client = MagicMock()
    client.list_comments.return_value = [
        {"comment_html": "Please look at this"},
        {"comment_stripped": "another note"},
    ]
    issues = [{"id": "42", "name": "Needs input", "state": {"name": "Awaiting Input"}}]
    out = handle_awaiting_input_scan(issues, client)
    assert len(out) == 1
    res = out[0]
    assert isinstance(res, AwaitingInputResult)
    assert res.task_id == "42"
    assert res.new_comment_count == 2
    client.list_comments.assert_called_once_with("42")


def test_awaiting_filters_bot_comments():
    client = MagicMock()
    client.list_comments.return_value = [
        {"comment_html": "<!-- operations-center bot -->"},
        {"comment_stripped": "<!-- OPERATIONS-CENTER something -->"},
    ]
    issues = [{"id": "9", "state": {"name": "Awaiting Input"}}]
    out = handle_awaiting_input_scan(issues, client)
    # all comments are bot comments -> no result
    assert out == []


def test_awaiting_comment_fetch_failure_skipped():
    client = MagicMock()
    client.list_comments.side_effect = RuntimeError("boom")
    issues = [{"id": "5", "state": {"name": "Awaiting Input"}}]
    out = handle_awaiting_input_scan(issues, client)
    assert out == []


def test_awaiting_custom_state_name():
    client = MagicMock()
    client.list_comments.return_value = [{"comment_html": "hi"}]
    issues = [{"id": "7", "state": {"name": "Blocked"}}]
    out = handle_awaiting_input_scan(issues, client, state_name="Blocked")
    assert len(out) == 1
    assert out[0].task_id == "7"


def test_awaiting_state_as_string():
    client = MagicMock()
    client.list_comments.return_value = [{"comment_html": "operator says hi"}]
    issues = [{"id": "8", "state": "Awaiting Input", "name": "x"}]
    out = handle_awaiting_input_scan(issues, client)
    assert len(out) == 1


def test_awaiting_empty_comments_no_result():
    client = MagicMock()
    client.list_comments.return_value = []
    issues = [{"id": "11", "state": {"name": "Awaiting Input"}}]
    out = handle_awaiting_input_scan(issues, client)
    assert out == []


def test_awaiting_missing_id_and_name():
    client = MagicMock()
    client.list_comments.return_value = [{"comment_html": "real comment"}]
    issues = [{"state": {"name": "Awaiting Input"}}]
    out = handle_awaiting_input_scan(issues, client)
    assert out[0].task_id == ""
    assert out[0].title == ""


def test_awaiting_state_none_skipped():
    client = MagicMock()
    issues = [{"id": "z", "state": None}]
    out = handle_awaiting_input_scan(issues, client)
    assert out == []
    client.list_comments.assert_not_called()


# ── signal_stale ─────────────────────────────────────────────────────────────


def test_signal_stale_none_is_stale():
    assert signal_stale(None) is True


def test_signal_stale_below_threshold():
    assert signal_stale(10.0) is False


def test_signal_stale_at_threshold():
    assert signal_stale(48.0) is True


def test_signal_stale_above_threshold():
    assert signal_stale(100.0) is True


def test_signal_stale_custom_threshold():
    assert signal_stale(5.0, threshold_hours=4.0) is True
    assert signal_stale(3.0, threshold_hours=4.0) is False
