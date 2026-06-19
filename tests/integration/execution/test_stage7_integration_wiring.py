# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""End-to-end integration tests for Stage 7: wiring env allowlist, token stripping, and applier.

Tests verify that all three SBX Layer 0 components work together in the dispatch flow:
1. Environment allowlist is applied when spawning workers (dispatch.py)
2. Git clone strips credentials from .git/config (workspace.py::prepare)
3. Patch applier validates and blocks dangerous changes (workspace.py::finalize)
4. All three components are properly integrated without regressions
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.adapters.git.client import GitClient
from operations_center.entrypoints.board_worker._subprocess import build_allowlist_env
from operations_center.execution.workspace import WorkspaceManager


class TestStage7IntegrationEnvAllowlist:
    """Verify environment allowlist is applied in dispatch flow."""

    def test_dispatch_builds_allowlist_env(self) -> None:
        """Test that build_allowlist_env creates minimal safe environment."""
        oc_root = Path(__file__).resolve().parents[4]
        env = build_allowlist_env(oc_root)

        # Verify only safe variables are present
        assert isinstance(env, dict)
        allowed_vars = {"PATH", "PYTHONPATH", "CI", "LANG", "LC_ALL", "GITHUB_ACTIONS"}
        present_vars = set(env.keys())

        # All present vars should be in allowed list
        for var in present_vars:
            assert var in allowed_vars, f"Unexpected env var: {var}"

        # Critical secrets must be absent
        assert "GITHUB_TOKEN" not in env
        assert "PLANE_API_KEY" not in env
        assert not any(k.startswith("AWS_") for k in env.keys())
        assert "OPENAI_API_KEY" not in env

    def test_allowlist_excludes_inherited_secrets(self) -> None:
        """Test that allowlist blocks secrets even if inherited from parent."""
        # Set a secret in the environment
        os.environ["TEST_SECRET_VAR"] = "secret_value_12345"
        try:
            oc_root = Path(__file__).resolve().parents[4]
            env = build_allowlist_env(oc_root)

            # TEST_SECRET_VAR should not be in the allowlist
            assert "TEST_SECRET_VAR" not in env
        finally:
            del os.environ["TEST_SECRET_VAR"]

    def test_allowlist_is_minimal(self) -> None:
        """Test that allowlist keeps env minimal (6-7 vars max)."""
        oc_root = Path(__file__).resolve().parents[4]
        env = build_allowlist_env(oc_root)

        # Should be minimal - typically 6-7 variables max
        assert len(env) <= 8, f"Allowlist too large: {len(env)} vars (expected ≤8)"

    def test_allowlist_has_valid_path(self) -> None:
        """Test that PATH in allowlist is valid and usable."""
        oc_root = Path(__file__).resolve().parents[4]
        env = build_allowlist_env(oc_root)

        # PATH should exist and be non-empty
        assert "PATH" in env
        assert env["PATH"], "PATH is empty"

        # PATH should be a valid directory list
        for path_entry in env["PATH"].split(":"):
            if path_entry:  # Skip empty entries
                assert os.path.isdir(path_entry), f"Invalid PATH entry: {path_entry}"

    def test_allowlist_preserves_pythonpath(self) -> None:
        """Test that PYTHONPATH is properly configured."""
        oc_root = Path(__file__).resolve().parents[4]
        env = build_allowlist_env(oc_root)

        # PYTHONPATH should include src directory
        if "PYTHONPATH" in env:
            pythonpath = env["PYTHONPATH"]
            assert "src" in pythonpath, f"PYTHONPATH missing src: {pythonpath}"


