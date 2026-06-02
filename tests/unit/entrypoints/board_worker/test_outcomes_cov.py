# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations_center.entrypoints.board_worker import outcomes
from operations_center.entrypoints.board_worker.labels import (
    LIFECYCLE_EXPANDED,
    STATE_BLOCKED,
    STATE_DONE,
    STATE_REVIEW,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_client(create_id="new-1"):
    client = MagicMock()
    client.create_issue.return_value = {"id": create_id}
    client.list_issues.return_value = []
    return client


def _make_settings(repos=None):
    return SimpleNamespace(repos=repos or {})


def _make_repo_cfg(await_review=False, local_path=None, validation_commands=None):
    kw = {"await_review": await_review}
    if local_path is not None:
        kw["local_path"] = local_path
    if validation_commands is not None:
        kw["validation_commands"] = validation_commands
    return SimpleNamespace(**kw)


# ── fail_task ───────────────────────────────────────────────────────────────


def test_fail_task_happy():
    client = _make_client()
    outcomes.fail_task(client, "t1", "goal", "boom")
    client.transition_issue.assert_called_once_with("t1", STATE_BLOCKED)
    client.comment_issue.assert_called_once()
    assert "boom" in client.comment_issue.call_args[0][1]


def test_fail_task_swallows_exception(caplog):
    client = _make_client()
    client.transition_issue.side_effect = RuntimeError("net down")
    # Should not raise; the exception is swallowed and logged.
    result = outcomes.fail_task(client, "t1", "goal", "boom")
    assert result is None
    client.transition_issue.assert_called_once_with("t1", STATE_BLOCKED)
    # comment_issue is in the same try block after the failing transition, so it
    # never runs.
    client.comment_issue.assert_not_called()


# ── read_improve_output ───────────────────────────────────────────────────────


def test_read_improve_output_missing(tmp_path):
    assert outcomes.read_improve_output(tmp_path) == []


def test_read_improve_output_malformed(tmp_path):
    (tmp_path / "improve-output.json").write_text("not json{", encoding="utf-8")
    assert outcomes.read_improve_output(tmp_path) == []


def test_read_improve_output_suggestions_not_list(tmp_path):
    (tmp_path / "improve-output.json").write_text(
        json.dumps({"suggestions": "nope"}), encoding="utf-8"
    )
    assert outcomes.read_improve_output(tmp_path) == []


def test_read_improve_output_missing_key(tmp_path):
    (tmp_path / "improve-output.json").write_text(json.dumps({}), encoding="utf-8")
    assert outcomes.read_improve_output(tmp_path) == []


def test_read_improve_output_filters_and_caps(tmp_path):
    suggestions = [{"title": f"t{i}"} for i in range(8)]
    # Add a non-dict and a dict without title — both filtered out.
    suggestions.append("string-item")
    suggestions.append({"no_title": 1})
    (tmp_path / "improve-output.json").write_text(
        json.dumps({"suggestions": suggestions}), encoding="utf-8"
    )
    out = outcomes.read_improve_output(tmp_path)
    # Only first 5 considered; all have titles.
    assert len(out) == 5
    assert all(item["title"] for item in out)


# ── handle_success ────────────────────────────────────────────────────────────


def test_handle_success_goal_needs_verification():
    client = _make_client(create_id="follow-9")
    issue = {"id": "g1", "labels": [{"name": "repo: web"}]}
    settings = _make_settings({"web": _make_repo_cfg(await_review=False)})
    outcomes.handle_success(client, issue, "goal", "goal", True, settings)
    # transition to DONE and a verification follow-up comment.
    client.transition_issue.assert_any_call("g1", STATE_DONE)
    create_call = client.create_issue.call_args
    assert create_call.kwargs["label_names"][0] == "task-kind: test"
    assert any("verification task #follow-9" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_success_goal_await_review_with_pr():
    client = _make_client()
    issue = {"id": "g2", "labels": [{"name": "repo: web"}]}
    settings = _make_settings({"web": _make_repo_cfg(await_review=True)})
    outcomes.handle_success(client, issue, "goal", "goal", False, settings, pr_url="http://pr/1")
    client.transition_issue.assert_any_call("g2", STATE_REVIEW)
    # add_label sets the pr-url through update_issue_labels.
    assert client.update_issue_labels.called
    labels_set = client.update_issue_labels.call_args[0][1]
    assert "pr-url: http://pr/1" in labels_set


def test_handle_success_goal_await_review_no_pr():
    client = _make_client()
    issue = {"id": "g3", "labels": [{"name": "repo: web"}]}
    settings = _make_settings({"web": _make_repo_cfg(await_review=True)})
    outcomes.handle_success(client, issue, "goal", "goal", False, settings, pr_url=None)
    client.transition_issue.assert_any_call("g3", STATE_REVIEW)
    # No pr-url label added.
    if client.update_issue_labels.called:
        assert all(
            "pr-url" not in lbl
            for call in client.update_issue_labels.mock_calls
            for lbl in call.args[1]
        )


def test_handle_success_goal_plain_done():
    client = _make_client()
    issue = {"id": "g4", "labels": [{"name": "repo: web"}]}
    settings = _make_settings({"web": _make_repo_cfg(await_review=False)})
    outcomes.handle_success(client, issue, "goal", "goal", False, settings)
    client.transition_issue.assert_any_call("g4", STATE_DONE)
    assert any("Implementation complete" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_success_goal_no_repo_key():
    client = _make_client()
    issue = {"id": "g5", "labels": []}
    settings = _make_settings({})
    outcomes.handle_success(client, issue, "goal", "goal", False, settings)
    client.transition_issue.assert_any_call("g5", STATE_DONE)


def test_handle_success_goal_repo_key_missing_in_settings():
    # repo_key present but settings.repos.get returns None → await_review False.
    client = _make_client()
    issue = {"id": "g6", "labels": [{"name": "repo: ghost"}]}
    settings = _make_settings({})
    outcomes.handle_success(client, issue, "goal", "goal", False, settings)
    client.transition_issue.assert_any_call("g6", STATE_DONE)


def test_handle_success_test_role():
    client = _make_client()
    issue = {"id": "t1", "labels": []}
    settings = _make_settings()
    outcomes.handle_success(client, issue, "test", "test", False, settings)
    client.transition_issue.assert_any_call("t1", STATE_DONE)
    assert any("Verification passed" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_success_improve_with_suggestions():
    client = _make_client(create_id="imp-1")
    issue = {"id": "i1", "labels": [{"name": "repo: web"}]}
    settings = _make_settings({"web": _make_repo_cfg()})
    suggestions = [{"title": "Add tests"}, {"title": "Refactor"}]
    outcomes.handle_success(
        client, issue, "improve", "improve", False, settings, improve_suggestions=suggestions
    )
    client.transition_issue.assert_any_call("i1", STATE_DONE)
    assert any("focused follow-up task(s)" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_success_improve_suggestions_none_enqueued(monkeypatch):
    client = _make_client()
    issue = {"id": "i2", "labels": [{"name": "repo: web"}]}
    settings = _make_settings({"web": _make_repo_cfg()})
    # Force create_improve_follow_up to return None (no ids).
    monkeypatch.setattr(outcomes, "create_improve_follow_up", lambda *a, **k: None)
    outcomes.handle_success(
        client,
        issue,
        "improve",
        "improve",
        False,
        settings,
        improve_suggestions=[{"title": "x"}],
    )
    assert any("none could be enqueued" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_success_improve_no_suggestions():
    client = _make_client()
    issue = {"id": "i3", "labels": []}
    settings = _make_settings()
    outcomes.handle_success(client, issue, "improve", "improve", False, settings)
    assert any("no actionable suggestions" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_success_transition_exception_swallowed():
    client = _make_client()
    client.transition_issue.side_effect = RuntimeError("api down")
    issue = {"id": "g7", "labels": []}
    settings = _make_settings()
    # Should not raise; the transition error is swallowed and logged.
    result = outcomes.handle_success(client, issue, "goal", "goal", False, settings)
    assert result is None
    client.transition_issue.assert_any_call("g7", STATE_DONE)
    # The exception is raised before the success comment is posted.
    client.comment_issue.assert_not_called()


def test_handle_success_close_parent_exception_swallowed(monkeypatch):
    client = _make_client()
    issue = {"id": "g8", "labels": []}
    settings = _make_settings()
    closer = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(outcomes, "maybe_close_split_parent", closer)
    # Should not raise despite close-parent failing.
    result = outcomes.handle_success(client, issue, "goal", "goal", False, settings)
    assert result is None
    # The success path still ran to completion before the swallowed close failure.
    client.transition_issue.assert_any_call("g8", STATE_DONE)
    closer.assert_called_once_with(client, issue)


# ── maybe_close_split_parent ──────────────────────────────────────────────────


def test_maybe_close_not_a_split_child():
    client = _make_client()
    issue = {"id": "c1", "labels": [{"name": "task-kind: goal"}]}
    outcomes.maybe_close_split_parent(client, issue)
    client.list_issues.assert_not_called()


def test_maybe_close_no_parent_id():
    client = _make_client()
    issue = {"id": "c1", "labels": [{"name": "source: scope-split"}]}
    outcomes.maybe_close_split_parent(client, issue)
    client.list_issues.assert_not_called()


def test_maybe_close_list_issues_raises():
    client = _make_client()
    client.list_issues.side_effect = RuntimeError("net")
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
    }
    # No raise; just returns.
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_not_called()


def test_maybe_close_parent_not_found():
    client = _make_client()
    client.list_issues.return_value = [{"id": "other", "labels": []}]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_not_called()


def test_maybe_close_parent_not_blocked():
    client = _make_client()
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Done"}},
    ]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
        "state": {"name": "Done"},
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_not_called()


def test_maybe_close_sibling_not_done():
    client = _make_client()
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Blocked"}},
        {
            "id": "sib",
            "labels": [
                {"name": "source: scope-split"},
                {"name": "original-task-id: P"},
            ],
            "state": {"name": "Running"},
        },
    ]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
        "state": {"name": "Done"},
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_not_called()


def test_maybe_close_this_state_invalid():
    client = _make_client()
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Blocked"}},
    ]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
        "state": {"name": "Blocked"},  # not in allowed set
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_not_called()


def test_maybe_close_success():
    client = _make_client()
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Blocked"}},
        {
            "id": "sib",
            "labels": [
                {"name": "source: scope-split"},
                {"name": "original-task-id: P"},
            ],
            "state": {"name": "Done"},
        },
    ]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
        "state": {"name": "Done"},
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_called_once_with("P", STATE_DONE)
    assert any("Auto-closed" in c.args[1] for c in client.comment_issue.mock_calls)


def test_maybe_close_success_empty_this_state():
    # this_state == "" is allowed (the `if this_state and ...` short circuits).
    client = _make_client()
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Blocked"}},
    ]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
        # no state → ""
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_called_once_with("P", STATE_DONE)


def test_maybe_close_transition_raises():
    client = _make_client()
    client.transition_issue.side_effect = RuntimeError("api")
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Blocked"}},
    ]
    issue = {
        "id": "c1",
        "labels": [{"name": "source: scope-split"}, {"name": "original-task-id: P"}],
        "state": {"name": "Done"},
    }
    # No raise; the failing parent transition is swallowed.
    result = outcomes.maybe_close_split_parent(client, issue)
    assert result is None
    # The auto-close transition on the parent was still attempted.
    client.transition_issue.assert_called_once_with("P", STATE_DONE)


