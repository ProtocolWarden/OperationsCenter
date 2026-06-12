# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Query API for test signal visibility and autonomy consumption.

Provides bounded APIs for accessing test execution signals and aggregated metrics
to enable autonomy systems to reason about test reliability and coverage trends.

## API Categories

### Single-Signal Queries
- get_latest_test_signal() — Most recent test signal
- get_signal_by_run_id(run_id) — Test signal for specific run
- list_test_signal_history(timerange) — Test signals in time window

### Aggregation & Trend Analysis
- test_status_trend(count) — Status changes over N snapshots
- coverage_change_rate(timerange) — Coverage improvement/regression rate
- failure_reason_summary(timerange) — Most common failure types

### Snapshot-Level Access
- get_snapshot(run_id) — Full snapshot with all signals
- list_snapshot_run_ids(timerange) — Available snapshot IDs in window

## Usage Pattern

    query = TestSignalQuery(root_path=Path("tools/report/operations_center/observer"))
    latest_signal = query.get_latest_test_signal()
    if latest_signal and latest_signal.failed_count > 0:
        summary = query.failure_reason_summary(hours=24)
        autonomy_system.investigate_failures(latest_signal, summary)

## Return Value Contracts

All query methods return None (not exception) when:
- No snapshots available for the requested time window
- Snapshot file is corrupted or unreadable
- Test signal has status="unavailable"

This allows graceful degradation in autonomy systems without exception handling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.observer.models import RepoStateSnapshot, TestSignal
from operations_center.observer.query_flaky import (
    FlakyTest,  # noqa: F401 — re-exported for existing importers
    FlakyTestMetrics,  # noqa: F401 — re-exported for existing importers
    FlakyTestQueryMixin,
    RepositoryHealth,  # noqa: F401 — re-exported for existing importers
)

logger = logging.getLogger(__name__)


@dataclass
class TimeRange:
    """Represents a time window for historical queries."""

    start: datetime
    end: datetime

    @classmethod
    def last_hours(cls, hours: int) -> TimeRange:
        """Create range for last N hours."""
        now = datetime.now(UTC)
        return cls(start=now - timedelta(hours=hours), end=now)

    @classmethod
    def last_days(cls, days: int) -> TimeRange:
        """Create range for last N days."""
        now = datetime.now(UTC)
        return cls(start=now - timedelta(days=days), end=now)

    @classmethod
    def since(cls, start: datetime) -> TimeRange:
        """Create range from start time to now."""
        return cls(start=start, end=datetime.now(UTC))

    def contains(self, observed_at: datetime) -> bool:
        """Check if timestamp is within this range."""
        return self.start <= observed_at <= self.end


@dataclass
class StatusTrend:
    """Represents test status changes over time for trend analysis.

    Attributes:
        status_sequence: Ordered list of (observed_at, status) tuples
        change_count: Number of status transitions in the window
        current_status: Most recent status
        status_history: Dict mapping status values to their occurrence counts
    """

    status_sequence: list[tuple[datetime, str]]
    change_count: int
    current_status: str | None
    status_history: dict[str, int]

    @property
    def is_stable(self) -> bool:
        """True if status has not changed in sequence."""
        return self.change_count == 0

    @property
    def dominant_status(self) -> str | None:
        """Most frequently occurring status."""
        if not self.status_history:
            return None
        return max(self.status_history, key=lambda k: self.status_history[k])


@dataclass
class CoverageTrend:
    """Coverage metrics and change rate over time.

    Attributes:
        measurements: List of (observed_at, coverage_percent) tuples, oldest first
        current_percent: Latest coverage percentage (or None if unavailable)
        change_percent: Absolute change from oldest to latest measurement
        average_percent: Mean coverage across all measurements
        min_percent: Lowest coverage in window
        max_percent: Highest coverage in window
        trend_direction: "improving" (coverage up), "regressing" (coverage down), "stable"
    """

    measurements: list[tuple[datetime, float]]
    current_percent: float | None
    change_percent: float | None
    average_percent: float | None
    min_percent: float | None
    max_percent: float | None

    @property
    def trend_direction(self) -> str:
        """Classify coverage direction: improving, regressing, or stable."""
        if self.change_percent is None or len(self.measurements) < 2:
            return "unavailable"
        if self.change_percent > 0.1:
            return "improving"
        if self.change_percent < -0.1:
            return "regressing"
        return "stable"


@dataclass
class FailureSummary:
    """Summary of failure reasons across a time window.

    Attributes:
        failure_counts: Dict mapping failure_category to count
        most_common: Primary failure category (or None if no failures)
        total_failing_runs: Count of snapshots with failures
        failing_rate: Fraction of snapshots with failures in window [0.0, 1.0]
    """

    failure_counts: dict[str, int]
    most_common: str | None
    total_failing_runs: int
    failing_rate: float

    @property
    def is_concerning(self) -> bool:
        """True if 20% or more of runs are failing."""
        return self.failing_rate >= 0.2


