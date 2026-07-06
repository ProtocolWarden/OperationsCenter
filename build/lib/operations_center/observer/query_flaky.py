# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Flaky-test query types and mixin for TestSignalQuery.

Extracted from query.py to keep that module under the 500-line limit.
FlakyTestQueryMixin adds get_flaky_tests, get_test_metrics, get_repository_health,
and filter_by_category to any class that exposes _load_snapshots_in_range and
_get_recent_snapshots helpers.

Note: FlakyTest / FlakyTestMetrics / RepositoryHealth here are lightweight
query-result projections read from snapshot signals. They are NOT the flaky-test
*detection* subsystem's domain models — for those (FlakyTestMetric, FlakyTestResult,
FlakyTestSessionReport, with flakiness_score / confidence / pattern_entropy and
serialization) see flaky_test_models.py. Mind the singular/plural: FlakyTestMetric
(detection) vs FlakyTestMetrics (this aggregate view).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operations_center.observer.models import RepoStateSnapshot

# Bare exception type names produced by parse_non_assertion_exception() when
# the exception carries no args — these are placeholders, not diagnostic content.
_BARE_EXCEPTION_TYPE_NAMES: frozenset[str] = frozenset(
    {"TimeoutError", "ConnectionError", "OSError"}
)
# Minimum character count for an assertion message to be considered informative.
_MESSAGE_QUALITY_MIN_LENGTH: int = 10


@dataclass
class FlakyTest:
    """A single flaky test result from a query.

    Attributes:
        name: Test node ID (full path including parameters)
        failure_rate: Fraction of runs where test failed (0.0-1.0)
        run_count: Number of runs analyzed
        category: Flakiness category (INTERMITTENT, ENVIRONMENT, INFRASTRUCTURE, UNKNOWN)
        last_failed: Timestamp of most recent failure (or None)
        test_name: Extracted test function name (e.g., "test_foo" from full path)
        assertion_message: Most recent assertion message from failures (max 200 chars)
    """

    name: str
    failure_rate: float
    run_count: int
    category: str | None = None
    last_failed: datetime | None = None
    test_name: str | None = None
    assertion_message: str | None = None


@dataclass
class FlakyTestMetrics:
    """Aggregated metrics for flaky tests in a repository.

    Attributes:
        total_flaky_tests: Number of tests with failure_rate > 10%
        unstable_tests: Number of tests with 5-10% failure rate
        critical_tests: Number of tests with failure_rate > 50%
        affected_modules: Set of modules/packages with flaky tests
        average_failure_rate: Mean failure rate across flaky tests
        most_problematic: Top 5 flakiest tests
        trend: Change in flaky test count over time period
    """

    total_flaky_tests: int = 0
    unstable_tests: int = 0
    critical_tests: int = 0
    affected_modules: list[str] = dataclass_field(default_factory=list)
    average_failure_rate: float = 0.0
    most_problematic: list[FlakyTest] = dataclass_field(default_factory=list)
    trend: float = 0.0


@dataclass
class RepositoryHealth:
    """Overall repository health based on all signals.

    Attributes:
        status: Overall health status (HEALTHY, NOMINAL, DEGRADED, CRITICAL)
        flaky_test_percent: Percentage of tests that are flaky
        recovery_rate: Percentage of previously flaky tests now stable
        failure_rate_trend: Change in failure rate vs previous period
        affected_modules_count: Number of modules with issues
        estimated_ci_impact_percent: Estimated CI slowdown due to flakiness
        last_improved: Timestamp of most recent improvement
    """

    status: str = "NOMINAL"
    flaky_test_percent: float = 0.0
    recovery_rate: float = 0.0
    failure_rate_trend: float = 0.0
    affected_modules_count: int = 0
    estimated_ci_impact_percent: float = 0.0
    last_improved: datetime | None = None


