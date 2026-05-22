# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for CI contract types."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from operations_center.contracts.ci import (
    ClpBinding,
    ContinuousImprovementSpec,
    EvaluationScore,
    EvaluationSpec,
    ImprovementLineage,
    ImprovementStrategy,
    LineageAttempt,
    OcLineageIndexEntry,
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
from operations_center.contracts.proposal import OcPlanningProposal
from operations_center.contracts.common import TaskTarget
from operations_center.contracts.enums import ExecutionMode, TaskType
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _target() -> TaskTarget:
    return TaskTarget(repo_key="svc", clone_url="https://git.example.com/svc.git", base_branch="main")


def _strategy() -> ImprovementStrategy:
    return ImprovementStrategy(
        principle="Reduce false-positive retry rate",
        constraints=["fail_closed", "preserve_real_failure_detection"],
    )


def _eval_spec() -> EvaluationSpec:
    return EvaluationSpec(
        baseline_description="current classifier — 18% false retry rate",
        primary_scoring=ScoringMetric(
            metric="false_retry_rate",
            direction="lower_is_better",
            baseline_value=0.18,
            target_delta=-0.05,
        ),
        guardrails=[EnforcedGuardrail.CUSTODIAN_CLEAN, EnforcedGuardrail.REGRESSION_FIXTURES_PASS],
    )


def _ci_spec() -> ContinuousImprovementSpec:
    return ContinuousImprovementSpec(strategy=_strategy(), evaluation=_eval_spec())


class TestImprovementStrategy:
    def test_basic_construction(self):
        s = _strategy()
        assert s.principle == "Reduce false-positive retry rate"
        assert "fail_closed" in s.constraints
        assert s.variation_hint is None

    def test_frozen(self):
        s = _strategy()
        with pytest.raises((TypeError, ValidationError)):
            s.principle = "changed"

    def test_fail_closed_required(self):
        with pytest.raises(ValidationError, match="fail_closed"):
            ImprovementStrategy(
                principle="Some principle",
                constraints=["preserve_real_failure_detection"],
            )

    def test_fail_closed_empty_constraints_rejected(self):
        with pytest.raises(ValidationError, match="fail_closed"):
            ImprovementStrategy(principle="Some principle", constraints=[])


class TestEvaluationSpec:
    def test_guardrails_are_enum_values(self):
        spec = _eval_spec()
        assert EnforcedGuardrail.CUSTODIAN_CLEAN in spec.guardrails
        assert EnforcedGuardrail.REGRESSION_FIXTURES_PASS in spec.guardrails

    def test_custom_checks_default_empty(self):
        spec = _eval_spec()
        assert spec.custom_checks == []

    def test_evaluation_command_hint_optional(self):
        spec = EvaluationSpec(
            baseline_description="baseline",
            primary_scoring=ScoringMetric(metric="m"),
            guardrails=[],
            evaluation_command_hint="python scripts/eval.py",
        )
        assert spec.evaluation_command_hint == "python scripts/eval.py"
        assert spec.evaluation_command is None
        assert spec.evaluation_command_source is None

    def test_invalid_guardrail_string_rejected(self):
        with pytest.raises(ValidationError):
            EvaluationSpec(
                baseline_description="b",
                primary_scoring=ScoringMetric(metric="m"),
                guardrails=["totally_made_up_guardrail"],
            )


class TestRefinementPolicy:
    def test_defaults(self):
        p = RefinementPolicy()
        assert p.enabled is True
        assert p.max_attempts == 3
        assert p.requires_checkpoint_between_attempts is True
        assert p.failure_penalty == 0
        assert p.accept_on_neutral is False

    def test_max_attempts_bounds(self):
        with pytest.raises(ValidationError):
            RefinementPolicy(max_attempts=0)
        with pytest.raises(ValidationError):
            RefinementPolicy(max_attempts=11)


class TestContinuousImprovementSpec:
    def test_construction(self):
        spec = _ci_spec()
        assert spec.strategy.principle == "Reduce false-positive retry rate"
        assert len(spec.evaluation.guardrails) == 2

    def test_frozen(self):
        spec = _ci_spec()
        with pytest.raises((TypeError, ValidationError)):
            spec.strategy = _strategy()

    def test_clp_binding_defaults_to_empty(self):
        spec = _ci_spec()
        assert spec.clp.lineage_artifact_path is None


