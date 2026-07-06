# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Replay harness — grade corpus cases against the code-computed verdict.

HARNESS_TRUST_HARDENING §4.2 Component 3 (D-EVAL-5): the **blocking** gate is an
exact-match of the *code-computed* verdict (``pr_review_watcher.verdict``) against
the committed corpus answer. The verdict layer is pure, deterministic code — no
model — so this gate has zero flakiness and catches a regression in the decision
logic itself (e.g. re-introducing the #313 retraction where a forged field flips
the merge decision). Real-model check-extraction is the *separate, non-blocking*
drift monitor (see ``critic.py``); voting smooths onset-of-regression variance and
so must never gate.

Only **graded** (operator-signed) cases count toward the gate. Unsigned candidate
cases are still replayed and reported — so the fleet sees them — but a candidate
can never move the gate until an operator signs it once (the answer-key/exam split
from §4.2)."""

from __future__ import annotations

from dataclasses import dataclass

from operations_center.entrypoints.pr_review_watcher.verdict import compute_verdict
from operations_center.eval.corpus import Case

VERDICT_KIND = "verdict"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: str
    graded: bool
    passed: bool
    expected: object
    actual: object
    detail: str = ""


@dataclass(frozen=True)
class ReplayReport:
    results: list[CaseResult]

    @property
    def graded(self) -> list[CaseResult]:
        return [r for r in self.results if r.graded]

    @property
    def candidates(self) -> list[CaseResult]:
        return [r for r in self.results if not r.graded]

    @property
    def graded_pass_rate(self) -> float:
        g = self.graded
        return 1.0 if not g else sum(r.passed for r in g) / len(g)

    @property
    def gate_ok(self) -> bool:
        """The blocking signal: every GRADED case must pass. Candidates never gate."""
        return all(r.passed for r in self.graded)

    def failures(self) -> list[CaseResult]:
        return [r for r in self.graded if not r.passed]


def replay_case(case: Case, *, graded: bool) -> CaseResult:
    """Replay one case through its graded layer and compare to the answer."""
    if case.kind != VERDICT_KIND:
        return CaseResult(
            case.case_id, case.kind, graded, False, None, None,
            detail=f"unsupported kind {case.kind!r} (only {VERDICT_KIND!r} graded today)",
        )
    checks = case.input.get("checks")
    result, failing = compute_verdict(checks)
    actual = {"result": result, "failing": sorted(failing)}
    gt = case.ground_truth
    expected = {
        "result": gt.get("result"),
        "failing": sorted(gt.get("failing", []) or []),
    }
    passed = actual == expected
    detail = "" if passed else f"expected {expected} but code computed {actual}"
    return CaseResult(case.case_id, case.kind, graded, passed, expected, actual, detail)


def run_corpus(cases: list[Case], graded_ids: set[str]) -> ReplayReport:
    """Replay the VERDICT-kind cases; ``graded_ids`` are the verified-signed ids.

    Non-verdict kinds (e.g. ``extraction`` cases for the drift monitor) are NOT part
    of the deterministic blocking gate and are excluded here — they would otherwise
    show as spurious failures (``compute_verdict`` can't grade a diff). The chain
    integrity check in ``verify`` still covers every case regardless of kind."""
    results = [
        replay_case(c, graded=c.case_id in graded_ids)
        for c in cases
        if c.kind == VERDICT_KIND
    ]
    return ReplayReport(results)


__all__ = [
    "VERDICT_KIND",
    "CaseResult",
    "ReplayReport",
    "replay_case",
    "run_corpus",
]
