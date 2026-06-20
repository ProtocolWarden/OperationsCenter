# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the bwrap process sandbox (SBX Phase 2).

Covers the exit-gate properties (HARNESS_TRUST_HARDENING.md §3 Phase 2):
  - /proc/<parent>/environ unreadable in-sandbox (--unshare-pid + fresh /proc),
  - ~/.ssh (and other secret HOME dirs) never bound,
  - fail-open: missing bwrap / disabled / bad workspace -> command unchanged.

The two "real bwrap" tests actually run the sandbox and are skipped where bwrap
is unavailable (CI containers); the unit tests pin the argv contract offline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from operations_center.entrypoints.board_worker import sandbox as sbx
from operations_center.entrypoints.board_worker.sandbox import (
    build_sandbox_argv,
    bwrap_available,
    maybe_sandbox,
)

_HAS_BWRAP = shutil.which("bwrap") is not None


def _env(tmp_path: Path) -> dict:
    return {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "sk-x"}


class TestArgvContract:
    def test_unshare_pid_and_fresh_proc(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_sandbox_argv(["echo", "hi"], oc_root=tmp_path, rw_root=ws, env=_env(tmp_path))
        assert "--unshare-pid" in argv
        # fresh /proc mounted in the new PID namespace
        assert argv[argv.index("--proc") + 1] == "/proc"
        assert "--die-with-parent" in argv

    def test_clearenv_and_explicit_setenv(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_sandbox_argv(["x"], oc_root=tmp_path, rw_root=ws, env=_env(tmp_path))
        assert "--clearenv" in argv
        joined = " ".join(argv)
        assert "--setenv OPENAI_API_KEY sk-x" in joined  # model cred passed explicitly
        # HOME is re-pointed at the sandbox tmpfs home, not the real one
        assert argv[-3:-1] != ["--setenv", "HOME"] or True
        assert "--setenv HOME /sandbox-home" in joined

    def test_secret_home_dirs_never_bound(self, tmp_path: Path):
        # Simulate a real HOME containing secrets.
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        (home / ".aws").mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_sandbox_argv(["x"], oc_root=tmp_path, rw_root=ws, env={"HOME": str(home)})
        joined = " ".join(argv)
        assert "/.ssh" not in joined
        assert "/.aws" not in joined
        assert "/.gnupg" not in joined

    def test_workspace_is_the_only_writable_bind(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_sandbox_argv(["x"], oc_root=tmp_path, rw_root=ws, env=_env(tmp_path))
        # exactly one --bind (rw), and it is the workspace; everything else ro
        rw = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
        assert rw == [str(ws)]

    def test_secret_home_dir_bind_is_filtered(self, tmp_path: Path):
        # A toolchain path that resolves under a credential dir is dropped.
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        argv = build_sandbox_argv(
            ["x"],
            oc_root=tmp_path,
            rw_root=tmp_path,
            env={"HOME": str(home)},
            extra_ro_binds=[str(home / ".ssh")],
        )
        assert str(home / ".ssh") not in " ".join(argv)

    def test_inner_command_is_appended_last(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_sandbox_argv(
            ["python", "-m", "x"], oc_root=tmp_path, rw_root=ws, env=_env(tmp_path)
        )
        assert argv[-3:] == ["python", "-m", "x"]


class TestFailOpen:
    def test_disabled_returns_unchanged(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        cmd = ["python", "-m", "execute"]
        assert (
            maybe_sandbox(cmd, oc_root=tmp_path, rw_root=ws, env=_env(tmp_path), enabled=False)
            == cmd
        )

    def test_missing_workspace_returns_unchanged(self, tmp_path: Path):
        cmd = ["python"]
        out = maybe_sandbox(cmd, oc_root=tmp_path, rw_root=tmp_path / "nope", env={}, enabled=True)
        assert out == cmd

    def test_no_bwrap_returns_unchanged(self, tmp_path: Path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(
            "operations_center.entrypoints.board_worker.sandbox.bwrap_available", lambda: False
        )
        cmd = ["python"]
        assert maybe_sandbox(cmd, oc_root=tmp_path, rw_root=ws, env={}, enabled=True) == cmd


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap not installed")
class TestRealBwrapExitGate:
    def test_parent_environ_unreadable_in_sandbox(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        # Try to read PID 1's environ from inside; with --unshare-pid + fresh
        # /proc there is no parent to read, so this must fail/empty.
        oc_root = Path(__file__).resolve().parents[4]
        argv = build_sandbox_argv(
            ["sh", "-c", "cat /proc/1/environ 2>/dev/null | tr -d '\\0'; echo DONE"],
            oc_root=oc_root,
            rw_root=ws,
            env={"HOME": str(tmp_path / "home"), "SECRET_SENTINEL": "leak-me"},
        )
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        assert "leak-me" not in out.stdout  # the parent's secret env did not leak
        assert out.stdout.strip().endswith("DONE")

    def test_ssh_dir_unreadable_in_sandbox(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        oc_root = Path(__file__).resolve().parents[4]
        argv = build_sandbox_argv(
            ["sh", "-c", "ls ~/.ssh 2>&1; echo DONE"],
            oc_root=oc_root,
            rw_root=ws,
            env={"HOME": "/home/dev"},  # real home with a real ~/.ssh on this host
        )
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        # ~/.ssh resolves to the tmpfs sandbox home where it does not exist
        assert "id_" not in out.stdout and "authorized_keys" not in out.stdout
        assert "DONE" in out.stdout


def test_bwrap_available_matches_shutil():
    assert bwrap_available() == (shutil.which("bwrap") is not None)


class TestEgressProxyWiring:
    """SBX Phase 3 (D-SBX-2): HTTPS_PROXY wiring into the sandbox, fail-open."""

    def test_proxy_env_sets_both_cases_and_localhost_bypass(self):
        out = sbx._proxy_env("http://127.0.0.1:8889")
        assert out["HTTPS_PROXY"] == "http://127.0.0.1:8889"
        assert out["https_proxy"] == "http://127.0.0.1:8889"
        assert out["HTTP_PROXY"] == "http://127.0.0.1:8889"
        # localhost (ollama floor + key-proxy) must bypass the egress proxy
        assert "127.0.0.1" in out["NO_PROXY"]
        assert "localhost" in out["NO_PROXY"]
        assert out["NO_PROXY"] == out["no_proxy"]

    def test_proxy_env_preserves_existing_no_proxy_without_dupes(self):
        out = sbx._proxy_env("http://127.0.0.1:8889", existing_no_proxy="example.com,127.0.0.1")
        parts = out["NO_PROXY"].split(",")
        assert "example.com" in parts
        # 127.0.0.1 came from both the bypass default and the caller — dedup'd
        assert parts.count("127.0.0.1") == 1

    def test_resolve_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("OC_EGRESS_PROXY", raising=False)
        assert sbx._resolve_egress_proxy({}) is None

    def test_resolve_returns_none_when_unreachable(self, monkeypatch):
        # Set the flag but make the reachability probe fail -> fail-open (None).
        monkeypatch.setattr(sbx, "_proxy_reachable", lambda url, **kw: False)
        assert sbx._resolve_egress_proxy({"OC_EGRESS_PROXY": "http://127.0.0.1:8889"}) is None

    def test_resolve_returns_url_when_reachable(self, monkeypatch):
        monkeypatch.setattr(sbx, "_proxy_reachable", lambda url, **kw: True)
        assert (
            sbx._resolve_egress_proxy({"OC_EGRESS_PROXY": "http://127.0.0.1:8889"})
            == "http://127.0.0.1:8889"
        )

    def test_resolve_falls_back_to_parent_env(self, monkeypatch):
        monkeypatch.setenv("OC_EGRESS_PROXY", "http://127.0.0.1:9999")
        monkeypatch.setattr(sbx, "_proxy_reachable", lambda url, **kw: True)
        assert sbx._resolve_egress_proxy({}) == "http://127.0.0.1:9999"

    def test_maybe_sandbox_injects_proxy_when_reachable(self, tmp_path: Path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(sbx, "bwrap_available", lambda: True)
        monkeypatch.setattr(sbx, "_proxy_reachable", lambda url, **kw: True)
        env = {"HOME": str(tmp_path / "home"), "OC_EGRESS_PROXY": "http://127.0.0.1:8889"}
        argv = maybe_sandbox(["x"], oc_root=tmp_path, rw_root=ws, env=env, enabled=True)
        joined = " ".join(argv)
        assert "--setenv HTTPS_PROXY http://127.0.0.1:8889" in joined
        assert "--setenv NO_PROXY" in joined

    def test_maybe_sandbox_no_proxy_when_unreachable_fail_open(self, tmp_path: Path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(sbx, "bwrap_available", lambda: True)
        monkeypatch.setattr(sbx, "_proxy_reachable", lambda url, **kw: False)
        env = {"HOME": str(tmp_path / "home"), "OC_EGRESS_PROXY": "http://127.0.0.1:8889"}
        argv = maybe_sandbox(["x"], oc_root=tmp_path, rw_root=ws, env=env, enabled=True)
        # dead proxy => no proxy env injected => still sandboxed, direct egress
        assert "HTTPS_PROXY" not in " ".join(argv)
        assert "bwrap" in argv[0]

    def test_proxy_reachable_false_on_closed_port(self):
        # Nothing listens on this port -> reachable is False (real socket probe).
        assert sbx._proxy_reachable("http://127.0.0.1:1", timeout=0.2) is False


class TestGitHttpsTokenAuth:
    """SBX Phase 2: git@github clones work in the sandbox (no ~/.ssh) via an
    HTTPS+token rewrite injected as GIT_CONFIG_* env."""

    def test_no_token_returns_empty(self):
        assert sbx._git_auth_env({}) == {}
        assert sbx._git_auth_env({"GITHUB_TOKEN": ""}) == {}

    def test_builds_insteadof_and_extraheader_from_github_token(self):
        import base64

        out = sbx._git_auth_env({"GITHUB_TOKEN": "tok123"})
        assert out["GIT_CONFIG_COUNT"] == "2"
        assert out["GIT_CONFIG_KEY_0"] == "url.https://github.com/.insteadOf"
        assert out["GIT_CONFIG_VALUE_0"] == "git@github.com:"
        assert out["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraheader"
        expected = base64.b64encode(b"x-access-token:tok123").decode()
        assert out["GIT_CONFIG_VALUE_1"] == f"Authorization: Basic {expected}"

    def test_falls_back_to_git_token(self):
        out = sbx._git_auth_env({"GIT_TOKEN": "abc"})
        assert out.get("GIT_CONFIG_COUNT") == "2"

    def test_github_token_preferred_over_git_token(self):
        out = sbx._git_auth_env({"GITHUB_TOKEN": "primary", "GIT_TOKEN": "secondary"})
        import base64

        assert base64.b64encode(b"x-access-token:primary").decode() in out["GIT_CONFIG_VALUE_1"]

    def test_build_sandbox_argv_setenvs_git_config_when_token_present(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        env = {"HOME": str(tmp_path / "home"), "GITHUB_TOKEN": "tok"}
        argv = build_sandbox_argv(["git", "clone", "x"], oc_root=tmp_path, rw_root=ws, env=env)
        joined = " ".join(argv)
        assert "--setenv GIT_CONFIG_COUNT 2" in joined
        assert "url.https://github.com/.insteadOf" in joined

    def test_build_sandbox_argv_no_git_config_without_token(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = build_sandbox_argv(["x"], oc_root=tmp_path, rw_root=ws, env={"HOME": str(tmp_path)})
        assert "GIT_CONFIG_COUNT" not in " ".join(argv)
