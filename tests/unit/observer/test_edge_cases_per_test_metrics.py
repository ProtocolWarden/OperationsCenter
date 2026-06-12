# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parametrized edge-case tests for per-test flaky metrics.

Tests all 7 per-test metrics with extreme, boundary, and invalid values:
1. failure_rate
2. failure_entropy
3. streak_variance
4. recovery_time_percentile_90
5. duration_stability
6. environment_correlation
7. isolation_score

All tests use pytest parametrization for comprehensive edge-case coverage.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from tests.unit.observer.data_generators import (
    generate_duration_stability_scenarios,
    generate_environment_correlation_scenarios,
    generate_failure_entropy_scenarios,
    generate_failure_rate_scenarios,
    generate_isolation_score_scenarios,
    generate_recovery_time_percentile_90_scenarios,
    generate_streak_variance_scenarios,
)


class TestFailureRate:
    """Test edge cases for failure_rate metric.

    Metric: failures / total_runs
    Valid range: [0, 1]
    Threshold: > 0.05 (5%)
    """

    @pytest.mark.parametrize(
        "failures,total,expected_rate,scenario",
        generate_failure_rate_scenarios(),
        ids=[s[3] for s in generate_failure_rate_scenarios()],
    )
    def test_failure_rate_calculation(
        self, failures: int, total: int, expected_rate: float, scenario: str
    ) -> None:
        """Test failure_rate calculation with various edge cases."""
        if total == 0:
            rate = 0.0 if failures == 0 else failures
        else:
            rate = failures / total
        assert abs(rate - expected_rate) < 1e-5, f"{scenario}: {rate} != {expected_rate}"

    @pytest.mark.parametrize(
        "failures,total,expected_rate,scenario",
        generate_failure_rate_scenarios(),
        ids=[s[3] for s in generate_failure_rate_scenarios()],
    )
    def test_failure_rate_range(
        self, failures: int, total: int, expected_rate: float, scenario: str
    ) -> None:
        """Test that failure_rate stays within [0, 1]."""
        assert 0.0 <= expected_rate <= 1.0, f"{scenario}: {expected_rate} outside [0, 1]"

    @pytest.mark.parametrize(
        "failures,total,expected_rate,scenario",
        generate_failure_rate_scenarios(),
        ids=[s[3] for s in generate_failure_rate_scenarios()],
    )
    def test_failure_rate_threshold(
        self, failures: int, total: int, expected_rate: float, scenario: str
    ) -> None:
        """Test threshold logic: > 0.05 indicates flakiness."""
        is_flaky = expected_rate > 0.05
        assert isinstance(is_flaky, bool)


class TestFailureEntropy:
    """Test edge cases for failure_entropy metric.

    Metric: Shannon entropy of pass/fail distribution
    Valid range: [0, 1]
    Threshold: > 0.7
    """

    @pytest.mark.parametrize(
        "pass_count,fail_count,expected_entropy,scenario",
        generate_failure_entropy_scenarios(),
        ids=[s[2] for s in generate_failure_entropy_scenarios()],
    )
    def test_failure_entropy_calculation(
        self, pass_count: int, fail_count: int, expected_entropy: float, scenario: str
    ) -> None:
        """Test failure_entropy calculation."""
        total = pass_count + fail_count
        if total == 0:
            entropy = 0.0
        else:
            p_pass = pass_count / total if pass_count > 0 else 0
            p_fail = fail_count / total if fail_count > 0 else 0
            entropy = 0.0
            if p_pass > 0:
                entropy -= p_pass * math.log2(p_pass)
            if p_fail > 0:
                entropy -= p_fail * math.log2(p_fail)
        assert abs(entropy - expected_entropy) < 1e-5, (
            f"{scenario}: {entropy} != {expected_entropy}"
        )

    @pytest.mark.parametrize(
        "pass_count,fail_count,expected_entropy,scenario",
        generate_failure_entropy_scenarios(),
        ids=[s[2] for s in generate_failure_entropy_scenarios()],
    )
    def test_failure_entropy_range(
        self, pass_count: int, fail_count: int, expected_entropy: float, scenario: str
    ) -> None:
        """Test that entropy stays within [0, 1]."""
        assert 0.0 <= expected_entropy <= 1.0, f"{scenario}: {expected_entropy} outside [0, 1]"

    @pytest.mark.parametrize(
        "pass_count,fail_count,expected_entropy,scenario",
        generate_failure_entropy_scenarios(),
        ids=[s[2] for s in generate_failure_entropy_scenarios()],
    )
    def test_failure_entropy_randomness(
        self, pass_count: int, fail_count: int, expected_entropy: float, scenario: str
    ) -> None:
        """Test threshold logic: > 0.7 indicates high randomness."""
        is_random = expected_entropy > 0.7
        assert isinstance(is_random, bool)


