# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden

from __future__ import annotations

from operations_center.close_invariants import close_without_receipt_allowed


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
