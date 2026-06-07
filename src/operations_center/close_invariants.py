# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations


NO_SALVAGE_PHRASE = "no salvage value"


def close_without_receipt_allowed(*, comment: str, durable_receipt_recorded: bool) -> bool:
    """Return True when an automated close satisfies the salvage invariant."""
    if durable_receipt_recorded:
        return True
    return NO_SALVAGE_PHRASE in comment.lower()
