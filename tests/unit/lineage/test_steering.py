# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the sanctioned lane steering path (typed-only, free-text stripped)."""

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
from operations_center.lineage.steering import steerable_facts


def _green() -> TrustFlags:
    return TrustFlags(
        provenance=Provenance.CODE_COMPUTED,
        integrity=Integrity.CHAINED,
        completeness=Completeness.DURABLE,
        order=Order.CAUSAL,
    )


def test_no_steerable_facts_when_edges_unverified():
    # Default trust → nothing steerable → empty facts (the safe default).
    node = LineageNode("v:1", "verdict", default_trust(provenance=Provenance.CODE_COMPUTED))
    edge = LineageEdge("pr:1", "v:1", "reviewed_as", default_trust(provenance=Provenance.CODE_COMPUTED))
    chain = LineageChain(task_id="t", nodes=(node,), edges=(edge,))
    assert steerable_facts(chain) == ()


def test_steerable_fact_emitted_for_green_edge_with_allowlisted_attrs():
    verdict = LineageNode(
        "v:1",
        "verdict",
        _green(),
        attributes={"result": "LGTM", "failing_checks": [], "summary": "looks great"},
    )
    pr = LineageNode("pr:1", "pr", _green())
    edge = LineageEdge("pr:1", "v:1", "reviewed_as", _green())
    chain = LineageChain(task_id="t", nodes=(pr, verdict), edges=(edge,))

    facts = steerable_facts(chain)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.edge_kind == "reviewed_as"
    assert fact.dst_kind == "verdict"
    # typed allowlisted attrs survive...
    assert fact.attributes["result"] == "LGTM"
    # ...free-text 'summary' is stripped (not on the allowlist)
    assert "summary" not in fact.attributes


def test_free_text_goal_never_reaches_a_fact_even_if_edge_is_green():
    # A task node's goal_text is free text; even a (hypothetically) green edge
    # into it must not surface the goal — task isn't an allowlisted dst kind.
    task = LineageNode("task:t", "task", _green(), attributes={"goal_text": "SECRET"})
    edge = LineageEdge("task:t", "task:t", "self", _green())
    chain = LineageChain(task_id="t", nodes=(task,), edges=(edge,))
    facts = steerable_facts(chain)
    assert all("goal_text" not in f.attributes for f in facts)


def test_dangling_edge_endpoints_are_skipped():
    edge = LineageEdge("missing-src", "missing-dst", "x", _green())
    chain = LineageChain(task_id="t", nodes=(), edges=(edge,))
    assert steerable_facts(chain) == ()
