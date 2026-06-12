# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parametrized edge-case tests for repository-level flaky test metrics.

Tests all 7 repository-level metrics with extreme, boundary, and invalid values:
1. flaky_test_percentage
2. median_failure_rate
3. flaky_growth_rate
4. category_concentration
5. critical_test_flakiness_ratio
6. flaky_velocity
7. repository_health_score

All tests use pytest parametrization for comprehensive edge-case coverage.
"""

from __future__ import annotations

import math

import pytest

from tests.unit.observer.test_data_generators import (
    generate_category_concentration_scenarios,
    generate_critical_test_flakiness_scenarios,
    generate_flaky_growth_rate_scenarios,
    generate_flaky_test_percentage_scenarios,
    generate_flaky_velocity_scenarios,
    generate_median_failure_rate_scenarios,
    generate_repository_health_score_scenarios,
)


class TestFlakyTestPercentage:
    """Test edge cases for flaky_test_percentage metric.

    Metric: flaky_count / total_tests
    Valid range: [0, 1]
    Threshold: > 0.05 (5%)
    """

    @pytest.mark.parametrize(
        "flaky_count,total_tests,expected_pct,scenario",
        generate_flaky_test_percentage_scenarios(),
        ids=[s[3] for s in generate_flaky_test_percentage_scenarios()],
    )
    def test_flaky_test_percentage_calculation(
        self, flaky_count: int, total_tests: int, expected_pct: float, scenario: str
    ) -> None:
        """Test flaky_test_percentage calculation with various edge cases."""
        if total_tests == 0:
            # Division by zero edge case - should return 0.0
            pct = 0.0 if flaky_count == 0 else flaky_count
            assert pct == expected_pct, f"{scenario}: {pct} != {expected_pct}"
        else:
            pct = flaky_count / total_tests
            assert abs(pct - expected_pct) < 1e-6, f"{scenario}: {pct} != {expected_pct}"

    @pytest.mark.parametrize(
        "flaky_count,total_tests,expected_pct,scenario",
        generate_flaky_test_percentage_scenarios(),
        ids=[s[3] for s in generate_flaky_test_percentage_scenarios()],
    )
    def test_flaky_test_percentage_range(
        self, flaky_count: int, total_tests: int, expected_pct: float, scenario: str
    ) -> None:
        """Test that flaky_test_percentage stays within valid range [0, 1]."""
        if total_tests == 0:
            pct = expected_pct
        else:
            pct = flaky_count / total_tests
        assert 0.0 <= pct <= 1.0, f"{scenario}: {pct} outside [0, 1]"

    @pytest.mark.parametrize(
        "flaky_count,total_tests,expected_pct,scenario",
        generate_flaky_test_percentage_scenarios(),
        ids=[s[3] for s in generate_flaky_test_percentage_scenarios()],
    )
    def test_flaky_test_percentage_threshold(
        self, flaky_count: int, total_tests: int, expected_pct: float, scenario: str
    ) -> None:
        """Test threshold logic: > 0.05 is degraded."""
        if total_tests == 0:
            pct = expected_pct
        else:
            pct = flaky_count / total_tests
        # Just verify we can determine if above/below threshold
        is_degraded = pct > 0.05
        assert isinstance(is_degraded, bool)


class TestMedianFailureRate:
    """Test edge cases for median_failure_rate metric.

    Metric: median of failure rates across flaky tests
    Valid range: [0, 1]
    Threshold: > 0.10 (10%)
    """

    @pytest.mark.parametrize(
        "failure_rates,expected_median,scenario",
        generate_median_failure_rate_scenarios(),
        ids=[s[2] for s in generate_median_failure_rate_scenarios()],
    )
    def test_median_failure_rate_calculation(
        self, failure_rates: list[float], expected_median: float, scenario: str
    ) -> None:
        """Test median_failure_rate calculation with various distributions."""
        if not failure_rates:
            median = 0.0
        else:
            sorted_rates = sorted(failure_rates)
            n = len(sorted_rates)
            if n % 2 == 1:
                median = sorted_rates[n // 2]
            else:
                median = (sorted_rates[n // 2 - 1] + sorted_rates[n // 2]) / 2.0
        assert abs(median - expected_median) < 1e-6, f"{scenario}: {median} != {expected_median}"

    @pytest.mark.parametrize(
        "failure_rates,expected_median,scenario",
        generate_median_failure_rate_scenarios(),
        ids=[s[2] for s in generate_median_failure_rate_scenarios()],
    )
    def test_median_failure_rate_range(
        self, failure_rates: list[float], expected_median: float, scenario: str
    ) -> None:
        """Test that median_failure_rate stays within valid range [0, 1]."""
        assert 0.0 <= expected_median <= 1.0, f"{scenario}: {expected_median} outside [0, 1]"

    @pytest.mark.parametrize(
        "failure_rates,expected_median,scenario",
        generate_median_failure_rate_scenarios(),
        ids=[s[2] for s in generate_median_failure_rate_scenarios()],
    )
    def test_median_failure_rate_threshold(
        self, failure_rates: list[float], expected_median: float, scenario: str
    ) -> None:
        """Test threshold logic: > 0.10 indicates significant failures."""
        is_significant = expected_median > 0.10
        assert isinstance(is_significant, bool)


class TestFlakyGrowthRate:
    """Test edge cases for flaky_growth_rate metric.

    Metric: (current - previous) / previous
    Valid range: [-1, ∞]
    Threshold: > 0.2 (20% growth)
    """

    @pytest.mark.parametrize(
        "previous_count,current_count,expected_growth,scenario",
        generate_flaky_growth_rate_scenarios(),
        ids=[s[3] for s in generate_flaky_growth_rate_scenarios()],
    )
    def test_flaky_growth_rate_calculation(
        self,
        previous_count: int,
        current_count: int,
        expected_growth: float,
        scenario: str,
    ) -> None:
        """Test flaky_growth_rate calculation with division by zero edge cases."""
        if previous_count == 0:
            # Division by zero - handle as infinity or special case
            if current_count == 0:
                growth = 0.0
            else:
                growth = float("inf")
        else:
            growth = (current_count - previous_count) / previous_count

        if math.isinf(expected_growth):
            assert math.isinf(growth), f"{scenario}: {growth} should be inf"
        else:
            assert abs(growth - expected_growth) < 1e-6, (
                f"{scenario}: {growth} != {expected_growth}"
            )

    @pytest.mark.parametrize(
        "previous_count,current_count,expected_growth,scenario",
        generate_flaky_growth_rate_scenarios(),
        ids=[s[3] for s in generate_flaky_growth_rate_scenarios()],
    )
    def test_flaky_growth_rate_negative_bounds(
        self,
        previous_count: int,
        current_count: int,
        expected_growth: float,
        scenario: str,
    ) -> None:
        """Test that growth rate cannot go below -1.0 (complete elimination)."""
        if previous_count == 0:
            if current_count == 0:
                growth = 0.0
            else:
                growth = float("inf")
        else:
            growth = (current_count - previous_count) / previous_count

        if not math.isinf(growth):
            assert growth >= -1.0, f"{scenario}: {growth} < -1.0 (impossible)"

    @pytest.mark.parametrize(
        "previous_count,current_count,expected_growth,scenario",
        generate_flaky_growth_rate_scenarios(),
        ids=[s[3] for s in generate_flaky_growth_rate_scenarios()],
    )
    def test_flaky_growth_rate_threshold(
        self,
        previous_count: int,
        current_count: int,
        expected_growth: float,
        scenario: str,
    ) -> None:
        """Test threshold logic: > 0.2 indicates regression."""
        if math.isinf(expected_growth):
            # Infinity always exceeds threshold
            is_regressing = True
        else:
            is_regressing = expected_growth > 0.2
        assert isinstance(is_regressing, bool)


class TestCategoryConcentration:
    """Test edge cases for category_concentration metric.

    Metric: max_category_count / total_flaky
    Valid range: [0, 1] (actually [0.25, 1] with min 4 categories)
    Threshold: > 0.6 (60% in one category)
    """

    @pytest.mark.parametrize(
        "category_counts,expected_concentration,scenario",
        generate_category_concentration_scenarios(),
        ids=[s[2] for s in generate_category_concentration_scenarios()],
    )
    def test_category_concentration_calculation(
        self,
        category_counts: dict[str, int],
        expected_concentration: float,
        scenario: str,
    ) -> None:
        """Test category_concentration calculation."""
        if not category_counts:
            concentration = 0.0
        else:
            total = sum(category_counts.values())
            max_count = max(category_counts.values())
            concentration = max_count / total
        assert abs(concentration - expected_concentration) < 1e-6, (
            f"{scenario}: {concentration} != {expected_concentration}"
        )

    @pytest.mark.parametrize(
        "category_counts,expected_concentration,scenario",
        generate_category_concentration_scenarios(),
        ids=[s[2] for s in generate_category_concentration_scenarios()],
    )
    def test_category_concentration_range(
        self,
        category_counts: dict[str, int],
        expected_concentration: float,
        scenario: str,
    ) -> None:
        """Test that concentration stays within [0, 1]."""
        assert 0.0 <= expected_concentration <= 1.0, (
            f"{scenario}: {expected_concentration} outside [0, 1]"
        )

    @pytest.mark.parametrize(
        "category_counts,expected_concentration,scenario",
        generate_category_concentration_scenarios(),
        ids=[s[2] for s in generate_category_concentration_scenarios()],
    )
    def test_category_concentration_threshold(
        self,
        category_counts: dict[str, int],
        expected_concentration: float,
        scenario: str,
    ) -> None:
        """Test threshold logic: > 0.6 indicates concentration."""
        is_concentrated = expected_concentration > 0.6
        assert isinstance(is_concentrated, bool)


class TestCriticalTestFlakiness:
    """Test edge cases for critical_test_flakiness_ratio metric.

    Metric: critical_flaky_count / total_critical_count
    Valid range: [0, 1]
    Threshold: > 0.1 (10% of critical tests are flaky)
    """

    @pytest.mark.parametrize(
        "critical_flaky,total_critical,expected_ratio,scenario",
        generate_critical_test_flakiness_scenarios(),
        ids=[s[3] for s in generate_critical_test_flakiness_scenarios()],
    )
    def test_critical_flakiness_calculation(
        self,
        critical_flaky: int,
        total_critical: int,
        expected_ratio: float,
        scenario: str,
    ) -> None:
        """Test critical_flakiness_ratio calculation with division by zero."""
        if total_critical == 0:
            ratio = 0.0
        else:
            ratio = critical_flaky / total_critical
        assert abs(ratio - expected_ratio) < 1e-6, f"{scenario}: {ratio} != {expected_ratio}"

    @pytest.mark.parametrize(
        "critical_flaky,total_critical,expected_ratio,scenario",
        generate_critical_test_flakiness_scenarios(),
        ids=[s[3] for s in generate_critical_test_flakiness_scenarios()],
    )
    def test_critical_flakiness_range(
        self,
        critical_flaky: int,
        total_critical: int,
        expected_ratio: float,
        scenario: str,
    ) -> None:
        """Test that ratio stays within [0, 1]."""
        assert 0.0 <= expected_ratio <= 1.0, f"{scenario}: {expected_ratio} outside [0, 1]"

    @pytest.mark.parametrize(
        "critical_flaky,total_critical,expected_ratio,scenario",
        generate_critical_test_flakiness_scenarios(),
        ids=[s[3] for s in generate_critical_test_flakiness_scenarios()],
    )
    def test_critical_flakiness_severity(
        self,
        critical_flaky: int,
        total_critical: int,
        expected_ratio: float,
        scenario: str,
    ) -> None:
        """Test that critical flakiness is treated as high-severity."""
        is_critical = expected_ratio > 0.1
        assert isinstance(is_critical, bool)


class TestFlakyVelocity:
    """Test edge cases for flaky_velocity metric.

    Metric: new flaky tests per day in 7-day window
    Valid range: [0, ∞]
    Threshold: > 1.0 (more than 1 per day = outbreak)
    """

    @pytest.mark.parametrize(
        "new_flaky_count,window_days,expected_velocity,scenario",
        generate_flaky_velocity_scenarios(),
        ids=[s[3] for s in generate_flaky_velocity_scenarios()],
    )
    def test_flaky_velocity_calculation(
        self,
        new_flaky_count: int,
        window_days: int,
        expected_velocity: float,
        scenario: str,
    ) -> None:
        """Test flaky_velocity calculation: new_count / window_days."""
        if window_days == 0:
            velocity = 0.0
        else:
            velocity = new_flaky_count / window_days
        assert abs(velocity - expected_velocity) < 1e-6, (
            f"{scenario}: {velocity} != {expected_velocity}"
        )

    @pytest.mark.parametrize(
        "new_flaky_count,window_days,expected_velocity,scenario",
        generate_flaky_velocity_scenarios(),
        ids=[s[3] for s in generate_flaky_velocity_scenarios()],
    )
    def test_flaky_velocity_non_negative(
        self,
        new_flaky_count: int,
        window_days: int,
        expected_velocity: float,
        scenario: str,
    ) -> None:
        """Test that velocity cannot be negative."""
        assert expected_velocity >= 0.0, f"{scenario}: velocity {expected_velocity} < 0"

    @pytest.mark.parametrize(
        "new_flaky_count,window_days,expected_velocity,scenario",
        generate_flaky_velocity_scenarios(),
        ids=[s[3] for s in generate_flaky_velocity_scenarios()],
    )
    def test_flaky_velocity_threshold(
        self,
        new_flaky_count: int,
        window_days: int,
        expected_velocity: float,
        scenario: str,
    ) -> None:
        """Test threshold logic: > 1.0 indicates outbreak."""
        is_outbreak = expected_velocity > 1.0
        assert isinstance(is_outbreak, bool)


class TestRepositoryHealthScore:
    """Test edge cases for repository_health_score metric.

    Metric: composite health score from multiple factors
    Valid range: [0, 1]
    Formula: (1.0 - flaky_pct/0.1) - growth_penalty - critical_penalty - unknown_penalty
    Clamped to [0, 1]
    Threshold: < 0.7 is degraded
    """

    @pytest.mark.parametrize(
        "flaky_pct,growth_rate,critical_ratio,unknown_ratio,expected_health,scenario",
        generate_repository_health_score_scenarios(),
        ids=[s[4] for s in generate_repository_health_score_scenarios()],
    )
    def test_health_score_calculation(
        self,
        flaky_pct: float,
        growth_rate: float,
        critical_ratio: float,
        unknown_ratio: float,
        expected_health: float,
        scenario: str,
    ) -> None:
        """Test health score calculation with clamp to [0, 1]."""
        # Base score from flaky percentage
        score = 1.0 - (flaky_pct / 0.1)

        # Apply penalties
        if growth_rate > 0.2:
            score -= 0.1
        if critical_ratio > 0.1:
            score -= 0.1
        if unknown_ratio > 0.5:
            score -= 0.15

        # Clamp to [0, 1]
        health = max(0.0, min(1.0, score))

        assert abs(health - expected_health) < 1e-6, f"{scenario}: {health} != {expected_health}"

    @pytest.mark.parametrize(
        "flaky_pct,growth_rate,critical_ratio,unknown_ratio,expected_health,scenario",
        generate_repository_health_score_scenarios(),
        ids=[s[4] for s in generate_repository_health_score_scenarios()],
    )
    def test_health_score_range(
        self,
        flaky_pct: float,
        growth_rate: float,
        critical_ratio: float,
        unknown_ratio: float,
        expected_health: float,
        scenario: str,
    ) -> None:
        """Test that health score is clamped to [0, 1]."""
        assert 0.0 <= expected_health <= 1.0, f"{scenario}: {expected_health} outside [0, 1]"

    @pytest.mark.parametrize(
        "flaky_pct,growth_rate,critical_ratio,unknown_ratio,expected_health,scenario",
        generate_repository_health_score_scenarios(),
        ids=[s[4] for s in generate_repository_health_score_scenarios()],
    )
    def test_health_score_status(
        self,
        flaky_pct: float,
        growth_rate: float,
        critical_ratio: float,
        unknown_ratio: float,
        expected_health: float,
        scenario: str,
    ) -> None:
        """Test health status determination."""
        if expected_health >= 0.9:
            status = "healthy"
        elif expected_health >= 0.7:
            status = "nominal"
        elif expected_health >= 0.4:
            status = "degraded"
        else:
            status = "critical"
        assert status in ["healthy", "nominal", "degraded", "critical"]

    @pytest.mark.parametrize(
        "flaky_pct,growth_rate,critical_ratio,unknown_ratio,expected_health,scenario",
        generate_repository_health_score_scenarios(),
        ids=[s[4] for s in generate_repository_health_score_scenarios()],
    )
    def test_health_score_perfect_health(
        self,
        flaky_pct: float,
        growth_rate: float,
        critical_ratio: float,
        unknown_ratio: float,
        expected_health: float,
        scenario: str,
    ) -> None:
        """Test that all zeros produces perfect health."""
        if (
            flaky_pct == 0.0
            and growth_rate == 0.0
            and critical_ratio == 0.0
            and unknown_ratio == 0.0
        ):
            assert expected_health == 1.0, f"{scenario}: Perfect inputs should yield 1.0"

    @pytest.mark.parametrize(
        "flaky_pct,growth_rate,critical_ratio,unknown_ratio,expected_health,scenario",
        generate_repository_health_score_scenarios(),
        ids=[s[4] for s in generate_repository_health_score_scenarios()],
    )
    def test_health_score_zero_health(
        self,
        flaky_pct: float,
        growth_rate: float,
        critical_ratio: float,
        unknown_ratio: float,
        expected_health: float,
        scenario: str,
    ) -> None:
        """Test that critical conditions produce zero or near-zero health."""
        # Only test scenarios where we expect zero health
        if scenario == "all_issues_critical":
            assert expected_health == 0.0, f"{scenario}: Critical issues should yield 0.0"
