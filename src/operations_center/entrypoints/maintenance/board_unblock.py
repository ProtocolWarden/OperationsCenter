# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Autonomous board unblocking — resolves stuck patterns without operator intervention.

The loop is the operator for all conditions handled here.  Do not add "operator action
required" notes for patterns this tool covers.  When a new stuck pattern emerges, add a
rule here rather than logging it and waiting.

Applies five rules on every run:

  Rule 1 — DEAD_REMEDIATION_CANCEL
    Tasks with label "dead-remediation" OR (executor-signal: sigkill + retry-count ≥ 3)
    that are not already in a terminal state → transition to Cancelled.

  Rule 2 — INVESTIGATE_DEPRIORITISE
    Tasks with label "task-kind: investigate" in Ready for AI → move to Backlog.
    (No board_worker consumer exists for this task-kind; they starve R4AI.)

  Rule 3 — IMPROVE_UNBLOCK
    Tasks with label "task-kind: improve" or "task-kind: goal" in Blocked state,
    WITHOUT "self-modify: approved" (those are handled by Rule 4):
      - If their blocker (blocked-by: label) is Cancelled/Done → move to Backlog.
      - OR if they have been Blocked for longer than --stale-blocked-hours (default 4h)
        with no executor progress → move to Backlog.

  Rule 4 — SELF_MODIFY_REQUEUE
    Tasks with label "self-modify: approved" in Blocked state whose blocking dependency
    (blocked-by: label) is either absent or already in a terminal state → move to
    Ready for AI.  These tasks have operator approval to proceed; keeping them Blocked
    when the dependency is gone is pure queue waste.
    Exception: tasks with "executor-signal: SIGKILL" are skipped — a SIGKILL indicates
    a systemic failure (timeout, OOM) that requires triage review before re-dispatch.
    Rule 1 cancels such tasks once retry-count reaches ≥3.

  Rule 5 — STALE_IN_REVIEW
    Tasks in "In Review" state for longer than --stale-blocked-hours (default 4h) →
    move to Backlog.  Catches tasks whose PR was never created, was closed without
    merging, or whose reviewer state was set prematurely.  The pr_review_watcher only
    processes open PRs it discovers on GitHub; orphaned In Review tasks are invisible
    to it and will never self-resolve.

  Rule 6 — STALE_RUNNING_REQUEUE
    Tasks in "Running" state for longer than --stale-running-hours (default 2h) →
    move to Ready for AI.  Catches tasks whose executor died (OOM, SIGKILL, watcher
    restart) without updating Plane state.  The watcher startup reconciliation only
    handles tasks Running at cycle 1; tasks that become orphaned mid-session are
    covered here.  2h threshold is chosen to be > the backend timeout (1h) so legitimate
    long-running tasks are not incorrectly recovered.

Usage:
    python -m operations_center.entrypoints.maintenance.board_unblock \\
        --config config/operations_center.local.yaml [--apply] [--stale-blocked-hours 4]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings

_MEM_SKIP_THRESHOLD_GB = 1.7   # skip all rules below this
_MEM_R4AI_THRESHOLD_GB = 8.0   # skip Rule 4 (requeue to R4AI) below this

_TERMINAL_STATES = {"done", "cancelled", "cancelled by operator", "closed"}


def _mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except Exception:
        pass
    return float("inf")
_DEAD_REMEDIATION_LABEL = "dead-remediation"
_INVESTIGATE_LABEL = "task-kind: investigate"
_IMPROVE_LABEL = "task-kind: improve"
_GOAL_LABEL = "task-kind: goal"
_SELF_MODIFY_APPROVED_LABEL = "self-modify: approved"
_SIGKILL_SIGNAL_PREFIX = "executor-signal:"  # value checked separately
_RETRY_COUNT_PREFIX = "retry-count:"
_BLOCKED_BY_PREFIX = "blocked-by:"


def _labels(issue: dict[str, Any]) -> list[str]:
    names = []
    for raw in issue.get("labels", []) or []:
        name = raw.get("name") if isinstance(raw, dict) else raw
        if name:
            names.append(str(name).strip())
    return names


def _state_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).strip()
    return str(state or "").strip()


def _label_value(labels: list[str], prefix: str) -> str | None:
    for label in labels:
        if label.lower().startswith(prefix.lower()):
            return label[len(prefix):].strip()
    return None


