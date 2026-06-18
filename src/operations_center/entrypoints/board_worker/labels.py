# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Plane label helpers and board-state constants for board_worker."""

from __future__ import annotations

import logging
from pathlib import Path

# Public API of this module — declared explicitly (consumed library; some
# functions are tested as the boundary but not all internally wired).
__all__ = [
    "label_value",
    "has_label",
    "retry_count_from_labels",
    "add_label",
    "increment_retry_count",
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


# ── Label helpers ─────────────────────────────────────────────────────────────


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


def retry_count_from_labels(labels: list) -> int:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        if name.startswith("retry-count:"):
            try:
                return int(name.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def add_label(client, issue: dict, new_label: str) -> None:
    """Append new_label to an issue's label set if not already present.

    Plane's update_issue_labels replaces the set, so we read existing labels
    first. Failures are non-fatal — the next cycle can re-apply.
    """
    existing = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
    ]
    existing = [name for name in existing if name]
    if new_label in existing:
        return
    try:
        client.update_issue_labels(str(issue["id"]), existing + [new_label])
    except Exception as exc:
        logger.warning(
            "board_worker: failed to add label %r to task_id=%s — %s",
            new_label,
            issue.get("id"),
            exc,
        )


def increment_retry_count(client, issue: dict) -> None:
    """Bump retry-count label by 1 (adds 'retry-count: 1' if absent).

    Removes the old retry-count: N and adds retry-count: N+1 so
    board_unblock Rule 1 can cancel tasks that SIGKILL repeatedly.
    """
    existing = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
    ]
    existing = [name for name in existing if name]
    current = 0
    filtered = []
    for label in existing:
        if label.lower().startswith("retry-count:"):
            try:
                current = int(label.split(":", 1)[1].strip())
            except ValueError:
                pass
        else:
            filtered.append(label)
    filtered.append(f"retry-count: {current + 1}")
    try:
        client.update_issue_labels(str(issue["id"]), filtered)
    except Exception as exc:
        logger.warning(
            "board_worker: failed to increment retry-count for task_id=%s — %s",
            issue.get("id"),
            exc,
        )
