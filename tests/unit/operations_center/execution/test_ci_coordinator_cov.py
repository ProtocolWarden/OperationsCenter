# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.contracts.ci import (
    ContinuousImprovementSpec,
    EvaluationScore,
    EvaluationSpec,
    ImprovementLineage,
    ImprovementStrategy,
    RefinementPolicy,
    ScoringMetric,
)
from operations_center.contracts.enums import (
    EnforcedGuardrail,
    EvaluationCommandSource,
    EvaluationOutcome,
    LineageBranchReason,
    RefinementDecision,
    RefinementStatus,
)
from operations_center.execution import ci_coordinator
from operations_center.execution.ci_coordinator import (
    CiCoordinator,
    CiRunContext,
    ExecutionCallable,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    max_attempts: int = 3,
    accept_on_neutral: bool = False,
    vary_strategy_on_retry: bool = False,
    failure_penalty: int = 0,
    evaluation_command: str | None = None,
    evaluation_command_hint: str | None = None,
    variation_hint: str | None = None,
    guardrails=None,
) -> ContinuousImprovementSpec:
    strategy = ImprovementStrategy(
        principle="reduce false retries",
        constraints=["fail_closed"],
        variation_hint=variation_hint,
    )
    evaluation = EvaluationSpec(
        baseline_description="baseline",
        primary_scoring=ScoringMetric(metric="false_retry_rate"),
        guardrails=guardrails if guardrails is not None else [],
        evaluation_command=evaluation_command,
        evaluation_command_hint=evaluation_command_hint,
    )
    refinement = RefinementPolicy(
        max_attempts=max_attempts,
        accept_on_neutral=accept_on_neutral,
        vary_strategy_on_retry=vary_strategy_on_retry,
        failure_penalty=failure_penalty,
    )
    return ContinuousImprovementSpec(
        strategy=strategy,
        evaluation=evaluation,
        refinement=refinement,
    )


def _make_ctx(spec: ContinuousImprovementSpec, repo_path: Path, **kw) -> CiRunContext:
    return CiRunContext(
        proposal_id=kw.get("proposal_id", "prop-1"),
        repo_path=repo_path,
        lineage_id=kw.get("lineage_id", "lin-1"),
        spec=spec,
        validation_commands=kw.get("validation_commands", ["pytest"]),
        base_commit_sha=kw.get("base_commit_sha", "abc123"),
        eval_output_dir=kw.get("eval_output_dir", None),
        timeout_seconds=kw.get("timeout_seconds", 120),
    )


def _score(
    outcome: EvaluationOutcome,
    *,
    guardrails_failed=None,
    delta: float | None = -1.0,
) -> EvaluationScore:
    return EvaluationScore(
        primary_metric_delta=delta,
        guardrails_failed=guardrails_failed or [],
        outcome=outcome,
    )


def _make_coordinator(eval_scores):
    """Build a coordinator with mocked evaluator (returns scores in order) and store."""
    evaluator = MagicMock()
    if isinstance(eval_scores, list):
        evaluator.evaluate.side_effect = eval_scores
    else:
        evaluator.evaluate.return_value = eval_scores

    store = MagicMock()
    store.get_state.return_value = None  # force init path
    coord = CiCoordinator(evaluator=evaluator, store=store)
    return coord, evaluator, store


def _execute_stub(success: bool = True):
    calls = []

    def _execute(*, attempt_number, strategy, proposal_id):
        calls.append((attempt_number, strategy, proposal_id))
        return (f"run-{attempt_number}", [f"file-{attempt_number}.py"], success)

    _execute.calls = calls
    return _execute


@pytest.fixture
def repo(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


def test_execution_callable_runtime_checkable():
    assert isinstance(_execute_stub(), ExecutionCallable)


# ---------------------------------------------------------------------------
# run() — happy path: ACCEPT on first attempt
# ---------------------------------------------------------------------------


def test_run_accept_first_attempt(repo):
    spec = _make_spec(max_attempts=3)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.IMPROVED))
    ctx = _make_ctx(spec, repo)
    execute = _execute_stub()

    result = coord.run(ctx, execute)

    assert result.final_status == RefinementStatus.ACCEPTED
    assert result.accepted_attempt_number == 1
    assert result.total_attempts == 1
    assert result.last_decision == RefinementDecision.ACCEPT
    assert result.last_score.outcome == EvaluationOutcome.IMPROVED
    # only one attempt dispatched
    assert len(execute.calls) == 1
    # persistence happened
    store.save_lineage = store.save_lineage  # noqa
    store.upsert_index_entry.assert_called_once()
    store.upsert_state.assert_called_once()


