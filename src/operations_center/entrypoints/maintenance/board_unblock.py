# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Autonomous board unblocking — resolves stuck patterns without operator intervention.

The loop is the operator for all conditions handled here.  Do not add "operator action
required" notes for patterns this tool covers.  When a new stuck pattern emerges, add a
rule here rather than logging it and waiting.

Applies ten rules on every run:

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
    to it and will never self-resolve.  Tasks carrying a "pr-url:" label are SKIPPED
    — a pr-url label means the goal worker successfully opened a PR, which is actively
    monitored by pr_review_watcher; those tasks must not be demoted mid-review.

  Rule 6 — STALE_RUNNING_REQUEUE
    Tasks in "Running" state for longer than --stale-running-hours (default 2h) →
    move to Ready for AI.  Catches tasks whose executor died (OOM, SIGKILL, watcher
    restart) without updating Plane state.  The watcher startup reconciliation only
    handles tasks Running at cycle 1; tasks that become orphaned mid-session are
    covered here.  2h threshold is chosen to be > the backend timeout (1h) so legitimate
    long-running tasks are not incorrectly recovered.

  Rule 7 — GOAL_BACKLOG_PROMOTE
    Goal tasks in Backlog whose parent task (original-task-id: label) has reached a
    terminal state (Done/Cancelled) → move to Ready for AI.  Matches two patterns:
    (A) "source: autonomy" + "source: improve-suggestion" — improve board_worker sub-tasks.
    (B) "source: board_worker" + "handoff-reason: improvement_applied" — goal board_worker
        follow-on tasks created after an improvement was applied.
    No watcher promotes either pattern from Backlog to R4AI automatically.
    Skipped when memory is below the executor dispatch threshold or when
    executor-signal: SIGKILL is present.

  Rule 8 — CLEAN_BLOCKED_RETRY
    Tasks with "task-kind: goal", "task-kind: improve", or "task-kind: spec-author" in Blocked state where:
      - No "blocked-by:" label (not held by an explicit dependency gate)
      - No "executor-signal:" label (executor was never killed by a signal)
      - No "executor-exit-code:" label (executor never ran — pre-execution failure)
      - Not "self-modify: approved" (those are handled by Rule 4)
      - Not "thin-goal" (board_worker adds this when goal text is too short; needs human to enrich)
      - Blocked for at least --clean-blocked-min-minutes (default 5) minutes
    → move to Backlog for retry.
    These represent pre-execution failures (workspace preparation errors, missing
    sandbox branch, transient infra config issues) where no executor ran and the
    failure is safe to retry once the underlying infrastructure is fixed.
    The minimum age avoids re-queuing before the board_worker finishes writing labels.

  Rule 9 — SPEC_AUTHOR_BACKLOG_PROMOTE
    spec-author tasks in Backlog state with no active retry blocker → move to Ready for AI.
    Rule 8 (CLEAN_BLOCKED_RETRY) moves budget-exhausted spec-author tasks Blocked → Backlog,
    but no watcher re-promotes them to R4AI once the gate clears.  This rule closes that gap.
    Skipped when memory is below the executor dispatch threshold or when any active retry
    blocker is present (budget_exhausted, session_limit, global_rate_exceeded, etc.).

  Rule 10 — ORPHANED_IN_FLIGHT_CLEAR
    Usage-store ``execution_started`` events whose matching ``execution_finished`` was never
    written, where the task no longer exists in Plane (404) or is in a terminal state
    (Done/Cancelled) → write ``execution_finished`` to close the orphaned in-flight slot.
    Without this, a task deleted from Plane while its executor was running holds the global
    concurrency gate at ``current=1`` for up to 24 h (the stale-event cutoff), blocking every
    watcher from dispatching anything.  This rule detects and clears the deadlock within one
    board-unblock cycle.

