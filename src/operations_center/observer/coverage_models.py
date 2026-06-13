# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Data models for code coverage metrics and trend analysis.

Defines the data structures for capturing, storing, and analyzing coverage measurements
at repository, module, and file granularities.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CoverageMetric(BaseModel):
    """A single coverage measurement for a scope (repo/module/file)."""

    scope: str  # "" (repo), "src/module" (module), "src/file.py" (file)
    scope_type: str  # "repository", "module", "file"
    timestamp: datetime
    source: str  # "coverage.py", "pytest-cov", "jacoco", etc.

    # Coverage percentages
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float

    # Counts for detailed analysis
    statement_count: int = 0
    branch_count: int = 0
    line_count: int = 0
    executed_statements: int = 0
    executed_branches: int = 0
    executed_lines: int = 0

    # Metadata
    test_execution_time_ms: Optional[int] = None
    test_count: Optional[int] = None


class ModuleCoverage(BaseModel):
    """Coverage metrics for a specific module/package."""

    module_path: str  # "src/operations_center/observer"
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float

    # Counts for detailed analysis
    statement_count: int
    branch_count: int
    line_count: int
    executed_statements: int = 0
    executed_branches: int = 0
    executed_lines: int = 0

    # Derived status
    health_status: str  # "healthy" (>80%), "at_risk" (70-80%), "critical" (<70%)


class FileCoverage(BaseModel):
    """Coverage metrics for a specific source file."""

    file_path: str  # "src/observer.py"
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float

    # Granular details
    uncovered_lines: list[tuple[int, int]] = Field(default_factory=list)  # [(start, end), ...]
    uncovered_branches: list[str] = Field(default_factory=list)  # Condition descriptions


class CoverageSnapshot(BaseModel):
    """A single point-in-time coverage measurement across all granularities."""

    timestamp: datetime
    run_id: str  # Git commit SHA or test run ID
    source: str  # "coverage.py", "jacoco", etc.

    # Repository-level aggregates
    overall_statement_coverage_pct: float
    overall_branch_coverage_pct: float
    overall_line_coverage_pct: float

    # Module-level breakdown
    module_coverages: list[ModuleCoverage] = Field(default_factory=list)

    # File-level details (optional, for deep diagnostics)
    file_coverages: list[FileCoverage] = Field(default_factory=list)

    # Metadata
    test_execution_time_ms: Optional[int] = None
    test_count: Optional[int] = None
    uncovered_file_count: int = 0


class CoverageTrendAnalysis(BaseModel):
    """Computed trend metrics over a time window."""

    metric_type: str  # "statement", "branch", "line"
    granularity: str  # "repository", "module", "file"
    scope_id: str  # "" (repo), "src/observer" (module), "file.py" (file)

    # Time window
    window_start: datetime
    window_end: datetime

    # Historical values
    measurements: list[tuple[datetime, float]] = Field(default_factory=list)  # Sorted by date

    # Computed metrics
    current_value: float
    average_value: float
    min_value: float
    max_value: float

    # Trend analysis
    trend_direction: str  # "improving", "stable", "degrading"
    trend_pct: float  # % change per unit time
    regression_count: int = 0  # Number of drops > threshold

    # Stability
    standard_deviation: float = 0.0
    stability_score: float = 0.0  # 0-1, higher = more stable

    # Velocity and projection
    days_of_decline: int = 0
    projected_value_7days: Optional[float] = None


class CoverageAlert(BaseModel):
    """A generated coverage alert."""

    alert_id: str
    timestamp: datetime
    alert_type: str  # "below_threshold", "regression_detected", "trend_degrading", "module_gap"
    severity: str  # "critical", "high", "medium", "low"

    # What triggered the alert
    metric_type: str  # "statement", "branch", "line"
    granularity: str  # "repository", "module", "file"
    scope_id: str  # module path or file path

    # Measurements
    current_value: float
    threshold_or_baseline: Optional[float] = None
    delta_pct: float

    # Context
    baseline_type: str  # "minimum_threshold", "previous_run", "7day_avg", "30day_avg"

    # Remediation
    affected_modules: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    recommendation: Optional[str] = None

    # Status tracking
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    dismissal_reason: Optional[str] = None