def _has_label(labels: list[str], target: str) -> bool:
    target_lower = target.lower()
    return any(label.lower() == target_lower for label in labels)


def _has_label_prefix(labels: list[str], prefix: str) -> bool:
    prefix_lower = prefix.lower()
    return any(label.lower().startswith(prefix_lower) for label in labels)


def _parse_updated_at(issue: dict[str, Any]) -> datetime | None:
    raw = issue.get("updated_at") or issue.get("created_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _retry_count(labels: list[str]) -> int:
    raw = _label_value(labels, _RETRY_COUNT_PREFIX)
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _is_terminal(state: str) -> bool:
    return state.lower() in _TERMINAL_STATES


def _blocker_task_id(labels: list[str]) -> str | None:
    return _label_value(labels, _BLOCKED_BY_PREFIX)


def _build_id_state_map(issues: list[dict[str, Any]]) -> dict[str, str]:
    return {str(issue["id"]): _state_name(issue) for issue in issues}


def _apply_rules(
    issues: list[dict[str, Any]],
    *,
    now: datetime,
    stale_blocked_hours: int,
    stale_running_hours: int,
    mem_available_gb: float,
) -> list[dict[str, Any]]:
    id_state = _build_id_state_map(issues)
    actions = []

    for issue in issues:
        task_id = str(issue["id"])
        title = str(issue.get("name") or "")
        state = _state_name(issue)
        labels = _labels(issue)
        state_lower = state.lower()

        # Rule 1 — dead-remediation cancel
        if not _is_terminal(state):
            is_dead = _has_label(labels, _DEAD_REMEDIATION_LABEL)
            executor_signal_val = _label_value(labels, _SIGKILL_SIGNAL_PREFIX) or ""
            is_sigkill_exhausted = (
                "sigkill" in executor_signal_val.lower()
                and _retry_count(labels) >= 3
            )
            if is_dead or is_sigkill_exhausted:
                reason = (
                    "labelled dead-remediation"
                    if is_dead
                    else "≥3 SIGKILL retries with no safe path"
                )
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "DEAD_REMEDIATION_CANCEL",
                    "from_state": state,
                    "to_state": "Cancelled",
                    "reason": reason,
                })
                continue

        # Rule 2 — investigate tasks deprioritised out of R4AI
        if state_lower == "ready for ai" and _has_label(labels, _INVESTIGATE_LABEL):
            actions.append({
                "task_id": task_id,
                "title": title,
                "rule": "INVESTIGATE_DEPRIORITISE",
                "from_state": state,
                "to_state": "Backlog",
                "reason": "task-kind:investigate has no board_worker consumer; starving R4AI slot",
            })
            continue

        # Rule 3 — improve/goal tasks stuck in Blocked (no self-modify gate)
        is_workable = _has_label(labels, _IMPROVE_LABEL) or _has_label(labels, _GOAL_LABEL)
        if state_lower == "blocked" and is_workable and not _has_label(labels, _SELF_MODIFY_APPROVED_LABEL):
            blocker_id = _blocker_task_id(labels)
            if blocker_id and _is_terminal(id_state.get(blocker_id, "")):
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "IMPROVE_UNBLOCK",
                    "from_state": state,
                    "to_state": "Backlog",
                    "reason": f"blocker {blocker_id} is now {id_state[blocker_id]}",
                })
                continue
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) > timedelta(hours=stale_blocked_hours):
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "IMPROVE_UNBLOCK",
                    "from_state": state,
                    "to_state": "Backlog",
                    "reason": f"stale in Blocked >{stale_blocked_hours}h with no executor progress",
                })
                continue

        # Rule 4 — self-modify:approved tasks blocked on a resolved (or absent) dependency
        # Skipped when memory is below the executor dispatch threshold — requeueing to R4AI
        # when memory is low would cause the executor to get OOM-killed on the next dispatch.
        # Also skipped when executor-signal:SIGKILL is present — SIGKILL'd tasks have a
        # systemic failure (timeout, OOM) and should not be automatically re-dispatched;
        # they require triage review before retrying (Rule 1 cancels at ≥3 retries).
        if state_lower == "blocked" and _has_label(labels, _SELF_MODIFY_APPROVED_LABEL):
            executor_signal_val = _label_value(labels, _SIGKILL_SIGNAL_PREFIX) or ""
            if "sigkill" in executor_signal_val.lower():
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "SELF_MODIFY_REQUEUE",
                    "from_state": state,
                    "to_state": "Ready for AI",
                    "reason": "SKIPPED — executor-signal:SIGKILL present; triage review required before requeue",
                    "skipped": True,
                })
            elif mem_available_gb < _MEM_R4AI_THRESHOLD_GB:
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "SELF_MODIFY_REQUEUE",
                    "from_state": state,
                    "to_state": "Ready for AI",
                    "reason": f"SKIPPED — mem {mem_available_gb:.2f}GB < {_MEM_R4AI_THRESHOLD_GB}GB threshold",
                    "skipped": True,
                })
            else:
                blocker_id = _blocker_task_id(labels)
                if blocker_id is None or _is_terminal(id_state.get(blocker_id, "")):
                    reason = (
                        "no blocking dependency; operator approval already granted"
                        if blocker_id is None
                        else f"blocker {blocker_id} is now {id_state.get(blocker_id, 'unknown')}"
                    )
                    actions.append({
                        "task_id": task_id,
                        "title": title,
                        "rule": "SELF_MODIFY_REQUEUE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": reason,
                    })

        # Rule 5 — orphaned In Review tasks (pr_review_watcher is PR-driven, not task-driven)
        if state_lower == "in review":
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) > timedelta(hours=stale_blocked_hours):
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "STALE_IN_REVIEW",
                    "from_state": state,
                    "to_state": "Backlog",
                    "reason": f"stale in In Review >{stale_blocked_hours}h — PR likely never created or already closed",
                })

        # Rule 6 — stale Running tasks whose executor died without updating Plane
        # Threshold must exceed the backend timeout (default 3600s = 1h) so legitimate
        # long-running tasks are not prematurely recovered.
        if state_lower == "running":
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) > timedelta(hours=stale_running_hours):
                actions.append({
                    "task_id": task_id,
                    "title": title,
                    "rule": "STALE_RUNNING_REQUEUE",
                    "from_state": state,
                    "to_state": "Ready for AI",
                    "reason": (
                        f"stale in Running >{stale_running_hours}h — executor likely died "
                        f"(OOM/SIGKILL/watcher restart) without updating Plane state"
                    ),
                })

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous board unblocking")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="apply transitions (default: dry-run)")
    parser.add_argument("--stale-blocked-hours", type=int, default=4,
                        help="hours after which an improve task in Blocked is considered stale")
    parser.add_argument("--stale-running-hours", type=int, default=2,
                        help="hours after which a Running task is considered orphaned (must exceed backend timeout)")
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )

    mem_gb = _mem_available_gb()
    if mem_gb < _MEM_SKIP_THRESHOLD_GB:
        client.close()
        print(json.dumps({
            "skipped": True,
            "reason": f"mem_available {mem_gb:.2f}GB < {_MEM_SKIP_THRESHOLD_GB}GB threshold — pre-OOM, skip all rules",
        }, ensure_ascii=False))
        return 0

    try:
        issues = client.list_issues()
    except Exception as exc:
        client.close()
        print(json.dumps({"error": f"plane_fetch_failed: {exc}"}, ensure_ascii=False))
        return 1

    now = datetime.now(UTC)
    actions = _apply_rules(
        issues, now=now, stale_blocked_hours=args.stale_blocked_hours,
        stale_running_hours=args.stale_running_hours, mem_available_gb=mem_gb,
    )

    results = []
    for action in actions:
        entry = {k: v for k, v in action.items()}
        if action.get("skipped"):
            results.append(entry)
            continue
        if not args.apply:
            entry["action"] = "would_apply"
        else:
            try:
                client.transition_issue(action["task_id"], action["to_state"])
                client.comment_issue(
                    action["task_id"],
                    f"Board unblock (autonomous): {action['rule']} — "
                    f"{action['reason']}. "
                    f"Transitioned {action['from_state']} → {action['to_state']}.",
                )
                entry["action"] = "applied"
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
        results.append(entry)

    client.close()
    print(json.dumps({
        "scanned_at": now.isoformat(),
        "mem_available_gb": round(mem_gb, 2),
        "apply": args.apply,
        "actions": results,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
