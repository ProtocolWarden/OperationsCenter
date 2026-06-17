# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for CiCoordinator — budget arithmetic, state machine, and 3-attempt cycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

from operations_center.contracts.ci import (
    ContinuousImprovementSpec,
    EvaluationScore,
    EvaluationSpec,
    ImprovementStrategy,
    RefinementPolicy,
    ScoringMetric,
)
from operations_center.contracts.enums import (
    EnforcedGuardrail,
    EvaluationOutcome,
    RefinementDecision,
    RefinementStatus,
)
from operations_center.execution.ci_coordinator import CiCoordinator, CiRunContext
from operations_center.execution.ci_evaluator import CiEvaluator
from operations_center.execution.ci_store import CiStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strategy(variation_hint: Optional[str] = None) -> ImprovementStrategy:
    return ImprovementStrategy(
        principle="Reduce false retry rate",
        constraints=["fail_closed"],
        variation_hint=variation_hint,
    )


def _eval_spec(guardrails=None, target_delta=-0.05) -> EvaluationSpec:
    return EvaluationSpec(
        baseline_description="baseline",
        primary_scoring=ScoringMetric(
            metric="false_retry_rate",
            direction="lower_is_better",
            baseline_value=0.18,
            target_delta=target_delta,
        ),
        guardrails=guardrails or [],
    )


def _spec(policy: Optional[RefinementPolicy] = None, **eval_kwargs) -> ContinuousImprovementSpec:
    return ContinuousImprovementSpec(
        strategy=_strategy(),
        evaluation=_eval_spec(**eval_kwargs),
        refinement=policy or RefinementPolicy(max_attempts=3),
    )


def _ctx(tmp_path: Path, spec: ContinuousImprovementSpec) -> CiRunContext:
    return CiRunContext(
        proposal_id="prop-test-001",
        repo_path=tmp_path,
        lineage_id="lin-test-001",
        spec=spec,
        validation_commands=[],
        store_path=tmp_path / "state/ci_lineage.json",
    )


def _mock_score(
    outcome: EvaluationOutcome,
    primary_delta: Optional[float] = None,
    guardrails_failed=None,
) -> EvaluationScore:
    return EvaluationScore(
        primary_metric_delta=primary_delta,
        outcome=outcome,
        guardrails_failed=guardrails_failed or [],
        guardrails_passed=[],
    )


def _make_execute(outcomes: list[tuple[EvaluationOutcome, Optional[float]]]):
    """Return (execute_callable, call_count_tracker)."""
    calls = []

    def execute(*, attempt_number: int, strategy, proposal_id: str):
        calls.append(attempt_number)
        return (f"run-{attempt_number}", [], True)

    return execute, calls


# ---------------------------------------------------------------------------
# Decision logic tests
# ---------------------------------------------------------------------------


class TestCiCoordinatorDecideLogic:
    """Unit tests for the _decide method in isolation."""

    def setup_method(self):
        self.coord = CiCoordinator(
            evaluator=MagicMock(spec=CiEvaluator),
            store=MagicMock(spec=CiStore),
        )

    def _decide(self, score, policy, attempts_used=1):
        return self.coord._decide(score=score, policy=policy, attempts_used=attempts_used)

    def test_improved_with_no_guardrail_fail_accepts(self):
        score = _mock_score(EvaluationOutcome.IMPROVED, primary_delta=-0.07)
        policy = RefinementPolicy(max_attempts=3)
        decision, reason = self._decide(score, policy)
        assert decision == RefinementDecision.ACCEPT
        assert "improved" in reason.lower()

    def test_guardrail_violation_abandons_immediately(self):
        score = _mock_score(
            EvaluationOutcome.GUARDRAIL_VIOLATED,
            guardrails_failed=[EnforcedGuardrail.CUSTODIAN_CLEAN],
        )
        policy = RefinementPolicy(max_attempts=3)
        decision, reason = self._decide(score, policy)
        assert decision == RefinementDecision.ABANDON
        assert "custodian_clean" in reason

    def test_regressed_with_budget_remaining_retries(self):
        score = _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.02)
        policy = RefinementPolicy(max_attempts=3)
        decision, reason = self._decide(score, policy, attempts_used=1)
        assert decision == RefinementDecision.RETRY

    def test_regressed_budget_exhausted_abandons(self):
        score = _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.02)
        policy = RefinementPolicy(max_attempts=3, failure_penalty=0)
        # attempts_used = max_attempts → no budget left
        decision, reason = self._decide(score, policy, attempts_used=3)
        assert decision == RefinementDecision.ABANDON

    def test_failure_penalty_consumed_early(self):
        score = _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.02)
        policy = RefinementPolicy(max_attempts=3, failure_penalty=2)
        # attempts_used=1, penalty=2 → remaining = 3-1-2 = 0
        decision, _ = self._decide(score, policy, attempts_used=1)
        assert decision == RefinementDecision.ABANDON

    def test_neutral_not_accepted_by_default(self):
        score = _mock_score(EvaluationOutcome.NEUTRAL, primary_delta=0.0)
        policy = RefinementPolicy(max_attempts=3, accept_on_neutral=False)
        decision, _ = self._decide(score, policy, attempts_used=1)
        assert decision == RefinementDecision.RETRY

    def test_neutral_accepted_when_policy_set(self):
        score = _mock_score(EvaluationOutcome.NEUTRAL, primary_delta=0.0)
        policy = RefinementPolicy(max_attempts=3, accept_on_neutral=True)
        decision, _ = self._decide(score, policy)
        assert decision == RefinementDecision.ACCEPT

    def test_inconclusive_escalates(self):
        score = _mock_score(EvaluationOutcome.INCONCLUSIVE)
        policy = RefinementPolicy(max_attempts=3)
        decision, _ = self._decide(score, policy, attempts_used=2)
        assert decision == RefinementDecision.ESCALATE