def test_maybe_close_sibling_string_labels():
    # Exercise the str(lab) branch for non-dict labels.
    client = _make_client()
    client.list_issues.return_value = [
        {"id": "P", "labels": [], "state": {"name": "Blocked"}},
        {
            "id": "sib",
            "labels": ["source: scope-split", "original-task-id: P"],
            "state": {"name": "Done"},
        },
    ]
    issue = {
        "id": "c1",
        "labels": ["source: scope-split", "original-task-id: P"],
        "state": {"name": "Done"},
    }
    outcomes.maybe_close_split_parent(client, issue)
    client.transition_issue.assert_called_once_with("P", STATE_DONE)


# ── split_files_into_chunks ───────────────────────────────────────────────────


def test_split_files_empty():
    assert outcomes.split_files_into_chunks([]) == []


def test_split_files_single_group():
    files = ["a.py", "b.py", "c.py"]
    chunks = outcomes.split_files_into_chunks(files, chunk_size=2)
    assert chunks == [["a.py", "b.py"], ["c.py"]]


def test_split_files_groups_by_top_dir():
    files = ["src/a.py", "src/b.py", "tests/c.py"]
    chunks = outcomes.split_files_into_chunks(files, chunk_size=15)
    # Two top-level groups → two chunks.
    assert len(chunks) == 2


