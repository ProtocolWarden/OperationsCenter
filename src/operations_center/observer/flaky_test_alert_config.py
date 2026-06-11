# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Configuration for flaky test alerting system.

Defines:
- Alert type to channel mappings
- Severity level thresholds
- Default alert configurations
- Custom threshold overrides

Usage:
    config = FlakyTestAlertConfig()
    channels = config.get_channels_for_alert("NEW_FLAKY_TEST", severity="MEDIUM")
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any


@dataclass
class AlertThreshold:
    """Threshold configuration for an alert type."""

    alert_type: str
    low_threshold: float
    medium_threshold: float
    high_threshold: float
    critical_threshold: float


@dataclass
class AlertChannelConfig:
    """Configuration for routing alerts to channels."""

    alert_type: str
    low_channels: list[str] = dataclass_field(default_factory=list)
    medium_channels: list[str] = dataclass_field(default_factory=list)
    high_channels: list[str] = dataclass_field(default_factory=list)
    critical_channels: list[str] = dataclass_field(default_factory=list)

    def get_channels_for_severity(self, severity: str) -> list[str]:
        """Get channels to notify for a given severity level."""
        severity_map = {
            "LOW": self.low_channels,
            "MEDIUM": self.medium_channels,
            "HIGH": self.high_channels,
            "CRITICAL": self.critical_channels,
        }
        return severity_map.get(severity.upper(), self.medium_channels)


