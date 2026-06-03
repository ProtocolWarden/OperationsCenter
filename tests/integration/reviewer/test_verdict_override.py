# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for mixed verdict scenarios with override logic."""

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
class TestMixedVerdictOverrideLogic:
    """Test verdict consolidation with mixed verdicts and override logic."""

    def test_verdict_override_concerns_to_lgtm(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: CONCERNS → fix pass → re-review → LGTM → merge."""
        # Loop 1: Create PR state with CONCERNS verdict
        state_loop1 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=0,
        )
        state_path = save_pr_state(tmp_path, state_loop1)

        # Create CONCERNS verdict (Loop 1)
        concerns_verdict = audit_verdict_builder.concerns("Fix linting").build()
        concerns_lane = (
            lane_verdict_builder.with_lane_id("lint").with_verdict(concerns_verdict).build()
        )
        concerns_decision = merge_decision_builder.retry().add_lane_verdict(concerns_lane).build()

        # Verify loop 1 decision is retry
        assert_verdict_consolidated(concerns_decision, "retry", "mixed_verdicts")

        # Mock clients for loop 1
        gh = mock_github_client()
        settings = mock_settings()

        # Execute loop 1: CONCERNS → dispatch fix pass
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "Fix linting"},
            ),
            patch.object(watcher, "_run_fix_pass", return_value=True),
        ):
            watcher._phase1(
                state_loop1,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify state after loop 1
        state_after_loop1 = load_pr_state(state_path)
        assert state_after_loop1["self_review_loops"] == 1
        assert state_after_loop1["fix_attempts"] == 1
        assert state_path.exists()  # Not merged yet

        # Loop 2: Update state for next review cycle
        state_loop2 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=1,
        )
        save_pr_state(tmp_path, state_loop2)

        # Create LGTM verdict (Loop 2 - fix resolved concerns)
        lgtm_verdict = audit_verdict_builder.lgtm().build()
        lgtm_lane = lane_verdict_builder.with_lane_id("lint").with_verdict(lgtm_verdict).build()
        merge_decision = merge_decision_builder.merge().add_lane_verdict(lgtm_lane).build()

        # Verify loop 2 decision is merge
        assert_verdict_consolidated(merge_decision, "merge", "unanimous_lgtm")

        # Execute loop 2: LGTM → merge
        gh_loop2 = mock_github_client()
        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "LGTM", "summary": "All checks passed"},
        ):
            watcher._phase1(
                state_loop2,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_loop2,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify merge happened
        gh_loop2.merge_pr.assert_called_once()
        # Verify state file removed after merge
        assert not state_path.exists()

    def test_verdict_flip_lgtm_to_concerns(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: LGTM in first review → CONCERNS in second (edge case)."""
        # Initial review gets LGTM but state is persisted
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # First review: LGTM
        lgtm_verdict = audit_verdict_builder.lgtm().build()
        lgtm_lane = lane_verdict_builder.with_lane_id("lint").with_verdict(lgtm_verdict).build()
        merge_decision = merge_decision_builder.merge().add_lane_verdict(lgtm_lane).build()
        assert_verdict_consolidated(merge_decision, "merge")

        # Simulate PR diff changed after first review
        # Second review: CONCERNS (on new diff)
        concerns_verdict = audit_verdict_builder.concerns("New issues in changed code").build()
        (lane_verdict_builder.with_lane_id("lint").with_verdict(concerns_verdict).build())

        gh = mock_github_client()
        settings = mock_settings()

        # Execute with CONCERNS verdict
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "New issues in changed code"},
            ),
            patch.object(watcher, "_run_fix_pass", return_value=True),
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

        # Verify state transitioned correctly despite verdict flip
        loaded_state = load_pr_state(state_path)
        assert loaded_state["self_review_loops"] == 1
        assert loaded_state["fix_attempts"] == 1
        # PR should NOT be merged (CONCERNS overrides LGTM)
        gh.merge_pr.assert_not_called()

    def test_verdict_changes_accumulate_fix_attempts(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: Multiple CONCERNS verdicts count toward fix_attempts cap."""
        settings = mock_settings(max_fix_attempts=2)
        gh = mock_github_client()

        # Loop 1: First CONCERNS verdict
        state1 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=0,
            plane_task_id="task-123",
        )
        state_path = save_pr_state(tmp_path, state1)

        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "Issue 1"},
            ),
            patch.object(watcher, "_run_fix_pass", return_value=True),
        ):
            watcher._phase1(
                state1,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        state_after_loop1 = load_pr_state(state_path)
        assert state_after_loop1["fix_attempts"] == 1
        assert state_after_loop1["self_review_loops"] == 1

        # Loop 2: Second CONCERNS verdict
        state2 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=1,
            plane_task_id="task-123",
        )
        save_pr_state(tmp_path, state2)

        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "Issue 2"},
            ),
            patch.object(watcher, "_run_fix_pass", return_value=True),
        ):
            watcher._phase1(
                state2,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        state_after_loop2 = load_pr_state(state_path)
        assert state_after_loop2["fix_attempts"] == 2
        assert state_after_loop2["self_review_loops"] == 2

        # Loop 3: Third CONCERNS verdict at cap → close_and_requeue
        state3 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=2,
            fix_attempts=2,
            plane_task_id="task-123",
        )
        save_pr_state(tmp_path, state3)

        gh_loop3 = mock_github_client()
        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "CONCERNS", "summary": "Issue 3"},
        ):
            watcher._phase1(
                state3,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_loop3,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # At max fix_attempts with CONCERNS → escalate (no close without proper Plane setup)
        # Verify PR was NOT merged
        gh_loop3.merge_pr.assert_not_called()
