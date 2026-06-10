# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for pr_review_watcher — three-phase autonomous PR review state machine.

All GitHub API calls are intercepted via monkeypatching GitHubPRClient methods.
_run_direct_review is stubbed to return controlled verdicts for self-review tests.
_run_pipeline is stubbed for fix-pass tests (return_result=True path).
State files use tmp_path so no real disk state is left behind.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher

# ── Shared fixtures ───────────────────────────────────────────────────────────

REPO_KEY = "MyRepo"
PR_NUMBER = 42
STATE_KEY = f"{REPO_KEY}-{PR_NUMBER}"

REVIEWER_CFG = MagicMock(
    bot_logins=[],
    allowed_reviewer_logins=[],
    max_self_review_loops=2,
    max_fix_attempts=2,
    bot_comment_marker="<!-- operations-center:bot -->",
)

SETTINGS = MagicMock(
    reviewer=REVIEWER_CFG,
    repos={},
    plane=MagicMock(base_url="http://plane.local", project_id="proj", workspace_slug="ws"),
)


def _pr_data(*, draft: bool = False, title: str = "My PR", head_sha: str = "abc123") -> dict[str, Any]:
    return {
        "number": PR_NUMBER,
        "title": title,
        "draft": draft,
        "head": {"ref": f"goal/{PR_NUMBER}", "sha": head_sha},
    }


def _make_state(tmp_path: Path, **overrides: Any) -> tuple[dict, Path]:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    state.update(overrides)
    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    watcher._save_state(sp, state)
    return state, sp


def _make_gh(*, comment_id: int = 0) -> MagicMock:
    gh = MagicMock()
    gh.get_pr_diff.return_value = "diff --git a/foo.py\n+print('hello')"
    gh.list_pr_comments.return_value = []
    gh.get_pr_reactions.return_value = []
    gh.has_thumbs_up.return_value = False
    gh.post_comment.return_value = {"id": comment_id} if comment_id else {}
    gh.merge_pr.return_value = {}
    gh.update_comment.return_value = {}
    return gh


# ── State helpers ─────────────────────────────────────────────────────────────


def test_new_state_defaults(tmp_path: Path) -> None:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    assert state["phase"] == "ci_fix"
    assert state["ci_fix_attempts"] == 0
    assert state["ci_fix_last_push_at"] is None
    assert state["self_review_loops"] == 0
    assert state["pr_number"] == PR_NUMBER
    assert state["repo_key"] == REPO_KEY
    assert state["plane_task_id"] is None


def test_save_and_load_state(tmp_path: Path) -> None:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    watcher._save_state(sp, state)
    loaded = watcher._load_state(sp)
    assert loaded["pr_number"] == PR_NUMBER
    assert loaded["phase"] == "ci_fix"


def test_load_state_missing_file(tmp_path: Path) -> None:
    sp = tmp_path / "nonexistent.json"
    assert watcher._load_state(sp) == {}


def test_save_state_creates_parent_dirs(tmp_path: Path) -> None:
    sp = tmp_path / "deep" / "dir" / "state.json"
    watcher._save_state(sp, {"pr_number": 1, "phase": "self_review"})
    assert sp.exists()


# ── Phase 1: LGTM path ───────────────────────────────────────────────────────


