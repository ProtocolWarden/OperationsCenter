# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Execution-lineage — a derived read-model plus an attestation authority.

See ``docs/design/EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md``. This package
has two halves with deliberately separated roles:

* **Projection (read-model, derived):** ``projection.py`` reads signals the fleet
  already emits and joins them into per-task chains. It NEVER writes; it only
  *reads* the durable tier's ``durable_lineage_ids()`` to decide which edges are
  attested. This half upholds the spec's "lineage = derived, never authored."
* **Durable/integrity tier (attestation authority, authored):** ``durable.py`` +
  ``integrity.py`` are an append-only, hash-chained, authorship-bound ledger —
  genuinely a system of record (it establishes lineage ownership). It is the ONLY
  writer, and the projection treats it as just another source signal (identical
  to how it reads ``pr_reviews``). Keeping it here, with this one-directional
  boundary (projection reads, durable writes), is intentional: the "authority" is
  isolated to ``durable.py``/``integrity.py`` and never leaks into the projection.

An edge becomes *steerable* only once the durable tier attests it (``attested_trust``)
AND its provenance is code-computed; ``steering.steerable_facts`` is the single
sanctioned path a lane may plan from (free text stripped). Humans read
``LineageChain.display_view()``.
"""

from __future__ import annotations

from .durable import DurableLineageStore, lineage_id_for_task
from .integrity import (
    AuthorshipError,
    LedgerEntry,
    LineageLedger,
    chained_trust,
    entry_hash,
)
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
from .repograph_binding import to_repograph
from .steering import SteerableFact, steerable_facts

__all__ = [
    "AuthorshipError",
    "Completeness",
    "DurableLineageStore",
    "Integrity",
    "LedgerEntry",
    "LineageChain",
    "LineageEdge",
    "LineageLedger",
    "LineageNode",
    "Order",
    "Provenance",
    "SteerableFact",
    "TrustFlags",
    "build_all",
    "build_chain",
    "chained_trust",
    "entry_hash",
    "lineage_id_for_task",
    "steerable_facts",
    "to_repograph",
]
