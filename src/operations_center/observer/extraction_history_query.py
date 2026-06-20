# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Query and retrieval API for extraction signal history.

Provides methods to fetch, filter, paginate, and aggregate historical extraction metrics
(success_rate, extraction counts) from stored snapshots. Follows the FlakyTestQueryMixin
pattern for consistency with existing query APIs in the codebase.

Usage:
    # Create a query interface
    storage = ExtractionHistoryStorage.create_local("/var/extraction-history")
    query = ExtractionHistoryQuery(storage)

    # Fetch paginated historical data
    page = query.get_success_rate_history(days=7, limit=20, offset=0)
    for snapshot in page.snapshots:
        print(f"{snapshot.observed_at}: {snapshot.success_rate}%")

    # Get aggregated trends at different granularities
    daily_trend = query.get_success_rate_trend(days=30, granularity="daily")
    print(f"Trend slope: {daily_trend.success_rate_trend}% per day")

    # Detect anomalies
    anomalies = query.detect_anomalies(days=7)
    for anomaly in anomalies:
        print(f"Anomaly: {anomaly['type']} at {anomaly['timestamp']}")
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operations_center.observer.extraction_health_history import (
        ExtractionHealthSnapshot,
        ExtractionHealthTrend,
        ExtractionHistoryStorage,
    )

logger = logging.getLogger(__name__)


@dataclass
class SuccessRateHistoryPage:
    """Paginated result of historical success_rate snapshots.

    Attributes:
        snapshots: List of ExtractionHealthSnapshot objects.
        total_count: Total number of snapshots available (across all pages).
        offset: Starting position of this page (0-based).
        limit: Maximum number of snapshots requested.
        has_more: Whether more results are available after this page.
    """

    snapshots: list[ExtractionHealthSnapshot] = dataclass_field(default_factory=list)
    total_count: int = 0
    offset: int = 0
    limit: int = 20
    has_more: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "snapshots": [s.to_dict() for s in self.snapshots],
            "total_count": self.total_count,
            "offset": self.offset,
            "limit": self.limit,
            "has_more": self.has_more,
        }


@dataclass
class AnomalyResult:
    """Detection result for a single anomaly.

    Attributes:
        anomaly_type: Type of anomaly ("spike_down", "spike_up", "sustained_drop", "sustained_rise").
        timestamp: When the anomaly was detected.
        metric: What metric was affected ("success_rate", "extracted_count", etc.).
        value: The anomalous value.
        baseline: Expected baseline value.
        delta_pct: Percentage change from baseline.
    """

    anomaly_type: str
    timestamp: datetime
    metric: str
    value: float
    baseline: float
    delta_pct: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "anomaly_type": self.anomaly_type,
            "timestamp": self.timestamp.isoformat(),
            "metric": self.metric,
            "value": self.value,
            "baseline": self.baseline,
            "delta_pct": round(self.delta_pct, 2),
        }


