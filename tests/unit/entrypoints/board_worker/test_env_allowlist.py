# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for environment variable allowlisting in board_worker.

The allowlist is a *minimization*, not a strip-everything: it drops secrets the
worker provably does not need (the Plane token, sibling-repo tokens, host secrets)
while preserving what it needs to run/review/fix/push — HOME + cache dirs, model
creds, and the ACTIVE repo's git token. Dropping those would hard-halt the fleet,
violating the self-healing invariant (HARNESS_TRUST_HARDENING.md §0.1). These
tests pin both halves of that contract.
"""

from pathlib import Path
from unittest import mock


from operations_center.entrypoints.board_worker._subprocess import (
    MINIMAL_ENV_ALLOWLIST,
    build_allowlist_env,
)


class TestEnvAllowlistDropsSecrets:
    """The minimization drops secrets the worker does not need."""

    def test_static_base_is_only_safe_keys(self) -> None:
        assert set(MINIMAL_ENV_ALLOWLIST.keys()) == {"PATH", "LANG", "LC_ALL", "CI"}

    def test_returns_dict_with_pinned_pythonpath(self, tmp_path: Path) -> None:
        env = build_allowlist_env(tmp_path)
        assert isinstance(env, dict)
        assert env["PYTHONPATH"] == str(tmp_path / "src")

    def test_drops_unrelated_secrets(self, tmp_path: Path) -> None:
        """Plane token, AWS creds, sibling git token, and unknown vars are dropped."""
        with mock.patch.dict(
            "os.environ",
            {
                "PLANE_API_TOKEN": "pk_xyz",
                "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
                "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
                "GITHUB_TOKEN": "ghp_secret",  # not forwarded unless it is the active token
                "CUSTOM_SECRET": "nope",
                "PATH": "/custom/path",
            },
            clear=True,
        ):
            env = build_allowlist_env(tmp_path)
            for leaked in (
                "PLANE_API_TOKEN",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "GITHUB_TOKEN",
                "CUSTOM_SECRET",
            ):
                assert leaked not in env
            # PATH is pinned, never inherited
            assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    def test_deny_set_wins_over_explicit_passthrough(self, tmp_path: Path) -> None:
        """A denied secret is never forwarded even if a caller lists it."""
        with mock.patch.dict("os.environ", {"PLANE_API_TOKEN": "pk"}, clear=True):
            env = build_allowlist_env(tmp_path, passthrough=("PLANE_API_TOKEN",))
            assert "PLANE_API_TOKEN" not in env

    def test_github_actions_default_and_passthrough(self, tmp_path: Path) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            assert build_allowlist_env(tmp_path)["GITHUB_ACTIONS"] == "false"
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=True):
            assert build_allowlist_env(tmp_path)["GITHUB_ACTIONS"] == "true"


class TestEnvAllowlistPreservesFunction:
    """The minimization preserves what the worker needs — the §0.1 invariant.

    These are the assertions that would have caught the original strip-everything
    bug: had model creds / HOME been dropped, the fleet could not reach a model or
    run its toolchain, a hard halt.
    """

    def test_preserves_home_and_cache_dirs(self, tmp_path: Path) -> None:
        with mock.patch.dict(
            "os.environ",
            {"HOME": "/home/dev", "XDG_CACHE_HOME": "/home/dev/.cache"},
            clear=True,
        ):
            env = build_allowlist_env(tmp_path)
            assert env["HOME"] == "/home/dev"
            assert env["XDG_CACHE_HOME"] == "/home/dev/.cache"

    def test_preserves_model_creds(self, tmp_path: Path) -> None:
        """Model access must survive or the worker cannot run/fix (self-healing)."""
        with mock.patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "sk-local",
                "ANTHROPIC_API_KEY": "sk-ant",
                "OLLAMA_HOST": "http://localhost:11434",
            },
            clear=True,
        ):
            env = build_allowlist_env(tmp_path)
            assert env["OPENAI_API_KEY"] == "sk-local"
            assert env["ANTHROPIC_API_KEY"] == "sk-ant"
            assert env["OLLAMA_HOST"] == "http://localhost:11434"

    def test_forwards_active_repo_git_token_only(self, tmp_path: Path) -> None:
        """The active repo's git token is forwarded so push works; others are not."""
        with mock.patch.dict(
            "os.environ",
            {"REPO_A_TOKEN": "ghp_a", "REPO_B_TOKEN": "ghp_b"},
            clear=True,
        ):
            env = build_allowlist_env(tmp_path, passthrough=("REPO_A_TOKEN",))
            assert env["REPO_A_TOKEN"] == "ghp_a"  # active repo's token: forwarded
            assert "REPO_B_TOKEN" not in env  # sibling repo's token: dropped

    def test_absent_passthrough_vars_are_not_invented(self, tmp_path: Path) -> None:
        """Only vars actually present in the parent env are forwarded."""
        with mock.patch.dict("os.environ", {}, clear=True):
            env = build_allowlist_env(tmp_path, passthrough=("SOME_TOKEN",))
            assert "SOME_TOKEN" not in env
            assert "HOME" not in env  # not present → not added
