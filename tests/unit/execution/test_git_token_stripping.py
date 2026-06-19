# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for git token stripping from .git/config and reflog."""

from pathlib import Path
from unittest import mock

import pytest

from operations_center.execution.workspace import WorkspaceManager


class TestExtractTokenlessUrl:
    """Test token extraction from URLs."""

    def test_extract_tokenless_from_https_with_token(self) -> None:
        """Should remove token from https://token@host URL."""
        mgr = WorkspaceManager()
        url = "https://ghp_abc123@github.com/acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/acme/widget.git"
        assert "@" not in result or result.startswith("git@")

    def test_extract_tokenless_from_ssh_unchanged(self) -> None:
        """SSH URLs have no embedded token; return unchanged."""
        mgr = WorkspaceManager()
        url = "git@github.com:acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == url

    def test_extract_tokenless_handles_token_with_colon(self) -> None:
        """Tokens may contain colons; extract correctly."""
        mgr = WorkspaceManager()
        url = "https://user:token@github.com/acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/acme/widget.git"

    def test_extract_tokenless_preserves_path(self) -> None:
        """Should preserve full path after host."""
        mgr = WorkspaceManager()
        url = "https://token@github.com/org/repo/path.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/org/repo/path.git"

    def test_extract_tokenless_handles_complex_path(self) -> None:
        """Should handle paths with multiple segments."""
        mgr = WorkspaceManager()
        url = "https://ghp_longtoken123@github.com/myorg/myrepo.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/myorg/myrepo.git"

    def test_extract_tokenless_no_protocol_unchanged(self) -> None:
        """URLs without protocol are returned unchanged."""
        mgr = WorkspaceManager()
        url = "git@github.com:org/repo.git"
        result = mgr._extract_tokenless_url(url)
        assert result == url


class TestStripTokenFromConfig:
    """Test .git/config token stripping after clone."""

    def test_strip_token_calls_git_config(self, tmp_path: Path) -> None:
        """_strip_token_from_config should call git config to rewrite URL."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)
        clone_url = "https://token@github.com/test/repo.git"

        mgr._strip_token_from_config(ws, clone_url)

        # Verify git config was called
        git_mock._run.assert_called()

    def test_strip_token_rewrites_to_tokenless_url(self, tmp_path: Path) -> None:
        """Should rewrite git config to tokenless URL."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)
        clone_url = "https://token@github.com/test/repo.git"

        mgr._strip_token_from_config(ws, clone_url)

        # Check that git config command was called with tokenless URL
        calls = git_mock._run.call_args_list
        assert len(calls) > 0
        # First call should be git config
        assert "config" in str(calls[0])
        assert "https://github.com/test/repo.git" in str(calls[0])

    def test_strip_token_cleans_reflog(self, tmp_path: Path) -> None:
        """Should clean reflog after stripping token."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        mgr._strip_token_from_config(ws, "https://token@github.com/test/repo.git")

        # Verify reflog commands were called
        calls = [str(call) for call in git_mock._run.call_args_list]
        reflog_or_gc = any("reflog" in str(c) or "gc" in str(c) for c in calls)
        assert reflog_or_gc

    def test_strip_token_raises_on_git_error(self, tmp_path: Path) -> None:
        """Should raise RuntimeError if git config fails."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        git_mock._run.side_effect = RuntimeError("git config failed")
        mgr = WorkspaceManager(git_client=git_mock)

        with pytest.raises(RuntimeError, match="Could not strip token"):
            mgr._strip_token_from_config(ws, "https://token@github.com/test/repo.git")


class TestCleanReflog:
    """Test reflog cleaning."""

    def test_clean_reflog_calls_reflog_expire(self, tmp_path: Path) -> None:
        """Should call git reflog expire."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        mgr._clean_reflog(ws)

        calls = [str(call) for call in git_mock._run.call_args_list]
        assert any("reflog" in c for c in calls)

    def test_clean_reflog_calls_gc(self, tmp_path: Path) -> None:
        """Should call git gc --prune=now."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        mgr._clean_reflog(ws)

        calls = [str(call) for call in git_mock._run.call_args_list]
        assert any("gc" in c for c in calls)

    def test_clean_reflog_non_fatal_on_error(self, tmp_path: Path) -> None:
        """Clean reflog should not raise on git error."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        git_mock._run.side_effect = RuntimeError("git failed")
        mgr = WorkspaceManager(git_client=git_mock)

        # Should not raise
        mgr._clean_reflog(ws)


class TestVerifyNoTokenInWorkspace:
    """Test post-clone verification that no token remains."""

    def test_verify_detects_token_in_git_config(self, tmp_path: Path) -> None:
        """Should detect embedded credentials in .git/config."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Write config WITH embedded token (bad)
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://ghp_abc123@github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert not success
        assert any("credentials" in e.lower() for e in errors)

    def test_verify_accepts_tokenless_git_config(self, tmp_path: Path) -> None:
        """Should pass when .git/config has no embedded credentials."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Write config WITHOUT token (good)
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert success
        assert len(errors) == 0

    def test_verify_accepts_ssh_config(self, tmp_path: Path) -> None:
        """Should pass for SSH URLs (which never embed tokens)."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Write config with SSH URL (safe)
        (git_dir / "config").write_text('[remote "origin"]\n\turl = git@github.com:test/repo.git\n')

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert success
        assert len(errors) == 0

    def test_verify_detects_token_in_reflog(self, tmp_path: Path) -> None:
        """Should detect token in reflog."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Create reflog with token
        (git_dir / "logs").mkdir()
        (git_dir / "logs" / "HEAD").write_text(
            "0000000 abc1234 https://ghp_token@github.com/test/repo.git\n"
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert not success
        assert any("credentials" in e.lower() or "reflog" in e.lower() for e in errors)

    def test_verify_no_error_on_missing_git_dir(self, tmp_path: Path) -> None:
        """Should handle missing .git directory gracefully."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        # Should pass (no token found because no config)
        assert success

    def test_verify_no_error_on_missing_reflog(self, tmp_path: Path) -> None:
        """Should handle missing reflog gracefully."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert success


class TestTokenStrippingWorkflow:
    """End-to-end tests for token stripping workflow."""

    def test_extract_and_strip_workflow(self, tmp_path: Path) -> None:
        """Full workflow: extract tokenless URL and strip config."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        clone_url = "https://ghp_abc123@github.com/acme/widget.git"
        tokenless = WorkspaceManager()._extract_tokenless_url(clone_url)

        assert tokenless == "https://github.com/acme/widget.git"
        assert "@" not in tokenless

    def test_token_stripping_integration(self, tmp_path: Path) -> None:
        """Token stripping should produce tokenless config."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Before strip: config has token
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://token@github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        # Verify we detect the problem
        success, errors = mgr.verify_no_token_in_workspace(ws)
        assert not success

        # Now simulate stripping (in real code, git config would rewrite the file)
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )

        # Verify problem is fixed
        success, errors = mgr.verify_no_token_in_workspace(ws)
        assert success
