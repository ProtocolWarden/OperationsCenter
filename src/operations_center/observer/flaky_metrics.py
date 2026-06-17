# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pure, validated computations for flaky-test metrics.

This module is the *correct* implementation of the per-test and repository-level
flakiness metrics. Each function is a pure function over explicit inputs with
well-defined edge-case behaviour (division by zero, empty input, no variation),
so the results are testable against derived ground truth rather than hand-typed
constants.

Per-test metrics operate on a single test's run history; repository-level metrics
operate on aggregates across the suite. Two per-test metrics —
:func:`environment_correlation` and :func:`isolation_score` — require inputs that
the current pytest collector does not yet capture (per-run environment vectors and
serial-vs-parallel failure counts). They are implemented and tested as pure
functions so the computation is correct and ready; wiring them needs a collector
that records those inputs (tracked separately).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# ── Per-test metrics ─────────────────────────────────────────────────────────


def failure_rate(failures: int, total_runs: int) -> float:
    """Fraction of runs that failed, in [0, 1]. Zero runs → 0.0."""
    if total_runs <= 0:
        return 0.0
    return failures / total_runs


def failure_entropy(pass_count: int, fail_count: int) -> float:
    """Normalised binary Shannon entropy of the pass/fail distribution, in [0, 1].

    H = -(p·log2 p + q·log2 q). Already normalised because a 2-outcome
    distribution has maximum entropy of exactly 1 bit (at a 50/50 split). A
    deterministic test (all pass or all fail) has entropy 0.
    """
    total = pass_count + fail_count
    if total <= 0 or pass_count <= 0 or fail_count <= 0:
        return 0.0
    p = pass_count / total
    q = fail_count / total
    return -(p * math.log2(p) + q * math.log2(q))


def streak_variance(outcomes: Sequence[bool]) -> float | None:
    """Population variance of consecutive same-outcome streak lengths, normalised
    by the mean streak length (a coefficient-of-dispersion form).

    ``outcomes`` is the chronological pass/fail sequence (True = pass). Returns
    ``None`` for fewer than two runs (undefined). A single uninterrupted streak
    and a perfectly alternating sequence both yield 0.0 (no dispersion).
    """
    if len(outcomes) < 2:
        return None
    streaks: list[int] = []
    current = 1
    for prev, cur in zip(outcomes, outcomes[1:]):
        if cur == prev:
            current += 1
        else:
            streaks.append(current)
            current = 1
    streaks.append(current)
    mean = sum(streaks) / len(streaks)
    if mean == 0:
        return 0.0
    variance = sum((s - mean) ** 2 for s in streaks) / len(streaks)
    return variance / mean


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolation percentile (pct in [0, 100]); ``None`` if empty."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(ordered[lo])
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def recovery_time_percentile_90(recovery_times: Sequence[float]) -> float | None:
    """90th-percentile recovery time (runs/days from a failure to the next pass).

    ``recovery_times`` is the list of observed recovery times; never-recovered
    failures should be passed as ``math.inf``. ``None`` when there were no
    failures to recover from.
    """
    return percentile(recovery_times, 90.0)


def duration_stability(durations: Sequence[float]) -> float | None:
    """Coefficient of variation (stddev / mean) of run durations.

    0.0 for a single run or perfectly identical durations. ``None`` when the
    mean is 0 (all-zero durations → undefined, no division by zero).
    """
    if not durations:
        return None
    n = len(durations)
    mean = sum(durations) / n
    if mean == 0:
        return None
    variance = sum((d - mean) ** 2 for d in durations) / n
    return math.sqrt(variance) / mean


def environment_correlation(failures: Sequence[float], env_values: Sequence[float]) -> float | None:
    """Pearson correlation between per-run failure indicators and an environment
    metric, in [-1, 1].

    Returns ``None`` for empty/mismatched input. Returns 0.0 when either series
    has no variation (correlation undefined → treated as "no relationship").
    """
    n = len(failures)
    if n == 0 or n != len(env_values):
        return None
    mean_f = sum(failures) / n
    mean_e = sum(env_values) / n
    cov = sum((f - mean_f) * (e - mean_e) for f, e in zip(failures, env_values))
    var_f = sum((f - mean_f) ** 2 for f in failures)
    var_e = sum((e - mean_e) ** 2 for e in env_values)
    if var_f == 0 or var_e == 0:
        return 0.0
    return cov / math.sqrt(var_f * var_e)


def isolation_score(serial_failures: int, parallel_failures: int) -> float:
    """How much a test's failures are explained by parallel execution.

    ``1 - parallel_failures / serial_failures``: 1.0 = fully isolated (fails the
    same or less in parallel), values toward 0 = order/parallelism-sensitive, and
    negative = fails *more* in parallel than serial (anomalous). With no serial
    failures: 1.0 if it never fails at all, else 0.0 (parallel-only failures are
    the worst isolation).
    """
    if serial_failures == 0:
        return 1.0 if parallel_failures == 0 else 0.0
    return 1.0 - parallel_failures / serial_failures


# ── Repository-level metrics ─────────────────────────────────────────────────


def flaky_test_percentage(flaky_count: int, total_tests: int) -> float:
    """Fraction of the suite that is flaky, in [0, 1]. No tests → 0.0."""
    if total_tests <= 0:
        return 0.0
    return flaky_count / total_tests


def median_failure_rate(failure_rates: Sequence[float]) -> float:
    """Median failure rate across flaky tests; 0.0 when there are none."""
    if not failure_rates:
        return 0.0
    ordered = sorted(failure_rates)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def flaky_growth_rate(previous_count: int, current_count: int) -> float:
    """Relative change in flaky-test count: ``(cur - prev) / prev``.

    No previous flaky tests: 0.0 if there are still none, else ``inf`` (growth
    from zero is unbounded).
    """
    if previous_count == 0:
        return 0.0 if current_count == 0 else math.inf
    return (current_count - previous_count) / previous_count


def category_concentration(category_counts: dict[str, int]) -> float:
    """Share of flaky tests in the single largest root-cause category, in [0, 1].

    1.0 = all in one category, → 0 = evenly spread. Empty → 0.0.
    """
    total = sum(category_counts.values())
    if total <= 0:
        return 0.0
    return max(category_counts.values()) / total


def critical_test_flakiness_ratio(critical_flaky: int, total_critical: int) -> float:
    """Fraction of critical-path tests that are flaky, in [0, 1]. None → 0.0."""
    if total_critical <= 0:
        return 0.0
    return critical_flaky / total_critical


def flaky_velocity(new_flaky_count: int, window_days: float) -> float:
    """Newly-detected flaky tests per day over the window. Non-positive window → 0.0."""
    if window_days <= 0:
        return 0.0
    return new_flaky_count / window_days


def repository_health_score(
    flaky_pct: float,
    growth_rate: float,
    critical_ratio: float,
    unknown_ratio: float,
) -> float:
    """Composite suite-health score in [0, 1] (1.0 = healthy).

    Starts from flakiness headroom against a 10%-flaky ceiling and subtracts
    penalties for flaky growth, flakiness on critical-path tests, and
    uncategorised (hard-to-diagnose) flakiness::

        health = (1 - flaky_pct / 0.10)
                 - 0.5 · growth_rate
                 - 2.0 · critical_ratio
                 - 0.3 · unknown_ratio

    clamped to [0, 1].
    """
    score = (
        (1.0 - flaky_pct / 0.10) - 0.5 * growth_rate - 2.0 * critical_ratio - 0.3 * unknown_ratio
    )
    return max(0.0, min(1.0, score))
