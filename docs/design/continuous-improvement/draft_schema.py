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
from ContextLifecycleProtocol. Paths are relative to the target repo root.

Design doc: docs/design/continuous-improvement/design.md
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
    ACCEPTED = "accepted"         # an attempt met all guardrails and was proposed
    BUDGET_EXHAUSTED = "budget_exhausted"  # max_attempts reached without acceptance
    ABANDONED = "abandoned"       # explicitly halted (policy block, invariant violation)
    ESCALATED = "escalated"       # requires operator decision


class RefinementDecision(str, Enum):
    """Decision emitted after evaluating a single attempt."""
    ACCEPT = "accept"       # attempt passes all guardrails; propose for merge
    RETRY = "retry"         # attempt failed a non-fatal gate; retry within budget
    ABANDON = "abandon"     # attempt violated a hard invariant; no retry
    ESCALATE = "escalate"   # ambiguous outcome; requires operator decision


class EvaluationOutcome(str, Enum):
    """Coarse evaluation result for a single attempt."""
    IMPROVED = "improved"         # primary metric improved, all guardrails passed
    NEUTRAL = "neutral"           # no regression, no improvement
    REGRESSED = "regressed"       # primary metric worsened
    GUARDRAIL_VIOLATED = "guardrail_violated"  # hard gate failed
    INCONCLUSIVE = "inconclusive" # not enough evidence to score


class LineageBranchReason(str, Enum):
    """Why a new lineage branch was created."""
    INITIAL = "initial"
    RETRY_AFTER_FAILURE = "retry_after_failure"
    STRATEGY_VARIATION = "strategy_variation"
    OPERATOR_RESTART = "operator_restart"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class ImprovementStrategy(BaseModel):
    """
    Describes the semantic approach the worker should use.

    ``principle`` is a one-sentence description of the improvement heuristic.
    ``constraints`` are OC-enforced hard limits on what the worker may change.
    These must be a subset of the originating proposal's constraints.
    """
    principle: str = Field(
        description="One-sentence improvement heuristic, e.g. 'fingerprint repeated failures and apply cooldown'",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Hard limits on the improvement attempt. Must include at least 'fail_closed'. "
            "Examples: fail_closed, preserve_existing_escalations, no_runtime_policy_widening"
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
        description="Minimum required improvement to pass this metric",
    )
    unit: Optional[str] = Field(default=None, description="Unit for reporting, e.g. 'count', 'percent'")


