# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""BoardUnblockTask — run the autonomous board-unblock engine inside the live
maintenance loop, so the controller investigates and self-heals stuck/failed
tasks every cycle with NO human in the loop (HARNESS_TRUST_HARDENING.md §0.1).

The rule engine in ``board_unblock.py`` was complete and tested but registered
nowhere — runnable only as a standalone CLI, so in practice nothing ever
unblocked the board. This task wraps that engine (the existing Plane-only Rules
1–10) and adds one GitHub-aware reconciliation that the pure rules cannot
express:

    reconcile_merged_pr_tasks — a task left in ``In Review`` or ``Blocked`` whose
    linked PR (head ``<role>/<task_id[:8]>``) actually MERGED is transitioned to
    ``Done``. The board_worker can mark a task Blocked on a transient late-stage
    failure (e.g. backend_error) AFTER its PR has already been reviewed and
    merged; the stale rules would otherwise re-queue already-merged work. This
    runs FIRST so a merged PR wins over the timeout heuristics.

Transient `backend_error`/`timeout` Blocked tasks with NO merged PR still
self-heal through the existing Rule 3 (IMPROVE_UNBLOCK → Backlog after the stale
window) + backlog promotion; this task does not duplicate that path.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.adapters.plane import PlaneClient
from operations_center.config.settings import Settings
from operations_center.in_flight_reconcile import state_name as _state_name
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceResult

from .board_unblock import (
    _MEM_SKIP_THRESHOLD_GB,
    _GOAL_LABEL,
    _IMPROVE_LABEL,
    _SPEC_AUTHOR_LABEL,
    _apply_rules,
    _has_label,
    _labels,
    _mem_available_gb,
)

logger = logging.getLogger(__name__)

# 10 minutes: tight enough that a stuck task (no-PR In Review, Blocked-but-merged)
# is reconciled promptly, loose enough not to hammer the GitHub/Plane APIs.
DEFAULT_INTERVAL_SECONDS = 600

# task-kind label → branch prefix the board_worker uses (branch = prefix/<id8>).
_LABEL_PREFIX = {
    _GOAL_LABEL: "goal",
    _IMPROVE_LABEL: "improve",
    _SPEC_AUTHOR_LABEL: "spec-author",
}
# Order tried when the task-kind label does not pin a single prefix.
_ALL_PREFIXES = ("goal", "improve", "test", "spec-author")

# Only these (non-terminal) states are candidates for PR-merged reconciliation.
_RECONCILE_STATES = {"in review", "blocked"}


def _repo_key_from_labels(labels: list[str]) -> str | None:
    for lab in labels:
        if lab.lower().startswith("repo:"):
            return lab.split(":", 1)[1].strip()
    return None


def _head_candidates(task_id: str, labels: list[str]) -> list[str]:
    """Branch heads to probe for a merged PR, most-likely first.

    The board_worker names the branch ``<role>/<task_id[:8]>`` (dispatch.py
    ``short_id = task_id[:8]``). Prefer the prefix implied by the task-kind
    label; fall back to the full set (each probe is one cheap, precise API call).
    """
    short = str(task_id)[:8]
    primary = [p for lab, p in _LABEL_PREFIX.items() if _has_label(labels, lab)]
    ordered = primary + [p for p in _ALL_PREFIXES if p not in primary]
    return [f"{prefix}/{short}" for prefix in ordered]


def reconcile_merged_pr_tasks(
    issues: list[dict],
    *,
    settings: Settings,
    gh_client: GitHubPRClient,
) -> list[dict]:
    """Return Done-transition actions for In-Review/Blocked tasks whose PR merged.

    GitHub-aware companion to ``_apply_rules``. Best-effort per task: any lookup
    error skips that task (never raises) so the cycle proceeds.
    """
    actions: list[dict] = []
    owner_repo_cache: dict[str, tuple[str, str] | None] = {}

    for issue in issues:
        state = _state_name(issue)
        if state.lower() not in _RECONCILE_STATES:
            continue
        task_id = str(issue["id"])
        labels = _labels(issue)
        repo_key = _repo_key_from_labels(labels)
        if not repo_key:
            continue

        if repo_key not in owner_repo_cache:
            cfg = settings.repos.get(repo_key)
            try:
                owner_repo_cache[repo_key] = (
                    GitHubPRClient.owner_repo_from_clone_url(cfg.clone_url) if cfg else None
                )
            except Exception:
                owner_repo_cache[repo_key] = None
        owner_repo = owner_repo_cache[repo_key]
        if not owner_repo:
            continue
        owner, repo = owner_repo

        for head in _head_candidates(task_id, labels):
            try:
                pr = gh_client.find_pr_by_head(owner, repo, head)
            except Exception:
                pr = None
            if pr and pr.get("merged_at"):
                actions.append(
                    {
                        "task_id": task_id,
                        "title": str(issue.get("name") or ""),
                        "rule": "PR_MERGED_RECONCILE",
                        "from_state": state,
                        "to_state": "Done",
                        "reason": (
                            f"PR #{pr.get('number')} ({head}) merged at "
                            f"{pr.get('merged_at')} — work landed; reconciling to Done"
                        ),
                    }
                )
                break  # one merged PR is enough

    return actions


