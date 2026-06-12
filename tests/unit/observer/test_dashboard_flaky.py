# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for dashboard flaky test panels."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from operations_center.observer.dashboard import DashboardProvider
from operations_center.observer.models import FlakyTestSignal
from operations_center.observer.health_checks import HealthChecker
from operations_center.observer.metrics import MetricsCollector


class TestDashboardFlakyPanels:
    """Test flaky test dashboard panels."""

    @pytest.fixture
    def mock_health_checker(self) -> MagicMock:
        """Create mock health checker."""
        checker = MagicMock(spec=HealthChecker)
        health_report = MagicMock()
        health_report.critical_issues = []
        health_report.warnings = []
        health_report.overall_status.value = "HEALTHY"
        checker.run_all_checks.return_value = health_report
        return checker

    @pytest.fixture
    def mock_metrics_collector(self) -> MagicMock:
        """Create mock metrics collector."""
        collector = MagicMock(spec=MetricsCollector)
        system_metrics = MagicMock()
        system_metrics.total_collectors = 10
        system_metrics.healthy_collectors = 9
        system_metrics.degraded_collectors = 1
        system_metrics.critical_collectors = 0
        system_metrics.system_health_status = "HEALTHY"
        system_metrics.overall_error_rate_percent = 0.5
        system_metrics.total_validation_failures = 1
        system_metrics.collector_metrics = {}
        collector.get_system_metrics.return_value = system_metrics
        collector.get_all_collector_metrics.return_value = {}
        return collector

    @pytest.fixture
    def flaky_test_signal(self) -> FlakyTestSignal:
        """Create test flaky test signal."""
        return FlakyTestSignal(
            status="measured",
            flaky_test_count=5,
            unstable_test_count=3,
            affected_modules=["tests.unit.service", "tests.integration.api"],
            most_problematic_tests=[
                {"name": "test_timeout", "failure_rate": 0.45},
                {"name": "test_race_condition", "failure_rate": 0.35},
                {"name": "test_network", "failure_rate": 0.25},
            ],
            failure_rate_trend=5.2,
            recovery_rate=0.75,
            category_breakdown={
                "INTERMITTENT": 4,
                "INFRASTRUCTURE": 1,
            },
            estimated_impact={
                "ci_slowdown_percent": 8.5,
                "dev_time_hours": 12.3,
            },
            source="flaky-test-reporter",
            observed_at=datetime.now(timezone.utc),
            summary="5 flaky tests detected in 2 modules",
        )

    def test_panel_flaky_test_summary_available(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        flaky_test_signal: FlakyTestSignal,
    ) -> None:
        """Test flaky test summary panel with available data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=flaky_test_signal,
        )

        panel = provider._panel_flaky_test_summary()

        assert panel.title == "Flaky Tests Summary"
        assert len(panel.metrics) >= 4
        metric_names = [m.name for m in panel.metrics]
        assert "Flaky Tests" in metric_names
        assert "Unstable Tests" in metric_names
        assert "Recovery Rate" in metric_names

    def test_panel_flaky_test_summary_unavailable(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test flaky test summary panel with unavailable data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=None,
        )

        panel = provider._panel_flaky_test_summary()

        assert panel.title == "Flaky Tests Summary"
        assert any("Unavailable" in m.value for m in panel.metrics)

    def test_panel_flaky_test_categories(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        flaky_test_signal: FlakyTestSignal,
    ) -> None:
        """Test flaky test category breakdown panel."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=flaky_test_signal,
        )

        panel = provider._panel_flaky_test_categories()

        assert panel.title == "Flaky Tests by Category"
        assert len(panel.metrics) == 2
        metric_names = [m.name for m in panel.metrics]
        assert "Intermittent" in metric_names
        assert "Infrastructure" in metric_names

    def test_panel_most_problematic_tests(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        flaky_test_signal: FlakyTestSignal,
    ) -> None:
        """Test most problematic tests panel."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=flaky_test_signal,
        )

        panel = provider._panel_most_problematic_tests()

        assert panel.title == "Most Problematic Tests"
        assert len(panel.metrics) == 3
        metric_names = [m.name for m in panel.metrics]
        assert any("test_timeout" in name for name in metric_names)
        assert any("test_race_condition" in name for name in metric_names)

    def test_generate_snapshot_with_flaky_signal(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        flaky_test_signal: FlakyTestSignal,
    ) -> None:
        """Test dashboard snapshot generation with flaky test signal."""
        system_metrics = MagicMock()
        system_metrics.total_collectors = 10
        system_metrics.healthy_collectors = 9
        system_metrics.degraded_collectors = 1
        system_metrics.critical_collectors = 0
        system_metrics.system_health_status = "HEALTHY"
        system_metrics.overall_error_rate_percent = 0.5
        system_metrics.total_validation_failures = 1
        system_metrics.collector_metrics = {}
        mock_metrics_collector.get_system_metrics.return_value = system_metrics
        mock_metrics_collector.get_all_collector_metrics.return_value = {}

        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=flaky_test_signal,
        )

        snapshot = provider.generate_snapshot()

        assert snapshot is not None
        panel_titles = [p.title for p in snapshot.panels]
        assert "Flaky Tests Summary" in panel_titles
        assert "Flaky Tests by Category" in panel_titles
        assert "Most Problematic Tests" in panel_titles

    def test_flaky_test_status_healthy(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test flaky test status when no flaky tests."""
        signal = FlakyTestSignal(
            status="measured",
            flaky_test_count=0,
            unstable_test_count=0,
        )

        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=signal,
        )

        panel = provider._panel_flaky_test_summary()
        flaky_metric = next(m for m in panel.metrics if "Flaky Tests" in m.name)
        assert flaky_metric.status == "HEALTHY"

    def test_flaky_test_status_critical(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test flaky test status when many flaky tests."""
        signal = FlakyTestSignal(
            status="measured",
            flaky_test_count=15,
            unstable_test_count=0,
        )

        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=signal,
        )

        panel = provider._panel_flaky_test_summary()
        flaky_metric = next(m for m in panel.metrics if "Flaky Tests" in m.name)
        assert flaky_metric.status == "CRITICAL"
