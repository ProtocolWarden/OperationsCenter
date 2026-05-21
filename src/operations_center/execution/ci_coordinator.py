# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
ci_coordinator.py — Refinement loop coordinator for CI-enabled proposals.

Drives the multi-attempt evaluation-and-decision cycle for proposals that
carry a ``ContinuousImprovementSpec``.

State machine:
    NOT_STARTED
        → IN_PROGRESS (first attempt dispatched)
        → ACCEPTED (an attempt passes all guardrails + primary metric)
        → BUDGET_EXHAUSTED (max_attempts reached without acceptance)
        → ABANDONED (hard guardrail violated; no_retry constraint)
        → ESCALATED (inconclusive; operator decision required)

The coordinator does not run the backend directly — it calls a provided
``ExecutionCallable`` for each attempt, then calls ``CiEvaluator.evaluate()``,
then makes a decision. The caller (board_worker or equivalent) is responsible
for wiring up the callable.

Design doc: docs/design/continuous-improvement/design.md §4, §5, §8
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

from operations_center.contracts.ci import (
    ClpBinding,
    ContinuousImprovementSpec,
    EvaluationScore,
    ImprovementLineage,
    ImprovementStrategy,
    LineageAttempt,
    OcContinuousImprovementState,
    OcLineageIndexEntry,
)
from operations_center.contracts.enums import (
    EvaluationOutcome,
    LineageBranchReason,
    RefinementDecision,
    RefinementStatus,
)
from operations_center.execution.ci_evaluator import CiEvaluator, EvaluationContext
from operations_center.execution.ci_store import CiStore

logger = logging.getLogger(__name__)


@runtime_checkable
class ExecutionCallable(Protocol):
    """
    Protocol for the backend execution callable the coordinator invokes per attempt.

    Returns (run_id, changed_file_paths, success).
    """

    def __call__(
        self,
        *,
        attempt_number: int,
        strategy: ImprovementStrategy,
        proposal_id: str,
    ) -> tuple[str, list[str], bool]:
        ...


@dataclass(frozen=True)
class CiRunContext:
    """Context for a CI coordination run."""

    proposal_id: str
    repo_path: Path
    lineage_id: str
    spec: ContinuousImprovementSpec
    validation_commands: list[str]
    base_commit_sha: str = ""
    store_path: Path = Path("state/ci_lineage.json")
    eval_output_dir: Optional[Path] = None
    timeout_seconds: int = 120


@dataclass(frozen=True)
class CiRunResult:
    """Outcome of a full CI coordination run (all attempts)."""

    lineage_id: str
    final_status: RefinementStatus
    accepted_attempt_number: Optional[int]
    total_attempts: int
    last_decision: Optional[RefinementDecision]
    last_score: Optional[EvaluationScore]