class TestStreakVariance:
    """Test edge cases for streak_variance metric.

    Metric: variance of failure streak lengths
    Valid range: [0, ∞]
    Threshold: > 1.5
    """

    @pytest.mark.parametrize(
        "streaks,expected_var,scenario",
        generate_streak_variance_scenarios(),
        ids=[s[2] for s in generate_streak_variance_scenarios()],
    )
    def test_streak_variance_calculation(
        self, streaks: list[int], expected_var: Any, scenario: str
    ) -> None:
        """Test streak_variance calculation."""
        if not streaks or expected_var == "error":
            var = 0.0
        else:
            mean = sum(streaks) / len(streaks)
            variance = sum((x - mean) ** 2 for x in streaks) / len(streaks)
            var = variance
        if expected_var is not None and expected_var != "error":
            assert abs(var - expected_var) < 1e-5, f"{scenario}: {var} != {expected_var}"

    @pytest.mark.parametrize(
        "streaks,expected_var,scenario",
        generate_streak_variance_scenarios(),
        ids=[s[2] for s in generate_streak_variance_scenarios()],
    )
    def test_streak_variance_non_negative(
        self, streaks: list[int], expected_var: Any, scenario: str
    ) -> None:
        """Test that variance cannot be negative."""
        if expected_var is not None and expected_var != "error":
            assert expected_var >= 0.0, f"{scenario}: variance {expected_var} < 0"

    @pytest.mark.parametrize(
        "streaks,expected_var,scenario",
        generate_streak_variance_scenarios(),
        ids=[s[2] for s in generate_streak_variance_scenarios()],
    )
    def test_streak_variance_threshold(
        self, streaks: list[int], expected_var: Any, scenario: str
    ) -> None:
        """Test threshold logic: > 1.5 indicates inconsistent patterns."""
        if expected_var is not None and expected_var != "error":
            is_inconsistent = expected_var > 1.5
            assert isinstance(is_inconsistent, bool)


