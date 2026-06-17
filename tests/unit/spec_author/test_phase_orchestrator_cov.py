# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from operations_center.spec_author.models import ActiveCampaigns, CampaignRecord
from operations_center.spec_author.phase_orchestrator import (
    PendingPhaseAdvance,
    PhaseOrchestrationResult,
    PhaseOrchestrator,
    _campaign_id_from_issue,
    _labels,
    _status,
    _summarize_phase_tasks,
    _task_kind,
)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def test_status_from_dict_state():
    assert _status({"state": {"name": "Done"}}) == "done"


def test_status_from_string_state():
    assert _status({"state": "Backlog"}) == "backlog"


def test_status_missing_state_is_empty():
    assert _status({}) == ""


def test_status_none_state_is_empty():
    assert _status({"state": None}) == ""


def test_status_dict_without_name():
    assert _status({"state": {}}) == ""


def test_labels_with_dicts_and_strings():
    issue = {
        "labels": [
            {"name": "campaign-id:abc"},
            "task-kind:test_campaign",
            {"name": ""},  # falsy name skipped
            {"nope": "x"},  # no name key skipped
            "",  # falsy string skipped
        ]
    }
    assert _labels(issue) == ["campaign-id:abc", "task-kind:test_campaign"]


def test_labels_non_list_returns_empty():
    assert _labels({"labels": "notalist"}) == []


def test_labels_missing_returns_empty():
    assert _labels({}) == []


def test_campaign_id_from_issue_found():
    issue = {"labels": ["campaign-id:  cmp-1  "]}
    assert _campaign_id_from_issue(issue) == "cmp-1"


def test_campaign_id_from_issue_case_insensitive():
    issue = {"labels": ["Campaign-ID:cmp-2"]}
    assert _campaign_id_from_issue(issue) == "cmp-2"


def test_campaign_id_from_issue_none():
    assert _campaign_id_from_issue({"labels": ["other"]}) is None


def test_task_kind_found():
    issue = {"labels": ["task-kind: Test_Campaign "]}
    assert _task_kind(issue) == "test_campaign"


def test_task_kind_defaults_to_goal():
    assert _task_kind({"labels": ["random"]}) == "goal"


def test_summarize_phase_tasks_orders_and_filters():
    by_phase = {
        "goal": [{"state": "Done", "name": "  G1  "}],
        "test_campaign": [{"state": {"name": "Backlog"}, "name": "T1"}],
        "improve_campaign": [{"name": "I1"}],
        "parent": [{"name": "P1"}],  # ignored
    }
    out = _summarize_phase_tasks(by_phase)
    assert out == [
        ("goal", "done", "G1"),
        ("test_campaign", "backlog", "T1"),
        ("improve_campaign", "", "I1"),
    ]


def test_summarize_phase_tasks_missing_buckets():
    assert _summarize_phase_tasks({}) == []


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_pending_phase_advance_defaults():
    p = PendingPhaseAdvance(
        campaign_id="c",
        spec_slug="s",
        spec_file_path="/p",
        current_phase="goal",
        next_phase="test",
    )
    assert p.task_summaries == []


def test_result_defaults():
    r = PhaseOrchestrationResult()
    assert r.phases_advanced == 0
    assert r.campaigns_completed == 0
    assert r.pending_advances == []
    assert r.errors == []
    assert r.tasks_unblocked == 0
    assert r.tasks_cancelled == 0


# ---------------------------------------------------------------------------
# Fixtures / helpers for orchestrator tests
# ---------------------------------------------------------------------------


def _make_campaign(campaign_id="cmp-1", slug="myslug", spec_file="", status="active"):
    return CampaignRecord(
        campaign_id=campaign_id,
        slug=slug,
        spec_file=spec_file,
        status=status,
        created_at="2026-01-01",
    )


def _make_state_manager(campaigns):
    sm = MagicMock()
    sm.load.return_value = ActiveCampaigns(campaigns=campaigns)
    return sm


def _issue(id_, name, status, *, campaign="cmp-1", kind=None):
    labels = [f"campaign-id:{campaign}"] if campaign else []
    if kind:
        labels.append(f"task-kind:{kind}")
    return {"id": id_, "name": name, "state": {"name": status}, "labels": labels}


# ---------------------------------------------------------------------------
# run() / detect_pending_advances
# ---------------------------------------------------------------------------


def test_run_no_active_campaigns(tmp_path):
    sm = _make_state_manager([])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    result = orch.run([])
    assert result.phases_advanced == 0
    assert result.pending_advances == []
    client.transition_issue.assert_not_called()