class CiCoordinator:
    """
    Orchestrates the multi-attempt refinement loop for a CI-enabled proposal.

    Instantiate once per CI run; call ``run()`` to drive the full loop.
    """

    def __init__(
        self,
        evaluator: Optional[CiEvaluator] = None,
        store: Optional[CiStore] = None,
    ) -> None:
        self._evaluator = evaluator or CiEvaluator()
        self._store = store or CiStore()

    def run(
        self,
        ctx: CiRunContext,
        execute: ExecutionCallable,
    ) -> CiRunResult:
        """
        Drive the full multi-attempt refinement loop.

        Initialises or resumes lineage, dispatches attempts via ``execute``,
        evaluates each, decides accept/retry/abandon/escalate, and persists
        state after every attempt.
        """
        spec = ctx.spec
        policy = spec.refinement

        lineage = self._load_or_init_lineage(ctx)
        state = self._load_or_init_state(ctx, lineage)

        last_decision: Optional[RefinementDecision] = None
        last_score: Optional[EvaluationScore] = None

        while lineage.current_attempt_number < policy.max_attempts:
            attempt_n = lineage.current_attempt_number + 1

            branch_reason = (
                LineageBranchReason.INITIAL
                if attempt_n == 1
                else LineageBranchReason.RETRY_AFTER_FAILURE
            )

            strategy = self._strategy_for_attempt(spec, lineage, attempt_n)

            logger.info(
                '{"event": "ci_attempt_start", "proposal_id": "%s", "attempt": %d, '
                '"lineage_id": "%s"}',
                ctx.proposal_id,
                attempt_n,
                ctx.lineage_id,
            )

            started_at = datetime.now(UTC)
            run_id, changed_files, exec_success = execute(
                attempt_number=attempt_n,
                strategy=strategy,
                proposal_id=ctx.proposal_id,
            )

            eval_output_path: Optional[Path] = None
            if ctx.eval_output_dir:
                eval_output_path = (
                    ctx.eval_output_dir / f"attempt-{attempt_n}" / "eval.json"
                )

            eval_ctx = EvaluationContext(
                repo_path=ctx.repo_path,
                attempt_number=attempt_n,
                evaluation_command=state.resolved_evaluation_command or "",
                validation_commands=ctx.validation_commands,
                changed_file_paths=changed_files,
                eval_output_path=eval_output_path,
                timeout_seconds=ctx.timeout_seconds,
            )

            score = self._evaluator.evaluate(spec.evaluation, eval_ctx)
            last_score = score

            decision, decision_reason = self._decide(
                score=score,
                policy=policy,
                attempts_used=attempt_n,
            )
            last_decision = decision

            attempt = LineageAttempt(
                attempt_number=attempt_n,
                run_id=run_id,
                branch_reason=branch_reason,
                strategy_used=strategy,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                score=score,
                decision=decision,
                decision_reason=decision_reason,
                replay_metadata=self._build_replay_metadata(ctx, spec, attempt_n),
            )

            lineage = self._append_attempt(lineage, attempt, decision)
            state = self._update_state(state, lineage, score)

            self._persist(ctx, lineage, state)

            logger.info(
                '{"event": "ci_attempt_end", "proposal_id": "%s", "attempt": %d, '
                '"outcome": "%s", "decision": "%s"}',
                ctx.proposal_id,
                attempt_n,
                score.outcome.value,
                decision.value,
            )

            if decision in (
                RefinementDecision.ACCEPT,
                RefinementDecision.ABANDON,
                RefinementDecision.ESCALATE,
            ):
                break

            # RETRY — continue loop (checkpoint requirement is advisory at this layer)

        return CiRunResult(
            lineage_id=ctx.lineage_id,
            final_status=lineage.status,
            accepted_attempt_number=lineage.accepted_attempt_number,
            total_attempts=lineage.current_attempt_number,
            last_decision=last_decision,
            last_score=last_score,
        )

    # ------------------------------------------------------------------
    # Decision logic

    def _decide(
        self,
        *,
        score: EvaluationScore,
        policy,
        attempts_used: int,
    ) -> tuple[RefinementDecision, str]:
        outcome = score.outcome

        if score.guardrails_failed:
            return (
                RefinementDecision.ABANDON,
                f"Hard guardrail(s) violated: {[g.value for g in score.guardrails_failed]}",
            )

        if outcome == EvaluationOutcome.GUARDRAIL_VIOLATED:
            return RefinementDecision.ABANDON, "guardrail_violated outcome from evaluator"

        if outcome == EvaluationOutcome.IMPROVED:
            return RefinementDecision.ACCEPT, f"Primary metric improved (delta={score.primary_metric_delta})"

        if outcome == EvaluationOutcome.NEUTRAL and policy.accept_on_neutral:
            return RefinementDecision.ACCEPT, "Neutral outcome accepted per policy"

        if outcome == EvaluationOutcome.INCONCLUSIVE:
            remaining = policy.max_attempts - attempts_used
            if remaining <= 0:
                return RefinementDecision.ESCALATE, "Inconclusive after budget exhausted"
            return RefinementDecision.ESCALATE, "Inconclusive outcome requires operator decision"

        # REGRESSED or NEUTRAL (not accept_on_neutral)
        remaining = policy.max_attempts - attempts_used - policy.failure_penalty
        if remaining <= 0:
            return (
                RefinementDecision.ABANDON,
                f"Budget exhausted after {attempts_used} attempt(s)",
            )
        return (
            RefinementDecision.RETRY,
            f"outcome={outcome.value}; {remaining} attempt(s) remaining",
        )

    # ------------------------------------------------------------------
    # Lineage mutation (produces new frozen instances)

    def _append_attempt(
        self,
        lineage: ImprovementLineage,
        attempt: LineageAttempt,
        decision: RefinementDecision,
    ) -> ImprovementLineage:
        new_status = self._status_for_decision(decision, lineage.current_attempt_number + 1)
        accepted_n = (
            attempt.attempt_number if decision == RefinementDecision.ACCEPT else lineage.accepted_attempt_number
        )
        return ImprovementLineage(
            lineage_id=lineage.lineage_id,
            parent_lineage_id=lineage.parent_lineage_id,
            status=new_status,
            current_attempt_number=attempt.attempt_number,
            accepted_attempt_number=accepted_n,
            attempts=[*lineage.attempts, attempt],
        )

    @staticmethod
    def _status_for_decision(
        decision: RefinementDecision, attempt_number: int
    ) -> RefinementStatus:
        return {
            RefinementDecision.ACCEPT: RefinementStatus.ACCEPTED,
            RefinementDecision.ABANDON: RefinementStatus.ABANDONED,
            RefinementDecision.ESCALATE: RefinementStatus.ESCALATED,
            RefinementDecision.RETRY: RefinementStatus.IN_PROGRESS,
        }[decision]

    # ------------------------------------------------------------------
    # Strategy selection

    @staticmethod
    def _strategy_for_attempt(
        spec: ContinuousImprovementSpec,
        lineage: ImprovementLineage,
        attempt_n: int,
    ) -> ImprovementStrategy:
        if attempt_n == 1 or not spec.refinement.vary_strategy_on_retry:
            return spec.strategy
        # Carry over variation_hint from spec if set for retry
        return ImprovementStrategy(
            principle=spec.strategy.principle,
            constraints=spec.strategy.constraints,
            variation_hint=spec.strategy.variation_hint,
        )

    # ------------------------------------------------------------------
    # State management

    def _load_or_init_lineage(self, ctx: CiRunContext) -> ImprovementLineage:
        lineage_path = self._lineage_path(ctx)
        existing = CiStore.load_lineage(lineage_path)
        if existing is not None:
            return existing
        return ImprovementLineage(
            lineage_id=ctx.lineage_id,
            status=RefinementStatus.NOT_STARTED,
        )

    def _load_or_init_state(
        self, ctx: CiRunContext, lineage: ImprovementLineage
    ) -> OcContinuousImprovementState:
        existing = self._store.get_state(ctx.proposal_id)
        if existing is not None:
            return existing

        lineage_path = self._lineage_path(ctx)
        index_entry = OcLineageIndexEntry(
            lineage_id=ctx.lineage_id,
            proposal_id=ctx.proposal_id,
            lineage_artifact_path=str(lineage_path),
            status=RefinementStatus.NOT_STARTED,
            current_attempt_number=0,
            last_updated_at=datetime.now(UTC),
        )
        resolved_cmd, cmd_source = self._resolve_evaluation_command(ctx.spec)

        return OcContinuousImprovementState(
            proposal_id=ctx.proposal_id,
            lineage_index=index_entry,
            spec_snapshot=ctx.spec,
            clp_binding=ClpBinding(lineage_artifact_path=str(lineage_path)),
            resolved_evaluation_command=resolved_cmd,
            resolved_evaluation_command_source=cmd_source,
            last_updated_at=datetime.now(UTC),
        )

    def _update_state(
        self,
        state: OcContinuousImprovementState,
        lineage: ImprovementLineage,
        score: EvaluationScore,
    ) -> OcContinuousImprovementState:
        updated_index = OcLineageIndexEntry(
            lineage_id=state.lineage_index.lineage_id,
            proposal_id=state.proposal_id,
            lineage_artifact_path=state.lineage_index.lineage_artifact_path,
            status=lineage.status,
            current_attempt_number=lineage.current_attempt_number,
            accepted_attempt_number=lineage.accepted_attempt_number,
            last_updated_at=datetime.now(UTC),
            warehouse_archive_id=state.lineage_index.warehouse_archive_id,
        )
        return OcContinuousImprovementState(
            proposal_id=state.proposal_id,
            lineage_index=updated_index,
            spec_snapshot=state.spec_snapshot,
            clp_binding=state.clp_binding,
            resolved_evaluation_command=state.resolved_evaluation_command,
            resolved_evaluation_command_source=state.resolved_evaluation_command_source,
            last_updated_at=datetime.now(UTC),
            operator_override=state.operator_override,
        )

    def _persist(
        self,
        ctx: CiRunContext,
        lineage: ImprovementLineage,
        state: OcContinuousImprovementState,
    ) -> None:
        CiStore.save_lineage(lineage, self._lineage_path(ctx))
        self._store.upsert_index_entry(state.lineage_index)
        self._store.upsert_state(state)

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _lineage_path(ctx: CiRunContext) -> Path:
        return ctx.repo_path / ".context" / "capsules" / ctx.lineage_id / "lineage.json"

    @staticmethod
    def _resolve_evaluation_command(spec: ContinuousImprovementSpec):
        from operations_center.contracts.enums import EvaluationCommandSource

        eval_spec = spec.evaluation
        # Priority: OC-set evaluation_command > hint > derive from first validation command
        if eval_spec.evaluation_command:
            return eval_spec.evaluation_command, EvaluationCommandSource.OC_DERIVED
        if eval_spec.evaluation_command_hint:
            return eval_spec.evaluation_command_hint, EvaluationCommandSource.PROPOSER_SUGGESTED
        return None, None

    @staticmethod
    def _build_replay_metadata(
        ctx: CiRunContext,
        spec: ContinuousImprovementSpec,
        attempt_n: int,
    ) -> dict:
        def _sha(s: str) -> str:
            return "sha256:" + hashlib.sha256(s.encode()).hexdigest()[:8]

        return {
            "base_commit_sha": ctx.base_commit_sha,
            "goal_text_hash": _sha(spec.strategy.principle),
            "strategy_principle_hash": _sha(spec.strategy.principle),
            "clp_schema_version": "0.1",
            "oc_schema_version": "0.3",
        }
