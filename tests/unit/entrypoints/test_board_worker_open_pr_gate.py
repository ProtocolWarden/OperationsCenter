# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
Tests for the P3 OPEN_PR_GATE spec-author exclusion in _claim_next (goal role).

The gate must:
  - Block when blocking (non-spec-author) PRs are open
  - Pass through when only spec-author/* PRs are open (they touch docs/specs/ only)
  - Block when a mix of spec and non-spec PRs exist (non-spec is blocking)
  - Pass through when no PRs are open
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_pr(number: int, ref: str) -> dict:
    return {"number": number, "head": {"ref": ref}}


def _make_issue(task_kind: str = "goal") -> dict:
    return {
        "id": "task-aaa",
        "name": "Fix something important with a description that is long enough",
        "description": "## Goal\nFix something important with a description that is long enough for the guard.",
        "state": {"name": "Ready for AI"},
        "labels": [
            {"name": "task-kind: goal"},
            {"name": "repo: my-repo"},
        ],
        "priority": "medium",
        "created_at": "2026-05-01T00:00:00Z",
        "updated_at": "2026-05-01T00:00:00Z",
    }


def _make_settings(clone_url: str = "git@github.com:owner/my-repo.git") -> MagicMock:
    settings = MagicMock()
    repo_cfg = MagicMock()
    repo_cfg.clone_url = clone_url
    repo_cfg.local_path = "/tmp/my-repo"
    repo_cfg.max_daily_executions = None
    repo_cfg.validation_commands = []
    settings.repos.get.return_value = repo_cfg
    settings.repos.keys.return_value = ["my-repo"]
    settings.git_token.return_value = "ghp_fake_token"
    return settings


def _call_claim_next(open_prs: list[dict]) -> dict | None:
    from operations_center.entrypoints.board_worker.claim import claim_next as _claim_next

    client = MagicMock()
    client.list_issues.return_value = [_make_issue()]
    client.transition_issue.return_value = None

    settings = _make_settings()

    with patch("operations_center.adapters.github_pr.GitHubPRClient") as MockGH:
        gh_instance = MagicMock()
        gh_instance.list_open_prs.return_value = open_prs
        MockGH.return_value = gh_instance
        MockGH.owner_repo_from_clone_url.return_value = ("owner", "my-repo")

        return _claim_next(client, "goal", settings)


class TestOpenPrGateSpecAuthorExclusion:
    def test_no_open_prs_passes_gate(self):
        result = _call_claim_next([])
        assert result is not None, "gate should pass when no PRs are open"

    def test_non_spec_pr_blocks_gate(self):
        prs = [_make_pr(42, "goal/fix-something")]
        result = _call_claim_next(prs)
        assert result is None, "gate should block when a non-spec PR is open"

    def test_spec_author_pr_excluded_from_gate(self):
        prs = [_make_pr(185, "spec-author/abc123")]
        result = _call_claim_next(prs)
        assert result is not None, "gate must pass when only spec-author PRs are open"

    def test_multiple_spec_author_prs_all_excluded(self):
        prs = [
            _make_pr(185, "spec-author/abc"),
            _make_pr(187, "spec-author/def"),
            _make_pr(189, "spec-author/ghi"),
        ]
        result = _call_claim_next(prs)
        assert result is not None, "gate must pass when all open PRs are spec-author"

    def test_mixed_spec_and_non_spec_blocks_on_non_spec(self):
        prs = [
            _make_pr(184, "goal/handle-observed-at"),
            _make_pr(185, "spec-author/abc123"),
        ]
        result = _call_claim_next(prs)
        assert result is None, "gate must block when any non-spec PR is open"

    def test_spec_author_branch_prefix_only(self):
        prs = [_make_pr(99, "spec-author-extra/not-a-spec")]
        result = _call_claim_next(prs)
        assert result is None, "only exact 'spec-author/' prefix should be excluded"
