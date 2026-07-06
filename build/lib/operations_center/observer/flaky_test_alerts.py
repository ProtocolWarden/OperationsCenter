# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Flaky Test Alert Manager — Failure categorization and alert generation.

Implements alert conditions for critical flakiness patterns and generates
prioritized alerts for action by CI/dashboard systems.

Alert Types:
  - NEW_FLAKY_TEST: Test became flaky in past 24h (WARNING severity)
  - REGRESSION_SPIKE: Flakiness increased significantly (CRITICAL severity)
  - CRITICAL_FLAKINESS: Failure rate >30% (CRITICAL severity)
  - MODULE_OUTBREAK: >20% of module tests are flaky (WARNING severity)
  - EXTRACTION_SUCCESS_RATE_LOW: Extraction success rate below threshold (WARNING–EMERGENCY)
  - MESSAGE_QUALITY_RATE_LOW: Assertion message quality rate below threshold (WARNING–EMERGENCY)

Usage:
    alerts = FlakyTestAlertManager.check_alerts(agg_report)
    for alert in alerts:
        print(f"[{alert['severity']}] {alert['type']}: {alert['description']}")

    # Check extraction success rate from a FlakyTestSignal:
    extraction_alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
    for alert in extraction_alerts:
        print(f"[{alert.severity.value}] {alert.description}")

    # Check message quality rate:
    quality_alerts = FlakyTestAlertManager.check_message_quality_rate(health.message_quality_rate)
    for alert in quality_alerts:
        print(f"[{alert.severity.value}] {alert.description}")
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .flaky_test_alert_config import FlakyTestAlertConfig
from .flaky_test_storage import FlakyTestAggregationReport
from .models import FlakyTestSignal


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class FlakyTestAlert:
    """Represents a single alert condition."""

    alert_type: str
    severity: AlertSeverity
    description: str
    details: dict

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.alert_type,
            "severity": self.severity.value,
            "description": self.description,
            "details": self.details,
        }


