# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for FlakyTestReporter — Tier 1-2 flakiness detection and analysis."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest  # noqa: F401

from operations_center.observer.flaky_test_reporter import (
    FlakyTestMetric,
    FlakyTestReporter,
    FlakyTestResult,
    FlakyTestSessionReport,
    FlakynessCategory,
    TestOutcome,
)


class TestFlakynessMetricDataclass:
    """Tests for FlakyTestMetric dataclass."""

    def test_metric_initialization(self) -> None:
        metric = FlakyTestMetric(
            nodeid="tests/unit/test_foo.py::TestClass::test_method",
            failure_rate=0.25,
            run_count=4,
            flakiness_score=0.3,
            confidence=0.8,
        )
        assert metric.nodeid == "tests/unit/test_foo.py::TestClass::test_method"
        assert metric.failure_rate == 0.25
        assert metric.run_count == 4

    def test_metric_to_dict_serialization(self) -> None:
        metric = FlakyTestMetric(
            nodeid="tests/unit/test_foo.py::TestClass::test_method",
            failure_rate=0.333333,
            run_count=3,
            duration_variance=0.01234,
            flakiness_score=0.35,
            confidence=0.6,
        )
        data = metric.to_dict()
        assert data["nodeid"] == "tests/unit/test_foo.py::TestClass::test_method"
        assert data["failure_rate"] == 0.3333
        assert data["run_count"] == 3
        assert data["duration_variance"] == 0.0123

    def test_metric_category_serialization(self) -> None:
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.5,
            run_count=2,
            suspected_category=FlakynessCategory.STRUCTURAL,
        )
        data = metric.to_dict()
        assert data["suspected_category"] == "structural"

    def test_metric_with_markers_and_reasons(self) -> None:
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.5,
            run_count=2,
            markers=["slow", "flaky"],
            last_failure_reason="TimeoutError: operation timed out",
        )
        data = metric.to_dict()
        assert data["markers"] == ["slow", "flaky"]
        assert data["last_failure_reason"] == "TimeoutError: operation timed out"

    def test_metric_recovery_time_serialization(self) -> None:
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.25,
            run_count=4,
            recovery_time_days=0.5,
        )
        data = metric.to_dict()
        assert data["recovery_time_days"] == 0.5

    def test_metric_recovery_time_none(self) -> None:
        metric = FlakyTestMetric(
            nodeid="test",
            failure_rate=0.25,
            run_count=4,
            recovery_time_days=None,
        )
        data = metric.to_dict()
        assert data["recovery_time_days"] is None


class TestTestResultDataclass:
    """Tests for FlakyTestResult dataclass."""

    def test_result_initialization(self) -> None:
        result = FlakyTestResult(
            nodeid="tests/unit/test_foo.py::TestClass::test_method",
            outcome="passed",
            duration=1.234,
        )
        assert result.nodeid == "tests/unit/test_foo.py::TestClass::test_method"
        assert result.outcome == TestOutcome.PASSED
        assert result.duration == 1.234

    def test_result_outcome_conversion(self) -> None:
        result = FlakyTestResult(
            nodeid="test",
            outcome="failed",
            duration=1.0,
        )
        assert result.outcome == TestOutcome.FAILED

    def test_result_auto_generates_run_id(self) -> None:
        result1 = FlakyTestResult(nodeid="test", outcome="passed", duration=1.0)
        result2 = FlakyTestResult(nodeid="test", outcome="passed", duration=1.0)
        assert result1.run_id
        assert result2.run_id
        assert result1.run_id != result2.run_id

    def test_result_with_exception_info(self) -> None:
        result = FlakyTestResult(
            nodeid="test",
            outcome="failed",
            duration=1.0,
            exception_type="TimeoutError",
            exception_message="Timed out waiting for event",
        )
        assert result.exception_type == "TimeoutError"
        assert result.exception_message == "Timed out waiting for event"

    def test_result_to_dict_serialization(self) -> None:
        now = datetime.now(UTC)
        result = FlakyTestResult(
            nodeid="test",
            outcome="passed",
            duration=1.234,
            markers=["slow"],
            timestamp=now,
        )
        data = result.to_dict()
        assert data["nodeid"] == "test"
        assert data["outcome"] == "passed"
        assert data["duration"] == 1.2340
        assert data["markers"] == ["slow"]