# ---------------------------------------------------------------------------
# run() — ABANDON on hard guardrail failure
# ---------------------------------------------------------------------------


def test_run_abandon_on_guardrails_failed(repo):
    spec = _make_spec(max_attempts=3)
    score = _score(
        EvaluationOutcome.REGRESSED,
        guardrails_failed=[EnforcedGuardrail.CUSTODIAN_CLEAN],
    )
    coord, evaluator, store = _make_coordinator(score)
    ctx = _make_ctx(spec, repo)
    execute = _execute_stub()

    result = coord.run(ctx, execute)

    assert result.final_status == RefinementStatus.ABANDONED
    assert result.last_decision == RefinementDecision.ABANDON
    assert result.accepted_attempt_number is None
    assert len(execute.calls) == 1


def test_run_abandon_on_guardrail_violated_outcome(repo):
    spec = _make_spec(max_attempts=3)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.GUARDRAIL_VIOLATED))
    ctx = _make_ctx(spec, repo)
    result = coord.run(ctx, _execute_stub())
    assert result.last_decision == RefinementDecision.ABANDON
    assert result.final_status == RefinementStatus.ABANDONED


# ---------------------------------------------------------------------------
# run() — NEUTRAL accept-on-neutral policy
# ---------------------------------------------------------------------------


def test_run_neutral_accepted_per_policy(repo):
    spec = _make_spec(max_attempts=3, accept_on_neutral=True)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.NEUTRAL))
    result = coord.run(_make_ctx(spec, repo), _execute_stub())
    assert result.last_decision == RefinementDecision.ACCEPT
    assert result.final_status == RefinementStatus.ACCEPTED


# ---------------------------------------------------------------------------
# run() — INCONCLUSIVE escalation
# ---------------------------------------------------------------------------


def test_run_inconclusive_escalates_with_budget(repo):
    spec = _make_spec(max_attempts=3)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.INCONCLUSIVE))
    result = coord.run(_make_ctx(spec, repo), _execute_stub())
    assert result.last_decision == RefinementDecision.ESCALATE
    assert result.final_status == RefinementStatus.ESCALATED
    # only one attempt — escalate breaks the loop
    assert result.total_attempts == 1


def test_run_inconclusive_escalates_at_budget_exhausted(repo):
    # max_attempts=1: remaining = 1 - 1 = 0 → "Inconclusive after budget exhausted"
    spec = _make_spec(max_attempts=1)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.INCONCLUSIVE))
    result = coord.run(_make_ctx(spec, repo), _execute_stub())
    assert result.last_decision == RefinementDecision.ESCALATE


# ---------------------------------------------------------------------------
# run() — RETRY then ACCEPT (multi-attempt loop)
# ---------------------------------------------------------------------------


def test_run_retry_then_accept(repo):
    spec = _make_spec(max_attempts=3)
    scores = [
        _score(EvaluationOutcome.REGRESSED),  # retry (remaining=3-1-0=2)
        _score(EvaluationOutcome.IMPROVED),  # accept
    ]
    coord, evaluator, store = _make_coordinator(scores)
    execute = _execute_stub()
    result = coord.run(_make_ctx(spec, repo), execute)

    assert result.total_attempts == 2
    assert result.accepted_attempt_number == 2
    assert result.final_status == RefinementStatus.ACCEPTED
    assert len(execute.calls) == 2
    # second attempt is a retry branch
    assert evaluator.evaluate.call_count == 2


# ---------------------------------------------------------------------------
# run() — budget exhausted via repeated regression
# ---------------------------------------------------------------------------


def test_run_budget_exhausted_via_regression(repo):
    spec = _make_spec(max_attempts=2)
    # attempt1: regressed → remaining = 2-1-0=1 → retry
    # attempt2: regressed → remaining = 2-2-0=0 → abandon (budget exhausted)
    scores = [
        _score(EvaluationOutcome.REGRESSED),
        _score(EvaluationOutcome.REGRESSED),
    ]
    coord, evaluator, store = _make_coordinator(scores)
    result = coord.run(_make_ctx(spec, repo), _execute_stub())

    assert result.total_attempts == 2
    assert result.last_decision == RefinementDecision.ABANDON
    assert result.final_status == RefinementStatus.ABANDONED


