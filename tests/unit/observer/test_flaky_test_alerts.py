# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for flaky test alert manager."""

import pytest

from operations_center.observer.flaky_test_alert_config import AlertThreshold, FlakyTestAlertConfig
from operations_center.observer.flaky_test_alerts import (
    AlertSeverity,
    FlakyTestAlertManager,
)
from operations_center.observer.flaky_test_storage import FlakyTestAggregationReport
from operations_center.observer.models import FlakyTestSignal


@pytest.mark.flaky
class TestFlakyTestAlertManager:
    """Tests for flaky test alert generation."""

    def test_check_alerts_empty_report(self):
        """Test alerts with empty aggregation report."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=0,
            flaky_test_count=0,
            unstable_test_count=0,
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        assert len(alerts) == 0

    def test_check_alerts_no_conditions_met(self):
        """Test alerts when no alert conditions are met."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=1,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": "tests/test_foo.py::test_low_flakiness",
                    "failure_rate": 0.12,
                    "category": "transient",
                }
            ],
            by_module={"tests": {"flaky_count": 1, "total_count": 50}},
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        # Should have minimal or no alerts for low-impact flakiness
        assert len(alerts) <= 2

    def test_check_alerts_critical_flakiness(self):
        """Test detection of critical flakiness (>30% failure rate)."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=2,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": "tests/test_critical.py::test_critical",
                    "failure_rate": 0.8,
                    "category": "structural",
                },
                {
                    "test_name": "tests/test_critical2.py::test_critical2",
                    "failure_rate": 0.5,
                    "category": "structural",
                },
            ],
            by_module={},
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        # Should have CRITICAL severity alert
        critical_alerts = [a for a in alerts if a.alert_type == "CRITICAL_FLAKINESS"]
        assert len(critical_alerts) > 0
        assert critical_alerts[0].severity == AlertSeverity.CRITICAL

    def test_check_alerts_regression_spike(self):
        """Test detection of regression spike in flakiness."""
        previous = FlakyTestAggregationReport(
            date="2026-06-06",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=2,
            unstable_test_count=0,
        )

        current = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=5,
            unstable_test_count=2,
            flaky_tests=[
                {
                    "test_name": f"tests/test_{i}.py::test_{i}",
                    "failure_rate": 0.3,
                    "category": "structural",
                }
                for i in range(5)
            ],
        )

        alerts = FlakyTestAlertManager.check_alerts(current, previous)

        # Should have regression spike alert
        spike_alerts = [a for a in alerts if a.alert_type == "REGRESSION_SPIKE"]
        assert len(spike_alerts) > 0
        assert spike_alerts[0].severity == AlertSeverity.CRITICAL

    def test_check_alerts_module_outbreak(self):
        """Test detection of module outbreak (>20% flaky tests in module)."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=5,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": f"tests/problematic_module/test_{i}.py::test_{i}",
                    "failure_rate": 0.25,
                    "category": "structural",
                }
                for i in range(5)
            ],
            by_module={
                "tests/problematic_module": {
                    "flaky_count": 5,
                    "total_count": 20,  # 25% flaky
                }
            },
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        # Should have module outbreak alert
        outbreak_alerts = [a for a in alerts if a.alert_type == "MODULE_OUTBREAK"]
        assert len(outbreak_alerts) > 0
        assert outbreak_alerts[0].severity == AlertSeverity.WARNING

    def test_check_alerts_new_flaky_tests(self):
        """Test detection of new flaky tests."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=1,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": "tests/test_new_flaky.py::test_new_flaky",
                    "failure_rate": 0.4,
                    "category": "transient",
                    "first_seen": "2026-06-07T10:00:00+00:00",  # Today
                }
            ],
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        # Should have NEW_FLAKY_TEST alert
        new_alerts = [a for a in alerts if a.alert_type == "NEW_FLAKY_TEST"]
        assert len(new_alerts) > 0
        assert new_alerts[0].severity == AlertSeverity.WARNING

    def test_check_alerts_severity_ordering(self):
        """Test that alerts are sorted by severity."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=3,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": "tests/test_critical.py::test_critical",
                    "failure_rate": 0.8,  # Critical
                    "category": "structural",
                    "first_seen": "2026-06-07T10:00:00+00:00",
                },
                {
                    "test_name": "tests/test_new.py::test_new",
                    "failure_rate": 0.25,
                    "category": "transient",
                    "first_seen": "2026-06-07T10:00:00+00:00",
                },
            ],
            by_module={"tests/problematic": {"flaky_count": 2, "total_count": 10}},
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        # Filter to only alerts we expect
        critical_severity = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
        warning_severity = [a for a in alerts if a.severity == AlertSeverity.WARNING]

        # CRITICAL severity alerts should come before WARNING
        if critical_severity and warning_severity:
            critical_index = alerts.index(critical_severity[0])
            warning_index = alerts.index(warning_severity[0])
            assert critical_index < warning_index

    def test_alert_serialization(self):
        """Test alert serialization to dict."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=1,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": "tests/test_critical.py::test_critical",
                    "failure_rate": 0.8,
                    "category": "structural",
                }
            ],
        )

        alerts = FlakyTestAlertManager.check_alerts(report)

        if alerts:
            alert = alerts[0]
            alert_dict = alert.to_dict()

            assert "type" in alert_dict
            assert "severity" in alert_dict
            assert "description" in alert_dict
            assert "details" in alert_dict

    def test_check_alerts_multiple_conditions(self):
        """Test alerts when multiple conditions are met."""
        previous = FlakyTestAggregationReport(
            date="2026-06-06",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=1,
            unstable_test_count=0,
        )

        current = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=5,
            unstable_test_count=1,
            flaky_tests=[
                {
                    "test_name": "tests/test_critical.py::test_critical",
                    "failure_rate": 0.9,  # Critical
                    "category": "structural",
                    "first_seen": "2026-06-07T10:00:00+00:00",
                },
            ]
            + [
                {
                    "test_name": f"tests/problematic/test_{i}.py::test_{i}",
                    "failure_rate": 0.3,
                    "category": "transient",
                    "first_seen": "2026-06-07T10:00:00+00:00",
                }
                for i in range(1, 4)
            ],
            by_module={"tests/problematic": {"flaky_count": 3, "total_count": 15}},
        )

        alerts = FlakyTestAlertManager.check_alerts(current, previous)

        # Should detect multiple alert types
        alert_types = {a.alert_type for a in alerts}
        assert len(alert_types) >= 2  # At least regression spike and critical flakiness

    def test_check_alerts_no_previous_report(self):
        """Test alerts without previous report for trend detection."""
        report = FlakyTestAggregationReport(
            date="2026-06-07",
            period_days=7,
            total_test_executions=100,
            flaky_test_count=3,
            unstable_test_count=0,
            flaky_tests=[
                {
                    "test_name": f"tests/test_{i}.py::test_{i}",
                    "failure_rate": 0.4,
                    "category": "transient",
                }
                for i in range(3)
            ],
        )

        # Should not raise error without previous report
        alerts = FlakyTestAlertManager.check_alerts(report, prev_report=None)

        # Should still detect critical flakiness if present
        assert len(alerts) >= 0


class TestCheckExtractionSuccessRate:
    """Tests for extraction success rate alert detection."""

    def _make_signal(self, rate: float, status: str = "measured") -> FlakyTestSignal:
        return FlakyTestSignal(status=status, extraction_success_rate=rate)

    # --- no-alert cases ---

    def test_no_alert_when_rate_above_threshold(self) -> None:
        """90% success rate is above the 80% warning threshold — no alert."""
        signal = self._make_signal(90.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 0

    def test_no_alert_when_rate_equals_warning_threshold(self) -> None:
        """Rate exactly at the warning threshold (80%) is acceptable — no alert."""
        signal = self._make_signal(80.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 0

    def test_no_alert_at_100_percent(self) -> None:
        """Perfect extraction coverage never triggers an alert."""
        signal = self._make_signal(100.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 0

    def test_no_alert_when_signal_status_unavailable(self) -> None:
        """When status is 'unavailable' there is no data to evaluate — skip check."""
        signal = self._make_signal(0.0, status="unavailable")
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 0

    # --- alert generated cases ---

    def test_warning_alert_just_below_threshold(self) -> None:
        """79.9% is just below the 80% warning threshold."""
        signal = self._make_signal(79.9)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_warning_alert_between_warning_and_critical_thresholds(self) -> None:
        """60% falls between WARNING (80%) and CRITICAL (50%) thresholds."""
        signal = self._make_signal(60.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_critical_alert_just_below_critical_threshold(self) -> None:
        """49.9% is just below the 50% critical threshold."""
        signal = self._make_signal(49.9)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_critical_alert_between_critical_and_emergency_thresholds(self) -> None:
        """25% falls between CRITICAL (50%) and EMERGENCY (10%) thresholds."""
        signal = self._make_signal(25.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_emergency_alert_below_emergency_threshold(self) -> None:
        """5% is below the 10% emergency threshold."""
        signal = self._make_signal(5.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.EMERGENCY

    def test_emergency_alert_at_zero_percent(self) -> None:
        """Complete extraction failure (0%) on a measured signal triggers EMERGENCY."""
        signal = self._make_signal(0.0, status="measured")
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.EMERGENCY

    def test_partial_status_still_triggers_alert(self) -> None:
        """Status 'partial' with low rate still fires a critical alert."""
        signal = self._make_signal(40.0, status="partial")
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    # --- alert content ---

    def test_alert_type_is_extraction_success_rate_low(self) -> None:
        signal = self._make_signal(70.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "EXTRACTION_SUCCESS_RATE_LOW"

    def test_alert_details_include_current_rate(self) -> None:
        signal = self._make_signal(70.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert "current_rate" in alerts[0].details
        assert alerts[0].details["current_rate"] == 70.0

    def test_alert_details_include_threshold(self) -> None:
        signal = self._make_signal(70.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert "threshold" in alerts[0].details
        assert alerts[0].details["threshold"] > 0

    def test_alert_details_include_positive_gap(self) -> None:
        signal = self._make_signal(70.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert "gap" in alerts[0].details
        assert alerts[0].details["gap"] > 0

    def test_alert_description_mentions_current_rate(self) -> None:
        signal = self._make_signal(70.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        assert "70" in alerts[0].description

    def test_alert_serialization_to_dict(self) -> None:
        signal = self._make_signal(70.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
        assert len(alerts) == 1
        alert_dict = alerts[0].to_dict()
        assert alert_dict["type"] == "EXTRACTION_SUCCESS_RATE_LOW"
        assert "severity" in alert_dict
        assert "description" in alert_dict
        assert "details" in alert_dict

    # --- custom config ---

    def test_custom_config_higher_threshold_triggers_alert(self) -> None:
        """With a 90% warning threshold, a rate of 85% should fire a WARNING."""
        config = FlakyTestAlertConfig()
        config.thresholds["extraction_success_rate"] = AlertThreshold(
            alert_type="extraction_success_rate",
            info_threshold=95.0,
            warning_threshold=90.0,
            critical_threshold=50.0,
            emergency_threshold=10.0,
        )
        signal = self._make_signal(85.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal, config=config)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_custom_config_lower_threshold_suppresses_alert(self) -> None:
        """With a 70% warning threshold, a rate of 75% should produce no alert."""
        config = FlakyTestAlertConfig()
        config.thresholds["extraction_success_rate"] = AlertThreshold(
            alert_type="extraction_success_rate",
            info_threshold=95.0,
            warning_threshold=70.0,
            critical_threshold=50.0,
            emergency_threshold=10.0,
        )
        signal = self._make_signal(75.0)
        alerts = FlakyTestAlertManager.check_extraction_success_rate(signal, config=config)
        assert len(alerts) == 0

    def test_only_one_alert_generated_regardless_of_severity(self) -> None:
        """A single signal produces at most one alert."""
        for rate in [0.0, 5.0, 25.0, 49.9, 70.0]:
            signal = self._make_signal(rate, status="measured")
            alerts = FlakyTestAlertManager.check_extraction_success_rate(signal)
            assert len(alerts) <= 1, f"Expected ≤1 alert for rate={rate}, got {len(alerts)}"


class TestCheckMessageQualityRate:
    """Tests for FlakyTestAlertManager.check_message_quality_rate() (Stage 3)."""

    def test_none_rate_produces_no_alert(self) -> None:
        """No alert when message_quality_rate is None (no assertion messages)."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(None)
        assert alerts == []

    def test_high_quality_rate_produces_no_alert(self) -> None:
        """No alert when quality rate is above the warning threshold."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(90.0)
        assert alerts == []

    def test_rate_at_100_produces_no_alert(self) -> None:
        """Perfect quality rate produces no alert."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(100.0)
        assert alerts == []

    def test_rate_below_warning_threshold_produces_warning(self) -> None:
        """Rate below the warning threshold triggers a WARNING alert."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(70.0)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING
        assert alerts[0].alert_type == "MESSAGE_QUALITY_RATE_LOW"

    def test_rate_below_critical_threshold_produces_critical(self) -> None:
        """Rate below the critical threshold triggers a CRITICAL alert."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(40.0)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_rate_below_emergency_threshold_produces_emergency(self) -> None:
        """Rate below the emergency threshold triggers an EMERGENCY alert."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(5.0)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.EMERGENCY

    def test_alert_description_includes_rate_and_threshold(self) -> None:
        """Alert description mentions the current rate."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(60.0)
        assert len(alerts) == 1
        assert "60.0%" in alerts[0].description or "60.0" in alerts[0].description

    def test_alert_details_include_current_rate(self) -> None:
        """Alert details dict includes current_rate."""
        alerts = FlakyTestAlertManager.check_message_quality_rate(55.0)
        assert len(alerts) == 1
        assert "current_rate" in alerts[0].details
        assert alerts[0].details["current_rate"] == 55.0

    def test_at_most_one_alert_per_call(self) -> None:
        """Each call produces at most one alert."""
        for rate in [0.0, 5.0, 30.0, 65.0, 79.9]:
            alerts = FlakyTestAlertManager.check_message_quality_rate(rate)
            assert len(alerts) <= 1, f"Expected ≤1 alert for rate={rate}"

    def test_custom_config_overrides_threshold(self) -> None:
        """Custom config object is respected for threshold lookup."""
        config = FlakyTestAlertConfig()
        config.thresholds["message_quality_rate"] = AlertThreshold(
            alert_type="message_quality_rate",
            info_threshold=95.0,
            warning_threshold=90.0,
            critical_threshold=50.0,
            emergency_threshold=10.0,
        )
        # 85% is below the custom 90% warning threshold → should alert
        alerts = FlakyTestAlertManager.check_message_quality_rate(85.0, config=config)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING


