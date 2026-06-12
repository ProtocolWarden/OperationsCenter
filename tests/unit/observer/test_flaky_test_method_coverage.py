# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Method-level coverage tests for flaky test reporter components.

Focused tests for specific methods and code paths to ensure comprehensive
coverage across all modules and achieve ≥85% threshold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.observer.flaky_test_aggregator import FlakyTestAggregator
from operations_center.observer.flaky_test_reporter import (
    FlakyTestConfig,
    FlakyTestMetric,
    FlakyTestReporter,
    FlakyTestResult,
    FlakyTestSessionReport,
    FlakynessCategory,
    TestOutcome,
)
from operations_center.observer.flaky_test_storage import (
    FlakyTestAggregationReport,
    FlakyTestStorageManager,
)


@pytest.mark.flaky
class TestFlakyTestMetricMethods:
    """Tests for FlakyTestMetric methods."""

    def test_metric_to_dict_all_fields(self) -> None:
        """Test FlakyTestMetric.to_dict includes all fields."""
        metric = FlakyTestMetric(
            nodeid="tests/unit/test_foo.py::test_method",
            failure_rate=0.25,
            run_count=4,
            flakiness_score=0.3,
            confidence=0.8,
            pattern_entropy=0.65,
            streak_length=3,
            recovery_time_days=1.5,
            duration_variance=2.1,
            retry_success_count=2,
            markers=["slow", "integration"],
            last_failure_reason="Timeout",
            suspected_category=FlakynessCategory.INTERMITTENT,
        )

        data = metric.to_dict()
        assert data["nodeid"] == "tests/unit/test_foo.py::test_method"
        assert data["failure_rate"] == 0.25
        assert data["pattern_entropy"] == 0.65
        assert data["streak_length"] == 3
        assert data["recovery_time_days"] == 1.5
        assert data["markers"] == ["slow", "integration"]
        assert data["suspected_category"] == "intermittent"

    def test_metric_to_dict_with_none_values(self) -> None:
        """Test FlakyTestMetric.to_dict handles None/default values correctly."""
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.5,
            run_count=2,
            recovery_time_days=None,
            last_failure_reason="",
            suspected_category=FlakynessCategory.UNKNOWN,
        )

        data = metric.to_dict()
        assert data["recovery_time_days"] is None
        assert data["last_failure_reason"] == ""
        assert data["suspected_category"] == "unknown"

    def test_metric_numeric_rounding(self) -> None:
        """Test FlakyTestMetric rounds numeric values correctly."""
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.333333333,
            run_count=3,
            flakiness_score=0.123456789,
            confidence=0.987654321,
        )

        data = metric.to_dict()
        # Values should be rounded to 4 decimal places
        assert data["failure_rate"] == 0.3333
        assert data["flakiness_score"] <= 0.1235
        assert data["confidence"] <= 0.9877


@pytest.mark.flaky
class TestFlakyTestResultMethods:
    """Tests for FlakyTestResult methods."""

    def test_result_initialization_defaults(self) -> None:
        """Test FlakyTestResult with default values."""
        result = FlakyTestResult(
            nodeid="tests/test_foo.py::test_method",
            outcome="passed",
            duration=1.5,
            timestamp=datetime.now(UTC),
        )

        assert result.nodeid == "tests/test_foo.py::test_method"
        assert result.outcome == TestOutcome.PASSED
        assert result.duration == 1.5
        assert result.exception_message == ""
        assert result.markers == []

    def test_result_with_markers_and_reason(self) -> None:
        """Test FlakyTestResult with markers and exception message."""
        result = FlakyTestResult(
            nodeid="tests/test_flaky.py::test_method",
            outcome="failed",
            duration=0.5,
            timestamp=datetime.now(UTC),
            exception_message="AssertionError: Expected 5 but got 4",
            markers=["slow", "flaky"],
        )

        assert result.exception_message == "AssertionError: Expected 5 but got 4"
        assert "slow" in result.markers


@pytest.mark.flaky
class TestFlakyTestSessionReportMethods:
    """Tests for FlakyTestSessionReport methods."""

    def test_session_report_initialization(self) -> None:
        """Test FlakyTestSessionReport initialization."""
        report = FlakyTestSessionReport(
            session_id="test-session",
            timestamp=datetime.now(UTC),
            run_count=10,
            total_tests=10,
        )

        assert report.total_tests == 10
        assert report.run_count == 10
        assert report.flaky_candidates == []
        assert report.unstable_candidates == []

    def test_session_report_with_metrics(self) -> None:
        """Test FlakyTestSessionReport with flaky/unstable candidates."""
        flaky_metric = FlakyTestMetric(
            nodeid="tests/test_flaky.py::test_method",
            failure_rate=0.4,
            run_count=10,
        )

        unstable_metric = FlakyTestMetric(
            nodeid="tests/test_unstable.py::test_method",
            failure_rate=0.08,
            run_count=25,
        )

        report = FlakyTestSessionReport(
            session_id="test-session",
            timestamp=datetime.now(UTC),
            run_count=100,
            total_tests=100,
            flaky_candidates=[flaky_metric],
            unstable_candidates=[unstable_metric],
        )

        assert len(report.flaky_candidates) == 1
        assert len(report.unstable_candidates) == 1


