# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage threshold alerting system for detecting coverage regressions and degradation.

Implements alert generation, severity classification, and categorization logic for
coverage metrics at repository, module, and file granularities.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
)


class AlertType(str, Enum):
    """Coverage alert type enumeration."""

    BELOW_THRESHOLD = "below_threshold"
    REGRESSION_DETECTED = "regression_detected"
    TREND_DEGRADING = "trend_degrading"
    CRITICAL_MODULE_COVERAGE = "critical_module_coverage"


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


def calculate_coverage_gap(current: float, target: float) -> float:
    """Calculate the gap between current and target coverage.

    Args:
        current: Current coverage percentage
        target: Target coverage percentage

    Returns:
        Gap percentage (can be negative if exceeds target)
    """
    gap: float = target - current
    return gap


def is_coverage_critical(coverage: float) -> bool:
    """Determine if coverage percentage indicates critical status.

    Args:
        coverage: Coverage percentage

    Returns:
        True if coverage is critically low
    """
    is_critical: bool = coverage < 50.0
    return is_critical


def format_coverage_value(coverage: float, precision: int = 1) -> str:
    """Format coverage value with specified decimal precision.

    Args:
        coverage: Coverage percentage value
        precision: Number of decimal places

    Returns:
        Formatted coverage string
    """
    formatted: str = f"{coverage:.{precision}f}%"
    return formatted


def get_alert_priority(alert_type: str, severity: str) -> int:
    """Calculate priority score for an alert (higher = more urgent).

    Args:
        alert_type: Type of alert
        severity: Severity level

    Returns:
        Priority score (0-10)
    """
    base_priority: int = 0

    severity_weights: dict[str, int] = {
        AlertSeverity.EMERGENCY.value: 10,
        AlertSeverity.CRITICAL.value: 7,
        AlertSeverity.WARNING.value: 4,
        AlertSeverity.INFO.value: 1,
    }
    base_priority = severity_weights.get(severity, 0)

    type_weights: dict[str, int] = {
        AlertType.CRITICAL_MODULE_COVERAGE.value: 3,
        AlertType.REGRESSION_DETECTED.value: 2,
        AlertType.TREND_DEGRADING.value: 1,
        AlertType.BELOW_THRESHOLD.value: 0,
    }
    type_bonus: int = type_weights.get(alert_type, 0)

    priority: int = min(10, base_priority + type_bonus)
    return priority


def calculate_coverage_trend_direction(previous: float, current: float) -> Literal["improving", "stable", "degrading"]:
    """Determine coverage trend direction based on previous and current values.

    Args:
        previous: Previous coverage value
        current: Current coverage value

    Returns:
        Trend direction string
    """
    delta: float = current - previous
    if delta > 0.5:
        direction: Literal["improving", "stable", "degrading"] = "improving"
    elif delta < -0.5:
        direction = "degrading"
    else:
        direction = "stable"
    return direction


class CoverageAlertConfig(BaseModel):
    """Configuration for coverage alerting with repository and module-level thresholds."""

    # Repository-level thresholds (defaults)
    repo_minimum_threshold: float = 80.0
    repo_warning_threshold: float = 85.0
    repo_target_threshold: float = 90.0

    # Coverage type specific thresholds
    statement_coverage_minimum: float = 75.0
    branch_coverage_minimum: float = 65.0
    line_coverage_minimum: float = 75.0

    # Regression thresholds
    regression_threshold_pct: float = 2.0
    regression_7day_threshold_pct: float = 3.0
    regression_30day_threshold_pct: float = 5.0

    # Trend detection
    trend_degradation_days: int = 5
    trend_degradation_velocity_pct: float = 1.0

    # Module-level thresholds (per-module overrides)
    module_thresholds: dict[str, dict[str, float]] = Field(default_factory=dict)

    # Severity mapping thresholds
    severity_critical_threshold: float = 50.0
    severity_high_threshold: float = 70.0
    severity_medium_threshold: float = 80.0

    def get_module_threshold(self, module_path: str, metric_type: str = "statement") -> float:
        """Get threshold for a specific module, falling back to repository default.

        Args:
            module_path: Module path (e.g., "src/operations_center/observer")
            metric_type: Metric type ("statement", "branch", or "line")

        Returns:
            Threshold percentage for the module
        """
        if module_path in self.module_thresholds:
            return self.module_thresholds[module_path].get(
                f"{metric_type}_coverage_minimum", self.repo_minimum_threshold
            )
        return self.repo_minimum_threshold

    def classify_severity(self, coverage_pct: float) -> AlertSeverity:
        """Classify alert severity based on coverage percentage.

        Args:
            coverage_pct: Coverage percentage

        Returns:
            AlertSeverity enum value
        """
        if coverage_pct < self.severity_critical_threshold:
            return AlertSeverity.EMERGENCY
        elif coverage_pct < self.severity_high_threshold:
            return AlertSeverity.CRITICAL
        elif coverage_pct < self.severity_medium_threshold:
            return AlertSeverity.WARNING
        return AlertSeverity.INFO