def test_detect_pending_advances_delegates(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    # all goal tasks terminal -> a pending advance is emitted
    issues = [_issue("1", "goal task", "Done", kind="goal")]
    advances = orch.detect_pending_advances(issues)
    assert len(advances) == 1
    assert advances[0].next_phase == "test"


def test_run_catches_orchestrate_exception(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    # Make _orchestrate blow up
    orch._orchestrate = MagicMock(side_effect=RuntimeError("boom"))
    result = orch.run([])
    assert len(result.errors) == 1
    assert "cmp-1" in result.errors[0]
    assert "boom" in result.errors[0]


# ---------------------------------------------------------------------------
# Phase advancement
# ---------------------------------------------------------------------------


def test_phase_advance_goal_terminal_promotes_backlog_test(tmp_path):
    sm = _make_state_manager([_make_campaign(spec_file="/custom/spec.md")])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [
        _issue("g1", "goal", "Done", kind="goal"),
        _issue("t1", "test", "Backlog", kind="test_campaign"),
        _issue("t2", "test in progress", "In Progress", kind="test_campaign"),
        # parent (campaign) issue
        {
            "id": "p1",
            "name": "[Campaign] my campaign",
            "state": {"name": "In Progress"},
            "labels": ["campaign-id:cmp-1"],
        },
    ]
    result = orch.run(issues)
    # only the backlog test task promoted
    client.transition_issue.assert_called_once_with("t1", "Ready for AI")
    assert result.phases_advanced == 1
    # parent commented because backlog_next non-empty
    client.comment_issue.assert_any_call("p1", "Advancing to test phase: 1 tasks promoted.")
    # pending advance emitted with custom spec file
    assert len(result.pending_advances) == 1
    pa = result.pending_advances[0]
    assert pa.current_phase == "goal"
    assert pa.next_phase == "test"
    assert pa.spec_file_path == "/custom/spec.md"


def test_phase_advance_no_backlog_no_promotion_but_pending_emitted(tmp_path):
    sm = _make_state_manager([_make_campaign(spec_file="")])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [
        _issue("g1", "goal", "Cancelled", kind="goal"),
        _issue("t1", "test running", "In Progress", kind="test_campaign"),
    ]
    result = orch.run(issues)
    # no backlog test tasks -> no transition, no comment
    client.transition_issue.assert_not_called()
    client.comment_issue.assert_not_called()
    assert result.phases_advanced == 0
    # but a pending advance is still emitted; spec_file empty -> derived path
    assert len(result.pending_advances) == 1
    pa = result.pending_advances[0]
    assert pa.spec_file_path == str(tmp_path / "myslug.md")


def test_no_advance_when_current_phase_not_terminal(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [
        _issue("g1", "goal", "In Progress", kind="goal"),
        _issue("t1", "test", "Backlog", kind="test_campaign"),
    ]
    result = orch.run(issues)
    client.transition_issue.assert_not_called()
    assert result.pending_advances == []
    assert result.phases_advanced == 0


def test_no_advance_when_current_phase_empty(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    # no goal tasks at all -> chain skips goal->test
    issues = [_issue("t1", "test", "Backlog", kind="test_campaign")]
    result = orch.run(issues)
    assert result.pending_advances == []
    client.transition_issue.assert_not_called()


def test_test_to_improve_advance(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [
        _issue("t1", "test", "Done", kind="test_campaign"),
        _issue("i1", "improve", "Backlog", kind="improve_campaign"),
    ]
    result = orch.run(issues)
    client.transition_issue.assert_called_once_with("i1", "Ready for AI")
    assert result.phases_advanced == 1
    pa = result.pending_advances[0]
    assert pa.current_phase == "test_campaign"
    assert pa.next_phase == "improve"


def test_issue_for_other_campaign_ignored(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [
        _issue("g1", "goal", "Done", kind="goal"),
        _issue("x1", "other", "Backlog", campaign="other-cmp", kind="test_campaign"),
    ]
    result = orch.run(issues)
    # the other-campaign test task is not in this campaign's buckets
    client.transition_issue.assert_not_called()
    # goal terminal still emits pending advance
    assert len(result.pending_advances) == 1


def test_unknown_kind_buckets_to_goal(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [_issue("g1", "weird", "Done", kind="totally_unknown")]
    result = orch.run(issues)
    # bucketed into goal; goal terminal -> pending advance for goal->test
    assert len(result.pending_advances) == 1
    assert result.pending_advances[0].current_phase == "goal"


# ---------------------------------------------------------------------------
# Campaign completion
# ---------------------------------------------------------------------------


def test_campaign_completion_archives_and_marks_complete(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    parent = {
        "id": "p1",
        "name": "[Campaign] done campaign",
        "state": {"name": "In Progress"},
        "labels": ["campaign-id:cmp-1", {"name": "priority:high"}],
    }
    issues = [
        _issue("g1", "g", "Done", kind="goal"),
        _issue("t1", "t", "Cancelled", kind="test_campaign"),
        _issue("i1", "i", "Done", kind="improve_campaign"),
        parent,
    ]
    result = orch.run(issues)
    assert result.campaigns_completed == 1
    client.transition_issue.assert_any_call("p1", "Done")
    client.comment_issue.assert_any_call("p1", "Campaign complete. 2 tasks done, 1 cancelled.")
    client.update_issue_labels.assert_called_once_with(
        "p1", ["campaign-id:cmp-1", "priority:high", "lifecycle: archived"]
    )
    sm.mark_complete.assert_called_once_with("cmp-1")


def test_campaign_completion_skips_archive_if_already_archived(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    parent = {
        "id": "p1",
        "name": "[Campaign] x",
        "state": {"name": "In Progress"},
        "labels": ["campaign-id:cmp-1", "lifecycle: archived"],
    }
    issues = [_issue("g1", "g", "Done", kind="goal"), parent]
    orch.run(issues)
    client.update_issue_labels.assert_not_called()


def test_campaign_completion_label_update_exception_logged(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    client.update_issue_labels.side_effect = RuntimeError("label fail")
    orch = PhaseOrchestrator(client, sm, tmp_path)
    parent = {
        "id": "p1",
        "name": "[Campaign] x",
        "state": {"name": "In Progress"},
        "labels": ["campaign-id:cmp-1"],
    }
    issues = [_issue("g1", "g", "Done", kind="goal"), parent]
    result = orch.run(issues)
    # exception in archive is swallowed; completion still proceeds
    assert result.campaigns_completed == 1
    sm.mark_complete.assert_called_once_with("cmp-1")


def test_no_completion_when_tasks_not_all_terminal(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    issues = [
        _issue("g1", "g", "Done", kind="goal"),
        _issue("i1", "i", "In Progress", kind="improve_campaign"),
    ]
    result = orch.run(issues)
    assert result.campaigns_completed == 0
    sm.mark_complete.assert_not_called()


def test_no_completion_when_no_tasks(tmp_path):
    sm = _make_state_manager([_make_campaign()])
    client = MagicMock()
    orch = PhaseOrchestrator(client, sm, tmp_path)
    # only a parent, no child tasks
    parent = {
        "id": "p1",
        "name": "[Campaign] empty",
        "state": {"name": "In Progress"},
        "labels": ["campaign-id:cmp-1"],
    }
    result = orch.run([parent])
    assert result.campaigns_completed == 0
    client.transition_issue.assert_not_called()


# ---------------------------------------------------------------------------
# _all_terminal / _comment_parent
# ---------------------------------------------------------------------------


def test_all_terminal_true(tmp_path):
    orch = PhaseOrchestrator(MagicMock(), _make_state_manager([]), tmp_path)
    assert orch._all_terminal([{"state": "Done"}, {"state": "Cancelled"}]) is True


def test_all_terminal_false_with_nonterminal(tmp_path):
    orch = PhaseOrchestrator(MagicMock(), _make_state_manager([]), tmp_path)
    assert orch._all_terminal([{"state": "Done"}, {"state": "Backlog"}]) is False


def test_all_terminal_empty_false(tmp_path):
    orch = PhaseOrchestrator(MagicMock(), _make_state_manager([]), tmp_path)
    assert orch._all_terminal([]) is False


def test_comment_parent_handles_exception(tmp_path):
    client = MagicMock()
    client.comment_issue.side_effect = RuntimeError("nope")
    orch = PhaseOrchestrator(client, _make_state_manager([]), tmp_path)
    # should not raise
    orch._comment_parent([{"id": "p1"}], "hello")
    client.comment_issue.assert_called_once_with("p1", "hello")


def test_comment_parent_success(tmp_path):
    client = MagicMock()
    orch = PhaseOrchestrator(client, _make_state_manager([]), tmp_path)
    orch._comment_parent([{"id": "p1"}, {"id": "p2"}], "msg")
    assert client.comment_issue.call_count == 2


def test_init_accepts_max_rewrite_attempts(tmp_path):
    # back-compat kwarg retained; should be accepted without error
    orch = PhaseOrchestrator(MagicMock(), _make_state_manager([]), tmp_path, max_rewrite_attempts=5)
    assert isinstance(orch._specs_dir, Path)
