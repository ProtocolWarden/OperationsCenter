# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Dry-run alert validation for testing alert conditions without triggering notifications.

Defines:
- AlertDryRunResult — result of dry-run evaluation
- AlertValidator — validates alert configuration
- evaluate_alerts_dry_run() — test all alerts without notification
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from operations_center.observer.alert_config import (
    ALERT_ROUTES,
    COLLECTOR_THRESHOLDS,
    AlertRoute,
    get_collector_thresholds,
)
from operations_center.observer.security_logging import (
    ALERT_CONDITIONS,
    AlertCondition,
    MalformedPayloadMetrics,
    should_trigger_alert,
)

logger = logging.getLogger(__name__)


@dataclass
class AlertDryRunResult:
    """Result of evaluating a single alert condition in dry-run mode."""

    condition_name: str
    would_trigger: bool
    error_count: int
    threshold: int
    time_window_minutes: int
    matching_errors: list[dict[str, Any]]
    routes: list[str]
    channels: list[str]
    severity: str
    evaluation_time: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "condition_name": self.condition_name,
            "would_trigger": self.would_trigger,
            "error_count": self.error_count,
            "threshold": self.threshold,
            "time_window_minutes": self.time_window_minutes,
            "matching_errors": self.matching_errors,
            "routes": self.routes,
            "channels": self.channels,
            "severity": self.severity,
            "evaluation_time": self.evaluation_time.isoformat(),
        }


@dataclass
class AlertValidationReport:
    """Comprehensive validation report from dry-run evaluation."""

    evaluation_time: datetime
    total_conditions: int
    triggered_count: int
    conditions: list[AlertDryRunResult]
    collector_thresholds_checked: list[str]
    configuration_issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "evaluation_time": self.evaluation_time.isoformat(),
            "total_conditions": self.total_conditions,
            "triggered_count": self.triggered_count,
            "conditions": [c.to_dict() for c in self.conditions],
            "collector_thresholds_checked": self.collector_thresholds_checked,
            "configuration_issues": self.configuration_issues,
        }