@dataclass
class ExtractionHealth:
    """Health metrics for test extraction coverage.

    Attributes:
        success_rate: Percentage of flaky tests with complete extraction (0.0-100.0)
        complete_extraction: Number of tests with both test_name and assertion_message
        partial_extraction: Number of tests with only test_name or assertion_message
        no_extraction: Number of tests with neither field
        edge_case_summary: Dict describing encountered edge cases
            - truncated_messages: count of assertion messages truncated to 200 chars
            - special_chars: count of messages containing special characters
            - malformed_exceptions: count of non-standard exception formats
        gaps: Sample list (up to 10) of pytest node IDs where both test_name and
            assertion_message are None — the full count is in ``no_extraction``
        edge_cases: Sample list (up to 10) of dicts with keys ``test_id`` and
            ``issue`` (one of "truncated_message", "special_chars",
            "malformed_exception") — full counts are in ``edge_case_summary``
        message_quality_rate: Percentage of extracted assertion messages that are
            informative (0.0-100.0), or None when no tests carry an
            assertion_message. An informative message is non-empty, at least
            ``_MESSAGE_QUALITY_MIN_LENGTH`` characters, and not a bare exception
            type name such as "TimeoutError".
        low_quality_messages: Sample list (up to 10) of dicts with keys
            ``test_id`` (pytest node ID) and ``reason`` (one of "empty",
            "too_short", "bare_exception_type") for messages classified as
            low-quality. Full count is derivable from
            ``message_quality_rate`` and the tests-with-assertion denominator.
    """

    success_rate: float = 0.0
    complete_extraction: int = 0
    partial_extraction: int = 0
    no_extraction: int = 0
    edge_case_summary: dict[str, int] = dataclass_field(default_factory=dict)
    gaps: list[str] = dataclass_field(default_factory=list)
    edge_cases: list[dict] = dataclass_field(default_factory=list)
    message_quality_rate: float | None = None
    low_quality_messages: list[dict] = dataclass_field(default_factory=list)


