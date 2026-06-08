# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for boundary conditions on loop counters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher
from tests.verdicts.conftest import (
    AuditVerdictBuilder,
    create_pr_state,
    load_pr_state,
    mock_github_client,
    mock_settings,
    save_pr_state,
)


@pytest.mark.integration
class TestBoundaryConditions:
    """Test exact boundaries on retry caps and loop counters."""

    def test_fix_attempts_cap_exactly_at_max(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: fix_attempts reaches exactly max_fix_attempts."""
        settings = mock_settings(max_fix_attempts=2)
        gh = mock_github_client()

        # Loop at exactly max_fix_attempts with CONCERNS
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=1,
            fix_attempts=2,  # Exactly at max
        )
        state_path = save_pr_state(tmp_path, state)

        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "CONCERNS", "summary": "Still has issues"},
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

        # At max with no Plane task, escalates instead of closing
        # Verify not merged
        gh.merge_pr.assert_not_called()
        # State persists (for escalation)

    def test_fix_attempts_below_max_still_retries(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: fix_attempts < max still allows retry dispatch."""
        settings = mock_settings(max_fix_attempts=2)
        gh = mock_github_client()

        # Loop at fix_attempts = 1 (below max 2)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=1,  # Below max
        )
        state_path = save_pr_state(tmp_path, state)

        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "Issue found"},
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

        # Verify fix pass was dispatched (not closed)
        gh.close_pr.assert_not_called()
        # Verify state persisted (not yet at cap)
        state_after = load_pr_state(state_path)
        assert state_after["fix_attempts"] == 2

    def test_no_verdict_passes_exactly_at_max(
        self,
        tmp_path: Path,
    ):
        """Test: no_verdict_passes reaches exactly max_self_review_loops."""
        settings = mock_settings(max_fix_attempts=2)
        gh = mock_github_client()

        # State at exactly max_self_review_loops with no verdict
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=settings.reviewer.max_self_review_loops,
            no_verdict_passes=settings.reviewer.max_self_review_loops,
        )
        state_path = save_pr_state(tmp_path, state)

        with patch.object(watcher, "_run_pipeline", return_value=None):
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

        # Verify escalation occurred
        gh.post_comment.assert_called()
        state_after = load_pr_state(state_path)
        assert state_after["escalated_needs_human"] is True

    def test_no_verdict_passes_below_max_continues(
        self,
        tmp_path: Path,
    ):
        """Test: no_verdict_passes < max continues retrying."""
        settings = mock_settings(max_fix_attempts=2)
        gh = mock_github_client()

        # State below max with no verdict
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            no_verdict_passes=0,
        )
        state_path = save_pr_state(tmp_path, state)

        with patch.object(watcher, "_run_pipeline", return_value=None):
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

        # No escalation (below max)
        gh.post_comment.assert_not_called()
        # State persisted for retry
        state_after = load_pr_state(state_path)
        assert state_after["no_verdict_passes"] == 1
        assert state_after["escalated_needs_human"] is False

    def test_ci_wait_cycles_exactly_at_max(
        self,
        tmp_path: Path,
    ):
        """Test: ci_wait_cycles reaches exactly _MAX_CI_WAIT_CYCLES."""
        settings = mock_settings()
        gh = mock_github_client()

        # Assume _MAX_CI_WAIT_CYCLES = 20
        max_ci_wait = 20

        # State at exactly max CI wait cycles with red CI
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=max_ci_wait,
        )
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = ["Test: FAILED"]  # CI red

        with patch.object(watcher, "_run_pipeline") as mock_pipeline:
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

        # At max CI wait cycles, should escalate
        # Pipeline should not be called (deferred)
        mock_pipeline.assert_not_called()

    def test_self_review_loops_increments_correctly(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: self_review_loops increments on each cycle."""
        settings = mock_settings()
        gh = mock_github_client()

        # Multiple cycles with verdicts
        for i in range(3):
            state = create_pr_state(
                repo_key="TestRepo",
                pr_number=42,
                phase="self_review",
                self_review_loops=i,
                fix_attempts=0,
            )
            state_path = save_pr_state(tmp_path, state)

            with (
                patch.object(
                    watcher,
                    "_run_pipeline",
                    return_value={"result": "CONCERNS", "summary": f"Issue {i}"},
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

            state_after = load_pr_state(state_path)
            assert state_after["self_review_loops"] == i + 1

    def test_state_counters_independent(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Different counters track independently."""
        settings = mock_settings()
        gh = mock_github_client()

        # State with mixed counters
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=2,
            fix_attempts=1,
            no_verdict_passes=0,
            ci_wait_cycles=3,
        )
        state_path = save_pr_state(tmp_path, state)

        with (
            patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "CONCERNS", "summary": "Test"},
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

        state_after = load_pr_state(state_path)
        # Verify each counter updated independently
        assert state_after["self_review_loops"] == 3
        assert state_after["fix_attempts"] == 2
        assert state_after["no_verdict_passes"] == 0  # Unchanged
        # ci_wait_cycles resets when a verdict is received
        assert state_after["ci_wait_cycles"] == 0  # Reset on verdict