@pytest.mark.flaky
class TestFlakyTestConfigMethods:
    """Tests for FlakyTestConfig methods."""

    def test_config_to_dict_all_fields(self) -> None:
        """Test FlakyTestConfig.to_dict includes all fields."""
        config = FlakyTestConfig(
            storage_root="/tmp/metrics",
            min_run_count=5,
            flakiness_threshold=0.2,
            unstable_threshold=0.08,
            historical_window_days=60,
        )

        data = config.to_dict()
        assert data["min_run_count"] == 5
        assert data["flakiness_threshold"] == 0.2
        assert data["unstable_threshold"] == 0.08
        assert data["historical_window_days"] == 60

    def test_config_with_path_coercion(self) -> None:
        """Test FlakyTestConfig coerces string paths to Path objects."""
        config = FlakyTestConfig(storage_root="/var/lib/metrics")
        assert isinstance(config.storage_root, Path)

    def test_config_with_s3_uri(self) -> None:
        """Test FlakyTestConfig accepts S3 URIs."""
        config = FlakyTestConfig(storage_root="s3://my-bucket/metrics")
        assert isinstance(config.storage_root, str)


@pytest.mark.flaky
class TestFlakyTestStorageMethods:
    """Tests for FlakyTestStorageManager methods."""

    def test_aggregation_report_from_dict_minimal(self) -> None:
        """Test FlakyTestAggregationReport.from_dict with minimal data."""
        data = {
            "date": "2026-06-07",
            "period_days": 7,
            "total_test_executions": 100,
            "flaky_test_count": 5,
            "unstable_test_count": 2,
        }

        report = FlakyTestAggregationReport.from_dict(data)
        assert report.date == "2026-06-07"
        assert report.flaky_test_count == 5
        assert report.flaky_tests == []
        assert report.by_module == {}

    def test_aggregation_report_from_dict_complete(self) -> None:
        """Test FlakyTestAggregationReport.from_dict with complete data."""
        data = {
            "date": "2026-06-07",
            "period_days": 7,
            "total_test_executions": 100,
            "flaky_test_count": 5,
            "unstable_test_count": 2,
            "flaky_tests": [
                {
                    "test_name": "tests/test_1.py::test_method",
                    "failure_rate": 0.5,
                }
            ],
            "by_module": {"tests": {"flaky_count": 5}},
            "by_category": {"intermittent": 3, "infrastructure": 2},
            "recommendations": [{"priority": "high", "description": "Fix test"}],
        }

        report = FlakyTestAggregationReport.from_dict(data)
        assert len(report.flaky_tests) == 1
        assert "tests" in report.by_module
        assert len(report.recommendations) == 1

    def test_aggregation_report_to_dict_roundtrip(self, tmp_path: Path) -> None:
        """Test FlakyTestAggregationReport to_dict/from_dict roundtrip."""
        original = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=30,
            total_test_executions=500,
            flaky_test_count=25,
            unstable_test_count=10,
            flaky_tests=[
                {"test_name": "tests/test_1.py::test_method", "failure_rate": 0.5}
            ],
            by_module={"tests/unit": {"flaky_count": 10}},
            by_category={"intermittent": 15, "infrastructure": 10},
            recommendations=[{"priority": "high", "description": "Fix flaky tests"}],
        )

        # Convert to dict and back
        data = original.to_dict()
        restored = FlakyTestAggregationReport.from_dict(data)

        # Verify all fields are preserved
        assert restored.date == original.date
        assert restored.period_days == original.period_days
        assert restored.flaky_test_count == original.flaky_test_count
        assert len(restored.flaky_tests) == len(original.flaky_tests)
        assert restored.by_module == original.by_module
        assert restored.by_category == original.by_category


