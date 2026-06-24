# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Per-task ExecutionRequest constraints are now load-bearing (wire-all stage 1).

`allowed_paths` (write-scope allowlist) and `max_changed_files` were populated on
the request but ignored by the live commit gate. These exercise the wiring:
honored when the proposal sets them, no-op (current behavior) when unset/empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from operations_center.contracts.execution import ExecutionRequest
from operations_center.execution import workspace as ws_mod
from operations_center.execution.workspace import WorkspaceManager


def _make_request(ws: Path, **overrides) -> ExecutionRequest:
    data = dict(
        proposal_id="p",
        decision_id="d",
        goal_text="g",
        repo_key="acme/widget",
        clone_url="https://github.com/acme/widget.git",
        base_branch="main",
        task_branch="goal/x",
        workspace_path=ws,
    )
    data.update(overrides)
    return ExecutionRequest(**data)


def _completed(stdout: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _git_ws(tmp_path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    return ws


# ── max_changed_files ─────────────────────────────────────────────────────────


def _oversized_run(name_only: str, shortstat: str):
    def run(cmd, *a, **k):
        if cmd[:4] == ["git", "diff", "--cached", "--shortstat"]:
            return _completed(shortstat)
        if cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            return _completed(name_only)
        return _completed()

    return run


def test_diff_oversized_honors_per_task_max_changed_files(tmp_path):
    ws = _git_ws(tmp_path)
    mgr = WorkspaceManager(max_files=50, max_lines=2000)  # generous global caps
    run = _oversized_run("a.py\nb.py\nc.py\n", " 3 files changed, 4 insertions(+)")
    with mock.patch.object(ws_mod.subprocess, "run", side_effect=run):
        # per-task cap of 2 < 3 changed -> oversized despite the generous global cap
        out = mgr._diff_oversized(ws, _make_request(ws, max_changed_files=2))
        assert out is not None and out[0] == 3
        # unset (None) -> rides the global cap -> not oversized (current behavior)
        assert mgr._diff_oversized(ws, _make_request(ws)) is None


def test_diff_oversized_per_task_cap_only_tightens(tmp_path):
    ws = _git_ws(tmp_path)
    mgr = WorkspaceManager(max_files=2, max_lines=2000)  # tight global cap
    run = _oversized_run("a.py\nb.py\nc.py\n", " 3 files changed, 1 insertion(+)")
    with mock.patch.object(ws_mod.subprocess, "run", side_effect=run):
        # a per-task cap of 100 cannot LOOSEN the global cap of 2 -> still oversized
        out = mgr._diff_oversized(ws, _make_request(ws, max_changed_files=100))
        assert out is not None and out[0] == 3


# ── allowed_paths ─────────────────────────────────────────────────────────────


def _validate_run(diff_text: str, name_only: str):
    def run(cmd, *a, **k):
        if cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            return _completed(name_only)
        if cmd[:3] == ["git", "diff", "--cached"]:
            return _completed(diff_text)
        return _completed()

    return run


def _validate(mgr, ws, req, diff_text, name_only):
    with (
        mock.patch.object(
            mgr._patch_applier,
            "validate",
            return_value=SimpleNamespace(success=True, blocked_paths=None, reason=None),
        ),
        mock.patch.object(
            ws_mod.subprocess, "run", side_effect=_validate_run(diff_text, name_only)
        ),
    ):
        return mgr._validate_patch_before_commit(ws, req)


def test_validate_rejects_path_outside_allowlist(tmp_path):
    ws = _git_ws(tmp_path)
    mgr = WorkspaceManager()
    ok, msg = _validate(
        mgr,
        ws,
        _make_request(ws, allowed_paths=["docs/specs/"]),
        "diff --git a/src/x.py b/src/x.py\n+code\n",
        "src/x.py\n",
    )
    assert ok is False
    assert "outside the task allowlist" in msg and "src/x.py" in msg


def test_validate_allows_path_within_allowlist(tmp_path):
    ws = _git_ws(tmp_path)
    mgr = WorkspaceManager()
    ok, msg = _validate(
        mgr,
        ws,
        _make_request(ws, allowed_paths=["docs/specs/"]),
        "diff --git a/docs/specs/y.md b/docs/specs/y.md\n+spec\n",
        "docs/specs/y.md\n",
    )
    assert ok is True and msg is None


def test_validate_empty_allowlist_skips_enforcement(tmp_path):
    ws = _git_ws(tmp_path)
    mgr = WorkspaceManager()
    # allowed_paths defaults [] -> no restriction (current behavior preserved)
    ok, msg = _validate(
        mgr,
        ws,
        _make_request(ws),
        "diff --git a/anything.py b/anything.py\n+x\n",
        "anything.py\n",
    )
    assert ok is True and msg is None
