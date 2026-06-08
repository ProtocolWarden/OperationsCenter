# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import re


NO_SALVAGE_PHRASE = "no salvage value"
_NO_SALVAGE_LINE_RE = re.compile(
    r"no salvage value\s*[:\-]\s*\S.+",
    re.IGNORECASE,
)
_PRESERVED_WORK_PATTERNS = (
    "work preserved",
    "preserved on branch",
    "preserved in the branch",
    "preserved on it",
    "preserved on origin",
    "preserved in branch",
)


def has_no_salvage_justification(comment: str) -> bool:
    """Return True when the comment explicitly declares no salvage value and why."""
    return any(_NO_SALVAGE_LINE_RE.search(line or "") for line in comment.splitlines())


def close_comment_claims_preserved_work(comment: str) -> bool:
    """Return True when the close comment says the work remains on a branch."""
    lowered = comment.lower()
    return any(pattern in lowered for pattern in _PRESERVED_WORK_PATTERNS)


def close_without_receipt_allowed(*, comment: str, durable_receipt_recorded: bool) -> bool:
    """Return True when an automated close satisfies the salvage invariant."""
    if durable_receipt_recorded:
        return True
    return has_no_salvage_justification(comment)


def branch_delete_allowed_after_close(*, comment: str, durable_receipt_recorded: bool) -> bool:
    """Return True when deleting the head branch would not contradict the close record."""
    if not durable_receipt_recorded:
        return False
    return not close_comment_claims_preserved_work(comment)
