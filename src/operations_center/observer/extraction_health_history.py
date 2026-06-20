# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Extraction Signal History Tracking — Data models and storage for time-series extraction metrics.

Captures and manages historical snapshots of extraction health metrics (success_rate, extraction counts),
enabling trend analysis and anomaly detection over time.

Usage:
    # Record a snapshot
    snapshot = ExtractionHealthSnapshot(
        observed_at=datetime.now(UTC),
        success_rate=87.5,
        complete_extraction=14,
        partial_extraction=2,
        no_extraction=0,
        total_flaky_tests=16,
    )

    # Store it
    storage = ExtractionHistoryStorage.create_local("/var/extraction-history")
    storage.save_snapshot(snapshot)

    # Query trends
    snapshots = storage.load_snapshots_range(days=7)
    trend = snapshots_to_trend(snapshots)
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExtractionHealthSnapshot:
    """Point-in-time snapshot of extraction health metrics.

    Captured every time FlakyTestSignal is created. Used to build time-series trends.

    Attributes:
        observed_at: ISO 8601 timestamp when extraction health was measured.
        success_rate: Percentage of flaky tests with extraction (0-100).
        complete_extraction: Count of tests with both test_name AND assertion_message.
        partial_extraction: Count of tests with one field only.
        no_extraction: Count of tests with neither field.
        total_flaky_tests: Total count of flaky tests analyzed (for context).
        extracted_count: Count of tests with any extraction data (= complete + partial).
        edge_case_summary: Dict of edge case counts (truncated_messages, special_chars, etc.).
        snapshot_id: Reference to source FlakyTestSignal (optional, for debugging).
        collection_run_id: Unique identifier for this collection cycle (optional).
    """

    observed_at: datetime
    success_rate: float  # 0-100
    complete_extraction: int
    partial_extraction: int
    no_extraction: int
    total_flaky_tests: int
    extracted_count: int = 0  # Default to complete + partial
    edge_case_summary: dict[str, int] = field(default_factory=dict)
    snapshot_id: str | None = None
    collection_run_id: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize snapshot data."""
        # Ensure extracted_count is accurate if not explicitly set
        if self.extracted_count == 0:
            self.extracted_count = self.complete_extraction + self.partial_extraction

        # Validate success_rate is in range
        if not (0.0 <= self.success_rate <= 100.0):
            raise ValueError(f"success_rate must be 0-100, got {self.success_rate}")

        # Validate counts are non-negative
        if self.complete_extraction < 0 or self.partial_extraction < 0 or self.no_extraction < 0:
            raise ValueError("Extraction counts cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "observed_at": self.observed_at.isoformat(),
            "success_rate": round(self.success_rate, 2),
            "complete_extraction": self.complete_extraction,
            "partial_extraction": self.partial_extraction,
            "no_extraction": self.no_extraction,
            "total_flaky_tests": self.total_flaky_tests,
            "extracted_count": self.extracted_count,
            "edge_case_summary": self.edge_case_summary,
            "snapshot_id": self.snapshot_id,
            "collection_run_id": self.collection_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionHealthSnapshot:
        """Deserialize from JSON dict."""
        return cls(
            observed_at=datetime.fromisoformat(data["observed_at"]),
            success_rate=data["success_rate"],
            complete_extraction=data["complete_extraction"],
            partial_extraction=data["partial_extraction"],
            no_extraction=data["no_extraction"],
            total_flaky_tests=data["total_flaky_tests"],
            extracted_count=data.get("extracted_count", 0),
            edge_case_summary=data.get("edge_case_summary", {}),
            snapshot_id=data.get("snapshot_id"),
            collection_run_id=data.get("collection_run_id"),
        )


@dataclass
class ExtractionHealthTrend:
    """Aggregated trend metrics computed from snapshots over a time period.

    Used to represent trends at different granularities (daily, weekly, monthly).

    Attributes:
        period_start: Start of the aggregation period (ISO 8601).
        period_end: End of the aggregation period (ISO 8601).
        granularity: "hourly", "daily", "weekly", or "monthly".
        success_rate_mean: Mean success rate over period.
        success_rate_min: Minimum success rate in period.
        success_rate_max: Maximum success rate in period.
        success_rate_std_dev: Standard deviation of success rate.
        success_rate_trend: Linear regression slope (% per day).
        complete_extraction_mean: Mean count of fully extracted tests.
        partial_extraction_mean: Mean count of partially extracted tests.
        no_extraction_mean: Mean count of tests with no extraction.
        observation_count: Number of snapshots in this period.
        edge_case_trends: Dict mapping edge case type → {"mean": X, "max": Y, "min": Z}.
        anomalies: List of detected anomalies (type, timestamp, metric, delta_pct).
    """

    period_start: datetime
    period_end: datetime
    granularity: str  # "hourly", "daily", "weekly", "monthly"
    success_rate_mean: float
    success_rate_min: float
    success_rate_max: float
    success_rate_std_dev: float
    success_rate_trend: float  # % per day (slope from linear regression)
    complete_extraction_mean: float
    partial_extraction_mean: float
    no_extraction_mean: float
    observation_count: int
    edge_case_trends: dict[str, dict[str, float]] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "granularity": self.granularity,
            "success_rate_mean": round(self.success_rate_mean, 2),
            "success_rate_min": round(self.success_rate_min, 2),
            "success_rate_max": round(self.success_rate_max, 2),
            "success_rate_std_dev": round(self.success_rate_std_dev, 2),
            "success_rate_trend": round(self.success_rate_trend, 3),
            "complete_extraction_mean": round(self.complete_extraction_mean, 2),
            "partial_extraction_mean": round(self.partial_extraction_mean, 2),
            "no_extraction_mean": round(self.no_extraction_mean, 2),
            "observation_count": self.observation_count,
            "edge_case_trends": self.edge_case_trends,
            "anomalies": self.anomalies,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionHealthTrend:
        """Deserialize from JSON dict."""
        return cls(
            period_start=datetime.fromisoformat(data["period_start"]),
            period_end=datetime.fromisoformat(data["period_end"]),
            granularity=data["granularity"],
            success_rate_mean=data["success_rate_mean"],
            success_rate_min=data["success_rate_min"],
            success_rate_max=data["success_rate_max"],
            success_rate_std_dev=data["success_rate_std_dev"],
            success_rate_trend=data["success_rate_trend"],
            complete_extraction_mean=data["complete_extraction_mean"],
            partial_extraction_mean=data["partial_extraction_mean"],
            no_extraction_mean=data["no_extraction_mean"],
            observation_count=data["observation_count"],
            edge_case_trends=data.get("edge_case_trends", {}),
            anomalies=data.get("anomalies", []),
        )


class ExtractionHistoryStorage:
    """Manages storage and retrieval of extraction health historical snapshots.

    Stores snapshots in JSONL format (one JSON object per line) for efficient
    streaming, time-series queries, and trend analysis.

    Usage:
        storage = ExtractionHistoryStorage.create_local("/var/extraction-history")
        storage.save_snapshot(snapshot)
        snapshots = storage.load_snapshots_range(days=7)
    """

    def __init__(
        self,
        base_path: Path,
        retention_days: int = 365,
    ):
        """Initialize storage manager.

        Args:
            base_path: Root directory for storage.
            retention_days: How long to keep snapshots (default: 1 year).
        """
        self.base_path = Path(base_path)
        self.snapshots_file = self.base_path / "extraction_health_history.jsonl"
        self.retention_days = retention_days

        # Create directories
        self.base_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def create_local(base_path: str) -> ExtractionHistoryStorage:
        """Create local file-based storage manager.

        Args:
            base_path: Root directory for storage.

        Returns:
            Configured storage manager.
        """
        return ExtractionHistoryStorage(Path(base_path))

    def save_snapshot(self, snapshot: ExtractionHealthSnapshot) -> None:
        """Save a snapshot to the history file."""
        try:
            with open(self.snapshots_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("Failed to save snapshot to %s: %s", self.snapshots_file, e)
            raise

    def load_all_snapshots(self) -> list[ExtractionHealthSnapshot]:
        """Load all snapshots from history file, in chronological order."""
        snapshots: list[ExtractionHealthSnapshot] = []

        if not self.snapshots_file.exists():
            return snapshots

        try:
            with open(self.snapshots_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        snapshot = ExtractionHealthSnapshot.from_dict(data)
                        snapshots.append(snapshot)
                    except (json.JSONDecodeError, ValueError, TypeError) as e:
                        logger.debug("Failed to parse snapshot line: %s", e)
                        continue
        except OSError as e:
            logger.error("Failed to read snapshots file %s: %s", self.snapshots_file, e)
            return snapshots

        return snapshots

    def load_snapshots_range(self, days: int = 7) -> list[ExtractionHealthSnapshot]:
        """Load snapshots from the past ``days`` days, in chronological order."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        all_snapshots = self.load_all_snapshots()

        return [s for s in all_snapshots if s.observed_at >= cutoff]

    def load_snapshots_since(self, since: datetime) -> list[ExtractionHealthSnapshot]:
        """Load snapshots at or after ``since``, in chronological order."""
        all_snapshots = self.load_all_snapshots()
        return [s for s in all_snapshots if s.observed_at >= since]

    def cleanup_old_snapshots(self) -> int:
        """Remove snapshots older than the retention period; returns the count deleted."""
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        all_snapshots = self.load_all_snapshots()

        # Filter to keep only recent snapshots
        recent = [s for s in all_snapshots if s.observed_at >= cutoff]

        if len(recent) == len(all_snapshots):
            return 0  # Nothing to delete

        # Rewrite file with only recent snapshots
        try:
            with open(self.snapshots_file, "w", encoding="utf-8") as f:
                for snapshot in recent:
                    f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")
            deleted_count = len(all_snapshots) - len(recent)
            logger.info("Cleaned up %d old snapshots, %d remaining", deleted_count, len(recent))
            return deleted_count
        except OSError as e:
            logger.error("Failed to cleanup snapshots: %s", e)
            return 0


