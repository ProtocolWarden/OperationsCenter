# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Reconcile merged PRs to their Plane tasks — mark a task Done on merge.

The pr_review_watcher marks a task Done only when *it* merges the PR
(`_merge_and_done`). A PR merged by any other path — a manual `gh pr merge`,
another host, or while this watcher was down — leaves its Plane task stuck in a
non-terminal state forever. The board then accretes "done-but-open" debt that
looks like a pending queue.

This reconciler closes that gap. A non-terminal task is marked Done when a
*merged* PR closes it, via either signal:

  1. **Explicit close-reference** — a merged PR's title/body says
     ``Closes <task-id>`` / ``Fixes <task-id>`` / ``Resolves <task-id>``
     naming the task's id. Robust and unambiguous; works for any merge path.
  2. **In-Review convention** — an ``In Review`` task whose description
     references a now-merged PR number (``#<n>`` / ``/<n>``), the exact link the
     watcher itself uses (`_find_plane_task_id`). Catches PRs merged out-of-band.

Read-only by default (reports proposed closures); ``--apply`` transitions the
tasks to Done with a citation comment. Scoped per-repo by the ``repo:`` label so
it never closes a task against an unrelated repo's PR.

Run (report only):
  python -m operations_center.entrypoints.maintenance.reconcile_merged_tasks \\
      --config config/operations_center.local.yaml

Apply (mark matched tasks Done):
  python -m operations_center.entrypoints.maintenance.reconcile_merged_tasks \\
      --config config/operations_center.local.yaml --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.config import load_settings

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({"done", "cancelled"})

# "Closes <id>", "Fixes: <id>", "Resolved task <id>", "Promotes <id>" — capture
# the id token (a Plane UUID, or any 6+ hex/alnum-dash run). Case-insensitive.
_CLOSE_REF_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|promote[sd]?)"
    r"\s*:?\s+(?:task\s+|plane\s+|issue\s+)?"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{8,}|[A-Za-z0-9][A-Za-z0-9-]{5,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskClosure:
    """A non-terminal task that a merged PR has closed."""

    repo_key: str
    task_id: str
    task_name: str
    pr_number: int
    via: str  # "closes-ref" | "in-review-pr-merged"


def _label_value(labels: list, prefix: str) -> str:
    for lab in labels or []:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def _state_name(issue: dict) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).strip().lower()
    return str(state or "").strip().lower()


def find_closures(
    issues: list[dict], merged_prs: list[dict], repo_key: str
) -> list[TaskClosure]:
    """Pure matcher: which non-terminal `repo_key` tasks are closed by a merge.

    ``merged_prs`` are dicts with ``number``, ``title``, ``body``. Each task is
    matched at most once; the explicit close-ref wins over the In-Review
    convention.
    """
    merged_numbers = sorted({int(pr["number"]) for pr in merged_prs if pr.get("number")})
    # token (lowercased) -> earliest merged PR number that close-referenced it
    ref_to_pr: dict[str, int] = {}
    for pr in merged_prs:
        num = pr.get("number")
        if num is None:
            continue
        text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
        for m in _CLOSE_REF_RE.finditer(text):
            ref_to_pr.setdefault(m.group(1).lower(), int(num))

    closures: list[TaskClosure] = []
    for issue in issues:
        if _state_name(issue) in _TERMINAL_STATES:
            continue
        if _label_value(issue.get("labels", []), "repo") != repo_key:
            continue
        task_id = str(issue.get("id") or "").strip()
        if not task_id:
            continue
        name = str(issue.get("name") or "").strip()

        # (1) explicit close-reference naming this task's id
        if task_id.lower() in ref_to_pr:
            closures.append(
                TaskClosure(repo_key, task_id, name, ref_to_pr[task_id.lower()], "closes-ref")
            )
            continue

        # (2) In-Review task whose description references a now-merged PR number
        if _state_name(issue) == "in review":
            desc = str(issue.get("description") or issue.get("description_stripped") or "")
            for n in merged_numbers:
                if f"#{n}" in desc or f"/{n}" in desc:
                    closures.append(
                        TaskClosure(repo_key, task_id, name, n, "in-review-pr-merged")
                    )
                    break
    return closures


