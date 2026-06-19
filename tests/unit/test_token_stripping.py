# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for git token stripping in workspace preparation (SBX Layer 0)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from operations_center.execution.workspace import WorkspaceManager


class TestExtractTokenlessUrl:
    """Test URL tokenization to extract tokenless URLs."""

    def test_extract_token_from_https_url(self):
        """Extract token from https://token@github.com/org/repo.git."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        url = "https://ghp_abc123def456@github.com/org/repo.git"
        result = manager._extract_tokenless_url(url)
        assert result == "https://github.com/org/repo.git"
        assert "ghp_" not in result
        assert "@" not in result

    def test_extract_token_from_simple_token(self):
        """Extract simple token from https://token@github.com/org/repo.git."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        url = "https://simple_token@github.com/org/repo.git"
        result = manager._extract_tokenless_url(url)
        assert result == "https://github.com/org/repo.git"
        assert "@" not in result

    def test_preserve_ssh_url(self):
        """SSH URLs (git@github.com) should remain unchanged."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        url = "git@github.com:org/repo.git"
        result = manager._extract_tokenless_url(url)
        assert result == url

    def test_preserve_tokenless_https_url(self):
        """Already-tokenless URLs should remain unchanged."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        url = "https://github.com/org/repo.git"
        result = manager._extract_tokenless_url(url)
        assert result == url

    def test_preserve_file_url(self):
        """file:// URLs should remain unchanged."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        url = "file:///home/user/repo"
        result = manager._extract_tokenless_url(url)
        assert result == url


class TestVerifyNoTokenInWorkspace:
    """Test verification that no tokens remain in workspace after clone."""

    def test_clean_workspace_passes_verification(self):
        """A workspace with no token in .git/config passes verification."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            git_dir = ws / ".git"
            git_dir.mkdir()
            config_file = git_dir / "config"
            config_file.write_text(
                "[core]\n"
                "\trepositoryformatversion = 0\n"
                '[remote "origin"]\n'
                "\turl = https://github.com/org/repo.git\n",
                encoding="utf-8",
            )

            success, errors = manager.verify_no_token_in_workspace(ws)
            assert success is True
            assert len(errors) == 0

    def test_token_in_config_fails_verification(self):
        """A workspace with token in .git/config fails verification."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            git_dir = ws / ".git"
            git_dir.mkdir()
            config_file = git_dir / "config"
            config_file.write_text(
                '[remote "origin"]\n\turl = https://ghp_token123@github.com/org/repo.git\n',
                encoding="utf-8",
            )

            success, errors = manager.verify_no_token_in_workspace(ws)
            assert success is False
            assert len(errors) > 0
            assert "credentials" in errors[0].lower()

    def test_token_in_reflog_fails_verification(self):
        """A workspace with token in reflog fails verification."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            git_dir = ws / ".git"
            logs_dir = git_dir / "logs"
            logs_dir.mkdir(parents=True)

            # Create a clean config
            config_file = git_dir / "config"
            config_file.write_text(
                '[remote "origin"]\n\turl = https://github.com/org/repo.git\n',
                encoding="utf-8",
            )

            # Create a reflog with embedded token
            head_log = logs_dir / "HEAD"
            head_log.write_text(
                "0000000000000000000000000000000000000000 "
                "1234567890 +0000\tclone: from https://ghp_token123@github.com/org/repo.git\n",
                encoding="utf-8",
            )

            success, errors = manager.verify_no_token_in_workspace(ws)
            assert success is False
            assert len(errors) > 0

    def test_ssh_urls_do_not_trigger_false_positive(self):
        """SSH URLs with @ should not trigger false positives."""
        manager = WorkspaceManager(git_client=mock.MagicMock())

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            git_dir = ws / ".git"
            git_dir.mkdir()
            config_file = git_dir / "config"
            # git@ URLs should not be flagged
            config_file.write_text(
                '[remote "origin"]\n\turl = git@github.com:org/repo.git\n',
                encoding="utf-8",
            )

            success, errors = manager.verify_no_token_in_workspace(ws)
            assert success is True
            assert len(errors) == 0


class TestStripTokenFromConfig:
    """Test the token stripping workflow."""

    def test_strip_token_rewrites_config(self):
        """_strip_token_from_config rewrites .git/config with tokenless URL."""
        git_mock = mock.MagicMock()
        manager = WorkspaceManager(git_client=git_mock)

        # Track the git command calls
        manager._strip_token_from_config(
            Path("/tmp/test"),
            "https://ghp_token123@github.com/org/repo.git",
        )

        # Verify git config command was called with tokenless URL
        git_mock._run.assert_any_call(
            ["git", "config", "remote.origin.url", "https://github.com/org/repo.git"],
            cwd=Path("/tmp/test"),
        )

    def test_strip_token_calls_reflog_cleanup(self):
        """_strip_token_from_config also cleans the reflog."""
        git_mock = mock.MagicMock()
        manager = WorkspaceManager(git_client=git_mock)

        manager._strip_token_from_config(
            Path("/tmp/test"),
            "https://ghp_token123@github.com/org/repo.git",
        )

        # Verify reflog expire was called
        git_mock._run.assert_any_call(
            ["git", "reflog", "expire", "--expire=now", "--all"],
            cwd=Path("/tmp/test"),
        )

        # Verify gc --prune was called
        git_mock._run.assert_any_call(
            ["git", "gc", "--prune=now"],
            cwd=Path("/tmp/test"),
        )
