# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for subprocess environment allowlist (SBX Layer 0)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from operations_center.entrypoints.board_worker._subprocess import (
    MINIMAL_ENV_ALLOWLIST,
    build_allowlist_env,
)


class TestMinimalEnvAllowlist:
    """Verify MINIMAL_ENV_ALLOWLIST contains only safe variables."""

    def test_allowlist_has_required_keys(self):
        """Allowlist includes PATH, CI, LANG, LC_ALL (GITHUB_ACTIONS added at runtime)."""
        required_keys = {"PATH", "CI", "LANG", "LC_ALL"}
        assert required_keys.issubset(MINIMAL_ENV_ALLOWLIST.keys())

    def test_allowlist_excludes_secrets(self):
        """Allowlist does not include any known secret variables."""
        secret_keys = {
            "GITHUB_TOKEN",
            "PLANE_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "HOME",
            "USER",
            "SSH_AUTH_SOCK",
        }
        for key in secret_keys:
            assert key not in MINIMAL_ENV_ALLOWLIST, f"{key} should not be in allowlist"

    def test_path_is_pinned(self):
        """PATH is set to a fixed value, not inherited."""
        path_val = MINIMAL_ENV_ALLOWLIST["PATH"]
        assert path_val == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    def test_ci_flag_is_set(self):
        """CI is set to 'true' to indicate automation environment."""
        assert MINIMAL_ENV_ALLOWLIST["CI"] == "true"

    def test_locale_is_set(self):
        """LANG and LC_ALL are set to prevent encoding errors."""
        assert MINIMAL_ENV_ALLOWLIST["LANG"] == "en_US.UTF-8"
        assert MINIMAL_ENV_ALLOWLIST["LC_ALL"] == "en_US.UTF-8"


class TestBuildAllowlistEnv:
    """Test build_allowlist_env() returns minimal, controlled environment."""

    def test_returns_dict(self):
        """build_allowlist_env returns a dict."""
        oc_root = Path("/tmp/oc")
        env = build_allowlist_env(oc_root)
        assert isinstance(env, dict)

    def test_includes_pythonpath(self):
        """build_allowlist_env sets PYTHONPATH to oc_root/src."""
        oc_root = Path("/tmp/oc")
        env = build_allowlist_env(oc_root)
        assert env["PYTHONPATH"] == "/tmp/oc/src"

    def test_includes_all_allowlist_keys(self):
        """build_allowlist_env includes all keys from MINIMAL_ENV_ALLOWLIST."""
        oc_root = Path("/tmp/oc")
        env = build_allowlist_env(oc_root)
        for key in MINIMAL_ENV_ALLOWLIST:
            assert key in env

    def test_does_not_inherit_parent_secrets(self):
        """build_allowlist_env does not inherit parent env secrets."""
        oc_root = Path("/tmp/oc")

        # Simulate secrets in parent environment
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "ghp_secret_token",
                "PLANE_API_KEY": "plane_secret_key",
                "AWS_ACCESS_KEY_ID": "aws_secret_id",
                "AWS_SECRET_ACCESS_KEY": "aws_secret_key",
                "OPENAI_API_KEY": "sk-secret",
            },
        ):
            env = build_allowlist_env(oc_root)

            # Verify secrets are NOT in the allowlist env
            assert "GITHUB_TOKEN" not in env
            assert "PLANE_API_KEY" not in env
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env
            assert "OPENAI_API_KEY" not in env

    def test_does_not_inherit_arbitrary_env_vars(self):
        """build_allowlist_env does not inherit arbitrary environment variables."""
        oc_root = Path("/tmp/oc")

        with mock.patch.dict(
            os.environ,
            {
                "CUSTOM_VAR": "custom_value",
                "RANDOM_VAR": "random_value",
            },
        ):
            env = build_allowlist_env(oc_root)
            assert "CUSTOM_VAR" not in env
            assert "RANDOM_VAR" not in env

    def test_github_actions_inheritance(self):
        """GITHUB_ACTIONS is inherited from parent if present, else defaults to 'false'."""
        oc_root = Path("/tmp/oc")

        # Test with GITHUB_ACTIONS in parent env
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            env = build_allowlist_env(oc_root)
            assert env["GITHUB_ACTIONS"] == "true"

        # Test without GITHUB_ACTIONS in parent env
        with mock.patch.dict(os.environ, {}, clear=True):
            env = build_allowlist_env(oc_root)
            assert env["GITHUB_ACTIONS"] == "false"

    def test_env_has_only_allowlist_keys(self):
        """build_allowlist_env only includes MINIMAL_ENV_ALLOWLIST + PYTHONPATH + GITHUB_ACTIONS."""
        oc_root = Path("/tmp/oc")
        env = build_allowlist_env(oc_root)

        expected_keys = set(MINIMAL_ENV_ALLOWLIST.keys()) | {"PYTHONPATH", "GITHUB_ACTIONS"}
        assert set(env.keys()) == expected_keys

    def test_path_pinned_not_inherited(self):
        """PATH is pinned to allowlist value, not inherited from parent."""
        oc_root = Path("/tmp/oc")

        parent_path = "/custom/bin:/custom/sbin"
        with mock.patch.dict(os.environ, {"PATH": parent_path}):
            env = build_allowlist_env(oc_root)
            assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            assert env["PATH"] != parent_path


class TestEnvDiff:
    """Test that the allowlist env is significantly smaller than parent env."""

    def test_env_minimization(self):
        """Allowlist env is substantially smaller than parent env."""
        oc_root = Path("/tmp/oc")

        # Simulate a realistic parent environment with many vars
        parent_env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/home/user",
            "USER": "user",
            "SHELL": "/bin/bash",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "SSH_AUTH_SOCK": "/tmp/ssh",
            "GITHUB_TOKEN": "ghp_secret",
            "PLANE_API_KEY": "plane_secret",
            "AWS_ACCESS_KEY_ID": "aws_secret",
            "AWS_SECRET_ACCESS_KEY": "aws_secret",
            "CUSTOM_VAR_1": "value1",
            "CUSTOM_VAR_2": "value2",
            "CUSTOM_VAR_3": "value3",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "NPM_TOKEN": "npm_token",
        }

        with mock.patch.dict(os.environ, parent_env, clear=True):
            env = build_allowlist_env(oc_root)

            # Allowlist env should have significantly fewer vars
            # 4 keys from MINIMAL_ENV_ALLOWLIST + PYTHONPATH + GITHUB_ACTIONS = 6 total
            assert len(env) == 6
            assert len(env) < len(parent_env)

            # Verify no secrets made it through
            assert "GITHUB_TOKEN" not in env
            assert "PLANE_API_KEY" not in env
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env
            assert "NPM_TOKEN" not in env
            assert "HOME" not in env
            assert "USER" not in env
            assert "SSH_AUTH_SOCK" not in env

            # Verify required vars are present
            assert "PATH" in env
            assert "PYTHONPATH" in env
            assert "CI" in env
            assert "LANG" in env
            assert "LC_ALL" in env
