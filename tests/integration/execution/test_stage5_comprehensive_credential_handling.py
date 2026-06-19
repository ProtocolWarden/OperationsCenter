# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Comprehensive tests for Stage 5: env allowlist + token stripping defenses.

This test module verifies the complete credential handling system:
1. Environment allowlisting (build_allowlist_env) blocks all inherited secrets
2. Token extraction removes credentials from URLs
3. Git config rewriting removes embedded tokens
4. Reflog cleaning removes token references
5. Post-clone verification confirms no credentials persist

These tests ensure the harness trust-hardening Phase 0 credential defense is robust.
"""

from pathlib import Path
from unittest import mock

import pytest

from operations_center.entrypoints.board_worker._subprocess import (
    MINIMAL_ENV_ALLOWLIST,
    build_allowlist_env,
)
from operations_center.execution.workspace import WorkspaceManager


class TestEnvAllowlistComprehensive:
    """Comprehensive tests for environment variable allowlisting.

    Verification: Only whitelisted environment variables are exposed to worker processes.
    No secrets from parent environment are inherited (GITHUB_TOKEN, AWS_*, PLANE_API_KEY, etc).
    """

    # ===== Core Allowlist Structure =====

    def test_allowlist_contains_exactly_safe_keys(self) -> None:
        """Allowlist must contain exactly these safe keys, no more, no less."""
        expected_keys = {"PATH", "LANG", "LC_ALL", "CI"}
        actual_keys = set(MINIMAL_ENV_ALLOWLIST.keys())
        assert actual_keys == expected_keys, f"Expected {expected_keys}, got {actual_keys}"

    def test_allowlist_values_are_safe_and_pinned(self) -> None:
        """Allowlist values must be safe defaults, not inherited from parent."""
        assert MINIMAL_ENV_ALLOWLIST["PATH"].startswith("/usr/")
        assert "custom" not in MINIMAL_ENV_ALLOWLIST["PATH"].lower()
        assert MINIMAL_ENV_ALLOWLIST["LANG"] == "en_US.UTF-8"
        assert MINIMAL_ENV_ALLOWLIST["LC_ALL"] == "en_US.UTF-8"
        assert MINIMAL_ENV_ALLOWLIST["CI"] == "true"

    def test_allowlist_path_includes_standard_system_dirs(self) -> None:
        """PATH must include standard system directories for normal execution."""
        path = MINIMAL_ENV_ALLOWLIST["PATH"]
        assert "/usr/bin" in path
        assert "/bin" in path
        assert "/usr/sbin" in path
        assert "/sbin" in path

    # ===== build_allowlist_env() Function =====

    def test_build_allowlist_env_returns_dict(self, tmp_path: Path) -> None:
        """Function must return a dictionary."""
        result = build_allowlist_env(tmp_path)
        assert isinstance(result, dict)

    def test_build_allowlist_env_sets_pythonpath(self, tmp_path: Path) -> None:
        """PYTHONPATH must be set to oc_root/src."""
        env = build_allowlist_env(tmp_path)
        assert env["PYTHONPATH"] == str(tmp_path / "src")

    def test_build_allowlist_env_includes_all_whitelisted_vars(self, tmp_path: Path) -> None:
        """Function must include all whitelisted vars plus PYTHONPATH."""
        env = build_allowlist_env(tmp_path)
        expected_keys = {"PATH", "LANG", "LC_ALL", "CI", "PYTHONPATH", "GITHUB_ACTIONS"}
        assert set(env.keys()) == expected_keys

    def test_build_allowlist_env_github_actions_default_false(self, tmp_path: Path) -> None:
        """GITHUB_ACTIONS must default to 'false' when not in parent environment."""
        with mock.patch.dict("os.environ", {}, clear=True):
            env = build_allowlist_env(tmp_path)
            assert env["GITHUB_ACTIONS"] == "false"

    def test_build_allowlist_env_github_actions_inherits_from_parent(self, tmp_path: Path) -> None:
        """GITHUB_ACTIONS must be inherited if present in parent environment."""
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            env = build_allowlist_env(tmp_path)
            assert env["GITHUB_ACTIONS"] == "true"

    # ===== Secret Blocking =====

    def test_build_allowlist_env_excludes_github_token(self, tmp_path: Path) -> None:
        """GITHUB_TOKEN must be excluded from worker environment."""
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_1234567890abcdef"}):
            env = build_allowlist_env(tmp_path)
            assert "GITHUB_TOKEN" not in env

    def test_build_allowlist_env_excludes_aws_credentials(self, tmp_path: Path) -> None:
        """All AWS credentials must be excluded from worker environment."""
        aws_vars = {
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "AWS_SESSION_TOKEN": "AQoDYXdzEJr...",
            "AWS_PROFILE": "default",
        }
        with mock.patch.dict("os.environ", aws_vars):
            env = build_allowlist_env(tmp_path)
            for var_name in aws_vars.keys():
                assert var_name not in env, f"{var_name} should be excluded"

    def test_build_allowlist_env_excludes_plane_api_key(self, tmp_path: Path) -> None:
        """PLANE_API_KEY must be excluded from worker environment."""
        with mock.patch.dict("os.environ", {"PLANE_API_KEY": "pk_123456789"}):
            env = build_allowlist_env(tmp_path)
            assert "PLANE_API_KEY" not in env

    def test_build_allowlist_env_excludes_openai_api_key(self, tmp_path: Path) -> None:
        """OPENAI_API_KEY must be excluded from worker environment."""
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk_live_123abc"}):
            env = build_allowlist_env(tmp_path)
            assert "OPENAI_API_KEY" not in env

    def test_build_allowlist_env_excludes_generic_secrets(self, tmp_path: Path) -> None:
        """Generic secret variables must be excluded."""
        secret_vars = {
            "SECRET_KEY": "super_secret",
            "API_KEY": "api_secret",
            "DB_PASSWORD": "password123",
            "CUSTOM_SECRET": "should_not_be_included",
        }
        with mock.patch.dict("os.environ", secret_vars):
            env = build_allowlist_env(tmp_path)
            for var_name in secret_vars.keys():
                assert var_name not in env, f"{var_name} should be excluded"

    # ===== Parent Environment Handling =====

    def test_build_allowlist_env_ignores_parent_path(self, tmp_path: Path) -> None:
        """Parent PATH must be ignored; only safe default PATH is used."""
        with mock.patch.dict("os.environ", {"PATH": "/custom/dangerous/path:/usr/bin"}):
            env = build_allowlist_env(tmp_path)
            # PATH should be reset to safe default, not inherited
            assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            assert "/custom/dangerous/path" not in env["PATH"]

    def test_build_allowlist_env_ignores_parent_lang(self, tmp_path: Path) -> None:
        """Parent LANG must be ignored; only safe default LANG is used."""
        with mock.patch.dict("os.environ", {"LANG": "fr_FR.UTF-8"}):
            env = build_allowlist_env(tmp_path)
            # LANG should be reset to safe default
            assert env["LANG"] == "en_US.UTF-8"

    def test_build_allowlist_env_minimal_size(self, tmp_path: Path) -> None:
        """Environment must be minimal regardless of parent environment size."""
        # Create a parent env with 30+ variables
        parent_env = {f"VAR_{i}": f"value_{i}" for i in range(30)}
        parent_env.update(
            {
                "PATH": "/custom",
                "LANG": "de_DE",
                "GITHUB_TOKEN": "ghp_secret",
            }
        )

        with mock.patch.dict("os.environ", parent_env):
            env = build_allowlist_env(tmp_path)
            # Should have exactly 6 keys: PATH, LANG, LC_ALL, CI, PYTHONPATH, GITHUB_ACTIONS
            assert len(env) == 6, f"Expected 6 keys, got {len(env)}: {set(env.keys())}"

    def test_build_allowlist_env_deterministic(self, tmp_path: Path) -> None:
        """Multiple calls must produce identical environments."""
        with mock.patch.dict("os.environ", {"EXTRA": "var", "GITHUB_TOKEN": "secret"}):
            env1 = build_allowlist_env(tmp_path)
            env2 = build_allowlist_env(tmp_path)
            assert env1 == env2

    # ===== Verification: Only Expected Variables Present =====

    def test_worker_env_contains_only_expected_variables(self, tmp_path: Path) -> None:
        """Final verification: worker environment contains only expected variables."""
        env = build_allowlist_env(tmp_path)
        expected = {"PATH", "LANG", "LC_ALL", "CI", "PYTHONPATH", "GITHUB_ACTIONS"}
        actual = set(env.keys())
        assert actual == expected, f"Unexpected variables: {actual - expected}"


class TestTokenStrippingComprehensive:
    """Comprehensive tests for token stripping from git artifacts.

    Verification:
    1. Token extraction removes embedded credentials from URLs
    2. Git config rewriting removes embedded tokens
    3. Reflog cleaning removes token references
    4. Post-clone verification confirms no tokens persist
    """

    # ===== URL Token Extraction =====

    def test_extract_tokenless_url_https_with_ghp_token(self) -> None:
        """Extract token from GitHub HTTPS URL with GHP token."""
        mgr = WorkspaceManager()
        url = "https://ghp_abc123def456@github.com/acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/acme/widget.git"
        assert "ghp_" not in result
        assert "@" not in result

    def test_extract_tokenless_url_https_with_generic_token(self) -> None:
        """Extract token from HTTPS URL with generic token."""
        mgr = WorkspaceManager()
        url = "https://mytoken@github.com/acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/acme/widget.git"
        assert "mytoken" not in result
        assert "@" not in result

    def test_extract_tokenless_url_https_with_user_colon_token(self) -> None:
        """Extract token from HTTPS URL with user:token format."""
        mgr = WorkspaceManager()
        url = "https://user:password@github.com/acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/acme/widget.git"
        assert "password" not in result
        assert "user" not in result

    def test_extract_tokenless_url_ssh_unchanged(self) -> None:
        """SSH URLs should be returned unchanged (no embedded token)."""
        mgr = WorkspaceManager()
        url = "git@github.com:acme/widget.git"
        result = mgr._extract_tokenless_url(url)
        assert result == url

    def test_extract_tokenless_url_preserves_complex_paths(self) -> None:
        """Must preserve complex repository paths."""
        mgr = WorkspaceManager()
        url = "https://token@github.com/org/group/subgroup/repo.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/org/group/subgroup/repo.git"
        assert "/org/group/subgroup/repo.git" in result

    def test_extract_tokenless_url_with_special_char_token(self) -> None:
        """Extract token containing special characters."""
        mgr = WorkspaceManager()
        url = "https://ghp_abc123!@#$@github.com/test/repo.git"
        result = mgr._extract_tokenless_url(url)
        assert result == "https://github.com/test/repo.git"
        assert "!@#$" not in result

    def test_extract_tokenless_url_no_protocol_ssh(self) -> None:
        """URLs without protocol are returned unchanged."""
        mgr = WorkspaceManager()
        url = "git@github.com:org/repo.git"
        result = mgr._extract_tokenless_url(url)
        assert result == url

    # ===== Git Config Rewriting =====

    def test_strip_token_from_config_calls_git_config(self, tmp_path: Path) -> None:
        """_strip_token_from_config must call git config command."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)
        clone_url = "https://token@github.com/test/repo.git"

        mgr._strip_token_from_config(ws, clone_url)

        # Verify git was called
        assert git_mock._run.called

    def test_strip_token_from_config_rewrites_to_tokenless(self, tmp_path: Path) -> None:
        """Git config must be rewritten with tokenless URL."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)
        clone_url = "https://token@github.com/test/repo.git"

        mgr._strip_token_from_config(ws, clone_url)

        # Check that git config was called with tokenless URL
        calls = [str(call) for call in git_mock._run.call_args_list]
        assert any("config" in c for c in calls)
        assert any("https://github.com/test/repo.git" in c for c in calls)

    def test_strip_token_from_config_raises_on_git_error(self, tmp_path: Path) -> None:
        """Should raise RuntimeError if git config fails."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        git_mock._run.side_effect = RuntimeError("git config failed")
        mgr = WorkspaceManager(git_client=git_mock)

        with pytest.raises(RuntimeError, match="Could not strip token"):
            mgr._strip_token_from_config(ws, "https://token@github.com/test/repo.git")

    # ===== Reflog Cleaning =====

    def test_clean_reflog_calls_reflog_expire(self, tmp_path: Path) -> None:
        """_clean_reflog must call git reflog expire command."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        mgr._clean_reflog(ws)

        calls = [str(call) for call in git_mock._run.call_args_list]
        assert any("reflog" in c for c in calls)

    def test_clean_reflog_calls_gc_prune(self, tmp_path: Path) -> None:
        """_clean_reflog must call git gc --prune command."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        mgr._clean_reflog(ws)

        calls = [str(call) for call in git_mock._run.call_args_list]
        assert any("gc" in c for c in calls)

    def test_clean_reflog_non_fatal_on_error(self, tmp_path: Path) -> None:
        """Clean reflog should handle errors gracefully without raising."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        git_mock._run.side_effect = RuntimeError("git failed")
        mgr = WorkspaceManager(git_client=git_mock)

        # Should not raise
        mgr._clean_reflog(ws)

    # ===== Post-Clone Verification =====

    def test_verify_no_token_in_config_detects_embedded_token(self, tmp_path: Path) -> None:
        """Verification must detect embedded token in .git/config."""
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
        assert len(errors) > 0
        assert any("credentials" in e.lower() or "token" in e.lower() for e in errors)

    def test_verify_no_token_in_config_accepts_tokenless(self, tmp_path: Path) -> None:
        """Verification must accept config with tokenless URL."""
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

    def test_verify_no_token_in_config_accepts_ssh(self, tmp_path: Path) -> None:
        """Verification must accept SSH URLs (which never have embedded tokens)."""
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

    def test_verify_no_token_in_reflog_detects_token(self, tmp_path: Path) -> None:
        """Verification must detect token in reflog."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Create reflog with token
        (git_dir / "logs").mkdir()
        (git_dir / "logs" / "HEAD").write_text(
            "0000000 abc1234 https://ghp_token@github.com/test/repo.git clone\n"
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert not success
        assert any("credentials" in e.lower() or "reflog" in e.lower() for e in errors)

    def test_verify_no_token_handles_missing_git_dir(self, tmp_path: Path) -> None:
        """Verification must handle missing .git directory gracefully."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        # Should pass (no token found because no config)
        assert success
        assert len(errors) == 0

    def test_verify_no_token_handles_missing_reflog(self, tmp_path: Path) -> None:
        """Verification must handle missing reflog directory."""
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
        assert len(errors) == 0

    # ===== Edge Cases and Alternate Credential Formats =====

    def test_verify_detects_pat_tokens(self, tmp_path: Path) -> None:
        """Verification must detect PAT (Personal Access Token) formats."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # GitHub PAT format: ghp_, ghs_, ghu_
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://ghs_abc123@github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert not success

    def test_verify_detects_oauth_tokens(self, tmp_path: Path) -> None:
        """Verification must detect OAuth token formats."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # OAuth token (ghu_)
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://ghu_abc123@github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert not success

    def test_extract_token_handles_unicode_chars(self) -> None:
        """Token extraction must handle URLs with unicode characters."""
        mgr = WorkspaceManager()
        url = "https://token@github.com/naïve/repo.git"
        result = mgr._extract_tokenless_url(url)
        assert "token" not in result
        assert "naïve" in result or "na%C3%AFve" in result

    def test_extract_token_long_token_string(self) -> None:
        """Token extraction must work with very long token strings."""
        mgr = WorkspaceManager()
        long_token = "a" * 500  # Very long token
        url = f"https://{long_token}@github.com/test/repo.git"
        result = mgr._extract_tokenless_url(url)
        assert long_token not in result
        assert result == "https://github.com/test/repo.git"

    # ===== Integration: End-to-End Workflow =====

    def test_env_allowlist_and_token_stripping_together(self, tmp_path: Path) -> None:
        """Verify environment allowlist and token stripping work together."""
        # Setup: parent env with secrets and token
        parent_env = {
            "GITHUB_TOKEN": "ghp_secret",
            "AWS_ACCESS_KEY_ID": "AKIA_key",
            "PLANE_API_KEY": "pk_secret",
            "PATH": "/custom/path",
        }

        with mock.patch.dict("os.environ", parent_env):
            # Verify env allowlist blocks secrets
            env = build_allowlist_env(tmp_path)
            assert "GITHUB_TOKEN" not in env
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "PLANE_API_KEY" not in env

        # Verify token extraction works
        clone_url = "https://ghp_secret@github.com/test/repo.git"
        mgr = WorkspaceManager()
        tokenless = mgr._extract_tokenless_url(clone_url)
        assert "ghp_secret" not in tokenless

    def test_token_stripping_workflow_simulation(self, tmp_path: Path) -> None:
        """Simulate complete token stripping workflow after git clone."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        clone_url = "https://ghp_abc123@github.com/test/repo.git"

        # Step 1: Extract tokenless URL
        mgr = WorkspaceManager()
        tokenless = mgr._extract_tokenless_url(clone_url)
        assert "ghp_abc123" not in tokenless

        # Step 2: Write initial config with token (simulating clone)
        (git_dir / "config").write_text(f'[remote "origin"]\n\turl = {clone_url}\n')

        # Step 3: Verify token is detected (pre-cleanup)
        success_before, errors_before = mgr.verify_no_token_in_workspace(ws)
        assert not success_before

        # Step 4: Simulate token stripping (rewrite config)
        (git_dir / "config").write_text(f'[remote "origin"]\n\turl = {tokenless}\n')

        # Step 5: Verify token is gone (post-cleanup)
        success_after, errors_after = mgr.verify_no_token_in_workspace(ws)
        assert success_after
        assert len(errors_after) == 0
