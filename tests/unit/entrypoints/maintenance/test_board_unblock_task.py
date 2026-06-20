# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for BoardUnblockTask + the PR-merged reconciliation.

These pin both halves of the fix for the unwired board-unblock engine:
  - reconcile_merged_pr_tasks turns a Blocked/In-Review task whose PR MERGED into
    a Done transition (the #267/#341 fixture), and does NOT touch open/no-PR tasks.
  - BoardUnblockTask satisfies the MaintenanceTask contract and applies actions
    via an injected PlaneClient, so registering it in the live loop makes the
    controller self-heal the board.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from operations_center.entrypoints.maintenance.board_unblock_task import (
    BoardUnblockTask,
    reconcile_merged_pr_tasks,
)
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceTask


def _issue(task_id, *, state, labels, updated_at="2026-06-19T04:00:00+00:00"):
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": label} for label in labels],
        "updated_at": updated_at,
    }


def _settings():
    repo = SimpleNamespace(clone_url="git@github.com:Velascat/OperationsCenter.git")
    return SimpleNamespace(
        repos={"OperationsCenter": repo},
        plane=SimpleNamespace(base_url="http://x", workspace_slug="w", project_id="p"),
        git=SimpleNamespace(token_env="GITHUB_TOKEN"),
        plane_token=lambda: "tok",
    )


_GOAL_LABELS = ["task-kind: goal", "repo: OperationsCenter"]


class TestReconcileMergedPrTasks:
    def test_blocked_task_with_merged_pr_reconciles_to_done(self):
        """The #267/#341 case: Blocked task, its goal/<id8> PR merged → Done."""
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = {
            "number": 341,
            "merged_at": "2026-06-19T06:27:17Z",
        }
        issue = _issue("0ccb698d-aaaa", state="Blocked", labels=_GOAL_LABELS)
        actions = reconcile_merged_pr_tasks([issue], settings=_settings(), gh_client=gh)
        assert len(actions) == 1
        assert actions[0]["to_state"] == "Done"
        assert actions[0]["rule"] == "PR_MERGED_RECONCILE"
        # branch probed is goal/<task_id[:8]>
        gh.find_pr_by_head.assert_any_call("Velascat", "OperationsCenter", "goal/0ccb698d")

    def test_in_review_task_with_merged_pr_reconciles(self):
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = {"number": 340, "merged_at": "2026-06-19T..."}
        issue = _issue("fc9d7e10-bbbb", state="In Review", labels=_GOAL_LABELS)
        actions = reconcile_merged_pr_tasks([issue], settings=_settings(), gh_client=gh)
        assert [a["to_state"] for a in actions] == ["Done"]

    def test_open_pr_is_not_reconciled(self):
        """A not-yet-merged PR must stay with the reviewer — no Done action."""
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = {"number": 999, "merged_at": None}
        issue = _issue("abc12345", state="In Review", labels=_GOAL_LABELS)
        actions = reconcile_merged_pr_tasks([issue], settings=_settings(), gh_client=gh)
        assert actions == []

    def test_no_pr_is_not_reconciled(self):
        """Phantom completion (#268: In Review, no PR) is left for STALE_IN_REVIEW."""
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = None
        issue = _issue("72fbc69c", state="In Review", labels=_GOAL_LABELS)
        actions = reconcile_merged_pr_tasks([issue], settings=_settings(), gh_client=gh)
        assert actions == []

    def test_terminal_and_other_states_skipped(self):
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = {"number": 1, "merged_at": "x"}
        issues = [
            _issue("d1", state="Done", labels=_GOAL_LABELS),
            _issue("r1", state="Running", labels=_GOAL_LABELS),
            _issue("b1", state="Backlog", labels=_GOAL_LABELS),
        ]
        actions = reconcile_merged_pr_tasks(issues, settings=_settings(), gh_client=gh)
        assert actions == []
        gh.find_pr_by_head.assert_not_called()

    def test_task_without_repo_label_skipped(self):
        gh = mock.Mock()
        issue = _issue("x1", state="Blocked", labels=["task-kind: goal"])
        actions = reconcile_merged_pr_tasks([issue], settings=_settings(), gh_client=gh)
        assert actions == []
        gh.find_pr_by_head.assert_not_called()

    def test_lookup_error_is_swallowed(self):
        gh = mock.Mock()
        gh.find_pr_by_head.side_effect = RuntimeError("github down")
        issue = _issue("e1", state="Blocked", labels=_GOAL_LABELS)
        actions = reconcile_merged_pr_tasks([issue], settings=_settings(), gh_client=gh)
        assert actions == []  # never raises


