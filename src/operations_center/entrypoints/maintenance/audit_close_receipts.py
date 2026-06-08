# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Audit closed-unmerged PRs for missing salvage receipts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.close_invariants import (
    NO_SALVAGE_PHRASE,
    close_comment_claims_preserved_work,
    has_no_salvage_justification,
)
from operations_center.config import load_settings

_RECEIPT_MARKERS = (
    "durable receipt recorded on plane task",
    "close receipt for pr #",
    "durable_head_ref:",
    "refs/pull/",
    "spec_file:",
)
_SALVAGE_MARKERS = (
    "re-queued",
    "requeued",
    "salvage",
    "work preserved",
    "spec file preserved",
)


def _comment_body(comment: dict) -> str:
    return str(comment.get("body") or "")


def _has_receipt_marker(comment: str) -> bool:
    lowered = comment.lower()
    return any(marker in lowered for marker in _RECEIPT_MARKERS)


def _looks_like_salvage(comment: str) -> bool:
    lowered = comment.lower()
    if any(marker in lowered for marker in _SALVAGE_MARKERS):
        return True
    return close_comment_claims_preserved_work(comment)


def _classify_closed_pr(pr: dict, comments: list[dict]) -> tuple[str, dict | None]:
    if pr.get("merged_at"):
        return "merged", None
    if not comments:
        return "no_comment", {
            "reason": "closed_unmerged_without_close_comment",
        }

    bodies = [_comment_body(comment) for comment in comments]
    if any(_has_receipt_marker(body) for body in bodies):
        return "receipted", None
    if any(has_no_salvage_justification(body) for body in bodies):
        return "no_salvage", None

    salvage_comments = [
        body for body in bodies if _looks_like_salvage(body) or NO_SALVAGE_PHRASE in body.lower()
    ]
    if salvage_comments:
        return "suspect", {
            "reason": "closed_unmerged_without_durable_receipt",
            "comment_excerpt": salvage_comments[-1].strip().splitlines()[0][:240],
        }
    return "no_signal", {
        "reason": "closed_unmerged_without_receipt_signal",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit closed-unmerged PRs for close receipts")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    settings = load_settings(args.config)
    token = settings.git_token()
    if not token:
        print(json.dumps({"error": "no git token configured"}, ensure_ascii=False))
        return 1

    gh = GitHubPRClient(token)
    scanned_at = datetime.now(UTC).isoformat()
    findings: list[dict] = []
    repo_summaries: list[dict] = []
    closed_unmerged_total = 0

    for repo_key, repo_cfg in (settings.repos or {}).items():
        try:
            owner, repo = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except ValueError:
            repo_summaries.append({"repo_key": repo_key, "error": "unparseable_clone_url"})
            continue
        try:
            prs = gh.list_closed_prs(owner, repo)
        except Exception as exc:
            repo_summaries.append({"repo_key": repo_key, "error": f"list_closed_failed: {exc}"})
            continue

        repo_closed_unmerged = 0
        repo_findings = 0
        for pr in prs:
            if pr.get("merged_at"):
                continue
            repo_closed_unmerged += 1
            closed_unmerged_total += 1
            pr_number = int(pr.get("number") or 0)
            try:
                comments = gh.list_pr_comments(owner, repo, pr_number)
            except Exception as exc:
                findings.append(
                    {
                        "repo_key": repo_key,
                        "repo": f"{owner}/{repo}",
                        "pr": pr_number,
                        "url": pr.get("html_url"),
                        "reason": f"comment_fetch_failed: {exc}",
                    }
                )
                repo_findings += 1
                continue
            status, detail = _classify_closed_pr(pr, comments)
            if detail is None:
                continue
            findings.append(
                {
                    "repo_key": repo_key,
                    "repo": f"{owner}/{repo}",
                    "pr": pr_number,
                    "url": pr.get("html_url"),
                    "head_ref": ((pr.get("head") or {}).get("ref") or ""),
                    **detail,
                }
            )
            repo_findings += 1

        repo_summaries.append(
            {
                "repo_key": repo_key,
                "repo": f"{owner}/{repo}",
                "closed_unmerged_count": repo_closed_unmerged,
                "finding_count": repo_findings,
            }
        )

    print(
        json.dumps(
            {
                "scanned_at": scanned_at,
                "closed_unmerged_count": closed_unmerged_total,
                "finding_count": len(findings),
                "repos": repo_summaries,
                "findings": findings,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