def apply_board_actions(client: PlaneClient, actions: list[dict], *, apply: bool) -> list[dict]:
    """Apply transition actions to Plane (or annotate as dry-run). Mirrors the
    standalone CLI's apply loop so behaviour is identical in both call paths."""
    results: list[dict] = []
    for action in actions:
        entry = dict(action)
        if action.get("skipped"):
            results.append(entry)
            continue
        if not apply:
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
            except Exception as exc:  # noqa: BLE001 — one bad action must not abort the rest
                entry["action"] = "error"
                entry["error"] = str(exc)
        results.append(entry)
    return results


class BoardUnblockTask:
    """MaintenanceTask: reconcile merged-PR tasks + run the board-unblock rules."""

    name = "board_unblock"

    def __init__(
        self,
        settings: Settings,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
        apply: bool = True,
        stale_blocked_hours: int = 4,
        stale_running_hours: int = 2,
        clean_blocked_min_minutes: int = 5,
        plane_client: PlaneClient | None = None,
        gh_client: GitHubPRClient | None = None,
    ) -> None:
        self._settings = settings
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._apply = apply
        self._stale_blocked_hours = stale_blocked_hours
        self._stale_running_hours = stale_running_hours
        self._clean_blocked_min_minutes = clean_blocked_min_minutes
        self._plane_client = plane_client
        self._gh_client = gh_client

    def _make_plane_client(self) -> PlaneClient:
        if self._plane_client is not None:
            return self._plane_client
        p = self._settings.plane
        return PlaneClient(
            base_url=p.base_url,
            api_token=self._settings.plane_token(),
            workspace_slug=p.workspace_slug,
            project_id=p.project_id,
        )

    def _make_gh_client(self) -> GitHubPRClient | None:
        if self._gh_client is not None:
            return self._gh_client
        token_env = (
            getattr(getattr(self._settings, "git", None), "token_env", None) or "GITHUB_TOKEN"
        )
        token = os.environ.get(token_env)
        return GitHubPRClient(token=token) if token else None

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()
        details: dict[str, object] = {"apply": self._apply}

        mem_gb = _mem_available_gb()
        if mem_gb < _MEM_SKIP_THRESHOLD_GB:
            return MaintenanceResult(
                name=self.name,
                status="skipped",
                duration_seconds=time.monotonic() - started,
                details={"reason": f"mem_available {mem_gb:.2f}GB < {_MEM_SKIP_THRESHOLD_GB}GB"},
            )

        client = ctx.resources.get("plane_client") or self._make_plane_client()
        owns_client = "plane_client" not in ctx.resources and self._plane_client is None
        try:
            try:
                issues = client.list_issues()
            except Exception as exc:  # noqa: BLE001
                return MaintenanceResult(
                    name=self.name,
                    status="failed",
                    duration_seconds=time.monotonic() - started,
                    details=details,
                    error=f"plane_fetch_failed: {exc}",
                )

            now = datetime.now(UTC)

            # 1) GitHub-aware reconciliation first — a merged PR wins over timeouts.
            reconcile_actions: list[dict] = []
            gh = self._make_gh_client()
            if gh is not None:
                reconcile_actions = reconcile_merged_pr_tasks(
                    issues, settings=self._settings, gh_client=gh
                )
            else:
                details["gh_skipped"] = "no GitHub token available"

            # Tasks reconciled to Done must not also be touched by the stale rules.
            reconciled_ids = {a["task_id"] for a in reconcile_actions}

            # 2) Existing Plane-only Rules 1–10.
            rule_actions = [
                a
                for a in _apply_rules(
                    issues,
                    now=now,
                    stale_blocked_hours=self._stale_blocked_hours,
                    stale_running_hours=self._stale_running_hours,
                    clean_blocked_min_minutes=self._clean_blocked_min_minutes,
                    mem_available_gb=mem_gb,
                )
                if a.get("task_id") not in reconciled_ids
            ]

            results = apply_board_actions(
                client, reconcile_actions + rule_actions, apply=self._apply
            )

            applied = [r for r in results if r.get("action") == "applied"]
            details["scanned"] = len(issues)
            details["reconciled_merged"] = len(reconcile_actions)
            details["rule_actions"] = len(rule_actions)
            details["applied"] = len(applied)
            details["actions"] = results[:50]
            return MaintenanceResult(
                name=self.name,
                status="ok",
                duration_seconds=time.monotonic() - started,
                details=details,
            )
        finally:
            if owns_client:
                client.close()


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "BoardUnblockTask",
    "apply_board_actions",
    "reconcile_merged_pr_tasks",
]
