# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Component 2 — outcome correlation as a FLAGGER, not a metric (§4.2, D-EVAL-1).

Reviewer decisions can be correlated against downstream outcomes — did an LGTM that
merged go on to regress main? did a CONCERNS requeue to exhaustion? — but that data
is **downstream-contaminated** (CI flakiness, sibling PRs, infra) and, worse,
*anti-correlated with truth in exactly the LGTM-happy-regression failure mode (#313)
you most need to catch*. A precision/recall number computed from it is confidently
wrong precisely there, and a naive over-flag penalty creates a gradient *toward*
that failure. So this module is forbidden from ever producing such a number.

What it produces instead: **disagreement tickets** for operator adjudication. Each
is a single (PR, kind, attribution) flag a human looks at — and, if it reveals a
real reviewer miss, turns into a signed corpus case. Two correlations:

* ``lgtm_then_regression`` — an LGTM merged and main regressed within the window.
  Attributed to the **reviewer** (a possible missed concern).
* ``requeue_to_death`` — a PR requeued until exhaustion. Attributed to the
  **worker**, NOT the reviewer (D-EVAL-4): charging the reviewer for the worker's
  non-convergence is exactly the LGTM-happy gradient. It is surfaced as a
  worker-non-convergence signal, not a reviewer over-flag.

The outcome data itself comes from an injected :class:`OutcomeSource` seam (reviewer
instrumentation + post-merge regression); with none wired the task skips — no data,
no false tickets (§0.1 fail-safe)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

LGTM = "LGTM"
CONCERNS = "CONCERNS"

KIND_LGTM_REGRESSION = "lgtm_then_regression"
KIND_REQUEUE_TO_DEATH = "requeue_to_death"

ATTRIB_REVIEWER = "reviewer"
ATTRIB_WORKER = "worker"


@dataclass(frozen=True)
class ReviewOutcome:
    """One reviewed PR plus the downstream signals correlated against its decision."""

    pr_number: int
    decision: str  # LGTM | CONCERNS
    merged: bool = False
    main_regressed: bool = False  # post-merge regression observed within the window
    requeued_to_death: bool = False  # requeued until the escalation budget was spent
    repo: str = ""


@dataclass(frozen=True)
class Disagreement:
    pr_number: int
    kind: str
    attribution: str
    detail: str
    repo: str = ""

    @property
    def dedup_key(self) -> str:
        return f"{self.repo}#{self.pr_number}:{self.kind}"


class OutcomeSource(Protocol):
    """Yield the recent review outcomes to correlate. Injected so the correlation is
    testable without a live instrumentation/regression backend; the production
    adapter joins reviewer-decision instrumentation with post-merge regression."""

    def __call__(self) -> list[ReviewOutcome]: ...


def flag_disagreements(outcomes: list[ReviewOutcome]) -> list[Disagreement]:
    """Correlate decisions with outcomes → adjudication tickets.

    NEVER returns a precision/recall figure (D-EVAL-1): outcome data is
    downstream-contaminated and anti-correlated with truth in the LGTM-happy mode.
    This is an anomaly *flagger*, valid only as a pointer for a human to adjudicate."""
    flags: list[Disagreement] = []
    for o in outcomes:
        if o.decision == LGTM and o.merged and o.main_regressed:
            flags.append(
                Disagreement(
                    o.pr_number,
                    KIND_LGTM_REGRESSION,
                    ATTRIB_REVIEWER,
                    "LGTM merged then main regressed within the window — possible "
                    "missed concern; adjudicate and, if real, add a signed corpus case",
                    repo=o.repo,
                )
            )
        if o.requeued_to_death:
            flags.append(
                Disagreement(
                    o.pr_number,
                    KIND_REQUEUE_TO_DEATH,
                    ATTRIB_WORKER,
                    "Requeued to exhaustion — attribute to WORKER non-convergence "
                    "(D-EVAL-4), not reviewer over-flag",
                    repo=o.repo,
                )
            )
    return flags


__all__ = [
    "ATTRIB_REVIEWER",
    "ATTRIB_WORKER",
    "CONCERNS",
    "KIND_LGTM_REGRESSION",
    "KIND_REQUEUE_TO_DEATH",
    "LGTM",
    "Disagreement",
    "OutcomeSource",
    "ReviewOutcome",
    "flag_disagreements",
]
