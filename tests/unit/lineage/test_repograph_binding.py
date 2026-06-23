# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the RepoGraph binding (Phase A3).

The lineage read-model maps into RepoGraph's RUN/AUDIT/EVIDENCE vocabulary,
stamped as derived (WORK_SCOPE) with trust preserved in metadata.
"""

from __future__ import annotations

from operations_center.lineage.models import (
    Completeness,
    Integrity,
    LineageChain,
    LineageEdge,
    LineageNode,
    Order,
    Provenance,
    TrustFlags,
    default_trust,
)
from operations_center.lineage.repograph_binding import to_repograph


def _green() -> TrustFlags:
    return TrustFlags(
        provenance=Provenance.CODE_COMPUTED,
        integrity=Integrity.CHAINED,
        completeness=Completeness.DURABLE,
        order=Order.CAUSAL,
    )


def _chain() -> LineageChain:
    nodes = (
        LineageNode("task:t", "task", default_trust(provenance=Provenance.TEXT_DERIVED)),
        LineageNode("run:r", "run", default_trust(provenance=Provenance.CODE_COMPUTED)),
        LineageNode("pr:1", "pr", default_trust(provenance=Provenance.CODE_COMPUTED)),
        LineageNode("verdict:1", "verdict", _green()),
    )
    edges = (
        LineageEdge("task:t", "run:r", "executed_as", default_trust(provenance=Provenance.CODE_COMPUTED)),
        LineageEdge("run:r", "pr:1", "produced_pr", default_trust(provenance=Provenance.CODE_COMPUTED)),
        LineageEdge("pr:1", "verdict:1", "reviewed_as", _green()),
    )
    return LineageChain(task_id="t", nodes=nodes, edges=edges)


def test_nodes_map_to_run_audit_evidence_kinds():
    nodes, _ = to_repograph(_chain())
    by_id = {n.repo_id: n.kind.value for n in nodes}
    assert by_id["run:r"] == "Run"
    assert by_id["verdict:1"] == "Audit"
    assert by_id["pr:1"] == "Evidence"
    assert by_id["task:t"] == "Run"


def test_all_nodes_marked_derived_work_scope():
    nodes, _ = to_repograph(_chain())
    for n in nodes:
        assert n.source.value == "work_scope"
        assert ("derived", "true") in n.metadata


def test_edges_map_and_preserve_trust():
    _, rels = to_repograph(_chain())
    by_pair = {(r.source_id, r.target_id): r for r in rels}
    reviewed = by_pair[("pr:1", "verdict:1")]
    assert reviewed.kind.value == "validates_output"
    # the green edge is marked steerable in metadata
    assert ("steerable", "true") in reviewed.metadata
    executed = by_pair[("task:t", "run:r")]
    assert executed.kind.value == "orchestrates"
    # default-trust edge is NOT steerable
    assert ("steerable", "false") in executed.metadata


def test_trust_dimensions_carried_into_metadata():
    nodes, _ = to_repograph(_chain())
    verdict = next(n for n in nodes if n.repo_id == "verdict:1")
    md = dict(verdict.metadata)
    assert md["trust.provenance"] == "code-computed"
    assert md["trust.integrity"] == "chained"


def test_empty_chain_yields_empty():
    nodes, rels = to_repograph(LineageChain(task_id="t", nodes=(), edges=()))
    assert nodes == [] and rels == []
