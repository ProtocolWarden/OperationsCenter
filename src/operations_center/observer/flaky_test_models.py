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

    MVP Implementation (Phase 1) — Implemented metrics:
    - failure_rate: Percentage of runs that failed (0.0-1.0)
    - pattern_entropy: Randomness in failure pattern (Shannon entropy)
    - streak_length: Longest consecutive failure streak (count)
    - recovery_time_days: Days between first and last failure
    - duration_mean/variance: Test execution duration statistics
    - flakiness_score: Combined metric for flakiness severity
    - confidence: Confidence in the flakiness determination

    Phase 2 Deferred Metrics (not implemented, no stubs):
    - failure_entropy: Shannon entropy of failure distribution
    - streak_variance: Variance in failure streak lengths
    - recovery_time_percentile_90: 90th percentile of recovery time
    - duration_stability: Stability of test execution duration
    - environment_correlation: Correlation with environment factors
    - isolation_score: Test isolation score

    Design Reference: docs/design/STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md
    Section 4.1 defines all 14 metrics (7 per-test + 7 repository-level).
    This Phase 1 implementation provides the foundation for Phase 2 metrics.
    See .console/backlog.md for Phase 2 implementation timeline.
    """

    nodeid: str
    failure_rate: float
    run_count: int
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

    def to_dict(self) -> dict[str, Any]:
        """Convert metric to dictionary for JSON serialization."""
        return {
            "nodeid": self.nodeid,
            "failure_rate": round(self.failure_rate, 4),
            "run_count": self.run_count,
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
        }


@dataclass
class FlakyTestResult:
    """Result of a single test execution (Tier 1 observation)."""

    nodeid: str
    outcome: TestOutcome | str
    duration: float
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
            "outcome": (
                self.outcome.value if isinstance(self.outcome, TestOutcome) else self.outcome
            ),
            "duration": round(self.duration, 4),
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
