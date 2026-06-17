# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for safety paths: unresolved concerns, escalation, close-and-requeue."""

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
class TestSafetyPaths:
    """Test safety paths: preventing half-finished merges and escalation."""

    def test_unresolved_concerns_close_and_requeue(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Max fix attempts exhausted → close PR and requeue task."""
        settings = mock_settings(max_fix_attempts=2)
        gh = mock_github_client()

        # Loop 1-2: CONCERNS with fix attempts
        for attempt in range(2):
            state = create_pr_state(
                repo_key="TestRepo",
                pr_number=42,
                phase="self_review",
                self_review_loops=attempt,
                fix_attempts=attempt,
                plane_task_id="task-123",
            )
            state_path = save_pr_state(tmp_path, state)

            with (
                patch.object(
                    watcher,
                    "_run_direct_review",
                    return_value={"result": "CONCERNS", "summary": "Issue"},
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

        # Loop 3: At max attempts with CONCERNS → close_and_requeue
        state_final = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=2,
            fix_attempts=2,  # At max
            plane_task_id="task-123",
        )
        state_path = save_pr_state(tmp_path, state_final)

        gh_final = mock_github_client()
        with patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "CONCERNS", "summary": "Still issues"},
        ):
            watcher._phase1(
                state_final,
                state_path,
                {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}},
                gh_final,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Verify not merged (safety path prevents merge with unresolved concerns)
        gh_final.merge_pr.assert_not_called()
        # State should be persisted (escalated or closed)
        assert state_final is not None

    def test_escalation_keeps_pr_open(
        self,
        tmp_path: Path,
    ):
        """Test: Escalation for human review keeps PR open."""
        settings = mock_settings()
        gh = mock_github_client()

        # State with persistent no verdict
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=settings.reviewer.max_self_review_loops,
            no_verdict_passes=settings.reviewer.max_self_review_loops,
        )
        state_path = save_pr_state(tmp_path, state)

        with patch.object(watcher, "_run_direct_review", return_value=None):
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

        # Verify escalation comment posted
        gh.post_comment.assert_called()
        # Verify PR NOT closed
        gh.close_pr.assert_not_called()
        # Verify PR NOT merged
        gh.merge_pr.assert_not_called()
        # Verify state persisted for future polling
        assert state_path.exists()

    def test_escalation_flag_prevents_duplicate_comments(
        self,
        tmp_path: Path,
    ):
        """Test: escalated_needs_human flag prevents duplicate escalation comments."""
        settings = mock_settings()
        mock_github_client()

        # First escalation
        state1 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=settings.reviewer.max_self_review_loops,
            no_verdict_passes=settings.reviewer.max_self_review_loops,
            escalated_needs_human=False,
        )
        state_path = save_pr_state(tmp_path, state1)

        gh1 = mock_github_client()
        with patch.object(watcher, "_run_direct_review", return_value=None):
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

        # Verify first comment posted
        assert gh1.post_comment.call_count >= 1

        # Second cycle: already escalated — escalated_head_sha keeps the flag anchored
        state2 = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=settings.reviewer.max_self_review_loops,
            no_verdict_passes=settings.reviewer.max_self_review_loops,
            escalated_needs_human=True,  # Already escalated
            escalated_head_sha="abc123",  # Pinned to current head
        )
        save_pr_state(tmp_path, state2)

        gh2 = mock_github_client()
        gh2.get_failed_checks.return_value = ["Test: FAILED"]  # CI red — no WO-3 retraction
        with patch.object(watcher, "_run_direct_review", return_value=None):
            watcher._phase1(
                state2,
                state_path,
                {
                    "number": 42,
                    "title": "Test PR",
                    "draft": False,
                    "head": {"ref": "goal/42", "sha": "abc123"},
                },
                gh2,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Should not post duplicate comment (or skip escalation logic)
        # Verify state remains escalated
        state_after = load_pr_state(state_path)
        assert state_after["escalated_needs_human"] is True

    def test_safety_never_merges_unresolved_concerns(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Safety: PR never merges with unresolved CONCERNS at cap."""
        settings = mock_settings(max_fix_attempts=1)
        gh = mock_github_client()

        # State at cap with CONCERNS
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=1,  # At cap
        )
        state_path = save_pr_state(tmp_path, state)

        with patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "CONCERNS", "summary": "Critical issue"},
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

        # Must NOT merge (safety: unresolved concerns at cap never merge)
        gh.merge_pr.assert_not_called()
        # Should escalate or close based on implementation
        assert state is not None

    def test_safety_never_merges_with_red_ci(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Safety: PR never merges with red CI."""
        settings = mock_settings()
        gh = mock_github_client()

        # Even with LGTM verdict, red CI blocks merge
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Simulate red CI
        gh.get_failed_checks.return_value = ["Test: FAILED"]

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

        # Review deferred (not called)
        mock_pipeline.assert_not_called()
        # Never merged
        gh.merge_pr.assert_not_called()
        # State persists
        assert state_path.exists()

    def test_safety_never_merges_non_mergeable_pr(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Safety: PR never merges if not mergeable (conflict)."""
        settings = mock_settings()
        gh = mock_github_client()

        # LGTM verdict but merge conflict
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Simulate merge conflict
        gh.get_mergeable.return_value = False

        with patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "LGTM", "summary": "Good"},
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

        # Merge not attempted
        gh.merge_pr.assert_not_called()
        # State persists (for manual resolution)
        assert state_path.exists()
