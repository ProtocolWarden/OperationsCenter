# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for dashboard coverage panels."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from operations_center.observer.coverage_models import (
    CoverageSnapshot,
    CoverageTrendAnalysis,
    ModuleCoverage,
)
from operations_center.observer.dashboard import DashboardProvider
from operations_center.observer.health_checks import HealthChecker
from operations_center.observer.metrics import MetricsCollector
from operations_center.observer.models import CoverageSignal


class TestDashboardCoveragePanels:
    """Test coverage dashboard panels."""

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
        system_metrics.healthy_collectors = 10
        system_metrics.degraded_collectors = 0
        system_metrics.critical_collectors = 0
        system_metrics.system_health_status = "HEALTHY"
        system_metrics.overall_error_rate_percent = 0.0
        system_metrics.total_validation_failures = 0
        system_metrics.collector_metrics = {}
        collector.get_system_metrics.return_value = system_metrics
        collector.get_all_collector_metrics.return_value = {}
        return collector

    @pytest.fixture
    def coverage_snapshot(self) -> CoverageSnapshot:
        """Create test coverage snapshot."""
        return CoverageSnapshot(
            timestamp=datetime.now(timezone.utc),
            run_id="abc123",
            source="coverage.py",
            overall_statement_coverage_pct=85.5,
            overall_branch_coverage_pct=78.2,
            overall_line_coverage_pct=86.1,
            module_coverages=[
                ModuleCoverage(
                    module_path="src/operations_center/observer",
                    statement_coverage_pct=88.5,
                    branch_coverage_pct=80.2,
                    line_coverage_pct=89.1,
                    statement_count=500,
                    branch_count=300,
                    line_count=600,
                    health_status="healthy",
                ),
                ModuleCoverage(
                    module_path="src/operations_center/custodian",
                    statement_coverage_pct=72.1,
                    branch_coverage_pct=65.0,
                    line_coverage_pct=73.5,
                    statement_count=400,
                    branch_count=250,
                    line_count=450,
                    health_status="at_risk",
                ),
                ModuleCoverage(
                    module_path="src/operations_center/api",
                    statement_coverage_pct=65.3,
                    branch_coverage_pct=55.0,
                    line_coverage_pct=66.0,
                    statement_count=350,
                    branch_count=200,
                    line_count=400,
                    health_status="critical",
                ),
            ],
            uncovered_file_count=3,
            test_execution_time_ms=5000,
            test_count=250,
        )

    @pytest.fixture
    def coverage_trends(self) -> CoverageTrendAnalysis:
        """Create test coverage trends."""
        return CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="",
            window_start=datetime(2026, 6, 5, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 12, tzinfo=timezone.utc),
            measurements=[
                (datetime(2026, 6, 5, tzinfo=timezone.utc), 83.2),
                (datetime(2026, 6, 6, tzinfo=timezone.utc), 83.5),
                (datetime(2026, 6, 7, tzinfo=timezone.utc), 84.1),
                (datetime(2026, 6, 8, tzinfo=timezone.utc), 84.8),
                (datetime(2026, 6, 9, tzinfo=timezone.utc), 85.2),
                (datetime(2026, 6, 10, tzinfo=timezone.utc), 85.5),
                (datetime(2026, 6, 11, tzinfo=timezone.utc), 85.3),
                (datetime(2026, 6, 12, tzinfo=timezone.utc), 85.5),
            ],
            current_value=85.5,
            average_value=84.6,
            min_value=83.2,
            max_value=85.5,
            trend_direction="improving",
            trend_pct=0.28,
            regression_count=0,
            standard_deviation=0.95,
            stability_score=0.92,
            days_of_decline=0,
            projected_value_7days=86.5,
        )

    def test_panel_coverage_summary_available(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        coverage_snapshot: CoverageSnapshot,
    ) -> None:
        """Test coverage summary panel with available data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=coverage_snapshot,
        )

        panel = provider._panel_coverage_summary()

        assert panel.title == "Coverage Summary"
        assert len(panel.metrics) >= 4
        assert panel.metrics[0].name == "Overall Coverage"
        assert panel.metrics[0].value == 85.5
        assert panel.metrics[0].unit == "%"
        assert panel.metrics[0].status == "NOMINAL"

    def test_panel_coverage_summary_unavailable(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage summary panel without data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_coverage_summary()

        assert panel.title == "Coverage Summary"
        assert panel.metrics[0].status == "UNKNOWN"

    def test_panel_coverage_summary_health_status_healthy(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage summary with healthy coverage (>90%)."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(timezone.utc),
            run_id="abc123",
            source="coverage.py",
            overall_statement_coverage_pct=92.5,
            overall_branch_coverage_pct=91.0,
            overall_line_coverage_pct=93.0,
            uncovered_file_count=1,
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=snapshot,
        )

        panel = provider._panel_coverage_summary()

        assert panel.metrics[0].status == "HEALTHY"

    def test_panel_coverage_summary_health_status_critical(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage summary with critical coverage (<70%)."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(timezone.utc),
            run_id="abc123",
            source="coverage.py",
            overall_statement_coverage_pct=65.0,
            overall_branch_coverage_pct=60.0,
            overall_line_coverage_pct=64.0,
            uncovered_file_count=10,
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=snapshot,
        )

        panel = provider._panel_coverage_summary()

        assert panel.metrics[0].status == "CRITICAL"

    def test_panel_coverage_by_module_available(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        coverage_snapshot: CoverageSnapshot,
    ) -> None:
        """Test coverage by module panel with available data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=coverage_snapshot,
        )

        panel = provider._panel_coverage_by_module()

        assert panel.title == "Coverage by Module"
        assert len(panel.metrics) == 3
        assert panel.metrics[0].name == "src/operations_center/api"
        assert panel.metrics[0].value == 65.3
        assert panel.metrics[0].status == "CRITICAL"

    def test_panel_coverage_by_module_sorts_by_coverage(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        coverage_snapshot: CoverageSnapshot,
    ) -> None:
        """Test that modules are sorted by coverage (lowest first)."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=coverage_snapshot,
        )

        panel = provider._panel_coverage_by_module()

        values = [m.value for m in panel.metrics]
        assert values == sorted(values)

    def test_panel_coverage_by_module_unavailable(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage by module panel without data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_coverage_by_module()

        assert panel.title == "Coverage by Module"
        assert len(panel.metrics) == 0

    def test_panel_coverage_trend_available(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        coverage_trends: CoverageTrendAnalysis,
    ) -> None:
        """Test coverage trend panel with available data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_trends=coverage_trends,
        )

        panel = provider._panel_coverage_trend()

        assert panel.title == "Coverage Trend"
        assert len(panel.metrics) >= 4

        current_metric = next(m for m in panel.metrics if m.name == "Current Value")
        assert current_metric.value == 85.5

        trend_metric = next(m for m in panel.metrics if m.name == "Trend Direction")
        assert trend_metric.value == "IMPROVING"
        assert trend_metric.status == "HEALTHY"

    def test_panel_coverage_trend_degrading(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage trend panel with degrading trend."""
        trends = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="",
            window_start=datetime(2026, 6, 5, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 12, tzinfo=timezone.utc),
            measurements=[],
            current_value=82.0,
            average_value=84.0,
            min_value=82.0,
            max_value=86.0,
            trend_direction="degrading",
            trend_pct=-0.5,
            regression_count=3,
            standard_deviation=1.5,
            stability_score=0.65,
            days_of_decline=5,
            projected_value_7days=79.5,
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_trends=trends,
        )

        panel = provider._panel_coverage_trend()

        trend_metric = next(m for m in panel.metrics if m.name == "Trend Direction")
        assert trend_metric.value == "DEGRADING"
        assert trend_metric.status == "DEGRADED"

        regression_metric = next(m for m in panel.metrics if m.name == "Regressions Detected")
        assert regression_metric.value == 3
        assert regression_metric.status == "CRITICAL"

    def test_panel_coverage_trend_unavailable(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage trend panel without data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_coverage_trend()

        assert panel.title == "Coverage Trend"
        assert len(panel.metrics) == 0

    def test_panel_coverage_alerts_available(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage alerts panel with available alerts."""
        coverage_signal = CoverageSignal(
            status="measured",
            total_coverage_pct=75.0,
            statement_coverage_pct=75.0,
            branch_coverage_pct=70.0,
            line_coverage_pct=74.0,
            uncovered_file_count=5,
            active_alerts=[
                {
                    "alert_type": "below_threshold",
                    "severity": "warning",
                    "scope_id": "src/observer",
                    "current_value": 75.0,
                },
                {
                    "alert_type": "regression_detected",
                    "severity": "critical",
                    "scope_id": "src/api",
                    "current_value": 72.0,
                },
            ],
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_signal=coverage_signal,
        )

        panel = provider._panel_coverage_alerts()

        assert panel.title == "Coverage Alerts"
        assert len(panel.metrics) >= 2

        alert1 = next(m for m in panel.metrics if "below_threshold" in m.name)
        assert alert1.status == "WARNING"

        alert2 = next(m for m in panel.metrics if "regression_detected" in m.name)
        assert alert2.status == "CRITICAL"

    def test_panel_coverage_alerts_no_alerts(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        coverage_snapshot: CoverageSnapshot,
    ) -> None:
        """Test coverage alerts panel with no alerts."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=coverage_snapshot,
        )

        panel = provider._panel_coverage_alerts()

        assert panel.title == "Coverage Alerts"
        assert len(panel.metrics) == 1
        assert "No Active Alerts" in panel.metrics[0].value

    def test_generate_snapshot_includes_coverage_panels(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
        coverage_snapshot: CoverageSnapshot,
        coverage_trends: CoverageTrendAnalysis,
    ) -> None:
        """Test that generate_snapshot includes coverage panels."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_snapshot=coverage_snapshot,
            coverage_trends=coverage_trends,
        )

        snapshot = provider.generate_snapshot()

        panel_titles = [p.title for p in snapshot.panels]
        assert "Coverage Summary" in panel_titles
        assert "Coverage by Module" in panel_titles
        assert "Coverage Trend" in panel_titles
        assert "Coverage Alerts" in panel_titles

    def test_generate_snapshot_without_coverage_data(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test that generate_snapshot works without coverage data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
        )

        snapshot = provider.generate_snapshot()

        panel_titles = [p.title for p in snapshot.panels]
        assert "System Overview" in panel_titles
        assert "Coverage Summary" not in panel_titles

    def test_coverage_health_status_classification(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test coverage health status classification logic."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
        )

        assert provider._get_coverage_health_status(95.0) == "HEALTHY"
        assert provider._get_coverage_health_status(85.0) == "NOMINAL"
        assert provider._get_coverage_health_status(75.0) == "DEGRADED"
        assert provider._get_coverage_health_status(65.0) == "CRITICAL"
