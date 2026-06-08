# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden

from __future__ import annotations

from operations_center.close_invariants import (
    branch_delete_allowed_after_close,
    close_comment_claims_preserved_work,
    close_without_receipt_allowed,
    has_no_salvage_justification,
)


def test_close_without_receipt_allows_durable_receipt() -> None:
    assert (
        close_without_receipt_allowed(
            comment="receipt recorded elsewhere",
            durable_receipt_recorded=True,
        )
        is True
    )


def test_close_without_receipt_requires_no_salvage_phrase_without_receipt() -> None:
    assert (
        close_without_receipt_allowed(
            comment="Auto-closing with no salvage value: stale autonomy PR.",
            durable_receipt_recorded=False,
        )
        is True
    )
    assert (
        close_without_receipt_allowed(
            comment="closing stale PR without explicit salvage justification",
            durable_receipt_recorded=False,
        )
        is False
    )


def test_no_salvage_requires_inline_justification() -> None:
    assert has_no_salvage_justification("Auto-closing with no salvage value: stale autonomy PR.")
    assert has_no_salvage_justification("No salvage value - superseded by merged fix.")
    assert has_no_salvage_justification("NO SALVAGE VALUE: duplicate branch with no unique commits.")
    assert not has_no_salvage_justification("Auto-closing with no salvage value")
    assert not has_no_salvage_justification("Auto-closing. no salvage value")


def test_branch_delete_disallowed_when_comment_claims_preserved_work() -> None:
    comment = "Closing without merge. Work preserved on branch `goal/42`; re-queued elsewhere."
    assert close_comment_claims_preserved_work(comment) is True
    assert (
        branch_delete_allowed_after_close(
            comment=comment,
            durable_receipt_recorded=True,
        )
        is False
    )
    assert (
        branch_delete_allowed_after_close(
            comment="Durable receipt recorded on Plane task `task-1`.",
            durable_receipt_recorded=True,
        )
        is True
    )
