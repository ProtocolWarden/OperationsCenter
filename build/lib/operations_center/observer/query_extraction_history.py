# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Query mixin for extraction signal history and trend analysis.

Provides query methods to retrieve historical extraction metrics, compute trends,
and detect anomalies in extraction coverage over time.

Usage:
    query = TestSignalQuery(snapshot_dir)
    history = query.get_extraction_health_history(days=7)
    trend = query.get_extraction_health_trend(period="daily")
    anomalies = query.get_extraction_anomalies(threshold_pct=5.0)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .extraction_health_history import (
    ExtractionHealthSnapshot,
    ExtractionHealthTrend,
    ExtractionHistoryStorage,
    calculate_trend_slope,
    detect_anomalies,
    snapshots_to_trend,
)

logger = logging.getLogger(__name__)


class ExtractionHistoryQueryMixin:
    """Mixin providing query methods for extraction signal history.

    Assumes the class has:
        - self.root: Path to snapshots directory
    """

    root: Path

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize mixin. Args passed to parent."""
        super().__init__(*args, **kwargs)
        self._extraction_storage: ExtractionHistoryStorage | None = None

    def _get_extraction_storage(self) -> ExtractionHistoryStorage:
        """Get or create extraction history storage manager.

        Uses ``getattr`` rather than reading ``self._extraction_storage``
        directly: when this mixin is combined with another (e.g. TestSignalQuery
        mixes FlakyTestQueryMixin first), the MRO may not run this mixin's
        ``__init__``, leaving the attribute unset. Lazily initializing here makes
        the query methods work regardless of cooperative-__init__ ordering.

        Returns:
            ExtractionHistoryStorage instance for the snapshot directory.
        """
        storage = getattr(self, "_extraction_storage", None)
        if storage is None:
            storage = ExtractionHistoryStorage(self.root / "extraction_history")
            self._extraction_storage = storage
        return storage

    def get_extraction_health_history(self, days: int = 7) -> list[ExtractionHealthSnapshot]:
        """Get extraction health history for past N days.

        Args:
            days: Number of days to look back (default: 7).

        Returns:
            List of ExtractionHealthSnapshot objects in chronological order.
        """
        storage = self._get_extraction_storage()
        return storage.load_snapshots_range(days=days)

    def get_extraction_health_history_since(
        self, since: datetime
    ) -> list[ExtractionHealthSnapshot]:
        """Get extraction health history since a specific timestamp.

        Args:
            since: Datetime to load snapshots after.

        Returns:
            List of ExtractionHealthSnapshot objects in chronological order.
        """
        storage = self._get_extraction_storage()
        return storage.load_snapshots_since(since)

    def get_extraction_health_trend(
        self, days: int = 7, granularity: str = "daily"
    ) -> ExtractionHealthTrend | None:
        """Get aggregated extraction trend over time period.

        Args:
            days: Number of days to analyze (default: 7).
            granularity: "daily", "weekly", or "monthly" for metadata.

        Returns:
            ExtractionHealthTrend with aggregated metrics and trend analysis.
            Returns None if insufficient data.
        """
        snapshots = self.get_extraction_health_history(days=days)

        if len(snapshots) < 2:
            logger.debug(
                "Insufficient snapshots for trend analysis: %d < 2",
                len(snapshots),
            )
            return None

        return snapshots_to_trend(snapshots, granularity=granularity)

    def get_extraction_trend_slope(self, days: int = 7) -> dict[str, float | str]:
        """Get linear regression trend slope for success_rate.

        Args:
            days: Number of days to analyze (default: 7).

        Returns:
            Dict with keys:
                - "slope": % per day
                - "r_squared": Model fit quality (0-1)
                - "confidence": "improving", "degrading", "stable", "uncertain"
            Returns dict with zero values if insufficient data.
        """
        snapshots = self.get_extraction_health_history(days=days)

        if len(snapshots) < 2:
            return {"slope": 0.0, "r_squared": 0.0, "confidence": "uncertain"}

        return calculate_trend_slope(snapshots)

    def get_extraction_anomalies(
        self, days: int = 7, threshold_pct: float = 5.0
    ) -> list[dict[str, Any]]:
        """Get detected anomalies in extraction metrics.

        Args:
            days: Number of days to analyze (default: 7).
            threshold_pct: Threshold for anomaly detection in % (default: 5.0).
                Currently unused; anomalies are fixed at 5% threshold.

        Returns:
            List of anomalies detected. Each dict has keys:
                - "type": "spike_down" or "spike_up"
                - "timestamp": ISO 8601 timestamp
                - "metric": "success_rate"
                - "delta_pct": Change from moving average
                - "previous_avg": Moving average before anomaly
                - "current_value": Value at anomaly point
        """
        snapshots = self.get_extraction_health_history(days=days)

        if len(snapshots) < 10:  # Need enough data for moving average
            logger.debug(
                "Insufficient snapshots for anomaly detection: %d < 10",
                len(snapshots),
            )
            return []

        return detect_anomalies(snapshots)

    def cleanup_old_extraction_history(self) -> int:
        """Remove extraction history snapshots older than retention period.

        Returns:
            Count of deleted snapshots.
        """
        storage = self._get_extraction_storage()
        return storage.cleanup_old_snapshots()
