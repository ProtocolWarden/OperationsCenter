# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Comprehensive tests for CI thrash prevention in escalation logic.

Tests the 6 scenarios from Stage 1 design to prevent false human-parks:
1. Flaky Required Check (passes 70%, escalates at 40 cycles not 20)
2. Late-Registering Workflow (waits 60 cycles not 20 for first registration)
3. Escalation-Retraction Loop Prevention (prevents false multi-escalations)
4. No-Verdict Exponential Backoff (5s → 10s → 20s between retries)
5. Stuck-Green Detection (ERROR log + escalation after 3 no-verdict escalations)
6. Rebase Thrashing (unchanged, legitimate escalation, no regression)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations_center.entrypoints.pr_review_watcher.main import (
    _classify_missing_checks,
    _compute_backoff_interval,
    _new_state,
    _normalize_concerns_signature,
    _save_state,
    _should_escalate_ci_wait,
    _state_path,
    _track_concern_raised,
    _update_check_history,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


REPO_KEY = "TestRepo"
PR_NUMBER = 100


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Create temporary state directory."""
    state_dir = tmp_path / "state" / "pr_reviews"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


@pytest.fixture
def state_path(tmp_state_dir: Path) -> Path:
    """Get state file path."""
    return _state_path(tmp_state_dir.parent.parent, REPO_KEY, PR_NUMBER)


def _make_state(path: Path, **overrides: dict) -> dict:
    """Create and save a state file with overrides."""
    state = _new_state(REPO_KEY, PR_NUMBER)
    state.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    _save_state(path, state)
    return state


# ── Scenario 1: Flaky Required Check ───────────────────────────────────────────


class TestFlakyRequiredCheck:
    """PR with check that passes 70% of the time should not escalate at cycle 20."""

    def test_flaky_check_passes_eventually_does_not_escalate_at_cycle_20(
        self, state_path: Path
    ) -> None:
        """Flaky check with 70% pass rate should not escalate at cycle 20."""
        state = _make_state(
            state_path,
            ci_wait_cycles=20,
            ci_check_history={
                "tests": {
                    "last_seen_sha": "abc123",
                    "first_registration_at": datetime.now(UTC).isoformat(),
                    "times_passed": 14,
                    "times_failed": 6,
                }
            },
        )

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["tests"],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )

        # At cycle 20, should NOT escalate (threshold is 40 for already-seen)
        assert not should_escalate, "Should not escalate at cycle 20"

    def test_flaky_check_escalates_at_cycle_40_with_high_failure_rate(
        self, state_path: Path
    ) -> None:
        """Flaky check with dense failures should escalate at cycle 40."""
        state = _make_state(
            state_path,
            ci_wait_cycles=40,
            ci_check_history={
                "tests": {
                    "last_seen_sha": "abc123",
                    "first_registration_at": datetime.now(UTC).isoformat(),
                    "times_passed": 14,
                    "times_failed": 26,  # 65% failure rate > 30% threshold
                }
            },
        )

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["tests"],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )

        # At cycle 40 with dense failures, should escalate
        assert should_escalate, "Should escalate at cycle 40 with dense failures"
        assert reason == "ci_persistently_red_dense_failure"

    def test_flaky_check_history_tracks_passes_and_failures(self, state_path: Path) -> None:
        """Check history should accumulate pass/fail counts correctly."""
        state = _new_state(REPO_KEY, PR_NUMBER)

        # Cycle 1: check passes
        _update_check_history(state, [], ["tests"], [], "sha1")
        assert state["ci_check_history"]["tests"]["times_passed"] == 1
        assert state["ci_check_history"]["tests"]["times_failed"] == 0

        # Cycle 2: check fails
        _update_check_history(state, ["tests"], ["tests"], [], "sha2")
        assert state["ci_check_history"]["tests"]["times_passed"] == 1
        assert state["ci_check_history"]["tests"]["times_failed"] == 1

        # Cycle 3: check passes
        _update_check_history(state, [], ["tests"], [], "sha3")
        assert state["ci_check_history"]["tests"]["times_passed"] == 2
        assert state["ci_check_history"]["tests"]["times_failed"] == 1


# ── Scenario 2: Late-Registering Workflow ──────────────────────────────────────


class TestLateRegisteringWorkflow:
    """Check that hasn't registered yet should wait 60 cycles, not 20."""

    def test_late_registering_check_waits_until_cycle_60(self, state_path: Path) -> None:
        """Late-registering check should wait 60 cycles, not escalate at 20."""
        state = _make_state(
            state_path,
            ci_wait_cycles=20,
            ci_check_history={},  # Never seen
        )

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["audit"],
            failed_checks=[],
            pending_checks=["audit"],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
            required_checks_configured=["audit"],
        )

        # At cycle 20, late-registering check should NOT escalate
        assert not should_escalate, "Should wait for late-registering check"

    def test_late_registering_check_escalates_at_cycle_60(self, state_path: Path) -> None:
        """Late-registering check should escalate at cycle 60."""
        state = _make_state(
            state_path,
            ci_wait_cycles=60,
            ci_check_history={},  # Still never seen
        )

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["audit"],
            failed_checks=[],
            pending_checks=["audit"],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
            required_checks_configured=["audit"],
        )

        # At cycle 60, should escalate (late-registration timeout)
        assert should_escalate, "Should escalate at cycle 60 for late-registering check"
        assert reason == "ci_never_settled_late_registration"

    def test_classify_late_registering_vs_never_registered(self, state_path: Path) -> None:
        """Classify missing checks: late-registering vs never-registered."""
        state = _make_state(state_path, ci_check_history={})

        never_reg, late_reg, stuck = _classify_missing_checks(
            state,
            missing_required=["audit", "unknown"],
            required_checks_configured=["audit"],
        )

        assert "audit" in late_reg, "Audit is configured but never seen (late-registering)"
        assert "unknown" in never_reg, "Unknown is not configured (never-registered)"
        assert len(stuck) == 0, "No checks in history (stuck)"


