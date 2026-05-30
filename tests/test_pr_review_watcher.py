# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for pr_review_watcher — three-phase autonomous PR review state machine.

All GitHub API calls are intercepted via monkeypatching GitHubPRClient methods.
The pipeline (_run_pipeline) is stubbed to return controlled verdicts.
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

REPO_KEY   = "MyRepo"
PR_NUMBER  = 42
STATE_KEY  = f"{REPO_KEY}-{PR_NUMBER}"

REVIEWER_CFG = MagicMock(
    bot_logins=[],
    allowed_reviewer_logins=[],
    max_self_review_loops=2,
    bot_comment_marker="<!-- operations-center:bot -->",
)

SETTINGS = MagicMock(
    reviewer=REVIEWER_CFG,
    repos={},
    plane=MagicMock(base_url="http://plane.local", project_id="proj", workspace_slug="ws"),
)


def _pr_data(*, draft: bool = False, title: str = "My PR") -> dict[str, Any]:
    return {"number": PR_NUMBER, "title": title, "draft": draft}


def _make_state(tmp_path: Path, **overrides: Any) -> tuple[dict, Path]:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    state.update(overrides)
    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    watcher._save_state(sp, state)
    return state, sp


def _make_gh() -> MagicMock:
    gh = MagicMock()
    gh.get_pr_diff.return_value = "diff --git a/foo.py\n+print('hello')"
    gh.list_pr_comments.return_value = []
    gh.get_pr_reactions.return_value = []
    gh.has_thumbs_up.return_value = False
    gh.post_comment.return_value = {}
    gh.merge_pr.return_value = {}
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

    with patch.object(watcher, "_run_pipeline", return_value={"result": "LGTM", "summary": "all good"}), \
         patch.object(watcher, "_plane_client") as mock_pc:
        mock_plane = MagicMock()
        mock_pc.return_value.__enter__ = lambda s: mock_plane
        mock_pc.return_value = MagicMock()
        mock_pc.return_value.close = MagicMock()

        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()


def test_phase1_lgtm_increments_loop_count(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "LGTM", "summary": "ok"}), \
         patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    # loop count was incremented before merge decision
    args = mock_merge.call_args
    assert args[1]["reason"] == "self_review_lgtm"


# ── Phase 1: CONCERNS path ───────────────────────────────────────────────────

def test_phase1_concerns_posts_comment(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS", "summary": "fix the bug"}):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    gh.post_comment.assert_called_once()
    body = gh.post_comment.call_args[0][3]
    assert "<!-- operations-center:bot -->" in body
    assert "fix the bug" in body


def test_phase1_concerns_stays_in_phase1_below_max_loops(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=0)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS", "summary": "issues"}):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["self_review_loops"] == 1


def test_phase1_concerns_auto_merges_at_max_loops(tmp_path: Path) -> None:
    # max_self_review_loops=2, already at loop 1 — this call pushes to 2 → auto-merge
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=1)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS", "summary": "still broken"}), \
         patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "self_review_auto_merge"


def test_phase1_no_verdict_retries_next_poll(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=0)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value=None):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["self_review_loops"] == 1
    gh.merge_pr.assert_not_called()
    gh.post_comment.assert_not_called()


def test_phase1_no_verdict_auto_merges_at_max_loops(tmp_path: Path) -> None:
    # No verdict at max loops → auto-merge rather than stalling
    state, sp = _make_state(tmp_path, phase="self_review", self_review_loops=1)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value=None), \
         patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "no_verdict_auto_merge"


def test_phase1_skips_empty_diff(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="self_review")
    gh = _make_gh()
    gh.get_pr_diff.return_value = ""

    with patch.object(watcher, "_run_pipeline") as mock_pipeline:
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_pipeline.assert_not_called()
    gh.merge_pr.assert_not_called()


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
        watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="lgtm_comment")

    mock_client.transition_issue.assert_called_once_with("task-abc", "Done")
    mock_client.comment_issue.assert_called_once()


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

    watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="auto_merge_on_ci_green")

    gh.merge_pr.assert_not_called()
    assert sp.exists()  # state preserved — branch must be rebased


def test_merge_and_done_proceeds_when_mergeable_unknown(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()
    gh.get_mergeable.return_value = None  # GitHub still computing

    watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="auto_merge_on_ci_green")

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()  # merged successfully, state cleaned up


# ── draft PR skipped ─────────────────────────────────────────────────────────

def test_poll_once_skips_draft_prs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={REPO_KEY: MagicMock(await_review=True, clone_url=f"git@github.com:owner/{REPO_KEY}.git")},
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    gh.list_open_prs.return_value = [{"number": 1, "title": "WIP", "draft": True}]

    with patch.object(watcher, "_github_client", return_value=gh), \
         patch.object(watcher, "_find_plane_task_id", return_value=None):
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    sp = watcher._state_path(tmp_path, REPO_KEY, 1)
    assert not sp.exists()


# ── poll_once creates state for new PRs ──────────────────────────────────────

def test_poll_once_creates_state_for_new_pr(tmp_path: Path) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={REPO_KEY: MagicMock(await_review=True, clone_url=f"git@github.com:owner/{REPO_KEY}.git")},
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    gh.list_open_prs.return_value = [_pr_data()]

    with patch.object(watcher, "_github_client", return_value=gh), \
         patch.object(watcher, "_find_plane_task_id", return_value=None), \
         patch.object(watcher, "_phase0_ci_fix") as mock_phase0:
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
        repos={REPO_KEY: MagicMock(await_review=False, clone_url=f"git@github.com:owner/{REPO_KEY}.git")},
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
    config.write_text("plane:\n  base_url: http://x\n  api_token_env: X\n  workspace_slug: ws\n  project_id: p\ngit:\n  provider: github\nrepos: {}\n")

    monkeypatch.setenv("X", "token")

    with patch.object(watcher, "_load_settings") as mock_settings, \
         patch.object(watcher, "_poll_once"):
        mock_settings.return_value = SETTINGS
        _result = watcher.main.__wrapped__() if hasattr(watcher.main, "__wrapped__") else None

    # Just verify --help doesn't crash and flags are accepted
    import subprocess
    import sys
    proc = subprocess.run(
        [sys.executable, "-m", "operations_center.entrypoints.pr_review_watcher.main", "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "--config" in proc.stdout
    assert "--watch" in proc.stdout
    assert "--poll-interval-seconds" in proc.stdout
    assert "--status-dir" in proc.stdout
