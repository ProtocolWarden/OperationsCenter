# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest fixtures for observer unit tests — metrics, reporters, and data factories."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pytest

from operations_center.observer.flaky_test_reporter import (
    FlakyTestReporter,
    FlakyTestResult,
    FlakynessCategory,
)
from operations_center.observer.flaky_test_models import FlakyTestMetric, FlakyTestSessionReport


@pytest.fixture
def flaky_test_reporter(tmp_path: Path) -> FlakyTestReporter:
    """Provide a FlakyTestReporter with local storage for testing.

    Scope: function

    Returns:
        FlakyTestReporter: Configured reporter instance with tmp_path storage

    Example:
        def test_something(flaky_test_reporter):
            result = flaky_test_reporter.analyze_session()
    """
    return FlakyTestReporter.create_local(tmp_path)


@pytest.fixture
def test_results_factory() -> Callable:
    """Factory to create FlakyTestResult objects with controlled properties.

    Scope: function

    Returns:
        Callable: Factory function that creates FlakyTestResult objects

    Example:
        def test_something(test_results_factory):
            result = test_results_factory(outcome="failed", duration=1.5)
            assert result.outcome == TestOutcome.FAILED
    """

    def _create(
        nodeid: str = "test::test_method",
        outcome: str = "passed",
        duration: float = 1.0,
        run_id: str | None = None,
        markers: list[str] | None = None,
        exception_type: str | None = None,
        exception_message: str | None = None,
    ) -> FlakyTestResult:
        return FlakyTestResult(
            nodeid=nodeid,
            outcome=outcome,
            duration=duration,
            run_id=run_id,
            markers=markers or [],
            exception_type=exception_type,
            exception_message=exception_message,
        )

    return _create


@pytest.fixture
def metric_factory() -> Callable:
    """Factory to create FlakyTestMetric objects with controlled properties.

    Scope: function

    Returns:
        Callable: Factory function that creates FlakyTestMetric objects

    Example:
        def test_something(metric_factory):
            metric = metric_factory(
                nodeid="test::test_foo",
                failure_rate=0.5,
                run_count=10
            )
            assert metric.failure_rate == 0.5
    """

    def _create(
        nodeid: str = "test::test_method",
        failure_rate: float = 0.0,
        run_count: int = 1,
        failure_entropy: float = 0.0,
        streak_variance: float = 0.0,
        recovery_time_days: float | None = None,
        duration_stability: float = 0.0,
        environment_correlation: float = 0.0,
        isolation_score: float = 1.0,
        flakiness_score: float = 0.0,
        confidence: float = 0.0,
        suspected_category: FlakynessCategory | None = None,
        markers: list[str] | None = None,
        last_failure_reason: str = "",
        **kwargs,
    ) -> FlakyTestMetric:
        return FlakyTestMetric(
            nodeid=nodeid,
            failure_rate=failure_rate,
            run_count=run_count,
            failure_entropy=failure_entropy,
            streak_variance=streak_variance,
            recovery_time_days=recovery_time_days,
            duration_stability=duration_stability,
            environment_correlation=environment_correlation,
            isolation_score=isolation_score,
            flakiness_score=flakiness_score,
            confidence=confidence,
            suspected_category=suspected_category,
            markers=markers or [],
            last_failure_reason=last_failure_reason,
            **kwargs,
        )

    return _create


@pytest.fixture
def flaky_test_session_report_factory(metric_factory: Callable) -> Callable:
    """Factory to create FlakyTestSessionReport objects.

    Scope: function

    Returns:
        Callable: Factory function that creates FlakyTestSessionReport objects

    Example:
        def test_something(flaky_test_session_report_factory):
            report = flaky_test_session_report_factory(
                total_tests=100,
                run_count=5
            )
            assert report.total_tests == 100
    """

    def _create(
        session_id: str = "session-123",
        run_count: int = 1,
        total_tests: int = 100,
        flaky_candidates: list[FlakyTestMetric] | None = None,
        unstable_candidates: list[FlakyTestMetric] | None = None,
    ) -> FlakyTestSessionReport:
        return FlakyTestSessionReport(
            session_id=session_id,
            timestamp=datetime.now(UTC),
            run_count=run_count,
            total_tests=total_tests,
            flaky_candidates=flaky_candidates or [],
            unstable_candidates=unstable_candidates or [],
        )

    return _create