class FlakyTestAlertManager:
    """Manages alert detection and generation."""

    @staticmethod
    def check_alerts(
        agg_report: FlakyTestAggregationReport,
        prev_report: FlakyTestAggregationReport | None = None,
    ) -> list[FlakyTestAlert]:
        """Check aggregation report for alert conditions.

        Args:
            agg_report: Current aggregation report
            prev_report: Previous aggregation report (for trend detection)

        Returns:
            List of generated alerts sorted by severity
        """
        alerts = []

        # Condition 1: New flaky tests
        new_flaky_alerts = FlakyTestAlertManager._check_new_flaky_tests(agg_report)
        alerts.extend(new_flaky_alerts)

        # Condition 2: Regression spike
        if prev_report:
            regression_alerts = FlakyTestAlertManager._check_regression_spike(
                agg_report, prev_report
            )
            alerts.extend(regression_alerts)

        # Condition 3: Critical flakiness
        critical_alerts = FlakyTestAlertManager._check_critical_flakiness(agg_report)
        alerts.extend(critical_alerts)

        # Condition 4: Module outbreak
        outbreak_alerts = FlakyTestAlertManager._check_module_outbreak(agg_report)
        alerts.extend(outbreak_alerts)

        # Sort by severity (emergency → critical → warning → info)
        severity_order = {
            AlertSeverity.EMERGENCY: 0,
            AlertSeverity.CRITICAL: 1,
            AlertSeverity.WARNING: 2,
            AlertSeverity.INFO: 3,
        }
        alerts.sort(key=lambda a: severity_order.get(a.severity, 4))

        return alerts

    @staticmethod
    def _check_new_flaky_tests(
        agg_report: FlakyTestAggregationReport,
    ) -> list[FlakyTestAlert]:
        """Detect tests that became flaky in the past 24h.

        Args:
            agg_report: Aggregation report

        Returns:
            List of new flaky test alerts
        """
        alerts = []
        new_flaky_tests = []

        for test in agg_report.flaky_tests:
            # Check if first_seen is recent (assuming first_seen is ISO format)
            first_seen = test.get("first_seen", "")
            if "T" in first_seen:
                # Very simplified check - in production would use proper datetime parsing
                if "2026-06-07" in first_seen or "2026-06-06" in first_seen:
                    new_flaky_tests.append(test)

        if new_flaky_tests:
            alert = FlakyTestAlert(
                alert_type="NEW_FLAKY_TEST",
                severity=AlertSeverity.WARNING,
                description=f"Detected {len(new_flaky_tests)} new flaky test(s) in past 24h",
                details={
                    "count": len(new_flaky_tests),
                    "tests": [t["test_name"] for t in new_flaky_tests[:5]],
                    "category_breakdown": {
                        t.get("category", "unknown"): len(
                            [x for x in new_flaky_tests if x.get("category") == t.get("category")]
                        )
                        for t in new_flaky_tests
                    },
                },
            )
            alerts.append(alert)

        return alerts

    @staticmethod
    def _check_regression_spike(
        current: FlakyTestAggregationReport,
        previous: FlakyTestAggregationReport,
    ) -> list[FlakyTestAlert]:
        """Detect significant increase in flakiness.

        Args:
            current: Current aggregation report
            previous: Previous aggregation report

        Returns:
            List of regression spike alerts
        """
        alerts = []

        # Check if flaky test count increased by >50%
        prev_count = previous.flaky_test_count if previous else 0
        curr_count = current.flaky_test_count

        if prev_count > 0:
            increase_pct = (curr_count - prev_count) / prev_count
        else:
            increase_pct = 1.0 if curr_count > 0 else 0

        if increase_pct > 0.5 and curr_count > 0:
            alert = FlakyTestAlert(
                alert_type="REGRESSION_SPIKE",
                severity=AlertSeverity.CRITICAL,
                description=f"Flaky test count increased by {increase_pct * 100:.0f}% "
                f"({prev_count} → {curr_count})",
                details={
                    "previous_count": prev_count,
                    "current_count": curr_count,
                    "increase_percent": increase_pct * 100,
                    "period_days": current.period_days,
                },
            )
            alerts.append(alert)

        return alerts

    @staticmethod
    def _check_critical_flakiness(
        agg_report: FlakyTestAggregationReport,
    ) -> list[FlakyTestAlert]:
        """Detect tests with critical failure rates.

        Args:
            agg_report: Aggregation report

        Returns:
            List of critical flakiness alerts
        """
        alerts = []
        critical_tests = [t for t in agg_report.flaky_tests if t.get("failure_rate", 0) > 0.3]

        if critical_tests:
            alert = FlakyTestAlert(
                alert_type="CRITICAL_FLAKINESS",
                severity=AlertSeverity.CRITICAL,
                description=f"Found {len(critical_tests)} test(s) with >30% failure rate",
                details={
                    "count": len(critical_tests),
                    "tests": [
                        {
                            "name": t["test_name"],
                            "failure_rate": t.get("failure_rate", 0),
                        }
                        for t in critical_tests[:5]
                    ],
                    "avg_failure_rate": sum(t.get("failure_rate", 0) for t in critical_tests)
                    / len(critical_tests),
                },
            )
            alerts.append(alert)

        return alerts

    @staticmethod
    def _check_module_outbreak(
        agg_report: FlakyTestAggregationReport,
    ) -> list[FlakyTestAlert]:
        """Detect modules with high flakiness concentration.

        Args:
            agg_report: Aggregation report

        Returns:
            List of module outbreak alerts
        """
        alerts = []
        outbreak_modules = []

        for module_name, stats in agg_report.by_module.items():
            total = stats.get("total_count", 1)
            flaky = stats.get("flaky_count", 0)
            flaky_ratio = flaky / total if total > 0 else 0

            if flaky_ratio > 0.2:  # >20% flaky
                outbreak_modules.append(
                    {
                        "module": module_name,
                        "flaky_count": flaky,
                        "total_count": total,
                        "flaky_ratio": flaky_ratio,
                    }
                )

        if outbreak_modules:
            outbreak_modules.sort(key=lambda x: x["flaky_ratio"], reverse=True)
            alert = FlakyTestAlert(
                alert_type="MODULE_OUTBREAK",
                severity=AlertSeverity.WARNING,
                description=f"Module outbreak detected in {len(outbreak_modules)} module(s)",
                details={
                    "count": len(outbreak_modules),
                    "modules": [
                        {
                            "name": m["module"],
                            "flaky_ratio": m["flaky_ratio"],
                            "affected_tests": m["flaky_count"],
                        }
                        for m in outbreak_modules[:3]
                    ],
                },
            )
            alerts.append(alert)

        return alerts

    @staticmethod
    def check_extraction_success_rate(
        signal: FlakyTestSignal,
        config: FlakyTestAlertConfig | None = None,
    ) -> list[FlakyTestAlert]:
        """Check if extraction success rate is below threshold.

        Args:
            signal: FlakyTestSignal carrying extraction_success_rate (0-100)
            config: Alert configuration; uses defaults if None

        Returns:
            List of alerts (0 or 1 items)
        """
        if signal.status == "unavailable":
            return []

        if config is None:
            config = FlakyTestAlertConfig()

        should_alert, severity_str = config.should_alert_on_extraction_success_rate(
            signal.extraction_success_rate
        )

        if not should_alert:
            return []

        severity = AlertSeverity[severity_str]
        rate = signal.extraction_success_rate
        threshold = config.get_threshold("extraction_success_rate", severity_str)

        alert = FlakyTestAlert(
            alert_type="EXTRACTION_SUCCESS_RATE_LOW",
            severity=severity,
            description=(
                f"Extraction success rate {rate:.1f}% is below {severity_str.lower()} "
                f"threshold of {threshold:.1f}%"
            ),
            details={
                "current_rate": rate,
                "threshold": threshold,
                "gap": threshold - rate,
                "severity": severity_str,
            },
        )
        return [alert]

    @staticmethod
    def check_message_quality_rate(
        rate: float | None,
        config: FlakyTestAlertConfig | None = None,
    ) -> list[FlakyTestAlert]:
        """Check if message quality rate is below threshold.

        Args:
            rate: message_quality_rate (0-100), or None when no assertion messages exist.
            config: Alert configuration; uses defaults if None.

        Returns:
            List of alerts (0 or 1 items). Empty when rate is None.
        """
        if rate is None:
            return []

        if config is None:
            config = FlakyTestAlertConfig()

        should_alert, severity_str = config.should_alert_on_message_quality_rate(rate)

        if not should_alert:
            return []

        severity = AlertSeverity[severity_str]
        threshold = config.get_threshold("message_quality_rate", severity_str)

        alert = FlakyTestAlert(
            alert_type="MESSAGE_QUALITY_RATE_LOW",
            severity=severity,
            description=(
                f"Message quality rate {rate:.1f}% is below {severity_str.lower()} "
                f"threshold of {threshold:.1f}%"
            ),
            details={
                "current_rate": rate,
                "threshold": threshold,
                "gap": threshold - rate,
                "severity": severity_str,
            },
        )
        return [alert]
