# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Forgejo board adapter. See `docs/specs/forgejo-board-adapter.md`."""

from operations_center.adapters.forgejo.client import (
    KNOWN_STATES,
    PRIORITY_LABEL_PREFIX,
    STATE_LABEL_PREFIX,
    ForgejoClient,
    MultipleStatesError,
    UnknownStateError,
)

__all__ = [
    "KNOWN_STATES",
    "PRIORITY_LABEL_PREFIX",
    "STATE_LABEL_PREFIX",
    "ForgejoClient",
    "MultipleStatesError",
    "UnknownStateError",
]