class TestSessionReportDataclass:
    """Tests for FlakyTestSessionReport dataclass."""

    def test_report_initialization(self) -> None:
        now = datetime.now(UTC)
        report = FlakyTestSessionReport(
            session_id="session-123",
            timestamp=now,
            run_count=1,
            total_tests=100,
        )
        assert report.session_id == "session-123"
        assert report.run_count == 1
        assert report.total_tests == 100

    def test_report_with_flaky_candidates(self) -> None:
        metric1 = FlakyTestMetric(
            nodeid="test1",
            failure_rate=0.5,
            run_count=2,
        )
        metric2 = FlakyTestMetric(
            nodeid="test2",
            failure_rate=0.15,
            run_count=2,
        )
        report = FlakyTestSessionReport(
            session_id="session",
            timestamp=datetime.now(UTC),
            run_count=1,
            total_tests=100,
            flaky_candidates=[metric1, metric2],
        )
        assert len(report.flaky_candidates) == 2

    def test_report_to_dict_counts(self) -> None:
        metric1 = FlakyTestMetric(nodeid="test1", failure_rate=0.5, run_count=2)
        metric2 = FlakyTestMetric(nodeid="test2", failure_rate=0.15, run_count=2)
        report = FlakyTestSessionReport(
            session_id="session",
            timestamp=datetime.now(UTC),
            run_count=1,
            total_tests=100,
            flaky_candidates=[metric1],
            unstable_candidates=[metric2],
        )
        data = report.to_dict()
        assert data["flaky_count"] == 1
        assert data["unstable_count"] == 1


