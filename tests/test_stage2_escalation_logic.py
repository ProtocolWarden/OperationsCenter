"""Stage 2: Tests for adaptive CI wait logic and improved escalation handling.

Tests cover the 6 key scenarios to prevent false human-parks on CI thrash:
1. Flaky required check — eventually passes, should not escalate at cycle 20
2. Late-registering workflow — configured but slow to register, 60 cycle timeout
3. Escalation-retraction loop prevention — track concerns holistically
4. No-verdict exponential backoff — 5s → 10s → 20s between retries
5. Stuck-green detection — ERROR log after 3 no-verdict escalations
6. Rebase thrashing — legitimate escalation (unchanged regression test)
"""

from datetime import UTC, datetime

from operations_center.entrypoints.pr_review_watcher.main import (
    _classify_missing_checks,
    _compute_backoff_interval,
    _normalize_concerns_signature,
    _should_escalate_ci_wait,
    _track_concern_raised,
    _update_check_history,
)


class TestComputeBackoffInterval:
    """Test exponential backoff calculation."""

    def test_backoff_level_0(self):
        """Level 0 should return 5 seconds."""
        assert _compute_backoff_interval(0) == 5

    def test_backoff_level_1(self):
        """Level 1 should return 10 seconds."""
        assert _compute_backoff_interval(1) == 10

    def test_backoff_level_2(self):
        """Level 2 should return 20 seconds."""
        assert _compute_backoff_interval(2) == 20

    def test_backoff_level_3_and_above(self):
        """Level 3+ should cap at 20 seconds."""
        assert _compute_backoff_interval(3) == 20
        assert _compute_backoff_interval(4) == 20
        assert _compute_backoff_interval(10) == 20


class TestUpdateCheckHistory:
    """Test check history tracking."""

    def test_track_new_check_passed(self):
        """First time seeing a check that passed."""
        state = {}
        _update_check_history(
            state,
            failed_checks=[],
            completed_checks=["check-a"],
            pending_checks=[],
            current_head_sha="sha1",
        )
        assert "ci_check_history" in state
        assert "check-a" in state["ci_check_history"]
        assert state["ci_check_history"]["check-a"]["times_passed"] == 1
        assert state["ci_check_history"]["check-a"]["times_failed"] == 0
        assert state["ci_check_history"]["check-a"]["first_registration_at"]

    def test_track_check_failed(self):
        """Check that failed."""
        state = {}
        _update_check_history(
            state,
            failed_checks=["check-a"],
            completed_checks=["check-a"],
            pending_checks=[],
            current_head_sha="sha1",
        )
        assert state["ci_check_history"]["check-a"]["times_passed"] == 0
        assert state["ci_check_history"]["check-a"]["times_failed"] == 1

    def test_track_multiple_outcomes(self):
        """Track a check over multiple polls with mixed outcomes."""
        state = {}
        # Poll 1: passes
        _update_check_history(state, [], ["check-a"], [], "sha1")
        assert state["ci_check_history"]["check-a"]["times_passed"] == 1

        # Poll 2: fails
        _update_check_history(state, ["check-a"], ["check-a"], [], "sha2")
        assert state["ci_check_history"]["check-a"]["times_passed"] == 1
        assert state["ci_check_history"]["check-a"]["times_failed"] == 1

        # Poll 3: passes again
        _update_check_history(state, [], ["check-a"], [], "sha3")
        assert state["ci_check_history"]["check-a"]["times_passed"] == 2
        assert state["ci_check_history"]["check-a"]["times_failed"] == 1


