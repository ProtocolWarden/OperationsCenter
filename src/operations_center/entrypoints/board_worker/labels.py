# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Plane label helpers and board-state constants for board_worker."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from operations_center.policy.engine import TRUSTED_SOURCE_LABELS

# Public API of this module — declared explicitly (consumed library; some
# functions are tested as the boundary but not all internally wired).
__all__ = [
    "BLOCKED_REASON_LABELS",
    "label_value",
    "has_label",
    "retry_count_from_labels",
    "add_label",
    "remove_labels",
    "clear_blocked_reason_labels",
    "increment_retry_count",
    "issue_author_identities",
    "build_forwarded_labels",
]

logger = logging.getLogger(__name__)

# ── Plane states ──────────────────────────────────────────────────────────────
STATE_READY = "Ready for AI"
STATE_RUNNING = "Running"
STATE_DONE = "Done"
STATE_BLOCKED = "Blocked"
STATE_REVIEW = "In Review"

# Lifecycle label applied to a meta-task whose real work is split into children.
# Prevents rewrite loops from picking at completed parent tasks.
LIFECYCLE_EXPANDED = "lifecycle: expanded"

# task-kind labels claimed per role
ROLE_KINDS: dict[str, list[str]] = {
    "goal": ["goal"],
    "test": ["test", "test_campaign"],
    "improve": ["improve", "improve_campaign"],
    "spec-author": ["spec-author"],
}

GITHUB_DIR = Path.home() / "Documents" / "GitHub"
BLOCKED_REASON_LABELS = [
    "blocked-reason: policy",
    "blocked-reason: backend-capacity",
]


# ── Label helpers ─────────────────────────────────────────────────────────────


def _label_names(issue: dict) -> list[str]:
    return [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
        if (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
    ]


def _store_label_names(issue: dict, names: list[str]) -> None:
    issue["labels"] = list(names)


def label_value(labels: list, prefix: str) -> str:
    """Extract value from a 'prefix: value' label, or ''."""
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def has_label(labels: list, value: str) -> bool:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower() == value.lower():
            return True
    return False


def count_from_labels(labels: list, prefix: str) -> int:
    """Parse the integer value of a ``<prefix>: N`` counter label (0 if absent)."""
    prefix = prefix.lower()
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        if name.startswith(prefix):
            try:
                return int(name.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def retry_count_from_labels(labels: list) -> int:
    return count_from_labels(labels, "retry-count:")


def code_failure_count_from_labels(labels: list) -> int:
    return count_from_labels(labels, "code-fail-count:")


def add_label(client, issue: dict, new_label: str) -> None:
    """Append new_label to an issue's label set if not already present.

    Plane's update_issue_labels replaces the set, so we read existing labels
    first. Failures are non-fatal — the next cycle can re-apply.
    """
    existing = _label_names(issue)
    if new_label in existing:
        return
    updated = existing + [new_label]
    try:
        client.update_issue_labels(str(issue["id"]), updated)
        _store_label_names(issue, updated)
    except Exception as exc:
        logger.warning(
            "board_worker: failed to add label %r to task_id=%s — %s",
            new_label,
            issue.get("id"),
            exc,
        )


def remove_labels(client, issue: dict, labels_to_remove: list[str]) -> None:
    """Remove matching labels from an issue, updating the local copy on success."""
    existing = _label_names(issue)
    removal = {label.lower() for label in labels_to_remove}
    filtered = [label for label in existing if label.lower() not in removal]
    if filtered == existing:
        return
    try:
        client.update_issue_labels(str(issue["id"]), filtered)
        _store_label_names(issue, filtered)
    except Exception as exc:
        logger.warning(
            "board_worker: failed to remove labels %r from task_id=%s — %s",
            labels_to_remove,
            issue.get("id"),
            exc,
        )


def clear_blocked_reason_labels(client, issue: dict) -> None:
    """Drop stale blocked-reason labels when a task becomes runnable again."""
    remove_labels(client, issue, BLOCKED_REASON_LABELS)


def increment_count_label(client, issue: dict, prefix: str) -> None:
    """Bump a ``<prefix> N`` counter label by 1 (adds ``<prefix> 1`` if absent).

    Removes the old ``<prefix> N`` and adds ``<prefix> N+1`` so board_unblock
    Rule 1 can cancel tasks that exhaust a count cap.
    """
    label_prefix = prefix.rstrip()  # e.g. "retry-count:"
    existing = _label_names(issue)
    current = 0
    filtered = []
    for label in existing:
        if label.lower().startswith(label_prefix.lower()):
            try:
                current = int(label.split(":", 1)[1].strip())
            except ValueError:
                pass
        else:
            filtered.append(label)
    filtered.append(f"{label_prefix} {current + 1}")
    try:
        client.update_issue_labels(str(issue["id"]), filtered)
        _store_label_names(issue, filtered)
    except Exception as exc:
        logger.warning(
            "board_worker: failed to increment %s for task_id=%s — %s",
            label_prefix,
            issue.get("id"),
            exc,
        )


def increment_retry_count(client, issue: dict) -> None:
    """Bump retry-count by 1 so board_unblock Rule 1 can cancel SIGKILL loops."""
    increment_count_label(client, issue, "retry-count:")


def increment_code_failure_count(client, issue: dict) -> None:
    """Bump code-fail-count by 1 so board_unblock Rule 1 can cancel a clean
    code-failure loop (CODE_FAILURE_RETRY_CAP). retry-count is SIGKILL-only, so a
    clean test/lint failure would otherwise re-run forever."""
    increment_count_label(client, issue, "code-fail-count:")


def issue_author_identities(issue: dict) -> tuple[str | None, ...]:
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


def build_forwarded_labels(
    labels: list,
    repo_cfg,
    *,
    issue: dict | None = None,
    settings: Any = None,
) -> list[str]:
    """Build the label list to forward to the planning subprocess.

    Filters source labels based on the repo's require_explicit_approval setting.

    Trusted source labels (TRUSTED_SOURCE_LABELS) bypass the policy engine's
    review gates, so they pass through here only when the issue creator is on
    ``task_admission.trusted_label_authors`` — a Plane label is a plain string
    any board author can attach, and the issue creator is the only provenance
    the API exposes. Unconfigured (empty allowlist) fails closed: the label is
    stripped and the task goes through the normal review gates.
    """
    explicit_required = bool(getattr(repo_cfg, "require_explicit_approval", False))
    admission = getattr(settings, "task_admission", None) if settings is not None else None
    creator_trusted = bool(
        admission is not None
        and issue is not None
        and admission.label_trust_allows(*issue_author_identities(issue))
    )
    forwarded: list[str] = []
    for label in labels:
        name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
        low = name.lower()
        if low == "review_required":
            forwarded.append(name)
            continue
        if low.startswith("source:"):
            if low in TRUSTED_SOURCE_LABELS:
                if explicit_required or not creator_trusted:
                    if not explicit_required:
                        logger.warning(
                            "board_worker: stripping trusted source label %r from task %s — "
                            "issue creator not in task_admission.trusted_label_authors "
                            "(review-gate bypass denied)",
                            name,
                            (issue or {}).get("id", "<unknown>"),
                        )
                    continue
            forwarded.append(name)
    if explicit_required:
        forwarded.append("review_required")
    return forwarded
