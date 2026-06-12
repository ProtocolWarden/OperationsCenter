# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Test data generators for edge-case testing of flaky test reporter metrics.

Provides factory functions and generators for creating metric objects with
extreme, boundary, and invalid values for comprehensive edge-case testing
across all 14 metrics.

This module is designed to be used with pytest parametrization:
    @pytest.mark.parametrize("input1,input2,expected", generate_failure_rate_scenarios())
"""

from __future__ import annotations


from operations_center.observer.flaky_test_reporter import FlakyTestResult


# ============================================================================
# Per-Test Metric Generators (7 metrics)
# ============================================================================


def generate_failure_rate_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for failure_rate metric.

    Covers:
    - Zero and edge cases: (0, 0), (0, 1), (1, 1)
    - Boundary values: at, above, below threshold (0.05)
    - Extreme values: very large sample sizes
    - Precision limits: floating-point edge cases

    Returns:
        List of tuples: (failures, total, expected_rate, scenario_name)
    """
    return [
        # ZERO_INPUT: Zero total runs
        (0, 0, 0.0, "zero_total_runs"),
        # ZERO_INPUT: Single cases
        (0, 1, 0.0, "single_pass"),
        (1, 1, 1.0, "single_failure"),
        # BOUNDARY: At threshold (0.05)
        (1, 20, 0.05, "at_threshold"),
        # BOUNDARY: Just below threshold
        (1, 21, 0.047619, "below_threshold"),
        # BOUNDARY: Just above threshold
        (1, 19, 0.052632, "above_threshold"),
        # EXTREME: Large sample, high rate
        (9999, 10000, 0.9999, "large_sample_high_rate"),
        # EXTREME: Large sample, low rate
        (1, 10000, 0.0001, "large_sample_low_rate"),
        # VALID: Midpoint
        (1, 2, 0.5, "midpoint"),
    ]