class EvaluationSpec(BaseModel):
    """
    Defines how an improvement attempt will be evaluated.

    ``baseline`` is a reference to the behavior being compared against.
    ``primary_scoring`` defines the improvement target.
    ``guardrails`` are conditions that must ALL pass; violating any single
    guardrail is grounds for ABANDON regardless of primary score.
    """
    baseline_description: str = Field(
        description="Human-readable description of the baseline behavior, e.g. 'current_watchdog_behavior'",
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
    guardrails: list[str] = Field(
        description=(
            "Hard-stop conditions. Any violation → ABANDON. "
            "Examples: no_lost_escalations, custodian_clean, no_architecture_violations, "
            "regression_fixtures_pass"
        ),
    )
    evaluation_command: Optional[str] = Field(
        default=None,
        description=(
            "Optional deterministic command to produce evaluation evidence. "
            "Must be runnable from target repo root."
        ),
    )


# ---------------------------------------------------------------------------
# Refinement policy
# ---------------------------------------------------------------------------

class RefinementPolicy(BaseModel):
    """
    Governs retry and refinement behavior.

    ``max_attempts`` counts total executions including the initial attempt.
    ``requires_checkpoint_between_attempts`` forces a CLP checkpoint before
    each retry — the operator can inspect and intervene between attempts.
    ``failure_penalty`` applies a budget deduction when an attempt fails a
    non-primary guardrail — prevents cheap retries on bad attempts.
    """
    enabled: bool = Field(default=True)
    max_attempts: int = Field(default=3, ge=1, le=10)
    requires_checkpoint_between_attempts: bool = Field(
        default=True,
        description="If true, a LoopCheckpoint must be written and inspectable before each retry",
    )
    vary_strategy_on_retry: bool = Field(
        default=False,
        description="If true, the worker may vary strategy.variation_hint between attempts",
    )
    failure_penalty: int = Field(
        default=0,
        ge=0,
        description="Additional attempts consumed when a hard guardrail fails. Prevents degenerate retries.",
    )
    accept_on_neutral: bool = Field(
        default=False,
        description="If true, a NEUTRAL evaluation outcome (no regression, no improvement) counts as acceptance",
    )


# ---------------------------------------------------------------------------
# CLP binding
# ---------------------------------------------------------------------------

class ClpBinding(BaseModel):
    """
    References to CLP artifacts that provide continuity across refinement attempts.

    All paths are relative to the target repo root (.context/).
    These are path references only — not live objects.
    The worker loads them on resume via standard CLP schema.
    """
    investigation_capsule_path: Optional[str] = Field(
        default=None,
        description="Path to active InvestigationCapsule, e.g. '.context/active/inv-<id>.yaml'",
    )
    loop_checkpoint_path: Optional[str] = Field(
        default=None,
        description="Path to latest LoopCheckpoint, e.g. '.context/checkpoints/chk-<id>.yaml'",
    )
    worker_handoff_path: Optional[str] = Field(
        default=None,
        description="Path to active WorkerHandoff, e.g. '.context/handoffs/handoff-<id>.yaml'",
    )
    worker_scope_path: Optional[str] = Field(
        default=None,
        description="Path to scope definition (within the handoff or separately stored)",
    )
    lease_expires_at: Optional[datetime] = Field(
        default=None,
        description="Copied from active lease for quick expiry checks without loading YAML",
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
    guardrails_passed: list[str] = Field(default_factory=list)
    guardrails_failed: list[str] = Field(default_factory=list)
    outcome: EvaluationOutcome = EvaluationOutcome.INCONCLUSIVE
    evidence_paths: list[str] = Field(
        default_factory=list,
        description="Paths to evaluation evidence artifacts",
    )
    evaluation_notes: Optional[str] = Field(default=None)


class LineageAttempt(BaseModel):
    """
    Complete record of a single refinement attempt.

    One LineageAttempt is appended per execution. Together they form the
    attempt history that OC and operators can inspect, replay, or audit.
    """
    attempt_number: int = Field(ge=1)
    run_id: str = Field(description="OcExecutionRequest.run_id for this attempt")
    branch_reason: LineageBranchReason = LineageBranchReason.INITIAL
    strategy_used: ImprovementStrategy
    started_at: datetime
    completed_at: Optional[datetime] = None

    # Artifact references (all path-based; actual content in Warehouse or repo)
    implementation_artifact_path: Optional[str] = Field(
        default=None,
        description="Path to the diff/patch produced by this attempt",
    )
    evaluation_artifact_path: Optional[str] = Field(
        default=None,
        description="Path to the evaluation evidence report",
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

    # Replay metadata — enough to reproduce this attempt deterministically
    replay_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Environment snapshot for replay: goal_hash, strategy_hash, "
            "base_commit_sha, validation_command_hashes, etc."
        ),
    )


class ImprovementLineage(BaseModel):
    """
    Full history of refinement attempts for this work item.

    Append-only in normal operation. Each attempt produces one LineageAttempt.
    ``current_attempt_number`` increments after each dispatch.
    ``accepted_attempt_number`` is set when an attempt reaches ACCEPT decision.
    """
    current_attempt_number: int = Field(default=0, ge=0)
    accepted_attempt_number: Optional[int] = Field(default=None)
    status: RefinementStatus = RefinementStatus.NOT_STARTED
    attempts: list[LineageAttempt] = Field(default_factory=list)
    lineage_id: str = Field(
        description="Stable identifier for this lineage chain (survives retries)",
    )
    parent_lineage_id: Optional[str] = Field(
        default=None,
        description="If this lineage was forked from another, reference to the parent",
    )


# ---------------------------------------------------------------------------
# Top-level extension block
# ---------------------------------------------------------------------------

class ContinuousImprovementSpec(BaseModel):
    """
    Continuous improvement metadata for a work item.

    Attach to OcPlanningProposal as::

        proposal.continuous_improvement: Optional[ContinuousImprovementSpec]

    Presence of this block opts the work item into the CI lifecycle:
    evaluation-driven iteration governed by OC, with CLP-backed continuity
    and full artifact lineage.

    Absence of this block preserves one-shot behavior. No behavioral change
    to existing proposals.
    """
    strategy: ImprovementStrategy
    evaluation: EvaluationSpec
    refinement: RefinementPolicy = Field(default_factory=RefinementPolicy)
    clp: ClpBinding = Field(default_factory=ClpBinding)

    # Lineage is initially None; populated by OC on first dispatch and
    # updated after each attempt. Proposals are immutable, so lineage lives
    # in the mutable execution state, not in the frozen proposal.
    # This field exists here for schema documentation; in practice lineage
    # is carried by OcContinuousImprovementState (see below).
    _lineage_ref: Optional[str] = Field(
        default=None,
        description="Path to lineage artifact; populated by OC at dispatch time",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Mutable execution-side state (separate from the frozen proposal)
# ---------------------------------------------------------------------------

class OcContinuousImprovementState(BaseModel):
    """
    Mutable continuous improvement execution state.

    While ContinuousImprovementSpec (attached to the immutable proposal)
    carries the policy, this object carries live execution state: lineage,
    current attempt count, and OC-managed status.

    Stored alongside OcExecutionRequest/OcExecutionResult in the run store.
    Not frozen — updated after each attempt by OC.
    """
    proposal_id: str
    lineage: ImprovementLineage
    spec_snapshot: ContinuousImprovementSpec = Field(
        description="Snapshot of the spec at dispatch time (immutable reference)",
    )
    clp_binding: ClpBinding = Field(
        description="Live CLP artifact paths; updated as new artifacts are written",
    )
    last_updated_at: datetime
    operator_override: Optional[str] = Field(
        default=None,
        description="If set, operator has overridden the automated decision",
    )
