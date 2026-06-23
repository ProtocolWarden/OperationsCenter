# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Execution-lineage projection — a derived, trust-labeled read-model.

See ``docs/design/EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md``. The package
reads signals the fleet already emits and joins them into per-task chains. It
never authors; it labels each edge's trust so the projection is honest about its
own untrustworthiness, and exposes a single sanctioned steering path.
"""

from __future__ import annotations

from .models import (
    Completeness,
    Integrity,
    LineageChain,
    LineageEdge,
    LineageNode,
    Order,
    Provenance,
    TrustFlags,
)
from .projection import build_all, build_chain
from .steering import SteerableFact, steerable_facts

__all__ = [
    "Completeness",
    "Integrity",
    "LineageChain",
    "LineageEdge",
    "LineageNode",
    "Order",
    "Provenance",
    "SteerableFact",
    "TrustFlags",
    "build_all",
    "build_chain",
    "steerable_facts",
]