def calculate_trend_slope(snapshots: list[ExtractionHealthSnapshot]) -> dict[str, float | str]:
    """Calculate linear regression trend of success_rate over time.

    Uses least squares regression to compute the trend slope (% per day).

    Args:
        snapshots: List of snapshots in chronological order.

    Returns:
        Dict with keys:
            - "slope": % per day (e.g., +0.5 means +0.5% per day improvement)
            - "r_squared": Model fit quality (0-1, higher is better)
            - "confidence": "improving", "degrading", "stable", or "uncertain"
    """
    if len(snapshots) < 2:
        return {"slope": 0.0, "r_squared": 0.0, "confidence": "uncertain"}

    # Convert timestamps to days since first observation
    t_0 = snapshots[0].observed_at
    x = [(s.observed_at - t_0).total_seconds() / (24 * 3600) for s in snapshots]  # Days
    y = [s.success_rate for s in snapshots]  # Success rates

    # Linear regression: y = mx + b
    n = len(x)
    x_mean = sum(x) / n
    y_mean = sum(y) / n

    numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

    slope = numerator / denominator if denominator > 0 else 0.0

    # R-squared: how well the line fits
    ss_res = sum((y[i] - (slope * x[i] + (y_mean - slope * x_mean))) ** 2 for i in range(n))
    ss_tot = sum((y[i] - y_mean) ** 2 for i in range(n))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Classify trend
    if abs(slope) < 0.1:  # Less than 0.1% per day
        confidence = "stable"
    elif slope > 0.1:
        confidence = "improving"
    else:  # slope < -0.1
        confidence = "degrading"

    return {
        "slope": round(slope, 3),
        "r_squared": round(r_squared, 3),
        "confidence": confidence,
    }


