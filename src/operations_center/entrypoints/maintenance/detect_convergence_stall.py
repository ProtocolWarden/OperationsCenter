# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Convergence stall breaker — stop a candidate family that can never converge.

A proposer ``source_family`` (e.g. ``test_visibility``) keeps observing a symptom,
proposing a fix, the fix fails *identically* (e.g. ``backend_error`` — the agent
blocked by an environment fault), the task lands Blocked, the symptom is
re-observed, and a new task is proposed. With no upper bound this loops unbounded
(the 2026-06-17 incident: one family re-proposed ~190×) — burning cycles while
nothing converges, and silently (no human is told).

This breaker closes both gaps. It groups Blocked tasks by
``(source_family, failure_category)``; when a group reaches ``--threshold``
identical failures it:

  1. **Escalates** once to the operator-interventions ledger
     (``cl ledger capture convergence-stalled "<family>|<category>|n=<count>"``),
     so the failure class *reaches a human* — capture, never auto-judge.
  2. **Suppresses** further re-proposal by recording each stalled task's
     ``dedup_key`` in the existing ``ProposalRejectionStore``, which the
     proposer's guardrail already consults (``is_rejected``) before proposing —
     so no proposer change is needed.

Read-only by default (reports the stalls); ``--apply`` performs the escalation +
suppression. Run from the watchdog loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from operations_center.config import load_settings
from operations_center.proposer.rejection_store import ProposalRejectionStore

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 2
# Only env/transport-shaped failures convey "can never converge as proposed" — a
# genuine code failure (test/lint) is the task legitimately doing its job.
_STALL_CATEGORIES = frozenset({"backend_error", "timeout"})

_FAMILY_RE = re.compile(r"source[_-]family:\s*(\S+)", re.IGNORECASE)
_DEDUP_RE = re.compile(r"(?:candidate_)?dedup_key:\s*(\S+)", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"category:\s*(\S+)", re.IGNORECASE)


@dataclass
class StalledFamily:
    """A (family, failure_category) group that has stalled past the threshold."""

    family: str
    failure_category: str
    count: int
    task_ids: list[str] = field(default_factory=list)
    dedup_keys: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)


def _label_value(labels: list, prefix: str) -> str:
    for lab in labels or []:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def normalize_task(issue: dict, comments_text: str) -> dict[str, Any]:
    """Extract (family, dedup_key, failure_category) for one Blocked task.

    ``comments_text`` is the concatenated comment bodies (where board_worker
    records the failure category); the family/dedup_key live in the description
    Provenance block (label fallback for family).
    """
    desc = str(issue.get("description") or issue.get("description_stripped") or "")
    family = ""
    m = _FAMILY_RE.search(desc)
    if m:
        family = m.group(1)
    if not family:
        family = _label_value(issue.get("labels", []), "source-family") or _label_value(
            issue.get("labels", []), "source_family"
        )
    dedup = ""
    md = _DEDUP_RE.search(desc) or _DEDUP_RE.search(comments_text)
    if md:
        dedup = md.group(1)
    # Failure category: last category mention wins (most recent attempt).
    cats = _CATEGORY_RE.findall(comments_text)
    category = cats[-1].strip().lower() if cats else ""
    return {
        "task_id": str(issue.get("id") or ""),
        "title": str(issue.get("name") or ""),
        "family": family,
        "dedup_key": dedup,
        "failure_category": category,
    }


def find_stalls(
    normalized: list[dict[str, Any]], *, threshold: int = DEFAULT_THRESHOLD
) -> list[StalledFamily]:
    """Group normalized Blocked tasks; return families stalled past ``threshold``.

    A stall is ``threshold``+ tasks sharing the same family AND the same
    env/transport failure category. Tasks with no family or a non-stall category
    are ignored (a genuine code failure is the task doing its job).
    """
    groups: dict[tuple[str, str], StalledFamily] = {}
    for t in normalized:
        family = t.get("family") or ""
        category = t.get("failure_category") or ""
        if not family or category not in _STALL_CATEGORIES:
            continue
        key = (family, category)
        g = groups.get(key)
        if g is None:
            g = StalledFamily(family=family, failure_category=category, count=0)
            groups[key] = g
        g.count += 1
        if t.get("task_id"):
            g.task_ids.append(t["task_id"])
        if t.get("dedup_key"):
            g.dedup_keys.append(t["dedup_key"])
        if t.get("title"):
            g.titles.append(t["title"])
    stalls = [g for g in groups.values() if g.count >= threshold]
    stalls.sort(key=lambda s: s.count, reverse=True)
    return stalls


