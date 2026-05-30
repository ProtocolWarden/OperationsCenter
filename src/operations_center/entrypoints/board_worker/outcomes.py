# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Task outcome handlers for board_worker.

Covers success, failure, follow-up creation, scope-split, and the
improve-campaign CI refinement loop.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .labels import (
    STATE_BLOCKED,
    STATE_DONE,
    STATE_READY,
    STATE_REVIEW,
    LIFECYCLE_EXPANDED,
    add_label,
    increment_retry_count,
    label_value,
    retry_count_from_labels,
)

logger = logging.getLogger(__name__)

_MAX_FOLLOW_UP_RETRIES = 3


# ── Atomic failure transition ─────────────────────────────────────────────────

def fail_task(client, task_id: str, role: str, reason: str) -> None:
    try:
        client.transition_issue(task_id, STATE_BLOCKED)
        client.comment_issue(task_id, f"board_worker[{role}] blocked — {reason}")
    except Exception as exc:
        logger.warning(
            "board_worker[%s]: failed to mark task_id=%s blocked — %s", role, task_id, exc,
        )


# ── Improve output ────────────────────────────────────────────────────────────

def read_improve_output(workspace: Path) -> list[dict]:
    """Pull structured suggestions written by the executor to improve-output.json.

    Returns [] when the file is missing or malformed — common when improve mode
    finds nothing actionable.
    """
    out_file = workspace / "improve-output.json"
    if not out_file.exists():
        return []
    try:
        data = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("board_worker[improve]: malformed improve-output.json — %s", exc)
        return []
    raw = data.get("suggestions") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:5] if isinstance(item, dict) and item.get("title")]


# ── Success handler ───────────────────────────────────────────────────────────

def handle_success(
    client,
    issue: dict,
    role: str,
    _task_kind: str,
    needs_verification: bool,
    settings,
    *,
    improve_suggestions: list[dict] | None = None,
    pr_url: str | None = None,
) -> None:
    task_id   = str(issue["id"])
    labels    = issue.get("labels", [])
    repo_key  = label_value(labels, "repo")
    await_review = (
        settings.repos.get(repo_key) and settings.repos[repo_key].await_review
    ) if repo_key else False

    try:
        if role == "goal":
            if needs_verification:
                follow_id = create_follow_up(
                    client, issue, settings, follow_kind="test", reason="verification_needed",
                )
                client.comment_issue(
                    task_id, f"Implementation complete — created verification task #{follow_id}",
                )
                client.transition_issue(task_id, STATE_DONE)
            elif await_review:
                client.transition_issue(task_id, STATE_REVIEW)
                if pr_url:
                    add_label(client, issue, f"pr-url: {pr_url}")
                client.comment_issue(task_id, "Implementation complete — moved to In Review")
            else:
                client.transition_issue(task_id, STATE_DONE)
                client.comment_issue(task_id, "Implementation complete")

        elif role == "test":
            client.transition_issue(task_id, STATE_DONE)
            client.comment_issue(task_id, "Verification passed")

        elif role == "improve":
            client.transition_issue(task_id, STATE_DONE)
            if improve_suggestions:
                created_ids = []
                for suggestion in improve_suggestions:
                    follow_id = create_improve_follow_up(client, issue, settings, suggestion)
                    if follow_id:
                        created_ids.append(follow_id)
                client.comment_issue(
                    task_id,
                    f"Improvement analysis complete — created {len(created_ids)} "
                    f"focused follow-up task(s): {', '.join('#' + i for i in created_ids)}"
                    if created_ids
                    else "Improvement analysis complete — backend wrote suggestions but none could be enqueued",
                )
            else:
                client.comment_issue(
                    task_id,
                    "Improvement analysis complete — no actionable suggestions emitted "
                    "(executor found nothing concrete, or improve-output.json was missing)",
                )

    except Exception as exc:
        logger.warning(
            "board_worker[%s]: post-success transition failed task_id=%s — %s",
            role, task_id, exc,
        )

    try:
        maybe_close_split_parent(client, issue)
    except Exception as exc:
        logger.warning(
            "board_worker: close-parent check failed for task_id=%s — %s", task_id, exc,
        )