class ExtractionHistoryQuery:
    """Query interface for extraction signal history.

    Provides methods to retrieve, filter, paginate, and analyze historical
    extraction metrics stored in the ExtractionHistoryStorage.

    This class follows the same patterns as FlakyTestQueryMixin for consistency
    with other query APIs in the codebase.
    """

    def __init__(self, storage: ExtractionHistoryStorage):
        """Initialize query interface.

        Args:
            storage: ExtractionHistoryStorage instance for data access.
        """
        self.storage = storage

    def get_success_rate_history(
        self,
        days: int = 7,
        limit: int = 100,
        offset: int = 0,
    ) -> SuccessRateHistoryPage:
        """Fetch paginated historical success_rate data.

        Retrieves snapshots from the past N days with pagination support.
        Results are ordered chronologically (oldest first).

        Args:
            days: Number of days to look back (default: 7).
            limit: Maximum snapshots per page (default: 100, max: 1000).
            offset: Starting position (0-based, default: 0).

        Returns:
            SuccessRateHistoryPage with paginated snapshots and metadata.
            Returns empty snapshots list if no data available.
        """
        # Clamp limit to reasonable max to prevent memory exhaustion
        limit = min(limit, 1000)
        limit = max(limit, 1)  # At least 1
        offset = max(offset, 0)  # At least 0

        snapshots = self.storage.load_snapshots_range(days=days)

        # Create page result
        total_count = len(snapshots)
        page_snapshots = snapshots[offset : offset + limit]
        has_more = (offset + limit) < total_count

        return SuccessRateHistoryPage(
            snapshots=page_snapshots,
            total_count=total_count,
            offset=offset,
            limit=limit,
            has_more=has_more,
        )

    def get_success_rate_trend(
        self,
        days: int = 30,
        granularity: str = "daily",
    ) -> ExtractionHealthTrend:
        """Compute aggregated success_rate trend over a time period.

        Aggregates snapshots at the specified granularity level (hourly, daily,
        weekly, monthly) and computes trend metrics including mean, min, max,
        standard deviation, and linear regression slope.

        Args:
            days: Number of days to analyze (default: 30).
            granularity: Aggregation level - "hourly", "daily", "weekly", "monthly" (default: "daily").

        Returns:
            ExtractionHealthTrend with aggregated metrics and trend analysis.
            Returns None if insufficient data for trend calculation.

        Raises:
            ValueError: If granularity is not one of the supported values.
        """
        if granularity not in ("hourly", "daily", "weekly", "monthly"):
            raise ValueError(
                f"Unsupported granularity: {granularity}. Must be one of: hourly, daily, weekly, monthly"
            )

        snapshots = self.storage.load_snapshots_range(days=days)

        if not snapshots:
            # Return empty trend
            now = datetime.now(UTC)
            cutoff = now - timedelta(days=days)
            return self._create_empty_trend(cutoff, now, granularity)

        # Group snapshots by time bucket
        buckets = self._bucket_snapshots_by_granularity(snapshots, granularity)

        # Aggregate metrics in each bucket
        aggregated_metrics = []
        for bucket_key, bucket_snapshots in buckets.items():
            metrics = self._aggregate_bucket(bucket_snapshots)
            aggregated_metrics.append(metrics)

        # Compute trend statistics
        success_rates = [m["success_rate"] for m in aggregated_metrics]
        complete_counts = [m["complete_extraction"] for m in aggregated_metrics]
        partial_counts = [m["partial_extraction"] for m in aggregated_metrics]
        no_extract_counts = [m["no_extraction"] for m in aggregated_metrics]

        success_rate_mean = statistics.mean(success_rates) if success_rates else 0.0
        success_rate_min = min(success_rates) if success_rates else 0.0
        success_rate_max = max(success_rates) if success_rates else 0.0
        success_rate_std_dev = statistics.stdev(success_rates) if len(success_rates) > 1 else 0.0

        # Compute linear regression trend (% per day)
        trend_slope = self._compute_trend_slope(snapshots, granularity)

        # Compute means for extraction counts
        complete_mean = statistics.mean(complete_counts) if complete_counts else 0.0
        partial_mean = statistics.mean(partial_counts) if partial_counts else 0.0
        no_extract_mean = statistics.mean(no_extract_counts) if no_extract_counts else 0.0

        # Import here to avoid circular imports
        from operations_center.observer.extraction_health_history import (
            ExtractionHealthTrend,
        )

        trend = ExtractionHealthTrend(
            period_start=snapshots[0].observed_at,
            period_end=snapshots[-1].observed_at,
            granularity=granularity,
            success_rate_mean=success_rate_mean,
            success_rate_min=success_rate_min,
            success_rate_max=success_rate_max,
            success_rate_std_dev=success_rate_std_dev,
            success_rate_trend=trend_slope,
            complete_extraction_mean=complete_mean,
            partial_extraction_mean=partial_mean,
            no_extraction_mean=no_extract_mean,
            observation_count=len(snapshots),
        )

        return trend

    def get_recent_snapshots(self, count: int = 10) -> list[ExtractionHealthSnapshot]:
        """Get the N most recent snapshots.

        Args:
            count: Number of recent snapshots to retrieve (default: 10, max: 1000).

        Returns:
            List of ExtractionHealthSnapshot objects, most recent last.
            Returns empty list if no data available.
        """
        count = min(count, 1000)
        count = max(count, 1)

        all_snapshots = self.storage.load_all_snapshots()
        return all_snapshots[-count:] if all_snapshots else []

    def detect_anomalies(
        self,
        days: int = 7,
        threshold_pct: float = 5.0,
    ) -> list[AnomalyResult]:
        """Detect anomalies in success_rate data using moving average.

        Identifies sudden changes or sustained deviations in success_rate
        that exceed the threshold percentage.

        Args:
            days: Number of days to analyze (default: 7).
            threshold_pct: Minimum percentage change to flag as anomaly (default: 5%).

        Returns:
            List of AnomalyResult objects, sorted by timestamp.
            Returns empty list if no anomalies detected or insufficient data.
        """
        snapshots = self.storage.load_snapshots_range(days=days)

        if len(snapshots) < 3:
            return []  # Need at least 3 points for moving average

        anomalies: list[AnomalyResult] = []

        # Compute moving average baseline (window size = 3 snapshots)
        for i in range(2, len(snapshots)):
            baseline = statistics.mean(
                [
                    snapshots[i - 2].success_rate,
                    snapshots[i - 1].success_rate,
                    snapshots[i].success_rate,
                ]
            )
            current_value = snapshots[i].success_rate

            delta_pct = abs(current_value - baseline)

            if delta_pct > threshold_pct:
                anomaly_type = "spike_up" if current_value > baseline else "spike_down"

                anomalies.append(
                    AnomalyResult(
                        anomaly_type=anomaly_type,
                        timestamp=snapshots[i].observed_at,
                        metric="success_rate",
                        value=current_value,
                        baseline=baseline,
                        delta_pct=delta_pct,
                    )
                )

        return anomalies

    # Private helper methods

    def _bucket_snapshots_by_granularity(
        self,
        snapshots: list[ExtractionHealthSnapshot],
        granularity: str,
    ) -> dict[tuple[int, int, int, int], list[ExtractionHealthSnapshot]]:
        """Group snapshots into time buckets based on granularity.

        Returns a dict mapping (year, month, day, hour) to snapshots in that bucket.
        The tuple size depends on granularity (e.g., hourly includes hour, daily excludes it).
        """
        buckets: dict[tuple[int, int, int, int], list[ExtractionHealthSnapshot]] = {}

        for snapshot in snapshots:
            ts = snapshot.observed_at
            if granularity == "hourly":
                key = (ts.year, ts.month, ts.day, ts.hour)
            elif granularity == "daily":
                key = (ts.year, ts.month, ts.day, 0)  # 0 for hour (unused)
            elif granularity == "weekly":
                # ISO week number
                iso_week = ts.isocalendar()
                key = (iso_week[0], iso_week[1], 0, 0)
            elif granularity == "monthly":
                key = (ts.year, ts.month, 0, 0)
            else:
                key = (ts.year, ts.month, ts.day, 0)

            if key not in buckets:
                buckets[key] = []
            buckets[key].append(snapshot)

        return buckets

    def _aggregate_bucket(self, snapshots: list[ExtractionHealthSnapshot]) -> dict[str, float]:
        """Aggregate metrics for a bucket of snapshots."""
        if not snapshots:
            return {
                "success_rate": 0.0,
                "complete_extraction": 0.0,
                "partial_extraction": 0.0,
                "no_extraction": 0.0,
            }

        success_rates = [s.success_rate for s in snapshots]
        complete_counts = [s.complete_extraction for s in snapshots]
        partial_counts = [s.partial_extraction for s in snapshots]
        no_extract_counts = [s.no_extraction for s in snapshots]

        return {
            "success_rate": statistics.mean(success_rates),
            "complete_extraction": statistics.mean(complete_counts),
            "partial_extraction": statistics.mean(partial_counts),
            "no_extraction": statistics.mean(no_extract_counts),
        }

    def _compute_trend_slope(
        self,
        snapshots: list[ExtractionHealthSnapshot],
        granularity: str,
    ) -> float:
        """Compute linear regression slope for success_rate over time.

        Returns percentage change per day. Positive = improving, negative = degrading.
        """
        if len(snapshots) < 2:
            return 0.0

        # Convert timestamps to days since first observation
        first_time = snapshots[0].observed_at
        x_values = [(s.observed_at - first_time).total_seconds() / (24 * 3600) for s in snapshots]
        y_values = [s.success_rate for s in snapshots]

        # Simple linear regression: y = mx + b
        n = len(x_values)
        x_mean = statistics.mean(x_values)
        y_mean = statistics.mean(y_values)

        numerator = sum((x_values[i] - x_mean) * (y_values[i] - y_mean) for i in range(n))
        denominator = sum((x_values[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        slope = numerator / denominator
        return slope

    def _create_empty_trend(
        self,
        period_start: datetime,
        period_end: datetime,
        granularity: str,
    ) -> ExtractionHealthTrend:
        """Create an empty trend object for periods with no data."""
        from operations_center.observer.extraction_health_history import (
            ExtractionHealthTrend,
        )

        return ExtractionHealthTrend(
            period_start=period_start,
            period_end=period_end,
            granularity=granularity,
            success_rate_mean=0.0,
            success_rate_min=0.0,
            success_rate_max=0.0,
            success_rate_std_dev=0.0,
            success_rate_trend=0.0,
            complete_extraction_mean=0.0,
            partial_extraction_mean=0.0,
            no_extraction_mean=0.0,
            observation_count=0,
        )
