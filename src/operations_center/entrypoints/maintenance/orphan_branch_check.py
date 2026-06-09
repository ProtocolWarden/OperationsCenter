# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Orphan-branch detector — WO-4 of the 2026-06-07 flow-hygiene work order.

An orphan branch is a remote branch that:
  (a) has at least one commit ahead of the default branch, AND
  (b) has no open pull request, AND
  (c) is older than --min-age-hours (default 24 h).

These are branches whose work has been abandoned, forgotten, or silently lost.
The close-with-receipt invariant (WO-1) covers PRs that get closed; this scanner
covers branches that were never promoted to a PR in the first place.

Protected branches are never reported as orphans:
  main, master, HEAD, operations-center-testing-branch
  plus any branch whose name matches the repo's sandbox_base_branch.

Run (read-only — never mutates repos or Plane):
  python -m operations_center.entrypoints.maintenance.orphan_branch_check \\
      --config config/operations_center.local.yaml

Run and emit Plane tasks (one task per orphan):
  python -m operations_center.entrypoints.maintenance.orphan_branch_check \\
      --config config/operations_center.local.yaml --emit

Exit code: 0 always (orphans are findings, not fatal errors).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.config import load_settings

logger = logging.getLogger(__name__)

# Never flag these as orphans regardless of commits-ahead.
_ALWAYS_PROTECTED = frozenset(
    {
        "main",
        "master",
        "HEAD",
        "operations-center-testing-branch",
        "gh-pages",  # GitHub Pages deployment branch
        "prod",      # Production deployment branch
        "staging",   # Staging deployment branch
    }
)


@dataclass(frozen=True)
class OrphanBranch:
    repo_key: str
    branch: str
    commits_ahead: int
    last_commit_at: datetime
    age_hours: float


@dataclass
class RepoOrphanResult:
    repo_key: str
    local_path: Path
    orphans: list[OrphanBranch] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False


