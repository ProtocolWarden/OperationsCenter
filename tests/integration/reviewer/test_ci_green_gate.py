# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for CI-green gate validation: merge precondition enforcement.

Tests that CI-green is a hard blocker for merge approval. PR cannot merge until:
- All CI checks pass (no failed checks)
- Green status is confirmed
- After developer fixes red CI, merge is allowed

Scenarios covered:
- CI red/failing checks (merge blocked, retry allowed after fix)
- CI green/no failing checks (merge allowed with LGTM verdict)
- CI wait cycles tracked and escalated at max
- Gate prevents premature merges even with LGTM if CI failing
"""

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
class TestCIGreenGateValidation:
    """Test CI-green gate as hard blocker for merge approval."""

    def test_ci_with_failing_checks_blocks_review(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: CI with failing checks → review deferred, merge blocked.

        Acceptance: PR cannot merge when CI checks are failing (red CI).
        """
        settings = mock_settings()
        gh = mock_github_client()

        # Setup: PR in self_review with some CI checks failing
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Setup: Some CI checks are failing (red CI)
        gh.get_failed_checks.return_value = ["Lint: FAILED", "Unit Tests: FAILED"]

        # Execute: Run phase1 with CI checks failing
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

        # Assert: Review pipeline NOT executed (CI gate blocks it when red)
        mock_pipeline.assert_not_called()

        # Assert: PR NOT merged (CI gate prevents merge with failing checks)
        gh.merge_pr.assert_not_called()

        # Assert: State persisted with ci_wait_cycles incremented
        assert state_path.exists()
        state_after = load_pr_state(state_path)
        assert state_after["ci_wait_cycles"] == 1

    def test_ci_still_running_defers_review_does_not_merge(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: no failed checks but some still in-progress → defer, do NOT merge.

        Regression for the premature-green bug (how #269 merged red): an empty
        failed-checks list only means "nothing has failed *yet*". If checks are
        still running, the gate must defer rather than declare green and let a
        self-review LGTM merge the PR before CI finishes.
        """
        settings = mock_settings()
        gh = mock_github_client()

        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # No check has FAILED, but the test job is still running.
        gh.get_failed_checks.return_value = []
        gh.get_incomplete_checks.return_value = ["Test (pytest)"]

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

        # Gate must defer: no self-review, no merge, wait counter advanced.
        mock_pipeline.assert_not_called()
        gh.merge_pr.assert_not_called()
        state_after = load_pr_state(state_path)
        assert state_after["ci_wait_cycles"] == 1

    def test_ci_with_no_checks_on_head_defers(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Guard C: no failed and no pending, but ZERO completed checks on the head
        → defer, do NOT declare green.

        This is the window right after a push or auto-rebase, before CI registers
        on the new head: get_failed_checks and get_incomplete_checks both return []
        for a head with no check runs, so a gate that only asks "nothing failed,
        nothing pending?" would merge on a head with no CI at all (a stale
        pre-rebase green carrying straight through).
        """
        settings = mock_settings()
        gh = mock_github_client()

        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []
        gh.get_incomplete_checks.return_value = []
        gh.get_completed_checks.return_value = []  # CI has not reported on this head

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

        mock_pipeline.assert_not_called()
        gh.merge_pr.assert_not_called()
        assert load_pr_state(state_path)["ci_wait_cycles"] == 1

    def test_required_check_not_yet_reported_defers(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Guard D: a configured required check that hasn't registered on the head
        (e.g. a separate-workflow `audit` job that starts later) → defer.

        Without this, the gate would see the main-workflow checks completed+green
        and the audit check simply absent (not failed, not pending) and merge before
        audit ever runs — which is how red-audit PRs reached main.
        """
        settings = mock_settings()
        settings.repos["TestRepo"].required_checks = ["audit"]
        gh = mock_github_client()

        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []
        gh.get_incomplete_checks.return_value = []
        # main-workflow checks done & green, but `audit` has not registered yet.
        gh.get_completed_checks.return_value = ["Test (pytest)", "Lint (ruff)"]

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

        mock_pipeline.assert_not_called()
        gh.merge_pr.assert_not_called()
        assert load_pr_state(state_path)["ci_wait_cycles"] == 1

    def test_required_check_present_and_green_proceeds(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Guard D: once the required check has reported (and passed), proceed."""
        settings = mock_settings()
        settings.repos["TestRepo"].required_checks = ["audit"]
        gh = mock_github_client()

        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []
        gh.get_incomplete_checks.return_value = []
        gh.get_completed_checks.return_value = ["Test (pytest)", "audit"]
        gh.get_mergeable.return_value = True

        with patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "LGTM", "summary": "All checks passed"},
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

        # required check satisfied → CI green → self-review LGTM → merge
        gh.merge_pr.assert_called_once_with("owner", "TestRepo", 42, merge_method="squash")

    def test_ci_red_then_green_allows_merge_after_fix(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: CI red → developer fixes → CI green → merge allowed.

        Scenario: Initial CI red blocks review. Developer pushes fix,
        CI becomes green. Next cycle proceeds with review and merges on LGTM.

        Acceptance: PR merges after CI fix (gate allows merge post-fix).
        """
        settings = mock_settings()
        pr_info = {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}}

        # Cycle 1: CI red (failing checks)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=0,
        )
        state_path = save_pr_state(tmp_path, state)

        gh1 = mock_github_client()
        gh1.get_failed_checks.return_value = ["Test: FAILED"]  # Red CI

        with patch.object(watcher, "_run_pipeline") as mock_pipeline1:
            watcher._phase1(
                state,
                state_path,
                pr_info,
                gh1,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: Deferred due to red CI
        mock_pipeline1.assert_not_called()
        state_after_cycle1 = load_pr_state(state_path)
        assert state_after_cycle1["ci_wait_cycles"] == 1

        # Cycle 2: CI green (developer fixed it)
        gh2 = mock_github_client()
        gh2.get_failed_checks.return_value = []  # Green CI (no failures)
        gh2.get_mergeable.return_value = True

        with patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "LGTM", "summary": "Good to merge"},
        ):
            watcher._phase1(
                state_after_cycle1,
                state_path,
                pr_info,
                gh2,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: CI green + LGTM → merged
        gh2.merge_pr.assert_called_once()
        # Assert: State cleaned up after merge
        assert not state_path.exists()

    def test_ci_green_with_lgtm_allows_merge(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: CI green + LGTM verdict → merge allowed.

        Acceptance: PR merges when CI has no failing checks and verdict is LGTM.
        """
        settings = mock_settings()
        gh = mock_github_client()

        # Setup: PR with no failing CI checks (green CI)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Setup: CI is green (no failed checks)
        gh.get_failed_checks.return_value = []
        gh.get_mergeable.return_value = True

        # Execute: Run phase1 with LGTM and CI green
        with patch.object(
            watcher,
            "_run_direct_review",
            return_value={"result": "LGTM", "summary": "All checks passed"},
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

        # Assert: PR was merged (CI green + LGTM)
        gh.merge_pr.assert_called_once_with("owner", "TestRepo", 42, merge_method="squash")

        # Assert: State file cleaned up (merged)
        assert not state_path.exists()

    def test_ci_gate_prevents_merge_with_failing_checks_even_with_lgtm(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Gate blocks merge even with LGTM if CI has failing checks.

        Scenario: Reviewer issues LGTM but CI has failing checks → merge blocked.
        Gate enforces CI-green as non-negotiable precondition.

        Acceptance: Zero false-pass merges with CI failing (safety).
        """
        settings = mock_settings()
        gh = mock_github_client()

        # Setup: PR in self_review, ready to merge but CI failing
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Setup: CI failing but would pass LGTM check
        gh.get_failed_checks.return_value = ["Integration Test: FAILED"]
        gh.get_mergeable.return_value = True

        # Execute: Try to review and merge with failing CI
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

        # Assert: CRITICAL SAFETY — review NOT called (blocked before pipeline)
        mock_pipeline.assert_not_called()

        # Assert: Merge NOT called (gate prevents merge with failing CI)
        gh.merge_pr.assert_not_called()

        # Assert: State persisted for polling CI fix
        assert state_path.exists()
        state_after = load_pr_state(state_path)
        assert state_after["ci_wait_cycles"] == 1

    def test_ci_wait_cycles_at_max_escalates(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: CI wait cycles reach max → escalate for human review.

        Scenario: CI has been failing for max_ci_wait_cycles (20) checks →
        escalate to human instead of waiting forever.

        Acceptance: Gate escalates when CI red for too long (prevents silent stall).
        """
        settings = mock_settings()
        gh = mock_github_client()

        # Setup: PR with ci_wait_cycles at max (20)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=20,  # At max
        )
        state_path = save_pr_state(tmp_path, state)

        # Setup: CI still failing
        gh.get_failed_checks.return_value = ["Critical Test: FAILED"]

        # Execute: Run phase1 with ci_wait_cycles at max
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

        # Assert: Review NOT called (escalation path, no review)
        mock_pipeline.assert_not_called()

        # Assert: PR NOT merged (escalated, not merged)
        gh.merge_pr.assert_not_called()

        # Assert: Escalation comment posted
        gh.post_comment.assert_called()

    def test_ci_wait_cycles_incremented_on_each_red_check(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: Each red CI check increments ci_wait_cycles.

        Scenario: Multiple cycles of CI failing → ci_wait_cycles incremented each time.

        Acceptance: Counter tracks CI wait duration accurately.
        """
        settings = mock_settings()
        pr_info = {"number": 42, "title": "Test PR", "draft": False, "head": {"ref": "goal/42"}}

        # Cycle 1: CI red
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=0,
        )
        state_path = save_pr_state(tmp_path, state)

        for cycle in range(1, 4):  # 3 cycles
            gh = mock_github_client()
            gh.get_failed_checks.return_value = ["Test: FAILED"]

            with patch.object(watcher, "_run_pipeline") as mock_pipeline:
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

            mock_pipeline.assert_not_called()
            state = load_pr_state(state_path)
            # Verify ci_wait_cycles incremented
            assert state["ci_wait_cycles"] == cycle, (
                f"Cycle {cycle}: expected ci_wait_cycles={cycle}, got {state['ci_wait_cycles']}"
            )

    def test_ci_wait_cycles_reset_when_green(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
    ):
        """Test: CI wait cycles reset to 0 when CI becomes green.

        Scenario: CI red (ci_wait_cycles=5) → developer fixes → CI green →
        ci_wait_cycles reset to 0.

        Acceptance: Counter resets on CI fix (tracks fresh waits only).
        """
        settings = mock_settings()
        gh = mock_github_client()

        # Setup: PR with ci_wait_cycles already at 5 (was waiting)
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            ci_wait_cycles=5,
        )
        state_path = save_pr_state(tmp_path, state)

        # Setup: CI now green (no failed checks)
        gh.get_failed_checks.return_value = []
        gh.get_mergeable.return_value = True

        # Execute: Run phase1 with CI green
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

        # Assert: Merge was called (CI green + LGTM)
        gh.merge_pr.assert_called_once()