def generate_failure_entropy_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for failure_entropy metric.

    Shannon entropy of pass/fail distribution.
    Valid range: [0, 1], threshold > 0.7

    Covers:
    - Deterministic cases (entropy = 0)
    - Maximum entropy (entropy = 1)
    - Single results
    - Imbalanced distributions

    Returns:
        List of tuples: (pass_count, fail_count, expected_entropy, scenario_name)
    """
    return [
        # ZERO_INPUT/PATHOLOGICAL: All passes
        (10, 0, 0.0, "all_pass"),
        # ZERO_INPUT/PATHOLOGICAL: All failures
        (0, 10, 0.0, "all_fail"),
        # BOUNDARY/EXTREME: Maximum entropy (50/50 split)
        (5, 5, 1.0, "balanced_50_50"),
        # ZERO_INPUT: Single pass
        (1, 0, 0.0, "single_pass"),
        # ZERO_INPUT: Single fail
        (0, 1, 0.0, "single_fail"),
        # BOUNDARY/EXTREME: Two different outcomes
        (1, 1, 1.0, "two_different"),
        # PATHOLOGICAL: Imbalanced 1/99
        (1, 99, 0.080793, "imbalanced_1_99"),
        # PATHOLOGICAL: Imbalanced 99/1
        (99, 1, 0.080793, "imbalanced_99_1"),
        # VALID: Moderately imbalanced
        (10, 1, 0.439497, "moderately_imbalanced"),
    ]


def generate_streak_variance_scenarios() -> list[tuple[list, float | None, str]]:
    """Generate parametrization scenarios for streak_variance metric.

    Variance of streak lengths: population variance of the list of streak lengths.
    Valid range: [0, ∞], threshold > 1.5

    Covers:
    - Single streak (undefined variance)
    - All same outcome (single long streak, variance = 0)
    - Alternating (10 streaks of 1, variance = 0)
    - Mixed patterns

    Returns:
        List of tuples: (streak_lengths, expected_variance, scenario_name)
        streak_lengths: list of int streak lengths
        expected_variance: float or None (None means variance is undefined/not checked)
    """
    return [
        # ZERO_INPUT: Single streak of length 1 (undefined variance)
        ([1], None, "single_run_undefined"),
        # PATHOLOGICAL: One long streak of 5 passes (variance = 0)
        ([5], 0.0, "all_same_pass"),
        # PATHOLOGICAL: One long streak of 5 failures (variance = 0)
        ([5], 0.0, "all_same_fail"),
        # PATHOLOGICAL: 10 alternating streaks of length 1 each (variance = 0)
        ([1] * 10, 0.0, "alternating"),
        # VALID: Three streaks [5, 1, 5] — high variance (32/9 ≈ 3.556)
        ([5, 1, 5], 32 / 9, "mixed_high_variance"),
        # VALID: Two streaks [3, 2] — low variance (0.25)
        ([3, 2], 0.25, "two_streaks"),
    ]


def generate_recovery_time_percentile_90_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for recovery_time_percentile_90 metric.

    Percentile 90 of recovery times (runs between failure and next pass).
    Valid range: [0, ∞], threshold > 5 runs

    Covers:
    - No failures
    - Single failure
    - Mixed recovered/unrecovered
    - Percentile edge cases

    Returns:
        List of tuples: (num_failures, num_recovered, expected_p90, scenario_name)
    """
    return [
        # ZERO_INPUT: No failures
        (0, 0, None, "no_failures"),
        # ZERO_INPUT: Single failure, recovered
        (1, 1, 1, "single_failure_recovered"),
        # BOUNDARY: All unrecovered
        (10, 0, None, "all_unrecovered"),
        # BOUNDARY: One recovered
        (10, 1, float("inf"), "mostly_unrecovered"),
        # VALID: 90% recovered (10 failures, 9 recovered)
        (10, 9, 9, "ninety_percent_recovered"),
        # VALID: Exactly at percentile boundary (range(1,10)=[1..9], idx=8, p90=9)
        (9, 9, 9, "all_but_one_recovered"),
        # EXTREME: Large sample (range(1,91)=[1..90], idx=81, p90=82)
        (100, 90, 82, "large_sample_recovery"),
    ]


def generate_duration_stability_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for duration_stability metric.

    Coefficient of variation: StdDev(duration) / Mean(duration)
    Valid range: [0, ∞], threshold > 0.4

    Covers:
    - All identical durations
    - Single duration
    - Zero durations (division by zero)
    - High variation

    Returns:
        List of tuples: (durations, expected_cov, scenario_name)
    """
    return [
        # PATHOLOGICAL: All identical
        ([1.0, 1.0, 1.0], 0.0, "all_identical"),
        # INVALID: All zero (division by zero)
        ([0.0, 0.0, 0.0], "error", "all_zero_division"),
        # ZERO_INPUT: Single run
        ([1.0], 0.0, "single_run"),
        # EXTREME: Minimal variation
        ([1.0, 1.0000001], None, "minimal_variation"),
        # EXTREME: High variation (100x range)
        ([0.1, 10.0], None, "high_variation_100x"),
        # VALID: Linear progression
        ([1.0, 2.0, 3.0, 4.0, 5.0], None, "linear_progression"),
    ]


def generate_environment_correlation_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for environment_correlation metric.

    Pearson correlation with environment metrics.
    Valid range: [-1, 1], threshold > 0.6

    Covers:
    - No variation in either variable
    - Perfect correlation
    - Perfect negative correlation
    - Zero correlation

    Returns:
        List of tuples: (failures, env_values, expected_corr, scenario_name)
    """
    return [
        # PATHOLOGICAL: No variation in either
        ([1, 1, 1], [1, 1, 1], 0.0, "no_variation_either"),
        # BOUNDARY/EXTREME: Perfect positive correlation
        ([0] * 9 + [1], [0] * 9 + [1], 1.0, "perfect_positive"),
        # BOUNDARY/EXTREME: Perfect negative correlation
        ([1] * 9 + [0], [0] * 9 + [1], -1.0, "perfect_negative"),
        # ZERO_INPUT: No failures, varying environment
        ([0] * 9, [1, 2, 3, 4, 5, 6, 7, 8, 9], 0.0, "no_failures_varying_env"),
        # ZERO_INPUT: Empty data
        ([], [], "undefined", "no_data"),
    ]


