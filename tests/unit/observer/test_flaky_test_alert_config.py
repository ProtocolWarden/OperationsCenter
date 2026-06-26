# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for flaky test alert configuration system."""

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
            info_threshold=1,
            warning_threshold=5,
            critical_threshold=10,
            emergency_threshold=20,
        )
        assert threshold.alert_type == "flaky_test_count"
        assert threshold.info_threshold == 1
        assert threshold.warning_threshold == 5
        assert threshold.critical_threshold == 10
        assert threshold.emergency_threshold == 20


class TestAlertChannelConfig:
    """Test AlertChannelConfig dataclass."""

    def test_create_config(self) -> None:
        config = AlertChannelConfig(
            alert_type="NEW_FLAKY_TEST",
            info_channels=["operator_log"],
            warning_channels=["operator_log", "slack"],
            critical_channels=["operator_log", "slack", "email"],
            emergency_channels=["operator_log", "slack", "email", "pagerduty"],
        )
        assert config.alert_type == "NEW_FLAKY_TEST"
        assert len(config.info_channels) == 1
        assert len(config.warning_channels) == 2

    def test_get_channels_for_severity(self) -> None:
        config = AlertChannelConfig(
            alert_type="TEST",
            info_channels=["a"],
            warning_channels=["a", "b"],
            critical_channels=["a", "b", "c"],
            emergency_channels=["a", "b", "c", "d"],
        )

        assert config.get_channels_for_severity("INFO") == ["a"]
        assert config.get_channels_for_severity("WARNING") == ["a", "b"]
        assert config.get_channels_for_severity("CRITICAL") == ["a", "b", "c"]
        assert config.get_channels_for_severity("EMERGENCY") == ["a", "b", "c", "d"]

    def test_get_channels_unknown_severity(self) -> None:
        config = AlertChannelConfig(
            alert_type="TEST",
            warning_channels=["default"],
        )

        # Unknown severity defaults to warning
        assert config.get_channels_for_severity("UNKNOWN") == ["default"]


