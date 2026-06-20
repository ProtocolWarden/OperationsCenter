# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for host-side wheelhouse pre-provisioning (SBX Phase 2/3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from operations_center.entrypoints.board_worker import wheelhouse as wh


def _repo(tmp: Path, deps: str = "a==1") -> Path:
    r = tmp / "repo"
    r.mkdir(parents=True)
    (r / "pyproject.toml").write_text(f"[project]\nname='x'\ndependencies=['{deps}']\n")
    return r


def test_returns_none_without_local_path():
    assert wh.ensure_wheelhouse("Repo", None, python_bin="python3") is None


def test_returns_none_when_no_pyproject(tmp_path, monkeypatch):
    monkeypatch.setattr(wh, "_CACHE_ROOT", tmp_path / "cache")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert wh.ensure_wheelhouse("Repo", str(empty), python_bin="python3") is None


def test_fingerprint_changes_with_deps(tmp_path):
    r1 = _repo(tmp_path / "a", deps="a==1")
    r2 = _repo(tmp_path / "b", deps="a==2")
    assert wh._fingerprint(r1) != wh._fingerprint(r2)


def test_fresh_wheelhouse_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(wh, "_CACHE_ROOT", tmp_path / "cache")
    repo = _repo(tmp_path)
    whd = wh.wheelhouse_dir("Repo")
    whd.mkdir(parents=True)
    (whd / "pkg-1.0-py3-none-any.whl").write_text("")
    (whd / ".fingerprint").write_text(wh._fingerprint(repo))
    called = {"n": 0}

    def _fail(*a, **k):
        called["n"] += 1
        raise AssertionError("should not rebuild a fresh wheelhouse")

    monkeypatch.setattr(subprocess, "run", _fail)
    assert wh.ensure_wheelhouse("Repo", str(repo), python_bin="python3") == whd
    assert called["n"] == 0


def test_rebuilds_when_fingerprint_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(wh, "_CACHE_ROOT", tmp_path / "cache")
    repo = _repo(tmp_path)
    whd = wh.wheelhouse_dir("Repo")
    whd.mkdir(parents=True)
    (whd / ".fingerprint").write_text("STALE")
    calls = []

    def _run(cmd, **k):
        calls.append(cmd)
        (whd / "pkg-1.0-py3-none-any.whl").write_text("")  # simulate pip wheel output
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _run)
    out = wh.ensure_wheelhouse("Repo", str(repo), python_bin="/v/bin/python")
    assert out == whd
    assert calls and calls[0][:4] == ["/v/bin/python", "-m", "pip", "wheel"]
    assert (whd / ".fingerprint").read_text() == wh._fingerprint(repo)


def test_fail_open_on_build_error(tmp_path, monkeypatch):
    monkeypatch.setattr(wh, "_CACHE_ROOT", tmp_path / "cache")
    repo = _repo(tmp_path)

    def _boom(*a, **k):
        raise subprocess.CalledProcessError(1, "pip", stderr="no network")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert wh.ensure_wheelhouse("Repo", str(repo), python_bin="python3") is None
