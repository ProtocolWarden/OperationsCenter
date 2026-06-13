# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage threshold alerting system for detecting coverage regressions and degradation.

Implements alert generation, severity classification, and categorization logic for
coverage metrics at repository, module, and file granularities.
"""

from __future__ import annotations

from enum import Enum
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
        coverage_pct = snapshot.overall_statement_coverage_pct
        threshold = self.config.repo_minimum_threshold

        if coverage_pct < threshold:
            severity = self.config.classify_severity(coverage_pct)
            alert = CoverageAlert(
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
                recommendation=f"Coverage {coverage_pct:.1f}% is below minimum threshold of {threshold:.1f}%. "
                f"Add tests to increase coverage.",
            )
            self.alerts.append(alert)

        # Also check branch coverage
        branch_coverage = snapshot.overall_branch_coverage_pct
        branch_threshold = self.config.branch_coverage_minimum
        if branch_coverage < branch_threshold:
            severity = self.config.classify_severity(branch_coverage)
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
                recommendation=f"Branch coverage {branch_coverage:.1f}% is below minimum threshold of {branch_threshold:.1f}%. "
                f"Add condition tests.",
            )
            self.alerts.append(alert)

        # Also check line coverage
        line_coverage = snapshot.overall_line_coverage_pct
        line_threshold = self.config.line_coverage_minimum
        if line_coverage < line_threshold:
            severity = self.config.classify_severity(line_coverage)
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
                recommendation=f"Line coverage {line_coverage:.1f}% is below minimum threshold of {line_threshold:.1f}%. "
                f"Add tests for uncovered lines.",
            )
            self.alerts.append(alert)

    def _check_module_critical_gaps(self, snapshot: CoverageSnapshot) -> None:
        """Check for modules with critical coverage gaps.

        Args:
            snapshot: Coverage snapshot to analyze
        """
        for module in snapshot.module_coverages:
            threshold = self.config.get_module_threshold(module.module_path, "statement")
            coverage_pct = module.statement_coverage_pct

            if coverage_pct < threshold:
                gap = threshold - coverage_pct
                if gap >= 15.0:
                    severity = self.config.classify_severity(coverage_pct)
                    alert = CoverageAlert(
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
                        recommendation=f"Module {module.module_path} has critical coverage gap of {gap:.1f}%. "
                        f"Current coverage {coverage_pct:.1f}% vs target {threshold:.1f}%. "
                        f"Prioritize tests for this module.",
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
        current = snapshot.overall_statement_coverage_pct
        previous = previous_snapshot.overall_statement_coverage_pct
        delta = current - previous

        if delta <= -self.config.regression_threshold_pct:
            severity = self.config.classify_severity(current)
            alert = CoverageAlert(
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
                recommendation=f"Coverage regressed from {previous:.1f}% to {current:.1f}% "
                f"({delta:.1f}%). Investigate recent changes that may have reduced coverage.",
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
                current = snapshot.overall_statement_coverage_pct
                severity = self.config.classify_severity(current)
                velocity_pct = trend_analysis.trend_pct if trend_analysis.trend_pct else 0
                alert = CoverageAlert(
                    alert_id=str(uuid4()),
                    timestamp=snapshot.timestamp,
                    alert_type=AlertType.TREND_DEGRADING.value,
                    severity=severity.value,
                    metric_type="statement",
                    granularity="repository",
                    scope_id="",
                    current_value=current,
                    threshold_or_baseline=trend_analysis.average_value,
                    delta_pct=-velocity_pct if velocity_pct > 0 else 0,
                    baseline_type="trend",
                    recommendation=f"Coverage is in sustained decline ({trend_analysis.days_of_decline} days). "
                    f"Current {current:.1f}% vs {trend_analysis.days_of_decline}-day average {trend_analysis.average_value:.1f}%. "
                    f"Trending down at {velocity_pct:.2f}% per day. "
                    f"Projected value in 7 days: {trend_analysis.projected_value_7days or 'N/A'}%. "
                    f"Review recent test changes and coverage improvements.",
                )
                self.alerts.append(alert)

    def categorize_alert(self, alert: CoverageAlert) -> dict[str, str]:
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
        if alert_type == AlertType.BELOW_THRESHOLD.value:
            return "Threshold Breach"
        elif alert_type == AlertType.REGRESSION_DETECTED.value:
            return "Regression"
        elif alert_type == AlertType.TREND_DEGRADING.value:
            return "Trend Decline"
        elif alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value:
            return "Module Critical"
        return "Unknown"

    def _is_action_required(self, severity: str) -> bool:
        """Determine if alert requires immediate action.

        Args:
            severity: AlertSeverity value

        Returns:
            True if action is required
        """
        return severity in [AlertSeverity.CRITICAL.value, AlertSeverity.EMERGENCY.value]

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

    def summarize_alerts(self) -> dict[str, int]:
        """Summarize alerts by type and severity.

        Returns:
            Dictionary with alert counts
        """
        summary = {
            "total": len(self.alerts),
            "by_type": {},
            "by_severity": {},
        }

        for alert_type in AlertType:
            count = len(self.filter_alerts_by_type(alert_type))
            if count > 0:
                summary["by_type"][alert_type.value] = count

        for severity in AlertSeverity:
            count = len(self.filter_alerts_by_severity(severity))
            if count > 0:
                summary["by_severity"][severity.value] = count

        return summary
