# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parent-side pushed-branch scope verification (audit Track A8).

The in-sandbox scope gates are agent-bypassable; these tests pin the parent's
independent verdict over the committed (remote) tree.
"""

from __future__ import annotations

import pytest

from operations_center.entrypoints.board_worker import scope_check


def _fake_git(monkeypatch, *, diff_output: str, fail_on: str | None = None):
    calls: list[list[str]] = []

    def fake(repo_path, *args):
        calls.append(list(args))
        if fail_on and args[0] == fail_on:
            raise scope_check.ScopeVerificationError(f"git {args[0]} failed")
        if args[0] == "diff":
            return diff_output
        return ""

    monkeypatch.setattr(scope_check, "_git", fake)
    return calls


def test_empty_allowlist_enforces_nothing(monkeypatch):
    calls = _fake_git(monkeypatch, diff_output="anything.py\n")
    out = scope_check.pushed_scope_violations(
        repo_path="/r", base_branch="main", task_branch="goal/abc", allowed_paths=[]
    )
    assert out == []
    assert calls == []  # no git work when nothing is declared


def test_fetches_remote_and_diffs_committed_tree(monkeypatch):
    calls = _fake_git(monkeypatch, diff_output="src/ok/a.py\n")
    out = scope_check.pushed_scope_violations(
        repo_path="/r", base_branch="main", task_branch="goal/abc", allowed_paths=["src/ok"]
    )
    assert out == []
    assert calls[0][:2] == ["fetch", "--quiet"]
    assert "origin/main...origin/goal/abc" in calls[1]


def test_out_of_scope_files_reported(monkeypatch):
    _fake_git(monkeypatch, diff_output="src/ok/a.py\nsecrets/creds.yaml\n.github/workflows/x.yml\n")
    out = scope_check.pushed_scope_violations(
        repo_path="/r", base_branch="main", task_branch="goal/abc", allowed_paths=["src/ok"]
    )
    # ChangedFilePolicyChecker normalizes leading "./"-style segments.
    assert out == ["github/workflows/x.yml", "secrets/creds.yaml"]


def test_fetch_failure_raises_fail_closed(monkeypatch):
    _fake_git(monkeypatch, diff_output="", fail_on="fetch")
    with pytest.raises(scope_check.ScopeVerificationError):
        scope_check.pushed_scope_violations(
            repo_path="/r", base_branch="main", task_branch="goal/abc", allowed_paths=["src"]
        )


# ── verify_pushed_scope (dispatch-facing wrapper) ─────────────────────────────


def test_verify_skips_when_branch_not_pushed():
    assert (
        scope_check.verify_pushed_scope(
            bundle={"proposal": {"target": {"allowed_paths": ["src"]}}},
            result={"branch_pushed": False},
            repo_path="/r",
            base_branch="main",
            task_branch="goal/abc",
        )
        is None
    )


def test_verify_reports_violations(monkeypatch):
    _fake_git(monkeypatch, diff_output="evil.py\n")
    msg = scope_check.verify_pushed_scope(
        bundle={"proposal": {"target": {"allowed_paths": ["src"]}}},
        result={"branch_pushed": True},
        repo_path="/r",
        base_branch="main",
        task_branch="goal/abc",
    )
    assert msg is not None and "outside the task allowlist" in msg and "evil.py" in msg


def test_verify_fail_closed_on_unverifiable_diff(monkeypatch):
    _fake_git(monkeypatch, diff_output="", fail_on="fetch")
    msg = scope_check.verify_pushed_scope(
        bundle={"proposal": {"target": {"allowed_paths": ["src"]}}},
        result={"branch_pushed": True},
        repo_path="/r",
        base_branch="main",
        task_branch="goal/abc",
    )
    assert msg is not None and "verification failed" in msg