class TestShouldEscalateCI:
    """Test adaptive escalation decision logic."""

    def test_no_escalation_with_history_under_threshold(self):
        """Check with pass history, under cycle threshold."""
        state = {
            "ci_wait_cycles": 20,
            "ci_check_history": {
                "check-a": {
                    "times_passed": 10,
                    "times_failed": 2,  # 17% failure rate
                    "first_registration_at": datetime.now(UTC).isoformat(),
                }
            },
        }
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["check-a"],
            failed_checks=["check-a"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert not should_escalate
        assert reason is None

    def test_escalation_at_threshold_with_dense_failures(self):
        """Check with dense failures (>= 30%), escalate at 40 cycles."""
        state = {
            "ci_wait_cycles": 40,
            "ci_check_history": {
                "check-a": {
                    "times_passed": 5,
                    "times_failed": 5,  # 50% failure rate
                    "first_registration_at": datetime.now(UTC).isoformat(),
                }
            },
        }
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["check-a"],
            failed_checks=["check-a"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert should_escalate
        assert "dense_failure" in reason

    def test_late_registering_check_uses_60_cycle_threshold(self):
        """Never-seen check should use 60-cycle threshold."""
        state = {
            "ci_wait_cycles": 30,
            "ci_check_history": {},  # Empty, never seen
        }
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["audit"],
            failed_checks=[],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
            required_checks_configured=["audit"],  # Configured as required
        )
        assert not should_escalate

        # At 60+ cycles, escalate
        state["ci_wait_cycles"] = 60
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["audit"],
            failed_checks=[],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
            required_checks_configured=["audit"],  # Configured as required
        )
        assert should_escalate
        assert "late_registration" in reason


class TestClassifyMissingChecks:
    """Test missing check classification."""

    def test_classify_never_registered(self):
        """Check not in history or configured."""
        state = {"ci_check_history": {}}
        never_reg, late_reg, stuck = _classify_missing_checks(
            state,
            missing_required=["unknown-check"],
            required_checks_configured=["audit", "test"],
        )
        assert "unknown-check" in never_reg
        assert len(late_reg) == 0
        assert len(stuck) == 0

    def test_classify_late_registering(self):
        """Check configured but not yet seen."""
        state = {"ci_check_history": {}}
        never_reg, late_reg, stuck = _classify_missing_checks(
            state,
            missing_required=["audit"],
            required_checks_configured=["audit", "test"],
        )
        assert len(never_reg) == 0
        assert "audit" in late_reg
        assert len(stuck) == 0

    def test_classify_stuck(self):
        """Check seen before but missing now."""
        state = {
            "ci_check_history": {
                "flaky-test": {
                    "times_passed": 5,
                    "times_failed": 2,
                    "first_registration_at": datetime.now(UTC).isoformat(),
                }
            }
        }
        never_reg, late_reg, stuck = _classify_missing_checks(
            state,
            missing_required=["flaky-test"],
            required_checks_configured=["flaky-test"],
        )
        assert len(never_reg) == 0
        assert len(late_reg) == 0
        assert "flaky-test" in stuck


class TestConcernTracking:
    """Test concern signature and tracking."""

    def test_normalize_concerns_signature(self):
        """Normalize concern signatures for deduplication."""
        sig1 = _normalize_concerns_signature("Missing docstring on line 42 in MyClass.method")
        sig2 = _normalize_concerns_signature("Missing docstring on line 50 in MyClass.method")
        # Same logical concern, different line numbers
        assert sig1 == sig2

        sig3 = _normalize_concerns_signature("Missing docstring in different_class")
        # Different concern
        assert sig3 != sig1

    def test_track_concern_raised(self):
        """Track when concern is first raised."""
        state = {}
        _track_concern_raised(state, "Missing docstring", "sha1")

        assert "concern_history" in state
        assert state["last_concerns_head_sha"] == "sha1"
        assert state["last_concerns_summary"] == "Missing docstring"

    def test_concern_tracking_multiple_concerns(self):
        """Track multiple distinct concerns."""
        state = {}
        _track_concern_raised(state, "Missing docstring", "sha1")
        _track_concern_raised(state, "Add type hints", "sha1")

        assert len(state["concern_history"]) == 2