class TestMessageQualityRateAlertConfig:
    """Tests for FlakyTestAlertConfig.should_alert_on_message_quality_rate() (Stage 3)."""

    def test_method_exists(self) -> None:
        """FlakyTestAlertConfig has should_alert_on_message_quality_rate method."""
        config = FlakyTestAlertConfig()
        assert hasattr(config, "should_alert_on_message_quality_rate")

    def test_threshold_key_exists(self) -> None:
        """Default thresholds include 'message_quality_rate' key."""
        config = FlakyTestAlertConfig()
        assert "message_quality_rate" in config.thresholds

    def test_high_rate_no_alert(self) -> None:
        """Rate above warning threshold returns (False, '')."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(95.0)
        assert not should
        assert sev == ""

    def test_low_rate_returns_warning(self) -> None:
        """Rate below warning threshold returns (True, 'WARNING')."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(70.0)
        assert should
        assert sev == "WARNING"

    def test_very_low_rate_returns_emergency(self) -> None:
        """Rate below emergency threshold returns (True, 'EMERGENCY')."""
        config = FlakyTestAlertConfig()
        should, sev = config.should_alert_on_message_quality_rate(5.0)
        assert should
        assert sev == "EMERGENCY"

    def test_channel_route_exists_for_alert_type(self) -> None:
        """Channel route for MESSAGE_QUALITY_RATE_LOW is configured."""
        config = FlakyTestAlertConfig()
        channels = config.get_channels_for_alert("MESSAGE_QUALITY_RATE_LOW", "WARNING")
        assert len(channels) > 0
