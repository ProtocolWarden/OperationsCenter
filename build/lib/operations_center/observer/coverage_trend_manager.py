# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Manager for coverage trend storage, retrieval, and analysis operations."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import TYPE_CHECKING, Literal

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

logger: logging.Logger = logging.getLogger(__name__)


class CoverageTrendManager:
    """High-level API for coverage trend storage and analysis."""

    def __init__(
        self,
        repository: CoverageTrendRepository,
    ) -> None:
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
        metadata_list: list[dict[str, str | int]] = self.repository.list_snapshots(
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )

        snapshots: list[CoverageSnapshot] = []
        for metadata in metadata_list:
            try:
                snapshot: CoverageSnapshot = self.repository.load_snapshot(str(metadata["run_id"]))
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
        metric_type: Literal["statement", "branch", "line"],
        granularity: Literal["repository", "module", "file"],
        scope_id: str | None = None,
        window_days: int = 7,
    ) -> CoverageTrendAnalysis:
        """Compute trend analysis for a metric and scope over a time window."""
        end_date: datetime = datetime.now(tz=timezone.utc)
        start_date: datetime = end_date - timedelta(days=window_days)

        snapshots: list[CoverageSnapshot] = self.list_snapshots(
            start_date=start_date, end_date=end_date
        )

        measurements: list[tuple[datetime, float]] = []

        for snapshot in snapshots:
            value: float | None = self._extract_metric_value(
                snapshot, metric_type, granularity, scope_id
            )
            if value is not None:
                measurements.append((snapshot.timestamp, value))

        measurements.sort(key=lambda x: x[0])

        if not measurements:
            empty_analysis: CoverageTrendAnalysis = CoverageTrendAnalysis(
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
            return empty_analysis

        values: list[float] = [v for _, v in measurements]
        current_value: float = values[-1]
        average_value: float = mean(values)
        min_value: float = min(values)
        max_value: float = max(values)

        std_dev: float = stdev(values) if len(values) > 1 else 0.0
        stability_score: float = 1.0 - (std_dev / average_value) if average_value > 0 else 0.0
        stability_score = max(0.0, min(1.0, stability_score))

        trend_direction: Literal["improving", "stable", "degrading"] = "stable"
        trend_pct: float = 0.0
        regression_count: int = 0
        days_of_decline: int = 0

        if len(measurements) > 1:
            first_value: float = values[0]
            delta_from_first: float = current_value - first_value
            if delta_from_first < -0.1:
                trend_direction = "degrading"
            elif delta_from_first > 0.1:
                trend_direction = "improving"

            trend_pct = (
                ((current_value - average_value) / average_value * 100)
                if average_value > 0
                else 0.0
            )

            for i in range(1, len(values)):
                if values[i] < values[i - 1]:
                    regression_count += 1

            for i in range(1, len(values)):
                if values[i] < values[i - 1]:
                    days_of_decline += 1

        projected_value_7days: float | None = None
        if len(values) >= 2 and trend_pct != 0:
            slope: float = (values[-1] - values[0]) / max(len(values) - 1, 1)
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
        metric_type: Literal["statement", "branch", "line"],
        threshold_pct: float = 2.0,
    ) -> bool:
        """Detect if coverage has regressed compared to previous measurement."""
        snapshots: list[CoverageSnapshot] = self.list_snapshots(limit=2)
        if len(snapshots) < 2:
            return False

        previous: CoverageSnapshot = snapshots[1]
        current: CoverageSnapshot = snapshots[0]

        current_value: float | None = self._extract_metric_value(
            current, metric_type, "repository", None
        )
        previous_value: float | None = self._extract_metric_value(
            previous, metric_type, "repository", None
        )

        if current_value is None or previous_value is None:
            return False

        delta: float = current_value - previous_value
        is_regression: bool = delta < -threshold_pct
        return is_regression

    def calculate_trend_slope(
        self,
        metric_type: Literal["statement", "branch", "line"],
        granularity: Literal["repository", "module", "file"],
        scope_id: str | None = None,
        window_days: int = 7,
    ) -> float:
        """Calculate the slope of coverage trend (% per day)."""
        analysis: CoverageTrendAnalysis = self.compute_trend_analysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id,
            window_days=window_days,
        )

        if len(analysis.measurements) < 2:
            return 0.0

        values: list[float] = [v for _, v in analysis.measurements]
        days: int = len(analysis.measurements) - 1
        if days <= 0:
            return 0.0

        slope: float = (values[-1] - values[0]) / days
        return slope

    def calculate_volatility_score(
        self,
        metric_type: Literal["statement", "branch", "line"],
        granularity: Literal["repository", "module", "file"],
        scope_id: str | None = None,
        window_days: int = 7,
    ) -> float:
        """Calculate volatility score (0-1, higher = more volatile)."""
        analysis: CoverageTrendAnalysis = self.compute_trend_analysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id,
            window_days=window_days,
        )

        if analysis.average_value == 0:
            return 0.0

        cv: float = (analysis.standard_deviation / analysis.average_value) * 100
        volatility: float = min(1.0, cv / 100.0)
        return volatility

    def get_historical_data(
        self,
        metric_type: Literal["statement", "branch", "line"],
        granularity: Literal["repository", "module", "file"],
        scope_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[tuple[datetime, float]]:
        """Get historical coverage data for a metric."""
        snapshots: list[CoverageSnapshot] = self.list_snapshots(
            start_date=start_date, end_date=end_date
        )

        data: list[tuple[datetime, float]] = []
        for snapshot in snapshots:
            value: float | None = self._extract_metric_value(
                snapshot, metric_type, granularity, scope_id
            )
            if value is not None:
                data_point: tuple[datetime, float] = (snapshot.timestamp, value)
                data.append(data_point)

        data.sort(key=lambda x: x[0])
        return data

    def _extract_metric_value(
        self,
        snapshot: CoverageSnapshot,
        metric_type: Literal["statement", "branch", "line"],
        granularity: Literal["repository", "module", "file"],
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
            for file_cov in snapshot.file_coverages:
                if file_cov.file_path == scope_id:
                    if metric_type == "statement":
                        return file_cov.statement_coverage_pct
                    elif metric_type == "branch":
                        return file_cov.branch_coverage_pct
                    elif metric_type == "line":
                        return file_cov.line_coverage_pct

        return None

    def cleanup(self, retention_days: int = 30) -> list[str]:
        """Clean up old data based on retention policy."""
        return self.repository.cleanup(retention_days=retention_days)

    def is_trend_stable(
        self, metric_type: Literal["statement", "branch", "line"], threshold: float = 1.0
    ) -> bool:
        """Determine if trend is stable (low variance).

        Args:
            metric_type: Type of metric to check
            threshold: Maximum allowable variance percentage

        Returns:
            True if trend variance is below threshold
        """
        analysis: CoverageTrendAnalysis = self.compute_trend_analysis(
            metric_type=metric_type, granularity="repository"
        )
        is_stable_trend: bool = analysis.stability_score >= (1.0 - threshold / 100.0)
        return is_stable_trend

    def predict_future_coverage(
        self,
        metric_type: Literal["statement", "branch", "line"],
        granularity: Literal["repository", "module", "file"],
        days_ahead: int = 7,
        scope_id: str | None = None,
    ) -> float:
        """Predict coverage value N days in the future.

        Args:
            metric_type: Type of metric to predict
            granularity: Granularity level
            days_ahead: Number of days to project forward
            scope_id: Scope identifier if granularity is module/file

        Returns:
            Predicted coverage percentage
        """
        analysis: CoverageTrendAnalysis = self.compute_trend_analysis(
            metric_type=metric_type,
            granularity=granularity,
            scope_id=scope_id,
        )

        if len(analysis.measurements) < 2:
            return analysis.current_value

        values: list[float] = [v for _, v in analysis.measurements]
        slope: float = (values[-1] - values[0]) / max(len(values) - 1, 1)
        predicted: float = analysis.current_value + (slope * days_ahead)
        predicted = max(0.0, min(100.0, predicted))
        return predicted

    def get_improvement_rate(
        self,
        metric_type: Literal["statement", "branch", "line"],
        window_days: int = 7,
    ) -> float:
        """Calculate how much coverage has improved per day.

        Args:
            metric_type: Type of metric
            window_days: Time window for calculation

        Returns:
            Improvement rate (% per day, negative if degrading)
        """
        analysis: CoverageTrendAnalysis = self.compute_trend_analysis(
            metric_type=metric_type,
            granularity="repository",
            window_days=window_days,
        )

        if len(analysis.measurements) < 2:
            return 0.0

        values: list[float] = [v for _, v in analysis.measurements]
        rate: float = (values[-1] - values[0]) / len(values)
        return rate

    def get_critical_modules(
        self, snapshot: CoverageSnapshot, threshold: float = 70.0
    ) -> list[str]:
        """Get list of modules below critical threshold.

        Args:
            snapshot: Coverage snapshot to analyze
            threshold: Coverage threshold for critical status

        Returns:
            List of module paths below threshold
        """
        critical: list[str] = []
        for module in snapshot.module_coverages:
            if module.statement_coverage_pct < threshold:
                critical.append(module.module_path)
        return critical

    def should_escalate_alert(self, trend: CoverageTrendAnalysis, alert_count: int) -> bool:
        """Determine if alert should be escalated based on trend and frequency.

        Args:
            trend: Trend analysis for the metric
            alert_count: Number of recent alerts

        Returns:
            True if alert warrants escalation
        """
        is_degrading: bool = trend.trend_direction == "degrading"
        high_frequency: bool = alert_count >= 3
        return is_degrading and high_frequency


def calculate_measurements_average(measurements: list[tuple[datetime, float]]) -> float:
    """Calculate average value from measurement list.

    Args:
        measurements: List of (timestamp, value) tuples

    Returns:
        Average value across all measurements
    """
    if not measurements:
        return 0.0
    values: list[float] = [v for _, v in measurements]
    average: float = sum(values) / len(values) if values else 0.0
    return average