Worker-backend cooldown gate:
    The four rules that promote a task INTO "Ready for AI" (Rule 4 SELF_MODIFY_REQUEUE,
    Rule 6 STALE_RUNNING_REQUEUE, Rule 7 GOAL_BACKLOG_PROMOTE, Rule 9 SPEC_AUTHOR_BACKLOG_PROMOTE)
    are deferred when EVERY worker backend the executor is allowed to dispatch to
    (per OPERATIONS_CENTER_ALLOWED_PROVIDERS) is in a known cooldown window.  Promoting to
    R4AI while the only usable backend is rate-limited causes the board_worker to re-claim,
    re-dispatch into a session-limited backend, fail the planner with a non-JSON "session
    limit" error, and bounce the task back to Blocked — a closed loop that churns the board
    and burns the capped execution budget every cycle until the cooldown resets.  Deferring
    lets tasks settle in Backlog; the next run after the reset promotes them normally
    (self-healing).  Demotions (Rule 2, Rule 5) and Blocked→Backlog parking (Rule 8) are NOT
    gated — they reduce queue pressure and are safe during a cooldown.

Usage:
    python -m operations_center.entrypoints.maintenance.board_unblock \\
        --config config/operations_center.local.yaml [--apply] [--stale-blocked-hours 4]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.execution.usage_store import UsageStore

_MEM_SKIP_THRESHOLD_GB = 1.7  # skip all rules below this
_MEM_R4AI_THRESHOLD_GB = 8.0  # skip Rule 4 (requeue to R4AI) below this

_TERMINAL_STATES = {"done", "cancelled", "cancelled by operator", "closed"}


def _mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except Exception:
        pass
    return float("inf")


# Provider (OPERATIONS_CENTER_ALLOWED_PROVIDERS) → worker backend the executor
# would dispatch to.  Used to decide whether a recorded worker-backend cooldown
# actually blocks dispatch: a claude_code cooldown only blocks when claude is the
# *only* allowed provider (codex_cli would otherwise absorb the work).
_PROVIDER_TO_WORKER_BACKEND = {
    "claude": "claude_code",
    "anthropic": "claude_code",
    "codex": "codex_cli",
    "openai": "codex_cli",
}