class TestFlakyTestReporterInitialization:
    """Tests for FlakyTestReporter initialization and factory methods."""

    def test_create_local(self, tmp_path: Path) -> None:
        reporter = FlakyTestReporter.create_local(tmp_path)
        assert reporter.storage_root == tmp_path
        assert tmp_path.exists()

    def test_create_local_creates_directory(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "new" / "nested" / "dir"
        reporter = FlakyTestReporter.create_local(new_dir)
        assert new_dir.exists()
        assert reporter.storage_root == new_dir

    def test_create_s3_stub(self) -> None:
        reporter = FlakyTestReporter.create_s3("my-bucket", prefix="flaky-tests")
        assert "my-bucket" in str(reporter.storage_root)

    def test_create_http_stub(self) -> None:
        reporter = FlakyTestReporter.create_http("api.example.com", auth_token="token")
        assert "api.example.com" in str(reporter.storage_root)

    def test_default_initialization(self) -> None:
        reporter = FlakyTestReporter()
        assert reporter.test_runs == {}
        assert reporter.all_results == []
        assert reporter.session_id


class TestFlakynessScoreComputation:
    """Tests for flakiness score computation."""

    def test_score_all_passes(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        score = reporter._compute_flakiness_score(0.0, runs, 2)
        assert score == 0.0

    def test_score_all_failures(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
        ]
        score = reporter._compute_flakiness_score(1.0, runs, 2)
        assert 0.0 <= score <= 1.0
        assert score >= 0.5

    def test_score_mixed_results(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        score = reporter._compute_flakiness_score(1.0 / 3, runs, 3)
        assert 0.0 <= score <= 1.0

    def test_score_insufficient_runs(self) -> None:
        reporter = FlakyTestReporter()
        runs = [FlakyTestResult(nodeid="test", outcome="failed", duration=1.0)]
        score = reporter._compute_flakiness_score(1.0, runs, 1)
        assert score == 0.0


class TestPatternAnalysisMethods:
    """Tests for pattern analysis methods."""

    def test_pattern_variance_all_same(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        variance = reporter._compute_pattern_variance(runs)
        assert variance == 0.0

    def test_pattern_variance_alternating(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
        ]
        variance = reporter._compute_pattern_variance(runs)
        assert variance > 0.0
        assert variance <= 1.0

    def test_pattern_entropy_deterministic(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        entropy = reporter._compute_pattern_entropy(runs)
        assert entropy == 0.0

    def test_pattern_entropy_balanced(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
        ]
        entropy = reporter._compute_pattern_entropy(runs)
        expected = -math.log(0.5) * 0.5 * 2
        assert abs(entropy - expected) < 0.001

    def test_streak_length_all_same(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        streak = reporter._compute_streak_length(runs)
        assert streak == 3

    def test_streak_length_alternating(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        streak = reporter._compute_streak_length(runs)
        assert streak == 1

    def test_retry_success_counting(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        count = reporter._count_retry_successes(runs)
        assert count == 2

    def test_recovery_time_computation(self) -> None:
        reporter = FlakyTestReporter()
        base_time = datetime.now(UTC)
        runs = [
            FlakyTestResult(
                nodeid="test",
                outcome="failed",
                duration=1.0,
                timestamp=base_time,
            ),
            FlakyTestResult(
                nodeid="test",
                outcome="passed",
                duration=1.0,
                timestamp=base_time + timedelta(hours=1),
            ),
        ]
        recovery = reporter._compute_recovery_time(runs)
        assert recovery is not None
        assert abs(recovery - 1 / 24) < 0.001

    def test_recovery_time_never_recovered(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
        ]
        recovery = reporter._compute_recovery_time(runs)
        assert recovery is None


class TestFlakynessCategorizationMethods:
    """Tests for root cause categorization."""

    def test_categorize_transient_low_rate_high_variance(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=2.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        category = reporter._categorize_flakiness(1.0 / 3, runs)
        assert category == FlakynessCategory.TRANSIENT

    def test_categorize_structural_high_rate_consistent(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
        ]
        category = reporter._categorize_flakiness(1.0, runs)
        assert category == FlakynessCategory.STRUCTURAL

    def test_categorize_transient_with_timeout_marker(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(
                nodeid="test",
                outcome="failed",
                duration=1.0,
                markers=["timeout"],
            ),
        ]
        category = reporter._categorize_flakiness(0.5, runs)
        assert category in [FlakynessCategory.TRANSIENT, FlakynessCategory.UNKNOWN]

    def test_categorize_transient_with_timeout_exception(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(
                nodeid="test",
                outcome="failed",
                duration=1.0,
                exception_type="TimeoutError",
            ),
        ]
        category = reporter._categorize_flakiness(0.25, runs)
        assert category == FlakynessCategory.TRANSIENT


class TestTracking:
    """Tests for test result tracking."""

    def test_track_single_test(self) -> None:
        reporter = FlakyTestReporter()
        result = FlakyTestResult(nodeid="test1", outcome="passed", duration=1.0)
        reporter.track_test(result)
        assert "test1" in reporter.test_runs
        assert len(reporter.test_runs["test1"]) == 1
        assert len(reporter.all_results) == 1

    def test_track_multiple_tests(self) -> None:
        reporter = FlakyTestReporter()
        for i in range(3):
            result = FlakyTestResult(
                nodeid="test1", outcome="passed" if i % 2 == 0 else "failed", duration=1.0
            )
            reporter.track_test(result)
        assert len(reporter.test_runs["test1"]) == 3
        assert len(reporter.all_results) == 3

    def test_track_different_tests(self) -> None:
        reporter = FlakyTestReporter()
        for i in range(3):
            result = FlakyTestResult(nodeid=f"test{i}", outcome="passed", duration=1.0)
            reporter.track_test(result)
        assert len(reporter.test_runs) == 3
        assert len(reporter.all_results) == 3


class TestSessionAnalysis:
    """Tests for session-level analysis."""

    def test_analyze_empty_session(self) -> None:
        reporter = FlakyTestReporter()
        report = reporter.analyze_session()
        assert report.total_tests == 0
        assert len(report.flaky_candidates) == 0

    def test_analyze_stable_tests(self) -> None:
        reporter = FlakyTestReporter()
        for _ in range(5):
            result = FlakyTestResult(nodeid="test1", outcome="passed", duration=1.0)
            reporter.track_test(result)
        report = reporter.analyze_session()
        assert report.total_tests == 1
        assert len(report.flaky_candidates) == 0

    def test_analyze_flaky_test(self) -> None:
        reporter = FlakyTestReporter()
        outcomes = ["passed", "failed", "passed", "failed", "failed"]
        for outcome in outcomes:
            result = FlakyTestResult(nodeid="test1", outcome=outcome, duration=1.0)
            reporter.track_test(result)
        report = reporter.analyze_session()
        assert len(report.flaky_candidates) == 1
        assert report.flaky_candidates[0].failure_rate == 0.6

    def test_analyze_unstable_test(self) -> None:
        reporter = FlakyTestReporter()
        outcomes = ["passed", "failed", "passed", "passed", "passed"]
        for outcome in outcomes:
            result = FlakyTestResult(nodeid="test1", outcome=outcome, duration=1.0)
            reporter.track_test(result)
        report = reporter.analyze_session()
        if report.unstable_candidates:
            assert report.unstable_candidates[0].failure_rate == 0.2
        else:
            assert report.flaky_candidates[0].failure_rate == 0.2

    def test_analyze_multiple_tests(self) -> None:
        reporter = FlakyTestReporter()
        outcomes1 = ["passed", "failed", "passed", "failed", "failed"]
        outcomes2 = ["passed", "passed", "passed", "passed", "passed"]
        for outcome in outcomes1:
            reporter.track_test(FlakyTestResult(nodeid="flaky", outcome=outcome, duration=1.0))
        for outcome in outcomes2:
            reporter.track_test(FlakyTestResult(nodeid="stable", outcome=outcome, duration=1.0))
        report = reporter.analyze_session()
        assert len(report.flaky_candidates) == 1
        assert report.flaky_candidates[0].nodeid == "flaky"
        assert report.total_tests == 2

    def test_analyze_insufficient_runs(self) -> None:
        reporter = FlakyTestReporter()
        result = FlakyTestResult(nodeid="test1", outcome="passed", duration=1.0)
        reporter.track_test(result)
        report = reporter.analyze_session()
        assert len(report.flaky_candidates) == 0


class TestAnalyzeTestRuns:
    """Tests for _analyze_test_runs method."""

    def test_analyze_basic_metrics(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="failed", duration=2.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.5),
        ]
        metric = reporter._analyze_test_runs("test", runs)
        assert metric.failure_rate == 1.0 / 3
        assert metric.run_count == 3
        assert abs(metric.duration_mean - 4.5 / 3) < 0.001

    def test_analyze_confidence_capped_at_five(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0)
            for _ in range(10)
        ]
        metric = reporter._analyze_test_runs("test", runs)
        assert metric.confidence == 1.0

    def test_analyze_flakiness_score_computation(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(nodeid="test", outcome="failed", duration=1.0),
            FlakyTestResult(nodeid="test", outcome="passed", duration=1.0),
        ]
        metric = reporter._analyze_test_runs("test", runs)
        assert metric.flakiness_score > 0.0
        assert metric.flakiness_score <= 1.0

    def test_analyze_captures_last_failure_reason(self) -> None:
        reporter = FlakyTestReporter()
        runs = [
            FlakyTestResult(
                nodeid="test",
                outcome="passed",
                duration=1.0,
            ),
            FlakyTestResult(
                nodeid="test",
                outcome="failed",
                duration=1.0,
                exception_type="AssertionError",
                exception_message="Expected 5 but got 3",
            ),
        ]
        metric = reporter._analyze_test_runs("test", runs)
        assert "AssertionError" in metric.last_failure_reason


class TestStorageOperations:
    """Tests for saving results and reports."""

    def test_save_session_report_local(self, tmp_path: Path) -> None:
        reporter = FlakyTestReporter.create_local(tmp_path)
        metric = FlakyTestMetric(nodeid="test", failure_rate=0.5, run_count=2)
        report = FlakyTestSessionReport(
            session_id="session-123",
            timestamp=datetime.now(UTC),
            run_count=1,
            total_tests=100,
            flaky_candidates=[metric],
        )
        path = reporter.save_session_report(report)
        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["session"] == "session-123"
        assert data["flaky_count"] == 1

    def test_save_test_results_local(self, tmp_path: Path) -> None:
        reporter = FlakyTestReporter.create_local(tmp_path)
        result1 = FlakyTestResult(nodeid="test1", outcome="passed", duration=1.0)
        result2 = FlakyTestResult(nodeid="test2", outcome="failed", duration=2.0)
        reporter.track_test(result1)
        reporter.track_test(result2)
        path = reporter.save_test_results()
        assert path is not None
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        data1 = json.loads(lines[0])
        assert data1["nodeid"] == "test1"

    def test_save_to_s3_returns_none(self) -> None:
        reporter = FlakyTestReporter.create_s3("bucket")
        report = FlakyTestSessionReport(
            session_id="session",
            timestamp=datetime.now(UTC),
            run_count=1,
            total_tests=10,
        )
        path = reporter.save_session_report(report)
        assert path is None

    def test_save_to_http_returns_none(self) -> None:
        reporter = FlakyTestReporter.create_http("http://api.example.com")
        path = reporter.save_test_results()
        assert path is None


class TestIntegration:
    """Integration tests for the full workflow."""

    def test_full_workflow_detection(self, tmp_path: Path) -> None:
        reporter = FlakyTestReporter.create_local(tmp_path)

        outcomes_tests = {
            "stable": ["passed"] * 5,
            "flaky": ["passed", "failed", "passed", "failed", "failed"],
        }

        for test_name, outcomes in outcomes_tests.items():
            for outcome in outcomes:
                reporter.track_test(
                    FlakyTestResult(nodeid=test_name, outcome=outcome, duration=1.0)
                )

        report = reporter.analyze_session()

        assert report.total_tests == 2
        assert len(report.flaky_candidates) == 1
        assert report.flaky_candidates[0].nodeid == "flaky"

        saved_path = reporter.save_session_report(report)
        assert saved_path is not None
        assert saved_path.exists()

    def test_categorization_workflow(self, tmp_path: Path) -> None:
        reporter = FlakyTestReporter.create_local(tmp_path)

        outcomes = ["passed", "failed", "passed", "failed", "passed"]
        for outcome in outcomes:
            reporter.track_test(FlakyTestResult(nodeid="test", outcome=outcome, duration=1.0))

        report = reporter.analyze_session()
        assert len(report.flaky_candidates) == 1
        metric = report.flaky_candidates[0]
        assert metric.suspected_category in [
            FlakynessCategory.TRANSIENT,
            FlakynessCategory.UNKNOWN,
        ]
        assert metric.flakiness_score > 0.0
        assert metric.confidence > 0.0