def test_split_files_merge_to_max_chunks():
    # Many distinct top dirs each one file → many chunks merged down to max_chunks.
    files = [f"d{i}/f.py" for i in range(10)]
    chunks = outcomes.split_files_into_chunks(files, chunk_size=1, max_chunks=3)
    assert len(chunks) == 3
    flat = sorted(f for c in chunks for f in c)
    assert flat == sorted(files)


# ── create_split_followups ────────────────────────────────────────────────────


def test_create_split_followups_retry_exhausted():
    client = _make_client()
    parent = {"id": "P", "name": "Big", "labels": [{"name": "retry-count: 2"}]}
    result = outcomes.create_split_followups(client, parent, None, ["a.py"], "r")
    assert result == []
    client.create_issue.assert_not_called()


def test_create_split_followups_no_chunks():
    client = _make_client()
    parent = {"id": "P", "name": "Big", "labels": []}
    result = outcomes.create_split_followups(client, parent, None, [], "r")
    assert result == []


def test_create_split_followups_happy():
    client = _make_client(create_id="child-1")
    parent = {
        "id": "P",
        "name": "Big Task",
        "labels": [
            {"name": "repo: web"},
            {"name": "source: campaign-x"},
            {"name": "source: board_worker"},
        ],
    }
    result = outcomes.create_split_followups(
        client, parent, None, ["src/a.py", "src/b.py"], "scope_too_wide_split"
    )
    assert result == ["child-1"]
    labels = client.create_issue.call_args.kwargs["label_names"]
    assert "task-kind: goal" in labels
    assert "source: scope-split" in labels
    assert "source: campaign-x" in labels  # inherited
    assert "source: board_worker" in [
        lbl for lbl in labels
    ]  # explicit, not duplicated as inherited
    assert "original-task-id: P" in labels
    assert "retry-count: 1" in labels
    # LIFECYCLE_EXPANDED applied via add_label → update_issue_labels.
    applied = [lbl for c in client.update_issue_labels.mock_calls for lbl in c.args[1]]
    assert LIFECYCLE_EXPANDED in applied