def _merged_prs_recent(
    github: GitHubPRClient, clone_url: str, *, days: int, now: datetime
) -> list[dict]:
    """Closed PRs that were merged within the last ``days``."""
    try:
        owner, repo = GitHubPRClient.owner_repo_from_clone_url(clone_url)
        prs = github.list_closed_prs(owner, repo)
    except Exception as exc:  # noqa: BLE001 — network/transport; treat as no data
        logger.warning("could not list closed PRs for %s: %s", clone_url, exc)
        return []
    cutoff = now - timedelta(days=days)
    out: list[dict] = []
    for pr in prs:
        merged_at = pr.get("merged_at")
        if not merged_at:
            continue  # closed-unmerged — not a completion
        try:
            when = datetime.fromisoformat(str(merged_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= cutoff:
            out.append(pr)
    return out


def _plane_client(settings: Any):
    from operations_center.adapters.plane import PlaneClient

    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


def scan(
    settings: Any,
    *,
    days: int = 7,
    github: GitHubPRClient | None = None,
    issues: list[dict] | None = None,
    now: datetime | None = None,
) -> list[TaskClosure]:
    """Find all task closures across configured repos (read-only)."""
    now = now or datetime.now(UTC)
    if github is None:
        token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
        github = GitHubPRClient(token=token)

    if issues is None:
        plane = _plane_client(settings)
        try:
            issues = plane.list_issues()
        finally:
            plane.close()

    closures: list[TaskClosure] = []
    for repo_key, repo_cfg in settings.repos.items():
        if not getattr(repo_cfg, "clone_url", None):
            continue
        merged = _merged_prs_recent(github, repo_cfg.clone_url, days=days, now=now)
        if not merged:
            continue
        closures.extend(find_closures(issues, merged, repo_key))
    return closures


def apply_closures(settings: Any, closures: list[TaskClosure]) -> int:
    """Transition each closure's task to Done with a citation comment."""
    if not closures:
        return 0
    plane = _plane_client(settings)
    applied = 0
    try:
        for c in closures:
            try:
                plane.transition_issue(c.task_id, "Done")
                plane.comment_issue(
                    c.task_id,
                    f"Auto-closed: merged PR #{c.pr_number} in {c.repo_key} "
                    f"({c.via}). Reconciled by reconcile_merged_tasks.",
                )
                applied += 1
            except Exception as exc:  # noqa: BLE001 — one bad task shouldn't abort the rest
                logger.warning("failed to close task %s: %s", c.task_id, exc)
    finally:
        plane.close()
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile merged PRs to Plane tasks")
    parser.add_argument("--config", required=True, help="Path to operations_center.local.yaml")
    parser.add_argument(
        "--days", type=int, default=7, help="Only consider PRs merged within N days (default 7)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Mark matched tasks Done (default: report only)"
    )
    parser.add_argument("--json", dest="output_json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    settings = load_settings(args.config)
    closures = scan(settings, days=args.days)
    applied = apply_closures(settings, closures) if args.apply else 0

    if args.output_json:
        print(
            json.dumps(
                {
                    "scanned_at": datetime.now(UTC).isoformat(),
                    "days": args.days,
                    "applied": bool(args.apply),
                    "closures": [
                        {
                            "repo_key": c.repo_key,
                            "task_id": c.task_id,
                            "task_name": c.task_name,
                            "pr_number": c.pr_number,
                            "via": c.via,
                        }
                        for c in closures
                    ],
                    "applied_count": applied,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        if closures:
            verb = "CLOSED" if args.apply else "WOULD CLOSE"
            print(f"{verb} ({len(closures)}):")
            for c in closures:
                print(f"  {c.repo_key} «{c.task_name[:48]}» ← PR #{c.pr_number} ({c.via})")
            if not args.apply:
                print("Re-run with --apply to mark these Done.")
        else:
            print("No merged-but-open tasks found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