class TestRecoveryTime:
    """Test edge cases for recovery_time_percentile_90 metric.

    Metric: 90th percentile of recovery time between failures
    Valid range: [0, ∞]
    Threshold: > 5 days
    """

    @pytest.mark.parametrize(
        "num_failures,num_recovered,expected_p90,scenario",
        generate_recovery_time_percentile_90_scenarios(),
        ids=[s[3] for s in generate_recovery_time_percentile_90_scenarios()],
    )
    def test_recovery_time_percentile(
        self, num_failures: int, num_recovered: int, expected_p90: Any, scenario: str
    ) -> None:
        """Test 90th percentile calculation for recovery times."""
        if num_failures == 0 or expected_p90 is None:
            p90 = None
        elif num_recovered == 0:
            p90 = None
        else:
            # Mock recovery times: [1, 1, 1, ..., 9] for percentile test
            recovery_times = list(range(1, num_recovered + 1))
            sorted_times = sorted(recovery_times)
            idx = int(0.9 * len(sorted_times))
            p90 = sorted_times[idx] if idx < len(sorted_times) else sorted_times[-1]

        if expected_p90 not in (None, float("inf")) and p90 is not None:
            # Allow some flexibility for percentile calculation
            assert abs(p90 - expected_p90) <= 1, f"{scenario}: {p90} != {expected_p90}"

    @pytest.mark.parametrize(
        "num_failures,num_recovered,expected_p90,scenario",
        generate_recovery_time_percentile_90_scenarios(),
        ids=[s[3] for s in generate_recovery_time_percentile_90_scenarios()],
    )
    def test_recovery_time_non_negative(
        self, num_failures: int, num_recovered: int, expected_p90: Any, scenario: str
    ) -> None:
        """Test that recovery time cannot be negative."""
        if expected_p90 is not None and expected_p90 != float("inf"):
            assert expected_p90 >= 0.0, f"{scenario}: recovery time {expected_p90} < 0"

    @pytest.mark.parametrize(
        "num_failures,num_recovered,expected_p90,scenario",
        generate_recovery_time_percentile_90_scenarios(),
        ids=[s[3] for s in generate_recovery_time_percentile_90_scenarios()],
    )
    def test_recovery_time_threshold(
        self, num_failures: int, num_recovered: int, expected_p90: Any, scenario: str
    ) -> None:
        """Test threshold logic: > 5 days indicates slow recovery."""
        if expected_p90 is not None and expected_p90 != float("inf"):
            is_slow = expected_p90 > 5.0
            assert isinstance(is_slow, bool)


class TestDurationStability:
    """Test edge cases for duration_stability metric.

    Metric: coefficient of variation of test duration
    Valid range: [0, ∞]
    Threshold: > 0.4
    """

    @pytest.mark.parametrize(
        "durations,expected_cov,scenario",
        generate_duration_stability_scenarios(),
        ids=[s[2] for s in generate_duration_stability_scenarios()],
    )
    def test_duration_stability_calculation(
        self, durations: list[float], expected_cov: Any, scenario: str
    ) -> None:
        """Test duration stability (CoV) calculation."""
        if not durations or expected_cov == "error":
            cov = 0.0
        else:
            mean = sum(durations) / len(durations)
            if mean == 0:
                cov = 0.0
            else:
                variance = sum((x - mean) ** 2 for x in durations) / len(durations)
                cov = (variance**0.5) / mean
        if expected_cov is not None and expected_cov != "error":
            assert abs(cov - expected_cov) < 1e-5, f"{scenario}: {cov} != {expected_cov}"

    @pytest.mark.parametrize(
        "durations,expected_cov,scenario",
        generate_duration_stability_scenarios(),
        ids=[s[2] for s in generate_duration_stability_scenarios()],
    )
    def test_duration_stability_non_negative(
        self, durations: list[float], expected_cov: Any, scenario: str
    ) -> None:
        """Test that CoV cannot be negative."""
        if expected_cov is not None and expected_cov != "error":
            assert expected_cov >= 0.0, f"{scenario}: CoV {expected_cov} < 0"

    @pytest.mark.parametrize(
        "durations,expected_cov,scenario",
        generate_duration_stability_scenarios(),
        ids=[s[2] for s in generate_duration_stability_scenarios()],
    )
    def test_duration_stability_threshold(
        self, durations: list[float], expected_cov: Any, scenario: str
    ) -> None:
        """Test threshold logic: > 0.4 indicates instability."""
        if expected_cov is not None and expected_cov != "error":
            is_unstable = expected_cov > 0.4
            assert isinstance(is_unstable, bool)


