# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""FlakyTestReporter — Core flaky test detection and analysis system.

Implements Tier 1 (per-run observation) and Tier 2 (session analysis) of the
flaky test detection architecture. Provides detection logic, pattern analysis,
and structured metrics for flakiness tracking.

Usage:
    reporter = FlakyTestReporter.create_local("/tmp/flaky-tests")
    test_result = FlakyTestResult(
        nodeid="tests/unit/test_foo.py::TestClass::test_method",
        outcome="failed",
        duration=1.234
    )
    reporter.track_test(test_result)
    report = reporter.analyze_session()
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any


class FlakynessCategory(Enum):
    """Root cause categories for flaky tests."""

    TRANSIENT = "transient"
    STRUCTURAL = "structural"
    CONFIGURATION = "configuration"
    INTERMITTENT_STRUCTURAL = "intermittent_structural"
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
    """Structured metrics for a single flaky test."""

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
                round(self.recovery_time_days, 2)
                if self.recovery_time_days is not None
                else None
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
                self.outcome.value
                if isinstance(self.outcome, TestOutcome)
                else self.outcome
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


class FlakyTestReporter:
    """Core flaky test detection and analysis engine.

    Implements Tier 1-2 of the detection architecture:
    - Tier 1: Tracks individual test outcomes from pytest
    - Tier 2: Analyzes patterns and produces flakiness metrics

    This class is storage-agnostic and can be used with different backends.
    """

    FLAKY_THRESHOLD = 0.10
    UNSTABLE_THRESHOLD = 0.05
    MIN_CONFIDENCE_RUNS = 3
    MAX_CONFIDENCE_RUNS = 5

    def __init__(self, storage_root: Path | None = None) -> None:
        """Initialize the reporter.

        Args:
            storage_root: Optional root directory for storing test results and reports.
        """
        self.storage_root = storage_root or Path("/tmp/flaky-tests")
        self.session_id = datetime.now(UTC).isoformat()

        self.test_runs: dict[str, list[FlakyTestResult]] = {}
        self.all_results: list[FlakyTestResult] = []

    @classmethod
    def create_local(cls, storage_root: str | Path) -> FlakyTestReporter:
        """Create a reporter with local file storage.

        Args:
            storage_root: Path to directory for storing reports and results.

        Returns:
            Configured FlakyTestReporter instance.
        """
        path = Path(storage_root)
        path.mkdir(parents=True, exist_ok=True)
        return cls(storage_root=path)

    @classmethod
    def create_s3(
        cls,
        bucket: str,
        prefix: str = "flaky-tests",
    ) -> FlakyTestReporter:
        """Create a reporter with S3 storage backend (stub for Stage 2+).

        Args:
            bucket: S3 bucket name.
            prefix: Key prefix for storing reports.

        Returns:
            Configured FlakyTestReporter instance.
        """
        # Stub: full S3 support in Stage 2-3
        path_str = f"s3://{bucket}/{prefix}"
        return cls(storage_root=Path(path_str))

    @classmethod
    def create_http(
        cls,
        base_url: str,
        auth_token: str | None = None,
    ) -> FlakyTestReporter:
        """Create a reporter with HTTP backend (stub for Stage 2+).

        Args:
            base_url: Base URL for HTTP API.
            auth_token: Optional bearer token for authentication.

        Returns:
            Configured FlakyTestReporter instance.
        """
        # Stub: full HTTP support in Stage 2-3
        return cls(storage_root=Path(f"http://{base_url}"))

    def track_test(self, result: FlakyTestResult) -> None:
        """Record a test execution result (Tier 1).

        Args:
            result: Test execution result to track.
        """
        if result.nodeid not in self.test_runs:
            self.test_runs[result.nodeid] = []
        self.test_runs[result.nodeid].append(result)
        self.all_results.append(result)

    def analyze_session(self) -> FlakyTestSessionReport:
        """Analyze all tracked test runs and produce session report (Tier 2).

        Returns:
            Session analysis report with flakiness metrics.
        """
        flaky_candidates = []
        unstable_candidates = []

        for nodeid, runs in self.test_runs.items():
            if len(runs) < 2:
                continue

            metric = self._analyze_test_runs(nodeid, runs)

            if metric.failure_rate > self.FLAKY_THRESHOLD:
                flaky_candidates.append(metric)
            elif metric.failure_rate > self.UNSTABLE_THRESHOLD:
                unstable_candidates.append(metric)

        return FlakyTestSessionReport(
            session_id=self.session_id,
            timestamp=datetime.now(UTC),
            run_count=len(set(r.run_id for r in self.all_results)),
            total_tests=len(self.test_runs),
            flaky_candidates=flaky_candidates,
            unstable_candidates=unstable_candidates,
        )

    def _analyze_test_runs(
        self, nodeid: str, runs: list[FlakyTestResult]
    ) -> FlakyTestMetric:
        """Analyze all runs of a single test to produce metrics.

        Args:
            nodeid: Fully qualified test name.
            runs: List of all execution results for this test.

        Returns:
            Computed metrics for the test.
        """
        failure_count = sum(1 for r in runs if r.outcome == TestOutcome.FAILED)

        run_count = len(runs)
        failure_rate = failure_count / run_count if run_count > 0 else 0.0

        confidence = min(
            1.0, run_count / self.MAX_CONFIDENCE_RUNS
        )  # Capped at 5 runs

        flakiness_score = self._compute_flakiness_score(
            failure_rate, runs, run_count
        )

        suspected_category = self._categorize_flakiness(failure_rate, runs)

        duration_mean = sum(r.duration for r in runs) / run_count if run_count > 0 else 0.0
        duration_variance = self._compute_variance(
            [r.duration for r in runs], duration_mean
        )

        pattern_entropy = self._compute_pattern_entropy(runs)
        streak_length = self._compute_streak_length(runs)
        retry_success_count = self._count_retry_successes(runs)
        recovery_time = self._compute_recovery_time(runs)

        last_failure_reason = ""
        for r in reversed(runs):
            if r.outcome == TestOutcome.FAILED and r.exception_type:
                last_failure_reason = f"{r.exception_type}: {r.exception_message}"[:100]
                break

        return FlakyTestMetric(
            nodeid=nodeid,
            failure_rate=failure_rate,
            run_count=run_count,
            retry_success_count=retry_success_count,
            duration_mean=duration_mean,
            duration_variance=duration_variance,
            pattern_entropy=pattern_entropy,
            streak_length=streak_length,
            recovery_time_days=recovery_time,
            suspected_category=suspected_category,
            markers=runs[0].markers if runs else [],
            last_failure_reason=last_failure_reason,
            flakiness_score=flakiness_score,
            confidence=confidence,
        )

    def _compute_flakiness_score(
        self, failure_rate: float, runs: list[FlakyTestResult], run_count: int
    ) -> float:
        """Compute overall flakiness score (0.0 to 1.0).

        Score combines failure rate and variance:
        - High failure rate + consistent = structural (high score)
        - Low failure rate + high variance = transient (moderate score)
        - High variance pattern = erratic (moderate-high score)

        Args:
            failure_rate: Proportion of failed runs.
            runs: List of test execution results.
            run_count: Total number of runs.

        Returns:
            Flakiness score from 0.0 (stable) to 1.0 (completely unreliable).
        """
        if run_count < 2:
            return 0.0

        base_score = max(0.5 * failure_rate, 0.0)

        variance = self._compute_pattern_variance(runs)
        entropy = self._compute_pattern_entropy(runs)

        if failure_rate > 0.5:
            score = base_score + (0.2 * variance)
        else:
            score = base_score + (0.1 * entropy)

        return min(1.0, score)

    def _compute_pattern_variance(self, runs: list[FlakyTestResult]) -> float:
        """Compute variance of pass/fail pattern.

        Returns:
            Variance value from 0.0 (all same) to 1.0 (maximally random).
        """
        if len(runs) < 2:
            return 0.0

        outcomes = [1.0 if r.outcome == TestOutcome.FAILED else 0.0 for r in runs]
        mean = sum(outcomes) / len(outcomes)

        variance = sum((x - mean) ** 2 for x in outcomes) / len(outcomes)
        return min(1.0, variance)

    def _compute_variance(self, values: list[float], mean: float) -> float:
        """Compute variance of numeric values."""
        if len(values) < 2:
            return 0.0
        squared_diffs = [(v - mean) ** 2 for v in values]
        return sum(squared_diffs) / len(squared_diffs)

    def _compute_pattern_entropy(self, runs: list[FlakyTestResult]) -> float:
        """Compute Shannon entropy of pass/fail pattern.

        Higher entropy = more random/unpredictable pass/fail sequence.

        Returns:
            Entropy in nats (0.0 = deterministic, ~0.693 = max for binary).
        """
        if len(runs) < 2:
            return 0.0

        pass_count = sum(1 for r in runs if r.outcome == TestOutcome.PASSED)
        fail_count = sum(1 for r in runs if r.outcome == TestOutcome.FAILED)
        total = pass_count + fail_count

        if total == 0 or pass_count == 0 or fail_count == 0:
            return 0.0

        p_pass = pass_count / total
        p_fail = fail_count / total

        entropy = -(p_pass * math.log(p_pass) + p_fail * math.log(p_fail))
        return entropy

    def _compute_streak_length(self, runs: list[FlakyTestResult]) -> int:
        """Compute longest consecutive sequence of same outcome.

        Higher = more deterministic (all passes or all failures in a row).

        Returns:
            Length of longest streak (1 if alternating).
        """
        if not runs:
            return 0

        max_streak = 1
        current_streak = 1
        last_outcome = runs[0].outcome

        for run in runs[1:]:
            if run.outcome == last_outcome:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 1
                last_outcome = run.outcome

        return max_streak

    def _count_retry_successes(self, runs: list[FlakyTestResult]) -> int:
        """Count how many times a test passed on retry (transient indicator).

        For each failed run, check if next run(s) pass within 1 hour.
        This is approximate without detailed retry timing metadata.

        Returns:
            Count of suspected retry successes.
        """
        if len(runs) < 2:
            return 0

        retry_successes = 0
        for i, run in enumerate(runs[:-1]):
            if run.outcome == TestOutcome.FAILED:
                next_run = runs[i + 1]
                if next_run.outcome == TestOutcome.PASSED:
                    retry_successes += 1

        return retry_successes

    def _compute_recovery_time(self, runs: list[FlakyTestResult]) -> float | None:
        """Compute time until test recovers after failure.

        Measures days from last failure to first subsequent pass.

        Returns:
            Days until recovery, or None if never recovered.
        """
        if not runs:
            return None

        last_failure_idx = None
        for i, run in enumerate(runs):
            if run.outcome == TestOutcome.FAILED:
                last_failure_idx = i

        if last_failure_idx is None:
            return None

        for run in runs[last_failure_idx + 1 :]:
            if run.outcome == TestOutcome.PASSED:
                delta = run.timestamp - runs[last_failure_idx].timestamp
                return delta.total_seconds() / (24 * 3600)

        return None

    def _categorize_flakiness(
        self, failure_rate: float, runs: list[FlakyTestResult]
    ) -> FlakynessCategory:
        """Categorize suspected root cause of flakiness.

        Uses failure rate, variance, and retry patterns to infer root cause:
        - Transient: Low failure rate, high variance, passes on retry
        - Structural: High failure rate, consistent, consistent failures
        - Configuration: Environment-specific (detected via markers/env)
        - Intermittent-Structural: Newly flaky (requires historical context)

        Args:
            failure_rate: Proportion of failed runs.
            runs: List of test execution results.

        Returns:
            Most likely flakiness category.
        """
        variance = self._compute_pattern_variance(runs)

        if 0.05 <= failure_rate <= 0.40 and variance > 0.1:
            return FlakynessCategory.TRANSIENT

        if failure_rate > 0.50:
            if variance < 0.05:
                return FlakynessCategory.STRUCTURAL
            return FlakynessCategory.INTERMITTENT_STRUCTURAL

        if any(marker in ("slow", "timeout") for marker in runs[0].markers):
            return FlakynessCategory.TRANSIENT

        if "timeout" in runs[0].exception_type.lower():
            return FlakynessCategory.TRANSIENT

        return FlakynessCategory.UNKNOWN

    def save_session_report(self, report: FlakyTestSessionReport) -> Path | None:
        """Save session report to storage.

        Args:
            report: Session analysis report to save.

        Returns:
            Path where report was saved, or None if storage not available.
        """
        storage_str = str(self.storage_root)
        if not self.storage_root or storage_str.startswith("s3:/") or storage_str.startswith("http:/"):
            return None

        reports_dir = self.storage_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        report_path = reports_dir / f"session-{timestamp}.json"

        report_path.write_text(json.dumps(report.to_dict(), indent=2))
        return report_path

    def save_test_results(self) -> Path | None:
        """Save all tracked test results to JSONL storage.

        Returns:
            Path where results were saved, or None if storage not available.
        """
        storage_str = str(self.storage_root)
        if not self.storage_root or storage_str.startswith("s3:/") or storage_str.startswith("http:/"):
            return None

        results_dir = self.storage_root / "runs"
        results_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        results_path = results_dir / f"results-{timestamp}.jsonl"

        with results_path.open("w") as f:
            for result in self.all_results:
                f.write(json.dumps(result.to_dict()) + "\n")

        return results_path

    def query_metrics_by_test(self, nodeid: str) -> FlakyTestMetric | None:
        """Get metrics for a specific test by name.

        Args:
            nodeid: Test node ID (e.g., 'tests/unit/test_foo.py::TestClass::test_method')

        Returns:
            FlakyTestMetric if test has been analyzed, None otherwise.
        """
        if nodeid not in self.test_runs:
            return None

        runs = self.test_runs[nodeid]
        if not runs:
            return None

        return self._analyze_test_runs(nodeid, runs)

    def query_module_flakiness(self, module_path: str) -> dict[str, Any]:
        """Get aggregated flakiness metrics for all tests in a module.

        Args:
            module_path: Module path (e.g., 'tests/unit' or 'tests/integration')

        Returns:
            Dictionary with aggregated metrics for all matching tests.
        """
        matching_tests = [
            nodeid for nodeid in self.test_runs.keys() if nodeid.startswith(module_path)
        ]

        if not matching_tests:
            return {
                "module": module_path,
                "test_count": 0,
                "flaky_count": 0,
                "unstable_count": 0,
                "avg_failure_rate": 0.0,
                "most_problematic": [],
            }

        metrics = []
        flaky_count = 0
        unstable_count = 0

        for nodeid in matching_tests:
            runs = self.test_runs[nodeid]
            metric = self._analyze_test_runs(nodeid, runs)
            metrics.append(metric)

            if metric.failure_rate > 0.10:
                flaky_count += 1
            elif 0.05 <= metric.failure_rate <= 0.10:
                unstable_count += 1

        avg_failure_rate = sum(m.failure_rate for m in metrics) / len(metrics) if metrics else 0.0
        most_problematic = sorted(metrics, key=lambda m: m.flakiness_score, reverse=True)[:5]

        return {
            "module": module_path,
            "test_count": len(matching_tests),
            "flaky_count": flaky_count,
            "unstable_count": unstable_count,
            "avg_failure_rate": round(avg_failure_rate, 4),
            "most_problematic": [m.to_dict() for m in most_problematic],
        }

    def query_trend_analysis(self, days: int = 7) -> dict[str, Any]:
        """Analyze test flakiness trend over a time window.

        Args:
            days: Number of days to look back in history.

        Returns:
            Dictionary with trend analysis including newly flaky and recovered tests.
        """
        cutoff_date = datetime.now(UTC).replace(microsecond=0) - timedelta(days=days)

        current_flaky = set()
        historical_flaky = set()

        for nodeid, runs in self.test_runs.items():
            if not runs:
                continue

            recent_runs = [r for r in runs if r.timestamp >= cutoff_date]
            older_runs = [r for r in runs if r.timestamp < cutoff_date]

            if recent_runs:
                recent_failures = sum(1 for r in recent_runs if r.outcome == TestOutcome.FAILED)
                recent_rate = recent_failures / len(recent_runs) if recent_runs else 0.0
                if recent_rate > 0.10:
                    current_flaky.add(nodeid)

            if older_runs:
                older_failures = sum(1 for r in older_runs if r.outcome == TestOutcome.FAILED)
                older_rate = older_failures / len(older_runs) if older_runs else 0.0
                if older_rate > 0.10:
                    historical_flaky.add(nodeid)

        newly_flaky = list(current_flaky - historical_flaky)
        recovered = list(historical_flaky - current_flaky)

        trend = "stable"
        if len(newly_flaky) > len(recovered):
            trend = "degrading"
        elif len(recovered) > len(newly_flaky) and recovered:
            trend = "improving"

        return {
            "period_days": days,
            "start_date": cutoff_date.isoformat(),
            "end_date": datetime.now(UTC).isoformat(),
            "current_flaky_count": len(current_flaky),
            "recovered_tests": recovered,
            "newly_flaky_tests": newly_flaky,
            "trend": trend,
        }


@dataclass
class FlakyTestConfig:
    """Configuration for flaky test collection and analysis.

    Attributes:
        storage_root: Path or URI for historical metrics storage (e.g., '/tmp/metrics', 's3://bucket/prefix')
        min_run_count: Minimum number of test runs required for analysis (default: 3)
        historical_window_days: Number of days of historical data to retain (default: 30)
        flakiness_threshold: Failure rate threshold for marking tests as flaky (default: 0.10 = 10%)
        unstable_threshold: Failure rate threshold for marking tests as unstable (default: 0.05 = 5%)
        recovery_rate_threshold: Target percentage of tests that should be stable (default: 0.80 = 80%)
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
