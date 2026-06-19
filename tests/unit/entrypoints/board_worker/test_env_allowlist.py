# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for environment variable allowlisting in board_worker."""

from pathlib import Path
from unittest import mock


from operations_center.entrypoints.board_worker._subprocess import (
    MINIMAL_ENV_ALLOWLIST,
    build_allowlist_env,
)


class TestEnvAllowlist:
    """Verify environment allowlisting blocks all secrets."""

    def test_allowlist_contains_only_safe_keys(self) -> None:
        """Allowlist should contain only whitelisted variable names."""
        safe_keys = {"PATH", "LANG", "LC_ALL", "CI"}
        assert set(MINIMAL_ENV_ALLOWLIST.keys()) == safe_keys

    def test_build_allowlist_env_returns_dict(self, tmp_path: Path) -> None:
        """build_allowlist_env() returns a dict."""
        env = build_allowlist_env(tmp_path)
        assert isinstance(env, dict)

    def test_build_allowlist_env_sets_pythonpath(self, tmp_path: Path) -> None:
        """PYTHONPATH should be set to oc_root/src."""
        env = build_allowlist_env(tmp_path)
        expected = str(tmp_path / "src")
        assert env["PYTHONPATH"] == expected

    def test_build_allowlist_env_has_minimal_keys(self, tmp_path: Path) -> None:
        """Environment should have minimal safe keys plus PYTHONPATH."""
        env = build_allowlist_env(tmp_path)
        expected_keys = {"PATH", "LANG", "LC_ALL", "CI", "PYTHONPATH", "GITHUB_ACTIONS"}
        assert set(env.keys()) == expected_keys

    def test_build_allowlist_env_excludes_inherited_secrets(self, tmp_path: Path) -> None:
        """Even if parent env has secrets, allowlist should exclude them."""
        with mock.patch.dict(
            "os.environ",
            {
                "GITHUB_TOKEN": "ghp_secret123",
                "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
                "CUSTOM_SECRET": "should_not_be_included",
                "PATH": "/custom/path",
            },
        ):
            env = build_allowlist_env(tmp_path)
            assert "GITHUB_TOKEN" not in env
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "CUSTOM_SECRET" not in env
            # PATH is pinned to safe default, not inherited from parent
            assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    def test_build_allowlist_env_includes_github_actions_if_present(self, tmp_path: Path) -> None:
        """GITHUB_ACTIONS should be included if in parent env."""
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            env = build_allowlist_env(tmp_path)
            assert env.get("GITHUB_ACTIONS") == "true"

    def test_build_allowlist_env_defaults_github_actions_to_false(self, tmp_path: Path) -> None:
        """GITHUB_ACTIONS should default to 'false' if not in parent env."""
        with mock.patch.dict("os.environ", {}, clear=True):
            env = build_allowlist_env(tmp_path)
            assert env.get("GITHUB_ACTIONS") == "false"

    def test_build_allowlist_env_excludes_plane_token(self, tmp_path: Path) -> None:
        """PLANE_API_KEY must not be in allowlist env."""
        with mock.patch.dict("os.environ", {"PLANE_API_KEY": "secret_plane_token"}):
            env = build_allowlist_env(tmp_path)
            assert "PLANE_API_KEY" not in env

    def test_build_allowlist_env_excludes_openai_key(self, tmp_path: Path) -> None:
        """OPENAI_API_KEY must not be in allowlist env."""
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk_secret"}):
            env = build_allowlist_env(tmp_path)
            assert "OPENAI_API_KEY" not in env

    def test_minimal_allowlist_has_safe_path(self) -> None:
        """PATH should be set to a safe, standard value."""
        path = MINIMAL_ENV_ALLOWLIST["PATH"]
        assert path.startswith("/usr/local/sbin")
        assert "/bin" in path
        assert "custom" not in path.lower()
        assert "user" not in path.lower()


class TestEnvAllowlistIntegration:
    """Integration tests: env allowlist actually excludes secrets."""

    def test_worker_env_passed_to_subprocess_excludes_secrets(self, tmp_path: Path) -> None:
        """Verify that worker subprocess receives allowlist env, not full env."""
        with mock.patch.dict(
            "os.environ",
            {
                "GITHUB_TOKEN": "ghp_abc123",
                "PLANE_API_KEY": "pk_xyz",
                "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "PATH": "/opt/custom/bin:/usr/bin",
                "LANG": "fr_FR.UTF-8",
            },
        ):
            env = build_allowlist_env(tmp_path)

            # Secrets excluded
            assert "GITHUB_TOKEN" not in env
            assert "PLANE_API_KEY" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env

            # Safe values present but pinned (not inherited)
            assert env["LANG"] == "en_US.UTF-8"
            assert env["PATH"] != "/opt/custom/bin:/usr/bin"
            assert "/usr/bin" in env["PATH"]

    def test_allowlist_env_minimal_size(self, tmp_path: Path) -> None:
        """Allowlist env should have only ~6 keys, not full parent env."""
        with mock.patch.dict("os.environ", {f"EXTRA_VAR_{i}": f"value_{i}" for i in range(20)}):
            env = build_allowlist_env(tmp_path)
            assert len(env) <= 7  # PATH, LANG, LC_ALL, CI, PYTHONPATH, GITHUB_ACTIONS, + extras

    def test_allowlist_env_deterministic(self, tmp_path: Path) -> None:
        """Multiple calls with same input should return same env."""
        env1 = build_allowlist_env(tmp_path)
        env2 = build_allowlist_env(tmp_path)
        assert env1 == env2
