# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Validated tests for flaky_metrics.

Expected values are derived from the metric definitions (and cross-checked against
independent computation), not hand-typed approximations. Notably, the binary
Shannon entropy of a 1/99 split is 0.080793 bits — an earlier, reverted test suite
asserted 0.081296, which does not match any correct entropy computation.
"""

from __future__ import annotations

import math

import pytest

from operations_center.observer import flaky_metrics as m

P, F = True, False  # pass / fail


# ── failure_rate ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "failures,total,expected",
    [
        (0, 0, 0.0),
        (0, 1, 0.0),
        (1, 1, 1.0),
        (1, 20, 0.05),
        (1, 2, 0.5),
        (9999, 10000, 0.9999),
    ],
)
def test_failure_rate(failures, total, expected):
    assert m.failure_rate(failures, total) == pytest.approx(expected)


# ── failure_entropy ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "passes,fails,expected",
    [
        (10, 0, 0.0),  # deterministic
        (0, 10, 0.0),
        (5, 5, 1.0),  # maximum entropy at 50/50
        (1, 0, 0.0),
        (0, 0, 0.0),
        (1, 99, 0.0807931),  # NOT 0.081296 (the reverted suite's wrong value)
        (99, 1, 0.0807931),  # symmetric
        (10, 1, 0.4394970),
    ],
)
def test_failure_entropy(passes, fails, expected):
    assert m.failure_entropy(passes, fails) == pytest.approx(expected, abs=1e-6)


def test_failure_entropy_in_unit_range():
    for passes in range(0, 11):
        for fails in range(0, 11):
            assert 0.0 <= m.failure_entropy(passes, fails) <= 1.0 + 1e-9


# ── streak_variance ──────────────────────────────────────────────────────────
def test_streak_variance_single_run_undefined():
    assert m.streak_variance([P]) is None


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        ([P, P, P, P, P], 0.0),  # one streak → no dispersion
        ([F, F, F, F, F], 0.0),
        ([P, F] * 5, 0.0),  # all streaks length 1
        ([P, P, P, F, F], 0.1),  # streaks [3,2]: var .25 / mean 2.5
    ],
)
def test_streak_variance(outcomes, expected):
    assert m.streak_variance(outcomes) == pytest.approx(expected)


def test_streak_variance_mixed():
    # streaks [5,1,5]: mean 11/3, var = ((5-11/3)^2*2 + (1-11/3)^2)/3
    mean = 11 / 3
    var = ((5 - mean) ** 2 * 2 + (1 - mean) ** 2) / 3
    assert m.streak_variance([P] * 5 + [F] + [P] * 5) == pytest.approx(var / mean)


# ── recovery_time_percentile_90 ──────────────────────────────────────────────
def test_recovery_p90_empty_is_none():
    assert m.recovery_time_percentile_90([]) is None


def test_recovery_p90_single():
    assert m.recovery_time_percentile_90([5.0]) == 5.0


def test_recovery_p90_linear():
    # p90 of 1..10 by linear interpolation: rank 8.1 → 9*0.9 + 10*0.1
    assert m.recovery_time_percentile_90([float(i) for i in range(1, 11)]) == pytest.approx(9.1)


def test_recovery_p90_with_never_recovered():
    assert m.recovery_time_percentile_90([1.0, 2.0, math.inf, math.inf]) == math.inf


# ── duration_stability ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "durations,expected",
    [
        ([1.0, 1.0, 1.0], 0.0),
        ([2.0], 0.0),
        ([1.0, 2.0, 3.0, 4.0, 5.0], math.sqrt(2.0) / 3.0),  # std √2, mean 3
    ],
)
def test_duration_stability(durations, expected):
    assert m.duration_stability(durations) == pytest.approx(expected)


def test_duration_stability_all_zero_is_none():
    assert m.duration_stability([0.0, 0.0, 0.0]) is None


def test_duration_stability_empty_is_none():
    assert m.duration_stability([]) is None


# ── environment_correlation ──────────────────────────────────────────────────
def test_env_corr_perfect_positive():
    f = [0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert m.environment_correlation(f, list(f)) == pytest.approx(1.0)


def test_env_corr_perfect_negative():
    f = [1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
    e = [0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert m.environment_correlation(f, e) == pytest.approx(-1.0)


def test_env_corr_no_variation_is_zero():
    assert m.environment_correlation([1, 1, 1], [1, 1, 1]) == 0.0
    assert m.environment_correlation([0, 0, 0], [1, 2, 3]) == 0.0


def test_env_corr_empty_or_mismatched_is_none():
    assert m.environment_correlation([], []) is None
    assert m.environment_correlation([1, 0], [1]) is None


# ── isolation_score ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "serial,parallel,expected",
    [
        (0, 0, 1.0),
        (10, 0, 1.0),
        (0, 10, 0.0),
        (10, 10, 0.0),
        (10, 5, 0.5),
        (5, 10, -1.0),
    ],
)
def test_isolation_score(serial, parallel, expected):
    assert m.isolation_score(serial, parallel) == pytest.approx(expected)


# ── repository-level ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "flaky,total,expected",
    [(0, 0, 0.0), (0, 1, 0.0), (1, 1, 1.0), (5, 100, 0.05), (1, 10000, 0.0001)],
)
def test_flaky_test_percentage(flaky, total, expected):
    assert m.flaky_test_percentage(flaky, total) == pytest.approx(expected)


@pytest.mark.parametrize(
    "rates,expected",
    [
        ([], 0.0),
        ([0.1], 0.1),
        ([0.1, 0.2], 0.15),
        ([0.1, 0.2, 0.3], 0.2),
        ([0.01, 0.5, 0.99], 0.5),
    ],
)
def test_median_failure_rate(rates, expected):
    assert m.median_failure_rate(rates) == pytest.approx(expected)


@pytest.mark.parametrize(
    "prev,cur,expected",
    [
        (0, 0, 0.0),
        (1, 1, 0.0),
        (10, 12, 0.2),
        (10, 8, -0.2),
        (10, 0, -1.0),
        (5, 10, 1.0),
    ],
)
def test_flaky_growth_rate(prev, cur, expected):
    assert m.flaky_growth_rate(prev, cur) == pytest.approx(expected)


def test_flaky_growth_rate_from_zero_is_inf():
    assert m.flaky_growth_rate(0, 1) == math.inf


@pytest.mark.parametrize(
    "counts,expected",
    [
        ({}, 0.0),
        ({"intermittent": 1}, 1.0),
        ({"a": 1, "b": 1, "c": 1, "d": 1}, 0.25),
        ({"a": 6, "b": 4}, 0.6),
        ({"a": 1000, "b": 1}, 1000 / 1001),
    ],
)
def test_category_concentration(counts, expected):
    assert m.category_concentration(counts) == pytest.approx(expected)


@pytest.mark.parametrize(
    "crit_flaky,total_crit,expected",
    [(0, 0, 0.0), (0, 1, 0.0), (1, 1, 1.0), (1, 10, 0.1), (1, 11, 1 / 11), (10, 100, 0.1)],
)
def test_critical_test_flakiness_ratio(crit_flaky, total_crit, expected):
    assert m.critical_test_flakiness_ratio(crit_flaky, total_crit) == pytest.approx(expected)


@pytest.mark.parametrize(
    "new,window,expected",
    [(0, 7, 0.0), (1, 7, 1 / 7), (7, 7, 1.0), (10, 2, 5.0), (1, 0, 0.0)],
)
def test_flaky_velocity(new, window, expected):
    assert m.flaky_velocity(new, window) == pytest.approx(expected)


@pytest.mark.parametrize(
    "flaky_pct,growth,critical,unknown,expected",
    [
        (0.0, 0.0, 0.0, 0.0, 1.0),
        (0.05, 0.0, 0.0, 0.0, 0.5),
        (0.10, 0.0, 0.0, 0.0, 0.0),
        (0.05, 0.2, 0.0, 0.0, 0.4),
        (0.05, 0.0, 0.1, 0.0, 0.3),
        (0.05, 0.0, 0.0, 0.5, 0.35),
        (0.20, 0.5, 0.2, 1.0, 0.0),  # clamped
    ],
)
def test_repository_health_score(flaky_pct, growth, critical, unknown, expected):
    assert m.repository_health_score(flaky_pct, growth, critical, unknown) == pytest.approx(
        expected
    )
