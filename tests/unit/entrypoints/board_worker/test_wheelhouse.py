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


def test_tiktoken_cache_noop_when_populated(tmp_path, monkeypatch):
    cache = tmp_path / "tk"
    cache.mkdir()
    (cache / "enc").write_text("x")
    monkeypatch.setattr(wh, "_TIKTOKEN_CACHE", cache)

    def _fail(*a, **k):
        raise AssertionError("should not repopulate a non-empty cache")

    monkeypatch.setattr(subprocess, "run", _fail)
    assert wh.ensure_tiktoken_cache("python3") == cache


def test_tiktoken_cache_populates_when_empty(tmp_path, monkeypatch):
    cache = tmp_path / "tk"
    monkeypatch.setattr(wh, "_TIKTOKEN_CACHE", cache)
    calls = []

    def _run(cmd, **k):
        calls.append(cmd)
        (cache / "enc").write_text("x")  # simulate tiktoken download
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _run)
    out = wh.ensure_tiktoken_cache("/v/bin/python")
    assert out == cache
    assert calls and "TIKTOKEN" not in str(calls[0])  # cmd is just python -c
    assert "tiktoken" in calls[0][-1]


def test_tiktoken_cache_fail_open(tmp_path, monkeypatch):
    cache = tmp_path / "tk"
    monkeypatch.setattr(wh, "_TIKTOKEN_CACHE", cache)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    assert wh.ensure_tiktoken_cache("python3") is None


def test_provision_env_empty_when_sandbox_off(monkeypatch):
    # Track A3: the sandbox is default-ON — an explicit opt-out is what skips
    # provisioning now.
    monkeypatch.setenv("OC_BWRAP_SANDBOX", "0")
    assert wh.provision_env("Repo", "/x", python_bin="python3") == {}


def test_provision_env_merges_wheelhouse_and_tiktoken(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_BWRAP_SANDBOX", "1")
    monkeypatch.setattr(wh, "ensure_wheelhouse", lambda *a, **k: tmp_path / "wh")
    monkeypatch.setattr(wh, "ensure_tiktoken_cache", lambda *a, **k: tmp_path / "tk")
    out = wh.provision_env("Repo", "/x", python_bin="/oc/.venv/bin/python")
    assert out["OC_WHEELHOUSE"] == str(tmp_path / "wh")
    assert out["TIKTOKEN_CACHE_DIR"] == str(tmp_path / "tk")
    # The wheels are tag-locked to the builder python — export it so the workspace
    # venv is created with the SAME interpreter (else cp-tag mismatch breaks install).
    assert out["OC_WHEELHOUSE_PYTHON"] == "/oc/.venv/bin/python"


def test_provision_env_no_wheelhouse_python_when_build_fails(monkeypatch, tmp_path):
    # Wheelhouse build fail-opens to None → no OC_WHEELHOUSE and no OC_WHEELHOUSE_PYTHON
    # (nothing to tag-match against); tiktoken can still wire independently.
    monkeypatch.setenv("OC_BWRAP_SANDBOX", "1")
    monkeypatch.setattr(wh, "ensure_wheelhouse", lambda *a, **k: None)
    monkeypatch.setattr(wh, "ensure_tiktoken_cache", lambda *a, **k: tmp_path / "tk")
    out = wh.provision_env("Repo", "/x", python_bin="/oc/.venv/bin/python")
    assert "OC_WHEELHOUSE" not in out
    assert "OC_WHEELHOUSE_PYTHON" not in out