class TestBoardUnblockTask:
    def test_satisfies_maintenance_task_protocol(self):
        task = BoardUnblockTask(_settings())
        assert isinstance(task, MaintenanceTask)
        assert task.name == "board_unblock"
        assert task.interval_seconds > 0
        assert task.enabled is True

    def test_run_once_applies_merged_reconcile(self):
        """End-to-end: a Blocked-but-merged task is transitioned to Done."""
        plane = mock.Mock()
        plane.list_issues.return_value = [
            _issue("0ccb698d-aaaa", state="Blocked", labels=_GOAL_LABELS)
        ]
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = {"number": 341, "merged_at": "2026-06-19T06:27:17Z"}
        task = BoardUnblockTask(_settings(), plane_client=plane, gh_client=gh, apply=True)

        ctx = MaintenanceContext(cycle_id="c", now=datetime(2026, 6, 19, tzinfo=UTC))
        result = task.run_once(ctx)

        assert result.status == "ok"
        assert result.details["reconciled_merged"] == 1
        assert result.details["applied"] == 1
        plane.transition_issue.assert_called_once_with("0ccb698d-aaaa", "Done")
        plane.comment_issue.assert_called_once()
        plane.close.assert_not_called()  # injected client is not owned

    def test_run_once_dry_run_does_not_transition(self):
        plane = mock.Mock()
        plane.list_issues.return_value = [
            _issue("0ccb698d-aaaa", state="Blocked", labels=_GOAL_LABELS)
        ]
        gh = mock.Mock()
        gh.find_pr_by_head.return_value = {"number": 341, "merged_at": "x"}
        task = BoardUnblockTask(_settings(), plane_client=plane, gh_client=gh, apply=False)
        result = task.run_once(
            MaintenanceContext(cycle_id="c", now=datetime(2026, 6, 19, tzinfo=UTC))
        )
        assert result.status == "ok"
        plane.transition_issue.assert_not_called()

    def test_run_once_plane_failure_is_reported(self):
        plane = mock.Mock()
        plane.list_issues.side_effect = RuntimeError("plane down")
        task = BoardUnblockTask(_settings(), plane_client=plane, gh_client=mock.Mock())
        result = task.run_once(
            MaintenanceContext(cycle_id="c", now=datetime(2026, 6, 19, tzinfo=UTC))
        )
        assert result.status == "failed"
        assert "plane down" in (result.error or "")

    def test_run_once_closes_owned_client_on_exception_in_rules(self):
        """Verify resource cleanup: injected client is NOT closed (not owned by task)."""
        plane = mock.Mock()
        plane.list_issues.return_value = [
            _issue("0ccb698d-aaaa", state="Blocked", labels=_GOAL_LABELS)
        ]
        task = BoardUnblockTask(_settings(), plane_client=plane, gh_client=mock.Mock())
        # Force an exception during rule processing by patching _apply_rules
        with mock.patch(
            "operations_center.entrypoints.maintenance.board_unblock_task._apply_rules",
            side_effect=RuntimeError("rules engine failed"),
        ):
            with mock.patch.object(task, "_make_gh_client", return_value=None):
                try:
                    task.run_once(
                        MaintenanceContext(cycle_id="c", now=datetime(2026, 6, 19, tzinfo=UTC))
                    )
                except RuntimeError:
                    pass  # Expected: exception from _apply_rules
        # Since plane_client was injected, owns_client is False, so close() should NOT be called
        plane.close.assert_not_called()

    def test_run_once_closes_created_client_on_exception_in_rules(self):
        """Verify resource cleanup: created client IS closed even if _apply_rules raises."""
        settings = _settings()
        # Create task without injected plane/gh clients so it will create/own them
        task = BoardUnblockTask(settings, apply=True)
        mock_client_instance = mock.Mock()
        mock_client_instance.list_issues.return_value = [
            _issue("0ccb698d-aaaa", state="Blocked", labels=_GOAL_LABELS)
        ]
        with (
            mock.patch(
                "operations_center.entrypoints.maintenance.board_unblock_task.PlaneClient",
                return_value=mock_client_instance,
            ),
            mock.patch(
                "operations_center.entrypoints.maintenance.board_unblock_task._apply_rules",
                side_effect=RuntimeError("rules engine failed"),
            ),
            mock.patch.object(task, "_make_gh_client", return_value=None),
        ):
            with mock.patch.dict("os.environ", {"GITHUB_TOKEN": ""}):  # Ensure no GitHub token
                try:
                    task.run_once(
                        MaintenanceContext(cycle_id="c", now=datetime(2026, 6, 19, tzinfo=UTC))
                    )
                except RuntimeError:
                    pass  # Expected: exception from _apply_rules
        # Verify the client was closed despite the exception
        mock_client_instance.close.assert_called_once()


class TestLiveLoopRegistration:
    """The whole point of #268: board_unblock must be wired into the running
    loop, not just exist as a CLI. This pins it so it can't silently un-register."""

    def test_board_unblock_registered_in_live_loop(self):
        from operations_center.entrypoints.spec_hygiene import main as sh

        registry = mock.Mock()
        registered: list[str] = []
        registry.register.side_effect = lambda t: registered.append(t.name)

        with (
            mock.patch.object(
                sh, "SpecHygieneTask", lambda *a, **k: SimpleNamespace(name="spec_hygiene")
            ),
            mock.patch.object(
                sh, "LedgerMaintainTask", lambda *a, **k: SimpleNamespace(name="ledger_maintain")
            ),
            mock.patch.object(
                sh, "BoardUnblockTask", lambda *a, **k: SimpleNamespace(name="board_unblock")
            ),
        ):
            sh.register_maintenance_tasks(registry, _settings(), mock.Mock())

        assert "board_unblock" in registered
        assert {"spec_hygiene", "ledger_maintain", "board_unblock"} <= set(registered)