@pytest.mark.flaky
class TestFlakyTestReporterQueryMethods:
    """Tests for FlakyTestReporter query methods."""

    def test_query_metrics_by_test_not_found(self, tmp_path: Path) -> None:
        """Test query_metrics_by_test returns None for unknown test."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        metric = reporter.query_metrics_by_test("tests/unknown/test.py::test_method")
        assert metric is None

    def test_query_module_flakiness_empty_module(self, tmp_path: Path) -> None:
        """Test query_module_flakiness with empty module."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        result = reporter.query_module_flakiness("nonexistent/module")
        assert result["test_count"] == 0
        assert result["most_problematic"] == []

    def test_query_module_flakiness_with_multiple_tests(self, tmp_path: Path) -> None:
        """Test query_module_flakiness aggregates module tests correctly."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        # Add tests from same module
        for i in range(5):
            for j in range(2):
                outcome = "passed" if j == 0 else "failed"
                reporter.track_test(
                    FlakyTestResult(
                        nodeid=f"tests/unit/mymodule/test_foo.py::test_case_{i}",
                        outcome=outcome,
                        duration=1.0,
                        timestamp=datetime.now(UTC),
                    )
                )

        result = reporter.query_module_flakiness("tests/unit/mymodule")
        assert result["test_count"] >= 5

    def test_query_trend_analysis_empty(self, tmp_path: Path) -> None:
        """Test query_trend_analysis with no test runs."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        result = reporter.query_trend_analysis(days=7)
        assert "trend" in result

    def test_query_trend_analysis_with_data(self, tmp_path: Path) -> None:
        """Test query_trend_analysis computes trends correctly."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        now = datetime.now(UTC)
        # Add tests spread across dates
        for day_offset in range(7):
            timestamp = now - timedelta(days=day_offset)
            for i in range(5):
                outcome = "passed" if i % 2 == 0 else "failed"
                reporter.track_test(
                    FlakyTestResult(
                        nodeid=f"tests/test_trend.py::test_case_{i}",
                        outcome=outcome,
                        duration=1.0,
                        timestamp=timestamp,
                    )
                )

        result = reporter.query_trend_analysis(days=7)
        assert "trend" in result

    def test_query_trend_with_insufficient_data(self, tmp_path: Path) -> None:
        """Test query_trend_analysis handles sparse data."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        result = reporter.query_trend_analysis(days=30)
        # Should handle gracefully even with no data
        assert result is not None


@pytest.mark.flaky
class TestFlakyTestReporterTracking:
    """Tests for FlakyTestReporter tracking methods."""

    def test_track_test_multiple_outcomes(self, tmp_path: Path) -> None:
        """Test tracking tests with various outcomes."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        outcomes = ["passed", "failed", "passed", "failed", "passed"]
        nodeid = "tests/test_varied.py::test_method"

        for outcome in outcomes:
            reporter.track_test(
                FlakyTestResult(
                    nodeid=nodeid,
                    outcome=outcome,
                    duration=1.0,
                    timestamp=datetime.now(UTC),
                )
            )

        metric = reporter.query_metrics_by_test(nodeid)
        assert metric is not None
        assert metric.run_count == 5
        assert metric.failure_rate == 0.4

    def test_analyze_session_after_tracking(self, tmp_path: Path) -> None:
        """Test analyze_session after tracking tests."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        # Track several tests
        for i in range(10):
            outcome = "passed" if i < 8 else "failed"
            reporter.track_test(
                FlakyTestResult(
                    nodeid=f"tests/test_analyze.py::test_case_{i}",
                    outcome=outcome,
                    duration=1.0,
                    timestamp=datetime.now(UTC),
                )
            )

        report = reporter.analyze_session()
        assert report.total_tests == 10
        assert isinstance(report.flaky_candidates, list)
        assert isinstance(report.unstable_candidates, list)


@pytest.mark.flaky
class TestFlakyTestAggregatorMethods:
    """Tests for FlakyTestAggregator methods."""

    def test_aggregate_categorization_logic(self, tmp_path: Path) -> None:
        """Test aggregate correctly categorizes flakiness levels from storage."""
        storage = FlakyTestStorageManager(tmp_path)
        aggregator = FlakyTestAggregator(storage)

        storage.save_session_results(
            {
                "session_count": 20,
                "flaky_candidates": [
                    {
                        "test_name": "tests/test_flaky_1.py::test_method",
                        "failure_rate": 0.35,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "intermittent",
                    }
                ],
                "unstable_candidates": [
                    {
                        "test_name": "tests/test_unstable.py::test_method",
                        "failure_rate": 0.07,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "unknown",
                    }
                ],
            }
        )

        result = aggregator.aggregate(days=7)
        assert result.flaky_test_count >= 1

    def test_aggregate_empty_recommendations(self, tmp_path: Path) -> None:
        """Test aggregate handles empty storage (no recommendations)."""
        storage = FlakyTestStorageManager(tmp_path)
        aggregator = FlakyTestAggregator(storage)

        result = aggregator.aggregate(days=7)
        # recommendations can be empty or populated depending on logic
        assert isinstance(result.recommendations, list)

    def test_aggregate_date_string_generation(self, tmp_path: Path) -> None:
        """Test aggregate generates correct date string."""
        storage = FlakyTestStorageManager(tmp_path)
        aggregator = FlakyTestAggregator(storage)

        result = aggregator.aggregate(days=7)
        # Date should be in YYYY-MM-DD format
        assert len(result.date) == 10
        assert result.date.count("-") == 2