def detect_anomalies(
    snapshots: list[ExtractionHealthSnapshot], window_size: int = 5
) -> list[dict[str, Any]]:
    """Detect sudden changes in extraction metrics using moving average.

    Args:
        snapshots: List of snapshots in chronological order.
        window_size: Size of moving average window (default: 5 observations).

    Returns:
        List of anomalies detected. Each anomaly is a dict with keys:
            - "type": "spike_down" or "spike_up"
            - "timestamp": ISO 8601 timestamp of anomaly
            - "metric": "success_rate"
            - "delta_pct": Change from moving average (percent)
            - "previous_avg": Moving average before anomaly
            - "current_value": Value at anomaly point
    """
    if len(snapshots) < window_size * 2:
        return []

    anomalies: list[dict[str, Any]] = []
    values = [s.success_rate for s in snapshots]

    for i in range(window_size, len(values) - 1):
        # Moving average of previous window_size observations
        before_avg = sum(values[i - window_size : i]) / window_size
        # Current observation
        current = values[i]

        # Detect drop of >5%
        if current < before_avg - 5.0:
            anomalies.append(
                {
                    "type": "spike_down",
                    "timestamp": snapshots[i].observed_at.isoformat(),
                    "metric": "success_rate",
                    "delta_pct": round(current - before_avg, 2),
                    "previous_avg": round(before_avg, 2),
                    "current_value": round(current, 2),
                }
            )

        # Detect spike of >5%
        if current > before_avg + 5.0:
            anomalies.append(
                {
                    "type": "spike_up",
                    "timestamp": snapshots[i].observed_at.isoformat(),
                    "metric": "success_rate",
                    "delta_pct": round(current - before_avg, 2),
                    "previous_avg": round(before_avg, 2),
                    "current_value": round(current, 2),
                }
            )

    return anomalies


