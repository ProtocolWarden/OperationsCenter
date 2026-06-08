# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the orphan-branch detector (WO-4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.entrypoints.maintenance.orphan_branch_check import (
    OrphanBranch,
    RepoOrphanResult,
    _ALWAYS_PROTECTED,
    _open_pr_head_branches,
    _scan_repo,
    main,
    scan,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_settings(repos: dict | None = None) -> MagicMock:
    s = MagicMock()
    s.repos = repos or {}
    return s


def _make_repo_cfg(
    local_path: str = "/tmp/fake_repo",
    clone_url: str = "https://github.com/owner/repo.git",
    default_branch: str = "main",
    sandbox_base_branch: str | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.local_path = local_path
    cfg.clone_url = clone_url
    cfg.default_branch = default_branch
    cfg.sandbox_base_branch = sandbox_base_branch
    return cfg


_NOW = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
_OLD = _NOW - timedelta(hours=48)  # 48h ago — definitely an orphan
_RECENT = _NOW - timedelta(hours=6)  # 6h ago — too new


# ── Protected branch set ──────────────────────────────────────────────────────


def test_always_protected_contains_expected_names() -> None:
    assert "main" in _ALWAYS_PROTECTED
    assert "master" in _ALWAYS_PROTECTED
    assert "HEAD" in _ALWAYS_PROTECTED
    assert "operations-center-testing-branch" in _ALWAYS_PROTECTED
    assert "gh-pages" in _ALWAYS_PROTECTED
    assert "prod" in _ALWAYS_PROTECTED
    assert "staging" in _ALWAYS_PROTECTED


# ── _open_pr_head_branches ────────────────────────────────────────────────────


def test_open_pr_head_branches_returns_branch_names() -> None:
    client = MagicMock()
    client.list_open_prs.return_value = [
        {"head": {"ref": "feat/foo"}},
        {"head": {"ref": "fix/bar"}},
    ]
    client.owner_repo_from_clone_url = MagicMock(return_value=("owner", "repo"))
    with patch(
        "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
        return_value=("owner", "repo"),
    ):
        result = _open_pr_head_branches(client, "https://github.com/owner/repo.git")
    assert "feat/foo" in result
    assert "fix/bar" in result


def test_open_pr_head_branches_handles_api_error() -> None:
    client = MagicMock()
    client.list_open_prs.side_effect = Exception("network error")
    with patch(
        "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
        return_value=("owner", "repo"),
    ):
        result = _open_pr_head_branches(client, "https://github.com/owner/repo.git")
    assert result == set()


def test_open_pr_head_branches_skips_prs_without_ref() -> None:
    client = MagicMock()
    client.list_open_prs.return_value = [
        {"head": {}},
        {"head": {"ref": ""}},
        {"head": {"ref": "valid-branch"}},
    ]
    with patch(
        "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
        return_value=("owner", "repo"),
    ):
        result = _open_pr_head_branches(client, "https://github.com/owner/repo.git")
    assert "valid-branch" in result
    assert "" not in result


# ── _scan_repo ────────────────────────────────────────────────────────────────


def _make_git_mock(responses: dict[tuple, tuple[str, int]]) -> MagicMock:
    """Return a mock for _git that returns (stdout, rc) based on args tuple."""

    def _git(args: list[str], cwd: Path) -> tuple[str, int]:
        key = tuple(args)
        return responses.get(key, ("", 0))

    return _git


def test_scan_repo_skips_if_no_dot_git(tmp_path: Path) -> None:
    client = MagicMock()
    result = _scan_repo(
        repo_key="test",
        local_path=tmp_path,
        clone_url="https://github.com/o/r.git",
        sandbox_base_branch=None,
        github_client=client,
        min_age_hours=24.0,
        default_branch="main",
    )
    assert result.skipped is True
    assert result.orphans == []


def test_scan_repo_detects_orphan(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    git_responses = {
        ("fetch", "origin", "--prune", "--quiet"): ("", 0),
        ("branch", "-r", "--format=%(refname:short)"): (
            "origin/main\norigin/HEAD -> origin/main\norigin/feature/orphan",
            0,
        ),
        ("rev-list", "--count", "origin/main..origin/feature/orphan"): ("3", 0),
        ("log", "-1", "--format=%cI", "origin/feature/orphan"): (
            _OLD.isoformat(),
            0,
        ),
    }
    client = MagicMock()
    client.list_open_prs.return_value = []

    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check._git",
            side_effect=lambda args, cwd: git_responses.get(tuple(args), ("", 0)),
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
            return_value=("owner", "repo"),
        ),
    ):
        result = _scan_repo(
            repo_key="test",
            local_path=tmp_path,
            clone_url="https://github.com/owner/repo.git",
            sandbox_base_branch=None,
            github_client=client,
            min_age_hours=24.0,
            default_branch="main",
        )

    assert len(result.orphans) == 1
    assert result.orphans[0].branch == "feature/orphan"
    assert result.orphans[0].commits_ahead == 3


