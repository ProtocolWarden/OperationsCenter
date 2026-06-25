# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Board claim logic — find and atomically claim the next eligible task."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ._text import desc_text, extract_goal
from .labels import (
    ROLE_KINDS,
    STATE_BLOCKED,
    STATE_READY,
    STATE_RUNNING,
    add_label,
    has_label,
    label_value,
)
from .spec_author import SPEC_AUTHOR_REPO_KEY

logger = logging.getLogger(__name__)

_MIN_GOAL_TEXT_CHARS = 40
_TOUCHED_STATES = {"running", "in review", "done", "blocked"}
_PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
# proposal.priority (contracts.enums.Priority) — wired as a STABLE, lowest-
# precedence tiebreaker so the *default* "normal" preserves today's claim order
# byte-for-byte (a constant rank only ever breaks a tie the prior keys left, and
# a stable sort then falls back to input order, exactly as before). A task opts
# into reordering by carrying a `priority: high|normal|low|critical` label.
_PROPOSAL_PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}
_DEFAULT_PROPOSAL_PRIORITY = "normal"
_DEFAULT_PROPOSAL_RANK = _PROPOSAL_PRIORITY_ORDER[_DEFAULT_PROPOSAL_PRIORITY]


def proposal_priority_rank(labels: list) -> int:
    """Rank an issue's proposal.priority for queue ordering (lower = sooner).

    Reads a ``priority: <level>`` label; absent/unknown → the default "normal"
    rank, which makes this key a no-op for today's all-default queue.
    """
    raw = (label_value(labels, "priority") or _DEFAULT_PROPOSAL_PRIORITY).strip().lower()
    return _PROPOSAL_PRIORITY_ORDER.get(raw, _DEFAULT_PROPOSAL_RANK)


def claim_next(client, role: str, settings) -> dict | None:
    """Find the oldest Ready-for-AI issue matching this role's task-kinds and a
    known repo, then atomically transition to Running to claim it.

    Returns the raw Plane issue dict, or None if nothing is available.
    """
    kinds = ROLE_KINDS[role]
    managed_repos = set(settings.repos.keys())

    try:
        issues = client.list_issues()
    except Exception:
        logger.warning("board_worker[%s]: failed to list issues", role)
        return None

    exec_today = _count_daily_executions(issues)
    candidates = _build_candidates(client, issues, role, kinds, managed_repos, settings, exec_today)

    if not candidates:
        return None

    candidates.sort(key=_sort_key)
    issue = candidates[0]
    task_id = str(issue["id"])
    task_kind = label_value(issue.get("labels", []), "task-kind")

    # Thin-goal guard: refuse tasks whose goal text is too short to act on.
    # Skip for spec-author (payload-based, not goal-text-based).
    if task_kind != "spec-author":
        candidate_goal = extract_goal(desc_text(issue), issue.get("name", "")).strip()
        if len(candidate_goal) < _MIN_GOAL_TEXT_CHARS:
            _block_thin_task(client, issue, role, len(candidate_goal))
            return None

    # OPEN_PR_GATE: refuse to claim goal tasks while a non-spec open PR exists.
    if role == "goal" and not _open_pr_gate_clear(client, issue, settings, task_id):
        return None

    try:
        client.transition_issue(task_id, STATE_RUNNING)
        logger.info(
            "board_worker[%s]: claimed task_id=%s title=%r",
            role,
            task_id,
            issue.get("name", ""),
        )
    except Exception as exc:
        logger.warning(
            "board_worker[%s]: failed to claim task_id=%s — %s",
            role,
            task_id,
            exc,
        )
        return None

    return issue


# ── Internal helpers ──────────────────────────────────────────────────────────


def _count_daily_executions(issues: list[dict]) -> dict[str, int]:
    """Count tasks each repo has had active in the last 24 hours."""
    exec_today: dict[str, int] = {}
    now_utc = datetime.now(UTC)
    for issue in issues:
        st = issue.get("state")
        st_name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        if st_name not in _TOUCHED_STATES:
            continue
        ts_raw = issue.get("updated_at") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if (now_utc - ts).total_seconds() > 86400:
            continue
        rk = label_value(issue.get("labels", []), "repo")
        if rk:
            exec_today[rk] = exec_today.get(rk, 0) + 1
    return exec_today