def _capture_escalation(family: str, category: str, count: int) -> None:
    """Best-effort operator-ledger escalation (mirrors pr_review_watcher)."""
    try:
        subprocess.run(
            [
                "cl",
                "ledger",
                "capture",
                "convergence-stalled",
                f"{family}|{category}|n={count}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 — escalation is best-effort
        logger.debug("ledger capture failed for %s — %s", family, exc)


def apply_breaker(
    stalls: list[StalledFamily],
    *,
    store: ProposalRejectionStore | None = None,
    capture: Any = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Escalate once per family and suppress each stalled dedup_key. Returns counts."""
    store = store or ProposalRejectionStore()
    capture = capture or _capture_escalation
    now = now or datetime.now(UTC)
    escalated = 0
    suppressed = 0
    for s in stalls:
        capture(s.family, s.failure_category, s.count)
        escalated += 1
        for dedup, title in zip(s.dedup_keys, s.titles + [""] * len(s.dedup_keys)):
            if not dedup:
                continue
            if not store.is_rejected(dedup):
                store.record_rejection(
                    dedup,
                    reason=f"convergence-stalled: {s.family}/{s.failure_category} "
                    f"x{s.count} (auto-suppressed by convergence breaker)",
                    task_id=s.task_ids[0] if s.task_ids else "",
                    task_title=title,
                    now=now,
                )
                suppressed += 1
    return {"escalated": escalated, "suppressed": suppressed}


def _plane_client(settings: Any):
    from operations_center.adapters.plane import PlaneClient

    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


def scan(settings: Any, *, threshold: int = DEFAULT_THRESHOLD) -> list[StalledFamily]:
    """Fetch Blocked tasks + their comments, normalize, and find stalls."""
    plane = _plane_client(settings)
    normalized: list[dict[str, Any]] = []
    try:
        for issue in plane.list_issues():
            state = issue.get("state")
            sname = (state.get("name", "") if isinstance(state, dict) else str(state or "")).strip()
            if sname.lower() != "blocked":
                continue
            try:
                comments = plane.list_comments(str(issue["id"]))
            except Exception:  # noqa: BLE001 — a task with unreadable comments is skipped
                comments = []
            text = "\n".join(
                re.sub("<[^>]+>", "", c.get("comment_html", "") or "") for c in comments
            )
            normalized.append(normalize_task(issue, text))
    finally:
        plane.close()
    return find_stalls(normalized, threshold=threshold)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convergence stall breaker")
    parser.add_argument("--config", required=True, help="Path to operations_center.local.yaml")
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Identical Blocked failures per family to trip the breaker (default {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Escalate + suppress (default: report only)",
    )
    parser.add_argument("--json", dest="output_json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    settings = load_settings(args.config)
    stalls = scan(settings, threshold=args.threshold)
    result = apply_breaker(stalls) if args.apply else {"escalated": 0, "suppressed": 0}

    if args.output_json:
        print(
            json.dumps(
                {
                    "scanned_at": datetime.now(UTC).isoformat(),
                    "threshold": args.threshold,
                    "applied": bool(args.apply),
                    "stalls": [
                        {
                            "family": s.family,
                            "failure_category": s.failure_category,
                            "count": s.count,
                            "dedup_keys": sorted(set(s.dedup_keys)),
                        }
                        for s in stalls
                    ],
                    **result,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        if stalls:
            verb = "BROKE" if args.apply else "WOULD BREAK"
            print(f"{verb} {len(stalls)} convergence stall(s):")
            for s in stalls:
                print(
                    f"  {s.family} / {s.failure_category}: {s.count} blocked "
                    f"({len(set(s.dedup_keys))} dedup_key(s))"
                )
            if not args.apply:
                print("Re-run with --apply to escalate + suppress re-proposal.")
        else:
            print("No convergence stalls found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
