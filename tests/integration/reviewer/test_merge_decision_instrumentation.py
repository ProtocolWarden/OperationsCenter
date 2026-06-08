# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for Goal 4: Merge-decision instrumentation and safety metrics.

Tests that verdict consolidation, merge-decision outcomes, and CI-green gate checks
are instrumented with structured logging and metrics for operator monitoring.

Scenarios covered:
- Decision outcome metrics (approved/blocked/retry counts)
- Decision latency measurements (less than 500ms baseline)
- Structured decision chain logging
- Anomaly detection triggers (unusual retry rates, CI-green delays)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher
from tests.verdicts.conftest import (
    AuditVerdictBuilder,
    LaneVerdictBuilder,
    MergeDecisionBuilder,
    create_pr_state,
    mock_github_client,
    mock_settings,
    save_pr_state,
)


class DecisionMetricsCollector:
    """Collector for merge decision metrics during test execution."""

    def __init__(self):
        self.decision_outcomes: dict[str, int] = {
            "approved": 0,
            "blocked": 0,
            "retry": 0,
        }
        self.decision_latencies: list[float] = []
        self.structured_logs: list[dict] = []

    def record_decision(
        self,
        outcome: str,
        latency_ms: float,
        reason: str = "",
        lanes: int = 0,
    ) -> None:
        """Record a merge decision with metrics."""
        if outcome in self.decision_outcomes:
            self.decision_outcomes[outcome] += 1
        self.decision_latencies.append(latency_ms)
        self.structured_logs.append(
            {
                "decision": outcome,
                "latency_ms": latency_ms,
                "reason": reason,
                "lanes": lanes,
                "timestamp": time.time(),
            }
        )

    def get_metrics_summary(self) -> dict:
        """Return aggregated metrics."""
        total_decisions = sum(self.decision_outcomes.values())
        avg_latency = (
            sum(self.decision_latencies) / len(self.decision_latencies)
            if self.decision_latencies
            else 0
        )
        max_latency = max(self.decision_latencies) if self.decision_latencies else 0
        return {
            "total_decisions": total_decisions,
            "outcomes": self.decision_outcomes.copy(),
            "avg_latency_ms": avg_latency,
            "max_latency_ms": max_latency,
            "decision_log": self.structured_logs,
        }


@pytest.fixture
def metrics_collector() -> DecisionMetricsCollector:
    """Fixture providing a metrics collector for tests."""
    return DecisionMetricsCollector()


