# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Manager for coverage trend storage, retrieval, and analysis operations."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import TYPE_CHECKING

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
)
from operations_center.observer.coverage_trend_repository import (
    CoverageTrendRepository,
    HTTPCoverageTrendRepository,
    LocalCoverageTrendRepository,
    S3CoverageTrendRepository,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class CoverageTrendManager:
    """High-level API for coverage trend storage and analysis."""

    def __init__(
        self,
        repository: CoverageTrendRepository,
    ):
        self.repository = repository

    @classmethod
    def create_local(
        cls,
        root: Path | None = None,
        retention_days: int = 30,
    ) -> CoverageTrendManager:
        """Create a manager with local filesystem storage."""
        repo = LocalCoverageTrendRepository(
            root=root,
            retention_days=retention_days,
        )
        return cls(repo)

    @classmethod
    def create_s3(
        cls,
        bucket: str,
        prefix: str = "coverage-trends",
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> CoverageTrendManager:
        """Create a manager with S3 storage."""
        repo = S3CoverageTrendRepository(
            bucket=bucket,
            prefix=prefix,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
        )
        return cls(repo)

    @classmethod
    def create_http(
        cls,
        base_url: str,
        token: str | None = None,
    ) -> CoverageTrendManager:
        """Create a manager with HTTP storage."""
        repo = HTTPCoverageTrendRepository(
            base_url=base_url,
            token=token,
        )
        return cls(repo)

    # Snapshot operations
    def save_snapshot(self, snapshot: CoverageSnapshot) -> None:
        """Save a coverage metrics snapshot."""
        self.repository.store_snapshot(snapshot)

    def get_snapshot(self, run_id: str) -> CoverageSnapshot | None:
        """Retrieve a snapshot by run_id."""
        try:
            return self.repository.load_snapshot(run_id)
        except FileNotFoundError:
            return None

    def list_snapshots(
        self,
        limit: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[CoverageSnapshot]:
        """List snapshots within optional date range."""
        metadata_list = self.repository.list_snapshots(
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )

        snapshots = []
        for metadata in metadata_list:
            try:
                snapshot = self.repository.load_snapshot(metadata["run_id"])
                snapshots.append(snapshot)
            except FileNotFoundError:
                continue

        return snapshots

    def delete_snapshot(self, run_id: str) -> bool:
        """Delete a snapshot."""
        return self.repository.delete_snapshot(run_id)

    # Trend analysis operations
    def save_trend_analysis(self, analysis: CoverageTrendAnalysis) -> None:
        """Save trend analysis data."""
        self.repository.store_trend_analysis(analysis)

    def get_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
    ) -> CoverageTrendAnalysis | None:
        """Retrieve trend analysis for a specific scope."""
        return self.repository.load_trend_analysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id,
        )

    # Alert operations
    def save_alert(self, alert: CoverageAlert) -> None:
        """Save a coverage alert."""
        self.repository.store_alert(alert)

    def list_alerts(
        self,
        limit: int | None = None,
        severity: str | None = None,
    ) -> list[CoverageAlert]:
        """List recent alerts, optionally filtered by severity."""
        return self.repository.list_alerts(limit=limit, severity=severity)

    # Trend analysis methods
    def compute_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
        window_days: int = 7,
    ) -> CoverageTrendAnalysis:
        """Compute trend analysis for a metric and scope over a time window."""
        end_date = datetime.now(tz=timezone.utc)
        start_date = end_date - timedelta(days=window_days)

        snapshots = self.list_snapshots(start_date=start_date, end_date=end_date)

        measurements: list[tuple[datetime, float]] = []

        for snapshot in snapshots:
            value = self._extract_metric_value(
                snapshot, metric_type, granularity, scope_id
            )
            if value is not None:
                measurements.append((snapshot.timestamp, value))

        measurements.sort(key=lambda x: x[0])

        if not measurements:
            return CoverageTrendAnalysis(
                metric_type=metric_type,
                granularity=granularity,
                scope_id=scope_id or "",
                window_start=start_date,
                window_end=end_date,
                measurements=[],
                current_value=0.0,
                average_value=0.0,
                min_value=0.0,
                max_value=0.0,
                trend_direction="stable",
                trend_pct=0.0,
                standard_deviation=0.0,
                stability_score=0.0,
            )

        values = [v for _, v in measurements]
        current_value = values[-1]
        average_value = mean(values)
        min_value = min(values)
        max_value = max(values)

        std_dev = stdev(values) if len(values) > 1 else 0.0
        stability_score = 1.0 - (std_dev / average_value) if average_value > 0 else 0.0
        stability_score = max(0.0, min(1.0, stability_score))

        trend_direction = "stable"
        trend_pct = 0.0
        regression_count = 0
        days_of_decline = 0

        if len(measurements) > 1:
            first_value = values[0]
            if current_value < first_value - 0.1:
                trend_direction = "degrading"
            elif current_value > first_value + 0.1:
                trend_direction = "improving"

            trend_pct = ((current_value - average_value) / average_value * 100) if average_value > 0 else 0.0

            for i in range(1, len(values)):
                if values[i] < values[i - 1]:
                    regression_count += 1

            for i in range(1, len(values)):
                if values[i] < values[i - 1]:
                    days_of_decline += 1

        projected_value_7days = None
        if len(values) >= 2 and trend_pct != 0:
            slope = (values[-1] - values[0]) / max(len(values) - 1, 1)
            projected_value_7days = current_value + (slope * 7)

        return CoverageTrendAnalysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id or "",
            window_start=start_date,
            window_end=end_date,
            measurements=measurements,
            current_value=current_value,
            average_value=average_value,
            min_value=min_value,
            max_value=max_value,
            trend_direction=trend_direction,
            trend_pct=trend_pct,
            regression_count=regression_count,
            standard_deviation=std_dev,
            stability_score=stability_score,
            days_of_decline=days_of_decline,
            projected_value_7days=projected_value_7days,
        )

    def detect_regression(
        self,
        current_snapshot: CoverageSnapshot,
        metric_type: str,
        threshold_pct: float = 2.0,
    ) -> bool:
        """Detect if coverage has regressed compared to previous measurement."""
        snapshots = self.list_snapshots(limit=2)
        if len(snapshots) < 2:
            return False

        previous = snapshots[1]
        current = snapshots[0]

        current_value = self._extract_metric_value(
            current, metric_type, "repository", None
        )
        previous_value = self._extract_metric_value(
            previous, metric_type, "repository", None
        )

        if current_value is None or previous_value is None:
            return False

        delta = current_value - previous_value
        return delta < -threshold_pct

    def calculate_trend_slope(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
        window_days: int = 7,
    ) -> float:
        """Calculate the slope of coverage trend (% per day)."""
        analysis = self.compute_trend_analysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id,
            window_days=window_days,
        )

        if len(analysis.measurements) < 2:
            return 0.0

        values = [v for _, v in analysis.measurements]
        days = len(analysis.measurements) - 1
        if days <= 0:
            return 0.0

        return (values[-1] - values[0]) / days

    def calculate_volatility_score(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
        window_days: int = 7,
    ) -> float:
        """Calculate volatility score (0-1, higher = more volatile)."""
        analysis = self.compute_trend_analysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id,
            window_days=window_days,
        )

        if analysis.average_value == 0:
            return 0.0

        cv = (analysis.standard_deviation / analysis.average_value) * 100
        return min(1.0, cv / 100.0)

    def get_historical_data(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[tuple[datetime, float]]:
        """Get historical coverage data for a metric."""
        snapshots = self.list_snapshots(start_date=start_date, end_date=end_date)

        data = []
        for snapshot in snapshots:
            value = self._extract_metric_value(
                snapshot, metric_type, granularity, scope_id
            )
            if value is not None:
                data.append((snapshot.timestamp, value))

        data.sort(key=lambda x: x[0])
        return data

    def _extract_metric_value(
        self,
        snapshot: CoverageSnapshot,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
    ) -> float | None:
        """Extract a metric value from a snapshot."""
        if granularity == "repository":
            if metric_type == "statement":
                return snapshot.overall_statement_coverage_pct
            elif metric_type == "branch":
                return snapshot.overall_branch_coverage_pct
            elif metric_type == "line":
                return snapshot.overall_line_coverage_pct
        elif granularity == "module" and scope_id:
            for module in snapshot.module_coverages:
                if module.module_path == scope_id:
                    if metric_type == "statement":
                        return module.statement_coverage_pct
                    elif metric_type == "branch":
                        return module.branch_coverage_pct
                    elif metric_type == "line":
                        return module.line_coverage_pct
        elif granularity == "file" and scope_id:
            for file in snapshot.file_coverages:
                if file.file_path == scope_id:
                    if metric_type == "statement":
                        return file.statement_coverage_pct
                    elif metric_type == "branch":
                        return file.branch_coverage_pct
                    elif metric_type == "line":
                        return file.line_coverage_pct

        return None

    def cleanup(self, retention_days: int = 30) -> list[str]:
        """Clean up old data based on retention policy."""
        return self.repository.cleanup(retention_days=retention_days)