def _allowed_worker_backends() -> set[str]:
    """Return the worker backends the executor is permitted to dispatch to.

    Derived from OPERATIONS_CENTER_ALLOWED_PROVIDERS.  Defaults to the configured
    worker_backend default (``claude_code``) when the env var is unset, matching
    ExecutionControlSettings' default.
    """
    raw = os.environ.get("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "")
    backends = {
        _PROVIDER_TO_WORKER_BACKEND[p.strip().lower()]
        for p in raw.split(",")
        if p.strip().lower() in _PROVIDER_TO_WORKER_BACKEND
    }
    return backends or {"claude_code"}


# Gate-path probe timeout: bounded so a hung probe can't stall a board cycle.
_GATE_PROBE_TIMEOUT_SECONDS = 20


def _dispatch_cooldown_reason(
    usage_store: UsageStore,
    *,
    now: datetime,
    refresh: Callable[..., object] | None = None,
) -> str | None:
    """Return a skip reason when every *allowed* worker backend is cooling down.

    When the only worker backend(s) the executor may use are all in a known
    cooldown window, promoting tasks to Ready for AI is counter-productive: the
    board_worker will re-claim them, re-dispatch into a session-limited backend,
    and the planner will fail with a non-JSON "session limit" error — bouncing the
    task back to Blocked.  That closed loop wastes the capped execution budget and
    churns the board across cycles.  Returning a reason here lets the promotion
    rules defer until the cooldown resets, at which point the board self-heals.

    Cooldowns carry an *estimated* reset that is never retracted on its own, so a
    backend can read as cooling long after its limit actually lifted.  Before
    deferring, if every allowed backend looks cooling, ``refresh`` (when supplied)
    probes the live backends and clears any cooldown a probe proves stale — turning
    a would-be deadlock into a self-heal.  ``refresh`` is injected so tests stay
    offline; ``main`` wires the real probe.

    Returns ``None`` (no gating) when at least one allowed backend is free, or on
    any error reading cooldown state — board-unblock must never harden into a
    deadlock because the usage store was momentarily unreadable.
    """
    try:
        allowed = _allowed_worker_backends()
        snapshot = usage_store.current_worker_backend_cooldowns(now=now)
    except Exception:
        return None
    if (
        refresh is not None
        and allowed
        and all(snapshot.get(b, {}).get("cooling_down") for b in allowed)
    ):
        try:
            refresh(usage_store, now=now)
            snapshot = usage_store.current_worker_backend_cooldowns(now=now)
        except Exception:
            pass
    cooling: list[str] = []
    for backend in sorted(allowed):
        status = snapshot.get(backend, {})
        if not status.get("cooling_down"):
            return None  # an allowed backend is free → dispatch is viable
        reset_at = status.get("reset_at")
        cooling.append(f"{backend}{f' until {reset_at}' if reset_at else ''}")
    if not cooling:
        return None
    return (
        "all allowed worker backends cooling down (" + ", ".join(cooling) + "); "
        "Ready-for-AI promotion deferred to avoid re-dispatch into a session-limited "
        "backend (self-heals on cooldown reset)"
    )


_DEAD_REMEDIATION_LABEL = "dead-remediation"
_INVESTIGATE_LABEL = "task-kind: investigate"
_IMPROVE_LABEL = "task-kind: improve"
_GOAL_LABEL = "task-kind: goal"
_SPEC_AUTHOR_LABEL = "task-kind: spec-author"
_SELF_MODIFY_APPROVED_LABEL = "self-modify: approved"
_THIN_GOAL_LABEL = "thin-goal"
_SIGKILL_SIGNAL_PREFIX = "executor-signal:"  # value checked separately
_RETRY_COUNT_PREFIX = "retry-count:"
_BLOCKED_BY_PREFIX = "blocked-by:"
_ORIGINAL_TASK_PREFIX = "original-task-id:"
_SOURCE_AUTONOMY_LABEL = "source: autonomy"
_SOURCE_IMPROVE_SUGGESTION_LABEL = "source: improve-suggestion"
_SOURCE_BOARD_WORKER_LABEL = "source: board_worker"
_HANDOFF_IMPROVEMENT_LABEL = "handoff-reason: improvement_applied"
_PR_URL_PREFIX = "pr-url:"


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
            return label[len(prefix) :].strip()
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
    clean_blocked_min_minutes: int,
    mem_available_gb: float,
    cooldown_skip_reason: str | None = None,
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
                "sigkill" in executor_signal_val.lower() and _retry_count(labels) >= 3
            )
            if is_dead or is_sigkill_exhausted:
                reason = (
                    "labelled dead-remediation"
                    if is_dead
                    else "≥3 SIGKILL retries with no safe path"
                )
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "DEAD_REMEDIATION_CANCEL",
                        "from_state": state,
                        "to_state": "Cancelled",
                        "reason": reason,
                    }
                )
                continue

        # Rule 2 — investigate tasks deprioritised out of R4AI
        if state_lower == "ready for ai" and _has_label(labels, _INVESTIGATE_LABEL):
            actions.append(
                {
                    "task_id": task_id,
                    "title": title,
                    "rule": "INVESTIGATE_DEPRIORITISE",
                    "from_state": state,
                    "to_state": "Backlog",
                    "reason": "task-kind:investigate has no board_worker consumer; starving R4AI slot",
                }
            )
            continue

        # Rule 3 — improve/goal tasks stuck in Blocked (no self-modify gate)
        is_workable = _has_label(labels, _IMPROVE_LABEL) or _has_label(labels, _GOAL_LABEL)
        if (
            state_lower == "blocked"
            and is_workable
            and not _has_label(labels, _SELF_MODIFY_APPROVED_LABEL)
        ):
            blocker_id = _blocker_task_id(labels)
            if blocker_id and _is_terminal(id_state.get(blocker_id, "")):
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "IMPROVE_UNBLOCK",
                        "from_state": state,
                        "to_state": "Backlog",
                        "reason": f"blocker {blocker_id} is now {id_state[blocker_id]}",
                    }
                )
                continue
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) > timedelta(hours=stale_blocked_hours):
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "IMPROVE_UNBLOCK",
                        "from_state": state,
                        "to_state": "Backlog",
                        "reason": f"stale in Blocked >{stale_blocked_hours}h with no executor progress",
                    }
                )
                continue

        # Rule 4 — self-modify:approved tasks blocked on a resolved (or absent) dependency
        # Skipped when memory is below the executor dispatch threshold — requeueing to R4AI
        # when memory is low would cause the executor to get OOM-killed on the next dispatch.
        # Also skipped when executor-signal:SIGKILL is present — SIGKILL'd tasks have a
        # systemic failure (timeout, OOM) and should not be automatically re-dispatched;
        # they require triage review before retrying (Rule 1 cancels at ≥3 retries).
        # Also skipped when executor-exit-code:0 is present WITHOUT a corresponding
        # executor-signal label — this indicates execute.main exited clean but the result
        # was unreadable (empty result.json race); re-queuing would loop forever until the
        # underlying execute.main bug is fixed.
        if state_lower == "blocked" and _has_label(labels, _SELF_MODIFY_APPROVED_LABEL):
            executor_signal_val = _label_value(labels, _SIGKILL_SIGNAL_PREFIX) or ""
            executor_exit_code_val = _label_value(labels, "executor-exit-code:") or ""
            exit_code_zero_no_signal = (
                executor_exit_code_val.strip() == "0" and not executor_signal_val
            )
            if "sigkill" in executor_signal_val.lower():
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "SELF_MODIFY_REQUEUE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": "SKIPPED — executor-signal:SIGKILL present; triage review required before requeue",
                        "skipped": True,
                    }
                )
            elif exit_code_zero_no_signal:
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "SELF_MODIFY_REQUEUE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": "SKIPPED — executor-exit-code:0 with no signal label; empty result.json pattern requires execute.main fix before retry",
                        "skipped": True,
                    }
                )
            elif mem_available_gb < _MEM_R4AI_THRESHOLD_GB:
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "SELF_MODIFY_REQUEUE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": f"SKIPPED — mem {mem_available_gb:.2f}GB < {_MEM_R4AI_THRESHOLD_GB}GB threshold",
                        "skipped": True,
                    }
                )
            elif cooldown_skip_reason:
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "SELF_MODIFY_REQUEUE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": f"SKIPPED — {cooldown_skip_reason}",
                        "skipped": True,
                    }
                )
            else:
                blocker_id = _blocker_task_id(labels)
                if blocker_id is None or _is_terminal(id_state.get(blocker_id, "")):
                    reason = (
                        "no blocking dependency; operator approval already granted"
                        if blocker_id is None
                        else f"blocker {blocker_id} is now {id_state.get(blocker_id, 'unknown')}"
                    )
                    actions.append(
                        {
                            "task_id": task_id,
                            "title": title,
                            "rule": "SELF_MODIFY_REQUEUE",
                            "from_state": state,
                            "to_state": "Ready for AI",
                            "reason": reason,
                        }
                    )

        # Rule 5 — orphaned In Review tasks (pr_review_watcher is PR-driven, not task-driven)
        # Skip tasks with a pr-url: label — those have an open PR being monitored by
        # pr_review_watcher and must not be demoted mid-review.
        if state_lower == "in review" and not _has_label_prefix(labels, _PR_URL_PREFIX):
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) > timedelta(hours=stale_blocked_hours):
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "STALE_IN_REVIEW",
                        "from_state": state,
                        "to_state": "Backlog",
                        "reason": f"stale in In Review >{stale_blocked_hours}h — PR likely never created or already closed",
                    }
                )

        # Rule 6 — stale Running tasks whose executor died without updating Plane
        # Threshold must exceed the backend timeout (default 3600s = 1h) so legitimate
        # long-running tasks are not prematurely recovered.
        if state_lower == "running":
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) > timedelta(hours=stale_running_hours):
                if cooldown_skip_reason:
                    actions.append(
                        {
                            "task_id": task_id,
                            "title": title,
                            "rule": "STALE_RUNNING_REQUEUE",
                            "from_state": state,
                            "to_state": "Ready for AI",
                            "reason": f"SKIPPED — {cooldown_skip_reason}",
                            "skipped": True,
                        }
                    )
                else:
                    actions.append(
                        {
                            "task_id": task_id,
                            "title": title,
                            "rule": "STALE_RUNNING_REQUEUE",
                            "from_state": state,
                            "to_state": "Ready for AI",
                            "reason": (
                                f"stale in Running >{stale_running_hours}h — executor likely died "
                                f"(OOM/SIGKILL/watcher restart) without updating Plane state"
                            ),
                        }
                    )

        # Rule 7 — goal tasks whose parent task is terminal (patterns A and B).
        _is_improve_suggestion = _has_label(labels, _SOURCE_AUTONOMY_LABEL) and _has_label(
            labels, _SOURCE_IMPROVE_SUGGESTION_LABEL
        )
        _is_improvement_applied = _has_label(labels, _SOURCE_BOARD_WORKER_LABEL) and _has_label(
            labels, _HANDOFF_IMPROVEMENT_LABEL
        )
        if (
            state_lower == "backlog"
            and _has_label(labels, _GOAL_LABEL)
            and (_is_improve_suggestion or _is_improvement_applied)
            and not _has_label(labels, _THIN_GOAL_LABEL)
            and not _has_label_prefix(labels, _SIGKILL_SIGNAL_PREFIX)
            and mem_available_gb >= _MEM_R4AI_THRESHOLD_GB
        ):
            parent_id = _label_value(labels, _ORIGINAL_TASK_PREFIX)
            if parent_id:
                parent_state = id_state.get(parent_id, "")
                if _is_terminal(parent_state) and cooldown_skip_reason:
                    actions.append(
                        {
                            "task_id": task_id,
                            "title": title,
                            "rule": "GOAL_BACKLOG_PROMOTE",
                            "from_state": state,
                            "to_state": "Ready for AI",
                            "reason": f"SKIPPED — {cooldown_skip_reason}",
                            "skipped": True,
                        }
                    )
                elif _is_terminal(parent_state):
                    actions.append(
                        {
                            "task_id": task_id,
                            "title": title,
                            "rule": "GOAL_BACKLOG_PROMOTE",
                            "from_state": state,
                            "to_state": "Ready for AI",
                            "reason": (
                                f"parent improve task {parent_id} is {parent_state}; "
                                "promote for goal board_worker dispatch"
                            ),
                        }
                    )

        # Rule 8 — clean-blocked tasks where no executor ran (pre-execution failure)
        # Workspace preparation failures, missing sandbox branch, transient infra errors.
        # These tasks have no executor-signal, no executor-exit-code, and no blocked-by
        # label because the executor never launched.  They are safe to retry once the
        # underlying infrastructure is fixed.  Min age avoids racing with label writes.
        is_clean_blocked = (
            state_lower == "blocked"
            and (
                _has_label(labels, _IMPROVE_LABEL)
                or _has_label(labels, _GOAL_LABEL)
                or _has_label(labels, _SPEC_AUTHOR_LABEL)
            )
            and not _has_label(labels, _SELF_MODIFY_APPROVED_LABEL)
            and not _has_label(labels, _THIN_GOAL_LABEL)
            and not _has_label_prefix(labels, _SIGKILL_SIGNAL_PREFIX)
            and not _has_label_prefix(labels, "executor-exit-code:")
            and not _has_label_prefix(labels, _BLOCKED_BY_PREFIX)
        )
        if is_clean_blocked:
            updated_at = _parse_updated_at(issue)
            if updated_at and (now - updated_at) >= timedelta(minutes=clean_blocked_min_minutes):
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "CLEAN_BLOCKED_RETRY",
                        "from_state": state,
                        "to_state": "Backlog",
                        "reason": (
                            f"no executor-signal/exit-code/blocked-by labels — pre-execution failure "
                            f"(workspace prep or infra config); safe to retry after "
                            f"{clean_blocked_min_minutes}m min age"
                        ),
                    }
                )

        # Rule 9 — spec-author Backlog promotion.
        # Rule 8 moves budget-exhausted spec-author tasks Blocked → Backlog, but no watcher
        # re-promotes them to R4AI once the gate clears.  This rule closes that gap.
        if (
            state_lower == "backlog"
            and _has_label(labels, _SPEC_AUTHOR_LABEL)
            and mem_available_gb >= _MEM_R4AI_THRESHOLD_GB
        ):
            if cooldown_skip_reason:
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "SPEC_AUTHOR_BACKLOG_PROMOTE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": f"SKIPPED — {cooldown_skip_reason}",
                        "skipped": True,
                    }
                )
            else:
                actions.append(
                    {
                        "task_id": task_id,
                        "title": title,
                        "rule": "SPEC_AUTHOR_BACKLOG_PROMOTE",
                        "from_state": state,
                        "to_state": "Ready for AI",
                        "reason": "spec-author task in Backlog; promoting for board_worker dispatch",
                    }
                )

    return actions


