# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
draft_schema.py — Continuous improvement schema extension draft.

STATUS: DRAFT — not yet wired into production contracts.

This module defines the ``ContinuousImprovementSpec`` block and all
supporting types for the platform work-item continuous-improvement extension.

Intended integration point:
  OcPlanningProposal.continuous_improvement: Optional[ContinuousImprovementSpec]

CLP artifact paths are carried as string references only — no live imports
from ContextLifecycle. Paths are relative to the target repo root.

Design doc: docs/design/continuous-improvement/design.md

Open questions resolved (2026-05-21):
  Q1 evaluation_command ownership → OC derives from validation_profile; proposer may suggest
  Q2 guardrail extensibility → closed EnforcedGuardrail enum + advisory custom_checks list
  Q3 lineage storage → .context/capsules/ (CLP-native); OC indexes; Warehouse archives
  Q4 CxRP wire extension → stay OC-internal; not in TaskProposal yet
  Q5 ExecutionMode → no new entry; improve_campaign + CI block presence is sufficient
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RefinementStatus(str, Enum):
    """Overall status of the refinement lifecycle for this work item."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"  # an attempt met all guardrails; proposed for merge
    BUDGET_EXHAUSTED = "budget_exhausted"  # max_attempts reached without acceptance
    ABANDONED = "abandoned"  # explicitly halted (hard invariant violation)
    ESCALATED = "escalated"  # requires operator decision


class RefinementDecision(str, Enum):
    """Decision emitted after evaluating a single attempt."""

    ACCEPT = "accept"  # attempt passes all guardrails; propose for merge
    RETRY = "retry"  # attempt failed a non-fatal gate; retry within budget
    ABANDON = "abandon"  # attempt violated a hard invariant; no retry
    ESCALATE = "escalate"  # ambiguous outcome; requires operator decision


class EvaluationOutcome(str, Enum):
    """Coarse evaluation result for a single attempt."""

    IMPROVED = "improved"  # primary metric improved, all guardrails passed
    NEUTRAL = "neutral"  # no regression, no improvement
    REGRESSED = "regressed"  # primary metric worsened
    GUARDRAIL_VIOLATED = "guardrail_violated"  # hard gate failed
    INCONCLUSIVE = "inconclusive"  # not enough evidence to score


class LineageBranchReason(str, Enum):
    """Why a new lineage branch was created."""

    INITIAL = "initial"
    RETRY_AFTER_FAILURE = "retry_after_failure"
    STRATEGY_VARIATION = "strategy_variation"
    OPERATOR_RESTART = "operator_restart"


class EnforcedGuardrail(str, Enum):
    """
    Closed enum of guardrails that ContextGuard and Custodian can enforce automatically.

    Q2 decision: closed enum for machine-enforceable gates. Each value maps to a
    concrete check OC runs post-execution. Advisory notes go in custom_checks.

    Enforcement mapping:
      NO_LOST_ESCALATIONS       → compare escalation count before/after attempt
      CUSTODIAN_CLEAN           → run Custodian; require 0 findings on result diff
      NO_ARCHITECTURE_VIOLATIONS → run Custodian X2/B1 boundary checks
      REGRESSION_FIXTURES_PASS  → re-run validation_profile.commands on result branch
      NO_RUNTIME_POLICY_WIDENING → static check: forbidden_paths unchanged, no policy file edits
    """

    NO_LOST_ESCALATIONS = "no_lost_escalations"
    CUSTODIAN_CLEAN = "custodian_clean"
    NO_ARCHITECTURE_VIOLATIONS = "no_architecture_violations"
    REGRESSION_FIXTURES_PASS = "regression_fixtures_pass"
    NO_RUNTIME_POLICY_WIDENING = "no_runtime_policy_widening"


class EvaluationCommandSource(str, Enum):
    """
    How the evaluation_command was determined.

    Q1 decision: OC derives the command from validation_profile; proposer may suggest
    one but OC validates/normalizes it before use.
    """

    OC_DERIVED = "oc_derived"  # OC built the command from validation_profile
    PROPOSER_SUGGESTED = "proposer_suggested"  # proposer suggested; OC validated
    VALIDATION_PROFILE = "validation_profile"  # taken directly from validation_profile.commands


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class ImprovementStrategy(BaseModel):
    """
    Describes the semantic approach the worker should use.

    ``principle`` is a one-sentence description of the improvement heuristic.
    ``constraints`` are hard limits propagated directly into WorkerHandoff.worker_scope
    and enforced by ContextGuard — not advisory.
    """

    principle: str = Field(
        description="One-sentence improvement heuristic",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Hard limits on the attempt. Must include 'fail_closed'. "
            "Propagated into WorkerHandoff worker_scope and enforced by ContextGuard."
        ),
    )
    variation_hint: Optional[str] = Field(
        default=None,
        description="For retry attempts: what to vary relative to the previous attempt",
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class ScoringMetric(BaseModel):
    """A single measurable improvement target."""

    metric: str = Field(description="Name of the metric, e.g. 'duplicate_escalations_reduced'")
    direction: str = Field(
        default="lower_is_better",
        description="'lower_is_better' or 'higher_is_better'",
    )
    baseline_value: Optional[float] = Field(
        default=None,
        description="Measured value before the improvement attempt",
    )
    target_delta: Optional[float] = Field(
        default=None,
        description="Minimum required improvement delta to pass this metric",
    )
    unit: Optional[str] = Field(default=None, description="Unit for reporting")


class EvaluationSpec(BaseModel):
    """
    Defines how an improvement attempt will be evaluated.

    Guardrail policy (Q2):
      ``guardrails`` is a closed list of EnforcedGuardrail values that OC and
      ContextGuard/Custodian can enforce automatically. All must pass; any
      violation → ABANDON regardless of primary score.

      ``custom_checks`` carries advisory notes or manual verification steps that
      an operator should inspect but that are not machine-enforced. Failures here
      do not trigger automatic ABANDON.

    Evaluation command policy (Q1):
      ``evaluation_command`` is set by OC, not the proposer. Proposers may put
      a suggestion in ``evaluation_command_hint``; OC validates, normalizes, and
      either adopts it or derives from validation_profile. The resolved command
      is stored in ``evaluation_command`` with its source in
      ``evaluation_command_source``.
    """

    baseline_description: str = Field(
        description="Human-readable description of baseline behavior",
    )
    baseline_artifact_path: Optional[str] = Field(
        default=None,
        description="Path to baseline measurement artifact, relative to target repo root",
    )
    primary_scoring: ScoringMetric = Field(
        description="The primary improvement metric",
    )
    secondary_scoring: list[ScoringMetric] = Field(
        default_factory=list,
        description="Additional metrics tracked but not required for acceptance",
    )

    # Q2: closed enum for enforced guardrails
    guardrails: list[EnforcedGuardrail] = Field(
        description=(
            "Machine-enforceable hard gates. All must pass; any violation → ABANDON. "
            "OC and ContextGuard/Custodian run these automatically."
        ),
    )
    custom_checks: list[str] = Field(
        default_factory=list,
        description=(
            "Advisory human-readable checks not enforced automatically. "
            "Operator should review before accepting. Failures do not trigger ABANDON."
        ),
    )

    # Q1: OC owns the resolved evaluation command
    evaluation_command_hint: Optional[str] = Field(
        default=None,
        description=(
            "Proposer-supplied evaluation command suggestion. "
            "OC validates and normalizes before use; may override entirely."
        ),
    )
    evaluation_command: Optional[str] = Field(
        default=None,
        description=(
            "Resolved evaluation command. Set by OC (not proposer) from validation_profile "
            "or by validating evaluation_command_hint. Must be runnable from target repo root."
        ),
    )
    evaluation_command_source: Optional[EvaluationCommandSource] = Field(
        default=None,
        description="How evaluation_command was determined. Set by OC at dispatch time.",
    )


# ---------------------------------------------------------------------------
# Refinement policy
# ---------------------------------------------------------------------------


class RefinementPolicy(BaseModel):
    """
    Governs retry and refinement behavior.

    ``max_attempts`` counts total executions including the initial attempt.
    ``requires_checkpoint_between_attempts`` forces a CLP LoopCheckpoint before
    each retry — operator can inspect and intervene between attempts.
    ``failure_penalty`` applies a budget deduction when an attempt fails a
    hard guardrail — prevents degenerate retries on bad attempts.
    """

    enabled: bool = Field(default=True)
    max_attempts: int = Field(default=3, ge=1, le=10)
    requires_checkpoint_between_attempts: bool = Field(
        default=True,
        description="LoopCheckpoint must be written and inspectable before each retry",
    )
    vary_strategy_on_retry: bool = Field(
        default=False,
        description="OC may update strategy.variation_hint between attempts",
    )
    failure_penalty: int = Field(
        default=0,
        ge=0,
        description="Extra attempts consumed when a hard guardrail fails.",
    )
    accept_on_neutral: bool = Field(
        default=False,
        description="NEUTRAL outcome (no regression, no improvement) counts as acceptance",
    )


# ---------------------------------------------------------------------------
# CLP binding
# ---------------------------------------------------------------------------


class ClpBinding(BaseModel):
    """
    References to CLP artifacts providing continuity across refinement attempts.

    Q3 decision: primary lineage lives in .context/capsules/<lineage_id>/ (CLP-native).
    OC run store keeps index/status. Warehouse archives after resolution.

    All paths are relative to the target repo root. Path references only —
    the worker loads artifacts at runtime via standard CLP schema.
    """

    investigation_capsule_path: Optional[str] = Field(
        default=None,
        description="Active InvestigationCapsule, e.g. '.context/active/inv-<id>.yaml'",
    )
    loop_checkpoint_path: Optional[str] = Field(
        default=None,
        description="Latest LoopCheckpoint, e.g. '.context/checkpoints/chk-<id>.yaml'",
    )
    worker_handoff_path: Optional[str] = Field(
        default=None,
        description="Active WorkerHandoff, e.g. '.context/handoffs/handoff-<id>.yaml'",
    )
    worker_scope_path: Optional[str] = Field(
        default=None,
        description="Scope definition path (within the handoff or separately stored)",
    )
    lease_expires_at: Optional[datetime] = Field(
        default=None,
        description="Copied from active lease for quick expiry checks without loading YAML",
    )
    # Q3: lineage canonical location
    lineage_artifact_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to ImprovementLineage JSON, e.g. "
            "'.context/capsules/<lineage_id>/lineage.json'. "
            "Canonical CLP-native location; OC indexes this path in run store."
        ),
    )


# ---------------------------------------------------------------------------
# Lineage — per-attempt records
# ---------------------------------------------------------------------------


class EvaluationScore(BaseModel):
    """Scored outcome of a single evaluation run."""

    primary_metric_value: Optional[float] = Field(default=None)
    primary_metric_delta: Optional[float] = Field(
        default=None,
        description="Value relative to baseline (negative = improvement for lower_is_better)",
    )
    secondary_metrics: dict[str, float] = Field(default_factory=dict)
    guardrails_passed: list[EnforcedGuardrail] = Field(default_factory=list)
    guardrails_failed: list[EnforcedGuardrail] = Field(default_factory=list)
    custom_checks_notes: Optional[str] = Field(
        default=None,
        description="Operator-facing notes on advisory custom_checks results",
    )
    outcome: EvaluationOutcome = EvaluationOutcome.INCONCLUSIVE
    evidence_paths: list[str] = Field(
        default_factory=list,
        description="Paths to evaluation evidence artifacts",
    )
    evaluation_command_used: Optional[str] = Field(
        default=None,
        description="The resolved evaluation_command that produced this score",
    )
    evaluation_notes: Optional[str] = Field(default=None)


class LineageAttempt(BaseModel):
    """
    Complete record of a single refinement attempt.

    One LineageAttempt is appended per execution. Stored in
    .context/capsules/<lineage_id>/attempt-<n>/ alongside implementation
    and evaluation artifacts.
    """

    attempt_number: int = Field(ge=1)
    run_id: str = Field(description="OcExecutionRequest.run_id for this attempt")
    branch_reason: LineageBranchReason = LineageBranchReason.INITIAL
    strategy_used: ImprovementStrategy
    started_at: datetime
    completed_at: Optional[datetime] = None

    # Artifact references — all paths relative to target repo root
    implementation_artifact_path: Optional[str] = Field(
        default=None,
        description="Diff/patch produced by this attempt",
    )
    evaluation_artifact_path: Optional[str] = Field(
        default=None,
        description="Evaluation evidence report",
    )
    checkpoint_path: Optional[str] = Field(
        default=None,
        description="CLP LoopCheckpoint written at end of this attempt",
    )
    capsule_snapshot_path: Optional[str] = Field(
        default=None,
        description="CLP InvestigationCapsule snapshot at end of this attempt",
    )

    # Outcome
    score: Optional[EvaluationScore] = None
    decision: Optional[RefinementDecision] = None
    decision_reason: Optional[str] = None

    # Replay metadata
    replay_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Environment snapshot for deterministic replay: base_commit_sha, "
            "goal_text_hash, strategy_principle_hash, validation_command_hashes, "
            "runtime_binding_kind, runtime_binding_model, evaluation_command_used, "
            "clp_schema_version, oc_schema_version."
        ),
    )


class OcLineageIndexEntry(BaseModel):
    """
    OC run-store index record for a lineage.

    Q3 decision: ImprovementLineage (full history) lives in CLP-native
    .context/capsules/<lineage_id>/lineage.json. OC keeps this lightweight
    index entry for fast status queries without loading the full lineage.
    Warehouse receives the full lineage artifact post-resolution.
    """

    lineage_id: str
    proposal_id: str
    lineage_artifact_path: str = Field(
        description="CLP-native path to full ImprovementLineage JSON",
    )
    status: RefinementStatus
    current_attempt_number: int
    accepted_attempt_number: Optional[int] = None
    last_updated_at: datetime
    warehouse_archive_id: Optional[str] = Field(
        default=None,
        description="Set by Warehouse after archival; null until resolved",
    )


class ImprovementLineage(BaseModel):
    """
    Full history of refinement attempts for this work item.

    Stored at: .context/capsules/<lineage_id>/lineage.json (CLP-native).
    OC indexes via OcLineageIndexEntry. Warehouse archives post-resolution.

    Append-only in normal operation.
    """

    lineage_id: str = Field(
        description="Stable identifier for this lineage chain (survives retries)",
    )
    parent_lineage_id: Optional[str] = Field(
        default=None,
        description="If this lineage was forked from another, reference to the parent",
    )
    status: RefinementStatus = RefinementStatus.NOT_STARTED
    current_attempt_number: int = Field(default=0, ge=0)
    accepted_attempt_number: Optional[int] = Field(default=None)
    attempts: list[LineageAttempt] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level extension block
# ---------------------------------------------------------------------------


class ContinuousImprovementSpec(BaseModel):
    """
    Continuous improvement metadata for a work item.

    Attach to OcPlanningProposal as:
        proposal.continuous_improvement: Optional[ContinuousImprovementSpec]

    Presence opts the work item into the CI lifecycle. Absence preserves
    one-shot behavior with no change to existing proposals.

    Q4: stays OC-internal. Not added to CxRP TaskProposal yet.
    Q5: no new ExecutionMode. improve_campaign + this block's presence is sufficient.
    """

    strategy: ImprovementStrategy
    evaluation: EvaluationSpec
    refinement: RefinementPolicy = Field(default_factory=RefinementPolicy)
    clp: ClpBinding = Field(default_factory=ClpBinding)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Mutable execution-side state (separate from frozen proposal)
# ---------------------------------------------------------------------------


class OcContinuousImprovementState(BaseModel):
    """
    Mutable CI execution state managed by OC.

    While ContinuousImprovementSpec (frozen on the proposal) carries policy,
    this object carries live execution state: lineage index, resolved evaluation
    command, and current CLP artifact paths.

    Stored in OC run store. Updated after each attempt.
    Full lineage artifact lives at clp_binding.lineage_artifact_path.
    """

    proposal_id: str
    lineage_index: OcLineageIndexEntry
    spec_snapshot: ContinuousImprovementSpec = Field(
        description="Snapshot of the spec at first dispatch (immutable reference)",
    )
    clp_binding: ClpBinding = Field(
        description="Live CLP artifact paths; updated as new artifacts are written",
    )
    resolved_evaluation_command: Optional[str] = Field(
        default=None,
        description=(
            "The evaluation command OC resolved from validation_profile or "
            "evaluation_command_hint. Stored here so retries use the same command."
        ),
    )
    resolved_evaluation_command_source: Optional[EvaluationCommandSource] = Field(
        default=None,
    )
    last_updated_at: datetime
    operator_override: Optional[str] = Field(
        default=None,
        description="If set, operator has overridden the automated decision",
    )