def _build_candidates(
    client,
    issues: list[dict],
    role: str,
    kinds: list[str],
    managed_repos: set[str],
    settings,
    exec_today: dict[str, int],
) -> list[dict]:
    """Filter issues down to claimable candidates for this role."""
    candidates = []
    for issue in issues:
        state_obj = issue.get("state")
        state_name = (
            state_obj.get("name", "") if isinstance(state_obj, dict) else str(state_obj or "")
        ).strip()
        if state_name != STATE_READY:
            continue

        labels = issue.get("labels", [])
        task_kind = label_value(labels, "task-kind")
        if task_kind not in kinds:
            continue

        repo_key = label_value(labels, "repo")
        if task_kind == "spec-author":
            repo_key = SPEC_AUTHOR_REPO_KEY
        if repo_key not in managed_repos:
            continue

        # Admission control (surface 5): refuse un-allowlisted task authors.
        # No-op unless settings.task_admission.author_allowlist is configured.
        if not _author_allowed(issue, settings):
            _flag_unauthorized_author(client, issue, role, settings)
            continue

        repo_cfg = settings.repos.get(repo_key)
        cap = getattr(repo_cfg, "max_daily_executions", None) if repo_cfg else None
        if cap and exec_today.get(repo_key, 0) >= int(cap):
            logger.info(
                "board_worker[%s]: skipping repo %s — daily quota %d reached (today=%d)",
                role,
                repo_key,
                cap,
                exec_today[repo_key],
            )
            continue

        candidates.append(issue)
    return candidates


def _issue_author_identities(issue: dict) -> tuple[str | None, ...]:
    """Extract comparable creator identities from a Plane issue, tolerant of the
    several shapes the API uses (bare id string, or a nested actor dict)."""

    out: list[str | None] = []
    for key in ("created_by", "created_by_id", "author", "creator"):
        val = issue.get(key)
        if isinstance(val, str):
            out.append(val)
        elif isinstance(val, dict):
            out.extend(str(val[k]) for k in ("id", "email", "display_name", "name") if val.get(k))
    return tuple(out)


def _author_allowed(issue: dict, settings) -> bool:
    admission = getattr(settings, "task_admission", None)
    if admission is None or not admission.enforced():
        return True
    return admission.allows(*_issue_author_identities(issue))


def _flag_unauthorized_author(client, issue: dict, role: str, settings) -> None:
    """Best-effort: label the rejected task once so an operator can promote it."""

    reject_label = settings.task_admission.reject_label
    if has_label(issue.get("labels", []), reject_label):
        return  # already flagged — don't spam the API every poll
    try:
        add_label(client, issue, reject_label)
        logger.warning(
            "board_worker[%s]: refused task_id=%s — author not in admission "
            "allowlist (identities=%s)",
            role,
            issue.get("id"),
            list(_issue_author_identities(issue)),
        )
    except Exception as exc:
        logger.warning(
            "board_worker[%s]: failed to flag unauthorized author task_id=%s — %s",
            role,
            issue.get("id"),
            exc,
        )


def _sort_key(issue: dict) -> tuple:
    labs = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        for lab in issue.get("labels", [])
    ]
    is_improve_suggestion = 0 if "source: improve-suggestion" in labs else 1
    plane_priority = str(issue.get("priority") or "none").lower()
    plane_rank = _PRIORITY_ORDER.get(plane_priority, 4)
    # proposal.priority is the LAST element: a pure tiebreaker that leaves the
    # existing (improve-suggestion, plane-rank, created_at) order untouched and
    # only orders genuine ties — and is constant (=normal) for today's queue.
    proposal_rank = proposal_priority_rank(issue.get("labels", []))
    return (is_improve_suggestion, plane_rank, issue.get("created_at", ""), proposal_rank)


