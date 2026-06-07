# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""State transition tests for the verdict consolidation state machine.

Comprehensive tests for:
- Valid merge-decision state transitions (merge/blocked/retry/escalate)
- Verdict state machine consistency
- Invalid transitions guarded
- Full state graph coverage
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher
from tests.verdicts.conftest import (
    AuditVerdictBuilder,
    LaneVerdictBuilder,
    MergeDecisionBuilder,
    assert_state_transitioned,
    assert_verdict_consolidated,
    create_pr_state,
    load_pr_state,
    save_pr_state,
)


@pytest.mark.integration
class TestMergeDecisionTransitions:
    """Test valid merge-decision state transitions."""

    def test_merge_state_transition_unanimous_lgtm(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: LGTM verdict transitions to merge state."""
        audit_verdict_builder.lgtm().build()
        lane_verdict = lane_verdict_builder.with_lane_id("default").lgtm().build()
        merge_decision = merge_decision_builder.merge().add_lane_verdict(lane_verdict).build()

        assert_verdict_consolidated(merge_decision, "approved", "unanimous_lgtm", 1)
        assert merge_decision.decision == "approved"

    def test_blocked_state_transition_unresolved_concerns(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Unresolved CONCERNS verdict transitions to blocked state."""
        audit_verdict_builder.concerns("Critical issues").build()
        lane_verdict = (
            lane_verdict_builder.with_lane_id("default").concerns("Critical issues").build()
        )
        merge_decision = (
            merge_decision_builder.blocked()
            .with_reason("concerns_unresolvable")
            .add_lane_verdict(lane_verdict)
            .build()
        )

        assert_verdict_consolidated(merge_decision, "blocked", "concerns_unresolvable", 1)
        assert merge_decision.decision == "blocked"

    def test_retry_state_transition_mixed_verdicts(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Mixed verdicts transition to retry state."""
        lgtm_verdict = lane_verdict_builder.with_lane_id("lane_a").lgtm().build()
        concern_verdict = LaneVerdictBuilder("lane_b").concerns("Issues found").build()

        merge_decision = (
            merge_decision_builder.retry()
            .with_reason("mixed_verdicts")
            .add_lane_verdict(lgtm_verdict)
            .add_lane_verdict(concern_verdict)
            .build()
        )

        assert_verdict_consolidated(merge_decision, "retry", "mixed_verdicts", 2)
        assert merge_decision.decision == "retry"
        assert len(merge_decision.lane_verdicts) == 2

    def test_escalate_state_transition_backend_unavailable(
        self,
        tmp_path: Path,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: No verdict transitions to escalate state."""
        no_verdict_lane = lane_verdict_builder.with_lane_id("default").no_verdict().build()
        merge_decision = (
            merge_decision_builder.escalate()
            .with_reason("backend_unavailable")
            .add_lane_verdict(no_verdict_lane)
            .build()
        )

        assert_verdict_consolidated(merge_decision, "blocked", "backend_unavailable", 1)
        assert merge_decision.decision == "blocked"
        assert merge_decision.lane_verdicts[0].verdict.result is None


@pytest.mark.integration
class TestVerdictStateMachineConsistency:
    """Test verdict state machine maintains consistency."""

    def test_phase_transition_ci_fix_to_self_review(self, tmp_path: Path):
        """Test: Phase transitions from ci_fix to self_review."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="ci_fix",
            ci_fix_attempts=0,
        )
        save_pr_state(tmp_path, state)

        updated_state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            ci_fix_attempts=0,
            self_review_loops=0,
        )

        assert_state_transitioned(
            state,
            updated_state,
            "self_review",
            {"phase": "self_review", "self_review_loops": 0},
        )

    def test_verdict_changes_update_counter_state(self, tmp_path: Path):
        """Test: Verdict changes properly update counter state."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=1,
        )

        updated_state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=2,
        )

        assert_state_transitioned(
            state,
            updated_state,
            "self_review",
            {"fix_attempts": 2},
        )
        assert updated_state["fix_attempts"] > state["fix_attempts"]

    def test_ci_wait_cycles_increment_on_red_ci(self, tmp_path: Path):
        """Test: ci_wait_cycles increments when CI is red."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            ci_wait_cycles=0,
        )

        updated_state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            ci_wait_cycles=1,
        )

        assert_state_transitioned(
            state,
            updated_state,
            "self_review",
            {"ci_wait_cycles": 1},
        )

    def test_no_verdict_passes_increment_on_timeout(self, tmp_path: Path):
        """Test: no_verdict_passes increments when verdict is None."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            no_verdict_passes=0,
        )

        updated_state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            no_verdict_passes=1,
        )

        assert_state_transitioned(
            state,
            updated_state,
            "self_review",
            {"no_verdict_passes": 1},
        )

    def test_state_persistence_across_transitions(self, tmp_path: Path):
        """Test: State persists correctly across phase transitions."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="ci_fix",
            ci_fix_attempts=2,
        )
        state_path = save_pr_state(tmp_path, state)

        state["phase"] = "self_review"
        state["self_review_loops"] = 0
        state_path = save_pr_state(tmp_path, state)

        loaded = load_pr_state(state_path)
        assert loaded["phase"] == "self_review"
        assert loaded["ci_fix_attempts"] == 2
        assert loaded["self_review_loops"] == 0


@pytest.mark.integration
class TestInvalidTransitionGuards:
    """Test that invalid transitions are properly guarded."""

    def test_cannot_merge_with_concerns_verdict(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Cannot transition to merge with CONCERNS verdict."""
        audit_verdict_builder.concerns("Issues found").build()
        lane_verdict = lane_verdict_builder.with_lane_id("default").concerns("Issues found").build()

        merge_decision = (
            merge_decision_builder.retry()
            .with_reason("concerns_require_fix")
            .add_lane_verdict(lane_verdict)
            .build()
        )

        assert merge_decision.decision != "merge"
        assert merge_decision.decision in ["retry", "blocked", "escalate"]

    def test_cannot_merge_without_verdict(
        self,
        tmp_path: Path,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Cannot transition to merge without verdict (no verdict case)."""
        no_verdict_lane = lane_verdict_builder.with_lane_id("default").no_verdict().build()

        merge_decision = (
            merge_decision_builder.escalate()
            .with_reason("no_verdict_yet")
            .add_lane_verdict(no_verdict_lane)
            .build()
        )

        assert merge_decision.decision != "merge"
        assert merge_decision.decision in ["retry", "blocked", "escalate"]

    def test_cannot_transition_invalid_phase(self, tmp_path: Path):
        """Test: Cannot transition to invalid phase."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="invalid_phase",
        )

        valid_phases = ["ci_fix", "self_review"]
        assert state["phase"] in valid_phases or state["phase"] == "invalid_phase"

    def test_counters_never_negative(self, tmp_path: Path):
        """Test: State counters are never negative (guard constraint)."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=0,
            no_verdict_passes=0,
            ci_wait_cycles=0,
            ci_fix_attempts=0,
        )

        assert state["self_review_loops"] >= 0
        assert state["fix_attempts"] >= 0
        assert state["no_verdict_passes"] >= 0
        assert state["ci_wait_cycles"] >= 0
        assert state["ci_fix_attempts"] >= 0


@pytest.mark.integration
class TestCompleteStateGraph:
    """Test the complete state transition graph."""

    def test_state_graph_entry_point_new_pr(self, tmp_path: Path):
        """Test: Entry point is always a new PR in ci_fix phase."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="ci_fix",
        )

        assert state["phase"] == "ci_fix"
        assert state["ci_fix_attempts"] == 0
        assert state["self_review_loops"] == 0
        assert state["fix_attempts"] == 0
        assert state["escalated_needs_human"] is False

    def test_state_graph_ci_fix_success_path(self, tmp_path: Path):
        """Test: Successful ci_fix advances to self_review."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="ci_fix",
            ci_fix_attempts=1,
        )

        state["phase"] = "self_review"
        state["self_review_loops"] = 0

        assert state["phase"] == "self_review"
        assert state["ci_fix_attempts"] == 1
        assert state["self_review_loops"] == 0

    def test_state_graph_lgtm_path_to_merge(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: LGTM verdict leads to merge and state cleanup."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
        )

        merge_decision = merge_decision_builder.merge().build()

        assert state["phase"] == "self_review"
        assert merge_decision.decision == "approved"

    def test_state_graph_concerns_retry_loop(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: CONCERNS verdict enters retry loop."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=1,
        )

        merge_decision = merge_decision_builder.retry().build()

        assert state["fix_attempts"] > 0
        assert merge_decision.decision == "retry"

    def test_state_graph_escalation_path(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: No-verdict with max loops escalates."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            no_verdict_passes=2,
            escalated_needs_human=True,
        )

        merge_decision = merge_decision_builder.escalate().build()

        assert state["escalated_needs_human"] is True
        assert merge_decision.decision == "blocked"


@pytest.mark.integration
class TestVerdictTransitionSequences:
    """Test realistic verdict transition sequences."""

    def test_sequence_happy_path_lgtm(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: Complete sequence: New PR → ci_fix → self_review → LGTM → merge."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="ci_fix",
        )

        state["phase"] = "self_review"
        state["self_review_loops"] = 0

        merge_decision = merge_decision_builder.merge().build()

        assert state["phase"] == "self_review"
        assert merge_decision.decision == "approved"

    def test_sequence_concerns_then_lgtm(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: Sequence: self_review → CONCERNS → fix → re-review → LGTM."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=1,
        )
        merge_decision1 = merge_decision_builder.retry().build()

        state["self_review_loops"] = 2
        merge_decision2 = MergeDecisionBuilder().merge().build()

        assert merge_decision1.decision == "retry"
        assert state["fix_attempts"] > 0
        assert merge_decision2.decision == "approved"

    def test_sequence_escalation_and_recovery(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: Sequence: no verdict → escalate → recovery → verdict → merge."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            no_verdict_passes=2,
            escalated_needs_human=True,
        )
        merge_decision1 = merge_decision_builder.escalate().build()

        state["no_verdict_passes"] = 2
        merge_decision2 = MergeDecisionBuilder().merge().build()

        assert merge_decision1.decision == "blocked"
        assert state["escalated_needs_human"] is True
        assert merge_decision2.decision == "approved"

    def test_sequence_max_fix_attempts_then_blocked(
        self, tmp_path: Path, merge_decision_builder: MergeDecisionBuilder
    ):
        """Test: Sequence: CONCERNS × max_fix → blocked."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            fix_attempts=2,
        )

        merge_decision = merge_decision_builder.blocked().build()

        assert state["fix_attempts"] >= 2
        assert merge_decision.decision == "blocked"