# ── Split-parent closer ───────────────────────────────────────────────────────

def maybe_close_split_parent(client, completed_issue: dict) -> None:
    """Close the parent of a scope-split when its last child completes.

    No-op when the just-completed task isn't a scope-split child, the parent
    can't be found, or any sibling is still pending.
    """
    labels = completed_issue.get("labels", [])
    label_names_lower = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        for lab in labels
    ]
    if "source: scope-split" not in label_names_lower:
        return
    parent_id = label_value(labels, "original-task-id")
    if not parent_id:
        return

    try:
        all_issues = client.list_issues()
    except Exception:
        return

    this_task_id = str(completed_issue.get("id", ""))
    parent: dict | None = None
    siblings: list[dict] = []
    for iss in all_issues:
        iss_id = str(iss.get("id", ""))
        if iss_id == parent_id:
            parent = iss
            continue
        if label_value(iss.get("labels", []), "original-task-id") == parent_id:
            sib_labels_lower = [
                (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
                for lab in iss.get("labels", [])
            ]
            if "source: scope-split" in sib_labels_lower:
                siblings.append(iss)

    if parent is None:
        return
    parent_state = (parent.get("state") or {}).get("name", "")
    if parent_state.lower() != "blocked":
        return

    other = [s for s in siblings if str(s.get("id", "")) != this_task_id]
    other_done = all(
        (s.get("state") or {}).get("name", "").strip().lower() == "done"
        for s in other
    )
    if not other_done:
        return

    this_state = (completed_issue.get("state") or {}).get("name", "").strip().lower()
    if this_state and this_state not in ("done", "running", "ready for ai"):
        return

    n_total = len(siblings) + 1
    try:
        client.transition_issue(parent_id, STATE_DONE)
        client.comment_issue(
            parent_id,
            f"Auto-closed: all {n_total} scope-split children completed.",
        )
        logger.info(
            "board_worker: closed parent task_id=%s after %d split children Done",
            parent_id, n_total,
        )
    except Exception as exc:
        logger.warning(
            "board_worker: failed to close parent task_id=%s — %s", parent_id, exc,
        )


# ── Scope-split helpers ───────────────────────────────────────────────────────

def split_files_into_chunks(
    files: list[str], chunk_size: int = 15, max_chunks: int = 6,
) -> list[list[str]]:
    """Group files into roughly-equal chunks capped at max_chunks total."""
    if not files:
        return []
    by_top: dict[str, list[str]] = {}
    for f in files:
        top = f.split("/", 1)[0] if "/" in f else "."
        by_top.setdefault(top, []).append(f)
    groups: list[list[str]] = sorted(by_top.values(), key=len, reverse=True)
    chunks: list[list[str]] = []
    for group in groups:
        for i in range(0, len(group), chunk_size):
            chunks.append(group[i: i + chunk_size])
    while len(chunks) > max_chunks and len(chunks) >= 2:
        chunks.sort(key=len)
        merged = chunks[0] + chunks[1]
        chunks = [merged] + chunks[2:]
    return chunks


def create_split_followups(
    client, parent: dict, _settings, file_list: list[str], reason: str,
) -> list[str]:
    """Spawn smaller goal tasks scoped to file subsets after a scope_too_wide block.

    Caps split depth at 2 (retry-count >= 2 → block instead of split).
    """
    parent_id     = str(parent["id"])
    parent_title  = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key      = label_value(parent_labels, "repo")
    retry_count   = retry_count_from_labels(parent_labels)

    if retry_count >= 2:
        logger.info(
            "board_worker: not splitting task_id=%s — retry-count=%d already exhausted",
            parent_id, retry_count,
        )
        return []

    chunks = split_files_into_chunks(file_list)
    if not chunks:
        return []

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]

    created: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        title = f"[split {idx}/{len(chunks)}] {parent_title}"[:80]
        files_block = "allowed_paths:\n" + "\n".join(f"  - {f}" for f in chunk) + "\n"
        description = (
            f"## Goal\n{parent_title}\n\n"
            f"This is split {idx} of {len(chunks)} from a scope_too_wide retry of "
            f"task #{parent_id}. Restrict changes to the listed files.\n\n"
            f"## Execution\n"
            f"repo: {repo_key}\n"
            f"mode: goal\n"
            f"{files_block}"
        )
        labels = [
            "task-kind: goal",
            f"repo: {repo_key}",
            "source: board_worker",
            "source: scope-split",
            *inherited_sources,
            f"original-task-id: {parent_id}",
            f"handoff-reason: {reason}",
            f"retry-count: {retry_count + 1}",
        ]
        try:
            new_issue = client.create_issue(
                name=title, description=description,
                state=STATE_READY, label_names=labels,
            )
            new_id = str(new_issue.get("id", ""))
            if new_id:
                created.append(new_id)
        except Exception as exc:
            logger.warning("board_worker: split create_issue failed — %s", exc)

    if created:
        add_label(client, parent, LIFECYCLE_EXPANDED)

    logger.info(
        "board_worker: split task_id=%s into %d chunks (retry-count=%d → %d)",
        parent_id, len(created), retry_count, retry_count + 1,
    )
    return created


