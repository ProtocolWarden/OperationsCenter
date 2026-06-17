# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from operations_center.contracts.enums import ValidationStatus
from operations_center.execution import baseline_validation as bv


def _cfg(**kwargs) -> types.SimpleNamespace:
    """Build a fake repo_cfg with the validation-related attributes."""
    defaults = {
        "skip_baseline_validation": False,
        "validation_commands": ["true"],
        "validation_timeout_seconds": 300,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_none_cfg_returns_skipped():
    summary = bv.run_baseline_validation(Path("/tmp"), repo_cfg=None)
    assert summary.status is ValidationStatus.SKIPPED
    assert summary.commands_run == 0


def test_skip_flag_returns_skipped(monkeypatch):
    called = []

    def _spy(*a, **k):  # pragma: no cover - should not be invoked
        called.append(1)
        return _proc()

    monkeypatch.setattr(bv.subprocess, "run", _spy)
    summary = bv.run_baseline_validation(Path("/tmp"), repo_cfg=_cfg(skip_baseline_validation=True))
    assert summary.status is ValidationStatus.SKIPPED
    assert called == []


def test_empty_commands_returns_skipped():
    summary = bv.run_baseline_validation(Path("/tmp"), repo_cfg=_cfg(validation_commands=[]))
    assert summary.status is ValidationStatus.SKIPPED


def test_none_commands_returns_skipped():
    summary = bv.run_baseline_validation(Path("/tmp"), repo_cfg=_cfg(validation_commands=None))
    assert summary.status is ValidationStatus.SKIPPED


def test_missing_commands_attr_returns_skipped():
    cfg = types.SimpleNamespace(skip_baseline_validation=False)
    summary = bv.run_baseline_validation(Path("/tmp"), repo_cfg=cfg)
    assert summary.status is ValidationStatus.SKIPPED


def test_all_commands_pass(monkeypatch, tmp_path):
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    summary = bv.run_baseline_validation(
        tmp_path, repo_cfg=_cfg(validation_commands=["a", "b", "c"])
    )
    assert summary.status is ValidationStatus.PASSED
    assert summary.commands_run == 3
    assert summary.commands_passed == 3
    assert summary.commands_failed == 0
    assert summary.duration_ms is not None and summary.duration_ms >= 0
    # subprocess called with the expected hermetic flags + cwd.
    assert all(k["shell"] is True for _, k in calls)
    assert all(k["cwd"] == tmp_path for _, k in calls)
    assert all(k["capture_output"] is True for _, k in calls)
    assert all(k["text"] is True for _, k in calls)
    assert all(k["timeout"] == 300 for _, k in calls)


def test_first_failure_aborts_chain(monkeypatch, tmp_path):
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == "fail":
            return _proc(returncode=2, stderr="boom")
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    summary = bv.run_baseline_validation(
        tmp_path, repo_cfg=_cfg(validation_commands=["ok", "fail", "never"])
    )
    assert summary.status is ValidationStatus.FAILED
    assert summary.commands_run == 2
    assert summary.commands_passed == 1
    assert summary.commands_failed == 1
    assert "exit=2" in summary.failure_excerpt
    assert "boom" in summary.failure_excerpt
    # "never" must not run after the failure.
    assert calls == ["ok", "fail"]


def test_failure_uses_stdout_when_no_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bv.subprocess, "run", lambda *a, **k: _proc(returncode=1, stdout="out-detail")
    )
    summary = bv.run_baseline_validation(tmp_path, repo_cfg=_cfg(validation_commands=["x"]))
    assert summary.status is ValidationStatus.FAILED
    assert "out-detail" in summary.failure_excerpt


def test_failure_empty_output(monkeypatch, tmp_path):
    monkeypatch.setattr(bv.subprocess, "run", lambda *a, **k: _proc(returncode=1))
    summary = bv.run_baseline_validation(tmp_path, repo_cfg=_cfg(validation_commands=["x"]))
    assert summary.status is ValidationStatus.FAILED
    assert summary.failure_excerpt == "exit=1: "


def test_failure_excerpt_truncated_to_1000(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bv.subprocess, "run", lambda *a, **k: _proc(returncode=1, stderr="z" * 5000)
    )
    summary = bv.run_baseline_validation(tmp_path, repo_cfg=_cfg(validation_commands=["x"]))
    assert summary.status is ValidationStatus.FAILED
    assert len(summary.failure_excerpt) == 1000


def test_timeout_returns_error(monkeypatch, tmp_path):
    def _run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(bv.subprocess, "run", _run)
    summary = bv.run_baseline_validation(
        tmp_path, repo_cfg=_cfg(validation_commands=["slow"], validation_timeout_seconds=7)
    )
    assert summary.status is ValidationStatus.ERROR
    assert summary.commands_run == 1
    assert summary.commands_passed == 0
    assert summary.commands_failed == 1
    assert summary.failure_excerpt == "timeout after 7s: slow"


def test_timeout_after_some_pass(monkeypatch, tmp_path):
    def _run(cmd, **kwargs):
        if cmd == "slow":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    summary = bv.run_baseline_validation(
        tmp_path, repo_cfg=_cfg(validation_commands=["ok1", "ok2", "slow"])
    )
    assert summary.status is ValidationStatus.ERROR
    assert summary.commands_run == 3  # passed (2) + 1
    assert summary.commands_passed == 2
    assert summary.commands_failed == 1


def test_non_string_commands_skipped(monkeypatch, tmp_path):
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    summary = bv.run_baseline_validation(
        tmp_path,
        repo_cfg=_cfg(validation_commands=[123, None, "real"]),
    )
    assert summary.status is ValidationStatus.PASSED
    assert summary.commands_passed == 1
    assert calls == ["real"]


def test_blank_and_whitespace_commands_skipped(monkeypatch, tmp_path):
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    summary = bv.run_baseline_validation(
        tmp_path, repo_cfg=_cfg(validation_commands=["", "   ", "go"])
    )
    assert summary.status is ValidationStatus.PASSED
    assert summary.commands_run == 1
    assert calls == ["go"]


def test_all_commands_skipped_yields_passed_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bv.subprocess,
        "run",
        lambda *a, **k: pytest.fail("subprocess should not run for all-blank commands"),
    )
    summary = bv.run_baseline_validation(tmp_path, repo_cfg=_cfg(validation_commands=["", "  "]))
    assert summary.status is ValidationStatus.PASSED
    assert summary.commands_run == 0
    assert summary.commands_passed == 0


def test_zero_timeout_defaults_to_300(monkeypatch, tmp_path):
    seen = {}

    def _run(cmd, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    bv.run_baseline_validation(
        tmp_path,
        repo_cfg=_cfg(validation_commands=["x"], validation_timeout_seconds=0),
    )
    assert seen["timeout"] == 300


def test_none_timeout_defaults_to_300(monkeypatch, tmp_path):
    seen = {}

    def _run(cmd, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    bv.run_baseline_validation(
        tmp_path,
        repo_cfg=_cfg(validation_commands=["x"], validation_timeout_seconds=None),
    )
    assert seen["timeout"] == 300


def test_custom_timeout_honored(monkeypatch, tmp_path):
    seen = {}

    def _run(cmd, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _proc(returncode=0)

    monkeypatch.setattr(bv.subprocess, "run", _run)
    bv.run_baseline_validation(
        tmp_path,
        repo_cfg=_cfg(validation_commands=["x"], validation_timeout_seconds=42),
    )
    assert seen["timeout"] == 42
