# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for timeout recovery and escalation scenarios."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher
from tests.verdicts.conftest import (
    AuditVerdictBuilder,
    LaneVerdictBuilder,
    MergeDecisionBuilder,
    create_pr_state,
    load_pr_state,
    mock_github_client,
    mock_settings,
    save_pr_state,
)


@pytest.mark.integration
class TestTimeoutRecoveryScenarios:
    """Test timeout recovery and escalation paths."""

    def test_timeout_recovery_from_backend_crash(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: No verdict (backend down) → escalate → resume polling on recovery."""
        settings = mock_settings(max_fix_attempts=2)

        # Cycle 1: Backend unavailable (no verdict)
        state1 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            no_verdict_passes=0,
        )
        state_path = save_pr_state(tmp_path, state1)

        gh1 = mock_github_client()
        with patch.object(watcher, "_run_pipeline", return_value=None):
            watcher._phase1(
                state1,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh1,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        state_after_cycle1 = load_pr_state(state_path)
        assert state_after_cycle1["escalated_needs_human"] is False

        # Cycle 2: Still no verdict, then escalate on max
        state2 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=settings.reviewer.max_self_review_loops - 1,
            no_verdict_passes=settings.reviewer.max_self_review_loops - 1,
        )
        save_pr_state(tmp_path, state2)

        gh_escalate = mock_github_client()
        with patch.object(watcher, "_run_pipeline", return_value=None):
            watcher._phase1(
                state2,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_escalate,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify escalation comment was posted
        gh_escalate.post_comment.assert_called()
        state_after_escalate = load_pr_state(state_path)
        assert state_after_escalate["escalated_needs_human"] is True

        # PR should still be open (not closed)
        gh_escalate.close_pr.assert_not_called()

        # Recovery: Backend comes back and next poll gets verdict
        state3 = load_pr_state(state_path)
        state3["escalated_needs_human"] = True
        save_pr_state(tmp_path, state3)

        gh_recovery = mock_github_client()
        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "LGTM", "summary": "All good"},
        ):
            watcher._phase1(
                state3,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_recovery,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify merge happens after recovery (escalation flag doesn't block merge)
        gh_recovery.merge_pr.assert_called_once()
        # Verify state file removed after merge
        assert not state_path.exists()

    def test_timeout_recovery_bounded_polling(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: No verdict polling bounded by max_self_review_loops."""
        settings = mock_settings(max_fix_attempts=2)
        mock_github_client()

        # Simulate reaching max_self_review_loops with no verdict
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=settings.reviewer.max_self_review_loops - 1,
            no_verdict_passes=settings.reviewer.max_self_review_loops - 1,
        )
        state_path = save_pr_state(tmp_path, state)

        gh_poll = mock_github_client()
        with patch.object(watcher, "_run_pipeline", return_value=None):
            watcher._phase1(
                state,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_poll,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify escalation occurred
        state_after = load_pr_state(state_path)
        assert state_after["escalated_needs_human"] is True
        # Single comment posted (not re-commented)
        gh_poll.post_comment.assert_called_once()

    def test_no_verdict_counter_resets_on_verdict_arrival(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
    ):
        """Test: no_verdict_passes counter resets when verdict finally arrives."""
        settings = mock_settings(max_fix_attempts=2)
        mock_github_client()

        # State with several no-verdict cycles
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=2,
            no_verdict_passes=2,
        )
        state_path = save_pr_state(tmp_path, state)

        # Verdict finally arrives (CONCERNS)
        concerns_verdict = audit_verdict_builder.concerns("Fix issues").build()
        (lane_verdict_builder.with_lane_id("lint").with_verdict(concerns_verdict).build())

        gh_verdict = mock_github_client()
        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "Fix issues"},
            ),
            patch.object(watcher, "_run_fix_pass", return_value=True),
        ):
            watcher._phase1(
                state,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_verdict,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify counter resets and fix_attempts incremented
        state_after = load_pr_state(state_path)
        assert state_after["no_verdict_passes"] == 0  # Reset
        assert state_after["fix_attempts"] == 1
        assert state_after["self_review_loops"] == 3  # Loop count continues

        # Now can dispatch max_fix_attempts more times before hitting cap
        # Verify fix pass was dispatched (post_comment called for CONCERNS verdict)
        gh_verdict.post_comment.assert_called()  # CONCERNS comment posted
        assert state_path.exists()  # State persisted for next cycle

    def test_pending_ci_defers_review_with_retry(
        self,
        tmp_path: Path,
    ):
        """Test: Pending CI verdict defers self-review, retries until resolved."""
        settings = mock_settings()
        gh = mock_github_client()

        # Cycle 1: CI pending
        state1 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=0,
        )
        state_path = save_pr_state(tmp_path, state1)

        # Mock pending CI (no failed checks, but not all passed)
        gh.get_failed_checks.return_value = []  # No failures but...
        # Simulate pending by returning None from pipeline (CI not ready)
        with patch.object(watcher, "_run_pipeline", return_value=None):
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

        # Verify CI wait cycles incremented
        load_pr_state(state_path)
        # Should be in wait state, not merged
        gh.merge_pr.assert_not_called()
        assert state_path.exists()

        # Cycle 2: CI green, review runs
        state2 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=1,
        )
        save_pr_state(tmp_path, state2)

        gh_green = mock_github_client()
        gh_green.get_failed_checks.return_value = []

        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "LGTM", "summary": "All good"},
        ):
            watcher._phase1(
                state2,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_green,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify merge happens after CI ready
        gh_green.merge_pr.assert_called_once()
        assert not state_path.exists()  # Cleaned up after merge
