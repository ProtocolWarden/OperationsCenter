# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Data models for the flaky test detection system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


class FlakynessCategory(Enum):
    """Root cause categories for flaky tests."""

    INTERMITTENT = "intermittent"
    ENVIRONMENT = "environment"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class TestOutcome(Enum):
    """Test outcome values from pytest."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"


@dataclass
class FlakyTestMetric:
    """Structured metrics for a single flaky test.

    Attributes:
        nodeid: Full pytest test path (e.g., "tests/unit/test_foo.py::TestClass::test_method").
        failure_rate: Proportion of failed runs [0.0, 1.0].
        run_count: Total number of test executions analyzed.
        test_name: Test function name extracted from nodeid or pytest Item (e.g., "test_method").
        assertion_message: Last assertion message when test failed, empty if test hasn't failed.
        retry_success_count: Number of successful retries after initial failure.
        duration_mean: Mean execution duration in seconds.
        duration_variance: Variance of execution duration.
        pattern_entropy: Entropy of pass/fail pattern [0.0, 1.0], measures randomness.
        streak_length: Length of current failure streak.
        recovery_time_days: Days since last failure (None if test still failing).
        suspected_category: Primary root cause category (intermittent, environment, infrastructure, unknown).
        markers: pytest markers applied to test.
        last_failure_reason: String representation of most recent failure.
        flakiness_score: Overall flakiness score [0.0, 1.0].
        confidence: Confidence in categorization [0.0, 1.0].
        failure_entropy: Normalized pass/fail entropy [0.0, 1.0].
        streak_variance: Variance of streak lengths, None if undefined.
        duration_stability: Coefficient of variation of durations, None if undefined.
    """

    nodeid: str
    failure_rate: float
    run_count: int
    test_name: str = ""
    assertion_message: str = ""
    retry_success_count: int = 0
    duration_mean: float = 0.0
    duration_variance: float = 0.0
    pattern_entropy: float = 0.0
    streak_length: int = 0
    recovery_time_days: float | None = None
    suspected_category: FlakynessCategory = FlakynessCategory.UNKNOWN
    markers: list[str] = field(default_factory=list)
    last_failure_reason: str = ""
    flakiness_score: float = 0.0
    confidence: float = 0.0
    # Derived flakiness metrics (see observer.flaky_metrics). Normalised
    # pass/fail entropy in [0,1]; dispersion of streak lengths; coefficient of
    # variation of run durations. None where undefined (e.g. <2 runs, zero mean).
    failure_entropy: float = 0.0
    streak_variance: float | None = None
    duration_stability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert metric to dictionary for JSON serialization."""
        return {
            "nodeid": self.nodeid,
            "test_name": self.test_name,
            "failure_rate": round(self.failure_rate, 4),
            "run_count": self.run_count,
            "assertion_message": self.assertion_message,
            "retry_success_count": self.retry_success_count,
            "duration_mean": round(self.duration_mean, 4),
            "duration_variance": round(self.duration_variance, 4),
            "pattern_entropy": round(self.pattern_entropy, 4),
            "streak_length": self.streak_length,
            "recovery_time_days": (
                round(self.recovery_time_days, 2) if self.recovery_time_days is not None else None
            ),
            "suspected_category": self.suspected_category.value,
            "markers": self.markers,
            "last_failure_reason": self.last_failure_reason,
            "flakiness_score": round(self.flakiness_score, 4),
            "confidence": round(self.confidence, 4),
            "failure_entropy": round(self.failure_entropy, 4),
            "streak_variance": (
                round(self.streak_variance, 4) if self.streak_variance is not None else None
            ),
            "duration_stability": (
                round(self.duration_stability, 4) if self.duration_stability is not None else None
            ),
        }


@dataclass
class FlakyTestResult:
    """Result of a single test execution (Tier 1 observation).

    Attributes:
        nodeid: Full pytest test path (e.g., "tests/unit/test_foo.py::TestClass::test_method").
        outcome: Test outcome (PASSED, FAILED, SKIPPED, XFAILED, XPASSED).
        duration: Execution duration in seconds.
        test_name: Test function name extracted from nodeid or pytest Item (e.g., "test_method").
        assertion_message: Assertion message from failure, empty if test passed or failed with non-assertion error.
        markers: pytest markers applied to test.
        exception_type: Exception class name if test failed (e.g., "AssertionError", "TimeoutError").
        exception_message: Exception message or string representation of exception.
        output_lines: Captured stdout/stderr output lines.
        run_id: Unique identifier for this test execution run.
        environment: Environment where test ran (e.g., "local", "ci", "staging").
        python_version: Python version used for test execution.
        timestamp: When the test was executed.
    """

    nodeid: str
    outcome: TestOutcome | str
    duration: float
    test_name: str = ""
    assertion_message: str = ""
    markers: list[str] = field(default_factory=list)
    exception_type: str = ""
    exception_message: str = ""
    output_lines: list[str] = field(default_factory=list)
    run_id: str = ""
    environment: str = "local"
    python_version: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if isinstance(self.outcome, str):
            self.outcome = TestOutcome(self.outcome)
        if not self.run_id:
            self.run_id = self.timestamp.isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for JSONL output."""
        return {
            "nodeid": self.nodeid,
            "test_name": self.test_name,
            "outcome": (
                self.outcome.value if isinstance(self.outcome, TestOutcome) else self.outcome
            ),
            "duration": round(self.duration, 4),
            "assertion_message": self.assertion_message,
            "markers": self.markers,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "output_lines": self.output_lines,
            "run_id": self.run_id,
            "environment": self.environment,
            "python_version": self.python_version,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class FlakyTestSessionReport:
    """Session-level analysis report (Tier 2)."""

    session_id: str
    timestamp: datetime
    run_count: int
    total_tests: int
    flaky_candidates: list[FlakyTestMetric] = field(default_factory=list)
    unstable_candidates: list[FlakyTestMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary for JSON serialization."""
        return {
            "session": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "run_count": self.run_count,
            "total_tests": self.total_tests,
            "flaky_count": len(self.flaky_candidates),
            "unstable_count": len(self.unstable_candidates),
            "flaky_candidates": [m.to_dict() for m in self.flaky_candidates],
            "unstable_candidates": [m.to_dict() for m in self.unstable_candidates],
        }


@dataclass
class FlakyTestConfig:
    """Configuration for flaky test collection and analysis.

    Attributes:
        storage_root: Path or URI for historical metrics storage.
        min_run_count: Minimum runs required for analysis (default: 3).
        historical_window_days: Days of historical data to retain (default: 30).
        flakiness_threshold: Failure rate to mark tests as flaky (default: 0.10).
        unstable_threshold: Failure rate to mark tests as unstable (default: 0.05).
        recovery_rate_threshold: Target fraction of stable tests (default: 0.80).
    """

    storage_root: Path | str
    min_run_count: int = 3
    historical_window_days: int = 30
    flakiness_threshold: float = 0.10
    unstable_threshold: float = 0.05
    recovery_rate_threshold: float = 0.80

    def __post_init__(self) -> None:
        if isinstance(self.storage_root, str):
            if not self.storage_root.startswith(("s3://", "http://")):
                self.storage_root = Path(self.storage_root)

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary for JSON serialization."""
        return {
            "storage_root": str(self.storage_root),
            "min_run_count": self.min_run_count,
            "historical_window_days": self.historical_window_days,
            "flakiness_threshold": self.flakiness_threshold,
            "unstable_threshold": self.unstable_threshold,
            "recovery_rate_threshold": self.recovery_rate_threshold,
        }
