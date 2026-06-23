# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/steering.py — the ONLY sanctioned path for a lane to read lineage.

This module enforces the typed-steering / display-only split (spec §2). A lane
that wants prior causality to choose its next branch must call
``steerable_facts`` — which returns *typed, code-computed, steerable* facts only,
never free text. Free-text attributes (goal_text, verdict summaries) are stripped
here so an attacker-controllable issue body cannot become a steering input by
laundering through the lineage projection.

If a lane reaches into ``LineageChain.nodes`` directly for planning, that is a
bug; the projection's attributes are display-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import LineageChain

# Attributes that are structural/typed and safe to surface to a planner. Anything
# not on this allowlist (notably goal_text and any *_summary) is withheld.
_STEERABLE_ATTR_ALLOWLIST: dict[str, frozenset[str]] = {
    "run": frozenset({"run_id", "status", "success", "selected_lane", "failure_category"}),
    "pr": frozenset({"pr_number", "phase", "head_sha"}),
    "verdict": frozenset({"result", "failing_checks"}),
}


@dataclass(frozen=True)
class SteerableFact:
    """A typed, attested fact a lane may plan from."""

    edge_kind: str
    src_kind: str
    dst_kind: str
    attributes: dict[str, object]


def steerable_facts(chain: LineageChain) -> tuple[SteerableFact, ...]:
    """Return only the facts a lane is permitted to plan from.

    A fact is emitted only when its edge ``is_steerable()`` (all four trust
    dimensions green) AND both endpoints are typed node kinds. Free-text
    attributes are stripped via the allowlist. Today this returns an empty tuple
    for every chain — integrity (D1) and ordering are not yet built, so no edge
    is steerable. That is the intended safe default, not a defect.
    """

    nodes_by_id = {n.node_id: n for n in chain.nodes}
    facts: list[SteerableFact] = []
    for edge in chain.steerable_edges():
        src = nodes_by_id.get(edge.src)
        dst = nodes_by_id.get(edge.dst)
        if src is None or dst is None:
            continue
        allow = _STEERABLE_ATTR_ALLOWLIST.get(dst.kind, frozenset())
        attrs = {k: v for k, v in dst.attributes.items() if k in allow}
        facts.append(
            SteerableFact(
                edge_kind=edge.kind,
                src_kind=src.kind,
                dst_kind=dst.kind,
                attributes=attrs,
            )
        )
    return tuple(facts)
