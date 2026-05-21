# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
enums.py — canonical enumerated types for the platform contract layer.

All enums are str-based so they round-trip cleanly through JSON/YAML
without custom serialisers.
"""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    """Broad category of work being proposed."""
    LINT_FIX = "lint_fix"
    BUG_FIX = "bug_fix"
    SIMPLE_EDIT = "simple_edit"
    TEST_WRITE = "test_write"
    DOCUMENTATION = "documentation"
    REFACTOR = "refactor"
    FEATURE = "feature"
    DEPENDENCY_UPDATE = "dependency_update"
    UNKNOWN = "unknown"


class LaneName(str, Enum):
    """Execution lanes available in the platform."""
    CLAUDE_CLI = "claude_cli"
    CODEX_CLI = "codex_cli"
    AIDER_LOCAL = "aider_local"


class BackendName(str, Enum):
    """Backend implementations that execute tasks inside a lane."""
    DIRECT_LOCAL = "direct_local"
    AIDER_LOCAL = "aider_local"
    TEAM_EXECUTOR = "team_executor"
    DAG_EXECUTOR = "dag_executor"
    CRITIQUE_EXECUTOR = "critique_executor"
    OPENCLAW = "openclaw"
    DEMO_STUB = "demo_stub"


class ExecutionMode(str, Enum):
    """High-level execution strategy for the run."""
    GOAL = "goal"
    FIX_PR = "fix_pr"
    TEST_CAMPAIGN = "test_campaign"
    IMPROVE_CAMPAIGN = "improve_campaign"


class ExecutionStatus(str, Enum):
    """Terminal or in-progress outcome of an execution run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ArtifactType(str, Enum):
    """Type of artifact produced during execution."""
    DIFF = "diff"
    PATCH = "patch"
    VALIDATION_REPORT = "validation_report"
    LOG_EXCERPT = "log_excerpt"
    GOAL_FILE = "goal_file"
    PR_URL = "pr_url"
    BRANCH_REF = "branch_ref"


class ValidationStatus(str, Enum):
    """Outcome of a validation step."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class RiskLevel(str, Enum):
    """Risk estimate for a proposed change."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(str, Enum):
    """Scheduling priority for the task."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class FailureReasonCategory(str, Enum):
    """Coarse failure category for routing and retry decisions."""
    VALIDATION_FAILED = "validation_failed"
    BACKEND_ERROR = "backend_error"
    UNSUPPORTED_REQUEST = "unsupported_request"
    TIMEOUT = "timeout"
    NO_CHANGES = "no_changes"
    CONFLICT = "conflict"
    POLICY_BLOCKED = "policy_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ROUTING_ERROR = "routing_error"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Continuous improvement enums
# ---------------------------------------------------------------------------

class RefinementStatus(str, Enum):
    """Overall status of the refinement lifecycle for a work item."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ABANDONED = "abandoned"
    ESCALATED = "escalated"


class RefinementDecision(str, Enum):
    """Decision emitted after evaluating a single attempt."""
    ACCEPT = "accept"
    RETRY = "retry"
    ABANDON = "abandon"
    ESCALATE = "escalate"


class EvaluationOutcome(str, Enum):
    """Coarse evaluation result for a single attempt."""
    IMPROVED = "improved"
    NEUTRAL = "neutral"
    REGRESSED = "regressed"
    GUARDRAIL_VIOLATED = "guardrail_violated"
    INCONCLUSIVE = "inconclusive"


class LineageBranchReason(str, Enum):
    """Why a new lineage branch was created."""
    INITIAL = "initial"
    RETRY_AFTER_FAILURE = "retry_after_failure"
    STRATEGY_VARIATION = "strategy_variation"
    OPERATOR_RESTART = "operator_restart"


class EnforcedGuardrail(str, Enum):
    """
    Closed enum of guardrails OC and ContextGuard/Custodian enforce automatically.

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
    """How evaluation_command was determined."""
    OC_DERIVED = "oc_derived"
    PROPOSER_SUGGESTED = "proposer_suggested"
    VALIDATION_PROFILE = "validation_profile"
