# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration test fixtures and builders for verdict consolidation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Verdict Models ────────────────────────────────────────────────────────────


@dataclass
class AuditVerdict:
    """Represents a single audit verdict from the executor."""

    result: str  # "LGTM" | "CONCERNS" | None
    summary: str = ""
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LaneVerdict:
    """Represents a consolidated verdict from a single review lane."""

    lane_id: str
    verdict: AuditVerdict
    reviewed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    phase: str = "self_review"

    def to_dict(self) -> dict:
        return {
            "lane_id": self.lane_id,
            "verdict": self.verdict.to_dict(),
            "reviewed_at": self.reviewed_at,
            "phase": self.phase,
        }


@dataclass
class MergeDecision:
    """Represents the final merge decision from verdict consolidation."""

    decision: str  # "merge" | "blocked" | "retry" | "escalate"
    reason: str
    consolidated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    lane_verdicts: list[LaneVerdict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "consolidated_at": self.consolidated_at,
            "lane_verdicts": [v.to_dict() for v in self.lane_verdicts],
        }


# ── Test Data Builders ────────────────────────────────────────────────────────


class AuditVerdictBuilder:
    """Builder for creating audit verdict fixtures."""

    def __init__(self):
        self.result = "LGTM"
        self.summary = "All checks passed"
        self.details = ""

    def with_result(self, result: str) -> AuditVerdictBuilder:
        self.result = result
        return self

    def with_summary(self, summary: str) -> AuditVerdictBuilder:
        self.summary = summary
        return self

    def with_details(self, details: str) -> AuditVerdictBuilder:
        self.details = details
        return self

    def lgtm(self) -> AuditVerdictBuilder:
        self.result = "LGTM"
        self.summary = "All checks passed"
        return self

    def concerns(self, concerns_text: str = "Issues found") -> AuditVerdictBuilder:
        self.result = "CONCERNS"
        self.summary = concerns_text
        return self

    def no_verdict(self) -> AuditVerdictBuilder:
        self.result = None
        self.summary = ""
        return self

    def build(self) -> AuditVerdict:
        return AuditVerdict(
            result=self.result,
            summary=self.summary,
            details=self.details,
        )


class LaneVerdictBuilder:
    """Builder for creating lane verdict fixtures."""

    def __init__(self, lane_id: str = "default"):
        self.lane_id = lane_id
        self.verdict_builder = AuditVerdictBuilder()
        self.reviewed_at = datetime.now(UTC).isoformat()
        self.phase = "self_review"

    def with_lane_id(self, lane_id: str) -> LaneVerdictBuilder:
        self.lane_id = lane_id
        return self

    def with_verdict(self, verdict: AuditVerdict) -> LaneVerdictBuilder:
        self.verdict_builder = None
        self.verdict = verdict
        return self

    def with_phase(self, phase: str) -> LaneVerdictBuilder:
        self.phase = phase
        return self

    def lgtm(self) -> LaneVerdictBuilder:
        self.verdict_builder.lgtm()
        return self

    def concerns(self, concerns_text: str = "Issues found") -> LaneVerdictBuilder:
        self.verdict_builder.concerns(concerns_text)
        return self

    def no_verdict(self) -> LaneVerdictBuilder:
        self.verdict_builder.no_verdict()
        return self

    def build(self) -> LaneVerdict:
        verdict = self.verdict if hasattr(self, "verdict") else self.verdict_builder.build()
        return LaneVerdict(
            lane_id=self.lane_id,
            verdict=verdict,
            reviewed_at=self.reviewed_at,
            phase=self.phase,
        )


class MergeDecisionBuilder:
    """Builder for creating merge decision fixtures."""

    def __init__(self):
        self.decision = "merge"
        self.reason = "unanimous_lgtm"
        self.consolidated_at = datetime.now(UTC).isoformat()
        self.lane_verdicts = []

    def with_decision(self, decision: str) -> MergeDecisionBuilder:
        self.decision = decision
        return self

    def with_reason(self, reason: str) -> MergeDecisionBuilder:
        self.reason = reason
        return self

    def add_lane_verdict(self, lane_verdict: LaneVerdict) -> MergeDecisionBuilder:
        self.lane_verdicts.append(lane_verdict)
        return self

    def merge(self) -> MergeDecisionBuilder:
        self.decision = "merge"
        self.reason = "unanimous_lgtm"
        return self

    def blocked(self) -> MergeDecisionBuilder:
        self.decision = "blocked"
        self.reason = "concerns_unresolvable"
        return self

    def retry(self) -> MergeDecisionBuilder:
        self.decision = "retry"
        self.reason = "mixed_verdicts"
        return self

    def escalate(self) -> MergeDecisionBuilder:
        self.decision = "escalate"
        self.reason = "backend_unavailable"
        return self

    def build(self) -> MergeDecision:
        return MergeDecision(
            decision=self.decision,
            reason=self.reason,
            consolidated_at=self.consolidated_at,
            lane_verdicts=self.lane_verdicts,
        )


