# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for LedgerMaintainTask (ledger consolidation loop, controller side)."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from operations_center.maintenance import MaintenanceContext, MaintenanceTask
from operations_center.maintenance.ledger_maintain import (
    DEFAULT_INTERVAL_SECONDS,
    LedgerMaintainTask,
    resolve_repos_root,
)


def _ctx() -> MaintenanceContext:
    return MaintenanceContext(cycle_id=str(uuid.uuid4()), now=datetime.now(UTC))


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _recorder(responses: dict[str, _FakeProc]):
    """Return a fake subprocess.run that dispatches on the cl subcommand."""
    calls: list[list[str]] = []

    def run(cmd, capture_output=True, text=True, timeout=None):  # noqa: ARG001
        calls.append(cmd)
        sub = cmd[2] if len(cmd) > 2 else ""  # cl ledger <sub>
        return responses.get(sub, _FakeProc(0, ""))

    return run, calls


def _settings(repos=None):
    # LedgerMaintainTask only reads settings.repos — a lightweight fake suffices
    # (same approach as test_spec_hygiene_task's SimpleNamespace settings).
    return SimpleNamespace(repos=repos or {})


def test_satisfies_maintenance_task_protocol():
    task = LedgerMaintainTask(_settings())
    assert isinstance(task, MaintenanceTask)
    assert task.name == "ledger_maintain"
    assert task.enabled is True
    assert task.interval_seconds == DEFAULT_INTERVAL_SECONDS


def test_run_once_invokes_promote_then_observe():
    run, calls = _recorder(
        {
            "promote": _FakeProc(0, "cl ledger promote: nothing to promote\n"),
            "observe": _FakeProc(0, "cl ledger observe: no recurring unjudged signals\n"),
        }
    )
    task = LedgerMaintainTask(_settings(), repos_root=Path("/repos"), runner=run)
    result = task.run_once(_ctx())
    assert result.status == "ok"
    # promote runs before observe, with the repos-root passed through
    assert calls[0] == ["cl", "ledger", "promote", "--repos-root", "/repos"]
    assert calls[1] == ["cl", "ledger", "observe"]


def test_run_once_counts_promotions_and_recurrences():
    run, _ = _recorder(
        {
            "promote": _FakeProc(0, "cl ledger promote: promoted 2 recurrence(s):\n  ✓ a\n  ✓ b\n"),
            "observe": _FakeProc(0, "recurring:\n  x4  sig  (latest 2026-06-20)\n  x3  s2\n"),
        }
    )
    task = LedgerMaintainTask(_settings(), repos_root=Path("/repos"), runner=run)
    details = task.run_once(_ctx()).details
    assert details["promoted"] == 2
    assert details["recurring"] == 2
    assert details["regressed"] is False


def test_run_once_flags_regression_without_failing():
    # promote exit 1 == an encoded check rotted. Surfaced in details, NOT a
    # task failure (the registry would otherwise mark the whole task failed).
    run, _ = _recorder(
        {
            "promote": _FakeProc(1, "cl ledger promote: 1 encoded check(s) REGRESSED:\n"),
            "observe": _FakeProc(0, ""),
        }
    )
    task = LedgerMaintainTask(_settings(), repos_root=Path("/repos"), runner=run)
    result = task.run_once(_ctx())
    assert result.status == "ok"
    assert result.details["regressed"] is True


def test_run_once_is_best_effort_when_cl_raises():
    def boom(cmd, **kwargs):  # noqa: ARG001
        raise FileNotFoundError("cl not found")

    task = LedgerMaintainTask(_settings(), repos_root=Path("/repos"), runner=boom)
    result = task.run_once(_ctx())
    assert result.status == "ok"  # never breaks the cycle
    assert result.details["promote_rc"] == -1


def test_resolve_repos_root_prefers_configured_local_path(tmp_path):
    repo = tmp_path / "RepoGraph"
    repo.mkdir()
    settings = _settings({"RepoGraph": SimpleNamespace(local_path=str(repo))})
    assert resolve_repos_root(settings) == tmp_path


def test_resolve_repos_root_falls_back_to_checkout_layout():
    # No configured local paths → derive from this package's location.
    root = resolve_repos_root(_settings())
    assert root.name  # a real directory name, not empty
    assert (root / "OperationsCenter").exists() or root.is_dir()


def test_default_timeout_passed_through():
    captured = {}

    def run(cmd, capture_output=True, text=True, timeout=None):  # noqa: ARG001
        captured["timeout"] = timeout
        return _FakeProc(0, "")

    LedgerMaintainTask(_settings(), repos_root=Path("/r"), runner=run).run_once(_ctx())
    assert captured["timeout"] == 30


def test_uses_real_subprocess_run_by_default():
    # Guard: the default runner is subprocess.run (best-effort live shell-out).
    task = LedgerMaintainTask(_settings(), repos_root=Path("/r"))
    assert task._run is subprocess.run
