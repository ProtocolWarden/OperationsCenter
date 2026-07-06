# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parent-side scope verification of the PUSHED branch (audit Track A8).

The executor's own scope gates (workspace.py: diff caps + the allowed_paths
allowlist check) run INSIDE the bwrap-sandboxed process — the same process the
agent's code-writing adapter runs in. A compromised or misbehaving executor
can push out-of-scope changes and still write a clean result.json; the parent
worker previously trusted those self-reported fields verbatim.

This module re-verifies OUT-OF-PROCESS, against the COMMITTED tree: the parent
fetches the pushed task branch from the remote (not the agent-writable
workspace) and diffs it against the base branch. Only when the task declared a
non-empty ``allowed_paths`` allowlist is there anything to enforce — same
semantics as the in-sandbox gate, but now on an independently observed diff.
"""

from __future__ import annotations

import logging
import subprocess

from operations_center.application.scope_policy import ChangedFilePolicyChecker

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 120


class ScopeVerificationError(RuntimeError):
    """The pushed branch could not be independently verified (fetch/diff
    failure). Fail closed: an unverifiable diff must not be treated as
    in-scope."""


def _git(repo_path: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise ScopeVerificationError(
            f"git {' '.join(args)} failed in {repo_path}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def pushed_scope_violations(
    *,
    repo_path: str,
    base_branch: str,
    task_branch: str,
    allowed_paths: list[str],
) -> list[str]:
    """Changed files on the REMOTE task branch that fall outside ``allowed_paths``.

    Fetches both refs from origin and diffs ``origin/base...origin/task`` (the
    task side of the merge base), so the verdict comes from the committed tree
    the reviewer/merge path will actually consume — not from any state the
    sandboxed executor controls. Empty ``allowed_paths`` means no allowlist was
    declared: returns [] (nothing to enforce — same fail-open-by-declaration
    semantics as the in-sandbox gate).

    Raises ScopeVerificationError when the fetch/diff itself fails — the caller
    fails the task rather than trusting an unverifiable diff.
    """
    if not allowed_paths:
        return []
    _git(repo_path, "fetch", "--quiet", "origin", base_branch, task_branch)
    out = _git(
        repo_path,
        "diff",
        "--name-only",
        f"origin/{base_branch}...origin/{task_branch}",
    )
    changed = [line.strip() for line in out.splitlines() if line.strip()]
    violations = ChangedFilePolicyChecker().find_violations(changed, list(allowed_paths))
    if violations:
        logger.error(
            'board_worker: pushed branch %s touches paths OUTSIDE the task allowlist: %s '
            '{"event": "pushed_scope_violation", "branch": "%s", "violations": %d}',
            task_branch,
            ", ".join(violations[:10]),
            task_branch,
            len(violations),
        )
    return violations


def verify_pushed_scope(
    *,
    bundle: dict,
    result: dict,
    repo_path: str,
    base_branch: str,
    task_branch: str,
) -> str | None:
    """Dispatch-facing wrapper: the failure message when the pushed branch is
    out of scope or unverifiable, else None. No allowlist declared / branch not
    pushed => nothing to enforce."""
    if not result.get("branch_pushed"):
        return None
    allowed = ((bundle.get("proposal") or {}).get("target") or {}).get("allowed_paths") or []
    if not allowed:
        return None
    try:
        violations = pushed_scope_violations(
            repo_path=repo_path,
            base_branch=base_branch,
            task_branch=task_branch,
            allowed_paths=list(allowed),
        )
    except ScopeVerificationError as exc:
        return f"pushed-branch scope verification failed: {exc}"
    if violations:
        return "pushed branch touches paths outside the task allowlist: " + ", ".join(
            violations[:20]
        )
    return None


__all__ = ["ScopeVerificationError", "pushed_scope_violations", "verify_pushed_scope"]