@pytest.fixture
def per_test_metric_edge_cases() -> dict[str, dict]:
    """Pre-configured edge-case scenarios for per-test metrics.

    Scope: module

    Returns:
        dict: Mapping of metric names to scenario dictionaries

    Each metric maps to a dict of scenarios:
        {scenario_name: (param1, param2, ..., expected_value)}
    """
    return {
        "failure_rate": {
            "zero_runs": (0, 0, 0.0),
            "single_pass": (0, 1, 0.0),
            "single_fail": (1, 1, 1.0),
            "at_threshold": (1, 20, 0.05),
            "below_threshold": (1, 21, 0.0476),
            "above_threshold": (1, 19, 0.0526),
            "large_sample_high_rate": (9999, 10000, 0.9999),
            "large_sample_low_rate": (1, 10000, 0.0001),
            "midpoint": (1, 2, 0.5),
        },
        "failure_entropy": {
            "all_pass": (10, 0, 0.0),
            "all_fail": (0, 10, 0.0),
            "balanced": (5, 5, 1.0),
            "single_pass": (1, 0, 0.0),
            "single_fail": (0, 1, 0.0),
            "two_different": (1, 1, 1.0),
            "imbalanced_1_99": (1, 99, 0.081),
            "imbalanced_99_1": (99, 1, 0.081),
            "moderately_imbalanced": (10, 1, 0.469),
        },
    }


@pytest.fixture
def repository_metric_edge_cases() -> dict[str, dict]:
    """Pre-configured edge-case scenarios for repository-level metrics.

    Scope: module

    Returns:
        dict: Mapping of metric names to scenario dictionaries

    Each metric maps to a dict of scenarios:
        {scenario_name: (param1, param2, ..., expected_value)}
    """
    return {
        "flaky_test_percentage": {
            "no_tests": (0, 0, 0.0),
            "single_stable": (0, 1, 0.0),
            "single_flaky": (1, 1, 1.0),
            "at_threshold": (1, 20, 0.05),
            "at_threshold_percentage": (5, 100, 0.05),
            "large_repo_minimal_flaky": (1, 10000, 0.0001),
            "large_repo_half_flaky": (5000, 10000, 0.5),
        },
        "median_failure_rate": {
            "no_flaky": ([], 0.0),
            "single_flaky": ([0.1], 0.1),
            "two_flaky": ([0.1, 0.2], 0.15),
            "three_flaky": ([0.1, 0.2, 0.3], 0.2),
            "all_same": ([0.05, 0.05, 0.05, 0.05, 0.05], 0.05),
            "skewed": ([0.01, 0.5, 0.99], 0.5),
        },
        "flaky_growth_rate": {
            "first_detection": (0, 0, 0.0),
            "first_flaky": (0, 1, float("inf")),
            "no_change": (10, 10, 0.0),
            "stable": (10, 10, 0.0),
            "at_threshold": (10, 12, 0.2),
            "improvement": (10, 8, -0.2),
            "complete_recovery": (10, 0, -1.0),
            "doubling": (5, 10, 1.0),
        },
        "category_concentration": {
            "no_tests": ({}, 0.0),
            "single_category": ({"intermittent": 1}, 1.0),
            "equal_split": ({"intermittent": 1, "env": 1, "infra": 1, "unknown": 1}, 0.25),
            "at_threshold": ({"intermittent": 6, "env": 4}, 0.6),
            "heavily_concentrated": ({"intermittent": 1000, "env": 1}, 0.999),
        },
        "critical_test_flakiness_ratio": {
            "no_critical_tests": (0, 0, 0.0),
            "single_stable": (0, 1, 0.0),
            "single_flaky": (1, 1, 1.0),
            "at_threshold": (1, 10, 0.1),
            "below_threshold": (1, 11, 0.0909),
            "above_threshold": (1, 9, 0.1111),
            "large_batch": (10, 100, 0.1),
        },
        "flaky_velocity": {
            "no_new_tests": (0, 7, 0.0),
            "one_per_week": (1, 7, 0.1429),
            "at_threshold": (7, 7, 1.0),
            "above_threshold": (8, 7, 1.1429),
            "one_per_day": (1, 1, 1.0),
            "outbreak": (10, 2, 5.0),
        },
        "repository_health_score": {
            "perfect": (0.0, 0.0, 0.0, 0.0, 1.0),
            "with_flakiness": (0.05, 0.0, 0.0, 0.0, 0.5),
            "at_limit": (0.10, 0.0, 0.0, 0.0, 0.0),
            "with_growth": (0.05, 0.2, 0.0, 0.0, 0.4),
            "with_critical": (0.05, 0.0, 0.1, 0.0, 0.3),
            "with_unknown": (0.05, 0.0, 0.0, 0.5, 0.35),
            "all_issues": (0.20, 0.5, 0.2, 1.0, 0.0),
        },
    }