class FlakyTestAlertConfig:
    """Configuration for flaky test alerting system."""

    def __init__(self) -> None:
        """Initialize alert configuration with defaults."""
        # Define default channel routing for each alert type
        self.channel_routes: dict[str, AlertChannelConfig] = {
            "NEW_FLAKY_TEST": AlertChannelConfig(
                alert_type="NEW_FLAKY_TEST",
                low_channels=["operator_log"],
                medium_channels=["operator_log", "slack"],
                high_channels=["operator_log", "slack", "email"],
                critical_channels=["operator_log", "slack", "email"],
            ),
            "REGRESSION_SPIKE": AlertChannelConfig(
                alert_type="REGRESSION_SPIKE",
                low_channels=["operator_log"],
                medium_channels=["operator_log", "slack"],
                high_channels=["operator_log", "slack", "email"],
                critical_channels=["operator_log", "slack", "email", "pagerduty"],
            ),
            "CRITICAL_FLAKINESS": AlertChannelConfig(
                alert_type="CRITICAL_FLAKINESS",
                low_channels=["operator_log"],
                medium_channels=["operator_log", "slack"],
                high_channels=["operator_log", "slack", "email", "github"],
                critical_channels=["operator_log", "slack", "email", "github", "pagerduty"],
            ),
            "MODULE_OUTBREAK": AlertChannelConfig(
                alert_type="MODULE_OUTBREAK",
                low_channels=["operator_log"],
                medium_channels=["operator_log", "slack"],
                high_channels=["operator_log", "slack", "email"],
                critical_channels=["operator_log", "slack", "email", "pagerduty"],
            ),
        }

        # Define severity thresholds for different metrics
        self.thresholds: dict[str, AlertThreshold] = {
            "flaky_test_count": AlertThreshold(
                alert_type="flaky_test_count",
                low_threshold=1,
                medium_threshold=5,
                high_threshold=10,
                critical_threshold=20,
            ),
            "failure_rate": AlertThreshold(
                alert_type="failure_rate",
                low_threshold=0.05,
                medium_threshold=0.1,
                high_threshold=0.2,
                critical_threshold=0.5,
            ),
            "regression_spike": AlertThreshold(
                alert_type="regression_spike",
                low_threshold=0.2,  # 20% increase
                medium_threshold=0.5,  # 50% increase
                high_threshold=1.0,  # 100% increase (doubled)
                critical_threshold=2.0,  # 200% increase (tripled)
            ),
        }

        # Configuration overrides (can be set via environment or config files)
        self.overrides: dict[str, Any] = {}

    def get_channels_for_alert(
        self,
        alert_type: str,
        severity: str = "MEDIUM",
    ) -> list[str]:
        """Get list of channels to notify for an alert.

        Args:
            alert_type: Type of alert (e.g., "NEW_FLAKY_TEST")
            severity: Severity level ("LOW", "MEDIUM", "HIGH", "CRITICAL")

        Returns:
            List of channel names to notify
        """
        route = self.channel_routes.get(alert_type)
        if not route:
            # Default routing for unknown alert types
            if severity.upper() in ("HIGH", "CRITICAL"):
                return ["operator_log", "slack"]
            return ["operator_log"]

        return route.get_channels_for_severity(severity)

    def get_threshold(self, metric_name: str, severity: str = "MEDIUM") -> float:
        """Get threshold value for a metric at a given severity level.

        Args:
            metric_name: Name of the metric (e.g., "flaky_test_count")
            severity: Severity level ("LOW", "MEDIUM", "HIGH", "CRITICAL")

        Returns:
            Threshold value for the metric
        """
        threshold = self.thresholds.get(metric_name)
        if not threshold:
            return 0

        severity_map = {
            "LOW": threshold.low_threshold,
            "MEDIUM": threshold.medium_threshold,
            "HIGH": threshold.high_threshold,
            "CRITICAL": threshold.critical_threshold,
        }
        return severity_map.get(severity.upper(), threshold.medium_threshold)

    def should_alert_on_flaky_count(self, count: int) -> tuple[bool, str]:
        """Determine if a flaky test count should trigger an alert.

        Args:
            count: Number of flaky tests

        Returns:
            Tuple of (should_alert, severity)
        """
        if count >= self.get_threshold("flaky_test_count", "CRITICAL"):
            return True, "CRITICAL"
        if count >= self.get_threshold("flaky_test_count", "HIGH"):
            return True, "HIGH"
        if count >= self.get_threshold("flaky_test_count", "MEDIUM"):
            return True, "MEDIUM"
        if count >= self.get_threshold("flaky_test_count", "LOW"):
            return True, "LOW"
        return False, ""

    def should_alert_on_failure_rate(self, rate: float) -> tuple[bool, str]:
        """Determine if a failure rate should trigger an alert.

        Args:
            rate: Failure rate (0-1 scale)

        Returns:
            Tuple of (should_alert, severity)
        """
        if rate >= self.get_threshold("failure_rate", "CRITICAL"):
            return True, "CRITICAL"
        if rate >= self.get_threshold("failure_rate", "HIGH"):
            return True, "HIGH"
        if rate >= self.get_threshold("failure_rate", "MEDIUM"):
            return True, "MEDIUM"
        if rate >= self.get_threshold("failure_rate", "LOW"):
            return True, "LOW"
        return False, ""

    def should_alert_on_regression(self, increase_percent: float) -> tuple[bool, str]:
        """Determine if a regression spike should trigger an alert.

        Args:
            increase_percent: Increase percentage (e.g., 0.5 for 50%)

        Returns:
            Tuple of (should_alert, severity)
        """
        if increase_percent >= self.get_threshold("regression_spike", "CRITICAL"):
            return True, "CRITICAL"
        if increase_percent >= self.get_threshold("regression_spike", "HIGH"):
            return True, "HIGH"
        if increase_percent >= self.get_threshold("regression_spike", "MEDIUM"):
            return True, "MEDIUM"
        if increase_percent >= self.get_threshold("regression_spike", "LOW"):
            return True, "LOW"
        return False, ""

    def set_override(self, key: str, value: Any) -> None:
        """Set a configuration override.

        Args:
            key: Configuration key
            value: Configuration value
        """
        self.overrides[key] = value

    def get_override(self, key: str, default: Any = None) -> Any:
        """Get a configuration override.

        Args:
            key: Configuration key
            default: Default value if not found

        Returns:
            Configuration value or default
        """
        return self.overrides.get(key, default)