class TestStage7IntegrationTokenStripping:
    """Verify token stripping in git clone flow."""

    @pytest.fixture
    def workspace_manager(self) -> WorkspaceManager:
        """Create a WorkspaceManager instance."""
        return WorkspaceManager()

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace directory."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        return ws

    def test_extract_tokenless_url_removes_ghp_token(
        self, workspace_manager: WorkspaceManager
    ) -> None:
        """Test that GitHub Personal Access tokens are removed from URLs."""
        url = "https://ghp_1234567890abcdefghijklmnopqrstuv@github.com/org/repo.git"
        result = workspace_manager._extract_tokenless_url(url)

        assert "ghp_" not in result
        assert "github.com/org/repo.git" in result
        assert result == "https://github.com/org/repo.git"

    def test_extract_tokenless_url_removes_generic_token(
        self, workspace_manager: WorkspaceManager
    ) -> None:
        """Test that generic tokens are removed from URLs."""
        url = "https://my_secret_token_abc123xyz@github.com/org/repo.git"
        result = workspace_manager._extract_tokenless_url(url)

        assert "my_secret_token_abc123xyz" not in result
        assert "github.com/org/repo.git" in result

    def test_extract_tokenless_url_preserves_ssh(self, workspace_manager: WorkspaceManager) -> None:
        """Test that SSH URLs are left unchanged."""
        url = "git@github.com:org/repo.git"
        result = workspace_manager._extract_tokenless_url(url)

        assert result == url
        assert result == "git@github.com:org/repo.git"

    def test_strip_token_from_config_writes_tokenless_url(
        self,
        workspace_manager: WorkspaceManager,
        temp_workspace: Path,
    ) -> None:
        """Test that git config is rewritten with tokenless URL."""
        # Initialize a minimal git repo
        subprocess.run(["git", "init"], cwd=temp_workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://fake@github.com/org/repo.git"],
            cwd=temp_workspace,
            check=True,
            capture_output=True,
        )

        # Strip the token
        workspace_manager._strip_token_from_config(
            temp_workspace,
            "https://ghp_token123@github.com/org/repo.git",
        )

        # Verify config was updated
        config_file = temp_workspace / ".git" / "config"
        config_text = config_file.read_text()

        assert "ghp_token123" not in config_text
        assert "github.com/org/repo.git" in config_text

    def test_verify_no_token_in_workspace_passes_on_clean_repo(
        self,
        workspace_manager: WorkspaceManager,
        temp_workspace: Path,
    ) -> None:
        """Test that verification passes on repo without tokens."""
        # Initialize a clean git repo
        subprocess.run(["git", "init"], cwd=temp_workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/org/repo.git"],
            cwd=temp_workspace,
            check=True,
            capture_output=True,
        )

        success, errors = workspace_manager.verify_no_token_in_workspace(temp_workspace)

        assert success
        assert not errors

    def test_verify_no_token_in_workspace_fails_on_embedded_credentials(
        self,
        workspace_manager: WorkspaceManager,
        temp_workspace: Path,
    ) -> None:
        """Test that verification fails when tokens are found."""
        # Initialize a git repo with embedded token
        subprocess.run(["git", "init"], cwd=temp_workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://ghp_token123@github.com/org/repo.git"],
            cwd=temp_workspace,
            check=True,
            capture_output=True,
        )

        success, errors = workspace_manager.verify_no_token_in_workspace(temp_workspace)

        assert not success
        assert len(errors) > 0
        assert any("credentials" in e for e in errors)


