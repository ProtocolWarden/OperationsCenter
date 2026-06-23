# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/models.py — data model for the execution-lineage projection.

A lineage chain is a *derived read-model* over signals the fleet already emits
(run artifacts, pr_reviews state, ci_lineage). It is never authored directly.

Every node and edge carries a four-dimension trust label. The binding rule from
``docs/design/EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md`` §2: an edge may be
consumed as a *steering* input by a lane only when all four dimensions are green
(``code-computed ∧ chained ∧ durable ∧ causal``). Everything else is
display-only and must never enter a lane's planning prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Provenance(str, Enum):
    """How the edge's content was produced."""

    CODE_COMPUTED = "code-computed"  # derived from typed, code-computed signal
    TEXT_DERIVED = "text-derived"  # touched free text (goal/summary/issue body)


class Integrity(str, Enum):
    """Whether the edge's authorship/content is cryptographically attested."""

    CHAINED = "chained"  # hash-chained + authorship-bound (Phase D1)
    UNVERIFIED = "unverified"


class Completeness(str, Enum):
    """Whether the source record still exists within its retention window."""

    DURABLE = "durable"  # source persisted in a tier outliving the projection
    EXPIRED = "expired"  # source GC'd / missing → rebuild would not reproduce it


class Order(str, Enum):
    """Whether the edge has a defined causal position across hosts."""

    CAUSAL = "causal"  # logical-clock / monotonic sequence stamped at capture
    HOST_RELATIVE = "host-relative"  # only commit-apply order; not a total order


@dataclass(frozen=True)
class TrustFlags:
    """The four orthogonal trust dimensions of a lineage node or edge."""

    provenance: Provenance
    integrity: Integrity
    completeness: Completeness
    order: Order

    def is_steerable(self) -> bool:
        """True iff the edge may be consumed as a steering input by a lane.

        All four dimensions must be green. This is the code-level enforcement of
        the typed-steering / display-only split — a lane planning prompt filters
        on this predicate, so a text-derived, unverified, expired, or
        host-relative edge can never silently steer the fleet.
        """

        return (
            self.provenance is Provenance.CODE_COMPUTED
            and self.integrity is Integrity.CHAINED
            and self.completeness is Completeness.DURABLE
            and self.order is Order.CAUSAL
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "provenance": self.provenance.value,
            "integrity": self.integrity.value,
            "completeness": self.completeness.value,
            "order": self.order.value,
        }


# Until Phase D1 (hash chain + authorship) and the ordering work land, NOTHING is
# steerable — the honest, safe default the spec requires. A lane reading lineage
# today gets an empty steerable set, exactly as intended.
def default_trust(
    *,
    provenance: Provenance,
    completeness: Completeness = Completeness.DURABLE,
) -> TrustFlags:
    """Construct trust flags with the not-yet-attested dimensions pinned red.

    Used for edges NOT backed by the durable tier: integrity is unverified (no
    chain) and order is host-relative (no per-lineage sequence), so such an edge
    can never be steerable regardless of provenance — the safe default.
    """

    return TrustFlags(
        provenance=provenance,
        integrity=Integrity.UNVERIFIED,
        completeness=completeness,
        order=Order.HOST_RELATIVE,
    )


def attested_trust(provenance: Provenance) -> TrustFlags:
    """Trust flags for an edge BACKED BY THE DURABLE TIER (Phase A5/D1).

    The durable ledger is hash-chained (→ ``integrity=CHAINED``), append-only and
    retained past source GC (→ ``completeness=DURABLE``), and establishes a
    monotonic per-lineage order via the chain itself (→ ``order=CAUSAL``). So an
    attested edge is steerable iff its ``provenance`` is ``CODE_COMPUTED`` — the
    one dimension the durable tier cannot vouch for. This is what makes the
    four-dimension model functional: without it, ``order`` never goes CAUSAL and
    ``is_steerable()`` is unreachable by construction.
    """

    return TrustFlags(
        provenance=provenance,
        integrity=Integrity.CHAINED,
        completeness=Completeness.DURABLE,
        order=Order.CAUSAL,
    )


@dataclass(frozen=True)
class LineageNode:
    """One node in a task's lineage chain."""

    node_id: str
    kind: str  # task | proposal | run | pr | verdict | merge
    trust: TrustFlags
    attributes: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "trust": self.trust.as_dict(),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class LineageEdge:
    """A directed, trust-labeled edge between two lineage nodes."""

    src: str
    dst: str
    kind: str  # proposed | executed_as | produced_pr | reviewed_as | merged_as
    trust: TrustFlags

    def as_dict(self) -> dict[str, object]:
        return {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind,
            "trust": self.trust.as_dict(),
        }


@dataclass(frozen=True)
class LineageChain:
    """The full derived lineage for a single task_id."""

    task_id: str
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]

    def steerable_edges(self) -> tuple[LineageEdge, ...]:
        """Edges a lane MAY plan from. Display surfaces use all edges."""

        return tuple(e for e in self.edges if e.trust.is_steerable())

    def display_edges(self) -> tuple[LineageEdge, ...]:
        """Edges that are NOT admissible for steering (render greyed/flagged)."""

        return tuple(e for e in self.edges if not e.trust.is_steerable())

    def display_view(self) -> dict[str, object]:
        """The sanctioned HUMAN/display representation (may contain free text).

        Two readers, two methods, by design (the typed-steering / display-only
        split, spec §2): a human/auditor calls ``display_view()`` and may see
        free-text attributes; a LANE that plans from lineage MUST call
        ``operations_center.lineage.steering.steerable_facts`` instead, which
        emits only typed, allowlisted fields. Reading ``.nodes``/``.edges`` raw
        for *planning* is a misuse — those carry display attributes (incl. the
        attacker-controllable goal text) and are not a steering surface.
        """

        return self.as_dict()

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "nodes": [n.as_dict() for n in self.nodes],
            "edges": [e.as_dict() for e in self.edges],
        }