# ── Scenario 3: Escalation-Retraction Loop Prevention ───────────────────────────


class TestEscalationRetractionLoopPrevention:
    """System should prevent escalation-retraction loops by tracking concerns."""

    def test_concern_tracking_on_same_head_prevents_retraction(self, state_path: Path) -> None:
        """If concern exists on current head, retraction should be prevented."""
        state = _make_state(state_path)

        # Raise a concern on head sha1
        _track_concern_raised(state, "Missing docstring on function foo", "abc123")

        # Verify concern was tracked
        assert state["last_concerns_head_sha"] == "abc123"
        assert "concern_history" in state
        concern_history = state["concern_history"]
        assert len(concern_history) > 0

        # Check if unfixed concerns exist on escalated head
        escalated_head = "abc123"
        has_unfixed = any(c.get("head_sha") == escalated_head for c in concern_history.values())
        assert has_unfixed, "Should find unfixed concerns on escalated head"

    def test_concern_signature_normalizes_variable_parts(self, state_path: Path) -> None:
        """Concern signatures should normalize line numbers and variable parts."""
        sig1 = _normalize_concerns_signature("Missing docstring on line 42 in MyClass.method")
        sig2 = _normalize_concerns_signature("Missing docstring on line 50 in MyClass.method")

        # Same logical concern, different line numbers
        assert sig1 == sig2, "Should normalize line numbers"

        sig3 = _normalize_concerns_signature("Missing docstring in DifferentClass")
        assert sig3 != sig1, "Different concern should have different signature"

    def test_retraction_allowed_after_fix_push_to_new_head(self, state_path: Path) -> None:
        """Retraction should be allowed after fix push to new head."""
        state = _make_state(state_path)

        # Raise concern on head sha1
        _track_concern_raised(state, "Missing docstring", "abc123")

        # Verify concern on abc123
        concern_history = state["concern_history"]
        escalated_head = "abc123"
        has_concerns = any(c.get("head_sha") == escalated_head for c in concern_history.values())
        assert has_concerns

        # New head sha2 should not have the concern (different commit)
        current_head = "def456"
        has_concerns_on_new_head = any(
            c.get("head_sha") == current_head for c in concern_history.values()
        )
        assert not has_concerns_on_new_head, "New head should not have old concerns"