class AlertValidator:
    """Validates alert configuration and performs dry-run evaluations."""

    def __init__(
        self,
        alert_conditions: dict[str, AlertCondition] | None = None,
        alert_routes: dict[str, AlertRoute] | None = None,
    ) -> None:
        self.alert_conditions = alert_conditions or ALERT_CONDITIONS
        self.alert_routes = alert_routes or ALERT_ROUTES

    def validate_configuration(self) -> tuple[bool, list[str]]:
        """Validate the entire alert configuration.

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []

        # Check that all routes reference valid conditions
        for route_name, route in self.alert_routes.items():
            if route.condition_name not in self.alert_conditions:
                issues.append(
                    f"Route '{route_name}' references unknown condition '{route.condition_name}'"
                )

        # Check that all conditions have routes
        for condition_name in self.alert_conditions:
            if condition_name not in self.alert_routes:
                logger.warning("Alert condition '%s' has no route configured", condition_name)

        # Validate per-collector thresholds
        for collector_name, thresholds in COLLECTOR_THRESHOLDS.items():
            if thresholds.high_water_mark >= thresholds.error_threshold:
                issues.append(
                    f"Collector '{collector_name}' has "
                    f"high_water_mark >= error_threshold "
                    f"({thresholds.high_water_mark} >= {thresholds.error_threshold})"
                )

        return len(issues) == 0, issues

    def evaluate_condition_dry_run(
        self,
        condition_name: str,
        metrics: MalformedPayloadMetrics,
        lookback_minutes: int = 5,
    ) -> AlertDryRunResult | None:
        """Evaluate a single alert condition in dry-run mode.

        Args:
            condition_name: Name of the condition to evaluate
            metrics: Current metrics
            lookback_minutes: How far back to look

        Returns:
            AlertDryRunResult if condition found, None otherwise
        """
        condition = self.alert_conditions.get(condition_name)
        if not condition:
            logger.warning("Unknown alert condition: %s", condition_name)
            return None

        # Check if alert would trigger
        would_trigger = should_trigger_alert(metrics, condition, lookback_minutes)

        # Get matching errors
        cutoff_time = datetime.now(tz=UTC) - timedelta(minutes=lookback_minutes)
        matching_errors = [
            e.to_dict()
            for e in metrics.recent_errors
            if e.normalized_timestamp() >= cutoff_time and e.error_type == condition.category.value
        ]

        # Get routes for this condition
        route = self.alert_routes.get(condition_name)
        routes = [condition_name] if route else []
        channels = route.channels if route else []

        return AlertDryRunResult(
            condition_name=condition_name,
            would_trigger=would_trigger,
            error_count=len(matching_errors),
            threshold=condition.trigger_threshold,
            time_window_minutes=condition.time_window_minutes,
            matching_errors=matching_errors,
            routes=routes,
            channels=channels,
            severity=condition.severity.value,
            evaluation_time=datetime.now(tz=UTC),
        )

    def evaluate_all_conditions_dry_run(
        self,
        metrics: MalformedPayloadMetrics,
        lookback_minutes: int = 5,
    ) -> AlertValidationReport:
        """Evaluate all alert conditions in dry-run mode.

        Args:
            metrics: Current metrics
            lookback_minutes: How far back to look

        Returns:
            AlertValidationReport with results
        """
        # Validate configuration first
        config_valid, config_issues = self.validate_configuration()

        # Evaluate all conditions
        results = []
        triggered_count = 0

        for condition_name in sorted(self.alert_conditions.keys()):
            result = self.evaluate_condition_dry_run(
                condition_name,
                metrics,
                lookback_minutes,
            )
            if result:
                results.append(result)
                if result.would_trigger:
                    triggered_count += 1

        return AlertValidationReport(
            evaluation_time=datetime.now(tz=UTC),
            total_conditions=len(self.alert_conditions),
            triggered_count=triggered_count,
            conditions=results,
            collector_thresholds_checked=sorted(COLLECTOR_THRESHOLDS.keys()),
            configuration_issues=config_issues,
        )

    def evaluate_per_collector_thresholds(
        self,
        metrics: MalformedPayloadMetrics,
    ) -> dict[str, Any]:
        """Evaluate per-collector error rates against thresholds.

        Args:
            metrics: Current metrics

        Returns:
            Dictionary of collector evaluation results
        """
        results = {}

        for collector_name, collector_errors in metrics.errors_by_collector.items():
            thresholds = get_collector_thresholds(collector_name)
            if not thresholds:
                results[collector_name] = {
                    "status": "unknown",
                    "error_count": collector_errors,
                    "reason": "No thresholds configured",
                }
                continue

            status = "ok"
            if collector_errors >= thresholds.error_threshold:
                status = "critical"
            elif collector_errors >= thresholds.high_water_mark:
                status = "warning"

            results[collector_name] = {
                "status": status,
                "error_count": collector_errors,
                "high_water_mark": thresholds.high_water_mark,
                "error_threshold": thresholds.error_threshold,
                "recovery_action": thresholds.recovery_action,
            }

        return results

    def format_report_text(self, report: AlertValidationReport) -> str:
        """Format validation report as human-readable text.

        Args:
            report: AlertValidationReport to format

        Returns:
            Formatted text report
        """
        lines = [
            "=" * 70,
            "ALERT DRY-RUN VALIDATION REPORT",
            "=" * 70,
            f"Evaluation Time: {report.evaluation_time.isoformat()}",
            f"Total Conditions: {report.total_conditions}",
            f"Would Trigger: {report.triggered_count}",
            "",
            "CONDITION STATUS:",
            "-" * 70,
        ]

        # Format condition results
        for result in sorted(report.conditions, key=lambda r: r.condition_name):
            trigger_status = "✓ TRIGGER" if result.would_trigger else "✗ OK"
            lines.append(
                f"{trigger_status} | {result.condition_name:40s} "
                f"({result.error_count}/{result.threshold}) "
                f"[{result.severity}]"
            )
            if result.channels:
                lines.append(f"       Channels: {', '.join(result.channels)}")
            if result.matching_errors and result.would_trigger:
                lines.append(f"       Sample errors ({len(result.matching_errors)} total):")
                for error in result.matching_errors[:2]:
                    msg = error.get("error_msg", "N/A")
                    lines.append(f"         - {msg[:60]}")

        # Format configuration issues
        if report.configuration_issues:
            lines.extend(
                [
                    "",
                    "CONFIGURATION ISSUES:",
                    "-" * 70,
                ]
            )
            for issue in report.configuration_issues:
                lines.append(f"✗ {issue}")
        else:
            lines.append("\n✓ Configuration valid")

        lines.append("=" * 70)
        return "\n".join(lines)

    def save_report_json(
        self,
        report: AlertValidationReport,
        output_path: Path,
    ) -> None:
        """Save validation report as JSON.

        Args:
            report: AlertValidationReport to save
            output_path: Path to write JSON file
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        logger.info("Saved validation report to %s", output_path)


def evaluate_alerts_dry_run(
    metrics: MalformedPayloadMetrics,
    lookback_minutes: int = 5,
) -> AlertValidationReport:
    """Evaluate all alert conditions in dry-run mode.

    This is the primary entry point for dry-run validation.

    Args:
        metrics: Current malformed payload metrics
        lookback_minutes: How far back to look in recent_errors

    Returns:
        AlertValidationReport with evaluation results
    """
    validator = AlertValidator()
    return validator.evaluate_all_conditions_dry_run(
        metrics,
        lookback_minutes=lookback_minutes,
    )