# ── State File Helpers ────────────────────────────────────────────────────────


def create_pr_state(
    repo_key: str = "TestRepo",
    pr_number: int = 42,
    phase: str = "self_review",
    **overrides: Any,
) -> dict:
    """Create a PR review state dictionary with optional overrides."""
    now = datetime.now(UTC).isoformat()
    state = {
        "state_key": f"{repo_key}-{pr_number}",
        "pr_number": pr_number,
        "repo_key": repo_key,
        "phase": phase,
        "ci_fix_attempts": 0,
        "ci_fix_last_push_at": None,
        "self_review_loops": 0,
        "no_verdict_passes": 0,
        "fix_attempts": 0,
        "human_review_loops": 0,
        "processed_comment_ids": [],
        "plane_task_id": None,
        "phase2_entered_at": None,
        "escalated_needs_human": False,
        "ci_wait_cycles": 0,
        "created_at": now,
        "updated_at": now,
    }
    state.update(overrides)
    return state


def save_pr_state(tmp_path: Path, state: dict) -> Path:
    """Save PR state to disk and return path."""
    state_dir = tmp_path / "state" / "pr_reviews"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"{state['state_key']}.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_path


def load_pr_state(state_path: Path) -> dict:
    """Load PR state from disk."""
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Mock Client Factories ─────────────────────────────────────────────────────


def mock_github_client() -> MagicMock:
    """Create a mocked GitHub PR client."""
    gh = MagicMock()
    gh.get_pr_diff.return_value = "diff --git a/test.py\n+pass"
    gh.list_pr_comments.return_value = []
    gh.get_pr_reactions.return_value = []
    gh.has_thumbs_up.return_value = False
    gh.post_comment.return_value = {}
    gh.merge_pr.return_value = {}
    gh.close_pr.return_value = {}
    gh.delete_branch.return_value = {}
    gh.get_mergeable.return_value = True
    gh.get_failed_checks.return_value = []
    return gh


def mock_plane_client() -> MagicMock:
    """Create a mocked Plane task client."""
    plane = MagicMock()
    plane.transition_issue.return_value = {}
    plane.comment_issue.return_value = {}
    plane.get_issue.return_value = {"id": "task-123"}
    plane.close.return_value = None
    return plane


def mock_settings(
    repo_key: str = "TestRepo",
    max_self_review_loops: int = 2,
    max_fix_attempts: int = 2,
) -> MagicMock:
    """Create a mocked settings configuration."""
    repo_cfg = MagicMock(
        auto_merge_on_ci_green=True,
        ci_ignored_checks=[],
        clone_url=f"git@github.com:owner/{repo_key}.git",
        default_branch="main",
        await_review=True,
    )
    settings = MagicMock(
        reviewer=MagicMock(
            bot_logins=[],
            allowed_reviewer_logins=[],
            max_self_review_loops=max_self_review_loops,
            max_fix_attempts=max_fix_attempts,
            bot_comment_marker="<!-- operations-center:bot -->",
        ),
        repos={repo_key: repo_cfg},
        plane=MagicMock(
            base_url="http://plane.local",
            project_id="proj",
            workspace_slug="ws",
        ),
    )
    return settings


# ── Assertion Helpers ─────────────────────────────────────────────────────────


def assert_state_transitioned(
    state_before: dict, state_after: dict, expected_phase: str, field_changes: dict | None = None
):
    """Assert that state transitioned as expected."""
    assert state_after["phase"] == expected_phase, (
        f"Phase transition failed: expected {expected_phase}, got {state_after['phase']}"
    )
    if field_changes:
        for field, expected_value in field_changes.items():
            assert state_after[field] == expected_value, (
                f"Field '{field}' transition failed: expected {expected_value}, "
                f"got {state_after[field]}"
            )


def assert_verdict_consolidated(
    decision: MergeDecision,
    expected_decision: str,
    expected_reason: str | None = None,
    expected_lane_count: int | None = None,
):
    """Assert that verdict was consolidated correctly."""
    assert decision.decision == expected_decision, (
        f"Decision mismatch: expected {expected_decision}, got {decision.decision}"
    )
    if expected_reason:
        assert decision.reason == expected_reason, (
            f"Reason mismatch: expected {expected_reason}, got {decision.reason}"
        )
    if expected_lane_count is not None:
        assert len(decision.lane_verdicts) == expected_lane_count, (
            f"Lane count mismatch: expected {expected_lane_count}, "
            f"got {len(decision.lane_verdicts)}"
        )


# ── Pytest Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def audit_verdict_builder() -> AuditVerdictBuilder:
    """Fixture providing AuditVerdictBuilder."""
    return AuditVerdictBuilder()


@pytest.fixture
def lane_verdict_builder() -> LaneVerdictBuilder:
    """Fixture providing LaneVerdictBuilder."""
    return LaneVerdictBuilder()


@pytest.fixture
def merge_decision_builder() -> MergeDecisionBuilder:
    """Fixture providing MergeDecisionBuilder."""
    return MergeDecisionBuilder()