def test_scan_repo_skips_branch_with_open_pr(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    git_responses = {
        ("fetch", "origin", "--prune", "--quiet"): ("", 0),
        ("branch", "-r", "--format=%(refname:short)"): (
            "origin/main\norigin/feat/has-pr",
            0,
        ),
    }
    client = MagicMock()
    client.list_open_prs.return_value = [{"head": {"ref": "feat/has-pr"}}]

    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check._git",
            side_effect=lambda args, cwd: git_responses.get(tuple(args), ("", 0)),
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
            return_value=("owner", "repo"),
        ),
    ):
        result = _scan_repo(
            repo_key="test",
            local_path=tmp_path,
            clone_url="https://github.com/owner/repo.git",
            sandbox_base_branch=None,
            github_client=client,
            min_age_hours=24.0,
            default_branch="main",
        )

    assert result.orphans == []


def test_scan_repo_skips_branch_with_zero_commits_ahead(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    git_responses = {
        ("fetch", "origin", "--prune", "--quiet"): ("", 0),
        ("branch", "-r", "--format=%(refname:short)"): (
            "origin/main\norigin/feat/merged",
            0,
        ),
        ("rev-list", "--count", "origin/main..origin/feat/merged"): ("0", 0),
    }
    client = MagicMock()
    client.list_open_prs.return_value = []

    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check._git",
            side_effect=lambda args, cwd: git_responses.get(tuple(args), ("", 0)),
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
            return_value=("owner", "repo"),
        ),
    ):
        result = _scan_repo(
            repo_key="test",
            local_path=tmp_path,
            clone_url="https://github.com/owner/repo.git",
            sandbox_base_branch=None,
            github_client=client,
            min_age_hours=24.0,
            default_branch="main",
        )

    assert result.orphans == []


def test_scan_repo_skips_recent_branch(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    git_responses = {
        ("fetch", "origin", "--prune", "--quiet"): ("", 0),
        ("branch", "-r", "--format=%(refname:short)"): (
            "origin/main\norigin/feat/new",
            0,
        ),
        ("rev-list", "--count", "origin/main..origin/feat/new"): ("5", 0),
        ("log", "-1", "--format=%cI", "origin/feat/new"): (
            _RECENT.isoformat(),
            0,
        ),
    }
    client = MagicMock()
    client.list_open_prs.return_value = []

    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check._git",
            side_effect=lambda args, cwd: git_responses.get(tuple(args), ("", 0)),
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
            return_value=("owner", "repo"),
        ),
    ):
        result = _scan_repo(
            repo_key="test",
            local_path=tmp_path,
            clone_url="https://github.com/owner/repo.git",
            sandbox_base_branch=None,
            github_client=client,
            min_age_hours=24.0,
            default_branch="main",
        )

    assert result.orphans == []


def test_scan_repo_skips_protected_branches(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    git_responses = {
        ("fetch", "origin", "--prune", "--quiet"): ("", 0),
        ("branch", "-r", "--format=%(refname:short)"): (
            "origin/main\norigin/master\norigin/operations-center-testing-branch\norigin/sandbox-branch",
            0,
        ),
    }
    client = MagicMock()
    client.list_open_prs.return_value = []

    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check._git",
            side_effect=lambda args, cwd: git_responses.get(tuple(args), ("", 0)),
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient.owner_repo_from_clone_url",
            return_value=("owner", "repo"),
        ),
    ):
        result = _scan_repo(
            repo_key="test",
            local_path=tmp_path,
            clone_url="https://github.com/owner/repo.git",
            sandbox_base_branch="sandbox-branch",
            github_client=client,
            min_age_hours=24.0,
            default_branch="main",
        )

    assert result.orphans == []


