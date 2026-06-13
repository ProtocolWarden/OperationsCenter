# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Data models for code coverage metrics and trend analysis.

Defines the data structures for capturing, storing, and analyzing coverage measurements
at repository, module, and file granularities.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CoverageMetric(BaseModel):
    """A single coverage measurement for a scope (repo/module/file)."""

    scope: str
    scope_type: Literal["repository", "module", "file"]
    timestamp: datetime
    source: str

    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float

    statement_count: int = 0
    branch_count: int = 0
    line_count: int = 0
    executed_statements: int = 0
    executed_branches: int = 0
    executed_lines: int = 0

    test_execution_time_ms: Optional[int] = None
    test_count: Optional[int] = None

    def get_coverage_by_type(self, coverage_type: Literal["statement", "branch", "line"]) -> float:
        """Get coverage percentage for a specific type.

        Args:
            coverage_type: Type of coverage to retrieve

        Returns:
            Coverage percentage for the specified type
        """
        if coverage_type == "statement":
            return self.statement_coverage_pct
        elif coverage_type == "branch":
            return self.branch_coverage_pct
        else:
            return self.line_coverage_pct

    def get_execution_count(self, count_type: Literal["statement", "branch", "line"]) -> int:
        """Get execution count for a specific type.

        Args:
            count_type: Type of count to retrieve

        Returns:
            Execution count for the specified type
        """
        if count_type == "statement":
            return self.executed_statements
        elif count_type == "branch":
            return self.executed_branches
        else:
            return self.executed_lines


class ModuleCoverage(BaseModel):
    """Coverage metrics for a specific module/package."""

    module_path: str
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float

    statement_count: int
    branch_count: int
    line_count: int
    executed_statements: int = 0
    executed_branches: int = 0
    executed_lines: int = 0

    health_status: Literal["healthy", "at_risk", "critical"]

    def is_healthy(self) -> bool:
        """Check if module is in healthy state.

        Returns:
            True if health_status is "healthy"
        """
        return self.health_status == "healthy"

    def is_at_risk(self) -> bool:
        """Check if module is at risk.

        Returns:
            True if health_status is "at_risk"
        """
        return self.health_status == "at_risk"

    def is_critical(self) -> bool:
        """Check if module is critical.

        Returns:
            True if health_status is "critical"
        """
        return self.health_status == "critical"

    def get_average_coverage(self) -> float:
        """Calculate average coverage across all metrics.

        Returns:
            Average of statement, branch, and line coverage percentages
        """
        return (self.statement_coverage_pct + self.branch_coverage_pct + self.line_coverage_pct) / 3


class FileCoverage(BaseModel):
    """Coverage metrics for a specific source file."""

    file_path: str
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float

    uncovered_lines: list[tuple[int, int]] = Field(default_factory=list)
    uncovered_branches: list[str] = Field(default_factory=list)

    def get_uncovered_line_count(self) -> int:
        """Calculate total number of uncovered lines.

        Returns:
            Total uncovered lines across all ranges
        """
        return sum(end - start for start, end in self.uncovered_lines)

    def is_below_threshold(self, threshold: float) -> bool:
        """Check if file coverage is below threshold.

        Args:
            threshold: Coverage percentage threshold

        Returns:
            True if line_coverage_pct is below threshold
        """
        return self.line_coverage_pct < threshold


class CoverageSnapshot(BaseModel):
    """A single point-in-time coverage measurement across all granularities."""

    timestamp: datetime
    run_id: str
    source: str

    overall_statement_coverage_pct: float
    overall_branch_coverage_pct: float
    overall_line_coverage_pct: float

    module_coverages: list[ModuleCoverage] = Field(default_factory=list)
    file_coverages: list[FileCoverage] = Field(default_factory=list)

    test_execution_time_ms: Optional[int] = None
    test_count: Optional[int] = None
    uncovered_file_count: int = 0

    def get_critical_modules(self) -> list[ModuleCoverage]:
        """Get all modules with critical health status.

        Returns:
            List of modules with health_status == "critical"
        """
        return [m for m in self.module_coverages if m.is_critical()]

    def get_at_risk_modules(self) -> list[ModuleCoverage]:
        """Get all modules with at-risk health status.

        Returns:
            List of modules with health_status == "at_risk"
        """
        return [m for m in self.module_coverages if m.is_at_risk()]

    def get_files_below_threshold(self, threshold: float) -> list[FileCoverage]:
        """Get files with coverage below threshold.

        Args:
            threshold: Coverage percentage threshold

        Returns:
            List of files with coverage below threshold
        """
        return [f for f in self.file_coverages if f.is_below_threshold(threshold)]


class CoverageTrendAnalysis(BaseModel):
    """Computed trend metrics over a time window."""

    metric_type: Literal["statement", "branch", "line"]
    granularity: Literal["repository", "module", "file"]
    scope_id: str

    window_start: datetime
    window_end: datetime

    measurements: list[tuple[datetime, float]] = Field(default_factory=list)

    current_value: float
    average_value: float
    min_value: float
    max_value: float

    trend_direction: Literal["improving", "stable", "degrading"]
    trend_pct: float
    regression_count: int = 0

    standard_deviation: float = 0.0
    stability_score: float = 0.0

    days_of_decline: int = 0
    projected_value_7days: Optional[float] = None

    def is_improving(self) -> bool:
        """Check if trend is improving.

        Returns:
            True if trend_direction is "improving"
        """
        return self.trend_direction == "improving"

    def is_degrading(self) -> bool:
        """Check if trend is degrading.

        Returns:
            True if trend_direction is "degrading"
        """
        return self.trend_direction == "degrading"

    def is_stable(self) -> bool:
        """Check if trend is stable.

        Returns:
            True if trend_direction is "stable"
        """
        return self.trend_direction == "stable"

    def get_total_change(self) -> float:
        """Calculate total change from first to last measurement.

        Returns:
            Difference between current and first measurement (or 0 if no measurements)
        """
        if not self.measurements:
            return 0.0
        return self.measurements[-1][1] - self.measurements[0][1]


