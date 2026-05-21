# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
ci.py — Continuous improvement contract types.

Defines ``ContinuousImprovementSpec`` and all supporting types for
evaluation-driven refinement within existing execution lanes.

Integration point:
    OcPlanningProposal.continuous_improvement: Optional[ContinuousImprovementSpec]

Presence opts a work item into the CI lifecycle. Absence is a no-op.

Design doc: docs/design/continuous-improvement/design.md
CLP artifact paths are string references only — no live CLP imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from .enums import (
    EnforcedGuardrail,
    EvaluationCommandSource,
    EvaluationOutcome,
    LineageBranchReason,
    RefinementDecision,
    RefinementStatus,
)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class ImprovementStrategy(BaseModel):
    """
    Describes the semantic approach the worker should use.

    ``constraints`` must include ``"fail_closed"`` — enforced by Custodian
    at proposal-validation time. Propagated into WorkerHandoff.worker_scope.
    """

    principle: str = Field(description="One-sentence improvement heuristic")
    constraints: list[str] = Field(
        default_factory=list,
        description="Hard limits. Must include 'fail_closed'. Propagated into WorkerHandoff worker_scope.",
    )
    variation_hint: Optional[str] = Field(
        default=None,
        description="For retry attempts: what to vary relative to the previous attempt",
    )

    @model_validator(mode="after")
    def _require_fail_closed(self) -> "ImprovementStrategy":
        if "fail_closed" not in self.constraints:
            raise ValueError(
                "ImprovementStrategy.constraints must include 'fail_closed'. "
                "CI proposals are fail-closed by invariant."
            )
        return self

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class ScoringMetric(BaseModel):
    """A single measurable improvement target."""

    metric: str = Field(description="Name of the metric, e.g. 'false_retry_rate'")
    direction: str = Field(
        default="lower_is_better",
        description="'lower_is_better' or 'higher_is_better'",
    )
    baseline_value: Optional[float] = Field(default=None)
    target_delta: Optional[float] = Field(
        default=None,
        description="Minimum required improvement delta to pass",
    )
    unit: Optional[str] = None

    model_config = {"frozen": True}