class TestOcPlanningProposalCiField:
    def test_proposal_without_ci(self):
        p = OcPlanningProposal(
            task_id="T1",
            project_id="proj",
            task_type=TaskType.BUG_FIX,
            execution_mode=ExecutionMode.IMPROVE_CAMPAIGN,
            goal_text="Fix the flaky classifier",
            target=_target(),
        )
        assert p.continuous_improvement is None

    def test_proposal_with_ci(self):
        p = OcPlanningProposal(
            task_id="T1",
            project_id="proj",
            task_type=TaskType.BUG_FIX,
            execution_mode=ExecutionMode.IMPROVE_CAMPAIGN,
            goal_text="Fix the flaky classifier",
            target=_target(),
            continuous_improvement=_ci_spec(),
        )
        assert p.continuous_improvement is not None
        assert p.continuous_improvement.strategy.principle == "Reduce false-positive retry rate"

    def test_proposal_round_trips_json(self):
        p = OcPlanningProposal(
            task_id="T2",
            project_id="proj",
            task_type=TaskType.BUG_FIX,
            execution_mode=ExecutionMode.IMPROVE_CAMPAIGN,
            goal_text="Fix it",
            target=_target(),
            continuous_improvement=_ci_spec(),
        )
        serialized = p.model_dump_json()
        restored = OcPlanningProposal.model_validate_json(serialized)
        assert restored.continuous_improvement is not None
        assert restored.continuous_improvement.strategy.principle == p.continuous_improvement.strategy.principle


class TestOcLineageIndexEntry:
    def test_construction(self):
        entry = OcLineageIndexEntry(
            lineage_id="lin-001",
            proposal_id="prop-001",
            lineage_artifact_path="active/lin-001/lineage.json",
            status=RefinementStatus.IN_PROGRESS,
            current_attempt_number=1,
            last_updated_at=_utcnow(),
        )
        assert entry.lineage_id == "lin-001"
        assert entry.warehouse_archive_id is None

    def test_accepted_attempt_number_optional(self):
        entry = OcLineageIndexEntry(
            lineage_id="lin-001",
            proposal_id="prop-001",
            lineage_artifact_path="active/lin-001/lineage.json",
            status=RefinementStatus.ACCEPTED,
            current_attempt_number=2,
            accepted_attempt_number=2,
            last_updated_at=_utcnow(),
        )
        assert entry.accepted_attempt_number == 2


class TestImprovementLineage:
    def test_initial_state(self):
        lin = ImprovementLineage(lineage_id="lin-001")
        assert lin.status == RefinementStatus.NOT_STARTED
        assert lin.current_attempt_number == 0
        assert lin.attempts == []

    def test_json_round_trip(self):
        lin = ImprovementLineage(
            lineage_id="lin-001",
            status=RefinementStatus.IN_PROGRESS,
            current_attempt_number=1,
        )
        restored = ImprovementLineage.model_validate_json(lin.model_dump_json())
        assert restored.lineage_id == "lin-001"
        assert restored.status == RefinementStatus.IN_PROGRESS


class TestEnforcedGuardrailEnum:
    def test_all_values_present(self):
        values = {g.value for g in EnforcedGuardrail}
        assert "no_lost_escalations" in values
        assert "custodian_clean" in values
        assert "no_architecture_violations" in values
        assert "regression_fixtures_pass" in values
        assert "no_runtime_policy_widening" in values

    def test_string_coercion(self):
        assert EnforcedGuardrail("custodian_clean") == EnforcedGuardrail.CUSTODIAN_CLEAN


class TestEvaluationCommandSourceEnum:
    def test_values(self):
        assert EvaluationCommandSource.OC_DERIVED.value == "oc_derived"
        assert EvaluationCommandSource.PROPOSER_SUGGESTED.value == "proposer_suggested"
        assert EvaluationCommandSource.VALIDATION_PROFILE.value == "validation_profile"