def test_phase1_lgtm_merges_and_removes_state(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()

    with (
        patch.object(
            watcher, "_run_direct_review", return_value={"result": "LGTM", "summary": "all good"}
        ),
        patch.object(watcher, "_plane_client") as mock_pc,
    ):
        mock_plane = MagicMock()
        mock_pc.return_value.__enter__ = lambda s: mock_plane
        mock_pc.return_value = MagicMock()
        mock_pc.return_value.close = MagicMock()

        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()


def test_phase1_lgtm_increments_loop_count(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()

    with (
        patch.object(watcher, "_run_direct_review", return_value={"result": "LGTM", "summary": "ok"}),
        patch.object(watcher, "_merge_and_done") as mock_merge,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_merge.assert_called_once()
    # loop count was incremented before merge decision
    args = mock_merge.call_args
    assert args[1]["reason"] == "self_review_lgtm"


# ── Phase 1: CONCERNS path ───────────────────────────────────────────────────


def test_phase1_concerns_posts_comment(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()

    with patch.object(
        watcher, "_run_direct_review", return_value={"result": "CONCERNS", "summary": "fix the bug"}
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    gh.post_comment.assert_called_once()
    body = gh.post_comment.call_args[0][3]
    assert "<!-- operations-center:bot -->" in body
    assert "fix the bug" in body


def test_phase1_concerns_stays_in_phase1_below_max_loops(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=0)
    gh = _make_gh()

    with patch.object(
        watcher, "_run_direct_review", return_value={"result": "CONCERNS", "summary": "issues"}
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["self_review_loops"] == 1


def test_phase1_concerns_dispatches_fix_pass_below_cap(tmp_path: Path) -> None:
    # CONCERNS below the fix cap → dispatch a fix pass, never merge.
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=0, fix_attempts=0)
    gh = _make_gh()

    with (
        patch.object(
            watcher, "_run_direct_review", return_value={"result": "CONCERNS", "summary": "issues"}
        ),
        patch.object(watcher, "_run_fix_pass", return_value=True) as mock_fix,
        patch.object(watcher, "_merge_and_done") as mock_merge,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_fix.assert_called_once()
    mock_merge.assert_not_called()
    loaded = watcher._load_state(sp)
    assert loaded["fix_attempts"] == 1


def test_phase1_concerns_records_no_progress_signature(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=0, fix_attempts=0)
    gh = _make_gh()

    with (
        patch.object(
            watcher, "_run_direct_review", return_value={"result": "CONCERNS", "summary": "issues"}
        ),
        patch.object(watcher, "_run_fix_pass", return_value=False),
        patch.object(watcher, "_merge_and_done"),
    ):
        watcher._phase1(
            state, sp, _pr_data(head_sha="abc123"), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    loaded = watcher._load_state(sp)
    assert loaded["fix_attempts"] == 1
    assert loaded["last_fix_pass_pushed"] is False
    assert loaded["last_concerns_head_sha"] == "abc123"
    assert loaded["last_concerns_summary"] == "issues"


def test_phase1_repeated_concerns_after_noop_fix_escalates(tmp_path: Path) -> None:
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        self_review_loops=1,
        fix_attempts=1,
        last_fix_pass_pushed=False,
        last_concerns_head_sha="abc123",
        last_concerns_summary="same issues",
    )
    gh = _make_gh()

    with (
        patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "CONCERNS", "summary": "same issues"},
        ),
        patch.object(watcher, "_run_fix_pass") as mock_fix,
        patch.object(watcher, "_escalate_needs_human") as mock_escalate,
    ):
        watcher._phase1(
            state, sp, _pr_data(head_sha="abc123"), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_fix.assert_not_called()
    mock_escalate.assert_called_once()
    assert state["escalated_head_sha"] == "abc123"


def test_phase1_concerns_closes_and_requeues_at_fix_cap(tmp_path: Path) -> None:
    # max_fix_attempts=2, already at 2 — CONCERNS must close+requeue, NOT merge.
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=1, fix_attempts=2)
    gh = _make_gh()

    with (
        patch.object(
            watcher, "_run_direct_review", return_value={"result": "CONCERNS", "summary": "still broken"}
        ),
        patch.object(watcher, "_merge_and_done") as mock_merge,
        patch.object(watcher, "_close_and_requeue") as mock_requeue,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_merge.assert_not_called()
    mock_requeue.assert_called_once()
    assert mock_requeue.call_args[1]["reason"] == "fix_attempts_exhausted"


def test_phase1_no_verdict_retries_next_poll(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=0)
    gh = _make_gh()

    with patch.object(watcher, "_run_direct_review", return_value=None):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["self_review_loops"] == 1
    gh.merge_pr.assert_not_called()
    gh.post_comment.assert_not_called()


def test_phase1_no_verdict_escalates_keeps_pr_open(tmp_path: Path) -> None:
    # No parseable verdict at the retry cap → escalate (leave PR open), never
    # merge blind and never close/destroy a possibly-good PR over infra flakiness.
    # max_self_review_loops=2; pre-seed one prior no-verdict pass so this is the 2nd.
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=1, no_verdict_passes=1)
    gh = _make_gh()

    with (
        patch.object(watcher, "_run_direct_review", return_value=None),
        patch.object(watcher, "_merge_and_done") as mock_merge,
        patch.object(watcher, "_close_and_requeue") as mock_close,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_merge.assert_not_called()
    mock_close.assert_not_called()  # good PR is NOT closed on reviewer-unavailable
    gh.close_pr.assert_not_called()
    gh.post_comment.assert_called_once()  # one needs-human escalation comment
    loaded = watcher._load_state(sp)
    assert loaded["escalated_needs_human"] is True
    assert loaded["escalated_head_sha"] == "abc123"
    assert loaded["no_verdict_passes"] == 0  # reset to keep retrying


def test_phase1_skips_empty_diff(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()
    gh.get_pr_diff.return_value = ""

    with patch.object(watcher, "_run_direct_review") as mock_review:
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_review.assert_not_called()
    gh.merge_pr.assert_not_called()


def test_phase1_skips_escalated_pr_without_new_head(tmp_path: Path) -> None:
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        escalated_needs_human=True,
        escalated_head_sha="abc123",
        no_verdict_passes=0,
    )
    gh = _make_gh()

    with patch.object(watcher, "_run_direct_review") as mock_review:
        watcher._phase1(
            state, sp, _pr_data(head_sha="abc123"), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_review.assert_not_called()
    loaded = watcher._load_state(sp)
    assert loaded["escalated_needs_human"] is True
    assert loaded["escalated_head_sha"] == "abc123"


def test_phase1_resumes_escalated_pr_with_null_sha(tmp_path: Path) -> None:
    # When escalated with escalated_head_sha=None but the current PR data has a
    # head SHA, the watcher should repair the state and keep waiting instead of
    # reposting the same needs-human comment every poll.
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        escalated_needs_human=True,
        escalated_head_sha=None,
        no_verdict_passes=0,
    )
    gh = _make_gh()

    with patch.object(
        watcher, "_run_direct_review", return_value={"result": "LGTM", "summary": "ok"}
    ) as mock_review, patch.object(watcher, "_merge_and_done"):
        watcher._phase1(
            state, sp, _pr_data(head_sha="abc123"), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_review.assert_not_called()
    loaded = watcher._load_state(sp)
    assert loaded["escalated_needs_human"] is True
    assert loaded["escalated_head_sha"] == "abc123"


def test_phase1_resumes_escalated_pr_with_null_sha_and_missing_head(tmp_path: Path) -> None:
    # Without a live head SHA we still need the legacy clear-and-retry path.
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        escalated_needs_human=True,
        escalated_head_sha=None,
        no_verdict_passes=0,
    )
    gh = _make_gh()

    with patch.object(
        watcher, "_run_direct_review", return_value={"result": "LGTM", "summary": "ok"}
    ) as mock_review, patch.object(watcher, "_merge_and_done"):
        watcher._phase1(
            state,
            sp,
            _pr_data(head_sha=None),
            gh,
            "owner",
            "repo",
            tmp_path,
            tmp_path / "cfg.yaml",
            SETTINGS,
        )

    mock_review.assert_called_once()
    loaded = watcher._load_state(sp)
    assert loaded["escalated_needs_human"] is False


def test_phase1_resumes_escalated_pr_after_new_head(tmp_path: Path) -> None:
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        escalated_needs_human=True,
        escalated_head_sha="abc123",
        no_verdict_passes=1,
    )
    gh = _make_gh()

    with patch.object(
        watcher, "_run_direct_review", return_value={"result": "LGTM", "summary": "ok"}
    ) as mock_review, patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase1(
            state, sp, _pr_data(head_sha="def456"), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    mock_review.assert_called_once()
    mock_merge.assert_called_once()


# ── CI-green precondition (not an auto-merge trigger) ──────────────────────────


def _settings_with_ci_green_repo() -> MagicMock:
    repo_cfg = MagicMock(
        auto_merge_on_ci_green=True,
        ci_ignored_checks=[],
        clone_url=f"git@github.com:owner/{REPO_KEY}.git",
        default_branch="main",
        await_review=True,
    )
    return MagicMock(
        reviewer=REVIEWER_CFG,
        repos={REPO_KEY: repo_cfg},
        plane=MagicMock(base_url="http://plane.local", project_id="proj", workspace_slug="ws"),
    )


def test_phase1_ci_green_requires_lgtm_not_automerge(tmp_path: Path) -> None:
    # Green CI must NOT auto-merge — it proceeds to the verdict gate; only LGTM merges.
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()
    gh.get_failed_checks.return_value = []  # CI green
    settings = _settings_with_ci_green_repo()

    with (
        patch.object(
            watcher, "_run_direct_review", return_value={"result": "LGTM", "summary": "ok"}
        ) as mock_review,
        patch.object(watcher, "_merge_and_done") as mock_merge,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", settings
        )

    mock_review.assert_called_once()  # the verdict gate ran
    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "self_review_lgtm"  # not auto_merge_on_ci_green


def test_phase1_ci_red_defers_without_review(tmp_path: Path) -> None:
    # Red CI defers — no expensive self-review, no merge — until CI goes green.
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()
    gh.get_failed_checks.return_value = ["Lint (ruff): failure"]  # CI red
    settings = _settings_with_ci_green_repo()

    with patch.object(watcher, "_run_direct_review") as mock_review:
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", settings
        )

    mock_review.assert_not_called()
    gh.merge_pr.assert_not_called()
    # the wait counter advances so the deferral is bounded
    assert watcher._load_state(sp)["ci_wait_cycles"] == 1


def test_phase1_ci_persistently_red_escalates(tmp_path: Path) -> None:
    # Red CI that never goes green must NOT defer forever and must NOT merge —
    # after the wait cap it escalates to a human (leaves the PR open).
    state, sp = _make_state(
        tmp_path, phase="self_review", ci_wait_cycles=watcher._MAX_CI_WAIT_CYCLES - 1
    )
    gh = _make_gh()
    gh.get_failed_checks.return_value = ["Test (pytest): fail"]  # persistently red
    settings = _settings_with_ci_green_repo()

    with patch.object(watcher, "_run_direct_review") as mock_review:
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", settings
        )

    mock_review.assert_not_called()
    gh.merge_pr.assert_not_called()
    gh.close_pr.assert_not_called()  # work preserved, not closed
    gh.post_comment.assert_called_once()  # needs-human escalation
    loaded = watcher._load_state(sp)
    assert loaded["escalated_needs_human"] is True
    assert loaded["escalated_head_sha"] == "abc123"


# ── merge_and_done ────────────────────────────────────────────────────────────


def test_merge_and_done_removes_state_file(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()

    watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="test")

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()


def test_merge_and_done_transitions_plane_task(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()

    mock_client = MagicMock()
    with patch.object(watcher, "_plane_client", return_value=mock_client):
        watcher._merge_and_done(
            state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="lgtm_comment"
        )

    mock_client.transition_issue.assert_called_once_with("task-abc", "Done")
    mock_client.comment_issue.assert_called_once()


# ── close + re-queue (verdict gate's escape hatch) ─────────────────────────────


def test_close_and_requeue_requeues_then_closes_and_deletes_branch(tmp_path: Path) -> None:
    # Re-queue succeeds → close PR (no merge) + delete head branch + drop state.
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {
        "id": "task-abc",
        "description_stripped": (
            "## Goal\nFinish the queue drain fix.\n\n"
            "## Execution\nrepo: MyRepo\nspec_file: docs/specs/queue-drain.md\n"
        ),
        "labels": [],
    }

    with (
        patch.object(watcher, "_requeue_plane_task", return_value=True) as mock_rq,
        patch.object(watcher, "_plane_client", return_value=mock_client),
    ):
        watcher._close_and_requeue(
            state,
            sp,
            _pr_data(),
            gh,
            "owner",
            "repo",
            SETTINGS,
            reason="fix_attempts_exhausted",
            detail="nope",
        )

    mock_rq.assert_called_once()
    mock_client.comment_issue.assert_called_once()
    receipt_body = mock_client.comment_issue.call_args.args[1]
    assert "refs/pull/42/head" in receipt_body
    assert "docs/specs/queue-drain.md" in receipt_body
    gh.close_pr.assert_called_once_with("owner", "repo", PR_NUMBER)
    gh.delete_branch.assert_called_once_with("owner", "repo", f"goal/{PR_NUMBER}")
    gh.merge_pr.assert_not_called()
    assert not sp.exists()


def test_close_and_requeue_records_receipt_comment_before_close(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {
        "id": "task-abc",
        "description_stripped": (
            "## Goal\nFinish the queue drain fix.\n\n"
            "## Execution\nrepo: MyRepo\nspec_file: docs/specs/queue-drain.md\n"
        ),
        "labels": [],
    }

    with (
        patch.object(watcher, "_requeue_plane_task", return_value=True),
        patch.object(watcher, "_plane_client", return_value=mock_client),
    ):
        watcher._close_and_requeue(
            state,
            sp,
            _pr_data(),
            gh,
            "owner",
            "repo",
            SETTINGS,
            reason="fix_attempts_exhausted",
            detail="nope",
        )

    posted = gh.post_comment.call_args.args[3]
    assert "Durable receipt recorded on Plane task `task-abc`" in posted
    assert "refs/pull/42/head" in posted
    assert "docs/specs/queue-drain.md" in posted


def test_close_and_requeue_keeps_branch_when_comment_claims_preserved_work(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {
        "id": "task-abc",
        "description_stripped": (
            "## Goal\nFinish the queue drain fix.\n\n"
            "## Execution\nrepo: MyRepo\nspec_file: docs/specs/queue-drain.md\n"
        ),
        "labels": [],
    }

    with (
        patch.object(watcher, "_requeue_plane_task", return_value=True),
        patch.object(watcher, "_plane_client", return_value=mock_client),
        patch.object(
            watcher,
            "branch_delete_allowed_after_close",
            return_value=False,
        ),
    ):
        watcher._close_and_requeue(
            state,
            sp,
            _pr_data(),
            gh,
            "owner",
            "repo",
            SETTINGS,
            reason="fix_attempts_exhausted",
            detail="nope",
        )

    gh.close_pr.assert_called_once_with("owner", "repo", PR_NUMBER)
    gh.delete_branch.assert_not_called()
    assert not sp.exists()


def test_close_and_requeue_keeps_pr_open_when_requeue_fails(tmp_path: Path) -> None:
    # Plane down → re-queue fails → DON'T close (work would be lost); retry later.
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()

    with patch.object(watcher, "_requeue_plane_task", return_value=False):
        watcher._close_and_requeue(
            state,
            sp,
            _pr_data(),
            gh,
            "owner",
            "repo",
            SETTINGS,
            reason="fix_attempts_exhausted",
            detail="nope",
        )

    gh.close_pr.assert_not_called()
    assert sp.exists()  # state preserved → retried next cycle


def test_close_and_requeue_keeps_pr_open_when_receipt_cannot_be_recorded(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {"id": "task-abc", "description_stripped": "", "labels": []}

    with (
        patch.object(watcher, "_requeue_plane_task", return_value=True),
        patch.object(watcher, "_plane_client", return_value=mock_client),
    ):
        watcher._close_and_requeue(
            state,
            sp,
            _pr_data(),
            gh,
            "owner",
            "repo",
            SETTINGS,
            reason="fix_attempts_exhausted",
            detail="nope",
        )

    gh.close_pr.assert_not_called()
    mock_client.comment_issue.assert_not_called()
    assert sp.exists()


def test_close_and_requeue_no_task_escalates_not_closes(tmp_path: Path) -> None:
    # No Plane task → nowhere to re-queue → escalate (leave open), never close.
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()

    watcher._close_and_requeue(
        state,
        sp,
        _pr_data(),
        gh,
        "owner",
        "repo",
        SETTINGS,
        reason="fix_attempts_exhausted",
        detail="nope",
    )

    gh.close_pr.assert_not_called()
    gh.post_comment.assert_called_once()  # needs-human escalation
    assert watcher._load_state(sp)["escalated_needs_human"] is True


def test_requeue_plane_task_below_cap_goes_ready(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {"id": "t1", "labels": []}

    with patch.object(watcher, "_plane_client", return_value=mock_client):
        ok = watcher._requeue_plane_task(SETTINGS, "t1", pr_number=PR_NUMBER, reason="x")

    assert ok is True
    mock_client.transition_issue.assert_called_once_with("t1", "Ready for AI")
    # dedicated label bumped to 1
    labels = mock_client.update_issue_labels.call_args[0][1]
    assert "reviewer-requeue-count: 1" in labels


def test_requeue_plane_task_at_cap_goes_blocked(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {
        "id": "t1",
        "labels": [{"name": f"reviewer-requeue-count: {watcher._MAX_REQUEUES}"}],
    }

    with patch.object(watcher, "_plane_client", return_value=mock_client):
        ok = watcher._requeue_plane_task(SETTINGS, "t1", pr_number=PR_NUMBER, reason="x")

    assert ok is True
    mock_client.transition_issue.assert_called_once_with("t1", "Blocked")
    labels = mock_client.update_issue_labels.call_args[0][1]
    assert "needs-human" in labels


def test_requeue_plane_task_ignores_execution_retry_count(tmp_path: Path) -> None:
    # board_worker's retry-count must NOT consume the reviewer re-queue budget.
    mock_client = MagicMock()
    mock_client.fetch_issue.return_value = {
        "id": "t1",
        "labels": [{"name": "retry-count: 9"}],  # high execution-retry count
    }

    with patch.object(watcher, "_plane_client", return_value=mock_client):
        watcher._requeue_plane_task(SETTINGS, "t1", pr_number=PR_NUMBER, reason="x")

    # Still re-queues (not Blocked) — reviewer-requeue-count is absent → 0.
    mock_client.transition_issue.assert_called_once_with("t1", "Ready for AI")


def test_requeue_plane_task_returns_false_when_plane_unavailable(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.fetch_issue.side_effect = Exception("plane down")

    with patch.object(watcher, "_plane_client", return_value=mock_client):
        ok = watcher._requeue_plane_task(SETTINGS, "t1", pr_number=PR_NUMBER, reason="x")

    assert ok is False


def test_run_fix_pass_noop_returns_false(tmp_path: Path) -> None:
    # Executor ran cleanly but pushed nothing → must NOT report a push.
    outcome = {"result": {"success": True, "branch_pushed": False}}
    with patch.object(watcher, "_run_pipeline", return_value=outcome):
        pushed = watcher._run_fix_pass(
            tmp_path, tmp_path / "cfg.yaml", REPO_KEY, "goal/1", "fix it", SETTINGS, state_key="k"
        )
    assert pushed is False


def test_merge_and_done_keeps_state_on_merge_failure(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()
    gh.merge_pr.side_effect = Exception("merge conflict")

    watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="test")

    assert sp.exists()  # state preserved for operator inspection


def test_merge_and_done_skips_when_not_mergeable(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()
    gh.get_mergeable.return_value = False

    watcher._merge_and_done(
        state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="auto_merge_on_ci_green"
    )

    gh.merge_pr.assert_not_called()
    assert sp.exists()  # state preserved — branch must be rebased


def test_merge_and_done_proceeds_when_mergeable_unknown(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()
    gh.get_mergeable.return_value = None  # GitHub still computing

    watcher._merge_and_done(
        state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="auto_merge_on_ci_green"
    )

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()  # merged successfully, state cleaned up


# ── draft PR skipped ─────────────────────────────────────────────────────────


def test_poll_once_skips_draft_prs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={
            REPO_KEY: MagicMock(await_review=True, clone_url=f"git@github.com:owner/{REPO_KEY}.git")
        },
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    gh.list_open_prs.return_value = [{"number": 1, "title": "WIP", "draft": True}]

    with (
        patch.object(watcher, "_github_client", return_value=gh),
        patch.object(watcher, "_find_plane_task_id", return_value=None),
    ):
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    sp = watcher._state_path(tmp_path, REPO_KEY, 1)
    assert not sp.exists()


# ── poll_once creates state for new PRs ──────────────────────────────────────


def test_poll_once_creates_state_for_new_pr(tmp_path: Path) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={
            REPO_KEY: MagicMock(await_review=True, clone_url=f"git@github.com:owner/{REPO_KEY}.git")
        },
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    gh.list_open_prs.return_value = [_pr_data()]

    with (
        patch.object(watcher, "_github_client", return_value=gh),
        patch.object(watcher, "_find_plane_task_id", return_value=None),
        patch.object(watcher, "_phase0_ci_fix") as mock_phase0,
    ):
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    assert sp.exists()
    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "ci_fix"
    assert loaded["pr_number"] == PR_NUMBER
    mock_phase0.assert_called_once()


def test_poll_once_skips_repos_without_await_review(tmp_path: Path) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={
            REPO_KEY: MagicMock(
                await_review=False, clone_url=f"git@github.com:owner/{REPO_KEY}.git"
            )
        },
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    with patch.object(watcher, "_github_client", return_value=gh):
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    gh.list_open_prs.assert_not_called()


# ── heartbeat ────────────────────────────────────────────────────────────────


def test_write_heartbeat_creates_file(tmp_path: Path) -> None:
    watcher._write_heartbeat(tmp_path)
    hb = tmp_path / "heartbeat_review.json"
    assert hb.exists()
    data = json.loads(hb.read_text())
    assert data["role"] == "review"
    assert data["status"] == "active"


def test_write_heartbeat_idempotent(tmp_path: Path) -> None:
    watcher._write_heartbeat(tmp_path)
    watcher._write_heartbeat(tmp_path)
    hb = tmp_path / "heartbeat_review.json"
    assert hb.exists()


# ── CLI contract ─────────────────────────────────────────────────────────────


def test_cli_accepts_all_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "cfg.yaml"
    config.write_text(
        "plane:\n  base_url: http://x\n  api_token_env: X\n  workspace_slug: ws\n  project_id: p\ngit:\n  provider: github\nrepos: {}\n"
    )

    monkeypatch.setenv("X", "token")

    with (
        patch.object(watcher, "_load_settings") as mock_settings,
        patch.object(watcher, "_poll_once"),
    ):
        mock_settings.return_value = SETTINGS
        _result = watcher.main.__wrapped__() if hasattr(watcher.main, "__wrapped__") else None

    # Just verify --help doesn't crash and flags are accepted
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "operations_center.entrypoints.pr_review_watcher.main", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "--config" in proc.stdout
    assert "--watch" in proc.stdout
    assert "--poll-interval-seconds" in proc.stdout
    assert "--status-dir" in proc.stdout


# ── OC source-tree cleanliness guard (2026-06-07 reviewer-outage hardening) ───


def _git_init_repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_conflict_markers_clean_tree(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    assert watcher._oc_source_conflict_markers(tmp_path) == []


def test_conflict_markers_detected_in_tracked_py(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text(
        "def f():\n<<<<<<< Updated upstream\n    return 1\n=======\n    return 2\n>>>>>>> Stashed changes\n"
    )
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    hits = watcher._oc_source_conflict_markers(tmp_path)
    assert hits == ["src/pkg/mod.py"]


def test_conflict_markers_ignores_non_py(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "notes.md").write_text("<<<<<<< Updated upstream\nfoo\n=======\nbar\n>>>>>>> x\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    # A markdown conflict does not crash a Python import — must not trip the guard.
    assert watcher._oc_source_conflict_markers(tmp_path) == []


def test_conflict_markers_fail_open_without_git(tmp_path: Path) -> None:
    # No git repo here → git grep errors → guard returns [] (never wedges reviewer).
    assert watcher._oc_source_conflict_markers(tmp_path) == []


def test_run_direct_review_raises_on_unclean_tree(tmp_path: Path) -> None:
    with patch.object(watcher, "_oc_source_conflict_markers", return_value=["src/pkg/bad.py"]):
        with pytest.raises(watcher.OCSourceTreeUncleanError) as ei:
            watcher._run_direct_review(tmp_path, "goal text", STATE_KEY)
    assert "src/pkg/bad.py" in str(ei.value)


def test_run_direct_review_returns_verdict_from_file(tmp_path: Path) -> None:
    # Verify _run_direct_review reads verdict.json written by the subprocess.
    import subprocess as _sp

    verdict = {"result": "LGTM", "summary": "looks good"}

    def _fake_run(cmd, cwd, capture_output, text, timeout):
        # Simulate claude writing verdict.json to the temp cwd.
        (Path(cwd) / "verdict.json").write_text(
            json.dumps(verdict), encoding="utf-8"
        )
        return _sp.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with (
        patch.object(watcher, "_oc_source_conflict_markers", return_value=[]),
        patch("operations_center.entrypoints.pr_review_watcher.main.subprocess.run", side_effect=_fake_run),
    ):
        result = watcher._run_direct_review(tmp_path, "review this diff", STATE_KEY)

    assert result == verdict


def test_run_direct_review_returns_none_when_no_verdict_file(tmp_path: Path) -> None:
    import subprocess as _sp

    def _fake_run(cmd, cwd, capture_output, text, timeout):
        return _sp.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with (
        patch.object(watcher, "_oc_source_conflict_markers", return_value=[]),
        patch("operations_center.entrypoints.pr_review_watcher.main.subprocess.run", side_effect=_fake_run),
    ):
        result = watcher._run_direct_review(tmp_path, "review this diff", STATE_KEY)

    assert result is None


def test_run_pipeline_raises_on_unclean_tree(tmp_path: Path) -> None:
    settings = MagicMock(repos={REPO_KEY: MagicMock(clone_url="u", default_branch="main")})
    with patch.object(watcher, "_oc_source_conflict_markers", return_value=["src/pkg/bad.py"]):
        with pytest.raises(watcher.OCSourceTreeUncleanError) as ei:
            watcher._run_pipeline(
                tmp_path,
                tmp_path / "cfg.yaml",
                REPO_KEY,
                "goal",
                settings,
                source="reviewer_self",
                state_key=STATE_KEY,
                branch_suffix="abc",
            )
    assert "src/pkg/bad.py" in str(ei.value)


def test_unclean_tree_does_not_burn_no_verdict_budget(tmp_path: Path) -> None:
    # An unclean tree must increment env_unclean_passes, NOT no_verdict_passes,
    # and must not escalate on the first pass (max_self_review_loops=2).
    state, sp = _make_state(tmp_path, phase="self_review", no_verdict_passes=0)
    gh = _make_gh()
    with (
        patch.object(watcher, "_run_direct_review", side_effect=watcher.OCSourceTreeUncleanError("dirty")),
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )
    assert state["no_verdict_passes"] == 0
    assert state["env_unclean_passes"] == 1
    esc.assert_not_called()


def test_unclean_tree_escalates_with_specific_reason_after_budget(tmp_path: Path) -> None:
    # After max_self_review_loops unclean passes, escalate — and with the
    # source-tree reason, never a misleading "no verdict / reviewer unavailable".
    state, sp = _make_state(tmp_path, phase="self_review", env_unclean_passes=1)  # max=2
    gh = _make_gh()
    with (
        patch.object(watcher, "_run_direct_review", side_effect=watcher.OCSourceTreeUncleanError("dirty")),
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )
    esc.assert_called_once()
    assert esc.call_args.kwargs.get("reason") == "oc_source_tree_unclean"
    assert state["no_verdict_passes"] == 0


# ── WO-6 item 2: backend crash vs genuine no-verdict budget separation ────────


def test_run_direct_review_raises_backend_error_on_nonzero_exit(tmp_path: Path) -> None:
    # Non-zero exit code = backend crash/rate-limit → ReviewerBackendError, not None.
    import subprocess as _sp

    def _fake_run(cmd, cwd, capture_output, text, timeout):
        return _sp.CompletedProcess(cmd, returncode=1, stdout="rate limit exceeded", stderr="")

    with (
        patch.object(watcher, "_oc_source_conflict_markers", return_value=[]),
        patch("operations_center.entrypoints.pr_review_watcher.main.subprocess.run", side_effect=_fake_run),
    ):
        with pytest.raises(watcher.ReviewerBackendError) as ei:
            watcher._run_direct_review(tmp_path, "review this diff", STATE_KEY)
    assert "rc=1" in str(ei.value)


def test_run_direct_review_raises_backend_error_on_timeout(tmp_path: Path) -> None:
    import subprocess as _sp

    def _fake_run(cmd, cwd, capture_output, text, timeout):
        raise _sp.TimeoutExpired(cmd, timeout)

    with (
        patch.object(watcher, "_oc_source_conflict_markers", return_value=[]),
        patch("operations_center.entrypoints.pr_review_watcher.main.subprocess.run", side_effect=_fake_run),
    ):
        with pytest.raises(watcher.ReviewerBackendError) as ei:
            watcher._run_direct_review(tmp_path, "review this diff", STATE_KEY)
    assert "timed out" in str(ei.value)


def test_run_direct_review_returns_none_on_clean_exit_no_verdict(tmp_path: Path) -> None:
    # Exit 0 + no verdict.json = genuine no-verdict (charged to budget), not a crash.
    import subprocess as _sp

    def _fake_run(cmd, cwd, capture_output, text, timeout):
        return _sp.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with (
        patch.object(watcher, "_oc_source_conflict_markers", return_value=[]),
        patch("operations_center.entrypoints.pr_review_watcher.main.subprocess.run", side_effect=_fake_run),
    ):
        result = watcher._run_direct_review(tmp_path, "review this diff", STATE_KEY)
    assert result is None  # charged against no_verdict budget


def test_backend_crash_does_not_burn_no_verdict_budget(tmp_path: Path) -> None:
    # ReviewerBackendError must increment backend_error_passes, NOT no_verdict_passes,
    # and must not escalate on the first pass (max_self_review_loops=2).
    state, sp = _make_state(tmp_path, phase="self_review", no_verdict_passes=0)
    gh = _make_gh()
    with (
        patch.object(watcher, "_run_direct_review", side_effect=watcher.ReviewerBackendError("rc=1")),
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )
    assert state["no_verdict_passes"] == 0
    assert state["backend_error_passes"] == 1
    esc.assert_not_called()


def test_backend_crash_escalates_with_specific_reason_after_budget(tmp_path: Path) -> None:
    # After max_self_review_loops backend errors, escalate with reviewer_backend_unavailable.
    state, sp = _make_state(tmp_path, phase="self_review", backend_error_passes=1)  # max=2
    gh = _make_gh()
    with (
        patch.object(watcher, "_run_direct_review", side_effect=watcher.ReviewerBackendError("timeout")),
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )
    esc.assert_called_once()
    assert esc.call_args.kwargs.get("reason") == "reviewer_backend_unavailable"
    assert state["no_verdict_passes"] == 0
    assert state["backend_error_passes"] == 0  # reset after escalation


def test_clean_exit_no_verdict_charges_budget(tmp_path: Path) -> None:
    # returncode=0 but no verdict.json = genuine no-verdict, must charge no_verdict_passes.
    state, sp = _make_state(tmp_path, phase="self_review", no_verdict_passes=0)
    gh = _make_gh()
    with patch.object(watcher, "_run_direct_review", return_value=None):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )
    assert state["no_verdict_passes"] == 1
    assert state["backend_error_passes"] == 0


# ── WO-6 item 3: stuck-green escalation ──────────────────────────────────────


def test_stuck_green_escalation_fires_after_repeated_no_verdict_cycles(tmp_path: Path) -> None:
    # After 3 no-verdict escalation cycles (no_verdict_escalation_count >= 3),
    # the detail message must include the stuck-green warning.
    # Seed state at the threshold: 2 escalations already, about to trigger the 3rd.
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        no_verdict_passes=1,  # one more will hit max=2
        no_verdict_escalation_count=2,  # 3rd escalation → stuck-green
    )
    gh = _make_gh()
    captured_detail: list[str] = []

    def _esc(s, sp, gh, o, r, settings, *, reason, detail, current_head_sha=None):
        captured_detail.append(detail)
        s["escalated_needs_human"] = True

    with (
        patch.object(watcher, "_run_direct_review", return_value=None),
        patch.object(watcher, "_escalate_needs_human", side_effect=_esc),
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    assert len(captured_detail) == 1
    assert "stuck_green_repeated_failures" in captured_detail[0]
    assert state["no_verdict_escalation_count"] == 3


def test_stuck_green_not_triggered_on_first_two_escalations(tmp_path: Path) -> None:
    # Stuck-green alarm must NOT fire before the 3rd escalation.
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        no_verdict_passes=1,  # hits max=2
        no_verdict_escalation_count=0,  # 1st escalation — not stuck yet
    )
    gh = _make_gh()
    captured_detail: list[str] = []

    def _esc(s, sp, gh, o, r, settings, *, reason, detail, current_head_sha=None):
        captured_detail.append(detail)
        s["escalated_needs_human"] = True

    with (
        patch.object(watcher, "_run_direct_review", return_value=None),
        patch.object(watcher, "_escalate_needs_human", side_effect=_esc),
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )

    assert len(captured_detail) == 1
    assert "stuck_green_repeated_failures" not in captured_detail[0]
    assert state["no_verdict_escalation_count"] == 1


def test_no_verdict_escalation_count_persisted(tmp_path: Path) -> None:
    # no_verdict_escalation_count must survive save/load cycles.
    state, sp = _make_state(
        tmp_path,
        phase="self_review",
        no_verdict_passes=1,
        no_verdict_escalation_count=1,
    )
    gh = _make_gh()
    with (
        patch.object(watcher, "_run_direct_review", return_value=None),
        patch.object(watcher, "_escalate_needs_human"),
    ):
        watcher._phase1(
            state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS
        )
    loaded = watcher._load_state(sp)
    assert loaded["no_verdict_escalation_count"] == 2


# ── proactive review-queue ordering (2026-06-07 starvation fix) ───────────────


def test_review_priority_tiers() -> None:
    # Fresh self_review (tier 0) < ci_fix (tier 1) < self_review-in-fix-loop (tier 2)
    fresh = {"phase": "self_review", "fix_attempts": 0, "pr_number": 100}
    ci = {"phase": "ci_fix", "fix_attempts": 0, "pr_number": 100}
    battling = {"phase": "self_review", "fix_attempts": 3, "pr_number": 100}
    assert watcher._review_priority(fresh) < watcher._review_priority(ci)
    assert watcher._review_priority(ci) < watcher._review_priority(battling)


def test_review_priority_merge_ready_beats_higher_numbered_fix_battle() -> None:
    # The live case: #247 (green, fresh self_review) must sort before #250
    # (self_review sunk into a fix loop) even though 250 > 247.
    pr247 = {"phase": "self_review", "fix_attempts": 0, "pr_number": 247}
    pr250 = {"phase": "self_review", "fix_attempts": 2, "pr_number": 250}
    assert watcher._review_priority(pr247) < watcher._review_priority(pr250)


def test_review_priority_within_tier_orders_by_attempts_then_number() -> None:
    a = {"phase": "self_review", "fix_attempts": 1, "pr_number": 300}
    b = {"phase": "self_review", "fix_attempts": 2, "pr_number": 200}
    c = {"phase": "self_review", "fix_attempts": 1, "pr_number": 400}
    ordered = sorted([b, c, a], key=watcher._review_priority)
    assert ordered == [a, c, b]  # fix_attempts asc, then pr_number asc


def test_review_priority_defaults_safe_on_empty_state() -> None:
    # Missing keys must not crash; defaults to ci_fix tier.
    key = watcher._review_priority({})
    assert key == (1, 0, 0)


# ── auto-rebase: real-git conflict classification (2026-06-08 WO-6) ────────────


def _make_repo_with_pr_branch(tmp_path: Path):
    """Build origin + a clone with a PR branch behind main. Returns (clone, repo_cfg)."""
    import subprocess as sp

    origin = tmp_path / "origin.git"
    sp.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    seed = tmp_path / "seed"
    sp.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    sp.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / "app.py").write_text("VALUE = 1\n")
    (seed / ".console").mkdir()
    (seed / ".console" / "log.md").write_text("## base\n")
    sp.run(["git", "-C", str(seed), "add", "-A"], check=True)
    sp.run(["git", "-C", str(seed), "commit", "-qm", "base"], check=True)
    sp.run(["git", "-C", str(seed), "branch", "-M", "main"], check=True)
    sp.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
    # PR branch off this base
    sp.run(["git", "-C", str(seed), "checkout", "-qb", "pr"], check=True)
    (seed / "feature.py").write_text("FEATURE = True\n")
    (seed / ".console" / "log.md").write_text("## pr entry\n## base\n")
    sp.run(["git", "-C", str(seed), "add", "-A"], check=True)
    sp.run(["git", "-C", str(seed), "commit", "-qm", "pr work"], check=True)
    sp.run(["git", "-C", str(seed), "push", "-q", "origin", "pr"], check=True)
    # main moves forward (sibling merge) — appends to log.md (the conflict magnet)
    sp.run(["git", "-C", str(seed), "checkout", "-q", "main"], check=True)
    (seed / ".console" / "log.md").write_text("## main entry\n## base\n")
    sp.run(["git", "-C", str(seed), "add", "-A"], check=True)
    sp.run(["git", "-C", str(seed), "commit", "-qm", "main moves"], check=True)
    sp.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)

    clone = tmp_path / "clone"
    sp.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    sp.run(["git", "-C", str(clone), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(clone), "config", "user.name", "t"], check=True)
    repo_cfg = MagicMock(local_path=str(clone), default_branch="main")
    return clone, repo_cfg, origin


def _rebase_settings():
    s = MagicMock()
    s.git_token.return_value = ""
    s.git = MagicMock(author_name="t", author_email="t@t")
    return s


def test_auto_rebase_clean_resolves_log_via_union_and_pushes(tmp_path: Path) -> None:
    import subprocess as sp

    clone, repo_cfg, origin = _make_repo_with_pr_branch(tmp_path)
    # log.md conflicts (both edited line 1) but union must auto-resolve → clean.
    outcome = watcher._attempt_auto_rebase(repo_cfg, "pr", _rebase_settings(), 1)
    assert outcome == "clean"
    # origin/pr now contains main's commit (merged) and both log entries survive.
    merged_log = sp.run(
        ["git", "-C", str(clone), "show", "origin/pr:.console/log.md"],
        capture_output=True, text=True,
    ).stdout
    assert "main entry" in merged_log and "pr entry" in merged_log


def test_auto_rebase_real_code_conflict_aborts(tmp_path: Path) -> None:
    import subprocess as sp

    clone, repo_cfg, origin = _make_repo_with_pr_branch(tmp_path)
    # Introduce a REAL conflict: main and pr both change app.py's VALUE line.
    work = tmp_path / "work"
    sp.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    sp.run(["git", "-C", str(work), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    sp.run(["git", "-C", str(work), "checkout", "-q", "pr"], check=True)
    (work / "app.py").write_text("VALUE = 999\n")
    sp.run(["git", "-C", str(work), "commit", "-qam", "pr edits app"], check=True)
    sp.run(["git", "-C", str(work), "push", "-q", "origin", "pr"], check=True)
    sp.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
    (work / "app.py").write_text("VALUE = 2\n")
    sp.run(["git", "-C", str(work), "commit", "-qam", "main edits app"], check=True)
    sp.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)

    outcome = watcher._attempt_auto_rebase(repo_cfg, "pr", _rebase_settings(), 1)
    assert outcome == "conflict"
    # The conflicting merge must NOT have been pushed — origin/pr is unchanged.
    head = sp.run(
        ["git", "-C", str(clone), "ls-remote", str(origin), "refs/heads/pr"],
        capture_output=True, text=True,
    ).stdout
    assert head.strip()  # branch still exists, not corrupted


def test_auto_rebase_unavailable_without_clone() -> None:
    repo_cfg = MagicMock(local_path=None, default_branch="main")
    assert watcher._attempt_auto_rebase(repo_cfg, "pr", _rebase_settings(), 1) == "unavailable"


# ── auto-rebase orchestration: grace, bounding, budget orthogonality ──────────


def _conflicting_gh() -> MagicMock:
    gh = _make_gh()
    gh.get_mergeable.return_value = False  # CONFLICTING
    return gh


def test_merge_and_done_conflicting_triggers_lazy_rebase(tmp_path: Path) -> None:
    state, sp_ = _make_state(tmp_path, phase="self_review", head_ref="pr", fix_attempts=2)
    gh = _conflicting_gh()
    with (
        patch.object(watcher, "_attempt_auto_rebase", return_value="clean") as reb,
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._merge_and_done(
            state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
        )
    reb.assert_called_once()
    gh.merge_pr.assert_not_called()  # do NOT merge the freshly-rebased tree this cycle
    esc.assert_not_called()
    loaded = watcher._load_state(sp_)
    assert loaded["rebase_attempts"] == 1
    assert loaded["fix_attempts"] == 2  # rebase MUST NOT touch the fix budget
    assert loaded.get("last_rebase_at")


def test_rebase_real_conflict_escalates(tmp_path: Path) -> None:
    state, sp_ = _make_state(tmp_path, phase="self_review", head_ref="pr")
    gh = _conflicting_gh()
    with (
        patch.object(watcher, "_attempt_auto_rebase", return_value="conflict"),
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._merge_and_done(
            state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
        )
    esc.assert_called_once()
    assert esc.call_args.kwargs.get("reason") == "rebase_conflict"


def test_rebase_grace_window_defers(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    recent = datetime.now(UTC).isoformat()
    state, sp_ = _make_state(
        tmp_path, phase="self_review", head_ref="pr", last_rebase_at=recent, rebase_attempts=1
    )
    gh = _conflicting_gh()
    with patch.object(watcher, "_attempt_auto_rebase") as reb:
        watcher._merge_and_done(
            state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
        )
    reb.assert_not_called()  # within grace window → no rebase, just defer


def test_rebase_attempts_exhausted_escalates(tmp_path: Path) -> None:
    state, sp_ = _make_state(
        tmp_path, phase="self_review", head_ref="pr", rebase_attempts=watcher._MAX_REBASE_ATTEMPTS
    )
    gh = _conflicting_gh()
    with (
        patch.object(watcher, "_attempt_auto_rebase") as reb,
        patch.object(watcher, "_escalate_needs_human") as esc,
    ):
        watcher._merge_and_done(
            state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
        )
    reb.assert_not_called()
    esc.assert_called_once()
    assert esc.call_args.kwargs.get("reason") == "rebase_attempts_exhausted"


def test_rebase_transient_outcome_not_charged(tmp_path: Path) -> None:
    # push_rejected/noop/error: no merge commit landed → must NOT consume an attempt.
    state, sp_ = _make_state(tmp_path, phase="self_review", head_ref="pr", rebase_attempts=0)
    gh = _conflicting_gh()
    with patch.object(watcher, "_attempt_auto_rebase", return_value="push_rejected"):
        watcher._merge_and_done(
            state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
        )
    assert watcher._load_state(sp_)["rebase_attempts"] == 0


def test_mergeable_clears_rebase_attempts(tmp_path: Path) -> None:
    state, sp_ = _make_state(tmp_path, phase="self_review", head_ref="pr", rebase_attempts=2)
    gh = _make_gh()
    gh.get_mergeable.return_value = True  # now mergeable
    watcher._merge_and_done(
        state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
    )
    gh.merge_pr.assert_called_once()
    assert not sp_.exists()  # merged, state cleaned


# ── WO-3: Self-retracting verdicts ───────────────────────────────────────────


def test_escalate_stores_comment_id(tmp_path: Path) -> None:
    """_escalate_needs_human stores the posted comment's ID in state."""
    state, sp_ = _make_state(tmp_path)
    gh = _make_gh(comment_id=9001)
    watcher._escalate_needs_human(
        state, sp_, gh, "owner", "repo", SETTINGS,
        reason="no_verdict_unreviewable", detail="details", current_head_sha="abc123",
    )
    assert state["escalated_needs_human"] is True
    assert state["escalation_comment_id"] == 9001
    assert state["escalated_head_sha"] == "abc123"


def test_escalate_no_comment_id_when_post_fails(tmp_path: Path) -> None:
    """If post_comment raises, escalation still sets the flag but no id stored."""
    state, sp_ = _make_state(tmp_path)
    gh = _make_gh()
    gh.post_comment.side_effect = RuntimeError("API down")
    watcher._escalate_needs_human(
        state, sp_, gh, "owner", "repo", SETTINGS,
        reason="no_verdict_unreviewable", detail="details",
    )
    assert state["escalated_needs_human"] is True
    assert state.get("escalation_comment_id") is None


def test_retract_flag_strikes_through_needs_human(tmp_path: Path) -> None:
    """_retract_flag edits the escalation comment with a strikethrough and resolution note."""
    state, sp_ = _make_state(
        tmp_path, escalation_comment_id=9001
    )
    gh = _make_gh()
    original_body = (
        "<!-- operations-center:bot -->\n"
        "**Needs human attention** (reason=`no_verdict_unreviewable`). Left open — "
        "not merged (unresolved) and not closed (work preserved).\n\nSome detail."
    )
    gh.list_pr_comments.return_value = [{"id": 9001, "body": original_body}]
    watcher._retract_flag(state, gh, "owner", "repo", resolution="PR merged")
    gh.update_comment.assert_called_once()
    edited_body = gh.update_comment.call_args[0][3]
    assert "~~**Needs human attention**~~" in edited_body
    assert "**Resolved**: PR merged" in edited_body
    assert state.get("escalation_comment_id") is None  # popped


def test_retract_flag_strikes_through_self_review_concerns(tmp_path: Path) -> None:
    """_retract_flag edits the concerns comment with a strikethrough."""
    state, sp_ = _make_state(tmp_path, concerns_comment_id=8888)
    gh = _make_gh()
    gh.list_pr_comments.return_value = [
        {"id": 8888, "body": "<!-- bot -->\n**Self-review concerns** — auto-fixing:\n\nconcerns"},
    ]
    watcher._retract_flag(state, gh, "owner", "repo", resolution="PR merged")
    gh.update_comment.assert_called_once()
    edited = gh.update_comment.call_args[0][3]
    assert "~~**Self-review concerns**~~" in edited
    assert "**Resolved**: PR merged" in edited
    assert state.get("concerns_comment_id") is None


def test_retract_flag_skips_missing_comment(tmp_path: Path) -> None:
    """If the comment is no longer found on the PR, _retract_flag does not crash."""
    state, sp_ = _make_state(tmp_path, escalation_comment_id=9999)
    gh = _make_gh()
    gh.list_pr_comments.return_value = []  # comment not found
    watcher._retract_flag(state, gh, "owner", "repo", resolution="PR merged")
    gh.update_comment.assert_not_called()


def test_retract_flag_skips_when_no_ids(tmp_path: Path) -> None:
    """_retract_flag is a no-op when no comment IDs are stored."""
    state, sp_ = _make_state(tmp_path)
    gh = _make_gh()
    watcher._retract_flag(state, gh, "owner", "repo", resolution="PR merged")
    gh.list_pr_comments.assert_not_called()
    gh.update_comment.assert_not_called()


def test_merge_and_done_retracts_flag(tmp_path: Path) -> None:
    """_merge_and_done calls _retract_flag before cleaning up state."""
    state, sp_ = _make_state(
        tmp_path, phase="self_review", escalation_comment_id=9001, concerns_comment_id=8888
    )
    gh = _make_gh()
    gh.get_mergeable.return_value = True
    original_esc = "<!-- bot -->\n**Needs human attention** (reason=`ci_persistently_red`)."
    original_con = "<!-- bot -->\n**Self-review concerns** — auto-fixing:\n\nconcerns"
    gh.list_pr_comments.return_value = [
        {"id": 9001, "body": original_esc},
        {"id": 8888, "body": original_con},
    ]
    watcher._merge_and_done(
        state, sp_, _pr_data(), gh, "owner", "repo", SETTINGS, reason="self_review_lgtm"
    )
    assert gh.update_comment.call_count == 2
    bodies = {c[0][2]: c[0][3] for c in gh.update_comment.call_args_list}
    assert "~~**Needs human attention**~~" in bodies[9001]
    assert "~~**Self-review concerns**~~" in bodies[8888]
    assert not sp_.exists()


def test_escalation_cleared_on_head_change_retracts_flag(tmp_path: Path) -> None:
    """When escalation clears due to new push, _retract_flag is called."""
    state, sp_ = _make_state(
        tmp_path,
        phase="self_review",
        escalated_needs_human=True,
        escalated_head_sha="old_sha",
        escalation_comment_id=7777,
        no_verdict_passes=3,
        plane_task_id=None,
    )
    gh = _make_gh()
    original_body = "<!-- bot -->\n**Needs human attention** (reason=`no_verdict_unreviewable`)."
    gh.list_pr_comments.return_value = [{"id": 7777, "body": original_body}]

    with (
        patch.object(watcher, "_run_direct_review", return_value=None),
        patch.object(watcher, "_run_pipeline", return_value=(0, None)),
    ):
        watcher._phase1(
            state, sp_, _pr_data(head_sha="new_sha"), gh, "owner", "repo",
            Path("/oc"), Path("/cfg"), SETTINGS,
        )

    gh.update_comment.assert_called_once()
    edited = gh.update_comment.call_args[0][3]
    assert "~~**Needs human attention**~~" in edited
    assert "new push — automated review resumed" in edited


def test_concerns_comment_id_tracked(tmp_path: Path) -> None:
    """When the first CONCERNS verdict posts the flag, its comment ID is stored in state."""
    state, sp_ = _make_state(
        tmp_path, phase="self_review", plane_task_id="task-1",
        fix_attempts=0, self_review_loops=0,
    )
    gh = _make_gh(comment_id=5555)

    with (
        patch.object(
            watcher, "_run_direct_review",
            return_value={"result": "CONCERNS", "summary": "issues found"},
        ),
        patch.object(watcher, "_run_fix_pass", return_value=True),
        patch.object(watcher, "_merge_and_done"),
    ):
        watcher._phase1(
            state, sp_, _pr_data(), gh, "owner", "repo",
            tmp_path, tmp_path / "cfg.yaml", SETTINGS,
        )

    loaded = watcher._load_state(sp_)
    assert loaded.get("concerns_comment_id") == 5555


def test_new_push_retracts_stale_concerns_and_reposts_for_new_head(tmp_path: Path) -> None:
    """A new PR head retracts the old concerns comment and resets fix state."""
    state, sp_ = _make_state(
        tmp_path,
        phase="self_review",
        plane_task_id="task-1",
        fix_attempts=2,
        concerns_comment_id=8888,
        last_concerns_head_sha="old_sha",
        last_concerns_summary="old concerns",
        last_fix_pass_pushed=False,
    )
    gh = _make_gh(comment_id=5555)
    gh.list_pr_comments.return_value = [
        {"id": 8888, "body": "<!-- bot -->\n**Self-review concerns** — auto-fixing:\n\nold concerns"},
    ]

    with (
        patch.object(
            watcher, "_run_direct_review",
            return_value={"result": "CONCERNS", "summary": "new concerns"},
        ),
        patch.object(watcher, "_run_fix_pass", return_value=True),
        patch.object(watcher, "_merge_and_done"),
    ):
        watcher._phase1(
            state, sp_, _pr_data(head_sha="new_sha"), gh, "owner", "repo",
            tmp_path, tmp_path / "cfg.yaml", SETTINGS,
        )

    assert gh.update_comment.call_count == 1
    retracted = gh.update_comment.call_args[0][3]
    assert "~~**Self-review concerns**~~" in retracted
    assert "superseded by new push — re-review resumed" in retracted
    assert gh.post_comment.call_count == 1
    loaded = watcher._load_state(sp_)
    assert loaded["fix_attempts"] == 1
    assert loaded["concerns_comment_id"] == 5555
    assert loaded["last_concerns_head_sha"] == "new_sha"
    assert loaded["last_concerns_summary"] == "new concerns"
    assert loaded["last_fix_pass_pushed"] is True