class CoverageAlertManager:
    """Generates and manages coverage alerts for threshold breaches and regressions."""

    def __init__(self, config: CoverageAlertConfig | None = None):
        """Initialize alert manager with optional configuration.

        Args:
            config: CoverageAlertConfig instance, defaults to new instance with defaults
        """
        self.config = config or CoverageAlertConfig()
        self.alerts: list[CoverageAlert] = []

    def generate_alerts(
        self,
        snapshot: CoverageSnapshot,
        previous_snapshot: CoverageSnapshot | None = None,
        trend_analysis: CoverageTrendAnalysis | None = None,
    ) -> list[CoverageAlert]:
        """Generate all applicable alerts for a coverage snapshot.

        Args:
            snapshot: Current coverage snapshot
            previous_snapshot: Previous snapshot for regression detection
            trend_analysis: Trend analysis results for trend detection

        Returns:
            List of generated CoverageAlert instances
        """
        self.alerts = []

        # Check repository-level thresholds
        self._check_repository_below_threshold(snapshot)

        # Check module-level thresholds
        self._check_module_critical_gaps(snapshot)

        # Check for regressions if previous snapshot available
        if previous_snapshot:
            self._check_regressions(snapshot, previous_snapshot)

        # Check for trend degradation if analysis available
        if trend_analysis:
            self._check_trend_degradation(snapshot, trend_analysis)

        return self.alerts

    def _check_repository_below_threshold(self, snapshot: CoverageSnapshot) -> None:
        """Check if repository coverage is below threshold.

        Args:
            snapshot: Coverage snapshot to analyze
        """
        coverage_pct: float = snapshot.overall_statement_coverage_pct
        threshold: float = self.config.repo_minimum_threshold

        if coverage_pct < threshold:
            severity: AlertSeverity = self.config.classify_severity(coverage_pct)
            recommendation: str = (
                f"Coverage {coverage_pct:.1f}% is below minimum threshold of {threshold:.1f}%. "
                "Add tests to increase coverage."
            )
            alert: CoverageAlert = CoverageAlert(
                alert_id=str(uuid4()),
                timestamp=snapshot.timestamp,
                alert_type=AlertType.BELOW_THRESHOLD.value,
                severity=severity.value,
                metric_type="statement",
                granularity="repository",
                scope_id="",
                current_value=coverage_pct,
                threshold_or_baseline=threshold,
                delta_pct=threshold - coverage_pct,
                baseline_type="minimum_threshold",
                recommendation=recommendation,
            )
            self.alerts.append(alert)

        branch_coverage: float = snapshot.overall_branch_coverage_pct
        branch_threshold: float = self.config.branch_coverage_minimum
        if branch_coverage < branch_threshold:
            severity = self.config.classify_severity(branch_coverage)
            recommendation = (
                f"Branch coverage {branch_coverage:.1f}% is below minimum threshold of "
                f"{branch_threshold:.1f}%. Add condition tests."
            )
            alert = CoverageAlert(
                alert_id=str(uuid4()),
                timestamp=snapshot.timestamp,
                alert_type=AlertType.BELOW_THRESHOLD.value,
                severity=severity.value,
                metric_type="branch",
                granularity="repository",
                scope_id="",
                current_value=branch_coverage,
                threshold_or_baseline=branch_threshold,
                delta_pct=branch_threshold - branch_coverage,
                baseline_type="minimum_threshold",
                recommendation=recommendation,
            )
            self.alerts.append(alert)

        line_coverage: float = snapshot.overall_line_coverage_pct
        line_threshold: float = self.config.line_coverage_minimum
        if line_coverage < line_threshold:
            severity = self.config.classify_severity(line_coverage)
            recommendation = (
                f"Line coverage {line_coverage:.1f}% is below minimum threshold of "
                f"{line_threshold:.1f}%. Add tests for uncovered lines."
            )
            alert = CoverageAlert(
                alert_id=str(uuid4()),
                timestamp=snapshot.timestamp,
                alert_type=AlertType.BELOW_THRESHOLD.value,
                severity=severity.value,
                metric_type="line",
                granularity="repository",
                scope_id="",
                current_value=line_coverage,
                threshold_or_baseline=line_threshold,
                delta_pct=line_threshold - line_coverage,
                baseline_type="minimum_threshold",
                recommendation=recommendation,
            )
            self.alerts.append(alert)

    def _check_module_critical_gaps(self, snapshot: CoverageSnapshot) -> None:
        """Check for modules with critical coverage gaps.

        Args:
            snapshot: Coverage snapshot to analyze
        """
        for module in snapshot.module_coverages:
            threshold: float = self.config.get_module_threshold(module.module_path, "statement")
            coverage_pct: float = module.statement_coverage_pct

            if coverage_pct < threshold:
                gap: float = threshold - coverage_pct
                if gap >= 15.0:
                    severity: AlertSeverity = self.config.classify_severity(coverage_pct)
                    recommendation: str = (
                        f"Module {module.module_path} has critical coverage gap of {gap:.1f}%. "
                        f"Current coverage {coverage_pct:.1f}% vs target {threshold:.1f}%. "
                        "Prioritize tests for this module."
                    )
                    alert: CoverageAlert = CoverageAlert(
                        alert_id=str(uuid4()),
                        timestamp=snapshot.timestamp,
                        alert_type=AlertType.CRITICAL_MODULE_COVERAGE.value,
                        severity=severity.value,
                        metric_type="statement",
                        granularity="module",
                        scope_id=module.module_path,
                        current_value=coverage_pct,
                        threshold_or_baseline=threshold,
                        delta_pct=-gap,
                        baseline_type="minimum_threshold",
                        affected_modules=[module.module_path],
                        recommendation=recommendation,
                    )
                    self.alerts.append(alert)

    def _check_regressions(
        self, snapshot: CoverageSnapshot, previous_snapshot: CoverageSnapshot
    ) -> None:
        """Check for coverage regressions comparing to previous snapshot.

        Args:
            snapshot: Current coverage snapshot
            previous_snapshot: Previous coverage snapshot
        """
        current: float = snapshot.overall_statement_coverage_pct
        previous: float = previous_snapshot.overall_statement_coverage_pct
        delta: float = current - previous

        if delta <= -self.config.regression_threshold_pct:
            severity: AlertSeverity = self.config.classify_severity(current)
            recommendation: str = (
                f"Coverage regressed from {previous:.1f}% to {current:.1f}% "
                f"({delta:.1f}%). Investigate recent changes that may have reduced coverage."
            )
            alert: CoverageAlert = CoverageAlert(
                alert_id=str(uuid4()),
                timestamp=snapshot.timestamp,
                alert_type=AlertType.REGRESSION_DETECTED.value,
                severity=severity.value,
                metric_type="statement",
                granularity="repository",
                scope_id="",
                current_value=current,
                threshold_or_baseline=previous,
                delta_pct=abs(delta),
                baseline_type="previous_run",
                recommendation=recommendation,
            )
            self.alerts.append(alert)

    def _check_trend_degradation(
        self, snapshot: CoverageSnapshot, trend_analysis: CoverageTrendAnalysis
    ) -> None:
        """Check for sustained coverage degradation trends.

        Args:
            snapshot: Current coverage snapshot
            trend_analysis: Trend analysis results
        """
        if trend_analysis.trend_direction == "degrading":
            if trend_analysis.days_of_decline >= self.config.trend_degradation_days:
                current: float = snapshot.overall_statement_coverage_pct
                severity: AlertSeverity = self.config.classify_severity(current)
                velocity_pct: float = trend_analysis.trend_pct if trend_analysis.trend_pct else 0.0
                days_decline: int = trend_analysis.days_of_decline
                avg_val: float = trend_analysis.average_value
                proj_val: float | str = trend_analysis.projected_value_7days or "N/A"
                recommendation: str = (
                    f"Coverage is in sustained decline ({days_decline} days). "
                    f"Current {current:.1f}% vs {days_decline}-day average {avg_val:.1f}%. "
                    f"Trending down at {velocity_pct:.2f}% per day. "
                    f"Projected value in 7 days: {proj_val}%. "
                    "Review recent test changes and coverage improvements."
                )
                alert: CoverageAlert = CoverageAlert(
                    alert_id=str(uuid4()),
                    timestamp=snapshot.timestamp,
                    alert_type=AlertType.TREND_DEGRADING.value,
                    severity=severity.value,
                    metric_type="statement",
                    granularity="repository",
                    scope_id="",
                    current_value=current,
                    threshold_or_baseline=trend_analysis.average_value,
                    delta_pct=-velocity_pct if velocity_pct > 0 else 0.0,
                    baseline_type="trend",
                    recommendation=recommendation,
                )
                self.alerts.append(alert)

    def categorize_alert(self, alert: CoverageAlert) -> dict[str, Any]:
        """Categorize an alert by type and severity.

        Args:
            alert: Alert to categorize

        Returns:
            Dictionary with categorization metadata
        """
        return {
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "category": self._get_category(alert.alert_type),
            "action_required": self._is_action_required(alert.severity),
        }

    def _get_category(self, alert_type: str) -> str:
        """Get human-readable category for alert type.

        Args:
            alert_type: AlertType value

        Returns:
            Category description
        """
        category_map: dict[str, str] = {
            AlertType.BELOW_THRESHOLD.value: "Threshold Breach",
            AlertType.REGRESSION_DETECTED.value: "Regression",
            AlertType.TREND_DEGRADING.value: "Trend Decline",
            AlertType.CRITICAL_MODULE_COVERAGE.value: "Module Critical",
        }
        return category_map.get(alert_type, "Unknown")

    def _is_action_required(self, severity: str) -> bool:
        """Determine if alert requires immediate action.

        Args:
            severity: AlertSeverity value

        Returns:
            True if action is required
        """
        action_required_severities: set[str] = {AlertSeverity.CRITICAL.value, AlertSeverity.EMERGENCY.value}
        return severity in action_required_severities

    def filter_alerts_by_severity(self, severity: AlertSeverity) -> list[CoverageAlert]:
        """Filter alerts by severity level.

        Args:
            severity: Severity level to filter by

        Returns:
            List of alerts matching severity
        """
        return [alert for alert in self.alerts if alert.severity == severity.value]

    def filter_alerts_by_type(self, alert_type: AlertType) -> list[CoverageAlert]:
        """Filter alerts by type.

        Args:
            alert_type: Alert type to filter by

        Returns:
            List of alerts matching type
        """
        return [alert for alert in self.alerts if alert.alert_type == alert_type.value]

    def summarize_alerts(self) -> dict[str, Any]:
        """Summarize alerts by type and severity.

        Returns:
            Dictionary with alert counts
        """
        summary: dict[str, Any] = {
            "total": len(self.alerts),
            "by_type": {},
            "by_severity": {},
        }

        for alert_type in AlertType:
            count: int = len(self.filter_alerts_by_type(alert_type))
            if count > 0:
                summary["by_type"][alert_type.value] = count

        for severity in AlertSeverity:
            count = len(self.filter_alerts_by_severity(severity))
            if count > 0:
                summary["by_severity"][severity.value] = count

        return summary

    def get_action_required_alerts(self) -> list[CoverageAlert]:
        """Get all alerts requiring immediate action.

        Returns:
            List of alerts with critical or emergency severity
        """
        action_alerts: list[CoverageAlert] = [
            alert for alert in self.alerts if self._is_action_required(alert.severity)
        ]
        return action_alerts

    def get_alerts_by_module(self, module_path: str) -> list[CoverageAlert]:
        """Get alerts affecting a specific module.

        Args:
            module_path: Module path to filter by

        Returns:
            List of alerts affecting this module
        """
        module_alerts: list[CoverageAlert] = [
            alert for alert in self.alerts if module_path in alert.affected_modules
        ]
        return module_alerts

    def clear_alerts(self) -> int:
        """Clear all stored alerts.

        Returns:
            Number of alerts cleared
        """
        count: int = len(self.alerts)
        self.alerts = []
        return count

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str, reason: str | None = None) -> bool:
        """Mark an alert as acknowledged.

        Args:
            alert_id: ID of alert to acknowledge
            acknowledged_by: User/system acknowledging the alert
            reason: Optional acknowledgment reason

        Returns:
            True if alert was found and updated
        """
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                alert.acknowledged_by = acknowledged_by
                alert.acknowledged_at = datetime.now(timezone.utc)
                return True
        return False

    def dismiss_alert(self, alert_id: str, reason: str) -> bool:
        """Mark an alert as dismissed.

        Args:
            alert_id: ID of alert to dismiss
            reason: Reason for dismissal

        Returns:
            True if alert was found and updated
        """
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                alert.dismissal_reason = reason
                return True
        return False
