# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest fixtures for observer unit tests — metrics, reporters, and data factories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

import pytest

from operations_center.observer.flaky_test_reporter import (
    FlakynessCategory,
)
from operations_center.observer.flaky_test_models import FlakyTestMetric, FlakyTestSessionReport


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
        retry_success_count: int = 0,
        duration_mean: float = 0.0,
        duration_variance: float = 0.0,
        pattern_entropy: float = 0.0,
        streak_length: int = 0,
        recovery_time_days: float | None = None,
        flakiness_score: float = 0.0,
        confidence: float = 0.0,
        suspected_category: FlakynessCategory | None = None,
        markers: list[str] | None = None,
        last_failure_reason: str = "",
        **kwargs,
    ) -> FlakyTestMetric:
        # Ignore extra kwargs that are not valid for FlakyTestMetric
        return FlakyTestMetric(
            nodeid=nodeid,
            failure_rate=failure_rate,
            run_count=run_count,
            retry_success_count=retry_success_count,
            duration_mean=duration_mean,
            duration_variance=duration_variance,
            pattern_entropy=pattern_entropy,
            streak_length=streak_length,
            recovery_time_days=recovery_time_days,
            flakiness_score=flakiness_score,
            confidence=confidence,
            suspected_category=suspected_category or FlakynessCategory.UNKNOWN,
            markers=markers or [],
            last_failure_reason=last_failure_reason,
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