def generate_isolation_score_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for isolation_score metric.

    Isolation measure: 1 - (parallel_failures / serial_failures)
    Valid range: [0, 1], threshold < 0.3 (poor isolation)

    Covers:
    - Division by zero edge cases
    - Perfect isolation
    - No isolation
    - Negative scores (invalid)

    Returns:
        List of tuples: (serial_failures, parallel_failures, expected_score, scenario_name)
    """
    return [
        # ZERO_INPUT: Neither fail
        (0, 0, 1.0, "no_failures_either_mode"),
        # BOUNDARY/EXTREME: Perfect isolation
        (10, 0, 1.0, "perfect_isolation"),
        # BOUNDARY: No isolation
        (0, 10, 0.0, "no_isolation"),
        # VALID: Same rate both ways
        (10, 10, 0.0, "same_failure_rate"),
        # VALID: Half in parallel
        (10, 5, 0.5, "half_parallel_failures"),
        # INVALID: More failures in parallel
        (5, 10, -1.0, "more_parallel_anomaly"),
    ]


# ============================================================================
# Repository-Level Metric Generators (7 metrics)
# ============================================================================


def generate_flaky_test_percentage_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for flaky_test_percentage metric.

    Percentage of flaky tests: flaky_count / total_tests
    Valid range: [0, 1], threshold > 0.05

    Covers:
    - No tests (division by zero)
    - Single test scenarios
    - Boundary values
    - Large repositories

    Returns:
        List of tuples: (flaky_count, total_tests, expected_pct, scenario_name)
    """
    return [
        # ZERO_INPUT: No tests
        (0, 0, 0.0, "no_tests"),
        # ZERO_INPUT: Single stable
        (0, 1, 0.0, "single_stable"),
        # ZERO_INPUT: Single flaky
        (1, 1, 1.0, "single_flaky"),
        # BOUNDARY: At threshold (5%)
        (1, 20, 0.05, "at_threshold"),
        # BOUNDARY: At threshold (percentage)
        (5, 100, 0.05, "at_threshold_percentage"),
        # EXTREME: Large repo, minimal flaky
        (1, 10000, 0.0001, "large_repo_minimal"),
        # EXTREME: Large repo, half flaky
        (5000, 10000, 0.5, "large_repo_half_flaky"),
    ]


def generate_median_failure_rate_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for median_failure_rate metric.

    Median of failure rates across flaky tests.
    Valid range: [0, 1], threshold > 0.10

    Covers:
    - No flaky tests
    - Single flaky test
    - Even and odd sample counts
    - Skewed distributions

    Returns:
        List of tuples: (failure_rates, expected_median, scenario_name)
    """
    return [
        # ZERO_INPUT: No flaky tests
        ([], 0.0, "no_flaky_tests"),
        # ZERO_INPUT: Single flaky
        ([0.1], 0.1, "single_flaky"),
        # BOUNDARY: Two tests (even)
        ([0.1, 0.2], 0.15, "two_tests_even"),
        # BOUNDARY: Three tests (odd)
        ([0.1, 0.2, 0.3], 0.2, "three_tests_odd"),
        # PATHOLOGICAL: All same
        ([0.05] * 5, 0.05, "all_same_rate"),
        # VALID: Skewed distribution
        ([0.01, 0.5, 0.99], 0.5, "skewed_distribution"),
    ]


def generate_flaky_growth_rate_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for flaky_growth_rate metric.

    Growth rate: (current - previous) / previous
    Valid range: [-1, ∞], threshold > 0.2

    Covers:
    - No previous data (division by zero)
    - No change
    - Negative growth (recovery)
    - Large growth

    Returns:
        List of tuples: (previous_count, current_count, expected_growth, scenario_name)
    """
    return [
        # ZERO_INPUT: First detection
        (0, 0, 0.0, "first_detection_none"),
        # ZERO_INPUT: First flaky found
        (0, 1, float("inf"), "first_flaky_found"),
        # BOUNDARY: No change
        (1, 1, 0.0, "no_change"),
        # BOUNDARY: Stable
        (10, 10, 0.0, "stable"),
        # BOUNDARY: At threshold (20%)
        (10, 12, 0.2, "at_threshold"),
        # VALID: Improvement
        (10, 8, -0.2, "improvement"),
        # EXTREME: Complete recovery
        (10, 0, -1.0, "complete_recovery"),
        # EXTREME: Doubling
        (5, 10, 1.0, "doubling"),
    ]


