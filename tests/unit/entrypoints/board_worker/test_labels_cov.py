# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from unittest.mock import MagicMock

from operations_center.entrypoints.board_worker import labels as mod


# ── label_value ────────────────────────────────────────────────────────────


def test_label_value_dict_match():
    labs = [{"name": "priority: high"}]
    assert mod.label_value(labs, "priority") == "high"


def test_label_value_case_insensitive_prefix():
    labs = [{"name": "Priority: High"}]
    assert mod.label_value(labs, "priority") == "High"


def test_label_value_strips_whitespace():
    labs = [{"name": "  priority:   medium  "}]
    assert mod.label_value(labs, "priority") == "medium"


def test_label_value_string_label():
    labs = ["kind: goal"]
    assert mod.label_value(labs, "kind") == "goal"


def test_label_value_no_name_key():
    labs = [{"color": "red"}]
    assert mod.label_value(labs, "priority") == ""


def test_label_value_no_match_returns_empty():
    labs = [{"name": "other: x"}]
    assert mod.label_value(labs, "priority") == ""


def test_label_value_empty_list():
    assert mod.label_value([], "priority") == ""


def test_label_value_first_match_wins():
    labs = [{"name": "p: a"}, {"name": "p: b"}]
    assert mod.label_value(labs, "p") == "a"


# ── has_label ──────────────────────────────────────────────────────────────


def test_has_label_true_dict():
    assert mod.has_label([{"name": "Running"}], "running") is True


def test_has_label_true_string():
    assert mod.has_label(["Done"], "done") is True


def test_has_label_false():
    assert mod.has_label([{"name": "Ready"}], "running") is False


def test_has_label_empty():
    assert mod.has_label([], "running") is False


def test_has_label_strips():
    assert mod.has_label([{"name": "  Blocked  "}], "blocked") is True


# ── retry_count_from_labels ──────────────────────────────────────────────────


def test_retry_count_parsed():
    assert mod.retry_count_from_labels([{"name": "retry-count: 3"}]) == 3


def test_retry_count_string_label():
    assert mod.retry_count_from_labels(["retry-count: 7"]) == 7


def test_retry_count_invalid_value_returns_zero():
    assert mod.retry_count_from_labels([{"name": "retry-count: abc"}]) == 0


def test_retry_count_absent_returns_zero():
    assert mod.retry_count_from_labels([{"name": "other"}]) == 0


def test_retry_count_empty_returns_zero():
    assert mod.retry_count_from_labels([]) == 0


def test_retry_count_case_insensitive():
    assert mod.retry_count_from_labels([{"name": "Retry-Count: 5"}]) == 5


# ── add_label ────────────────────────────────────────────────────────────────


def test_add_label_appends_new():
    client = MagicMock()
    issue = {"id": 42, "labels": [{"name": "Running"}]}
    mod.add_label(client, issue, "new-tag")
    client.update_issue_labels.assert_called_once_with("42", ["Running", "new-tag"])


def test_add_label_skips_when_present():
    client = MagicMock()
    issue = {"id": 1, "labels": [{"name": "existing"}]}
    mod.add_label(client, issue, "existing")
    client.update_issue_labels.assert_not_called()


def test_add_label_filters_blank_names():
    client = MagicMock()
    issue = {"id": 9, "labels": [{"name": "  "}, {"name": "keep"}]}
    mod.add_label(client, issue, "added")
    client.update_issue_labels.assert_called_once_with("9", ["keep", "added"])


def test_add_label_string_labels():
    client = MagicMock()
    issue = {"id": 5, "labels": ["a", "b"]}
    mod.add_label(client, issue, "c")
    client.update_issue_labels.assert_called_once_with("5", ["a", "b", "c"])


def test_add_label_no_labels_key():
    client = MagicMock()
    issue = {"id": 7}
    mod.add_label(client, issue, "first")
    client.update_issue_labels.assert_called_once_with("7", ["first"])


def test_add_label_swallows_exception(caplog):
    client = MagicMock()
    client.update_issue_labels.side_effect = RuntimeError("boom")
    issue = {"id": 3, "labels": []}
    with caplog.at_level("WARNING"):
        mod.add_label(client, issue, "x")
    assert "failed to add label" in caplog.text


# ── increment_retry_count ────────────────────────────────────────────────────


def test_increment_from_existing_count():
    client = MagicMock()
    issue = {"id": 11, "labels": [{"name": "keep"}, {"name": "retry-count: 2"}]}
    mod.increment_retry_count(client, issue)
    client.update_issue_labels.assert_called_once_with("11", ["keep", "retry-count: 3"])


def test_increment_when_absent_adds_one():
    client = MagicMock()
    issue = {"id": 12, "labels": [{"name": "keep"}]}
    mod.increment_retry_count(client, issue)
    client.update_issue_labels.assert_called_once_with("12", ["keep", "retry-count: 1"])


def test_increment_invalid_count_treated_as_zero():
    client = MagicMock()
    issue = {"id": 13, "labels": [{"name": "retry-count: oops"}]}
    mod.increment_retry_count(client, issue)
    client.update_issue_labels.assert_called_once_with("13", ["retry-count: 1"])


def test_increment_filters_blank_labels():
    client = MagicMock()
    issue = {"id": 14, "labels": [{"name": ""}, {"name": "retry-count: 4"}]}
    mod.increment_retry_count(client, issue)
    client.update_issue_labels.assert_called_once_with("14", ["retry-count: 5"])


def test_increment_string_labels():
    client = MagicMock()
    issue = {"id": 15, "labels": ["retry-count: 9"]}
    mod.increment_retry_count(client, issue)
    client.update_issue_labels.assert_called_once_with("15", ["retry-count: 10"])


def test_increment_swallows_exception(caplog):
    client = MagicMock()
    client.update_issue_labels.side_effect = ValueError("nope")
    issue = {"id": 16, "labels": []}
    with caplog.at_level("WARNING"):
        mod.increment_retry_count(client, issue)
    assert "failed to increment retry-count" in caplog.text


# ── module constants ─────────────────────────────────────────────────────────


def test_state_constants():
    assert mod.STATE_READY == "Ready for AI"
    assert mod.STATE_RUNNING == "Running"
    assert mod.STATE_DONE == "Done"
    assert mod.STATE_BLOCKED == "Blocked"
    assert mod.STATE_REVIEW == "In Review"


def test_lifecycle_and_role_kinds():
    assert mod.LIFECYCLE_EXPANDED == "lifecycle: expanded"
    assert mod.ROLE_KINDS["test"] == ["test", "test_campaign"]
    assert "goal" in mod.ROLE_KINDS["goal"]


def test_github_dir_under_home():
    assert mod.GITHUB_DIR.name == "GitHub"
    assert str(mod.GITHUB_DIR).endswith("Documents/GitHub")
