# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""C3 — cross-family EVAL panel aggregation (COUNCIL_VERDICT.md C3, D-EVAL-5).

``critic.run_drift_monitor`` grades a case with a SINGLE injected extractor —
today that extractor is (by construction, never enforced) some non-implementer
family. The guide-gap audit's same-family-generator/evaluator finding applies
here just as much as it does to the C1 merge council: a lone extractor, or a
naive pooled vote across several extractors, lets a dominant family's answer
outvote a different family's disagreement — exactly the collusion-by-shared-
blindspot the cross-family requirement exists to prevent.

This module is the grading-specific analogue of ``verdict.aggregate_council``
— but council's unanimous-LGTM/merge shape is the wrong fit here (grading is
never a merge decision, and it must never gate — see ``critic`` module intro).
Instead: each configured family votes ``votes`` times **on its own**; each
family's OWN majority is compared against the signed answer; the case is
flagged drifted if **any single family's** majority disagrees — regardless of
how many other families (or how many total votes) agree. A 2-seat family can
never out-vote a 1-seat family's dissent because votes are never pooled across
families for the drift decision — only within a family, to compute that
family's own majority.

Pure and injectable (families passed in as ``CheckExtractor``s), same shape as
``critic.run_drift_monitor``, so tests can inject deterministic fakes instead
of live models."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Mapping

from operations_center.entrypoints.pr_review_watcher.verdict import compute_verdict
from operations_center.eval.corpus import Case
from operations_center.eval.critic import CheckExtractor, DriftResult


@dataclass(frozen=True)
class PanelDriftResult(DriftResult):
    """``DriftResult`` extended with the per-family breakdown that makes the
    cross-family grading control auditable — which family (if any) drifted,
    not just whether the panel as a whole did.

    The inherited ``majority``/``agree_votes``/``total_votes`` fields keep
    ``DriftResult``'s original meaning (the panel's votes pooled as one big
    N-of-M tally, for continuity with the single-extractor report shape).
    ``drifted`` does NOT come from that pooled tally, though — see
    ``run_panel_drift_monitor``. ``per_family`` is the authoritative signal:
    ``{family: {"majority": {...}, "agree_votes": int, "total_votes": int}}``.
    """

    per_family: dict[str, dict[str, object]] = field(default_factory=dict)


def run_panel_drift_monitor(
    cases: list[Case],
    family_extractors: Mapping[str, CheckExtractor],
    *,
    votes: int = 3,
) -> list[PanelDriftResult]:
    """Replay each case through every family's extractor independently;
    flag drift when ANY family's own majority disagrees with the signed answer.

    ``family_extractors`` maps a family tag (e.g. ``"claude_code"``,
    ``"codex_cli"``) to a ``CheckExtractor`` for that family. Each family gets
    its own independent ``votes``-vote majority (never pooled with another
    family's votes for the drift decision) — that per-family isolation is what
    stops a dominant/larger family from masking its own drift by outvoting a
    smaller one.

    Raises ``ValueError`` on ``votes < 1`` or an empty ``family_extractors``
    (an empty panel has no cross-family control to speak of — the caller
    is expected to treat "no panel configured" as feature-OFF *before*
    calling this, not by handing it zero families)."""
    if votes < 1:
        raise ValueError("votes must be >= 1")
    if not family_extractors:
        raise ValueError("family_extractors must be non-empty")

    out: list[PanelDriftResult] = []
    for case in cases:
        gt = case.ground_truth
        expected = {"result": gt.get("result"), "failing": sorted(gt.get("failing", []) or [])}

        per_family: dict[str, dict[str, object]] = {}
        pooled_tally: Counter[str] = Counter()
        pooled_rendered: dict[str, dict[str, object]] = {}
        drifted_families: list[str] = []

        for family, extractor in family_extractors.items():
            tally: Counter[str] = Counter()
            rendered: dict[str, dict[str, object]] = {}
            for v in range(votes):
                result, failing = compute_verdict(extractor(case, vote=v))
                key = f"{result}|{','.join(sorted(failing))}"
                tally[key] += 1
                rendered[key] = {"result": result, "failing": sorted(failing)}
                pooled_tally[key] += 1
                pooled_rendered[key] = rendered[key]

            top_key, agree = tally.most_common(1)[0]
            majority = rendered[top_key]
            per_family[family] = {
                "majority": majority,
                "agree_votes": agree,
                "total_votes": votes,
            }
            if majority != expected:
                drifted_families.append(f"{family} majority {majority} != answer {expected}")

        pooled_top_key, pooled_agree = pooled_tally.most_common(1)[0]
        pooled_majority = pooled_rendered[pooled_top_key]
        drifted = bool(drifted_families)
        detail = "; ".join(drifted_families)

        out.append(
            PanelDriftResult(
                case_id=case.case_id,
                expected=expected,
                majority=pooled_majority,
                agree_votes=pooled_agree,
                total_votes=votes * len(family_extractors),
                drifted=drifted,
                detail=detail,
                per_family=per_family,
            )
        )
    return out


__all__ = ["PanelDriftResult", "run_panel_drift_monitor"]