def test_run_loop_terminates_on_max_attempts_without_break(repo):
    # failure_penalty large enough that decision is RETRY but loop ends on max_attempts.
    # max_attempts=2, failure_penalty=0 → attempt1 retry, attempt2 abandon.
    # Use neutral (not accepted) to keep retrying. attempt1: NEUTRAL not accept →
    # remaining = 2-1-0 = 1 → retry; attempt2: remaining = 2-2-0 = 0 → abandon.
    spec = _make_spec(max_attempts=2, accept_on_neutral=False)
    scores = [
        _score(EvaluationOutcome.NEUTRAL),
        _score(EvaluationOutcome.NEUTRAL),
    ]
    coord, evaluator, store = _make_coordinator(scores)
    result = coord.run(_make_ctx(spec, repo), _execute_stub())
    assert result.total_attempts == 2
    assert result.final_status == RefinementStatus.ABANDONED


# ---------------------------------------------------------------------------
# eval_output_dir wiring
# ---------------------------------------------------------------------------


def test_run_passes_eval_output_path(repo):
    spec = _make_spec(max_attempts=1)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.IMPROVED))
    out_dir = repo / "evals"
    ctx = _make_ctx(spec, repo, eval_output_dir=out_dir)
    coord.run(ctx, _execute_stub())

    eval_ctx = evaluator.evaluate.call_args.args[1]
    assert eval_ctx.eval_output_path == out_dir / "attempt-1" / "eval.json"


def test_run_no_eval_output_dir_yields_none(repo):
    spec = _make_spec(max_attempts=1)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.IMPROVED))
    coord.run(_make_ctx(spec, repo), _execute_stub())
    eval_ctx = evaluator.evaluate.call_args.args[1]
    assert eval_ctx.eval_output_path is None


# ---------------------------------------------------------------------------
# Resume from existing state / lineage
# ---------------------------------------------------------------------------


def test_run_resumes_existing_lineage_at_budget(repo, monkeypatch):
    spec = _make_spec(max_attempts=1)
    # Existing lineage already at current_attempt_number == max_attempts → loop skipped.
    existing_lineage = ImprovementLineage(
        lineage_id="lin-1",
        status=RefinementStatus.IN_PROGRESS,
        current_attempt_number=1,
    )
    monkeypatch.setattr(
        ci_coordinator.CiStore,
        "load_lineage",
        staticmethod(lambda p: existing_lineage),
    )
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.IMPROVED))
    # existing state present too
    store.get_state.return_value = MagicMock(resolved_evaluation_command="cmd")
    result = coord.run(_make_ctx(spec, repo), _execute_stub())

    # No attempt dispatched, no evaluation
    evaluator.evaluate.assert_not_called()
    assert result.total_attempts == 1
    assert result.last_decision is None
    assert result.last_score is None
    assert result.final_status == RefinementStatus.IN_PROGRESS


def test_load_or_init_lineage_initialises_when_absent(repo, monkeypatch):
    monkeypatch.setattr(ci_coordinator.CiStore, "load_lineage", staticmethod(lambda p: None))
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    ctx = _make_ctx(_make_spec(), repo)
    lineage = coord._load_or_init_lineage(ctx)
    assert lineage.lineage_id == "lin-1"
    assert lineage.status == RefinementStatus.NOT_STARTED


def test_load_or_init_state_returns_existing(repo):
    sentinel = object()
    store = MagicMock()
    store.get_state.return_value = sentinel
    coord = CiCoordinator(evaluator=MagicMock(), store=store)
    ctx = _make_ctx(_make_spec(), repo)
    lineage = ImprovementLineage(lineage_id="lin-1")
    assert coord._load_or_init_state(ctx, lineage) is sentinel


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def test_strategy_for_attempt_first_attempt_uses_spec_strategy():
    spec = _make_spec()
    lineage = ImprovementLineage(lineage_id="x")
    s = CiCoordinator._strategy_for_attempt(spec, lineage, 1)
    assert s is spec.strategy


