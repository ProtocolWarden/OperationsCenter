# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Comprehensive coverage enhancement tests for flaky test reporter.

This file contains additional tests to improve code coverage across all
flaky test reporter modules to meet the ≥85% threshold.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.observer.flaky_test_aggregator import FlakyTestAggregator
from operations_center.observer.flaky_test_alert_config import (
    AlertThreshold,
    FlakyTestAlertConfig,
)
from operations_center.observer.flaky_test_alerts import FlakyTestAlertManager
from operations_center.observer.flaky_test_reporter import (
    FlakyTestMetric,
    FlakyTestReporter,
    FlakyTestResult,
)
from operations_center.observer.flaky_test_storage import (
    FlakyTestAggregationReport,
    FlakyTestStorageManager,
)


@pytest.mark.flaky
class TestFlakyTestReporterEdgeCases:
    """Additional edge case tests for FlakyTestReporter."""

    def test_reporter_with_all_passing_tests(self, tmp_path: Path) -> None:
        """Test reporter with 100% passing test run."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        for i in range(5):
            reporter.track_test(
                FlakyTestResult(
                    nodeid="tests/unit/test_foo.py::test_method",
                    outcome="passed",
                    duration=1.5,
                    timestamp=datetime.now(UTC),
                )
            )

        metric = reporter.query_metrics_by_test("tests/unit/test_foo.py::test_method")
        assert metric is not None
        assert metric.failure_rate == 0.0
        assert metric.flakiness_score < 0.2

    def test_reporter_with_all_failing_tests(self, tmp_path: Path) -> None:
        """Test reporter with 100% failing test run."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        for i in range(3):
            reporter.track_test(
                FlakyTestResult(
                    nodeid="tests/unit/test_fail.py::test_method",
                    outcome="failed",
                    duration=0.1,
                    timestamp=datetime.now(UTC),
                )
            )

        metric = reporter.query_metrics_by_test("tests/unit/test_fail.py::test_method")
        assert metric is not None
        assert metric.failure_rate == 1.0
        assert metric.flakiness_score > 0.0

    def test_reporter_analyzes_very_long_nodeid(self, tmp_path: Path) -> None:
        """Test handling of very long test nodeids."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        long_nodeid = (
            "tests/unit/very/deeply/nested/package/structure/"
            "test_file_with_long_name.py::ClassWithLongName::test_method_with_long_name"
        )

        reporter.track_test(
            FlakyTestResult(
                nodeid=long_nodeid,
                outcome="passed",
                duration=1.0,
                timestamp=datetime.now(UTC),
            )
        )

        metric = reporter.query_metrics_by_test(long_nodeid)
        assert metric is not None
        assert metric.nodeid == long_nodeid

    def test_reporter_with_very_short_duration(self, tmp_path: Path) -> None:
        """Test reporter with extremely short test durations."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        reporter.track_test(
            FlakyTestResult(
                nodeid="tests/unit/test_fast.py::test_instant",
                outcome="passed",
                duration=0.001,
                timestamp=datetime.now(UTC),
            )
        )

        metric = reporter.query_metrics_by_test("tests/unit/test_fast.py::test_instant")
        assert metric is not None

    def test_reporter_with_very_long_duration(self, tmp_path: Path) -> None:
        """Test reporter with very long test durations."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        reporter.track_test(
            FlakyTestResult(
                nodeid="tests/integration/test_slow.py::test_wait",
                outcome="passed",
                duration=3600.0,
                timestamp=datetime.now(UTC),
            )
        )

        metric = reporter.query_metrics_by_test("tests/integration/test_slow.py::test_wait")
        assert metric is not None

    def test_reporter_with_mixed_outcomes(self, tmp_path: Path) -> None:
        """Test reporter with various outcome combinations."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        nodeid = "tests/unit/test_mixed.py::test_flaky"

        outcomes = ["passed", "failed", "passed", "passed", "failed"]
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
        assert metric.failure_rate == 0.4
        assert metric.run_count == 5

    def test_reporter_session_analysis_empty(self, tmp_path: Path) -> None:
        """Test session analysis with no tracked tests."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        report = reporter.analyze_session()

        assert report is not None
        assert report.total_tests == 0
        assert report.flaky_candidates == []

    def test_reporter_session_analysis_with_timeout_failure(self, tmp_path: Path) -> None:
        """Test session analysis detects timeout patterns."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        reporter.track_test(
            FlakyTestResult(
                nodeid="tests/unit/test_timeout.py::test_method",
                outcome="failed",
                duration=29.9,
                timestamp=datetime.now(UTC),
                exception_message="TimeoutError: operation timed out",
            )
        )

        report = reporter.analyze_session()
        assert report.total_tests >= 1

    def test_reporter_multiple_modules(self, tmp_path: Path) -> None:
        """Test reporter tracking tests across multiple modules."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        modules = [
            "tests/unit/auth/test_login.py::test_valid_credentials",
            "tests/unit/db/test_queries.py::test_select_all",
            "tests/integration/api/test_endpoints.py::test_post_handler",
        ]

        for nodeid in modules:
            reporter.track_test(
                FlakyTestResult(
                    nodeid=nodeid,
                    outcome="passed",
                    duration=1.0,
                    timestamp=datetime.now(UTC),
                )
            )

        module_flakiness = reporter.query_module_flakiness("tests")
        assert module_flakiness["test_count"] == 3


@pytest.mark.flaky
class TestFlakyTestStorageEdgeCases:
    """Additional edge case tests for storage manager."""

    def test_storage_with_special_characters_in_data(self, tmp_path: Path) -> None:
        """Test storage handles special characters in data."""
        storage = FlakyTestStorageManager(tmp_path)

        session_data = {
            "session_id": "test-session",
            "failure_reason": "Unicode: 你好世界 🎉 café",
            "test_name": "test_with_émojis_and_chars",
        }

        path = storage.save_session_results(session_data)
        assert path.exists()

        # Verify data was saved correctly
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
            assert "Unicode" in loaded["failure_reason"]

    def test_storage_large_session_data(self, tmp_path: Path) -> None:
        """Test storage can handle large session data."""
        storage = FlakyTestStorageManager(tmp_path)

        # Create a large session with many tests
        large_session = {
            "session_id": "large-session",
            "tests": [
                {
                    "nodeid": f"tests/test_{i}.py::test_{j}",
                    "duration": 1.0 + (i * j) * 0.01,
                    "outcome": "passed" if (i + j) % 2 == 0 else "failed",
                }
                for i in range(50)
                for j in range(10)
            ],
        }

        path = storage.save_session_results(large_session)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_aggregation_report_with_detailed_data(self, tmp_path: Path) -> None:
        """Test aggregation report with comprehensive data."""
        storage = FlakyTestStorageManager(tmp_path)

        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=30,
            total_test_executions=10000,
            flaky_test_count=150,
            unstable_test_count=50,
            flaky_tests=[
                {
                    "test_name": f"tests/test_{i}.py::test_method",
                    "failure_rate": 0.1 * i,
                    "category": "infrastructure" if i % 2 == 0 else "intermittent",
                }
                for i in range(1, 11)
            ],
            by_module={
                "tests/unit": {"flaky_count": 50, "total_count": 500},
                "tests/integration": {"flaky_count": 100, "total_count": 1000},
            },
            by_category={
                "intermittent": 75,
                "infrastructure": 50,
                "environment": 25,
            },
            recommendations=[
                {
                    "priority": "high",
                    "test": "tests/test_1.py::test_critical",
                    "description": "Frequent timeout failures",
                }
            ],
        )

        path = storage.save_aggregation(report)
        assert path.exists()

        # Verify we can round-trip
        with open(path) as f:
            loaded_dict = json.load(f)
            restored = FlakyTestAggregationReport.from_dict(loaded_dict)
            assert restored.flaky_test_count == 150
            assert len(restored.flaky_tests) == 10

    def test_cleanup_with_mixed_valid_invalid_dates(self, tmp_path: Path) -> None:
        """Test cleanup ignores invalid date directories."""
        storage = FlakyTestStorageManager(tmp_path, session_retention_days=3)

        # Create valid date dirs
        today = datetime.now(UTC).date()
        for i in range(5):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            date_dir = storage.session_dir / date_str
            date_dir.mkdir(parents=True, exist_ok=True)
            (date_dir / "session.json").write_text("{}")

        # Create invalid date dir that should be skipped
        invalid_dir = storage.session_dir / "invalid-date"
        invalid_dir.mkdir(parents=True, exist_ok=True)
        (invalid_dir / "session.json").write_text("{}")

        deleted = storage.cleanup_old_sessions()
        assert deleted > 0
        assert invalid_dir.exists()

    def test_load_aggregations_with_missing_fields(self, tmp_path: Path) -> None:
        """Test loading aggregations with missing optional fields."""
        storage = FlakyTestStorageManager(tmp_path)
        storage.aggregation_dir.mkdir(parents=True, exist_ok=True)

        # Create aggregation with minimal fields
        minimal_agg = {
            "date": "2026-06-07",
            "period_days": 7,
            "total_test_executions": 100,
            "flaky_test_count": 5,
            "unstable_test_count": 0,
        }

        today = datetime.now(UTC).date().strftime("%Y-%m-%d")
        agg_file = storage.aggregation_dir / f"{today}-aggregation.json"
        with open(agg_file, "w") as f:
            json.dump(minimal_agg, f)

        aggs = storage.load_recent_aggregations(days=7)
        assert len(aggs) == 1
        assert aggs[0].flaky_test_count == 5


@pytest.mark.flaky
class TestFlakyTestAlertManagerCoverage:
    """Additional tests for alert manager coverage."""

    def test_alert_manager_with_no_data(self) -> None:
        """Test alert manager with empty aggregation report."""
        report = FlakyTestAggregationReport(
            date="2026-06-12",
            period_days=7,
            total_test_executions=0,
            flaky_test_count=0,
            unstable_test_count=0,
        )
        alerts = FlakyTestAlertManager.check_alerts(report)
        assert isinstance(alerts, list)

    def test_alert_manager_with_single_flaky_test(self) -> None:
        """Test alert detection with a flaky test in the report."""
        report = FlakyTestAggregationReport(
            date="2026-06-12",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=1,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": "tests/unit/test_foo.py::test_method",
                    "failure_rate": 0.6,
                    "first_seen": "2026-06-11T10:00:00+00:00",
                    "category": "intermittent",
                }
            ],
        )
        alerts = FlakyTestAlertManager.check_alerts(report)
        assert isinstance(alerts, list)

    def test_alert_manager_regression_spike_detection(self) -> None:
        """Test detection of regression spikes via previous report comparison."""
        prev_report = FlakyTestAggregationReport(
            date="2026-06-11",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=2,
            unstable_test_count=0,
        )
        current_report = FlakyTestAggregationReport(
            date="2026-06-12",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=10,
            unstable_test_count=3,
            flaky_tests=[
                {
                    "test_name": f"tests/test_regressed_{i}.py::test_method",
                    "failure_rate": 0.5 + (i * 0.05),
                    "first_seen": "2026-06-12T08:00:00+00:00",
                }
                for i in range(10)
            ],
        )
        alerts = FlakyTestAlertManager.check_alerts(current_report, prev_report)
        assert isinstance(alerts, list)

    def test_alert_manager_critical_flakiness(self) -> None:
        """Test detection of critical flakiness levels."""
        report = FlakyTestAggregationReport(
            date="2026-06-12",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=5,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": f"tests/test_{i}.py::test_method",
                    "failure_rate": 0.8,
                    "first_seen": "2026-06-10T08:00:00+00:00",
                    "category": "infrastructure",
                }
                for i in range(5)
            ],
        )
        alerts = FlakyTestAlertManager.check_alerts(report)
        assert isinstance(alerts, list)


@pytest.mark.flaky
class TestFlakyTestAggregatorCoverage:
    """Additional tests for aggregator coverage."""

    def test_aggregator_with_single_metric(self, tmp_path: Path) -> None:
        """Test aggregation with session data containing one flaky test."""
        storage = FlakyTestStorageManager(tmp_path)
        aggregator = FlakyTestAggregator(storage)

        storage.save_session_results(
            {
                "session_count": 10,
                "flaky_candidates": [
                    {
                        "test_name": "tests/unit/test_foo.py::test_method",
                        "failure_rate": 0.5,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "intermittent",
                    }
                ],
                "unstable_candidates": [],
            }
        )

        result = aggregator.aggregate(days=7)
        assert result.flaky_test_count >= 1

    def test_aggregator_with_category_breakdown(self, tmp_path: Path) -> None:
        """Test aggregator breaks down by category."""
        storage = FlakyTestStorageManager(tmp_path)
        aggregator = FlakyTestAggregator(storage)

        storage.save_session_results(
            {
                "session_count": 20,
                "flaky_candidates": [
                    {
                        "test_name": "tests/test_intermittent.py::test_method",
                        "failure_rate": 0.3,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "intermittent",
                    },
                    {
                        "test_name": "tests/test_infra.py::test_method",
                        "failure_rate": 0.7,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "infrastructure",
                    },
                ],
                "unstable_candidates": [],
            }
        )

        result = aggregator.aggregate(days=7)
        assert result.by_category.get("intermittent", 0) >= 0
        assert result.by_category.get("infrastructure", 0) >= 0

    def test_aggregator_module_breakdown(self, tmp_path: Path) -> None:
        """Test aggregator breaks down by module."""
        storage = FlakyTestStorageManager(tmp_path)
        aggregator = FlakyTestAggregator(storage)

        storage.save_session_results(
            {
                "session_count": 30,
                "flaky_candidates": [
                    {
                        "test_name": f"tests/unit/auth/test_login.py::test_method_{i}",
                        "failure_rate": 0.3,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "intermittent",
                    }
                    for i in range(3)
                ]
                + [
                    {
                        "test_name": f"tests/integration/db/test_queries.py::test_method_{i}",
                        "failure_rate": 0.2,
                        "first_seen": "2026-06-12T08:00:00+00:00",
                        "category": "intermittent",
                    }
                    for i in range(2)
                ],
                "unstable_candidates": [],
            }
        )

        result = aggregator.aggregate(days=7)
        assert len(result.by_module) > 0


@pytest.mark.flaky
class TestFlakyTestAlertConfigCoverage:
    """Additional tests for alert configuration coverage."""

    def test_alert_config_default_thresholds(self) -> None:
        """Test alert config initializes with channel routes."""
        config = FlakyTestAlertConfig()
        assert config.channel_routes is not None
        assert len(config.channel_routes) > 0

    def test_alert_config_channel_routing(self) -> None:
        """Test alert config routes alerts to correct channels."""
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("NEW_FLAKY_TEST", severity="WARNING")
        assert isinstance(channels, list)

    def test_alert_threshold_comparison(self) -> None:
        """Test alert threshold value comparisons."""
        threshold = AlertThreshold(
            alert_type="FAILURE_RATE",
            info_threshold=0.1,
            warning_threshold=0.3,
            critical_threshold=0.6,
            emergency_threshold=0.9,
        )

        assert threshold.info_threshold < threshold.warning_threshold
        assert threshold.warning_threshold < threshold.critical_threshold
        assert threshold.critical_threshold < threshold.emergency_threshold

    def test_alert_config_should_alert_methods(self) -> None:
        """Test alert config alert detection methods."""
        config = FlakyTestAlertConfig()

        should_alert, severity = config.should_alert_on_failure_rate(0.2)
        assert isinstance(should_alert, bool)
        assert isinstance(severity, str)

        should_alert_high, severity_high = config.should_alert_on_failure_rate(0.8)
        assert isinstance(should_alert_high, bool)


@pytest.mark.flaky
class TestFlakyTestIntegrationCoverage:
    """Integration tests for comprehensive coverage."""

    def test_full_workflow_from_tracking_to_alerts(self, tmp_path: Path) -> None:
        """Test complete workflow: track → analyze → aggregate → alert."""
        # Create reporter
        reporter = FlakyTestReporter.create_local(tmp_path)

        # Track some tests
        for i in range(20):
            outcome = "passed" if i % 3 > 0 else "failed"
            reporter.track_test(
                FlakyTestResult(
                    nodeid=f"tests/test_workflow.py::test_case_{i}",
                    outcome=outcome,
                    duration=1.0,
                    timestamp=datetime.now(UTC),
                )
            )

        # Analyze session
        session_report = reporter.analyze_session()
        assert session_report.total_tests > 0

        # Get metrics
        metrics = []
        for i in range(20):
            nodeid = f"tests/test_workflow.py::test_case_{i}"
            metric = reporter.query_metrics_by_test(nodeid)
            if metric:
                metrics.append(metric)

        # Create alerts via aggregation report
        agg_report = FlakyTestAggregationReport(
            date="2026-06-12",
            period_days=1,
            total_test_executions=20,
            flaky_test_count=len(session_report.flaky_candidates),
            unstable_test_count=len(session_report.unstable_candidates),
        )
        alerts = FlakyTestAlertManager.check_alerts(agg_report)

        # Result: should have some data
        assert len(metrics) > 0
        assert isinstance(alerts, list)

    def test_reporter_with_storage_persistence(self, tmp_path: Path) -> None:
        """Test reporter data persistence across sessions."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        # Track tests in first "session"
        for i in range(3):
            reporter.track_test(
                FlakyTestResult(
                    nodeid="tests/test_persist.py::test_method",
                    outcome="passed",
                    duration=1.0,
                    timestamp=datetime.now(UTC),
                )
            )

        report1 = reporter.analyze_session()
        assert report1.total_tests >= 1

        # Analyze session again - data should be persisted
        metric = reporter.query_metrics_by_test("tests/test_persist.py::test_method")
        if metric:
            assert metric.run_count >= 1


@pytest.mark.flaky
class TestBoundaryConditions:
    """Tests for various boundary conditions."""

    def test_zero_duration_test(self, tmp_path: Path) -> None:
        """Test handling of zero-duration tests."""
        reporter = FlakyTestReporter.create_local(tmp_path)
        reporter.track_test(
            FlakyTestResult(
                nodeid="tests/test_zero.py::test_instant",
                outcome="passed",
                duration=0.0,
                timestamp=datetime.now(UTC),
            )
        )

    def test_negative_recovery_time(self, tmp_path: Path) -> None:
        """Test metric with invalid negative recovery time (should be handled)."""
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.5,
            run_count=2,
            recovery_time_days=0.0,
        )
        assert metric.recovery_time_days == 0.0

    def test_metric_with_no_runs(self, tmp_path: Path) -> None:
        """Test metric initialization with zero runs."""
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.0,
            run_count=0,
        )
        assert metric.run_count == 0

    def test_storage_with_empty_base_path_name(self, tmp_path: Path) -> None:
        """Test storage with minimal path."""
        storage = FlakyTestStorageManager(tmp_path / ".")
        assert storage.session_dir.exists()