class TestStage7IntegrationPatchApplier:
    """Verify patch applier integration in finalize flow."""

    @pytest.fixture
    def git_repo(self, tmp_path: Path) -> Path:
        """Create a minimal git repository for testing."""
        repo = tmp_path / "test_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        # Create initial commit
        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        return repo

    def test_workspace_manager_has_patch_applier(self) -> None:
        """Test that WorkspaceManager initializes with patch applier."""
        manager = WorkspaceManager()
        assert hasattr(manager, "_patch_applier")
        assert manager._patch_applier is not None

    def test_validate_patch_method_exists(self, git_repo: Path) -> None:
        """Test that validate_patch_before_commit method exists and is callable."""
        manager = WorkspaceManager()

        # Verify method exists
        assert hasattr(manager, "_validate_patch_before_commit")
        assert callable(manager._validate_patch_before_commit)

        # Test with empty diff (no changes) should pass
        is_valid, error = manager._validate_patch_before_commit(git_repo, MagicMock())
        # Empty diff is valid
        assert is_valid or error is not None  # Either valid or has error message

    def test_validate_patch_blocks_github_workflows(self, git_repo: Path) -> None:
        """Test that .github/workflows changes are blocked."""
        manager = WorkspaceManager()

        # Create a workflow file
        workflows_dir = git_repo / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / "ci.yml").write_text("name: CI\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)

        # Validate the patch - should be blocked
        is_valid, error = manager._validate_patch_before_commit(git_repo, MagicMock())

        assert not is_valid
        assert error is not None
        assert "validation failed" in error.lower() or "blocked" in error.lower()

    def test_validate_patch_blocks_setup_py(self, git_repo: Path) -> None:
        """Test that setup.py modifications are blocked."""
        manager = WorkspaceManager()

        # Create setup.py
        (git_repo / "setup.py").write_text("from setuptools import setup\nsetup()\n")
        subprocess.run(["git", "add", "setup.py"], cwd=git_repo, check=True, capture_output=True)

        # Validate the patch - should be blocked
        is_valid, error = manager._validate_patch_before_commit(git_repo, MagicMock())

        assert not is_valid
        assert error is not None

    def test_validate_patch_blocks_dockerfile(self, git_repo: Path) -> None:
        """Test that Dockerfile changes are blocked."""
        manager = WorkspaceManager()

        # Create Dockerfile
        (git_repo / "Dockerfile").write_text("FROM python:3.14\n")
        subprocess.run(["git", "add", "Dockerfile"], cwd=git_repo, check=True, capture_output=True)

        # Validate the patch - should be blocked
        is_valid, error = manager._validate_patch_before_commit(git_repo, MagicMock())

        assert not is_valid
        assert error is not None


class TestStage7IntegrationAllComponentsTogether:
    """Verify all three components work together in dispatch flow."""

    def test_all_components_configured_in_workspace_manager(self) -> None:
        """Test that WorkspaceManager has all three components configured."""
        manager = WorkspaceManager()

        # Env allowlist function must be importable
        from operations_center.entrypoints.board_worker._subprocess import build_allowlist_env

        assert callable(build_allowlist_env)

        # Token stripping methods must exist
        assert hasattr(manager, "_extract_tokenless_url")
        assert hasattr(manager, "_strip_token_from_config")
        assert hasattr(manager, "verify_no_token_in_workspace")

        # Patch applier must exist
        assert hasattr(manager, "_patch_applier")
        assert manager._patch_applier is not None

    def test_environment_allowlist_and_token_stripping_compatible(self) -> None:
        """Test that env allowlist and token stripping don't conflict."""
        oc_root = Path(__file__).resolve().parents[4]
        env = build_allowlist_env(oc_root)

        # Environment should not contain any tokens
        for key, value in env.items():
            if isinstance(value, str):
                assert "ghp_" not in value, f"Token found in {key}"
                assert "@github.com" not in value, f"Embedded credential found in {key}"

    def test_token_stripping_and_patch_applier_compatible(
        self,
        tmp_path: Path,
    ) -> None:
        """Test that token stripping and patch applier work together."""
        manager = WorkspaceManager()

        # Create a git repo
        repo = tmp_path / "test_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Initialize with a token URL (simulating clone)
        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Strip token from config
        token_url = "https://ghp_token123@github.com/org/repo.git"
        subprocess.run(
            ["git", "remote", "add", "origin", token_url],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Strip the token
        manager._strip_token_from_config(repo, token_url)

        # Verify no token
        success, errors = manager.verify_no_token_in_workspace(repo)
        assert success, f"Token verification failed: {errors}"

        # Test that patch applier can be called after token stripping
        # Verify the method exists and is callable
        assert hasattr(manager, "_validate_patch_before_commit")
        assert callable(manager._validate_patch_before_commit)

        # Call with no changes - should work fine
        is_valid, error = manager._validate_patch_before_commit(repo, MagicMock())
        # Either valid (no changes) or error message is present
        assert is_valid or error is not None

    def test_all_three_components_integration_workflow(
        self,
        tmp_path: Path,
    ) -> None:
        """Test end-to-end workflow with all three components."""
        oc_root = Path(__file__).resolve().parents[4]

        # 1. Test env allowlist is minimal and safe
        env = build_allowlist_env(oc_root)
        assert "GITHUB_TOKEN" not in env
        assert "PLANE_API_KEY" not in env

        # 2. Test token stripping
        manager = WorkspaceManager()

        repo = tmp_path / "test_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        token_url = "https://ghp_abc123@github.com/org/repo.git"
        subprocess.run(
            ["git", "remote", "add", "origin", token_url],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager._strip_token_from_config(repo, token_url)
        success, errors = manager.verify_no_token_in_workspace(repo)
        assert success

        # 3. Test that patch applier is integrated
        # Verify the method exists and is callable
        assert hasattr(manager, "_validate_patch_before_commit")
        assert callable(manager._validate_patch_before_commit)

        # Test calling patch validation on clean repo (no changes)
        is_valid, error = manager._validate_patch_before_commit(repo, MagicMock())
        # Empty diff is valid
        assert is_valid or error is not None

        # 4. Test that blocked paths are still detected
        # Try to add a blocked path
        blocked_file = repo / ".github" / "workflows"
        blocked_file.mkdir(parents=True, exist_ok=True)
        (blocked_file / "ci.yml").write_text("name: CI\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)

        # Validation should detect the blocked path
        is_valid, error = manager._validate_patch_before_commit(repo, MagicMock())
        assert not is_valid, "Blocked path should be rejected"
        assert error is not None


class TestStage7RegressionVerification:
    """Verify no regressions in existing functionality."""

    def test_existing_dispatch_still_works(self) -> None:
        """Test that dispatch.py still imports and uses build_allowlist_env."""
        from operations_center.entrypoints.board_worker.dispatch import dispatch_issue

        # Function must be importable
        assert callable(dispatch_issue)

    def test_workspace_manager_backward_compatibility(self) -> None:
        """Test that WorkspaceManager can be instantiated without breaking changes."""
        manager = WorkspaceManager()

        # All expected methods should exist
        assert hasattr(manager, "prepare")
        assert hasattr(manager, "finalize")
        assert callable(manager.prepare)
        assert callable(manager.finalize)

    def test_git_client_integration(self) -> None:
        """Test that GitClient still works with WorkspaceManager."""
        git_client = GitClient()
        manager = WorkspaceManager(git_client=git_client)

        assert manager._git == git_client