# ── Failure handler ───────────────────────────────────────────────────────────

def handle_failure(
    client,
    issue: dict,
    role: str,
    _task_kind: str,
    result: dict,
    settings,
    *,
    scope_files: list[str] | None = None,
) -> None:
    task_id  = str(issue["id"])
    status   = result.get("status", "unknown")
    category = result.get("failure_category") or "unknown"
    reason   = result.get("failure_reason") or "(no reason provided)"

    split_ids: list[str] = []
    if category == "scope_too_wide" and scope_files:
        try:
            split_ids = create_split_followups(
                client, issue, settings, scope_files, reason="scope_too_wide_split",
            )
        except Exception as exc:
            logger.warning("board_worker: scope-split spawn failed — %s", exc)

    logger.warning(
        "board_worker[%s]: task_id=%s blocked status=%s category=%s reason=%s",
        role, task_id, status, category, reason,
    )

    executor_exit_code: int | None = result.get("executor_exit_code")
    executor_signal: str | None    = result.get("executor_signal")

    try:
        if role == "test":
            follow_id = create_follow_up(
                client, issue, settings, follow_kind="goal", reason="verification_failed",
            )
            client.transition_issue(task_id, STATE_BLOCKED)
            client.comment_issue(
                task_id,
                f"Verification failed — created follow-up goal task #{follow_id}\n"
                f"\n"
                f"- status: {status}\n"
                f"- category: {category}\n"
                f"- reason: {reason}",
            )
        else:
            client.transition_issue(task_id, STATE_BLOCKED)
            split_block = ""
            if split_ids:
                split_block = (
                    f"\n\nAuto-split into {len(split_ids)} focused task(s): "
                    + ", ".join(f"#{i}" for i in split_ids)
                )
            exec_block = ""
            if executor_exit_code is not None:
                exec_block = f"\n- executor-exit-code: {executor_exit_code}"
                if executor_signal:
                    exec_block += f"\n- executor-signal: {executor_signal}"
            client.comment_issue(
                task_id,
                f"board_worker[{role}] failed\n"
                f"\n"
                f"- status: {status}\n"
                f"- category: {category}\n"
                f"- reason: {reason}"
                f"{exec_block}"
                f"{split_block}",
            )
        if executor_exit_code is not None:
            add_label(client, issue, f"executor-exit-code: {executor_exit_code}")
        if executor_signal:
            add_label(client, issue, f"executor-signal: {executor_signal}")
            if "sigkill" in executor_signal.lower():
                increment_retry_count(client, issue)
    except Exception as exc:
        logger.warning(
            "board_worker[%s]: post-failure transition failed task_id=%s — %s",
            role, task_id, exc,
        )


# ── Follow-up creators ────────────────────────────────────────────────────────