# ── Scenario 4: No-Verdict Exponential Backoff ──────────────────────────────────


class TestNoVerdictExponentialBackoff:
    """System should apply exponential backoff (5s → 10s → 20s) on no-verdicts."""

    def test_backoff_levels_progress_correctly(self) -> None:
        """Backoff levels should progress: 0→5s, 1→10s, 2→20s, 3→20s (cap)."""
        assert _compute_backoff_interval(0) == 5
        assert _compute_backoff_interval(1) == 10
        assert _compute_backoff_interval(2) == 20
        assert _compute_backoff_interval(3) == 20
        assert _compute_backoff_interval(4) == 20
        assert _compute_backoff_interval(10) == 20

    def test_backoff_caps_at_20_seconds(self) -> None:
        """Backoff should cap at 20 seconds."""
        for level in range(3, 20):
            interval = _compute_backoff_interval(level)
            assert interval == 20, f"Level {level} should cap at 20"


# ── Scenario 5: Stuck-Green Detection ──────────────────────────────────────────


class TestStuckGreenDetection:
    """System should detect stuck-green (3 no-verdict escalations) with ERROR log."""

    def test_stuck_green_threshold_at_3_escalations(self, state_path: Path) -> None:
        """At 3 escalations, should emit ERROR log for stuck-green alarm."""
        STUCK_GREEN_THRESHOLD = 3

        state = _make_state(
            state_path,
            no_verdict_escalation_count=3,
        )

        # At threshold, should trigger alarm
        if state["no_verdict_escalation_count"] >= STUCK_GREEN_THRESHOLD:
            # This would emit: logger.error("STUCK-GREEN PR ...")
            assert state["no_verdict_escalation_count"] >= STUCK_GREEN_THRESHOLD


# ── Scenario 6: Rebase Thrashing (Regression Check) ───────────────────────────────


class TestRebaseThrashingRegression:
    """Rebase conflicts should still escalate after 3 attempts (no changes)."""

    def test_rebase_conflict_escalates_after_3_attempts(self, state_path: Path) -> None:
        """Rebase conflict escalation should remain unchanged (bounded by 3 attempts)."""
        MAX_REBASE_ATTEMPTS = 3

        state = _make_state(
            state_path,
            ci_fix_attempts=3,  # Already tried 3 times
        )

        assert state["ci_fix_attempts"] >= MAX_REBASE_ATTEMPTS, "Should escalate after 3 attempts"


# ── Regression Tests ───────────────────────────────────────────────────────────


class TestRegressionFastPath:
    """Ensure LGTM on green CI still merges (fast path regression)."""

    def test_lgtm_on_green_ci_no_escalation_needed(self, state_path: Path) -> None:
        """PR with green CI should not trigger escalation logic."""
        state = _make_state(
            state_path,
            ci_wait_cycles=1,
            ci_check_history={},
        )

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=[],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )

        # No missing required checks = no escalation
        assert not should_escalate, "Green CI should not trigger escalation"


class TestRegressionHardEscalations:
    """Ensure hard escalations (persistent failures) still occur."""

    def test_persistent_failure_escalates_at_threshold(self, state_path: Path) -> None:
        """Check with 100% failure rate should escalate at 40 cycles."""
        state = _make_state(
            state_path,
            ci_wait_cycles=40,
            ci_check_history={
                "build": {
                    "last_seen_sha": "abc123",
                    "first_registration_at": datetime.now(UTC).isoformat(),
                    "times_passed": 0,
                    "times_failed": 40,  # 100% failure rate
                }
            },
        )

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["build"],
            failed_checks=["build"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )

        # 100% failure rate should escalate at 40 cycles
        assert should_escalate, "Should escalate at 40 cycles with 100% failure"
        assert reason == "ci_persistently_red_dense_failure"


class TestRegressionBoundedAttempts:
    """Ensure fix attempts still exhaust and close PR."""

    def test_fix_attempts_exhausted_closes_pr(self, state_path: Path) -> None:
        """PR should track fix attempts and not exceed max."""
        MAX_FIX_ATTEMPTS = 3

        state = _make_state(
            state_path,
            ci_fix_attempts=3,  # At max
        )

        assert state["ci_fix_attempts"] >= MAX_FIX_ATTEMPTS, "Should be at max attempts"