def test_scan_repo_no_local_path_skipped() -> None:
    client = MagicMock()
    result = _scan_repo(
        repo_key="test",
        local_path=Path("/nonexistent/path"),
        clone_url="https://github.com/o/r.git",
        sandbox_base_branch=None,
        github_client=client,
        min_age_hours=24.0,
        default_branch="main",
    )
    assert result.skipped is True


# ── main() CLI ────────────────────────────────────────────────────────────────


def test_main_returns_zero_on_no_orphans(tmp_path: Path) -> None:
    settings = _make_settings(repos={})
    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.load_settings",
            return_value=settings,
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.scan",
            return_value=[],
        ),
    ):
        rc = main(["--config", "fake.yaml", "--json"])
    assert rc == 0


def test_main_json_output_structure(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    orphan = OrphanBranch(
        repo_key="MyRepo",
        branch="feat/abandoned",
        commits_ahead=7,
        last_commit_at=_OLD,
        age_hours=48.0,
    )
    repo_result = RepoOrphanResult(repo_key="MyRepo", local_path=tmp_path, orphans=[orphan])

    settings = _make_settings()
    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.load_settings",
            return_value=settings,
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.scan",
            return_value=[repo_result],
        ),
    ):
        rc = main(["--config", "fake.yaml", "--json"])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["orphans"]) == 1
    assert out["orphans"][0]["branch"] == "feat/abandoned"
    assert out["orphans"][0]["commits_ahead"] == 7


def test_main_text_output_lists_orphan(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    orphan = OrphanBranch(
        repo_key="MyRepo",
        branch="feat/lost",
        commits_ahead=2,
        last_commit_at=_OLD,
        age_hours=48.0,
    )
    repo_result = RepoOrphanResult(repo_key="MyRepo", local_path=tmp_path, orphans=[orphan])

    settings = _make_settings()
    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.load_settings",
            return_value=settings,
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.scan",
            return_value=[repo_result],
        ),
    ):
        rc = main(["--config", "fake.yaml"])

    assert rc == 0
    captured = capsys.readouterr().out
    assert "feat/lost" in captured
    assert "2 commits" in captured


def test_main_emit_calls_plane_task_creation(tmp_path: Path) -> None:
    orphan = OrphanBranch(
        repo_key="MyRepo",
        branch="feat/old",
        commits_ahead=3,
        last_commit_at=_OLD,
        age_hours=50.0,
    )
    repo_result = RepoOrphanResult(repo_key="MyRepo", local_path=tmp_path, orphans=[orphan])

    settings = _make_settings()
    with (
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.load_settings",
            return_value=settings,
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check.scan",
            return_value=[repo_result],
        ),
        patch(
            "operations_center.entrypoints.maintenance.orphan_branch_check._emit_plane_task",
        ) as mock_emit,
    ):
        rc = main(["--config", "fake.yaml", "--emit"])

    assert rc == 0
    mock_emit.assert_called_once_with(settings, orphan)


# ── scan() integration-level ──────────────────────────────────────────────────


def test_scan_skips_repos_without_local_path() -> None:
    repo_cfg = _make_repo_cfg(local_path="")
    settings = _make_settings(repos={"orphan_repo": repo_cfg})
    repo_cfg.local_path = None  # no local path

    with patch(
        "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient",
    ):
        results = scan(settings, min_age_hours=24.0)

    assert results == []


def test_scan_returns_list_of_results() -> None:
    repo_cfg = _make_repo_cfg(local_path="/nonexistent")
    settings = _make_settings(repos={"repo_a": repo_cfg})

    with patch(
        "operations_center.entrypoints.maintenance.orphan_branch_check.GitHubPRClient",
    ):
        results = scan(settings, min_age_hours=24.0)

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].repo_key == "repo_a"
    assert results[0].skipped is True