class TestSignalQuery(FlakyTestQueryMixin):
    """Query API for test signal visibility in observer snapshots.

    Provides read-only access to historical test signals and aggregated metrics
    for autonomy systems to reason about test execution state, coverage trends,
    and failure patterns.

    ## Thread Safety
    This class is designed for single-threaded use. For concurrent access, create
    separate instances per thread or use external synchronization.

    ## Error Handling
    All methods return None (not exceptions) when data is unavailable. This allows
    autonomy systems to gracefully degrade when snapshots are missing or corrupted.
    """

    def __init__(self, root: Path | None = None) -> None:
        """Initialize query API pointing to snapshot root directory.

        Args:
            root: Path to snapshot root (default: tools/report/operations_center/observer).
                  Snapshots are expected at {root}/{run_id}/repo_state_snapshot.json.
        """
        self.root = root or Path("tools/report/operations_center/observer")

    def get_latest_test_signal(self) -> TestSignal | None:
        """Get the most recent test signal from available snapshots.

        Returns:
            TestSignal with all breakdown metrics, or None if:
            - No snapshots exist in root directory
            - Latest snapshot is corrupted/unreadable
            - Test signal has status="unavailable"

        Example:
            latest = query.get_latest_test_signal()
            if latest and latest.failed_count > 0:
                print(f"Latest run has {latest.failed_count} failures")
        """
        latest_snapshot = self._get_latest_snapshot()
        if not latest_snapshot:
            return None
        signal = latest_snapshot.signals.test_signal
        if signal.status == "unavailable":
            return None
        return signal

    def get_signal_by_run_id(self, run_id: str) -> TestSignal | None:
        """Get test signal for a specific snapshot run.

        Args:
            run_id: Run identifier matching snapshot directory name

        Returns:
            TestSignal or None if snapshot not found/readable/status=unavailable

        Example:
            signal = query.get_signal_by_run_id("obs_20260607T120000Z_abc123")
            if signal:
                print(f"Run completed with {signal.test_count} tests")
        """
        snapshot = self._load_snapshot(run_id)
        if not snapshot:
            return None
        signal = snapshot.signals.test_signal
        if signal.status == "unavailable":
            return None
        return signal

    def list_test_signal_history(self, timerange: TimeRange) -> list[tuple[str, TestSignal]]:
        """Get test signals within a time window, ordered oldest to newest.

        Args:
            timerange: TimeRange object defining the window (use TimeRange.last_hours(24))

        Returns:
            List of (run_id, signal) tuples ordered by observed_at ascending.
            Empty list if no snapshots in window or all signals unavailable.

        Example:
            signals = query.list_test_signal_history(TimeRange.last_hours(24))
            for run_id, signal in signals:
                if signal.status == "failing":
                    print(f"{run_id}: {signal.summary}")
        """
        snapshots = self._load_snapshots_in_range(timerange)
        results = []
        for snapshot in snapshots:
            signal = snapshot.signals.test_signal
            if signal.status != "unavailable":
                results.append((snapshot.run_id, signal))
        return results

    def test_status_trend(self, count: int = 10) -> StatusTrend | None:
        """Analyze test status changes over the last N snapshots.

        Detects whether test execution is stable, improving, or deteriorating
        by examining status transitions over recent snapshots.

        Args:
            count: Number of most recent snapshots to analyze (default: 10)

        Returns:
            StatusTrend with status_sequence, change_count, is_stable property.
            None if fewer than 2 snapshots available or all unavailable.

        Example:
            trend = query.test_status_trend(count=20)
            if trend and not trend.is_stable:
                autonomy.escalate(f"Test status unstable: {trend.change_count} transitions")
        """
        snapshots = self._get_recent_snapshots(count)
        if len(snapshots) < 2:
            return None

        status_sequence = []
        status_history: dict[str, int] = {}
        for snapshot in snapshots:
            signal = snapshot.signals.test_signal
            if signal.status != "unavailable":
                status_sequence.append((snapshot.observed_at, signal.status))
                status_history[signal.status] = status_history.get(signal.status, 0) + 1

        if not status_sequence:
            return None

        change_count = sum(
            1
            for i in range(len(status_sequence) - 1)
            if status_sequence[i][1] != status_sequence[i + 1][1]
        )

        return StatusTrend(
            status_sequence=status_sequence,
            change_count=change_count,
            current_status=status_sequence[-1][1] if status_sequence else None,
            status_history=status_history,
        )

    def coverage_change_rate(self, timerange: TimeRange) -> CoverageTrend | None:
        """Calculate coverage improvement/regression rate over time.

        Analyzes coverage_percent from test signals to detect coverage trends,
        enabling autonomy systems to identify if coverage is improving or regressing.

        Args:
            timerange: TimeRange defining the analysis window

        Returns:
            CoverageTrend with measurements, change_percent, trend_direction.
            None if fewer than 2 measurements in window or all unavailable.

        Example:
            trend = query.coverage_change_rate(TimeRange.last_days(30))
            if trend and trend.trend_direction == "regressing":
                autonomy.flag_for_review(f"Coverage dropped {abs(trend.change_percent):.1f}%")
        """
        snapshots = self._load_snapshots_in_range(timerange)
        measurements = []
        for snapshot in snapshots:
            signal = snapshot.signals.test_signal
            if signal.coverage_percent is not None:
                measurements.append((snapshot.observed_at, signal.coverage_percent))

        if len(measurements) < 2:
            return None

        percents = [pct for _, pct in measurements]
        change = percents[-1] - percents[0]
        return CoverageTrend(
            measurements=measurements,
            current_percent=percents[-1],
            change_percent=change,
            average_percent=sum(percents) / len(percents),
            min_percent=min(percents),
            max_percent=max(percents),
        )

    def failure_reason_summary(self, timerange: TimeRange) -> FailureSummary | None:
        """Summarize failure types and their frequency over a time window.

        Aggregates failure_category data to identify which types of failures
        are most common, helping autonomy systems prioritize investigation.

        Args:
            timerange: TimeRange defining the analysis window

        Returns:
            FailureSummary with failure_counts, most_common, failing_rate.
            None if no snapshots in window or all signals unavailable.

        Example:
            summary = query.failure_reason_summary(TimeRange.last_days(7))
            if summary and summary.is_concerning:
                for failure_type, count in summary.failure_counts.items():
                    autonomy.log_failure_type(failure_type, count)
        """
        snapshots = self._load_snapshots_in_range(timerange)
        if not snapshots:
            return None

        failure_counts: dict[str, int] = {}
        total_failing = 0
        total_with_signal = 0

        for snapshot in snapshots:
            signal = snapshot.signals.test_signal
            if signal.status != "unavailable":
                total_with_signal += 1
                if signal.failed_count > 0:
                    total_failing += 1
                    if signal.failure_category:
                        failure_counts[signal.failure_category] = (
                            failure_counts.get(signal.failure_category, 0) + 1
                        )

        if not failure_counts:
            return None

        most_common = max(failure_counts, key=lambda k: failure_counts[k])
        failing_rate = total_failing / total_with_signal if total_with_signal > 0 else 0.0

        return FailureSummary(
            failure_counts=failure_counts,
            most_common=most_common,
            total_failing_runs=total_failing,
            failing_rate=failing_rate,
        )

    def get_snapshot(self, run_id: str) -> RepoStateSnapshot | None:
        """Get complete snapshot with all signals.

        Args:
            run_id: Run identifier

        Returns:
            Full RepoStateSnapshot or None if not found/readable

        Example:
            snapshot = query.get_snapshot("obs_20260607T120000Z_abc123")
            if snapshot:
                print(f"All signals at {snapshot.observed_at.isoformat()}")
        """
        return self._load_snapshot(run_id)

    def list_snapshot_run_ids(self, timerange: TimeRange) -> list[str]:
        """List available snapshot run IDs in time window (oldest to newest).

        Args:
            timerange: TimeRange defining the window

        Returns:
            List of run_id strings ordered by observed_at ascending

        Example:
            ids = query.list_snapshot_run_ids(TimeRange.last_hours(6))
            print(f"Found {len(ids)} snapshots in last 6 hours")
        """
        snapshots = self._load_snapshots_in_range(timerange)
        return [s.run_id for s in snapshots]

    # Private helpers

    def _get_latest_snapshot(self) -> RepoStateSnapshot | None:
        """Load the most recent snapshot by observed_at."""
        snapshots = self._get_recent_snapshots(1)
        return snapshots[0] if snapshots else None

    def _get_recent_snapshots(self, count: int) -> list[RepoStateSnapshot]:
        """Load N most recent snapshots, ordered oldest to newest."""
        if not self.root.exists():
            return []
        # Collect all snapshots and sort by observed_at
        all_snapshots = []
        for run_dir in self.root.glob("*"):
            if not run_dir.is_dir():
                continue
            snapshot = self._load_snapshot(run_dir.name)
            if snapshot:
                all_snapshots.append(snapshot)
        # Sort by observed_at and return most recent N, oldest to newest
        all_snapshots.sort(key=lambda s: s.observed_at)
        return all_snapshots[-count:]

    def _load_snapshots_in_range(self, timerange: TimeRange) -> list[RepoStateSnapshot]:
        """Load all snapshots in time range, ordered oldest to newest."""
        if not self.root.exists():
            return []

        snapshots = []
        for run_dir in sorted(self.root.glob("*")):
            snapshot = self._load_snapshot(run_dir.name)
            if snapshot and timerange.contains(snapshot.observed_at):
                snapshots.append(snapshot)
        return snapshots

    def _load_snapshot(self, run_id: str) -> RepoStateSnapshot | None:
        """Load snapshot from disk, return None if unreadable."""
        json_path = self.root / run_id / "repo_state_snapshot.json"
        if not json_path.exists():
            return None

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return RepoStateSnapshot(**data)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.debug("Failed to load snapshot %s: %s", run_id, exc)
            return None
