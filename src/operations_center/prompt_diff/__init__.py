# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Surgical prompt-diff primitives.

Adapted from temm1e-labs/promptlabs `api/app/agents/optimizer.py`
(MIT per upstream README — https://github.com/temm1e-labs/promptlabs).
Copied in rather than imported because we want the schema + application
logic, not the full closed-loop optimizer agent.

Surface:

* :class:`EditOp`              — Literal alias of supported operations.
* :class:`Edit`                — Pydantic v2 model describing one surgical edit.
* :class:`EditApplicationError` — Raised when a single edit cannot be applied.
* :func:`apply_one`            — Apply one edit; raises on failure.
* :func:`apply_edits`          — Apply a list; skips failures, returns
  ``(new_text, ApplyResult)``.
* :class:`ApplyResult`         — Aggregate result: counts + skip reasons.

The Optimizer LLM closed loop, budgets, variable-preservation checks,
and async wiring are intentionally NOT carried over — OC drives the
LLM elsewhere (board_worker spec-author task-kind).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "EditOp",
    "Edit",
    "EditApplicationError",
    "ApplyResult",
    "apply_one",
    "apply_edits",
]


EditOp = Literal["replace", "insert_before", "insert_after", "delete", "append"]


class Edit(BaseModel):
    """One surgical edit against a string.

    Anchors are matched by exact substring (whitespace-sensitive). An anchor
    that occurs more than once in the target is rejected — pick a longer,
    unique surrounding substring instead. ``append`` is the only op that
    does not require an anchor.
    """

    op: EditOp = Field(description="Edit operation.")
    anchor: str | None = Field(
        default=None,
        description=(
            "The existing substring to find. Required for "
            "replace/insert_before/insert_after/delete. Must match exactly "
            "(including whitespace) AND be unique. Null for `append`."
        ),
    )
    new_text: str | None = Field(
        default=None,
        description=("Replacement text (for replace) or text to insert/append. Null for `delete`."),
    )
    reason: str = Field(
        description="Why this edit. Short — operator/auditor reads this.",
    )
    targets_criterion: str | None = Field(
        default=None,
        description="Optional name of a criterion this edit targets.",
    )


class EditApplicationError(ValueError):
    """Raised when a single edit cannot be applied (anchor missing,
    ambiguous, or required field absent)."""


@dataclass
class ApplyResult:
    """Aggregate outcome of :func:`apply_edits`."""

    edits_applied: int = 0
    edits_skipped: int = 0
    skip_reasons: list[str] = field(default_factory=list)


def apply_one(current: str, edit: Edit) -> str:
    """Apply a single :class:`Edit` to ``current`` and return the new string.

    Raises :class:`EditApplicationError` on any failure (missing anchor,
    ambiguous anchor, missing required ``new_text``, unknown op).
    """
    if edit.op == "append":
        if edit.new_text is None:
            raise EditApplicationError("append requires new_text")
        return current + edit.new_text

    if edit.anchor is None:
        raise EditApplicationError(f"{edit.op} requires anchor")
    if edit.anchor not in current:
        raise EditApplicationError("anchor not found")
    occurrences = current.count(edit.anchor)
    if occurrences > 1:
        raise EditApplicationError(f"anchor ambiguous ({occurrences} matches)")

    if edit.op == "replace":
        if edit.new_text is None:
            raise EditApplicationError("replace requires new_text")
        return current.replace(edit.anchor, edit.new_text, 1)
    if edit.op == "delete":
        return current.replace(edit.anchor, "", 1)
    if edit.op == "insert_before":
        if edit.new_text is None:
            raise EditApplicationError("insert_before requires new_text")
        return current.replace(edit.anchor, edit.new_text + edit.anchor, 1)
    if edit.op == "insert_after":
        if edit.new_text is None:
            raise EditApplicationError("insert_after requires new_text")
        return current.replace(edit.anchor, edit.anchor + edit.new_text, 1)

    raise EditApplicationError(f"unknown op: {edit.op}")


def apply_edits(current: str, edits: list[Edit]) -> tuple[str, ApplyResult]:
    """Apply ``edits`` in order; valid edits land, invalid ones are skipped.

    Each failure is recorded in :attr:`ApplyResult.skip_reasons` as
    ``"edit[i] <op>: <message>"``. Remaining edits continue against the
    partially-edited text, matching the upstream Optimizer semantics.
    """
    text = current
    result = ApplyResult()
    for i, edit in enumerate(edits):
        try:
            text = apply_one(text, edit)
        except EditApplicationError as exc:
            result.edits_skipped += 1
            result.skip_reasons.append(f"edit[{i}] {edit.op}: {exc}")
        else:
            result.edits_applied += 1
    return text, result