def _git(args: list[str], cwd: Path) -> tuple[str, int]:
    """Run a git command, return (stdout, returncode)."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip(), result.returncode


def _open_pr_head_branches(github_client: GitHubPRClient, clone_url: str) -> set[str]:
    """Return the set of head branch names for all open PRs in the repo."""
    try:
        owner, repo = GitHubPRClient.owner_repo_from_clone_url(clone_url)
        prs = github_client.list_open_prs(owner, repo)
        return {str(pr.get("head", {}).get("ref", "")) for pr in prs if pr.get("head", {}).get("ref")}
    except Exception as exc:
        logger.warning("could not list open PRs for %s: %s", clone_url, exc)
        return set()


def _unique_patch_commits(local_path: Path, default_branch: str, branch: str) -> int | None:
    """Return the count of branch commits whose patch is not already on default."""
    cherry_raw, rc = _git(
        ["cherry", f"origin/{default_branch}", f"origin/{branch}"],
        local_path,
    )
    if rc != 0:
        return None
    return sum(1 for line in cherry_raw.splitlines() if line.startswith("+"))


def _scan_repo(
    repo_key: str,
    local_path: Path,
    clone_url: str,
    sandbox_base_branch: str | None,
    github_client: GitHubPRClient,
    min_age_hours: float,
    default_branch: str,
) -> RepoOrphanResult:
    result = RepoOrphanResult(repo_key=repo_key, local_path=local_path)

    if not (local_path / ".git").exists():
        result.skipped = True
        return result

    # Fetch to sync remote refs (quiet; non-fatal on error).
    _, rc = _git(["fetch", "origin", "--prune", "--quiet"], local_path)
    if rc != 0:
        logger.warning("%s: git fetch failed (rc=%d); using cached refs", repo_key, rc)

    # List all remote branches.
    raw, rc = _git(
        ["branch", "-r", "--format=%(refname:short)"],
        local_path,
    )
    if rc != 0:
        result.error = f"git branch -r failed (rc={rc})"
        return result

    remote_branches = [
        b.removeprefix("origin/")
        for b in raw.splitlines()
        if b.startswith("origin/") and not b.endswith("/HEAD")
    ]

    # Branches that are never orphans.
    protected = set(_ALWAYS_PROTECTED)
    if sandbox_base_branch:
        protected.add(sandbox_base_branch)
    protected.add(default_branch)

    # Open-PR head branches — skip fetching if nothing to check.
    candidate_branches = [b for b in remote_branches if b not in protected]
    if not candidate_branches:
        return result

    open_pr_branches = _open_pr_head_branches(github_client, clone_url)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=min_age_hours)

    for branch in candidate_branches:
        if branch in open_pr_branches:
            continue

        # Commits ahead of default branch.
        ahead_raw, rc = _git(
            ["rev-list", "--count", f"origin/{default_branch}..origin/{branch}"],
            local_path,
        )
        if rc != 0:
            logger.debug("%s:%s — rev-list failed, skipping", repo_key, branch)
            continue
        try:
            commits_ahead = int(ahead_raw)
        except ValueError:
            continue
        if commits_ahead == 0:
            continue

        unique_patch_commits = _unique_patch_commits(local_path, default_branch, branch)
        if unique_patch_commits is None:
            logger.debug("%s:%s — git cherry failed, skipping", repo_key, branch)
            continue
        if unique_patch_commits == 0:
            continue

        # Last commit timestamp on the branch.
        ts_raw, rc = _git(
            ["log", "-1", "--format=%cI", f"origin/{branch}"],
            local_path,
        )
        if rc != 0 or not ts_raw:
            continue
        try:
            last_commit_at = datetime.fromisoformat(ts_raw)
            if last_commit_at.tzinfo is None:
                last_commit_at = last_commit_at.replace(tzinfo=UTC)
        except ValueError:
            continue

        if last_commit_at > cutoff:
            continue  # too recent — not yet an orphan

        age_hours = (now - last_commit_at).total_seconds() / 3600.0
        result.orphans.append(
            OrphanBranch(
                repo_key=repo_key,
                branch=branch,
                commits_ahead=unique_patch_commits,
                last_commit_at=last_commit_at,
                age_hours=age_hours,
            )
        )

    return result


def scan(settings: Any, min_age_hours: float = 24.0) -> list[RepoOrphanResult]:
    """Scan all configured repos and return orphan findings per repo."""
    token = ""
    for env_var in ("GITHUB_TOKEN", "GH_TOKEN"):
        import os

        token = os.environ.get(env_var, "")
        if token:
            break

    github_client = GitHubPRClient(token=token)
    results: list[RepoOrphanResult] = []

    for repo_key, repo_cfg in settings.repos.items():
        local_path_str = repo_cfg.local_path
        if not local_path_str:
            continue
        local_path = Path(local_path_str)

        results.append(
            _scan_repo(
                repo_key=repo_key,
                local_path=local_path,
                clone_url=repo_cfg.clone_url,
                sandbox_base_branch=repo_cfg.sandbox_base_branch,
                github_client=github_client,
                min_age_hours=min_age_hours,
                default_branch=repo_cfg.default_branch,
            )
        )

    return results


def _emit_plane_task(settings: Any, orphan: OrphanBranch) -> None:
    from operations_center.adapters.plane import PlaneClient

    plane = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    title = f"Orphan branch: {orphan.repo_key}/{orphan.branch} ({orphan.commits_ahead} commits ahead)"
    body = (
        f"Branch `{orphan.branch}` in **{orphan.repo_key}** has {orphan.commits_ahead} "
        f"commit(s) ahead of main with no open PR, last committed "
        f"{orphan.age_hours:.1f}h ago.\n\n"
        "Action: open a PR, merge the commits, or delete the branch after confirming "
        "no salvage value per the WO-1 close-with-receipt invariant."
    )
    plane.create_issue(
        name=title,
        description=body,
        label_names=["orphan-branch", f"repo:{orphan.repo_key}"],
    )
    logger.info("created Plane task for %s/%s", orphan.repo_key, orphan.branch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orphan-branch detector")
    parser.add_argument("--config", required=True, help="Path to operations_center.local.yaml")
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=24.0,
        help="Minimum branch age in hours to report (default: 24)",
    )
    parser.add_argument(
        "--emit",
        action="store_true",
        help="Create Plane tasks for each orphan found",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Emit JSON output",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    settings = load_settings(args.config)
    results = scan(settings, min_age_hours=args.min_age_hours)

    all_orphans = [o for r in results for o in r.orphans]
    errors = [r for r in results if r.error]

    if args.output_json:
        out: dict[str, Any] = {
            "scanned_at": datetime.now(UTC).isoformat(),
            "min_age_hours": args.min_age_hours,
            "orphans": [
                {
                    "repo_key": o.repo_key,
                    "branch": o.branch,
                    "commits_ahead": o.commits_ahead,
                    "last_commit_at": o.last_commit_at.isoformat(),
                    "age_hours": round(o.age_hours, 1),
                }
                for o in all_orphans
            ],
            "errors": [{"repo_key": r.repo_key, "error": r.error} for r in errors],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        if all_orphans:
            print(f"ORPHAN BRANCHES ({len(all_orphans)}):")
            for o in all_orphans:
                print(
                    f"  {o.repo_key}/{o.branch}: {o.commits_ahead} commits ahead, "
                    f"age={o.age_hours:.1f}h"
                )
        else:
            print("No orphan branches found.")
        if errors:
            print(f"\nERRORS ({len(errors)}):")
            for r in errors:
                print(f"  {r.repo_key}: {r.error}")

    if args.emit and all_orphans:
        for orphan in all_orphans:
            try:
                _emit_plane_task(settings, orphan)
            except Exception as exc:
                logger.warning("failed to emit Plane task for %s/%s: %s", orphan.repo_key, orphan.branch, exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