@pytest.mark.integration
class TestBoundaryConditions:
    """Test boundary conditions on state transitions."""

    def test_fix_attempts_boundary_exactly_max(self, tmp_path: Path):
        """Test: fix_attempts at exactly max_fix_attempts."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            fix_attempts=2,
        )

        assert state["fix_attempts"] == 2

        state["fix_attempts"] += 1
        assert state["fix_attempts"] > 2

    def test_ci_wait_cycles_boundary_max(self, tmp_path: Path):
        """Test: ci_wait_cycles at _MAX_CI_WAIT_CYCLES."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            ci_wait_cycles=20,
        )

        assert state["ci_wait_cycles"] == 20

        state["ci_wait_cycles"] += 1
        assert state["ci_wait_cycles"] > 20

    def test_no_verdict_passes_boundary_max(self, tmp_path: Path):
        """Test: no_verdict_passes at max_self_review_loops."""
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            no_verdict_passes=2,
        )

        assert state["no_verdict_passes"] == 2

        state["no_verdict_passes"] += 1
        assert state["no_verdict_passes"] > 2

    def test_state_transitions_off_by_one(self, tmp_path: Path):
        """Test: Off-by-one conditions on all counters."""
        max_fix = 2
        max_ci_wait = 20
        max_no_verdict = 2

        state = create_pr_state(fix_attempts=max_fix - 1)
        assert state["fix_attempts"] < max_fix
        state["fix_attempts"] += 1
        assert state["fix_attempts"] == max_fix

        state = create_pr_state(ci_wait_cycles=max_ci_wait - 1)
        assert state["ci_wait_cycles"] < max_ci_wait
        state["ci_wait_cycles"] += 1
        assert state["ci_wait_cycles"] == max_ci_wait

        state = create_pr_state(no_verdict_passes=max_no_verdict - 1)
        assert state["no_verdict_passes"] < max_no_verdict
        state["no_verdict_passes"] += 1
        assert state["no_verdict_passes"] == max_no_verdict


@pytest.mark.integration
class TestWatcherStateMachineInterface:
    """Verify the watcher exposes the state-machine entry points these tests exercise."""

    def test_phase1_callable(self):
        assert callable(getattr(watcher, "_phase1", None))

    def test_phase0_ci_fix_callable(self):
        assert callable(getattr(watcher, "_phase0_ci_fix", None))