class FlakyTestQueryMixin(ABC):
    """Mixin that adds flaky-test query methods to TestSignalQuery.

    Requires the host class to expose:
        _load_snapshots_in_range(timerange) -> list[RepoStateSnapshot]
        _get_recent_snapshots(count)        -> list[RepoStateSnapshot]
    """

    @abstractmethod
    def _load_snapshots_in_range(self, timerange: Any) -> list[RepoStateSnapshot]: ...

    @abstractmethod
    def _get_recent_snapshots(self, count: int) -> list[RepoStateSnapshot]: ...

    def get_flaky_tests(self, timerange: Any | None = None) -> list[FlakyTest]:
        """Get all flaky tests detected in a time range.

        Args:
            timerange: TimeRange for analysis. If None, uses most recent snapshot only.

        Returns:
            List of FlakyTest objects, sorted by failure_rate descending.
            Empty list if no flaky tests found or no snapshots available.
        """
        if timerange:
            snapshots = self._load_snapshots_in_range(timerange)
        else:
            snapshots = self._get_recent_snapshots(1)

        flaky_tests: list[FlakyTest] = []
        for snapshot in snapshots:
            signal = snapshot.signals.flaky_test_signal
            if signal.status == "unavailable" or not signal.most_problematic_tests:
                continue

            for test_dict in signal.most_problematic_tests:
                flaky_test = FlakyTest(
                    name=test_dict.get("name", "unknown"),
                    failure_rate=test_dict.get("failure_rate", 0.0),
                    run_count=test_dict.get("run_count", 0),
                    category=test_dict.get("category"),
                    last_failed=None,
                    test_name=test_dict.get("test_name"),
                    assertion_message=test_dict.get("assertion_message"),
                )
                flaky_tests.append(flaky_test)

        flaky_tests.sort(key=lambda t: t.failure_rate, reverse=True)
        return flaky_tests

    def get_test_metrics(self, timerange: Any | None = None) -> FlakyTestMetrics | None:
        """Get aggregated flaky test metrics for a repository.

        Args:
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            FlakyTestMetrics with aggregated statistics, or None if no data available.
        """
        if timerange:
            snapshots = self._load_snapshots_in_range(timerange)
        else:
            snapshots = self._get_recent_snapshots(1)

        if not snapshots:
            return None

        metrics = FlakyTestMetrics()
        seen_tests: set[str] = set()
        all_flaky: list[FlakyTest] = []

        for snapshot in snapshots:
            signal = snapshot.signals.flaky_test_signal
            if signal.status == "unavailable":
                continue

            # Current-state scalars reflect the most recent available snapshot in
            # range (last write wins) — counts cannot be summed across snapshots
            # without double-counting tests present in more than one.
            metrics.total_flaky_tests = signal.flaky_test_count or 0
            metrics.unstable_tests = signal.unstable_test_count or 0
            metrics.affected_modules = signal.affected_modules or []
            metrics.trend = signal.failure_rate_trend or 0.0

            if signal.most_problematic_tests:
                for test_dict in signal.most_problematic_tests:
                    test_name = test_dict.get("name", "unknown")
                    if test_name not in seen_tests:
                        seen_tests.add(test_name)
                        all_flaky.append(
                            FlakyTest(
                                name=test_name,
                                failure_rate=test_dict.get("failure_rate", 0.0),
                                run_count=test_dict.get("run_count", 0),
                                category=test_dict.get("category"),
                                test_name=test_dict.get("test_name"),
                                assertion_message=test_dict.get("assertion_message"),
                            )
                        )

        # critical_tests derives from the same deduplicated set as most_problematic
        # so the two never disagree and a test seen across snapshots is counted once.
        metrics.critical_tests = sum(1 for t in all_flaky if t.failure_rate > 0.5)

        if all_flaky:
            metrics.average_failure_rate = sum(t.failure_rate for t in all_flaky) / len(all_flaky)
            all_flaky.sort(key=lambda t: t.failure_rate, reverse=True)
            metrics.most_problematic = all_flaky[:5]

        return metrics if metrics.total_flaky_tests > 0 else None

    def get_repository_health(self, timerange: Any | None = None) -> RepositoryHealth:
        """Get overall repository health assessment.

        Args:
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            RepositoryHealth with overall status and key metrics.
        """
        if timerange:
            snapshots = self._load_snapshots_in_range(timerange)
        else:
            snapshots = self._get_recent_snapshots(1)

        health = RepositoryHealth()

        if not snapshots:
            health.status = "NOMINAL"
            return health

        latest = snapshots[-1]
        flaky_signal = latest.signals.flaky_test_signal

        if flaky_signal.status != "unavailable":
            # flaky_test_percent is a true percentage of the suite (0-100):
            # flaky_test_count / total_test_count * 100, per the Stage 0 spec.
            # Falls back to 0.0 when the suite size is unknown (no division by zero).
            flaky_count = flaky_signal.flaky_test_count or 0
            test_signal = latest.signals.test_signal
            total_tests = (test_signal.test_count or 0) if test_signal is not None else 0
            health.flaky_test_percent = (
                (flaky_count / total_tests) * 100.0 if total_tests > 0 else 0.0
            )
            health.recovery_rate = flaky_signal.recovery_rate or 0.0
            health.failure_rate_trend = flaky_signal.failure_rate_trend or 0.0
            health.affected_modules_count = len(flaky_signal.affected_modules or [])

            estimated_impact = flaky_signal.estimated_impact or {}
            health.estimated_ci_impact_percent = estimated_impact.get("ci_slowdown_percent", 0.0)

        # Thresholds are in percentage points: >5% critical, >2% degraded.
        if health.flaky_test_percent > 5.0:
            health.status = "CRITICAL"
        elif health.flaky_test_percent > 2.0 or health.failure_rate_trend > 1.0:
            health.status = "DEGRADED"
        else:
            health.status = "HEALTHY" if health.flaky_test_percent == 0 else "NOMINAL"

        return health

    def filter_by_category(self, category: str, timerange: Any | None = None) -> list[FlakyTest]:
        """Get flaky tests filtered by flakiness category.

        Args:
            category: Flakiness category to filter by (case-insensitive)
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            List of FlakyTest objects matching the category, sorted by failure_rate.
            Empty list if no tests match or no snapshots available.
        """
        flaky_tests = self.get_flaky_tests(timerange)
        category_upper = category.upper()

        filtered = [t for t in flaky_tests if t.category and t.category.upper() == category_upper]
        return sorted(filtered, key=lambda t: t.failure_rate, reverse=True)

    def filter_by_test_name(self, test_name: str, timerange: Any | None = None) -> list[FlakyTest]:
        """Get flaky tests filtered by extracted test name.

        Args:
            test_name: Test function name to filter by (case-insensitive, substring match)
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            List of FlakyTest objects matching the test name, sorted by failure_rate.
            Empty list if no tests match or no snapshots available.
        """
        flaky_tests = self.get_flaky_tests(timerange)
        test_name_lower = test_name.lower()

        filtered = [
            t for t in flaky_tests if t.test_name and test_name_lower in t.test_name.lower()
        ]
        return sorted(filtered, key=lambda t: t.failure_rate, reverse=True)

    def get_assertion_messages(self, timerange: Any | None = None) -> dict[str, list[str]]:
        """Get aggregated assertion messages grouped by test name.

        Returns a mapping of extracted test names to their assertion messages,
        enabling autonomy systems to understand common failure patterns.

        Args:
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            Dict mapping test names to list of assertion messages (deduplicated).
            Empty dict if no flaky tests found or no snapshots available.
        """
        flaky_tests = self.get_flaky_tests(timerange)
        messages_by_test: dict[str, set[str]] = {}

        for test in flaky_tests:
            if test.test_name and test.assertion_message:
                if test.test_name not in messages_by_test:
                    messages_by_test[test.test_name] = set()
                messages_by_test[test.test_name].add(test.assertion_message)

        return {name: sorted(msgs) for name, msgs in messages_by_test.items()}

    def get_extraction_health(self, timerange: Any | None = None) -> ExtractionHealth:
        """Assess extraction coverage for flaky test data.

        Analyzes how many flaky tests have extracted test_name and assertion_message
        fields, identifying gaps in observability.

        Args:
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            ExtractionHealth with coverage metrics and edge case summary.
        """
        flaky_tests = self.get_flaky_tests(timerange)

        if not flaky_tests:
            return ExtractionHealth()

        complete = 0
        partial = 0
        missing = 0
        edge_case_counts: dict[str, int] = {
            "truncated_messages": 0,
            "special_chars": 0,
            "malformed_exceptions": 0,
        }
        gap_samples: list[str] = []
        edge_case_samples: list[dict] = []
        quality_with_message = 0
        quality_informative = 0
        low_quality_samples: list[dict] = []

        for test in flaky_tests:
            has_test_name = test.test_name is not None
            has_assertion = test.assertion_message is not None

            if has_test_name and has_assertion:
                complete += 1
            elif has_test_name or has_assertion:
                partial += 1
            else:
                missing += 1
                if len(gap_samples) < 10:
                    gap_samples.append(test.name)

            if test.assertion_message is not None:
                msg = test.assertion_message
                if msg and len(msg) >= 200:
                    edge_case_counts["truncated_messages"] += 1
                    if len(edge_case_samples) < 10:
                        edge_case_samples.append(
                            {"test_id": test.name, "issue": "truncated_message"}
                        )
                if msg and any(ord(c) < 32 or ord(c) > 126 for c in msg if c not in "\n\r\t"):
                    edge_case_counts["special_chars"] += 1
                    if len(edge_case_samples) < 10:
                        edge_case_samples.append({"test_id": test.name, "issue": "special_chars"})

                # Quality check: is this message informative?
                quality_with_message += 1
                quality_reason: str | None = None
                if not msg:
                    quality_reason = "empty"
                elif msg in _BARE_EXCEPTION_TYPE_NAMES:
                    quality_reason = "bare_exception_type"
                elif len(msg) < _MESSAGE_QUALITY_MIN_LENGTH:
                    quality_reason = "too_short"

                if quality_reason is None:
                    quality_informative += 1
                elif len(low_quality_samples) < 10:
                    low_quality_samples.append({"test_id": test.name, "reason": quality_reason})

        total = len(flaky_tests)
        success_rate = ((complete + partial) / total * 100.0) if total > 0 else 0.0
        message_quality_rate: float | None = (
            quality_informative / quality_with_message * 100.0 if quality_with_message > 0 else None
        )

        return ExtractionHealth(
            success_rate=success_rate,
            complete_extraction=complete,
            partial_extraction=partial,
            no_extraction=missing,
            edge_case_summary=edge_case_counts,
            gaps=gap_samples,
            edge_cases=edge_case_samples,
            message_quality_rate=message_quality_rate,
            low_quality_messages=low_quality_samples,
        )

    def filter_by_extraction_status(
        self, status: str, timerange: Any | None = None
    ) -> list[FlakyTest]:
        """Filter flaky tests by extraction data availability.

        Enables querying tests based on how much extraction data is available,
        useful for identifying gaps in observability.

        Args:
            status: Extraction status to filter by.
                - "complete": both test_name and assertion_message present
                - "partial": only one of test_name or assertion_message present
                - "missing": neither test_name nor assertion_message present
            timerange: TimeRange for analysis. If None, uses most recent snapshot.

        Returns:
            List of FlakyTest objects matching the status, sorted by failure_rate.
            Empty list if no tests match or status is invalid.

        Raises:
            ValueError: If status is not one of "complete", "partial", "missing".
        """
        valid_statuses = ("complete", "partial", "missing")
        if status.lower() not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        flaky_tests = self.get_flaky_tests(timerange)
        filtered: list[FlakyTest] = []

        for test in flaky_tests:
            has_test_name = test.test_name is not None
            has_assertion = test.assertion_message is not None

            test_status = "missing"
            if has_test_name and has_assertion:
                test_status = "complete"
            elif has_test_name or has_assertion:
                test_status = "partial"

            if test_status == status.lower():
                filtered.append(test)

        return sorted(filtered, key=lambda t: t.failure_rate, reverse=True)
