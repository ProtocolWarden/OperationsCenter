# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime

from operations_center.entrypoints.maintenance.board_unblock import _apply_rules
from operations_center.entrypoints.maintenance.board_unblock_support import (
    COMMENT_HISTORY_KEY,
    LATEST_COMMENT_KEY,
    SUCCESS_HANDOFF_KEY,
    attach_latest_retry_signal_comment,
)


def _issue(
    task_id: str,
    *,
    state: str,
    labels: list[str],
    updated_at: str = "2026-05-27T15:00:00+00:00",
    latest_comment: str | None = None,
    latest_comment_ts: str | None = None,
) -> dict:
    issue = {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": label} for label in labels],
        "updated_at": updated_at,
    }
    if latest_comment is not None:
        issue["_latest_comment_text"] = latest_comment
    if latest_comment_ts is not None:
        issue["_latest_comment_ts"] = latest_comment_ts
    return issue


def test_self_modify_requeue_skips_when_latest_failure_is_not_retry_safe():
    actions = _apply_rules(
        [
            _issue(
                "task-1",
                state="Blocked",
                labels=["task-kind: improve", "self-modify: approved"],
                latest_comment=(
                    "board_worker[improve] failed\n"
                    "status: failed\n"
                    "category: backend_error\n"
                    "reason: Stage planner received non-JSON from agent (session limit or error)"
                ),
            )
        ],
        now=datetime(2026, 5, 27, 16, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert actions == [
        {
            "task_id": "task-1",
            "title": "Task task-1",
            "rule": "SELF_MODIFY_REQUEUE",
            "from_state": "Blocked",
            "to_state": "Ready for AI",
            "reason": (
                "SKIPPED — latest board_worker failure comment shows retry is not safe "
                "yet (session limit)"
            ),
            "skipped": True,
        }
    ]


def test_clean_blocked_retry_skips_budget_exhausted_failures():
    actions = _apply_rules(
        [
            _issue(
                "task-2",
                state="Blocked",
                labels=["task-kind: goal"],
                latest_comment=(
                    "board_worker[goal] failed\n"
                    "status: skipped\n"
                    "category: budget_exhausted\n"
                    "reason: dispatch skipped — global resource gate global_rate_exceeded"
                ),
            )
        ],
        now=datetime(2026, 5, 27, 16, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert actions == [
        {
            "task_id": "task-2",
            "title": "Task task-2",
            "rule": "CLEAN_BLOCKED_RETRY",
            "from_state": "Blocked",
            "to_state": "Backlog",
            "reason": (
                "SKIPPED — latest board_worker failure comment shows retry is not safe "
                "yet (category: budget_exhausted)"
            ),
            "skipped": True,
        }
    ]


def test_clean_blocked_retry_keeps_pre_execution_retry_path():
    actions = _apply_rules(
        [
            _issue(
                "task-3",
                state="Blocked",
                labels=["task-kind: goal"],
                latest_comment="board_worker[goal] failed\nreason: workspace preparation failed",
            )
        ],
        now=datetime(2026, 5, 27, 16, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert actions == [
        {
            "task_id": "task-3",
            "title": "Task task-3",
            "rule": "CLEAN_BLOCKED_RETRY",
            "from_state": "Blocked",
            "to_state": "Backlog",
            "reason": (
                "no executor-signal/exit-code/blocked-by labels — pre-execution failure "
                "(workspace prep or infra config); safe to retry after 5m min age"
            ),
        }
    ]


def test_goal_backlog_promote_skips_thin_goal_from_history():
    task = _issue(
        "task-4",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: autonomy",
            "source: improve-suggestion",
            "original-task-id: parent-1",
        ],
    )
    task[COMMENT_HISTORY_KEY] = "board_worker[goal] refused to claim — goal text too thin"
    actions = _apply_rules(
        [
            _issue(
                "parent-1",
                state="Done",
                labels=["task-kind: improve"],
            ),
            task,
        ],
        now=datetime(2026, 5, 27, 16, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert actions == [
        {
            "task_id": "task-4",
            "title": "Task task-4",
            "rule": "GOAL_BACKLOG_PROMOTE",
            "from_state": "Backlog",
            "to_state": "Ready for AI",
            "reason": (
                "SKIPPED — historical board_worker/review evidence shows "
                "re-promotion is not safe yet (goal text too thin)"
            ),
            "skipped": True,
        }
    ]


def test_goal_backlog_promote_skips_prior_success_handoff():
    task = _issue(
        "task-5",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: autonomy",
            "source: improve-suggestion",
            "original-task-id: parent-2",
        ],
    )
    task[SUCCESS_HANDOFF_KEY] = True
    actions = _apply_rules(
        [
            _issue(
                "parent-2",
                state="Done",
                labels=["task-kind: improve"],
            ),
            task,
        ],
        now=datetime(2026, 5, 27, 16, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert actions == [
        {
            "task_id": "task-5",
            "title": "Task task-5",
            "rule": "GOAL_BACKLOG_PROMOTE",
            "from_state": "Backlog",
            "to_state": "Ready for AI",
            "reason": (
                "SKIPPED — task already completed implementation and previously "
                "moved to In Review; do not re-execute without new review evidence"
            ),
            "skipped": True,
        }
    ]


_BUDGET_EXHAUSTED_COMMENT = (
    "board_worker[goal] failed\n"
    "status: skipped\n"
    "category: budget_exhausted\n"
    "reason: dispatch skipped — global resource gate global_rate_exceeded"
)


def test_clean_blocked_retry_unblocks_stale_budget_exhausted_after_reset():
    """A budget_exhausted comment from before the 04:00 UTC reset should not block retry."""
    actions = _apply_rules(
        [
            _issue(
                "task-6",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T04:01:00+00:00",  # recently blocked (within 4h)
                latest_comment=_BUDGET_EXHAUSTED_COMMENT,
                latest_comment_ts="2026-05-27T23:36:00+00:00",  # before 04:00 UTC on 2026-05-28
            )
        ],
        now=datetime(2026, 5, 28, 4, 11, tzinfo=UTC),  # after reset
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert "skipped" not in actions[0]
    assert actions[0]["to_state"] == "Backlog"


def test_clean_blocked_retry_holds_fresh_budget_exhausted_after_reset():
    """A budget_exhausted comment from AFTER the 04:00 UTC reset should still block retry."""
    actions = _apply_rules(
        [
            _issue(
                "task-7",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T04:06:00+00:00",  # recently blocked (within 4h)
                latest_comment=_BUDGET_EXHAUSTED_COMMENT,
                latest_comment_ts="2026-05-28T04:05:00+00:00",  # after 04:00 UTC reset
            )
        ],
        now=datetime(2026, 5, 28, 4, 11, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert actions[0].get("skipped") is True


def test_clean_blocked_retry_holds_budget_exhausted_without_ts():
    """When no comment timestamp is available, do not treat budget_exhausted as stale."""
    actions = _apply_rules(
        [
            _issue(
                "task-8",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T04:06:00+00:00",  # recently blocked (within 4h)
                latest_comment=_BUDGET_EXHAUSTED_COMMENT,
                # no latest_comment_ts
            )
        ],
        now=datetime(2026, 5, 28, 4, 11, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert actions[0].get("skipped") is True


_CONCURRENCY_BLOCKED_COMMENT = (
    "board_worker[goal] failed\n"
    "status: skipped\n"
    "category: budget_exhausted\n"
    "reason: dispatch skipped — global resource gate global_concurrency_exceeded; window=in_flight; current=1 limit=1"
)


def test_clean_blocked_retry_holds_concurrency_blocked_when_daily_gate_active():
    """global_concurrency_exceeded comment should block retry when daily gate is active."""
    actions = _apply_rules(
        [
            _issue(
                "task-9",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T05:30:00+00:00",  # 14 min ago — within stale window
                latest_comment=_CONCURRENCY_BLOCKED_COMMENT,
            )
        ],
        now=datetime(2026, 5, 28, 5, 44, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=True,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert actions[0].get("skipped") is True


def test_clean_blocked_retry_unblocks_concurrency_blocked_when_daily_gate_clear():
    """global_concurrency_exceeded comment should allow retry when daily gate is clear."""
    actions = _apply_rules(
        [
            _issue(
                "task-10",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T14:50:00+00:00",  # 10 min ago — within stale window
                latest_comment=_CONCURRENCY_BLOCKED_COMMENT,
            )
        ],
        now=datetime(2026, 5, 28, 15, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=False,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert "skipped" not in actions[0]
    assert actions[0]["to_state"] == "Backlog"


def test_clean_blocked_retry_holds_rate_exceeded_when_daily_gate_active():
    """global_rate_exceeded comment should block retry when daily gate is still active."""
    actions = _apply_rules(
        [
            _issue(
                "task-11",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T05:30:00+00:00",  # 14 min ago — within stale window
                latest_comment=_BUDGET_EXHAUSTED_COMMENT,
                latest_comment_ts="2026-05-28T01:50:00+00:00",  # pre-04:00 UTC but gate still active
            )
        ],
        now=datetime(2026, 5, 28, 5, 44, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=True,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert actions[0].get("skipped") is True


def test_clean_blocked_retry_unblocks_rate_exceeded_when_daily_gate_clear():
    """global_rate_exceeded comment should allow retry when daily gate is clear."""
    actions = _apply_rules(
        [
            _issue(
                "task-12",
                state="Blocked",
                labels=["task-kind: goal"],
                updated_at="2026-05-28T14:50:00+00:00",  # 10 min ago — within stale window
                latest_comment=_BUDGET_EXHAUSTED_COMMENT,
                latest_comment_ts="2026-05-28T01:50:00+00:00",
            )
        ],
        now=datetime(2026, 5, 28, 15, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=False,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "CLEAN_BLOCKED_RETRY"
    assert "skipped" not in actions[0]
    assert actions[0]["to_state"] == "Backlog"


# --- attach_latest_retry_signal_comment ordering ---


class _MockClient:
    """Minimal Plane client stub for comment-ordering tests."""

    def __init__(self, comments_by_id: dict) -> None:
        self._comments = comments_by_id

    def list_comments(self, issue_id: str) -> list:
        return self._comments.get(issue_id, [])


def _comment(body: str, ts: str) -> dict:
    return {"comment": body, "created_at": ts}


def test_attach_latest_comment_uses_newest_when_plane_returns_newest_first():
    """Plane returns comments newest-first; attach_latest_retry_signal_comment must
    select the most-recent board_worker comment, not the oldest one.

    Regression: previously reversed(comments) iterated oldest-first and stopped at
    a stale SwitchBoard-unreachable comment, masking a fresh budget_exhausted signal.
    """
    fresh_budget = _comment(
        "board_worker[improve] failed\nstatus: skipped\ncategory: budget_exhausted\n"
        "reason: dispatch skipped — global resource gate global_rate_exceeded",
        "2026-05-28T06:55:42Z",
    )
    stale_switchboard = _comment(
        "board_worker[improve] blocked — planning failed: SwitchBoard unreachable",
        "2026-05-28T02:46:59Z",
    )
    # Plane returns newest-first: fresh_budget at index 0, stale at index 1
    comments = [fresh_budget, stale_switchboard]

    issue = {
        "id": "task-x",
        "name": "Test",
        "state": {"name": "Blocked"},
        "labels": [],
    }
    client = _MockClient({"task-x": comments})
    attach_latest_retry_signal_comment(client, [issue])

    assert LATEST_COMMENT_KEY in issue
    assert "budget_exhausted" in issue[LATEST_COMMENT_KEY]
    assert "SwitchBoard" not in issue[LATEST_COMMENT_KEY]


def test_self_modify_requeue_skips_when_fresh_budget_exhausted_precedes_old_switchboard():
    """SELF_MODIFY_REQUEUE must not fire when the newest comment is budget_exhausted,
    even if an older comment contains a non-blocker pattern (SwitchBoard unreachable).

    This is the live regression from cycle 60: reversed(comments) selected the old
    SwitchBoard comment, retry_blocker_reason returned None, and the task was churned
    back to Ready-for-AI on every board-unblock cycle while the gate was active.
    """
    actions = _apply_rules(
        [
            _issue(
                "task-churn",
                state="Blocked",
                labels=["task-kind: improve", "self-modify: approved"],
                # Pre-populated as if attach_latest_retry_signal_comment ran correctly:
                latest_comment=(
                    "board_worker[improve] failed\nstatus: skipped\n"
                    "category: budget_exhausted\n"
                    "reason: dispatch skipped — global resource gate global_rate_exceeded"
                ),
                latest_comment_ts="2026-05-28T06:55:42+00:00",
            )
        ],
        now=datetime(2026, 5, 28, 7, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=True,
    )

    assert len(actions) == 1
    assert actions[0]["rule"] == "SELF_MODIFY_REQUEUE"
    assert actions[0].get("skipped") is True
    assert "budget_exhausted" in actions[0]["reason"]


def test_goal_backlog_promote_improvement_applied_promotes_when_parent_done():
    """Rule 7 must promote improvement_applied follow-on goal tasks when their
    parent task is Done — Pattern B (source: board_worker + handoff-reason: improvement_applied).
    """
    parent = _issue("parent-1", state="Done", labels=["task-kind: goal"])
    child = _issue(
        "child-1",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: board_worker",
            "handoff-reason: improvement_applied",
            "original-task-id: parent-1",
        ],
    )
    actions = _apply_rules(
        [parent, child],
        now=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=False,
    )
    promote = [a for a in actions if a.get("task_id") == "child-1"]
    assert len(promote) == 1
    assert promote[0]["rule"] == "GOAL_BACKLOG_PROMOTE"
    assert not promote[0].get("skipped")
    assert promote[0]["to_state"] == "Ready for AI"


def test_goal_backlog_promote_improvement_applied_skips_when_gate_active():
    """Rule 7 Pattern B must not promote when the daily gate is active."""
    parent = _issue("parent-2", state="Done", labels=["task-kind: goal"])
    child = _issue(
        "child-2",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: board_worker",
            "handoff-reason: improvement_applied",
            "original-task-id: parent-2",
        ],
    )
    actions = _apply_rules(
        [parent, child],
        now=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=True,
    )
    promote = [a for a in actions if a.get("task_id") == "child-2"]
    assert len(promote) == 1
    assert promote[0].get("skipped") is True
    assert "gate" in promote[0]["reason"].lower()


# ── Rule 9 — SPEC_AUTHOR_BACKLOG_PROMOTE ─────────────────────────────────────

def test_spec_author_backlog_promote_when_gate_cleared():
    issue = _issue(
        "spec-1",
        state="Backlog",
        labels=["task-kind: spec-author", "source: spec-director"],
        latest_comment="board_worker[spec-author] failed\ncategory: budget_exhausted\nreason: dispatch skipped",
        latest_comment_ts="2026-05-28T07:39:00+00:00",
    )
    actions = _apply_rules(
        [issue],
        now=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=False,  # gate has cleared
    )
    r9 = [a for a in actions if a.get("rule") == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(r9) == 1
    assert r9[0].get("skipped") is not True
    assert r9[0]["to_state"] == "Ready for AI"


def test_spec_author_backlog_promote_skipped_when_gate_active():
    issue = _issue(
        "spec-2",
        state="Backlog",
        labels=["task-kind: spec-author", "source: spec-director"],
        latest_comment="board_worker[spec-author] failed\ncategory: budget_exhausted\nreason: dispatch skipped",
        latest_comment_ts="2026-05-28T07:39:00+00:00",
    )
    actions = _apply_rules(
        [issue],
        now=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
        daily_gate_active=True,  # gate still active
    )
    r9 = [a for a in actions if a.get("rule") == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(r9) == 1
    assert r9[0].get("skipped") is True
    assert "budget_exhausted" in r9[0]["reason"]


def test_spec_author_backlog_promote_skipped_low_memory():
    issue = _issue(
        "spec-3",
        state="Backlog",
        labels=["task-kind: spec-author", "source: spec-director"],
    )
    actions = _apply_rules(
        [issue],
        now=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=4.0,  # below 8GB threshold
        daily_gate_active=False,
    )
    r9 = [a for a in actions if a.get("rule") == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(r9) == 0  # rule does not fire below memory threshold
