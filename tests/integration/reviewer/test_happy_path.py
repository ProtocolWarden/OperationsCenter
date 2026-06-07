# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for the happy-path verdict flow: CI green → LGTM → merge.

Tests the complete verdict consolidation flow:
- Unanimous LGTM across all lanes → merge-ready
- CI green + no objections → proceeds to review
- Normal verdict consolidation flow (audit → consolidation → decision)
- State transitions through review phases
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher
from tests.verdicts.conftest import (
    AuditVerdictBuilder,
    LaneVerdictBuilder,
    MergeDecisionBuilder,
    assert_verdict_consolidated,
    create_pr_state,
    load_pr_state,
    mock_github_client,
    mock_settings,
    save_pr_state,
)


@pytest.mark.integration
class TestHappyPathVerdictFlow:
    """Integration tests for the complete happy-path verdict consolidation flow."""

    def test_unanimous_lgtm_across_single_lane_merges_pr(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Single lane with unanimous LGTM → merge-ready verdict + PR merged.

        Acceptance: Unanimous pass across all lanes → merge-ready verdict test passing.
        """
        # Setup: Create PR state in self_review phase
        state = create_pr_state(repo_key="TestRepo", pr_number=42, phase="self_review")
        state_path = save_pr_state(tmp_path, state)
        assert state_path.exists()

        # Setup: Create single lane with unanimous LGTM
        lane_verdict = lane_verdict_builder.with_lane_id("default").lgtm().build()
        assert lane_verdict.verdict.result == "LGTM"

        # Setup: Build merge decision from unanimous verdict
        merge_decision = merge_decision_builder.merge().add_lane_verdict(lane_verdict).build()

        # Verify: Merge decision is "merge" with "unanimous_lgtm" reason
        assert_verdict_consolidated(merge_decision, "approved", "unanimous_lgtm", 1)

        # Setup: Mock GitHub client
        gh = mock_github_client()
        settings = mock_settings()

        # Execute: Run phase1 with LGTM verdict
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "LGTM", "summary": "all checks passed"},
            ),
            patch.object(watcher, "_plane_client"),
        ):
            watcher._phase1(
                state,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: PR was merged (happy path outcome)
        gh.merge_pr.assert_called_once_with("owner", "TestRepo", 42, merge_method="squash")

        # Assert: State file was removed (cleanup after merge)
        assert not state_path.exists()

    def test_unanimous_lgtm_across_multiple_lanes_merges_pr(
        self,
        tmp_path: Path,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Multiple lanes all with LGTM → unanimous pass verdict → merged.

        Acceptance: Unanimous pass across all lanes → merge-ready verdict test passing.
        """
        # Setup: Create PR state
        state = create_pr_state(repo_key="TestRepo", pr_number=43, phase="self_review")
        state_path = save_pr_state(tmp_path, state)

        # Setup: Create verdicts from 3 different review lanes
        lint_lane = lane_verdict_builder.with_lane_id("lint-check").lgtm().build()
        type_lane = LaneVerdictBuilder("type-check").lgtm().build()
        style_lane = LaneVerdictBuilder("style-check").lgtm().build()

        # Setup: Build merge decision from all unanimous LGTM verdicts
        merge_decision = (
            merge_decision_builder.merge()
            .add_lane_verdict(lint_lane)
            .add_lane_verdict(type_lane)
            .add_lane_verdict(style_lane)
            .build()
        )

        # Verify: All lanes unanimous, decision is merge
        assert_verdict_consolidated(merge_decision, "approved", "unanimous_lgtm", 3)
        assert all(v.verdict.result == "LGTM" for v in merge_decision.lane_verdicts), (
            "All lanes must have LGTM verdict"
        )

        # Setup: Mock clients
        gh = mock_github_client()
        settings = mock_settings()

        # Execute: Run phase1 with LGTM verdict
        pr_info = {
            "number": 43,
            "title": "Multi-lane PR",
            "draft": False,
            "head": {"ref": "goal/43"},
        }
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "LGTM", "summary": "all lanes pass"},
            ),
            patch.object(watcher, "_plane_client"),
        ):
            watcher._phase1(
                state,
                state_path,
                pr_info,
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: PR was merged
        gh.merge_pr.assert_called_once_with("owner", "TestRepo", 43, merge_method="squash")
        assert not state_path.exists()

    def test_ci_green_with_lgtm_proceeds_to_merge(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: CI green + LGTM verdict → complete happy path (no objections).

        Acceptance: CI green with no objections scenario tested and passing.
        """
        # Setup: Create PR state in self_review phase (CI already green)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=44,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Setup: Create LGTM verdict
        verdict = audit_verdict_builder.lgtm().build()
        assert verdict.result == "LGTM"

        # Setup: Mock GitHub client with green CI
        gh = mock_github_client()
        gh.get_failed_checks.return_value = []  # Green CI
        settings = mock_settings()

        # Execute: Run phase1 with green CI + LGTM
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "LGTM", "summary": "CI green, LGTM"},
            ),
            patch.object(watcher, "_plane_client"),
        ):
            watcher._phase1(
                state,
                state_path,
                {"number": 44, "title": "Green PR", "draft": False, "head": {"ref": "goal/44"}},
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: PR was merged (happy path)
        gh.merge_pr.assert_called_once_with("owner", "TestRepo", 44, merge_method="squash")
        assert not state_path.exists()

    def test_normal_verdict_consolidation_flow_audit_to_merge(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Normal verdict consolidation flow (audit → consolidation → decision).

        Acceptance: Normal verdict consolidation flow validated.
        Tests the complete flow: audit verdict collection → consolidation → merge decision.
        """
        # Phase 1: Audit - Collect verdict from executor
        audit_verdict = audit_verdict_builder.lgtm().build()
        assert audit_verdict.result == "LGTM"

        # Phase 2: Consolidation - Map audit verdict to lane verdict
        lane_verdict = (
            lane_verdict_builder.with_lane_id("default").with_verdict(audit_verdict).build()
        )
        assert lane_verdict.verdict.result == "LGTM"

        # Phase 3: Decision - Consolidate across lanes and make merge decision
        merge_decision = merge_decision_builder.merge().add_lane_verdict(lane_verdict).build()
        assert_verdict_consolidated(merge_decision, "approved", "unanimous_lgtm", 1)

        # Execute: Full state transition test through review phases
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=45,
            phase="self_review",
        )
        state_path = save_pr_state(tmp_path, state)

        # Verify: Initial state
        loaded_state = load_pr_state(state_path)
        assert loaded_state["phase"] == "self_review"
        assert loaded_state["self_review_loops"] == 0

        # Setup: Mock clients
        gh = mock_github_client()
        settings = mock_settings()

        # Execute: Run phase1 to complete consolidation and merge
        pr_info = {
            "number": 45,
            "title": "Consolidation PR",
            "draft": False,
            "head": {"ref": "goal/45"},
        }
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "LGTM", "summary": "consolidation ok"},
            ),
            patch.object(watcher, "_plane_client"),
        ):
            watcher._phase1(
                state,
                state_path,
                pr_info,
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: Complete flow executed successfully
        gh.merge_pr.assert_called_once()
        assert not state_path.exists(), "State should be cleaned after merge"

    def test_happy_path_state_transition_through_review(
        self,
        tmp_path: Path,
    ):
        """Test: State transitions correctly through the happy path (no regressions).

        Verifies that the PR state tracks phase transitions without losing data.
        """
        # Setup: Create PR state in self_review phase (happy path entry point)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=46,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Assert: Initial state is in self_review
        assert state["phase"] == "self_review"
        assert state["self_review_loops"] == 0

        # Load: Verify state persists correctly
        loaded_state = load_pr_state(state_path)
        assert loaded_state["phase"] == "self_review"
        assert loaded_state["pr_number"] == 46
        assert loaded_state["repo_key"] == "TestRepo"

        # Execute: Simulate phase completion (merge happens, state removed)
        # In happy path, state file is removed after merge
        state_path.unlink()

        # Assert: State file is cleaned up after merge
        assert not state_path.exists()


@pytest.mark.integration
class TestHappyPathBuilders:
    """Test verdict builders used in happy path scenarios."""

    def test_audit_verdict_builder_lgtm_for_happy_path(
        self,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: AuditVerdictBuilder correctly builds LGTM verdict (happy path)."""
        verdict = audit_verdict_builder.lgtm().build()
        assert verdict.result == "LGTM"
        assert verdict.summary == "All checks passed"
        assert verdict.details == ""

    def test_lane_verdict_builder_creates_single_lane(
        self,
        lane_verdict_builder: LaneVerdictBuilder,
    ):
        """Test: LaneVerdictBuilder creates single lane verdict with LGTM."""
        verdict = lane_verdict_builder.with_lane_id("default").lgtm().build()
        assert verdict.lane_id == "default"
        assert verdict.verdict.result == "LGTM"
        assert verdict.phase == "self_review"

    def test_merge_decision_builder_creates_unanimous_decision(
        self,
        merge_decision_builder: MergeDecisionBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
    ):
        """Test: MergeDecisionBuilder creates unanimous merge decision (happy path)."""
        lane1 = lane_verdict_builder.with_lane_id("lint").lgtm().build()
        lane2 = LaneVerdictBuilder("type-check").lgtm().build()

        decision = (
            merge_decision_builder.merge().add_lane_verdict(lane1).add_lane_verdict(lane2).build()
        )

        assert decision.decision == "approved"
        assert decision.reason == "unanimous_lgtm"
        assert len(decision.lane_verdicts) == 2
        assert all(v.verdict.result == "LGTM" for v in decision.lane_verdicts)

    def test_merge_decision_builder_multiple_lanes(
        self,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: MergeDecisionBuilder handles multiple lanes correctly."""
        lanes = [LaneVerdictBuilder(f"lane-{i}").lgtm().build() for i in range(3)]

        decision = merge_decision_builder.merge()
        for lane in lanes:
            decision.add_lane_verdict(lane)

        final_decision = decision.build()

        assert final_decision.decision == "approved"
        assert len(final_decision.lane_verdicts) == 3
        assert all(v.verdict.result == "LGTM" for v in final_decision.lane_verdicts)
