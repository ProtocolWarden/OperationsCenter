# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Out-of-band drift monitor — the independent critic lane (§4.2, D-EVAL-5).

The blocking gate (``replay.py``) grades the deterministic verdict *code*. This is
the complementary, **non-blocking** half: it grades the model's *check-extraction*
— given a case's diff/context, does a model still produce the per-check statuses
that compute to the answer? That is inherently non-deterministic, so it runs
N-of-M and must never gate (voting smooths the very onset-of-regression signal you
want to see, so a regression would hide behind the vote if it blocked).

Two independence rules from the design, enforced by construction here:

* **Different model family than the implementer.** N copies of one model is N=1 — a
  same-weights clone shares blindspots, so collusion is structural. The extractor
  is injected (``CheckExtractor``); the caller is responsible for wiring a
  *different-family* model. This lane never picks the model itself.
* **The critic only ever READS signed labels.** It compares against the corpus
  answer; it has no path to mutate it.

The extractor is a seam, not a hardcoded backend: production wires a real
different-family model adapter; tests inject a deterministic fake. Either way the
N-of-M aggregation and drift classification below are identical and testable."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from operations_center.entrypoints.pr_review_watcher.verdict import compute_verdict
from operations_center.eval.corpus import Case

# Corpus cases whose input carries a diff/context for the model to extract checks
# from (graded by THIS drift monitor, not the deterministic verdict gate).
EXTRACTION_KIND = "extraction"


class CheckExtractor(Protocol):
    """Produce the typed ``checks`` list for a case from a *different-family* model.

    ``vote`` lets the caller request M independent extractions; an implementation
    may vary sampling/seed per vote. Returns the same shape the reviewer model
    writes to ``verdict.json``: ``[{"check_id","status","evidence_span"}, ...]``."""

    def __call__(self, case: Case, *, vote: int) -> object: ...


@dataclass(frozen=True)
class DriftResult:
    case_id: str
    expected: object
    majority: object
    agree_votes: int
    total_votes: int
    drifted: bool
    detail: str = ""


def run_drift_monitor(
    cases: list[Case], extractor: CheckExtractor, *, votes: int = 3
) -> list[DriftResult]:
    """Replay each case through ``votes`` independent extractions; majority-vote the
    computed verdict and flag drift when the majority disagrees with the answer.

    Non-blocking by contract: this returns observations for a flagger/ticket, never
    a build-failing signal."""
    if votes < 1:
        raise ValueError("votes must be >= 1")
    out: list[DriftResult] = []
    for case in cases:
        tally: Counter[str] = Counter()
        rendered: dict[str, object] = {}
        for v in range(votes):
            result, failing = compute_verdict(extractor(case, vote=v))
            key = f"{result}|{','.join(sorted(failing))}"
            tally[key] += 1
            rendered[key] = {"result": result, "failing": sorted(failing)}
        top_key, agree = tally.most_common(1)[0]
        majority = rendered[top_key]
        gt = case.ground_truth
        expected = {"result": gt.get("result"), "failing": sorted(gt.get("failing", []) or [])}
        drifted = majority != expected
        detail = "" if not drifted else f"majority {majority} != answer {expected}"
        out.append(
            DriftResult(case.case_id, expected, majority, agree, votes, drifted, detail)
        )
    return out


__all__ = ["EXTRACTION_KIND", "CheckExtractor", "DriftResult", "run_drift_monitor"]