class CoverageAlert(BaseModel):
    """A generated coverage alert."""

    alert_id: str
    timestamp: datetime
    alert_type: Literal["below_threshold", "regression_detected", "trend_degrading", "critical_module_coverage"]
    severity: Literal["info", "warning", "critical", "emergency"]

    metric_type: Literal["statement", "branch", "line"]
    granularity: Literal["repository", "module", "file"]
    scope_id: str

    current_value: float
    threshold_or_baseline: Optional[float] = None
    delta_pct: float

    baseline_type: Literal["minimum_threshold", "previous_run", "7day_avg", "30day_avg", "trend"]

    affected_modules: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    recommendation: Optional[str] = None

    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    dismissal_reason: Optional[str] = None

    def is_critical(self) -> bool:
        """Check if alert is critical or emergency severity.

        Returns:
            True if severity is "critical" or "emergency"
        """
        return self.severity in ("critical", "emergency")

    def is_acknowledged(self) -> bool:
        """Check if alert has been acknowledged.

        Returns:
            True if acknowledged is True
        """
        return self.acknowledged

    def is_dismissed(self) -> bool:
        """Check if alert has been dismissed.

        Returns:
            True if dismissal_reason is set
        """
        return self.dismissal_reason is not None

    def get_severity_level(self) -> int:
        """Get numeric severity level (higher = more severe).

        Returns:
            0 for info, 1 for warning, 2 for critical, 3 for emergency
        """
        severity_levels: dict[str, int] = {
            "info": 0,
            "warning": 1,
            "critical": 2,
            "emergency": 3,
        }
        return severity_levels.get(self.severity, 0)

    def exceeds_threshold(self) -> bool:
        """Check if current value exceeds configured threshold.

        Returns:
            True if current_value exceeds threshold_or_baseline
        """
        if self.threshold_or_baseline is None:
            return False
        return self.current_value > self.threshold_or_baseline

    def is_below_target(self, target_pct: float = 90.0) -> bool:
        """Check if alert indicates coverage below target.

        Args:
            target_pct: Target coverage percentage

        Returns:
            True if current_value is below target
        """
        return self.current_value < target_pct

    def get_alert_emoji(self) -> str:
        """Get emoji representation for alert severity.

        Returns:
            Emoji character(s) representing severity
        """
        emoji_map: dict[str, str] = {
            "info": "ℹ️",
            "warning": "⚠️",
            "critical": "🚨",
            "emergency": "🚨🚨",
        }
        return emoji_map.get(self.severity, "❓")

    def get_alert_type_label(self) -> str:
        """Get human-readable label for alert type.

        Returns:
            Readable alert type description
        """
        label_map: dict[str, str] = {
            "below_threshold": "Below Threshold",
            "regression_detected": "Regression Detected",
            "trend_degrading": "Trend Degrading",
            "critical_module_coverage": "Module Coverage Gap",
        }
        return label_map.get(self.alert_type, "Unknown Alert")


def compare_snapshots(current: CoverageSnapshot, previous: CoverageSnapshot) -> dict[str, float]:
    """Calculate coverage deltas between two snapshots.

    Args:
        current: Current coverage snapshot
        previous: Previous coverage snapshot

    Returns:
        Dictionary with coverage changes for each metric type
    """
    deltas: dict[str, float] = {
        "statement_delta": (
            current.overall_statement_coverage_pct - previous.overall_statement_coverage_pct
        ),
        "branch_delta": (
            current.overall_branch_coverage_pct - previous.overall_branch_coverage_pct
        ),
        "line_delta": (
            current.overall_line_coverage_pct - previous.overall_line_coverage_pct
        ),
    }
    return deltas


def is_snapshot_valid(snapshot: CoverageSnapshot) -> bool:
    """Validate that a snapshot has all required fields and reasonable values.

    Args:
        snapshot: Snapshot to validate

    Returns:
        True if snapshot is valid
    """
    has_valid_values: bool = (
        0.0 <= snapshot.overall_statement_coverage_pct <= 100.0
        and 0.0 <= snapshot.overall_branch_coverage_pct <= 100.0
        and 0.0 <= snapshot.overall_line_coverage_pct <= 100.0
    )
    has_timestamp: bool = snapshot.timestamp is not None
    has_source: bool = snapshot.source is not None
    return has_valid_values and has_timestamp and has_source


def get_baseline_coverage(baseline_type: str, current_value: float, threshold: float) -> float:
    """Get the baseline coverage value for comparison based on baseline type.

    Args:
        baseline_type: Type of baseline (minimum_threshold, previous_run, etc.)
        current_value: Current coverage value
        threshold: Configured threshold value

    Returns:
        Baseline value for comparison
    """
    if baseline_type == "minimum_threshold":
        return threshold
    elif baseline_type == "trend":
        return threshold
    else:
        return current_value