def create_improve_follow_up(
    client, parent: dict, _settings, suggestion: dict,
) -> str | None:
    """Create a focused goal task from one improve suggestion."""
    parent_id     = str(parent["id"])
    parent_labels = parent.get("labels", [])
    repo_key      = label_value(parent_labels, "repo")

    title      = str(suggestion.get("title", "")).strip()[:80] or "Improve follow-up"
    rationale  = str(suggestion.get("rationale", "")).strip()
    files      = suggestion.get("files") or []
    complexity = str(suggestion.get("complexity", "")).strip().lower()

    files_block = ""
    if isinstance(files, list) and files:
        files_block = (
            "allowed_paths:\n"
            + "\n".join(f"  - {f}" for f in files[:10] if isinstance(f, str))
            + "\n"
        )

    description = (
        f"## Goal\n{title}\n\n"
        f"## Rationale\n{rationale or '(none provided)'}\n\n"
        f"## Execution\n"
        f"repo: {repo_key}\n"
        f"mode: goal\n"
        f"{files_block}"
    )

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
        if (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower().startswith("source:")
        and (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower() != "source: board_worker"
    ]
    label_names = [
        "task-kind: goal",
        f"repo: {repo_key}",
        "source: board_worker",
        "source: improve-suggestion",
        *inherited_sources,
        f"original-task-id: {parent_id}",
        "handoff-reason: improve_suggestion",
    ]
    if complexity in {"small", "medium", "large"}:
        label_names.append(f"complexity: {complexity}")

    try:
        issue = client.create_issue(
            name=title, description=description,
            state=STATE_READY, label_names=label_names,
        )
        new_id = str(issue.get("id", ""))
        logger.info(
            "board_worker[improve]: spawned follow-up task_id=%s title=%r complexity=%s",
            new_id, title, complexity,
        )
        return new_id or None
    except Exception as exc:
        logger.warning(
            "board_worker[improve]: failed to create follow-up for %r — %s", title, exc,
        )
        return None


def create_follow_up(
    client, parent: dict, _settings, follow_kind: str, reason: str,
) -> str:
    """Create a follow-up Plane task with full lineage metadata.

    Returns the new task id, or "" when the retry cap is reached.
    """
    parent_id     = str(parent["id"])
    parent_title  = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key      = label_value(parent_labels, "repo")
    base_branch   = label_value(parent_labels, "base-branch")
    retry_count   = retry_count_from_labels(parent_labels)

    if retry_count >= _MAX_FOLLOW_UP_RETRIES:
        logger.info(
            "board_worker: refusing follow-up — parent task_id=%s already at retry-count=%d",
            parent_id, retry_count,
        )
        return ""

    description = (
        f"## Goal\n{parent_title} — {reason.replace('_', ' ')}\n\n"
        f"## Execution\n"
        f"repo: {repo_key}\n"
        f"mode: {follow_kind}\n"
        + (f"base_branch: {base_branch}\n" if base_branch else "")
    )

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
        if (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower().startswith("source:")
        and (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower() != "source: board_worker"
    ]
    label_names = [
        f"task-kind: {follow_kind}",
        f"repo: {repo_key}",
        "source: board_worker",
        *inherited_sources,
        f"original-task-id: {parent_id}",
        f"handoff-reason: {reason}",
        f"retry-count: {retry_count + 1}",
    ]

    issue = client.create_issue(
        name=f"[{follow_kind}] {parent_title}",
        description=description,
        state=STATE_READY,
        label_names=label_names,
    )
    new_id = str(issue.get("id", "?"))
    logger.info(
        "board_worker: created follow-up task_id=%s kind=%s reason=%s",
        new_id, follow_kind, reason,
    )
    return new_id


# ── CI refinement loop (improve_campaign) ─────────────────────────────────────

def run_ci_loop(
    *,
    ci_spec_raw: dict,
    client,
    issue: dict,
    role: str,
    task_kind: str,
    task_id: str,
    repo_key: str,
    settings,
    python: str,
    oc_root: Path,
    env: dict,
    bundle_file: Path,
    config_file: Path,
    tmp: Path,
    short_id: str,
) -> bool:
    """Drive a ContinuousImprovementSpec refinement loop for an improve_campaign task."""
    from operations_center.contracts.ci import ContinuousImprovementSpec
    from operations_center.contracts.enums import RefinementStatus
    from operations_center.execution.ci_coordinator import CiCoordinator, CiRunContext
    from operations_center.execution.ci_store import CiStore
    from pydantic import ValidationError

    try:
        ci_spec = ContinuousImprovementSpec.model_validate(ci_spec_raw)
    except (ValidationError, Exception) as exc:
        logger.error(
            "board_worker[%s]: task_id=%s invalid ContinuousImprovementSpec — %s",
            role, task_id, exc,
        )
        fail_task(client, task_id, role, f"invalid continuous_improvement spec: {exc}")
        return False

    repo_path = Path(settings.repos[repo_key].local_path) if settings.repos.get(repo_key) else Path(repo_key)
    lineage_id = f"lin-{task_id[:12]}"
    validation_commands: list[str] = []
    repo_cfg = settings.repos.get(repo_key)
    if repo_cfg and hasattr(repo_cfg, "validation_commands"):
        validation_commands = list(getattr(repo_cfg, "validation_commands", []))

    store_path = oc_root / "state" / "ci_lineage.json"
    ctx = CiRunContext(
        proposal_id=bundle_file.parent.name,
        repo_path=repo_path,
        lineage_id=lineage_id,
        spec=ci_spec,
        validation_commands=validation_commands,
        store_path=store_path,
        eval_output_dir=tmp / "eval_output",
        timeout_seconds=120,
    )

    last_attempt_result: dict = {}

    def execute(*, attempt_number: int, strategy, proposal_id: str):
        attempt_workspace  = tmp / f"workspace-ci-{attempt_number}"
        attempt_workspace.mkdir(exist_ok=True)
        attempt_result_file = tmp / f"result-ci-{attempt_number}.json"

        exec_cmd = [
            python, "-m", "operations_center.entrypoints.execute.main",
            "--config",         str(config_file),
            "--bundle",         str(bundle_file),
            "--workspace-path", str(attempt_workspace),
            "--task-branch",    f"{role}/{short_id}-ci{attempt_number}",
            "--output",         str(attempt_result_file),
            "--source",         f"board_worker_{role}_ci_{attempt_number}",
        ]
        logger.info(
            "board_worker[%s]: CI attempt %d for task_id=%s", role, attempt_number, task_id,
        )
        subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        run_id: str = f"ci-{task_id[:8]}-attempt-{attempt_number}"
        changed_files: list[str] = []
        success = False

        if attempt_result_file.exists():
            try:
                attempt_outcome = json.loads(attempt_result_file.read_text(encoding="utf-8"))
                r = attempt_outcome.get("result", {})
                success       = r.get("success", False)
                run_id        = r.get("run_id", run_id)
                changed_files = [f["path"] for f in r.get("changed_files", []) if "path" in f]
                last_attempt_result.update(r)
            except Exception as exc:
                logger.warning(
                    "board_worker[%s]: CI attempt %d result parse failed — %s",
                    role, attempt_number, exc,
                )
        return run_id, changed_files, success

    try:
        coordinator = CiCoordinator(store=CiStore(path=store_path))
        ci_result   = coordinator.run(ctx, execute)
    except Exception as exc:
        logger.error(
            "board_worker[%s]: CI coordinator failed task_id=%s — %s", role, task_id, exc,
        )
        fail_task(client, task_id, role, f"CI coordinator error: {exc}")
        return False

    logger.info(
        "board_worker[%s]: CI loop done task_id=%s status=%s attempts=%d",
        role, task_id, ci_result.final_status.value, ci_result.total_attempts,
    )
    add_label(client, issue, f"ci-status: {ci_result.final_status.value}")
    add_label(client, issue, f"ci-attempts: {ci_result.total_attempts}")

    if ci_result.final_status == RefinementStatus.ACCEPTED:
        handle_success(client, issue, role, task_kind, False, settings)
        return True

    if ci_result.final_status == RefinementStatus.ESCALATED:
        fail_task(
            client, task_id, role,
            f"CI loop escalated after {ci_result.total_attempts} attempt(s) — "
            "inconclusive outcome requires operator decision",
        )
        return False

    failure_result = dict(last_attempt_result)
    failure_result.setdefault("status", ci_result.final_status.value)
    failure_result.setdefault(
        "failure_reason",
        f"CI refinement loop ended with status={ci_result.final_status.value} "
        f"after {ci_result.total_attempts} attempt(s)",
    )
    handle_failure(client, issue, role, task_kind, failure_result, settings)
    return False
