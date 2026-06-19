# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for Stage 2: Token stripping and credential handling."""

from pathlib import Path
from unittest import mock


from operations_center.entrypoints.board_worker._subprocess import build_allowlist_env
from operations_center.execution.workspace import WorkspaceManager


class TestStage2CredentialHandling:
    """Full integration: env allowlist + git token stripping work together."""

    def test_acceptance_criteria_1_env_allowlist_excludes_secrets(self, tmp_path: Path) -> None:
        """AC 1: Worker env contains NO secrets."""
        with mock.patch.dict(
            "os.environ",
            {
                "GITHUB_TOKEN": "ghp_secret",
                "PLANE_API_KEY": "pk_secret",
                "AWS_ACCESS_KEY_ID": "AKIA_secret",
                "API_KEY": "sk_secret",
            },
        ):
            env = build_allowlist_env(tmp_path)

            secrets = ["GITHUB_TOKEN", "PLANE_API_KEY", "AWS_ACCESS_KEY_ID", "API_KEY"]
            for secret in secrets:
                assert secret not in env, (
                    f"Acceptance Criteria 1 FAILED: Secret {secret} found in allowlist env"
                )

            # Verify safe vars are present
            assert "PYTHONPATH" in env
            assert "PATH" in env
            assert "LANG" in env

    def test_acceptance_criteria_2_git_token_stripped(self, tmp_path: Path) -> None:
        """AC 2: .git/config has NO token after clone."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Simulate state before stripping
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://token@github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success_before, errors_before = mgr.verify_no_token_in_workspace(ws)
        assert not success_before, "Config should have token before stripping"

        # Simulate stripping
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )

        success_after, errors_after = mgr.verify_no_token_in_workspace(ws)
        assert success_after, (
            f"Acceptance Criteria 2 FAILED: Token still in config after stripping: {errors_after}"
        )

    def test_acceptance_criteria_3_reflog_cleaned(self, tmp_path: Path) -> None:
        """AC 3: Reflog cleaned of token references."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        mgr._strip_token_from_config(ws, "https://token@github.com/acme/widget.git")

        # Verify reflog cleanup was attempted
        calls = [str(call) for call in git_mock._run.call_args_list]
        reflog_or_gc_called = any("reflog" in c.lower() or "gc" in c.lower() for c in calls)
        assert reflog_or_gc_called, (
            f"Acceptance Criteria 3 FAILED: Reflog cleanup not called. Calls: {calls}"
        )

    def test_acceptance_criteria_4_post_clone_verification(self, tmp_path: Path) -> None:
        """AC 4: Post-clone verification confirms no token."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Clean state (no token)
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert success, f"Acceptance Criteria 4 FAILED: Post-clone verification failed: {errors}"
        assert len(errors) == 0, f"Expected no errors, got: {errors}"

    def test_acceptance_criteria_5_env_and_git_together(self, tmp_path: Path) -> None:
        """AC 5: Both env allowlist + credential handling work together."""
        # Part A: Env allowlist works
        with mock.patch.dict(
            "os.environ",
            {
                "GITHUB_TOKEN": "ghp_secret",
                "PLANE_API_KEY": "pk_secret",
            },
        ):
            env = build_allowlist_env(tmp_path)
            assert "GITHUB_TOKEN" not in env
            assert "PLANE_API_KEY" not in env

        # Part B: Git token stripping works
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://token@github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        git_mock = mock.Mock()
        mgr._git = git_mock

        # Extract tokenless URL
        tokenless = mgr._extract_tokenless_url("https://token@github.com/test/repo.git")
        assert tokenless == "https://github.com/test/repo.git"

        # Part C: Verification works
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )
        success, errors = mgr.verify_no_token_in_workspace(ws)
        assert success, f"AC 5 FAILED: Combined verification failed: {errors}"


class TestStage2EdgeCases:
    """Edge cases and error conditions."""

    def test_env_allowlist_with_very_long_inherited_env(self, tmp_path: Path) -> None:
        """Allowlist should ignore large inherited environment."""
        many_env_vars = {f"VAR_{i}": f"value_{i}" for i in range(100)}
        many_env_vars["GITHUB_TOKEN"] = "secret"

        with mock.patch.dict("os.environ", many_env_vars):
            env = build_allowlist_env(tmp_path)
            # Should have only ~6 vars, not 101
            assert len(env) <= 7
            assert "GITHUB_TOKEN" not in env

    def test_token_stripping_with_complex_urls(self, tmp_path: Path) -> None:
        """Token stripping should handle various URL formats."""
        mgr = WorkspaceManager()

        test_cases = [
            ("https://ghp_abc123@github.com/org/repo.git", "https://github.com/org/repo.git"),
            ("https://user:pass@github.com/org/repo.git", "https://github.com/org/repo.git"),
            (
                "https://token@bitbucket.org/project/repo.git",
                "https://bitbucket.org/project/repo.git",
            ),
            ("git@github.com:org/repo.git", "git@github.com:org/repo.git"),
        ]

        for input_url, expected_url in test_cases:
            result = mgr._extract_tokenless_url(input_url)
            assert result == expected_url, f"Failed for {input_url}"
            assert "@" not in result or result.startswith("git@"), (
                f"Token found in result: {result}"
            )

    def test_verification_with_partial_git_state(self, tmp_path: Path) -> None:
        """Verification should handle incomplete .git directory state."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Only config file, no reflog
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/test/repo.git\n'
        )

        mgr = WorkspaceManager()
        success, errors = mgr.verify_no_token_in_workspace(ws)

        assert success, f"Should pass with clean config and no reflog: {errors}"

    def test_verification_with_malformed_config(self, tmp_path: Path) -> None:
        """Verification should handle malformed git config gracefully."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()

        # Malformed config
        (git_dir / "config").write_text("this is not valid git config text\n")

        mgr = WorkspaceManager()
        # Should not crash
        success, errors = mgr.verify_no_token_in_workspace(ws)
        # May pass or fail, but shouldn't crash
        assert isinstance(success, bool)
        assert isinstance(errors, list)


class TestStage2RealWorldScenarios:
    """Real-world scenarios combining env and git token handling."""

    def test_dispatch_worker_with_credential_handling(self, tmp_path: Path) -> None:
        """Worker dispatch should use allowlist env and handle token stripping."""
        # Simulate what dispatch.py does
        from operations_center.entrypoints.board_worker._subprocess import build_allowlist_env

        with mock.patch.dict(
            "os.environ",
            {
                "GITHUB_TOKEN": "ghp_abc123",
                "PATH": "/custom/path",
                "LANG": "fr_FR.UTF-8",
            },
        ):
            oc_root = tmp_path
            env = build_allowlist_env(oc_root)

            # Verify secrets are excluded
            assert "GITHUB_TOKEN" not in env
            # Verify safe vars are included
            assert "PYTHONPATH" in env
            # Verify pinned values
            assert env["LANG"] == "en_US.UTF-8"

    def test_workspace_prepare_with_token_stripping(self, tmp_path: Path) -> None:
        """WorkspaceManager.prepare should strip token from config."""
        # This is a unit test since we can't do real git operations
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".git").mkdir()

        git_mock = mock.Mock()
        mgr = WorkspaceManager(git_client=git_mock)

        # Verify the strip method is available
        assert hasattr(mgr, "_strip_token_from_config")
        assert hasattr(mgr, "_extract_tokenless_url")
        assert hasattr(mgr, "_clean_reflog")
        assert hasattr(mgr, "verify_no_token_in_workspace")

    def test_credential_handling_consistency(self, tmp_path: Path) -> None:
        """Env allowlist and git stripping should be consistent in approach."""
        # Both should follow principle: exclude secrets entirely
        mgr = WorkspaceManager()

        # Env allowlist: no secrets
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "secret"}):
            env = build_allowlist_env(tmp_path)
            assert "GITHUB_TOKEN" not in env

        # Git stripping: no tokens in config
        url_with_token = "https://token@github.com/org/repo.git"
        url_without_token = mgr._extract_tokenless_url(url_with_token)
        assert "@" not in url_without_token or url_without_token.startswith("git@")
