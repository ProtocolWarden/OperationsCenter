# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/repograph_binding.py — express a lineage chain in RepoGraph terms (A3).

Maps a ``LineageChain`` into RepoGraph's existing RUN / AUDIT / EVIDENCE node
vocabulary and ontology edges, so the lineage read-model can live in the
projection plane rather than as a bespoke side structure (spec §1.2, A3).

Two deliberate properties:

* **Derived, never authored.** Every node/edge is stamped ``Source.WORK_SCOPE``
  and carries ``metadata[("derived", "true")]`` so it can never be mistaken for a
  hand-authored platform fact. The binding does NOT call ``RepoGraph.build`` —
  lineage nodes are not repositories and must not be subjected to (or pollute)
  topology validation; it returns the RepoGraph-native building blocks for a
  consumer to merge into a projection.
* **Trust is preserved.** Each node/edge's four trust dimensions are carried into
  ``metadata`` verbatim, so a downstream RepoGraph consumer sees the same honest
  steerable/display-only split the native chain exposes.

Imports of ``repograph`` are lazy (inside the function) so the core lineage
package stays importable in environments without the manifest library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import LineageChain

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

# lineage node.kind → RepoGraph EntityKind name. RepoGraph has no "task"/"pr"
# kind; map to the closest execution-lineage primitive it does have.
_KIND_TO_ENTITY = {
    "task": "RUN",
    "run": "RUN",
    "pr": "EVIDENCE",
    "verdict": "AUDIT",
    "merge": "EVIDENCE",
}

# lineage edge.kind → RepoGraph EdgeKind name.
_EDGE_TO_KIND = {
    "executed_as": "ORCHESTRATES",
    "produced_pr": "PRODUCES_ARTIFACT",
    "reviewed_as": "VALIDATES_OUTPUT",
    "merged_as": "INDEXES_OUTPUT",
}


def _trust_metadata(trust) -> list[tuple[str, str]]:
    d = trust.as_dict()
    return [(f"trust.{k}", v) for k, v in d.items()] + [
        ("steerable", "true" if trust.is_steerable() else "false")
    ]


def to_repograph(chain: LineageChain) -> tuple[list[Any], list[Any]]:
    """Return ``(nodes, relationships)`` as RepoGraph ``RepoIdentity`` +
    ``GraphEdge`` objects representing the lineage chain. Derived/read-only."""

    from repograph.ontology.enums import EntityKind, Source
    from repograph.ontology.models import RepoIdentity
    from repograph.topology.edges import EdgeKind
    from repograph.topology.models import GraphEdge

    nodes: list[Any] = []
    for n in chain.nodes:
        entity = EntityKind[_KIND_TO_ENTITY.get(n.kind, "RUN")]
        meta = [("derived", "true"), ("lineage_kind", n.kind), *_trust_metadata(n.trust)]
        nodes.append(
            RepoIdentity(
                repo_id=n.node_id,
                canonical_name=n.node_id,
                kind=entity,
                source=Source.WORK_SCOPE,
                metadata=tuple(meta),
            )
        )

    relationships: list[Any] = []
    for e in chain.edges:
        edge_kind = EdgeKind[_EDGE_TO_KIND.get(e.kind, "REPORTS_TO")]
        meta = [("derived", "true"), ("lineage_edge", e.kind), *_trust_metadata(e.trust)]
        relationships.append(
            GraphEdge(
                relationship_id=f"{e.src}->{e.dst}:{e.kind}",
                source_id=e.src,
                target_id=e.dst,
                kind=edge_kind,
                source=Source.WORK_SCOPE,
                metadata=tuple(meta),
            )
        )
    return nodes, relationships


__all__ = ["to_repograph"]
