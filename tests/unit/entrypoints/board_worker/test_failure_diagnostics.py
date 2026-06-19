# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for persist_failure_diagnostics.

When a dispatch fails, the executor's stdout/stderr is captured but otherwise
discarded, and team_executor persists no run artifacts — so a recurring failure
records only "N of N stages failed" and cannot be root-caused. This helper
persists the raw output to a durable per-task log and enriches the failure reason
with a pointer, so the controller and operators can investigate.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from operations_center.entrypoints.board_worker._subprocess import (
    persist_failure_diagnostics,
)


def _proc(stdout="", stderr="", returncode=1):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_writes_durable_log_and_enriches_reason(tmp_path: Path) -> None:
    result = {"failure_reason": "4 of 4 stages failed", "status": "failed"}
    proc = _proc(stdout="planning ok\n", stderr="stage 2: model refused tool call\n", returncode=1)

    path = persist_failure_diagnostics(result, tmp_path, "improve", "ea7cf92f", proc, '{"x":1}')

    assert path is not None
    assert path == tmp_path / "logs" / "local" / "failures" / "improve-ea7cf92f.log"
    body = path.read_text(encoding="utf-8")
    # raw streams + result.json are all persisted for investigation
    assert "stage 2: model refused tool call" in body
    assert "planning ok" in body
    assert '{"x":1}' in body
    assert "returncode=1" in body
    # the failure reason now points at the log + carries a tail
    assert f"[diagnostics: {path}]" in result["failure_reason"]
    assert "model refused tool call" in result["failure_reason"]
    assert result["failure_reason"].startswith("4 of 4 stages failed")


def test_falls_back_to_status_when_no_reason(tmp_path: Path) -> None:
    result = {"status": "failed"}
    path = persist_failure_diagnostics(result, tmp_path, "goal", "abc12345", _proc(stderr="boom"))
    assert path is not None
    assert "[diagnostics:" in result["failure_reason"]
    assert result["failure_reason"].startswith("failed")


def test_prefers_stderr_tail_but_uses_stdout_when_stderr_empty(tmp_path: Path) -> None:
    result = {"failure_reason": "x"}
    persist_failure_diagnostics(result, tmp_path, "goal", "id1", _proc(stdout="only-stdout-detail"))
    assert "only-stdout-detail" in result["failure_reason"]


def test_never_raises_on_bad_proc(tmp_path: Path) -> None:
    """A diagnostics-write failure must not turn a recoverable failure into a crash."""
    result = {"failure_reason": "r"}
    # proc missing stdout/stderr attrs entirely
    path = persist_failure_diagnostics(result, tmp_path, "goal", "id2", object())
    # writes what it can (empty streams) or returns None — but never raises
    assert path is None or path.exists()


def test_unwritable_root_returns_none(tmp_path: Path) -> None:
    bad_root = tmp_path / "nope"
    bad_root.write_text("i am a file, not a dir", encoding="utf-8")  # mkdir under this fails
    result = {"failure_reason": "r"}
    path = persist_failure_diagnostics(result, bad_root, "goal", "id3", _proc(stderr="x"))
    assert path is None
    assert result["failure_reason"] == "r"  # unchanged on failure