def aggregate_edge_cases(
    snapshots: list[ExtractionHealthSnapshot],
) -> dict[str, dict[str, float]]:
    """Aggregate edge case metrics across snapshots.

    Args:
        snapshots: List of snapshots.

    Returns:
        Dict mapping edge case type to {"mean": X, "min": Y, "max": Z}.
    """
    if not snapshots:
        return {}

    # Collect all edge case types
    all_types = set()
    for s in snapshots:
        all_types.update(s.edge_case_summary.keys())

    aggregated: dict[str, dict[str, float]] = {}

    for edge_type in all_types:
        values = [float(s.edge_case_summary.get(edge_type, 0)) for s in snapshots]
        aggregated[edge_type] = {
            "mean": round(statistics.mean(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }

    return aggregated


def snapshots_to_trend(
    snapshots: list[ExtractionHealthSnapshot], granularity: str = "daily"
) -> ExtractionHealthTrend:
    """Convert snapshots to aggregated trend metrics.

    Args:
        snapshots: List of snapshots in chronological order.
        granularity: "daily", "weekly", or "monthly" (for metadata).

    Returns:
        ExtractionHealthTrend with aggregated metrics.

    Raises:
        ValueError: If snapshots list is empty.
    """
    if not snapshots:
        raise ValueError("Cannot create trend from empty snapshots list")

    # Extract metric arrays
    success_rates = [s.success_rate for s in snapshots]
    complete_counts = [float(s.complete_extraction) for s in snapshots]
    partial_counts = [float(s.partial_extraction) for s in snapshots]
    no_extract_counts = [float(s.no_extraction) for s in snapshots]

    # Calculate stats
    trend_data = calculate_trend_slope(snapshots)
    anomalies = detect_anomalies(snapshots)
    edge_trends = aggregate_edge_cases(snapshots)

    return ExtractionHealthTrend(
        period_start=snapshots[0].observed_at,
        period_end=snapshots[-1].observed_at,
        granularity=granularity,
        success_rate_mean=round(statistics.mean(success_rates), 2),
        success_rate_min=round(min(success_rates), 2),
        success_rate_max=round(max(success_rates), 2),
        success_rate_std_dev=round(
            statistics.stdev(success_rates) if len(success_rates) > 1 else 0.0,
            2,
        ),
        success_rate_trend=float(trend_data["slope"]),
        complete_extraction_mean=round(statistics.mean(complete_counts), 2),
        partial_extraction_mean=round(statistics.mean(partial_counts), 2),
        no_extraction_mean=round(statistics.mean(no_extract_counts), 2),
        observation_count=len(snapshots),
        edge_case_trends=edge_trends,
        anomalies=anomalies,
    )
