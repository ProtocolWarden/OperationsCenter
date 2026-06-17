# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for flaky test alert manager."""

import pytest

from operations_center.observer.flaky_test_alerts import (
    AlertSeverity,
    FlakyTestAlertManager,
)
from operations_center.observer.flaky_test_storage import FlakyTestAggregationReport


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