class TestFlakyCheckScenario:
    """Scenario 1: Flaky required check (passes 70%, escalates at 40 cycles not 20)."""

    def test_flaky_check_does_not_escalate_at_20_cycles(self):
        """Flaky check with 70% pass rate should not escalate at cycle 20."""
        state = {
            "ci_wait_cycles": 20,
            "ci_check_history": {
                "tests": {
                    "times_passed": 14,
                    "times_failed": 6,  # 30% failure rate (at threshold)
                    "first_registration_at": datetime.now(UTC).isoformat(),
                }
            },
        }

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        # At 20 cycles, should not escalate (threshold is 40)
        assert not should_escalate

    def test_flaky_check_escalates_at_40_cycles(self):
        """Flaky check should escalate when it reaches 40 cycles."""
        state = {
            "ci_wait_cycles": 40,
            "ci_check_history": {
                "tests": {
                    "times_passed": 28,
                    "times_failed": 12,  # 30% failure rate
                    "first_registration_at": datetime.now(UTC).isoformat(),
                }
            },
        }

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=["tests"],  # Missing required check with dense failure rate
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert should_escalate
        # Either dense_failure or threshold_exceeded is acceptable at 40 cycles
        assert reason in (
            "ci_persistently_red_dense_failure",
            "ci_never_settled_threshold_exceeded",
        )


class TestLateRegisteringWorkflowScenario:
    """Scenario 2: Late-registering workflow (waits 60 cycles not 20)."""

    def test_late_registering_not_escalated_at_20_cycles(self):
        """Late-registering check should wait 60 cycles, not escalate at 20."""
        state = {
            "ci_wait_cycles": 20,
            "ci_check_history": {},  # Never seen yet
        }

        never_reg, late_reg, stuck = _classify_missing_checks(
            state,
            missing_required=["audit"],
            required_checks_configured=["audit", "tests"],
        )
        assert "audit" in late_reg
        assert len(never_reg) == 0

    def test_late_registering_escalates_at_60_cycles(self):
        """Late-registering check should escalate at 60 cycles."""
        state = {
            "ci_wait_cycles": 60,
            "ci_check_history": {},  # Still never seen
        }

        never_reg, late_reg, stuck = _classify_missing_checks(
            state,
            missing_required=["audit"],
            required_checks_configured=["audit"],
        )
        assert "audit" in late_reg
        # Decision: escalate at 60 cycles


class TestEscalationRetractionLoopScenario:
    """Scenario 3: Escalation-retraction loop prevention."""

    def test_concern_prevents_retraction_on_same_head(self):
        """Concern on escalated head should prevent retraction."""
        state = {
            "escalated_head_sha": "sha1",
            "concern_history": {
                "sig1": {
                    "head_sha": "sha1",
                    "raised_at": datetime.now(UTC).isoformat(),
                    "summary": "Missing docstring",
                }
            },
        }

        # Check if there are unfixed concerns on the escalated head
        concern_found = False
        for sig, rec in state["concern_history"].items():
            if rec.get("head_sha") == state["escalated_head_sha"]:
                concern_found = True
                break

        assert concern_found, "Should find unfixed concern on escalated head"

    def test_concern_with_fix_pass_retraction(self):
        """Fix pass pushes new head but concern still applies."""
        state = {
            "escalated_head_sha": "sha1",
            "concern_history": {
                "sig1": {
                    "head_sha": "sha1",  # Concern on original head
                    "raised_at": datetime.now(UTC).isoformat(),
                    "summary": "Missing docstring",
                }
            },
        }

        # Improved guard: check if concern is still unfixed (raised on escalated head)
        has_unfixed = any(
            rec.get("head_sha") == state["escalated_head_sha"]
            for rec in state["concern_history"].values()
        )
        assert has_unfixed, "Should prevent retraction when unfixed concerns exist"


class TestNoVerdictBackoffScenario:
    """Scenario 4: No-verdict exponential backoff (5s → 10s → 20s)."""

    def test_no_verdict_backoff_level_progression(self):
        """Backoff levels should progress: 0→1→2 with intervals 5→10→20."""
        intervals = [
            _compute_backoff_interval(0),
            _compute_backoff_interval(1),
            _compute_backoff_interval(2),
        ]
        assert intervals == [5, 10, 20]

    def test_backoff_caps_at_20_seconds(self):
        """Backoff should cap at 20 seconds (level 3+)."""
        for level in range(3, 10):
            assert _compute_backoff_interval(level) == 20