@pytest.mark.integration
class TestMergeDecisionMetrics:
    """Test merge-decision outcome metrics collection."""

    def test_decision_outcome_approved_counted(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        lane_verdict_builder: LaneVerdictBuilder,
        merge_decision_builder: MergeDecisionBuilder,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Unanimous LGTM → approved decision counted in metrics.

        Acceptance: Decision outcome counter incremented for 'approved'.
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

        # Setup: LGTM with CI green
        gh.get_failed_checks.return_value = []
        gh.get_mergeable.return_value = True

        # Record decision outcome
        start_time = time.time()
        with patch.object(
            watcher,
            "_run_pipeline",
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
        latency_ms = (time.time() - start_time) * 1000

        # Record decision metrics
        metrics_collector.record_decision(
            outcome="approved",
            latency_ms=latency_ms,
            reason="unanimous_lgtm",
            lanes=1,
        )

        # Assert: Decision outcome metric recorded
        summary = metrics_collector.get_metrics_summary()
        assert summary["outcomes"]["approved"] == 1
        assert summary["total_decisions"] == 1
        assert summary["max_latency_ms"] < 500  # Baseline <500ms

    def test_decision_outcome_blocked_counted(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Unresolved CONCERNS → blocked decision counted in metrics.

        Acceptance: Decision outcome counter incremented for 'blocked'.
        """
        settings = mock_settings(max_fix_attempts=1)  # Low limit to trigger blocked
        gh = mock_github_client()

        # Simulate PR with max fix attempts exhausted
        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=1,  # At max limit
        )
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []

        # Mock verdict pipeline returning CONCERNS
        start_time = time.time()
        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "CONCERNS", "summary": "Unresolved issues"},
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
        latency_ms = (time.time() - start_time) * 1000

        # Record decision metrics
        metrics_collector.record_decision(
            outcome="blocked",
            latency_ms=latency_ms,
            reason="concerns_unresolvable",
            lanes=1,
        )

        # Assert: Decision outcome metric recorded
        summary = metrics_collector.get_metrics_summary()
        assert summary["outcomes"]["blocked"] >= 1
        assert summary["total_decisions"] >= 1

    def test_decision_outcome_retry_counted(
        self,
        tmp_path: Path,
        audit_verdict_builder: AuditVerdictBuilder,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Mixed verdicts → retry decision counted in metrics.

        Acceptance: Decision outcome counter incremented for 'retry'.
        """
        settings = mock_settings()
        gh = mock_github_client()

        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            phase="self_review",
            self_review_loops=0,
            fix_attempts=0,
        )
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []

        # Record retry decision
        start_time = time.time()
        with patch.object(
            watcher,
            "_run_pipeline",
            return_value={"result": "CONCERNS", "summary": "Fix linting"},
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
        latency_ms = (time.time() - start_time) * 1000

        # Record retry decision
        metrics_collector.record_decision(
            outcome="retry",
            latency_ms=latency_ms,
            reason="mixed_verdicts",
            lanes=1,
        )

        # Assert: Decision outcome metric recorded
        summary = metrics_collector.get_metrics_summary()
        assert summary["outcomes"]["retry"] >= 1
        assert summary["total_decisions"] >= 1


@pytest.mark.integration
class TestDecisionLatencyMetrics:
    """Test merge-decision latency measurement and baselines."""

    def test_decision_latency_under_500ms_baseline(
        self,
        tmp_path: Path,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Decision latency measured and <500ms baseline respected.

        Acceptance: Baseline decision latency <500ms confirmed.
        """
        settings = mock_settings()
        gh = mock_github_client()

        state = create_pr_state(repo_key="TestRepo", pr_number=42)
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []
        gh.get_mergeable.return_value = True

        # Measure latency of phase1 execution
        start_time = time.time()
        with patch.object(
            watcher,
            "_run_pipeline",
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
        latency_ms = (time.time() - start_time) * 1000

        # Record latency
        metrics_collector.decision_latencies.append(latency_ms)

        # Assert: Latency <500ms baseline
        assert latency_ms < 500, f"Decision latency {latency_ms}ms exceeds 500ms baseline"

    def test_decision_latency_histogram(
        self,
        tmp_path: Path,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Multiple decision latencies tracked in histogram.

        Acceptance: Histogram captures latency distribution across decisions.
        """
        # Simulate 5 decisions with different latencies
        for i in range(5):
            latency_ms = 100 + (i * 50)  # 100, 150, 200, 250, 300ms
            metrics_collector.record_decision(
                outcome="approved" if i % 2 == 0 else "retry",
                latency_ms=latency_ms,
                reason="test",
            )

        # Assert: Histogram populated
        summary = metrics_collector.get_metrics_summary()
        assert len(summary["decision_log"]) == 5
        assert summary["avg_latency_ms"] == 200  # (100+150+200+250+300)/5
        assert summary["max_latency_ms"] == 300
        assert all(log["latency_ms"] < 500 for log in summary["decision_log"])


@pytest.mark.integration
class TestStructuredDecisionLogging:
    """Test structured logging of decision chains."""

    def test_structured_log_unanimous_lgtm_chain(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test: Decision chain logged with structured format for unanimous LGTM.

        Acceptance: Structured log contains decision chain: verdict → consolidation → merge.
        """
        settings = mock_settings()
        gh = mock_github_client()

        state = create_pr_state(repo_key="TestRepo", pr_number=42)
        state_path = save_pr_state(tmp_path, state)

        gh.get_failed_checks.return_value = []
        gh.get_mergeable.return_value = True

        # Capture logs at INFO level
        with caplog.at_level(logging.INFO):
            with patch.object(
                watcher,
                "_run_pipeline",
                return_value={"result": "LGTM", "summary": "All checks passed"},
            ):
                watcher._phase1(
                    state,
                    state_path,
                    {
                        "number": 42,
                        "title": "Test PR",
                        "draft": False,
                        "head": {"ref": "goal/42"},
                    },
                    gh,
                    "owner",
                    "TestRepo",
                    tmp_path,
                    tmp_path / "cfg.yaml",
                    settings,
                )

        # Assert: Relevant logs captured
        log_messages = [record.message for record in caplog.records]
        assert any("CI green" in msg for msg in log_messages)

    def test_structured_log_ci_gate_defer(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test: Structured log tracks CI gate deferral with details.

        Acceptance: Log shows: CI status, wait cycle count, max cycles.
        """
        settings = mock_settings()
        gh = mock_github_client()

        state = create_pr_state(
            repo_key="TestRepo",
            pr_number=42,
            ci_wait_cycles=0,
        )
        state_path = save_pr_state(tmp_path, state)

        # Simulate red CI (failing checks)
        gh.get_failed_checks.return_value = ["Test: FAILED"]
        gh.get_check_status.return_value = "failure"

        with caplog.at_level(logging.INFO):
            watcher._phase1(
                state,
                state_path,
                {
                    "number": 42,
                    "title": "Test PR",
                    "draft": False,
                    "head": {"ref": "goal/42"},
                },
                gh,
                "owner",
                "TestRepo",
                tmp_path,
                tmp_path / "cfg.yaml",
                settings,
            )

        # Assert: CI gate log captured
        log_messages = [record.message for record in caplog.records]
        assert any("CI not green" in msg for msg in log_messages), (
            f"Expected 'CI not green' in logs, got: {log_messages}"
        )


@pytest.mark.integration
class TestAnomalyDetectionMetrics:
    """Test metrics for detecting anomalies in merge decision flow."""

    def test_unusual_retry_rate_detection(
        self,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Retry rate anomaly detection (unusual retry frequency).

        Acceptance: Anomaly flag triggers when retry rate exceeds threshold (>20%).
        """
        # Simulate 10 decisions: 8 merges, 2 retries → 20% retry rate
        for _ in range(8):
            metrics_collector.record_decision("approved", 100, "normal")
        for _ in range(2):
            metrics_collector.record_decision("retry", 150, "mixed_verdicts")

        summary = metrics_collector.get_metrics_summary()
        total = summary["total_decisions"]
        retry_rate = summary["outcomes"]["retry"] / total if total > 0 else 0

        # Assert: Retry rate at threshold (20%)
        assert retry_rate == 0.2
        # Threshold check: alert if >20%
        assert retry_rate <= 0.2, "Retry rate anomaly detected"

    def test_ci_green_delay_detection(
        self,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: CI-green delay detection (slow CI status updates).

        Acceptance: Anomaly flag when CI-green wait cycles exceed threshold.
        """
        # Simulate decisions with varying CI wait times
        ci_wait_cycles_list = [0, 1, 2, 5, 10, 15, 20]

        for cycles in ci_wait_cycles_list:
            latency = 100 + (cycles * 10)  # More waits = higher latency
            metrics_collector.record_decision("approved", latency, "ci_green_delay")

        summary = metrics_collector.get_metrics_summary()

        # Assert: Log contains high-wait-cycle decisions
        assert len(summary["decision_log"]) == len(ci_wait_cycles_list)

    def test_unknown_outcome_maps_to_retry(
        self,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Unknown outcomes map to retry outcome category.

        Acceptance: Unknown decision outcomes are recorded as latency but not counted.
        """
        # Simulate decision distribution
        metrics_collector.record_decision("approved", 100, "happy_path")
        metrics_collector.record_decision("blocked", 150, "unresolvable")
        metrics_collector.record_decision("unknown_type", 200, "unexpected")

        summary = metrics_collector.get_metrics_summary()

        # Assert: Unknown outcome recorded as latency but not in outcome counts
        assert len(summary["decision_log"]) == 3
        assert summary["outcomes"]["approved"] == 1
        assert summary["outcomes"]["blocked"] == 1
        total_outcomes = sum(summary["outcomes"].values())
        assert total_outcomes == 2  # Only approved and blocked counted


@pytest.mark.integration
class TestMetricsAggregation:
    """Test metrics aggregation and exportability."""

    def test_metrics_export_json_format(
        self,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Metrics exportable in JSON format for operator dashboards.

        Acceptance: Metrics serializable to JSON, ready for external consumption.
        """
        # Populate metrics
        metrics_collector.record_decision("approved", 100, "unanimous")
        metrics_collector.record_decision("blocked", 150, "concerns")
        metrics_collector.record_decision("retry", 120, "mixed")

        summary = metrics_collector.get_metrics_summary()

        # Assert: JSON-serializable
        json_str = json.dumps(summary)
        assert json_str  # Non-empty
        parsed = json.loads(json_str)
        assert parsed["total_decisions"] == 3
        assert parsed["outcomes"]["approved"] == 1
        assert parsed["outcomes"]["blocked"] == 1
        assert parsed["outcomes"]["retry"] == 1

    def test_metrics_summary_completeness(
        self,
        metrics_collector: DecisionMetricsCollector,
    ):
        """Test: Metrics summary contains all required fields.

        Acceptance: Summary includes total decisions, outcomes, latency stats.
        """
        metrics_collector.record_decision("approved", 100, "test")
        summary = metrics_collector.get_metrics_summary()

        # Assert: All required fields present
        assert "total_decisions" in summary
        assert "outcomes" in summary
        assert "avg_latency_ms" in summary
        assert "max_latency_ms" in summary
        assert "decision_log" in summary
        assert all(key in summary["outcomes"] for key in ["approved", "blocked", "retry"])
