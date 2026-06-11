# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for flaky test alert configuration system."""

import pytest

from operations_center.observer.flaky_test_alert_config import (
    AlertChannelConfig,
    AlertThreshold,
    FlakyTestAlertConfig,
)


class TestAlertThreshold:
    """Test AlertThreshold dataclass."""

    def test_create_threshold(self) -> None:
        threshold = AlertThreshold(
            alert_type="flaky_test_count",
            low_threshold=1,
            medium_threshold=5,
            high_threshold=10,
            critical_threshold=20,
        )
        assert threshold.alert_type == "flaky_test_count"
        assert threshold.low_threshold == 1
        assert threshold.medium_threshold == 5


class TestAlertChannelConfig:
    """Test AlertChannelConfig dataclass."""

    def test_create_config(self) -> None:
        config = AlertChannelConfig(
            alert_type="NEW_FLAKY_TEST",
            low_channels=["operator_log"],
            medium_channels=["operator_log", "slack"],
            high_channels=["operator_log", "slack", "email"],
            critical_channels=["operator_log", "slack", "email", "pagerduty"],
        )
        assert config.alert_type == "NEW_FLAKY_TEST"
        assert len(config.low_channels) == 1
        assert len(config.medium_channels) == 2

    def test_get_channels_for_severity(self) -> None:
        config = AlertChannelConfig(
            alert_type="TEST",
            low_channels=["a"],
            medium_channels=["a", "b"],
            high_channels=["a", "b", "c"],
            critical_channels=["a", "b", "c", "d"],
        )

        assert config.get_channels_for_severity("LOW") == ["a"]
        assert config.get_channels_for_severity("MEDIUM") == ["a", "b"]
        assert config.get_channels_for_severity("HIGH") == ["a", "b", "c"]
        assert config.get_channels_for_severity("CRITICAL") == ["a", "b", "c", "d"]

    def test_get_channels_unknown_severity(self) -> None:
        config = AlertChannelConfig(
            alert_type="TEST",
            medium_channels=["default"],
        )

        # Unknown severity defaults to medium
        assert config.get_channels_for_severity("UNKNOWN") == ["default"]


class TestFlakyTestAlertConfig:
    """Test FlakyTestAlertConfig."""

    def test_initialization(self) -> None:
        config = FlakyTestAlertConfig()
        assert config is not None
        assert len(config.channel_routes) == 4
        assert len(config.thresholds) == 3

    def test_default_channel_routes(self) -> None:
        config = FlakyTestAlertConfig()

        # Check that all expected alert types have routes
        assert "NEW_FLAKY_TEST" in config.channel_routes
        assert "REGRESSION_SPIKE" in config.channel_routes
        assert "CRITICAL_FLAKINESS" in config.channel_routes
        assert "MODULE_OUTBREAK" in config.channel_routes

    def test_get_channels_for_alert_low(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("NEW_FLAKY_TEST", "LOW")

        assert "operator_log" in channels
        assert isinstance(channels, list)

    def test_get_channels_for_alert_critical(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("REGRESSION_SPIKE", "CRITICAL")

        assert "operator_log" in channels
        assert "slack" in channels
        assert "email" in channels
        assert "pagerduty" in channels

    def test_get_channels_for_unknown_alert(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("UNKNOWN_ALERT", "HIGH")

        # Should return default routing for unknown alerts
        assert "operator_log" in channels
        assert "slack" in channels

    def test_get_threshold(self) -> None:
        config = FlakyTestAlertConfig()

        # Test flaky_test_count thresholds
        assert config.get_threshold("flaky_test_count", "LOW") == 1
        assert config.get_threshold("flaky_test_count", "MEDIUM") == 5
        assert config.get_threshold("flaky_test_count", "HIGH") == 10
        assert config.get_threshold("flaky_test_count", "CRITICAL") == 20

    def test_get_threshold_unknown_metric(self) -> None:
        config = FlakyTestAlertConfig()
        assert config.get_threshold("unknown_metric") == 0

    def test_should_alert_on_flaky_count(self) -> None:
        config = FlakyTestAlertConfig()

        # Test different counts
        should_alert, severity = config.should_alert_on_flaky_count(0)
        assert should_alert is False

        should_alert, severity = config.should_alert_on_flaky_count(3)
        assert should_alert is True
        assert severity == "MEDIUM"

        should_alert, severity = config.should_alert_on_flaky_count(15)
        assert should_alert is True
        assert severity == "HIGH"

        should_alert, severity = config.should_alert_on_flaky_count(25)
        assert should_alert is True
        assert severity == "CRITICAL"

    def test_should_alert_on_failure_rate(self) -> None:
        config = FlakyTestAlertConfig()

        # Test different rates
        should_alert, severity = config.should_alert_on_failure_rate(0.02)
        assert should_alert is False

        should_alert, severity = config.should_alert_on_failure_rate(0.07)
        assert should_alert is True
        assert severity == "MEDIUM"

        should_alert, severity = config.should_alert_on_failure_rate(0.25)
        assert should_alert is True
        assert severity == "HIGH"

        should_alert, severity = config.should_alert_on_failure_rate(0.6)
        assert should_alert is True
        assert severity == "CRITICAL"

    def test_should_alert_on_regression(self) -> None:
        config = FlakyTestAlertConfig()

        # Test different regression percentages
        should_alert, severity = config.should_alert_on_regression(0.1)  # 10%
        assert should_alert is False

        should_alert, severity = config.should_alert_on_regression(0.4)  # 40%
        assert should_alert is True
        assert severity == "MEDIUM"

        should_alert, severity = config.should_alert_on_regression(0.8)  # 80%
        assert should_alert is True
        assert severity == "HIGH"

        should_alert, severity = config.should_alert_on_regression(2.5)  # 250%
        assert should_alert is True
        assert severity == "CRITICAL"

    def test_set_and_get_override(self) -> None:
        config = FlakyTestAlertConfig()

        # Test setting and getting overrides
        config.set_override("custom_threshold", 42)
        assert config.get_override("custom_threshold") == 42

        # Test default value
        assert config.get_override("nonexistent", "default") == "default"

    def test_override_with_complex_value(self) -> None:
        config = FlakyTestAlertConfig()

        override_value = {
            "channels": ["slack", "email"],
            "threshold": 10,
        }
        config.set_override("custom_alert", override_value)

        retrieved = config.get_override("custom_alert")
        assert retrieved["channels"] == ["slack", "email"]
        assert retrieved["threshold"] == 10