def test_create_split_followups_create_raises():
    client = _make_client()
    client.create_issue.side_effect = RuntimeError("api down")
    parent = {"id": "P", "name": "Big", "labels": [{"name": "repo: web"}]}
    result = outcomes.create_split_followups(client, parent, None, ["a.py"], "r")
    assert result == []
    # No expansion label since nothing created.
    assert not client.update_issue_labels.called


def test_create_split_followups_blank_new_id():
    client = _make_client(create_id="")
    parent = {"id": "P", "name": "Big", "labels": [{"name": "repo: web"}]}
    result = outcomes.create_split_followups(client, parent, None, ["a.py"], "r")
    assert result == []


# ── handle_failure ────────────────────────────────────────────────────────────


def test_handle_failure_test_role_creates_follow_up():
    client = _make_client(create_id="g-9")
    issue = {"id": "t1", "labels": [{"name": "repo: web"}]}
    settings = _make_settings()
    result = {"status": "fail", "failure_category": "test_failure", "failure_reason": "nope"}
    outcomes.handle_failure(client, issue, "test", "test", result, settings)
    client.transition_issue.assert_any_call("t1", STATE_BLOCKED)
    create_labels = client.create_issue.call_args.kwargs["label_names"]
    assert create_labels[0] == "task-kind: goal"
    assert any("follow-up goal task #g-9" in c.args[1] for c in client.comment_issue.mock_calls)