# ---------------------------------------------------------------------------
# Integration: full 3-attempt cycle
# ---------------------------------------------------------------------------


class TestCiCoordinator3AttemptCycle:
    """Integration tests driving the full loop with mocked evaluator."""

    def _make_coord(self, scores: list[EvaluationScore], tmp_path: Path):
        evaluator = MagicMock(spec=CiEvaluator)
        evaluator.evaluate.side_effect = scores
        store = CiStore(path=tmp_path / "state/ci_lineage.json")
        return CiCoordinator(evaluator=evaluator, store=store)

    def test_accepts_on_first_improved_attempt(self, tmp_path):
        scores = [_mock_score(EvaluationOutcome.IMPROVED, primary_delta=-0.07)]
        coord = self._make_coord(scores, tmp_path)
        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, calls = _make_execute([(EvaluationOutcome.IMPROVED, -0.07)])

        result = coord.run(ctx, execute)

        assert result.final_status == RefinementStatus.ACCEPTED
        assert result.accepted_attempt_number == 1
        assert result.total_attempts == 1
        assert len(calls) == 1

    def test_retries_then_accepts(self, tmp_path):
        scores = [
            _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.02),
            _mock_score(EvaluationOutcome.IMPROVED, primary_delta=-0.06),
        ]
        coord = self._make_coord(scores, tmp_path)
        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, calls = _make_execute([])

        result = coord.run(ctx, execute)

        assert result.final_status == RefinementStatus.ACCEPTED
        assert result.accepted_attempt_number == 2
        assert result.total_attempts == 2
        assert len(calls) == 2

    def test_budget_exhausted_after_3_attempts(self, tmp_path):
        scores = [
            _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.01),
            _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.01),
            _mock_score(EvaluationOutcome.REGRESSED, primary_delta=0.01),
        ]
        coord = self._make_coord(scores, tmp_path)
        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, calls = _make_execute([])

        result = coord.run(ctx, execute)

        assert result.final_status == RefinementStatus.ABANDONED
        assert result.total_attempts == 3
        assert len(calls) == 3

    def test_guardrail_violation_abandons_immediately(self, tmp_path):
        scores = [
            _mock_score(
                EvaluationOutcome.GUARDRAIL_VIOLATED,
                guardrails_failed=[EnforcedGuardrail.CUSTODIAN_CLEAN],
            ),
        ]
        coord = self._make_coord(scores, tmp_path)
        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, calls = _make_execute([])

        result = coord.run(ctx, execute)

        assert result.final_status == RefinementStatus.ABANDONED
        assert result.total_attempts == 1
        assert len(calls) == 1

    def test_inconclusive_escalates(self, tmp_path):
        scores = [_mock_score(EvaluationOutcome.INCONCLUSIVE)]
        coord = self._make_coord(scores, tmp_path)
        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, calls = _make_execute([])

        result = coord.run(ctx, execute)

        assert result.final_status == RefinementStatus.ESCALATED
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Lineage persistence tests
# ---------------------------------------------------------------------------


class TestCiCoordinatorLineagePersistence:
    def test_lineage_written_to_clp_path(self, tmp_path):
        scores = [_mock_score(EvaluationOutcome.IMPROVED, primary_delta=-0.06)]
        evaluator = MagicMock(spec=CiEvaluator)
        evaluator.evaluate.side_effect = scores
        store = CiStore(path=tmp_path / "state/ci_lineage.json")
        coord = CiCoordinator(evaluator=evaluator, store=store)

        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, _ = _make_execute([])
        coord.run(ctx, execute)

        lineage_path = tmp_path / ".context" / "capsules" / "lin-test-001" / "lineage.json"
        assert lineage_path.exists()
        data = json.loads(lineage_path.read_text())
        assert data["lineage_id"] == "lin-test-001"
        assert data["status"] == "accepted"
        assert len(data["attempts"]) == 1

    def test_index_entry_written_to_store(self, tmp_path):
        scores = [_mock_score(EvaluationOutcome.IMPROVED, primary_delta=-0.06)]
        evaluator = MagicMock(spec=CiEvaluator)
        evaluator.evaluate.side_effect = scores
        store = CiStore(path=tmp_path / "state/ci_lineage.json")
        coord = CiCoordinator(evaluator=evaluator, store=store)

        spec = _spec()
        ctx = _ctx(tmp_path, spec)
        execute, _ = _make_execute([])
        coord.run(ctx, execute)

        entry = store.get_index_entry("lin-test-001")
        assert entry is not None
        assert entry.status == RefinementStatus.ACCEPTED
        assert entry.accepted_attempt_number == 1