class EvaluationSpec(BaseModel):
    """
    Defines how an improvement attempt will be evaluated.

    Guardrail policy: ``guardrails`` is a closed list of EnforcedGuardrail
    values that OC and ContextGuard/Custodian enforce automatically.
    All must pass; any violation → ABANDON regardless of primary score.
    Advisory notes go in ``custom_checks`` — not machine-enforced.

    Evaluation command policy: ``evaluation_command`` is set by OC, not
    the proposer. Proposers may put a suggestion in
    ``evaluation_command_hint``; OC validates, normalizes, and either
    adopts it or derives from validation_profile.
    """

    baseline_description: str = Field(description="Human-readable description of baseline behavior")
    baseline_artifact_path: Optional[str] = Field(
        default=None,
        description="Path to baseline measurement artifact, relative to target repo root",
    )
    primary_scoring: ScoringMetric
    secondary_scoring: list[ScoringMetric] = Field(default_factory=list)

    guardrails: list[EnforcedGuardrail] = Field(
        description=(
            "Machine-enforceable hard gates. All must pass; any violation → ABANDON. "
            "OC and ContextGuard/Custodian run these automatically."
        ),
    )
    custom_checks: list[str] = Field(
        default_factory=list,
        description="Advisory checks not enforced automatically. Failures do not trigger ABANDON.",
    )

    evaluation_command_hint: Optional[str] = Field(
        default=None,
        description="Proposer-supplied evaluation command suggestion. OC may override.",
    )
    evaluation_command: Optional[str] = Field(
        default=None,
        description="Resolved evaluation command. Set by OC at dispatch time.",
    )
    evaluation_command_source: Optional[EvaluationCommandSource] = Field(
        default=None,
        description="How evaluation_command was determined. Set by OC.",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Refinement policy
# ---------------------------------------------------------------------------

class RefinementPolicy(BaseModel):
    """
    Governs retry and refinement behaviour.

    ``max_attempts`` counts total executions including the initial attempt.
    ``requires_checkpoint_between_attempts`` forces a CLP LoopCheckpoint
    before each retry so the operator can inspect and intervene.
    """

    enabled: bool = Field(default=True)
    max_attempts: int = Field(default=3, ge=1, le=10)
    requires_checkpoint_between_attempts: bool = Field(
        default=True,
        description="LoopCheckpoint must be written before each retry",
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
        description="NEUTRAL outcome counts as acceptance",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# CLP binding
# ---------------------------------------------------------------------------

class ClpBinding(BaseModel):
    """
    References to CLP artifacts providing continuity across refinement attempts.

    Primary lineage lives in .context/capsules/<lineage_id>/lineage.json
    (CLP-native). OC indexes via OcLineageIndexEntry. Warehouse archives
    after resolution. All paths relative to the target repo root.
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
    lease_expires_at: Optional[datetime] = Field(
        default=None,
        description="Copied from active lease for quick expiry checks",
    )
    lineage_artifact_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to ImprovementLineage JSON, e.g. "
            "'.context/capsules/<lineage_id>/lineage.json'. "
            "CLP-native canonical location; OC indexes this path in run store."
        ),
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Lineage — per-attempt records
# ---------------------------------------------------------------------------

class EvaluationScore(BaseModel):
    """Scored outcome of a single evaluation run."""

    primary_metric_value: Optional[float] = None
    primary_metric_delta: Optional[float] = Field(
        default=None,
        description="Value relative to baseline (negative = improvement for lower_is_better)",
    )
    secondary_metrics: dict[str, float] = Field(default_factory=dict)
    guardrails_passed: list[EnforcedGuardrail] = Field(default_factory=list)
    guardrails_failed: list[EnforcedGuardrail] = Field(default_factory=list)
    custom_checks_notes: Optional[str] = None
    outcome: EvaluationOutcome = EvaluationOutcome.INCONCLUSIVE
    evidence_paths: list[str] = Field(default_factory=list)
    evaluation_command_used: Optional[str] = None
    evaluation_notes: Optional[str] = None

    model_config = {"frozen": True}


class LineageAttempt(BaseModel):
    """
    Complete record of a single refinement attempt.

    One LineageAttempt is appended per execution, stored in
    .context/capsules/<lineage_id>/attempt-<n>/ alongside artifacts.
    """

    attempt_number: int = Field(ge=1)
    run_id: str = Field(description="OcExecutionRequest.run_id for this attempt")
    branch_reason: LineageBranchReason = LineageBranchReason.INITIAL
    strategy_used: ImprovementStrategy
    started_at: datetime
    completed_at: Optional[datetime] = None

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

    score: Optional[EvaluationScore] = None
    decision: Optional[RefinementDecision] = None
    decision_reason: Optional[str] = None

    replay_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Environment snapshot for replay: base_commit_sha, goal_text_hash, "
            "strategy_principle_hash, validation_command_hashes, "
            "runtime_binding_kind, runtime_binding_model, clp_schema_version, "
            "oc_schema_version."
        ),
    )

    model_config = {"frozen": True}


class OcLineageIndexEntry(BaseModel):
    """
    OC run-store index record for a lineage.

    ImprovementLineage (full history) lives at the CLP-native
    lineage_artifact_path. OC holds only this lightweight index entry for
    fast status queries. Warehouse receives the full artifact post-resolution.
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

    model_config = {"frozen": True}


class ImprovementLineage(BaseModel):
    """
    Full history of refinement attempts for this work item.

    Stored at: .context/capsules/<lineage_id>/lineage.json (CLP-native).
    OC indexes via OcLineageIndexEntry. Warehouse archives post-resolution.
    Append-only in normal operation.
    """

    lineage_id: str = Field(description="Stable identifier for this lineage chain")
    parent_lineage_id: Optional[str] = Field(
        default=None,
        description="If forked from another lineage, reference to the parent",
    )
    status: RefinementStatus = RefinementStatus.NOT_STARTED
    current_attempt_number: int = Field(default=0, ge=0)
    accepted_attempt_number: Optional[int] = None
    attempts: list[LineageAttempt] = Field(default_factory=list)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Top-level extension block
# ---------------------------------------------------------------------------

class ContinuousImprovementSpec(BaseModel):
    """
    Continuous improvement metadata for a work item.

    Attach to OcPlanningProposal as:
        proposal.continuous_improvement: Optional[ContinuousImprovementSpec]

    Presence opts the work item into the CI lifecycle. Absence preserves
    one-shot behaviour; no existing proposals are affected.

    OC-internal only — not propagated to CxRP TaskProposal.
    execution_mode: improve_campaign is reused; this block's presence is sufficient.
    """

    strategy: ImprovementStrategy
    evaluation: EvaluationSpec
    refinement: RefinementPolicy = Field(default_factory=RefinementPolicy)
    clp: ClpBinding = Field(default_factory=ClpBinding)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Mutable execution-side state (separate from the frozen proposal)
# ---------------------------------------------------------------------------

class OcContinuousImprovementState(BaseModel):
    """
    Mutable CI execution state managed by OC during a refinement lifecycle.

    ``ContinuousImprovementSpec`` (frozen on the proposal) carries policy.
    This object carries live execution state: lineage index, resolved
    evaluation command, current CLP artifact paths.

    Stored in the CI store (state/ci_lineage.json). Updated after each attempt.
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
        description="The evaluation command OC resolved. Stored so retries use the same command.",
    )
    resolved_evaluation_command_source: Optional[EvaluationCommandSource] = None
    last_updated_at: datetime
    operator_override: Optional[str] = Field(
        default=None,
        description="If set, operator has overridden the automated decision",
    )

    model_config = {"frozen": True}