def _clear_orphaned_in_flight_events(
    client: PlaneClient,
    usage_store: UsageStore,
    *,
    now: datetime,
    apply: bool,
) -> list[dict[str, Any]]:
    """Rule 10 — clear orphaned execution_started events that will never be closed.

    Reads the usage store for ``execution_started`` events without a matching
    ``execution_finished``.  For each in-flight (backend, task_id) pair, fetches
    the task from Plane.  If the task is 404 (deleted) or in a terminal state
    (Done/Cancelled), the slot is leaked and will block dispatch until the 24 h
    stale-event cutoff expires.  This rule writes ``execution_finished`` immediately
    to release the slot.
    """
    data = usage_store.load()
    events = data.get("events", [])
    cutoff = now - timedelta(hours=24)

    in_flight: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        ts_raw = e.get("timestamp")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            continue
        tid = e.get("task_id")
        backend = e.get("backend") or ""
        if not isinstance(tid, str):
            continue
        kind = e.get("kind")
        key = (backend, tid)
        if kind == "execution_started":
            in_flight[key] = e
        elif kind == "execution_finished":
            in_flight.pop(key, None)

    cleared: list[dict[str, Any]] = []
    for (backend, task_id), start_event in in_flight.items():
        try:
            issue = client.fetch_issue(task_id)
            task_state = _state_name(issue)
            is_orphaned = _is_terminal(task_state)
            reason = f"task {task_id} is in terminal state {task_state!r}"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                is_orphaned = True
                reason = f"task {task_id} not found in Plane (404 — deleted or never created)"
            else:
                continue
        except Exception:
            continue

        if not is_orphaned:
            continue

        entry: dict[str, Any] = {
            "task_id": task_id,
            "backend": backend,
            "started_at": start_event.get("timestamp"),
            "rule": "ORPHANED_IN_FLIGHT_CLEAR",
            "reason": reason,
        }
        if apply:
            try:
                usage_store.record_execution_finished(
                    task_id=task_id,
                    backend=backend,
                    now=now,
                )
                entry["action"] = "applied"
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
        else:
            entry["action"] = "would_apply"
        cleared.append(entry)

    return cleared


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous board unblocking")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="apply transitions (default: dry-run)")
    parser.add_argument(
        "--stale-blocked-hours",
        type=int,
        default=4,
        help="hours after which an improve task in Blocked is considered stale",
    )
    parser.add_argument(
        "--stale-running-hours",
        type=int,
        default=2,
        help="hours after which a Running task is considered orphaned (must exceed backend timeout)",
    )
    parser.add_argument(
        "--clean-blocked-min-minutes",
        type=int,
        default=5,
        help="minimum minutes a task must be Blocked before Rule 8 re-queues it (avoids label-write race)",
    )
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
        print(
            json.dumps(
                {
                    "skipped": True,
                    "reason": f"mem_available {mem_gb:.2f}GB < {_MEM_SKIP_THRESHOLD_GB}GB threshold — pre-OOM, skip all rules",
                },
                ensure_ascii=False,
            )
        )
        return 0

    try:
        issues = client.list_issues()
    except Exception as exc:
        client.close()
        print(json.dumps({"error": f"plane_fetch_failed: {exc}"}, ensure_ascii=False))
        return 1

    now = datetime.now(UTC)
    try:
        from operations_center.backends.worker_backend_probe import refresh_cooldowns

        # Bound the gate-path probe: a successful probe returns in seconds, but a
        # hung one must not stall the board cycle. Tighter than the standalone
        # CLI/cron default.
        cooldown_skip_reason = _dispatch_cooldown_reason(
            UsageStore(),
            now=now,
            refresh=lambda store, *, now: refresh_cooldowns(
                store, now=now, timeout=_GATE_PROBE_TIMEOUT_SECONDS
            ),
        )
    except Exception:
        cooldown_skip_reason = None
    actions = _apply_rules(
        issues,
        now=now,
        stale_blocked_hours=args.stale_blocked_hours,
        stale_running_hours=args.stale_running_hours,
        clean_blocked_min_minutes=args.clean_blocked_min_minutes,
        mem_available_gb=mem_gb,
        cooldown_skip_reason=cooldown_skip_reason,
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

    # Rule 10 — clear orphaned in_flight events after board-state actions are applied
    usage_store = UsageStore()
    orphan_actions = _clear_orphaned_in_flight_events(
        client, usage_store, now=now, apply=args.apply
    )
    results.extend(orphan_actions)

    client.close()
    print(
        json.dumps(
            {
                "scanned_at": now.isoformat(),
                "mem_available_gb": round(mem_gb, 2),
                "apply": args.apply,
                "cooldown_skip_reason": cooldown_skip_reason,
                "actions": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
