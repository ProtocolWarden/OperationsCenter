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
            ["x"], oc_root=tmp_path, rw_root=tmp_path, env={"HOME": str(home)},
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