def test_strategy_for_attempt_no_vary_returns_spec_strategy():
    spec = _make_spec(vary_strategy_on_retry=False)
    lineage = ImprovementLineage(lineage_id="x")
    s = CiCoordinator._strategy_for_attempt(spec, lineage, 2)
    assert s is spec.strategy


def test_strategy_for_attempt_vary_builds_new_strategy():
    spec = _make_spec(vary_strategy_on_retry=True, variation_hint="try harder")
    lineage = ImprovementLineage(lineage_id="x")
    s = CiCoordinator._strategy_for_attempt(spec, lineage, 2)
    assert s is not spec.strategy
    assert s.principle == spec.strategy.principle
    assert s.variation_hint == "try harder"
    assert "fail_closed" in s.constraints


def test_run_uses_retry_branch_reason_with_vary(repo):
    spec = _make_spec(max_attempts=2, vary_strategy_on_retry=True)
    scores = [
        _score(EvaluationOutcome.REGRESSED),
        _score(EvaluationOutcome.IMPROVED),
    ]
    coord, evaluator, store = _make_coordinator(scores)
    execute = _execute_stub()
    coord.run(_make_ctx(spec, repo), execute)
    # attempt 1 used spec.strategy, attempt 2 a varied copy
    assert execute.calls[0][1] is spec.strategy
    assert execute.calls[1][1] is not spec.strategy


# ---------------------------------------------------------------------------
# _decide unit coverage
# ---------------------------------------------------------------------------


def _policy(**kw):
    return RefinementPolicy(**kw)


def test_decide_guardrails_failed():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    score = _score(
        EvaluationOutcome.IMPROVED,
        guardrails_failed=[EnforcedGuardrail.REGRESSION_FIXTURES_PASS],
    )
    decision, reason = coord._decide(score=score, policy=_policy(), attempts_used=1)
    assert decision == RefinementDecision.ABANDON
    assert "regression_fixtures_pass" in reason


def test_decide_improved():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    score = _score(EvaluationOutcome.IMPROVED, delta=-2.5)
    decision, reason = coord._decide(score=score, policy=_policy(), attempts_used=1)
    assert decision == RefinementDecision.ACCEPT
    assert "-2.5" in reason


def test_decide_neutral_no_accept_retry():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    score = _score(EvaluationOutcome.NEUTRAL)
    decision, reason = coord._decide(
        score=score, policy=_policy(max_attempts=3, accept_on_neutral=False), attempts_used=1
    )
    assert decision == RefinementDecision.RETRY
    assert "remaining" in reason


def test_decide_regressed_retry_and_abandon():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    score = _score(EvaluationOutcome.REGRESSED)
    # remaining = 3 - 1 - 0 = 2 → retry
    d1, _ = coord._decide(score=score, policy=_policy(max_attempts=3), attempts_used=1)
    assert d1 == RefinementDecision.RETRY
    # remaining = 1 - 1 - 0 = 0 → abandon
    d2, r2 = coord._decide(score=score, policy=_policy(max_attempts=1), attempts_used=1)
    assert d2 == RefinementDecision.ABANDON
    assert "Budget exhausted" in r2


def test_decide_failure_penalty_forces_abandon():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    score = _score(EvaluationOutcome.REGRESSED)
    # remaining = 5 - 1 - 5 = -1 → abandon
    d, r = coord._decide(
        score=score, policy=_policy(max_attempts=5, failure_penalty=5), attempts_used=1
    )
    assert d == RefinementDecision.ABANDON


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision,status",
    [
        (RefinementDecision.ACCEPT, RefinementStatus.ACCEPTED),
        (RefinementDecision.ABANDON, RefinementStatus.ABANDONED),
        (RefinementDecision.ESCALATE, RefinementStatus.ESCALATED),
        (RefinementDecision.RETRY, RefinementStatus.IN_PROGRESS),
    ],
)
def test_status_for_decision(decision, status):
    assert CiCoordinator._status_for_decision(decision, 1) == status


# ---------------------------------------------------------------------------
# _append_attempt
# ---------------------------------------------------------------------------


def _make_attempt(n, decision):
    from datetime import UTC, datetime

    return ci_coordinator.LineageAttempt(
        attempt_number=n,
        run_id=f"run-{n}",
        branch_reason=LineageBranchReason.INITIAL,
        strategy_used=ImprovementStrategy(principle="p", constraints=["fail_closed"]),
        started_at=datetime.now(UTC),
        decision=decision,
    )


