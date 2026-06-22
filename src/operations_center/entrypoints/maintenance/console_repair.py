# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Self-heal: auto-repair `.console/task.md` structure on open goal/improve PRs.

The board worker rewrites `.console/task.md` during a task and sweeps it into the
goal PR. If that rewrite drops a required section heading (e.g. `## Objective`), the
Custodian `.console` structure detector fails the PR's audit — and because the
reviewer has no auto-fix path for audit findings, it escalates to a human and leaves
the PR OPEN, which stalls the ENTIRE goal lane via OPEN_PR_GATE (this is exactly how
goal/c99f3159 wedged the lane for hours).

This closes the self-heal gap: each board-unblock cycle, for every open goal/improve
PR, restore any missing required `task.md` section heading via the GitHub Contents
API. Best-effort and idempotent — a structurally-valid task.md is left untouched."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operations_center.adapters.github_pr import GitHubPRClient

_CONSOLE_TASK_PATH = ".console/task.md"
# Mirror of the Custodian `.console` budget/structure detector's required sections.
_REQUIRED_TASK_SECTIONS = ("Objective", "Overall Plan", "Current Stage")
_GOAL_PREFIXES = ("goal/", "improve/")


def missing_required_sections(task_md: str) -> list[str]:
    """Required `## Section` headings absent from *task_md* (the audit's exact rule)."""
    return [s for s in _REQUIRED_TASK_SECTIONS if f"## {s}" not in task_md]


def repair_task_md(task_md: str) -> str | None:
    """Insert any missing required section headings; return new text, or None if the
    file is already structurally valid. Inserts after the first H1 so the restored
    headings sit at the top of the body, each with a clearly-marked placeholder."""
    missing = missing_required_sections(task_md)
    if not missing:
        return None
    lines = task_md.splitlines()
    insert_at = 0
    for i, ln in enumerate(lines):
        if ln.startswith("# ") and not ln.startswith("## "):
            insert_at = i + 1
            break
    block: list[str] = []
    for section in missing:
        block += [
            "",
            f"## {section}",
            "",
            "_(section heading restored by board-unblock console-repair to satisfy the "
            ".console structure audit; the board worker's task.md rewrite dropped it)_",
        ]
    new_lines = lines[:insert_at] + block + lines[insert_at:]
    text = "\n".join(new_lines)
    return text + "\n" if task_md.endswith("\n") else text


def repair_console_structure(
    gh_client: GitHubPRClient, owner: str, repo: str, open_prs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """For each open goal/improve PR, restore a dropped required `task.md` section.

    Returns one action record per PR that was repaired (or attempted). PRs whose
    task.md is absent or already valid are silently skipped."""
    actions: list[dict[str, Any]] = []
    for pr in open_prs:
        branch = str(((pr.get("head") or {}).get("ref")) or "")
        if not branch.startswith(_GOAL_PREFIXES):
            continue
        got = gh_client.get_file_content(owner, repo, _CONSOLE_TASK_PATH, branch)
        if got is None:
            continue  # no .console/task.md on this branch — nothing to repair
        text, blob_sha = got
        repaired = repair_task_md(text)
        if repaired is None:
            continue  # structurally valid
        missing = missing_required_sections(text)
        ok = gh_client.update_file(
            owner,
            repo,
            _CONSOLE_TASK_PATH,
            new_text=repaired,
            message=(
                "fix(.console): restore required task.md section(s) "
                f"{missing} (board-unblock console-repair)"
            ),
            branch=branch,
            blob_sha=blob_sha,
        )
        actions.append(
            {
                "rule": "CONSOLE_STRUCTURE_REPAIR",
                "pr_number": pr.get("number"),
                "branch": branch,
                "missing_sections": missing,
                "repaired": ok,
            }
        )
    return actions


__all__ = [
    "missing_required_sections",
    "repair_console_structure",
    "repair_task_md",
]