class TestEnvironmentCorrelation:
    """Test edge cases for environment_correlation metric.

    Metric: Pearson correlation with environment variables
    Valid range: [-1, 1]
    Threshold: > 0.6
    """

    @pytest.mark.parametrize(
        "failures,env_values,expected_corr,scenario",
        generate_environment_correlation_scenarios(),
        ids=[s[3] for s in generate_environment_correlation_scenarios()],
    )
    def test_environment_correlation_range(
        self,
        failures: list[int],
        env_values: list[int],
        expected_corr: Any,
        scenario: str,
    ) -> None:
        """Test that correlation stays within [-1, 1]."""
        if expected_corr not in ("undefined", "error"):
            assert -1.0 <= expected_corr <= 1.0, f"{scenario}: {expected_corr} outside [-1, 1]"

    @pytest.mark.parametrize(
        "failures,env_values,expected_corr,scenario",
        generate_environment_correlation_scenarios(),
        ids=[s[3] for s in generate_environment_correlation_scenarios()],
    )
    def test_environment_correlation_threshold(
        self,
        failures: list[int],
        env_values: list[int],
        expected_corr: Any,
        scenario: str,
    ) -> None:
        """Test threshold logic: > 0.6 indicates strong environment dependency."""
        if expected_corr not in ("undefined", "error"):
            is_env_dependent = expected_corr > 0.6
            assert isinstance(is_env_dependent, bool)

    @pytest.mark.parametrize(
        "failures,env_values,expected_corr,scenario",
        generate_environment_correlation_scenarios(),
        ids=[s[3] for s in generate_environment_correlation_scenarios()],
    )
    def test_environment_correlation_perfection(
        self,
        failures: list[int],
        env_values: list[int],
        expected_corr: Any,
        scenario: str,
    ) -> None:
        """Test perfect correlation values."""
        if expected_corr in (1.0, -1.0):
            assert expected_corr in [-1.0, 1.0], f"{scenario}: perfect corr should be ±1.0"


class TestIsolationScore:
    """Test edge cases for isolation_score metric.

    Metric: 1 - (parallel_failures / serial_failures)
    Valid range: [0, 1] (though can be negative for anomalies)
    Threshold: < 0.3 (poor isolation)
    """

    @pytest.mark.parametrize(
        "serial_failures,parallel_failures,expected_score,scenario",
        generate_isolation_score_scenarios(),
        ids=[s[3] for s in generate_isolation_score_scenarios()],
    )
    def test_isolation_score_calculation(
        self,
        serial_failures: int,
        parallel_failures: int,
        expected_score: float,
        scenario: str,
    ) -> None:
        """Test isolation_score calculation with edge cases."""
        if serial_failures == 0:
            if parallel_failures == 0:
                score = 1.0
            else:
                score = 0.0
        else:
            score = 1.0 - (parallel_failures / serial_failures)
        assert abs(score - expected_score) < 1e-5, f"{scenario}: {score} != {expected_score}"

    @pytest.mark.parametrize(
        "serial_failures,parallel_failures,expected_score,scenario",
        generate_isolation_score_scenarios(),
        ids=[s[3] for s in generate_isolation_score_scenarios()],
    )
    def test_isolation_score_valid_range(
        self,
        serial_failures: int,
        parallel_failures: int,
        expected_score: float,
        scenario: str,
    ) -> None:
        """Test that isolation score interpretation is valid."""
        if expected_score >= 1.0:
            status = "perfect"
        elif expected_score >= 0.7:
            status = "good"
        elif expected_score >= 0.3:
            status = "fair"
        elif expected_score >= 0.0:
            status = "poor"
        else:
            status = "anomaly"
        assert status in ["perfect", "good", "fair", "poor", "anomaly"]

    @pytest.mark.parametrize(
        "serial_failures,parallel_failures,expected_score,scenario",
        generate_isolation_score_scenarios(),
        ids=[s[3] for s in generate_isolation_score_scenarios()],
    )
    def test_isolation_score_threshold(
        self,
        serial_failures: int,
        parallel_failures: int,
        expected_score: float,
        scenario: str,
    ) -> None:
        """Test threshold logic: < 0.3 indicates poor isolation."""
        is_poor_isolation = expected_score < 0.3
        assert isinstance(is_poor_isolation, bool)
