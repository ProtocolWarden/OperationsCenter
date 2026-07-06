# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
contracts — OperationsCenter's internal subtype of the CxRP envelope.

The canonical cross-repo wire contract is **CxRP**
(``cxrp.contracts``). The classes here are OperationsCenter's *internal*
Pydantic representation: they layer narrower types (``LaneName``,
``BackendName``, structured ``TaskTarget``/``BranchPolicy``/
``ValidationProfile``) on top of CxRP's open envelope so adapters and
policy can rely on stricter shapes within OC. Where OC previously kept
local compatibility mirrors, prefer the canonical CxRP types directly;
``contracts.execution.RuntimeBindingSummary`` is now a compatibility
alias to ``cxrp.contracts.RuntimeBinding``.

At repo boundaries (HTTP between OC ↔ SwitchBoard, JSON written for
OperatorConsole, run artifacts) these models are translated to CxRP shape
via ``operations_center.contracts.cxrp_mapper``. The wire format is CxRP;
this module is the OC-internal subtype.

Canonical wire format:  CxRP v0.2 (https://github.com/ProtocolWarden/CxRP)
Internal owner:         OperationsCenter (Pydantic; this package)
"""

from .ci import (
    ClpBinding,
    ContinuousImprovementSpec,
    EvaluationScore,
    EvaluationSpec,
    ImprovementLineage,
    ImprovementStrategy,
    LineageAttempt,
    OcContinuousImprovementState,
    OcLineageIndexEntry,
    RefinementPolicy,
    ScoringMetric,
)
from .common import (
    BranchPolicy,
    ChangedFileRef,
    ExecutionConstraints,
    TaskTarget,
    ValidationProfile,
    ValidationSummary,
)
from .enums import (
    ArtifactType,
    BackendName,
    EnforcedGuardrail,
    EvaluationCommandSource,
    EvaluationOutcome,
    EvidenceType,
    ExecutionMode,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    LineageBranchReason,
    Priority,
    RefinementDecision,
    RefinementStatus,
    RiskLevel,
    TaskType,
    ValidationStatus,
)
from .evidence import RuleEvidence
from .execution import (
    ExecutionArtifact,
    ExecutionRequest,
    ExecutionResult,
    OcExecutionRequest,
    OcExecutionResult,
    RunTelemetry,
)
from .proposal import OcPlanningProposal, TaskProposal
from .routing import LaneDecision, OcRoutingDecision

__all__ = [
    # enums
    "ArtifactType",
    "BackendName",
    "EnforcedGuardrail",
    "EvaluationCommandSource",
    "EvaluationOutcome",
    "EvidenceType",
    "ExecutionMode",
    "ExecutionStatus",
    "FailureReasonCategory",
    "LaneName",
    "LineageBranchReason",
    "Priority",
    "RefinementDecision",
    "RefinementStatus",
    "RiskLevel",
    "TaskType",
    "ValidationStatus",
    # evidence models
    "RuleEvidence",
    # value objects
    "BranchPolicy",
    "ChangedFileRef",
    "ExecutionConstraints",
    "TaskTarget",
    "ValidationProfile",
    "ValidationSummary",
    # continuous improvement
    "ClpBinding",
    "ContinuousImprovementSpec",
    "EvaluationScore",
    "EvaluationSpec",
    "ImprovementLineage",
    "ImprovementStrategy",
    "LineageAttempt",
    "OcContinuousImprovementState",
    "OcLineageIndexEntry",
    "RefinementPolicy",
    "ScoringMetric",
    # top-level models
    "OcPlanningProposal",
    "OcRoutingDecision",
    "TaskProposal",
    "LaneDecision",
    "ExecutionArtifact",
    "OcExecutionRequest",
    "OcExecutionResult",
    "ExecutionRequest",
    "ExecutionResult",
    "RunTelemetry",
]