def _block_thin_task(client, issue: dict, role: str, goal_len: int) -> None:
    task_id = str(issue["id"])
    try:
        client.transition_issue(task_id, STATE_BLOCKED)
        client.comment_issue(
            task_id,
            f"board_worker[{role}] refused to claim — goal text too thin "
            f"({goal_len} chars; minimum {_MIN_GOAL_TEXT_CHARS}). "
            "Add concrete description and re-promote to Ready for AI.",
        )
        add_label(client, issue, "thin-goal")
    except Exception as exc:
        logger.warning(
            "board_worker[%s]: empty-goal block failed task_id=%s — %s",
            role,
            issue.get("id"),
            exc,
        )
    logger.info(
        "board_worker[%s]: refused thin task_id=%s title=%r",
        role,
        task_id,
        issue.get("name", ""),
    )


def _parse_iso(ts_raw: object) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (``...Z``) to an aware datetime, or None."""
    if not isinstance(ts_raw, str) or not ts_raw:
        return None
    try:
        return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _open_pr_gate_clear(client, issue: dict, settings, task_id: str) -> bool:
    """Return True if no blocking open PRs exist for this task's repo.

    Excludes spec-author branches and PRs with mergeable=UNKNOWN (CI in-flight).
    A candidate PR that has been idle beyond ``settings.open_pr_gate_stale_hours``
    is escaped (no longer blocking) so a single stuck PR cannot deadlock the lane
    forever — the #387 incident. Stale PRs are surfaced (warning), never auto-closed.
    """
    labels = issue.get("labels", [])
    gate_repo_key = label_value(labels, "repo")
    gate_repo_cfg = settings.repos.get(gate_repo_key) if gate_repo_key else None
    gate_clone_url = gate_repo_cfg.clone_url if gate_repo_cfg else None
    gh_token = settings.git_token()

    if not gh_token or not gate_clone_url:
        return True

    try:
        from operations_center.adapters.github_pr import GitHubPRClient

        gh = GitHubPRClient(gh_token)
        owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(gate_clone_url)
        open_prs = gh.list_open_prs(owner, repo_name)

        def _is_candidate(pr: dict) -> bool:
            # Spec-author branches never block; mergeable=UNKNOWN means CI is still
            # in-flight, so don't block on it yet.
            ref = (pr.get("head") or {}).get("ref", "")
            if ref.startswith("spec-author/"):
                return False
            if pr.get("mergeable") == "UNKNOWN":
                return False
            return True

        try:
            stale_hours = float(getattr(settings, "open_pr_gate_stale_hours", 0.0) or 0.0)
        except (TypeError, ValueError):
            stale_hours = 0.0
        stale_cutoff = (
            datetime.now(UTC) - timedelta(hours=stale_hours) if stale_hours > 0 else None
        )

        def _is_stale(pr: dict) -> bool:
            if stale_cutoff is None:
                return False
            updated = _parse_iso(pr.get("updated_at"))
            return updated is not None and updated < stale_cutoff

        candidates = [pr for pr in open_prs if _is_candidate(pr)]
        stale = [pr for pr in candidates if _is_stale(pr)]
        blocking = [pr for pr in candidates if not _is_stale(pr)]

        if stale:
            logger.warning(
                "board_worker[goal]: OPEN_PR_GATE staleness escape — repo=%s PR(s) %s "
                "idle >%.1fh; NOT blocking the lane (degrade-never-halt) — resolve or "
                "close them. task_id=%s",
                gate_repo_key,
                [pr.get("number") for pr in stale[:5]],
                stale_hours,
                task_id,
            )

        if blocking:
            pr_nums = [pr.get("number") for pr in blocking[:5]]
            logger.info(
                "board_worker[goal]: OPEN_PR_GATE — repo=%s has %d blocking PR(s) %s "
                "(%d spec-author/in-flight excluded, %d stale-escaped); skipping "
                "task_id=%s until merged",
                gate_repo_key,
                len(blocking),
                pr_nums,
                len(open_prs) - len(candidates),
                len(stale),
                task_id,
            )
            add_label(client, issue, "OPEN_PR_GATE")
            return False
    except Exception as exc:
        logger.warning(
            "board_worker[goal]: OPEN_PR_GATE check failed repo=%s — %s; proceeding",
            gate_repo_key,
            exc,
        )

    return True