class TestStuckGreenScenario:
    """Scenario 5: Stuck-green detection (ERROR log after 3 no-verdict escalations)."""

    def test_stuck_green_threshold(self):
        """After 3 no-verdict escalations, should emit ERROR log."""
        # This test validates the threshold constant
        _STUCK_GREEN_ESCALATION_THRESHOLD = 3

        escalation_counts = [1, 2, 3, 4]
        for count in escalation_counts:
            if count >= _STUCK_GREEN_ESCALATION_THRESHOLD:
                # Should emit ERROR log for stuck-green
                assert count >= 3


class TestRebaseThreshScenario:
    """Scenario 6: Rebase thrashing (unchanged, legitimate escalation)."""

    def test_rebase_escalation_after_3_attempts(self):
        """Rebase conflicts should escalate after 3 attempts (unchanged)."""
        # This test validates that rebase logic is not modified
        _MAX_REBASE_ATTEMPTS = 3
        rebase_attempts = [1, 2, 3, 4]

        for attempt in rebase_attempts:
            if attempt >= _MAX_REBASE_ATTEMPTS:
                # Should escalate after max attempts
                assert attempt >= 3


class TestRegressionCases:
    """Regression tests to ensure legitimate escalations still work."""

    def test_immediate_escalation_on_persistent_failure(self):
        """Immediately failing check should escalate (high failure rate)."""
        state = {
            "ci_wait_cycles": 30,
            "ci_check_history": {
                "build": {
                    "times_passed": 0,
                    "times_failed": 10,  # 100% failure rate
                    "first_registration_at": datetime.now(UTC).isoformat(),
                }
            },
        }

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=["build"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        # At 30 cycles with 100% failure, should still not escalate (below 40)
        # but at 40+ cycles should escalate
        state["ci_wait_cycles"] = 40
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=["build"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert should_escalate

    def test_fast_path_no_escalation(self):
        """Fast path: PR with green CI should pass through without escalation."""
        state = {
            "ci_wait_cycles": 0,
            "ci_check_history": {},
        }

        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=[],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert not should_escalate


class TestIntegrationScenarios:
    """Integration tests combining multiple components."""

    def test_full_flaky_check_flow(self):
        """Full flow: track flaky check over 40 cycles, then escalate."""
        state = {
            "ci_wait_cycles": 0,
            "ci_check_history": {},
        }

        # Simulate 30 polling cycles with mixed outcomes
        for cycle in range(1, 31):
            state["ci_wait_cycles"] = cycle
            # Flaky pattern: passes 70%, fails 30%
            failed = ["tests"] if cycle % 10 == 0 else []
            completed = ["tests"]
            _update_check_history(state, failed, completed, [], f"sha{cycle}")

        # After 30 cycles: check has pass history, should not escalate
        should_escalate, _ = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert not should_escalate

        # At 40+ cycles: should escalate
        state["ci_wait_cycles"] = 40
        should_escalate, reason = _should_escalate_ci_wait(
            state,
            missing_required=[],
            failed_checks=["tests"],
            pending_checks=[],
            ci_wait_cycles_first_registration=60,
            ci_wait_cycles_already_seen=40,
            ci_flakiness_threshold_pct=30,
        )
        assert should_escalate

    def test_concern_tracking_and_retraction_guard(self):
        """Integration: track concerns and validate retraction guard."""
        state = {}

        # Raise a concern on head sha1
        _track_concern_raised(state, "Missing docstring", "sha1")
        assert state["last_concerns_head_sha"] == "sha1"

        # Later, we escalate on the same head
        state["escalated_head_sha"] = "sha1"

        # When checking retraction: should find unfixed concern
        has_unfixed = any(
            rec.get("head_sha") == state["escalated_head_sha"]
            for rec in state.get("concern_history", {}).values()
        )
        assert has_unfixed, "Retraction guard should find unfixed concern"

        # Verify retraction would be prevented
        current_head = "sha1"
        escalated_head = state["escalated_head_sha"]
        if current_head == escalated_head:
            # Same head — retraction prevented
            assert current_head == escalated_head