def generate_category_concentration_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for category_concentration metric.

    Concentration: max_category_count / total_flaky
    Valid range: [0, 1], threshold > 0.6

    Covers:
    - No tests
    - Single test
    - Equal distribution
    - Concentrated distribution

    Returns:
        List of tuples: (category_counts, expected_concentration, scenario_name)
    """
    return [
        # ZERO_INPUT: No flaky tests
        ({}, 0.0, "no_flaky"),
        # ZERO_INPUT: Single category
        ({"intermittent": 1}, 1.0, "single_category"),
        # BOUNDARY: Four-way equal split
        ({"intermittent": 1, "env": 1, "infra": 1, "unknown": 1}, 0.25, "equal_4way_split"),
        # BOUNDARY: At threshold (60%)
        ({"intermittent": 6, "env": 4}, 0.6, "at_threshold"),
        # EXTREME: Heavily concentrated
        ({"intermittent": 1000, "env": 1}, 0.999, "heavily_concentrated"),
    ]


def generate_critical_test_flakiness_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for critical_test_flakiness_ratio metric.

    Ratio: critical_flaky_count / total_critical_count
    Valid range: [0, 1], threshold > 0.1

    Covers:
    - No critical tests (division by zero)
    - Single critical test
    - Boundary values
    - Large critical test suites

    Returns:
        List of tuples: (critical_flaky, total_critical, expected_ratio, scenario_name)
    """
    return [
        # ZERO_INPUT: No critical tests
        (0, 0, 0.0, "no_critical_tests"),
        # ZERO_INPUT: Single stable critical
        (0, 1, 0.0, "single_stable_critical"),
        # ZERO_INPUT: Single flaky critical
        (1, 1, 1.0, "single_flaky_critical"),
        # BOUNDARY: At threshold (10%)
        (1, 10, 0.1, "at_threshold"),
        # BOUNDARY: Below threshold
        (1, 11, 0.090909, "below_threshold"),
        # BOUNDARY: Above threshold
        (1, 9, 0.111111, "above_threshold"),
        # EXTREME: Large batch at threshold
        (10, 100, 0.1, "large_batch"),
    ]