class TestFlakyTestAlertConfig:
    """Test FlakyTestAlertConfig."""

    def test_initialization(self) -> None:
        config = FlakyTestAlertConfig()
        assert config is not None
        assert len(config.channel_routes) == 6
        assert len(config.thresholds) == 5

    def test_default_channel_routes(self) -> None:
        config = FlakyTestAlertConfig()

        # Check that all expected alert types have routes
        assert "NEW_FLAKY_TEST" in config.channel_routes
        assert "REGRESSION_SPIKE" in config.channel_routes
        assert "CRITICAL_FLAKINESS" in config.channel_routes
        assert "MODULE_OUTBREAK" in config.channel_routes
        assert "EXTRACTION_SUCCESS_RATE_LOW" in config.channel_routes

    def test_get_channels_for_alert_info(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("NEW_FLAKY_TEST", "INFO")

        assert "operator_log" in channels
        assert isinstance(channels, list)

    def test_get_channels_for_alert_critical(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("REGRESSION_SPIKE", "CRITICAL")

        assert "operator_log" in channels
        assert "slack" in channels
        assert "email" in channels
        # pagerduty only in emergency, not critical
        assert "pagerduty" not in channels

    def test_get_channels_for_unknown_alert(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("UNKNOWN_ALERT", "WARNING")

        # Should return default channels for unknown alert type
        assert isinstance(channels, list)
        assert "operator_log" in channels

    def test_get_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        threshold_value = config.get_threshold("flaky_test_count", "WARNING")

        assert threshold_value == 5
        assert isinstance(threshold_value, (int, float))

    def test_should_alert_on_flaky_count(self) -> None:
        config = FlakyTestAlertConfig()

        # Test no alert when count is below warning threshold
        should_alert, severity = config.should_alert_on_flaky_count(3)
        assert should_alert is False

        # Test warning alert when count is at warning threshold
        should_alert, severity = config.should_alert_on_flaky_count(5)
        assert should_alert is True
        assert severity == "WARNING"

        # Test critical alert when count is at critical threshold
        should_alert, severity = config.should_alert_on_flaky_count(10)
        assert should_alert is True
        assert severity == "CRITICAL"

    def test_should_alert_on_failure_rate(self) -> None:
        config = FlakyTestAlertConfig()

        # Test no alert when rate is below warning threshold
        should_alert, severity = config.should_alert_on_failure_rate(0.05)
        assert should_alert is False

        # Test warning alert when rate is at warning threshold
        should_alert, severity = config.should_alert_on_failure_rate(0.1)
        assert should_alert is True
        assert severity == "WARNING"

    def test_should_alert_on_regression(self) -> None:
        config = FlakyTestAlertConfig()

        # Test no alert below info threshold
        should_alert, severity = config.should_alert_on_regression(0.1)
        assert should_alert is False

        # Test alert at warning threshold (50% increase)
        should_alert, severity = config.should_alert_on_regression(0.5)
        assert should_alert is True
        assert severity == "WARNING"


class TestExtractionSuccessRateConfig:
    """Tests for extraction success rate alert configuration."""

    def test_extraction_success_rate_threshold_exists(self) -> None:
        config = FlakyTestAlertConfig()
        assert "extraction_success_rate" in config.thresholds

    def test_extraction_success_rate_channel_route_exists(self) -> None:
        config = FlakyTestAlertConfig()
        assert "EXTRACTION_SUCCESS_RATE_LOW" in config.channel_routes

    def test_no_alert_when_rate_above_warning_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        should_alert, severity = config.should_alert_on_extraction_success_rate(90.0)
        assert should_alert is False
        assert severity == ""

    def test_no_alert_when_rate_equals_warning_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        warning_threshold = config.get_threshold("extraction_success_rate", "WARNING")
        should_alert, _ = config.should_alert_on_extraction_success_rate(warning_threshold)
        assert should_alert is False

    def test_no_alert_at_100_percent(self) -> None:
        config = FlakyTestAlertConfig()
        should_alert, severity = config.should_alert_on_extraction_success_rate(100.0)
        assert should_alert is False
        assert severity == ""

    def test_warning_alert_below_warning_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        should_alert, severity = config.should_alert_on_extraction_success_rate(75.0)
        assert should_alert is True
        assert severity == "WARNING"

    def test_critical_alert_below_critical_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        should_alert, severity = config.should_alert_on_extraction_success_rate(40.0)
        assert should_alert is True
        assert severity == "CRITICAL"

    def test_emergency_alert_below_emergency_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        should_alert, severity = config.should_alert_on_extraction_success_rate(5.0)
        assert should_alert is True
        assert severity == "EMERGENCY"

    def test_emergency_alert_at_zero_percent(self) -> None:
        config = FlakyTestAlertConfig()
        should_alert, severity = config.should_alert_on_extraction_success_rate(0.0)
        assert should_alert is True
        assert severity == "EMERGENCY"

    def test_warning_alert_just_below_warning_threshold(self) -> None:
        """79.9% is just below the 80% warning threshold."""
        config = FlakyTestAlertConfig()
        warning_threshold = config.get_threshold("extraction_success_rate", "WARNING")
        should_alert, severity = config.should_alert_on_extraction_success_rate(
            warning_threshold - 0.1
        )
        assert should_alert is True
        assert severity == "WARNING"

    def test_critical_alert_just_below_critical_threshold(self) -> None:
        config = FlakyTestAlertConfig()
        critical_threshold = config.get_threshold("extraction_success_rate", "CRITICAL")
        should_alert, severity = config.should_alert_on_extraction_success_rate(
            critical_threshold - 0.1
        )
        assert should_alert is True
        assert severity == "CRITICAL"

    def test_warning_threshold_is_higher_than_critical(self) -> None:
        config = FlakyTestAlertConfig()
        warning = config.get_threshold("extraction_success_rate", "WARNING")
        critical = config.get_threshold("extraction_success_rate", "CRITICAL")
        assert warning > critical

    def test_critical_threshold_is_higher_than_emergency(self) -> None:
        config = FlakyTestAlertConfig()
        critical = config.get_threshold("extraction_success_rate", "CRITICAL")
        emergency = config.get_threshold("extraction_success_rate", "EMERGENCY")
        assert critical > emergency

    def test_channels_warning_includes_slack(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("EXTRACTION_SUCCESS_RATE_LOW", "WARNING")
        assert "operator_log" in channels
        assert "slack" in channels

    def test_channels_critical_includes_email(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("EXTRACTION_SUCCESS_RATE_LOW", "CRITICAL")
        assert "operator_log" in channels
        assert "slack" in channels
        assert "email" in channels

    def test_channels_emergency_includes_pagerduty(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("EXTRACTION_SUCCESS_RATE_LOW", "EMERGENCY")
        assert "pagerduty" in channels

    def test_channels_info_includes_operator_log(self) -> None:
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("EXTRACTION_SUCCESS_RATE_LOW", "INFO")
        assert "operator_log" in channels


class TestMessageQualityRateThresholdValues:
    """Verify the exact configured threshold values and boundary behaviour for
    should_alert_on_message_quality_rate()."""

    def test_warning_threshold_value_is_80(self) -> None:
        config = FlakyTestAlertConfig()
        assert config.get_threshold("message_quality_rate", "WARNING") == 80.0

    def test_critical_threshold_value_is_50(self) -> None:
        config = FlakyTestAlertConfig()
        assert config.get_threshold("message_quality_rate", "CRITICAL") == 50.0

    def test_emergency_threshold_value_is_10(self) -> None:
        config = FlakyTestAlertConfig()
        assert config.get_threshold("message_quality_rate", "EMERGENCY") == 10.0

    def test_exactly_at_warning_threshold_does_not_alert(self) -> None:
        """80.0 is not *below* 80.0 → no alert."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(80.0)
        assert not should
        assert sev == ""

    def test_just_below_warning_threshold_alerts_warning(self) -> None:
        """79.9 is below 80.0 → WARNING."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(79.9)
        assert should
        assert sev == "WARNING"

    def test_exactly_at_critical_threshold_alerts_warning_not_critical(self) -> None:
        """50.0 is not below the 50.0 critical threshold, but it is below 80.0 → WARNING."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(50.0)
        assert should
        assert sev == "WARNING"

    def test_zero_rate_alerts_emergency(self) -> None:
        """0.0 is below every threshold → EMERGENCY."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(0.0)
        assert should
        assert sev == "EMERGENCY"