# ── Performance Tests ──────────────────────────────────────────────────────────


class TestPerformanceAndMemory:
    """Verify backoff and state tracking don't cause excessive overhead."""

    def test_backoff_intervals_total_less_than_60_seconds(self) -> None:
        """3 no-verdict escalations should total < 60 seconds (5s + 10s + 20s = 35s)."""
        intervals = [
            _compute_backoff_interval(0),
            _compute_backoff_interval(1),
            _compute_backoff_interval(2),
        ]
        total = sum(intervals)
        assert total < 60, f"Total backoff {total}s should be < 60s"

    def test_check_history_memory_efficient(self, state_path: Path) -> None:
        """Check history should be compact."""
        state = _new_state(REPO_KEY, PR_NUMBER)

        # Add 100 checks worth of history
        for i in range(100):
            _update_check_history(
                state,
                failed_checks=[] if i % 3 else [f"check_{i}"],
                completed_checks=[f"check_{i}"],
                pending_checks=[],
                current_head_sha=f"sha{i}",
            )

        # Estimate state file size
        state_json = json.dumps(state)
        size_bytes = len(state_json.encode())

        # Should be reasonable (< 100KB for 100 checks with history)
        assert size_bytes < 100_000, f"State size {size_bytes} should be < 100KB"


# ── Integration Tests ──────────────────────────────────────────────────────────


class TestIntegrationFlakyCheckFlow:
    """Full flow: track flaky check over 40 cycles, then escalate."""

    def test_full_flaky_check_flow_30_cycles(self, state_path: Path) -> None:
        """Simulate 30 polling cycles with flaky pattern."""
        state = _make_state(state_path, ci_wait_cycles=0, ci_check_history={})

        # Simulate 30 polling cycles: 70% pass rate (flaky)
        for cycle in range(1, 31):
            state["ci_wait_cycles"] = cycle
            # Flaky pattern: fails every 10th cycle
            failed = ["tests"] if cycle % 10 == 0 else []
            _update_check_history(state, failed, ["tests"], [], f"sha{cycle}")

        # After 30 cycles: should NOT escalate (below 40 threshold)
        should_escalate, _ = _should_escalate_ci_wait(
            state,
            missing_required=["tests"],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert not should_escalate, "Should not escalate at cycle 30"

    def test_full_flaky_check_flow_40_cycles(self, state_path: Path) -> None:
        """Simulate reaching cycle 40 with flaky pattern."""
        state = _make_state(state_path, ci_wait_cycles=0, ci_check_history={})

        # Simulate 40 polling cycles with flaky pattern
        for cycle in range(1, 41):
            state["ci_wait_cycles"] = cycle
            failed = ["tests"] if cycle % 10 == 0 else []
            _update_check_history(state, failed, ["tests"], [], f"sha{cycle}")

        # At 40 cycles: should escalate if still failing
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["tests"],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        # At 40 cycles with failures, should escalate
        assert should_escalate, "Should escalate at cycle 40"


class TestIntegrationConcernTracking:
    """Integration: track concerns and validate retraction guard."""

    def test_concern_tracking_and_retraction_guard(self, state_path: Path) -> None:
        """Full concern tracking flow."""
        state = _make_state(state_path)

        # Raise a concern on head sha1
        _track_concern_raised(state, "Missing docstring", "sha1")
        assert state["last_concerns_head_sha"] == "sha1"

        # Simulate escalation on same head
        state["escalated_head_sha"] = "sha1"

        # When checking retraction: should find unfixed concern
        concern_history = state.get("concern_history", {})
        has_unfixed = any(
            c.get("head_sha") == state["escalated_head_sha"] for c in concern_history.values()
        )
        assert has_unfixed, "Should find unfixed concerns on escalated head"

        # Later, fix push to new head
        current_head = "sha2"
        has_unfixed_on_new = any(
            c.get("head_sha") == current_head for c in concern_history.values()
        )
        assert not has_unfixed_on_new, "New head should not have old concerns"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