def generate_flaky_velocity_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for flaky_velocity metric.

    New flaky tests per day in 7-day window.
    Valid range: [0, ∞], threshold > 1.0

    Covers:
    - No new tests
    - Boundary values
    - Short windows
    - High velocity (outbreak)

    Returns:
        List of tuples: (new_flaky_count, window_days, expected_velocity, scenario_name)
    """
    return [
        # ZERO_INPUT: No new tests
        (0, 7, 0.0, "no_new_tests"),
        # BOUNDARY: One per week
        (1, 7, 0.142857, "one_per_week"),
        # BOUNDARY: At threshold (1 per day)
        (7, 7, 1.0, "at_threshold_1_per_day"),
        # BOUNDARY: Above threshold
        (8, 7, 1.142857, "above_threshold"),
        # EXTREME: One per day (short window)
        (1, 1, 1.0, "one_per_day"),
        # EXTREME: Outbreak (5 per day)
        (10, 2, 5.0, "outbreak"),
    ]


def generate_repository_health_score_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for repository_health_score metric.

    Composite health score from multiple factors.
    Valid range: [0, 1], threshold > 0.7 (degraded)

    Formula:
        health = (1.0 - flaky_pct/0.1) - growth_penalty - critical_penalty - unknown_penalty
        clamped to [0, 1]

    Covers:
    - Perfect health
    - All inputs zero
    - Boundary at threshold
    - All issues combined

    Returns:
        List of tuples:
            (flaky_pct, growth_rate, critical_ratio, unknown_ratio, expected_health, scenario_name)
    """
    return [
        # ZERO_INPUT: Perfect health
        (0.0, 0.0, 0.0, 0.0, 1.0, "perfect_health"),
        # BOUNDARY: With flakiness (5%)
        (0.05, 0.0, 0.0, 0.0, 0.5, "with_flakiness_5pct"),
        # BOUNDARY: At limit (10%)
        (0.10, 0.0, 0.0, 0.0, 0.0, "at_limit_10pct"),
        # VALID: With growth penalty (0.3 > 0.2 threshold → score = 0.5 - 0.1 = 0.4)
        (0.05, 0.3, 0.0, 0.0, 0.4, "with_growth_penalty"),
        # VALID: With critical penalty (flaky=0.06→score=0.4, critical 0.2 > 0.1 → 0.4-0.1 = 0.3)
        (0.06, 0.0, 0.2, 0.0, 0.3, "with_critical_penalty"),
        # VALID: With unknown penalty (0.6 > 0.5 threshold → score = 0.5 - 0.15 = 0.35)
        (0.05, 0.0, 0.0, 0.6, 0.35, "with_unknown_penalty"),
        # EXTREME: All issues (clamped to 0)
        (0.20, 0.5, 0.2, 1.0, 0.0, "all_issues_critical"),
    ]


# ============================================================================
# Helper Functions for Test Data Creation
# ============================================================================


def create_test_results_sequence(
    pattern: str, count: int, nodeid: str = "test::test_method"
) -> list[FlakyTestResult]:
    """Create a sequence of test results following a pattern.

    Args:
        pattern: One of 'all_pass', 'all_fail', 'alternating', 'mostly_pass', 'mostly_fail'
        count: Number of results to generate
        nodeid: Test node ID to use for all results

    Returns:
        List of FlakyTestResult objects with the specified pattern

    Example:
        results = create_test_results_sequence('alternating', 10)
        assert len(results) == 10
        assert results[0].outcome == TestOutcome.PASSED
        assert results[1].outcome == TestOutcome.FAILED
    """
    outcomes_map = {
        "all_pass": ["passed"] * count,
        "all_fail": ["failed"] * count,
        "alternating": ["passed" if i % 2 == 0 else "failed" for i in range(count)],
        "mostly_pass": ["passed"] * (count - 1) + ["failed"],
        "mostly_fail": ["failed"] * (count - 1) + ["passed"],
    }

    outcomes = outcomes_map.get(pattern, ["passed"] * count)

    return [
        FlakyTestResult(
            nodeid=nodeid,
            outcome=outcome,
            duration=1.0 + (i * 0.1),
        )
        for i, outcome in enumerate(outcomes)
    ]


def apply_floating_point_error(value: float, epsilon: float = 1e-6) -> float:
    """Apply small floating-point error to test precision handling.

    Args:
        value: The value to perturb
        epsilon: The amount to perturb (default: 1e-6)

    Returns:
        Value with small error applied

    Example:
        perturbed = apply_floating_point_error(0.5)
        assert abs(perturbed - 0.5) < 1e-5
    """
    return value + epsilon if value > 0 else value