def test_handle_failure_goal_basic():
    client = _make_client()
    issue = {"id": "g1", "labels": []}
    settings = _make_settings()
    result = {}  # all defaults
    outcomes.handle_failure(client, issue, "goal", "goal", result, settings)
    client.transition_issue.assert_called_once_with("g1", STATE_BLOCKED)
    comment = client.comment_issue.call_args[0][1]
    assert "unknown" in comment
    assert "(no reason provided)" in comment


def test_handle_failure_scope_split():
    client = _make_client(create_id="child-1")
    issue = {
        "id": "g2",
        "labels": [{"name": "repo: web"}],
        "name": "Wide",
    }
    settings = _make_settings()
    result = {"failure_category": "scope_too_wide"}
    outcomes.handle_failure(
        client, issue, "goal", "goal", result, settings, scope_files=["src/a.py"]
    )
    comment = client.comment_issue.call_args[0][1]
    assert "Auto-split into 1 focused task(s)" in comment


def test_handle_failure_scope_split_spawn_raises(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        outcomes,
        "create_split_followups",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    issue = {"id": "g3", "labels": [], "name": "Wide"}
    settings = _make_settings()
    result = {"failure_category": "scope_too_wide"}
    # Should not raise; split_ids stays empty.
    outcomes.handle_failure(client, issue, "goal", "goal", result, settings, scope_files=["a.py"])
    client.transition_issue.assert_any_call("g3", STATE_BLOCKED)


def test_handle_failure_executor_exit_and_signal_sigkill():
    client = _make_client()
    issue = {"id": "g4", "labels": []}
    settings = _make_settings()
    result = {
        "executor_exit_code": 137,
        "executor_signal": "SIGKILL",
    }
    outcomes.handle_failure(client, issue, "goal", "goal", result, settings)
    comment = client.comment_issue.call_args[0][1]
    assert "executor-exit-code: 137" in comment
    assert "executor-signal: SIGKILL" in comment
    # add_label for exit code & signal, plus increment_retry_count → update_issue_labels.
    applied = [lbl for c in client.update_issue_labels.mock_calls for lbl in c.args[1]]
    assert "executor-exit-code: 137" in applied
    assert "executor-signal: SIGKILL" in applied
    # increment_retry_count adds retry-count: 1.
    assert any("retry-count: 1" in lbl for lbl in applied)


def test_handle_failure_exit_code_zero_no_signal():
    client = _make_client()
    issue = {"id": "g5", "labels": []}
    settings = _make_settings()
    result = {"executor_exit_code": 0}
    outcomes.handle_failure(client, issue, "goal", "goal", result, settings)
    comment = client.comment_issue.call_args[0][1]
    assert "executor-exit-code: 0" in comment
    assert "executor-signal" not in comment


def test_handle_failure_signal_non_sigkill_no_retry_bump():
    client = _make_client()
    issue = {"id": "g6", "labels": []}
    settings = _make_settings()
    result = {"executor_exit_code": 1, "executor_signal": "SIGTERM"}
    outcomes.handle_failure(client, issue, "goal", "goal", result, settings)
    applied = [lbl for c in client.update_issue_labels.mock_calls for lbl in c.args[1]]
    # No retry-count bump for non-sigkill.
    assert not any("retry-count" in lbl for lbl in applied)


def test_handle_failure_transition_raises():
    client = _make_client()
    client.transition_issue.side_effect = RuntimeError("api")
    issue = {"id": "g7", "labels": []}
    settings = _make_settings()
    # No raise; the failing transition is swallowed.
    result = outcomes.handle_failure(client, issue, "goal", "goal", {}, settings)
    assert result is None
    client.transition_issue.assert_called_once_with("g7", STATE_BLOCKED)
    # The block comment is never posted because the transition raised first.
    client.comment_issue.assert_not_called()


# ── create_improve_follow_up ──────────────────────────────────────────────────


def test_create_improve_follow_up_happy():
    client = _make_client(create_id="imp-7")
    parent = {
        "id": "P",
        "labels": [{"name": "repo: web"}, {"name": "source: camp"}],
    }
    suggestion = {
        "title": "Fix X",
        "rationale": "because",
        "files": ["a.py", "b.py", 123],  # non-str filtered
        "complexity": "Medium",
    }
    new_id = outcomes.create_improve_follow_up(client, parent, None, suggestion)
    assert new_id == "imp-7"
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["name"] == "Fix X"
    assert "complexity: medium" in kwargs["label_names"]
    assert "source: improve-suggestion" in kwargs["label_names"]
    assert "source: camp" in kwargs["label_names"]
    assert "- a.py" in kwargs["description"]
    assert "because" in kwargs["description"]


def test_create_improve_follow_up_defaults_and_no_files():
    client = _make_client(create_id="imp-8")
    parent = {"id": "P", "labels": []}
    suggestion = {}  # no title, no rationale, no files, bad complexity
    new_id = outcomes.create_improve_follow_up(client, parent, None, suggestion)
    assert new_id == "imp-8"
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["name"] == "Improve follow-up"
    assert "(none provided)" in kwargs["description"]
    # No complexity label for empty/invalid complexity.
    assert not any(lbl.startswith("complexity:") for lbl in kwargs["label_names"])


def test_create_improve_follow_up_files_not_list():
    client = _make_client(create_id="imp-9")
    parent = {"id": "P", "labels": []}
    suggestion = {"title": "T", "files": "notalist"}
    outcomes.create_improve_follow_up(client, parent, None, suggestion)
    kwargs = client.create_issue.call_args.kwargs
    assert "allowed_paths" not in kwargs["description"]


def test_create_improve_follow_up_blank_id_returns_none():
    client = _make_client(create_id="")
    parent = {"id": "P", "labels": []}
    result = outcomes.create_improve_follow_up(client, parent, None, {"title": "T"})
    assert result is None


def test_create_improve_follow_up_create_raises():
    client = _make_client()
    client.create_issue.side_effect = RuntimeError("api")
    parent = {"id": "P", "labels": []}
    result = outcomes.create_improve_follow_up(client, parent, None, {"title": "T"})
    assert result is None


# ── create_follow_up ──────────────────────────────────────────────────────────


def test_create_follow_up_happy_with_base_branch():
    client = _make_client(create_id="f-1")
    parent = {
        "id": "P",
        "name": "Parent Task",
        "labels": [
            {"name": "repo: web"},
            {"name": "base-branch: main"},
            {"name": "source: camp"},
        ],
    }
    new_id = outcomes.create_follow_up(client, parent, None, "test", "verification_needed")
    assert new_id == "f-1"
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["name"] == "[test] Parent Task"
    assert "base_branch: main" in kwargs["description"]
    assert "verification needed" in kwargs["description"]
    assert "task-kind: test" in kwargs["label_names"]
    assert "retry-count: 1" in kwargs["label_names"]
    assert "source: camp" in kwargs["label_names"]


def test_create_follow_up_no_base_branch():
    client = _make_client(create_id="f-2")
    parent = {"id": "P", "name": "PT", "labels": [{"name": "repo: web"}]}
    outcomes.create_follow_up(client, parent, None, "goal", "verification_failed")
    desc = client.create_issue.call_args.kwargs["description"]
    assert "base_branch" not in desc


def test_create_follow_up_retry_cap():
    client = _make_client()
    parent = {"id": "P", "name": "PT", "labels": [{"name": "retry-count: 3"}]}
    new_id = outcomes.create_follow_up(client, parent, None, "goal", "r")
    assert new_id == ""
    client.create_issue.assert_not_called()


def test_create_follow_up_missing_id_returns_question_mark():
    client = MagicMock()
    client.create_issue.return_value = {}  # no id key
    parent = {"id": "P", "name": "PT", "labels": []}
    new_id = outcomes.create_follow_up(client, parent, None, "goal", "r")
    assert new_id == "?"


# ── run_ci_loop ───────────────────────────────────────────────────────────────


@pytest.fixture
def ci_modules(monkeypatch):
    """Stub out the heavy CI collaborators imported inside run_ci_loop."""
    import sys
    import types as _types

    # Build a registry of fakes we can configure per-test.
    state = {}

    # contracts.enums.RefinementStatus
    enums_mod = _types.ModuleType("operations_center.contracts.enums")

    class RefinementStatus:
        ACCEPTED = SimpleNamespace(value="accepted")
        ESCALATED = SimpleNamespace(value="escalated")
        REJECTED = SimpleNamespace(value="rejected")

    enums_mod.RefinementStatus = RefinementStatus

    # contracts.ci.ContinuousImprovementSpec
    ci_mod = _types.ModuleType("operations_center.contracts.ci")

    class ContinuousImprovementSpec:
        @staticmethod
        def model_validate(raw):
            if state.get("spec_invalid"):
                raise ValueError("bad spec")
            return SimpleNamespace(raw=raw)

    ci_mod.ContinuousImprovementSpec = ContinuousImprovementSpec

    # execution.ci_coordinator
    coord_mod = _types.ModuleType("operations_center.execution.ci_coordinator")

    class CiRunContext:
        def __init__(self, **kw):
            self.kw = kw

    class CiCoordinator:
        def __init__(self, store):
            self.store = store

        def run(self, ctx, execute):
            if state.get("coordinator_raises"):
                raise RuntimeError("coordinator boom")
            # Optionally invoke execute to exercise the inner closure.
            if state.get("invoke_execute"):
                state["execute_return"] = execute(
                    attempt_number=1, strategy=None, proposal_id="prop"
                )
            return state["ci_result"]

    coord_mod.CiCoordinator = CiCoordinator
    coord_mod.CiRunContext = CiRunContext

    # execution.ci_store
    store_mod = _types.ModuleType("operations_center.execution.ci_store")

    class CiStore:
        def __init__(self, path):
            self.path = path

    store_mod.CiStore = CiStore

    monkeypatch.setitem(sys.modules, "operations_center.contracts.enums", enums_mod)
    monkeypatch.setitem(sys.modules, "operations_center.contracts.ci", ci_mod)
    monkeypatch.setitem(sys.modules, "operations_center.execution.ci_coordinator", coord_mod)
    monkeypatch.setitem(sys.modules, "operations_center.execution.ci_store", store_mod)

    return state, RefinementStatus


def _ci_kwargs(tmp_path, **overrides):
    base = dict(
        ci_spec_raw={"foo": "bar"},
        client=_make_client(),
        issue={"id": "task-123456789012", "labels": [{"name": "repo: web"}]},
        role="improve",
        task_kind="improve_campaign",
        task_id="task-123456789012",
        repo_key="web",
        settings=_make_settings({"web": _make_repo_cfg(local_path="/tmp/repo")}),
        python="python3",
        oc_root=tmp_path / "oc",
        env={"X": "1"},
        bundle_file=tmp_path / "props" / "p1" / "bundle.json",
        config_file=tmp_path / "config.json",
        tmp=tmp_path / "work",
        short_id="abc123",
    )
    base.update(overrides)
    (base["tmp"]).mkdir(parents=True, exist_ok=True)
    (base["bundle_file"].parent).mkdir(parents=True, exist_ok=True)
    return base


def test_run_ci_loop_invalid_spec(ci_modules, tmp_path):
    state, RS = ci_modules
    state["spec_invalid"] = True
    kw = _ci_kwargs(tmp_path)
    ok = outcomes.run_ci_loop(**kw)
    assert ok is False
    kw["client"].transition_issue.assert_any_call("task-123456789012", STATE_BLOCKED)


def test_run_ci_loop_coordinator_raises(ci_modules, tmp_path):
    state, RS = ci_modules
    state["coordinator_raises"] = True
    kw = _ci_kwargs(tmp_path)
    ok = outcomes.run_ci_loop(**kw)
    assert ok is False
    kw["client"].transition_issue.assert_any_call("task-123456789012", STATE_BLOCKED)


def test_run_ci_loop_accepted(ci_modules, tmp_path, monkeypatch):
    state, RS = ci_modules
    state["ci_result"] = SimpleNamespace(final_status=RS.ACCEPTED, total_attempts=2)
    # Spy on handle_success to confirm the accepted path.
    spy = MagicMock()
    monkeypatch.setattr(outcomes, "handle_success", spy)
    kw = _ci_kwargs(tmp_path)
    ok = outcomes.run_ci_loop(**kw)
    assert ok is True
    spy.assert_called_once()
    # ci-status / ci-attempts labels applied.
    applied = [lbl for c in kw["client"].update_issue_labels.mock_calls for lbl in c.args[1]]
    assert any("ci-status: accepted" in lbl for lbl in applied)
    assert any("ci-attempts: 2" in lbl for lbl in applied)


def test_run_ci_loop_escalated(ci_modules, tmp_path):
    state, RS = ci_modules
    state["ci_result"] = SimpleNamespace(final_status=RS.ESCALATED, total_attempts=3)
    kw = _ci_kwargs(tmp_path)
    ok = outcomes.run_ci_loop(**kw)
    assert ok is False
    kw["client"].transition_issue.assert_any_call("task-123456789012", STATE_BLOCKED)


def test_run_ci_loop_rejected_calls_handle_failure(ci_modules, tmp_path, monkeypatch):
    state, RS = ci_modules
    state["ci_result"] = SimpleNamespace(final_status=RS.REJECTED, total_attempts=1)
    spy = MagicMock()
    monkeypatch.setattr(outcomes, "handle_failure", spy)
    kw = _ci_kwargs(tmp_path)
    ok = outcomes.run_ci_loop(**kw)
    assert ok is False
    spy.assert_called_once()
    failure_result = spy.call_args[0][4]
    assert failure_result["status"] == "rejected"
    assert "CI refinement loop ended" in failure_result["failure_reason"]


def test_run_ci_loop_execute_closure(ci_modules, tmp_path, monkeypatch):
    """Drive the inner execute() closure: parse a result file successfully."""
    state, RS = ci_modules
    state["invoke_execute"] = True
    state["ci_result"] = SimpleNamespace(final_status=RS.ESCALATED, total_attempts=1)

    kw = _ci_kwargs(tmp_path)

    # Patch subprocess.run inside the module to write a result file.
    result_path = kw["tmp"] / "result-ci-1.json"

    def fake_run(cmd, **kwargs):
        result_path.write_text(
            json.dumps(
                {
                    "result": {
                        "success": True,
                        "run_id": "real-run",
                        "changed_files": [{"path": "a.py"}, {"no_path": 1}],
                    }
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(outcomes.subprocess, "run", fake_run)
    outcomes.run_ci_loop(**kw)
    run_id, changed, success = state["execute_return"]
    assert run_id == "real-run"
    assert changed == ["a.py"]
    assert success is True


def test_run_ci_loop_execute_no_result_file(ci_modules, tmp_path, monkeypatch):
    state, RS = ci_modules
    state["invoke_execute"] = True
    state["ci_result"] = SimpleNamespace(final_status=RS.ESCALATED, total_attempts=1)
    kw = _ci_kwargs(tmp_path)

    monkeypatch.setattr(
        outcomes.subprocess,
        "run",
        lambda cmd, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    outcomes.run_ci_loop(**kw)
    run_id, changed, success = state["execute_return"]
    # Falls back to generated run_id, no changed files, not successful.
    assert run_id == "ci-task-123-attempt-1"
    assert changed == []
    assert success is False


def test_run_ci_loop_execute_malformed_result(ci_modules, tmp_path, monkeypatch):
    state, RS = ci_modules
    state["invoke_execute"] = True
    state["ci_result"] = SimpleNamespace(final_status=RS.ESCALATED, total_attempts=1)
    kw = _ci_kwargs(tmp_path)
    result_path = kw["tmp"] / "result-ci-1.json"

    def fake_run(cmd, **kwargs):
        result_path.write_text("not-json{", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(outcomes.subprocess, "run", fake_run)
    outcomes.run_ci_loop(**kw)
    run_id, changed, success = state["execute_return"]
    assert run_id == "ci-task-123-attempt-1"
    assert success is False


def test_run_ci_loop_repo_path_fallback(ci_modules, tmp_path):
    # repo_key not in settings.repos → repo_path = Path(repo_key); no validation cmds.
    state, RS = ci_modules
    state["ci_result"] = SimpleNamespace(final_status=RS.ESCALATED, total_attempts=1)
    kw = _ci_kwargs(tmp_path, settings=_make_settings({}), repo_key="ghost-repo")
    ok = outcomes.run_ci_loop(**kw)
    assert ok is False