def test_append_attempt_accept_sets_accepted_number():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    lineage = ImprovementLineage(lineage_id="x", current_attempt_number=0)
    attempt = _make_attempt(1, RefinementDecision.ACCEPT)
    new = coord._append_attempt(lineage, attempt, RefinementDecision.ACCEPT)
    assert new.accepted_attempt_number == 1
    assert new.status == RefinementStatus.ACCEPTED
    assert new.current_attempt_number == 1
    assert len(new.attempts) == 1


def test_append_attempt_retry_keeps_prior_accepted():
    coord = CiCoordinator(evaluator=MagicMock(), store=MagicMock())
    lineage = ImprovementLineage(
        lineage_id="x", current_attempt_number=1, accepted_attempt_number=None
    )
    attempt = _make_attempt(2, RefinementDecision.RETRY)
    new = coord._append_attempt(lineage, attempt, RefinementDecision.RETRY)
    assert new.accepted_attempt_number is None
    assert new.status == RefinementStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# _resolve_evaluation_command
# ---------------------------------------------------------------------------


def test_resolve_evaluation_command_explicit():
    spec = _make_spec(evaluation_command="make eval")
    cmd, source = CiCoordinator._resolve_evaluation_command(spec)
    assert cmd == "make eval"
    assert source == EvaluationCommandSource.OC_DERIVED


def test_resolve_evaluation_command_hint():
    spec = _make_spec(evaluation_command_hint="pytest -q")
    cmd, source = CiCoordinator._resolve_evaluation_command(spec)
    assert cmd == "pytest -q"
    assert source == EvaluationCommandSource.PROPOSER_SUGGESTED


def test_resolve_evaluation_command_none():
    spec = _make_spec()
    cmd, source = CiCoordinator._resolve_evaluation_command(spec)
    assert cmd is None
    assert source is None


# ---------------------------------------------------------------------------
# _lineage_path / _build_replay_metadata
# ---------------------------------------------------------------------------


def test_lineage_path(repo):
    ctx = _make_ctx(_make_spec(), repo, lineage_id="LID")
    p = CiCoordinator._lineage_path(ctx)
    assert p == repo / ".context" / "capsules" / "LID" / "lineage.json"


def test_build_replay_metadata(repo):
    spec = _make_spec()
    ctx = _make_ctx(spec, repo, base_commit_sha="deadbeef")
    meta = CiCoordinator._build_replay_metadata(ctx, spec, 2)
    assert meta["base_commit_sha"] == "deadbeef"
    assert meta["goal_text_hash"].startswith("sha256:")
    assert meta["strategy_principle_hash"].startswith("sha256:")
    assert meta["clp_schema_version"] == "0.1"
    assert meta["oc_schema_version"] == "0.3"


# ---------------------------------------------------------------------------
# default construction
# ---------------------------------------------------------------------------


def test_coordinator_default_construction(monkeypatch):
    made = {}

    class _FakeEvaluator:
        def __init__(self):
            made["eval"] = True

    class _FakeStore:
        def __init__(self):
            made["store"] = True

    monkeypatch.setattr(ci_coordinator, "CiEvaluator", _FakeEvaluator)
    monkeypatch.setattr(ci_coordinator, "CiStore", _FakeStore)
    coord = CiCoordinator()
    assert made == {"eval": True, "store": True}
    assert isinstance(coord._evaluator, _FakeEvaluator)
    assert isinstance(coord._store, _FakeStore)


# ---------------------------------------------------------------------------
# _persist wiring (via run, with save_lineage patched)
# ---------------------------------------------------------------------------


def test_persist_calls_store(repo, monkeypatch):
    saved = []
    monkeypatch.setattr(
        ci_coordinator.CiStore,
        "save_lineage",
        staticmethod(lambda lineage, path: saved.append((lineage, path))),
    )
    monkeypatch.setattr(ci_coordinator.CiStore, "load_lineage", staticmethod(lambda p: None))
    spec = _make_spec(max_attempts=1)
    coord, evaluator, store = _make_coordinator(_score(EvaluationOutcome.IMPROVED))
    coord.run(_make_ctx(spec, repo), _execute_stub())
    assert len(saved) == 1
    store.upsert_index_entry.assert_called_once()
    store.upsert_state.assert_called_once()
