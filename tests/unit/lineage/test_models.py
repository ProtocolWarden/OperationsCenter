# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the lineage trust model (TrustFlags steerability + serialization)."""

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


def _green() -> TrustFlags:
    return TrustFlags(
        provenance=Provenance.CODE_COMPUTED,
        integrity=Integrity.CHAINED,
        completeness=Completeness.DURABLE,
        order=Order.CAUSAL,
    )


def test_all_green_is_steerable():
    assert _green().is_steerable()


def test_any_red_dimension_blocks_steering():
    base = _green()
    for field, red in (
        ("provenance", Provenance.TEXT_DERIVED),
        ("integrity", Integrity.UNVERIFIED),
        ("completeness", Completeness.EXPIRED),
        ("order", Order.HOST_RELATIVE),
    ):
        flags = TrustFlags(**{**base.__dict__, field: red})
        assert not flags.is_steerable(), field


def test_default_trust_is_never_steerable():
    # The safe default: integrity unverified + order host-relative.
    flags = default_trust(provenance=Provenance.CODE_COMPUTED)
    assert flags.integrity is Integrity.UNVERIFIED
    assert flags.order is Order.HOST_RELATIVE
    assert not flags.is_steerable()


def test_trustflags_as_dict_roundtrips_values():
    d = _green().as_dict()
    assert d == {
        "provenance": "code-computed",
        "integrity": "chained",
        "completeness": "durable",
        "order": "causal",
    }


def test_chain_partitions_steerable_and_display_edges():
    steer = LineageEdge("a", "b", "x", _green())
    display = LineageEdge("a", "c", "y", default_trust(provenance=Provenance.CODE_COMPUTED))
    chain = LineageChain(
        task_id="t",
        nodes=(LineageNode("a", "task", _green()),),
        edges=(steer, display),
    )
    assert chain.steerable_edges() == (steer,)
    assert chain.display_edges() == (display,)


def test_node_and_chain_serialize():
    node = LineageNode("n", "run", _green(), attributes={"k": "v"})
    assert node.as_dict()["kind"] == "run"
    chain = LineageChain(task_id="t", nodes=(node,), edges=())
    payload = chain.as_dict()
    assert payload["task_id"] == "t"
    assert payload["nodes"][0]["attributes"] == {"k": "v"}


def test_display_view_vs_steering_split():
    # A2: humans see free text via display_view; lanes must use steerable_facts
    # which strips it. Pin both halves of the split in one place.
    from operations_center.lineage.steering import steerable_facts

    task = LineageNode("task:t", "task", default_trust(provenance=Provenance.TEXT_DERIVED),
                       attributes={"goal_text": "SECRET-MARKER"})
    chain = LineageChain(task_id="t", nodes=(task,), edges=())
    # human display surfaces the free text...
    assert "SECRET-MARKER" in __import__("json").dumps(chain.display_view(), default=str)
    # ...but the steering path never does
    assert "SECRET-MARKER" not in __import__("json").dumps(
        [f.attributes for f in steerable_facts(chain)], default=str
    )
