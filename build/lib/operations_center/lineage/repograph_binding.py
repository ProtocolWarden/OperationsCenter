# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/repograph_binding.py — express a lineage chain in RepoGraph terms (A3).

Maps a ``LineageChain`` into RepoGraph's existing RUN / AUDIT / EVIDENCE node
vocabulary and ontology edges, so the lineage read-model can be merged into the
projection plane rather than living as a bespoke side structure (spec §1.2, A3).

**Boundary-respecting export.** OperationsCenter's sanctioned dependency is
``platform_manifest`` (it consumes the *EffectiveRepoGraph*), not the lower-level
``repograph`` package — and ``platform_manifest`` does not re-export the
RUN/AUDIT/EVIDENCE ``EntityKind`` vocabulary. So rather than import ``repograph``
directly (which would cross an undeclared cross-repo edge — Custodian X2), this
emits **RepoGraph-shaped dicts** using the kind *names* as strings. The manifest
side, which does have ``repograph``, can hydrate these into ``RepoIdentity`` /
``GraphEdge`` objects. This keeps OC decoupled from repograph internals.

Two deliberate properties carry over: every node/edge is stamped
``source="work_scope"`` + ``metadata["derived"]="true"`` (never mistakable for a
hand-authored platform fact), and each one's four trust dimensions are carried in
``metadata`` verbatim so a downstream consumer sees the same honest
steerable/display-only split.
"""

from __future__ import annotations

from .models import LineageChain

# lineage node.kind → RepoGraph EntityKind value. RepoGraph has no "task"/"pr"
# kind; map to the closest execution-lineage primitive it does have.
_KIND_TO_ENTITY = {
    "task": "Run",
    "run": "Run",
    "pr": "Evidence",
    "verdict": "Audit",
    "merge": "Evidence",
}

# lineage edge.kind → RepoGraph EdgeKind value.
_EDGE_TO_KIND = {
    "executed_as": "orchestrates",
    "produced_pr": "produces_artifact",
    "reviewed_as": "validates_output",
    "merged_as": "indexes_output",
}


def _trust_metadata(trust) -> dict[str, str]:
    md = {f"trust.{k}": v for k, v in trust.as_dict().items()}
    md["steerable"] = "true" if trust.is_steerable() else "false"
    return md


def to_repograph(chain: LineageChain) -> tuple[list[dict], list[dict]]:
    """Return ``(nodes, relationships)`` as RepoGraph-shaped dicts representing
    the lineage chain. Derived/read-only; no ``repograph`` import."""

    nodes: list[dict] = []
    for n in chain.nodes:
        metadata = {"derived": "true", "lineage_kind": n.kind, **_trust_metadata(n.trust)}
        nodes.append(
            {
                "repo_id": n.node_id,
                "canonical_name": n.node_id,
                "kind": _KIND_TO_ENTITY.get(n.kind, "Run"),
                "source": "work_scope",
                "metadata": metadata,
            }
        )

    relationships: list[dict] = []
    for e in chain.edges:
        metadata = {"derived": "true", "lineage_edge": e.kind, **_trust_metadata(e.trust)}
        relationships.append(
            {
                "relationship_id": f"{e.src}->{e.dst}:{e.kind}",
                "source_id": e.src,
                "target_id": e.dst,
                "kind": _EDGE_TO_KIND.get(e.kind, "reports_to"),
                "source": "work_scope",
                "metadata": metadata,
            }
        )
    return nodes, relationships


__all__ = ["to_repograph"]
