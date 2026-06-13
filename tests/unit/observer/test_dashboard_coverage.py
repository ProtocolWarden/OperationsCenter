# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Comprehensive tests for dashboard coverage and system panels."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from operations_center.observer.coverage_models import (
    CoverageSnapshot,
    CoverageTrendAnalysis,
    ModuleCoverage,
)
from operations_center.observer.dashboard import (
    DashboardMetric,
    DashboardPanel,
    DashboardProvider,
    DashboardSnapshot,
)
from operations_center.observer.health_checks import HealthChecker
from operations_center.observer.metrics import MetricsCollector
from operations_center.observer.models import CoverageSignal, FlakyTestSignal


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
        assert panel.metrics[0].value == 65.3

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
            ],
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            coverage_signal=coverage_signal,
        )

        panel = provider._panel_coverage_alerts()

        assert panel.title == "Coverage Alerts"
        assert len(panel.metrics) >= 1

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


class TestDashboardDataclasses:
    """Test dashboard dataclass serialization and methods."""

    def test_dashboard_metric_to_dict(self) -> None:
        """Test DashboardMetric to_dict method."""
        metric = DashboardMetric(
            name="Test Metric",
            value=42.5,
            unit="%",
            status="HEALTHY",
            threshold_warning=50.0,
            threshold_critical=75.0,
        )

        result = metric.to_dict()

        assert result["name"] == "Test Metric"
        assert result["value"] == 42.5
        assert result["unit"] == "%"
        assert result["status"] == "HEALTHY"

    def test_dashboard_metric_to_dict_with_none_thresholds(self) -> None:
        """Test DashboardMetric to_dict with None thresholds."""
        metric = DashboardMetric(
            name="Simple Metric",
            value="OK",
            unit="status",
            status="NOMINAL",
        )

        result = metric.to_dict()

        assert result["threshold_warning"] is None
        assert result["threshold_critical"] is None

    def test_dashboard_panel_to_dict(self) -> None:
        """Test DashboardPanel to_dict method."""
        metrics = [
            DashboardMetric(
                name="Metric 1",
                value=50.0,
                unit="%",
                status="HEALTHY",
            ),
        ]
        panel = DashboardPanel(
            title="Test Panel",
            description="A test panel",
            metrics=metrics,
        )

        result = panel.to_dict()

        assert result["title"] == "Test Panel"
        assert len(result["metrics"]) == 1

    def test_dashboard_panel_to_dict_empty_metrics(self) -> None:
        """Test DashboardPanel to_dict with empty metrics."""
        panel = DashboardPanel(
            title="Empty Panel",
            description="Panel with no metrics",
            metrics=[],
        )

        result = panel.to_dict()

        assert result["title"] == "Empty Panel"
        assert result["metrics"] == []

    def test_dashboard_snapshot_to_dict(self) -> None:
        """Test DashboardSnapshot to_dict method."""
        panel = DashboardPanel(
            title="Test Panel",
            description="Description",
            metrics=[
                DashboardMetric(
                    name="Metric",
                    value=100,
                    unit="count",
                    status="HEALTHY",
                ),
            ],
        )
        snapshot = DashboardSnapshot(
            timestamp=datetime.now(timezone.utc),
            system_status="HEALTHY",
            panels=[panel],
            alerts=["Alert 1"],
        )

        result = snapshot.to_dict()

        assert result["system_status"] == "HEALTHY"
        assert len(result["panels"]) == 1


class TestDashboardSystemPanels:
    """Test system and error rate dashboard panels."""

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
    def mock_metrics_collector_with_data(self) -> MagicMock:
        """Create mock metrics collector with full data."""
        collector = MagicMock(spec=MetricsCollector)
        system_metrics = MagicMock()
        system_metrics.total_collectors = 10
        system_metrics.healthy_collectors = 9
        system_metrics.degraded_collectors = 1
        system_metrics.critical_collectors = 0
        system_metrics.system_health_status = "DEGRADED"
        system_metrics.overall_error_rate_percent = 0.5
        system_metrics.total_validation_failures = 5
        system_metrics.collector_metrics = {
            "c1": MagicMock(total_parse_errors=0, total_structure_errors=1),
            "c2": MagicMock(total_parse_errors=1, total_structure_errors=0),
        }
        collector.get_system_metrics.return_value = system_metrics
        collector_metrics = {
            "c1": MagicMock(
                max_latency_ms=250,
                mean_latency_ms=100,
                throughput_artifacts_per_sec=50,
                error_rate_percent=0.2,
                health_status="HEALTHY",
                successful_runs=95,
                total_runs=100,
            ),
        }
        collector.get_all_collector_metrics.return_value = collector_metrics
        return collector

    def test_panel_system_overview(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector_with_data: MagicMock,
    ) -> None:
        """Test system overview panel."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector_with_data,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_system_overview()

        assert panel.title == "System Overview"
        assert len(panel.metrics) >= 5

    def test_panel_error_rates(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector_with_data: MagicMock,
    ) -> None:
        """Test error rates panel."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector_with_data,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_error_rates()

        assert panel.title == "Error Rates"
        assert len(panel.metrics) >= 3

    def test_panel_latency(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector_with_data: MagicMock,
    ) -> None:
        """Test latency panel with collector data."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector_with_data,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_latency()

        assert panel.title == "Latency & Throughput"
        assert len(panel.metrics) >= 3

    def test_panel_collector_health(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector_with_data: MagicMock,
    ) -> None:
        """Test collector health panel."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector_with_data,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_collector_health()

        assert panel.title == "Collector Health"
        assert len(panel.metrics) >= 2


class TestDashboardFlakyTestPanels:
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
        system_metrics.total_collectors = 5
        system_metrics.healthy_collectors = 5
        system_metrics.degraded_collectors = 0
        system_metrics.critical_collectors = 0
        system_metrics.system_health_status = "HEALTHY"
        system_metrics.overall_error_rate_percent = 0.0
        system_metrics.total_validation_failures = 0
        system_metrics.collector_metrics = {}
        collector.get_system_metrics.return_value = system_metrics
        collector.get_all_collector_metrics.return_value = {}
        return collector

    def test_panel_flaky_test_summary_with_data(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test flaky test summary panel with data."""
        signal = FlakyTestSignal(
            flaky_test_count=3,
            unstable_test_count=5,
            recovery_rate=0.85,
            category_breakdown={"timeout": 2},
            most_problematic_tests=[],
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=signal,
        )

        panel = provider._panel_flaky_test_summary()

        assert panel.title == "Flaky Tests Summary"
        flaky_metric = next(m for m in panel.metrics if m.name == "Flaky Tests")
        assert flaky_metric.value == 3

    def test_panel_flaky_test_summary_no_data(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test flaky test summary panel without signal."""
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
        )

        panel = provider._panel_flaky_test_summary()

        assert panel.title == "Flaky Tests Summary"
        assert len(panel.metrics) == 1
        assert panel.metrics[0].status == "UNKNOWN"

    def test_panel_flaky_test_categories(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test flaky test categories panel."""
        signal = FlakyTestSignal(
            flaky_test_count=5,
            unstable_test_count=3,
            recovery_rate=0.8,
            category_breakdown={"timeout": 2, "assertion": 1},
            most_problematic_tests=[],
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=signal,
        )

        panel = provider._panel_flaky_test_categories()

        assert panel.title == "Flaky Tests by Category"
        assert len(panel.metrics) == 2

    def test_panel_most_problematic_tests(
        self,
        mock_health_checker: MagicMock,
        mock_metrics_collector: MagicMock,
    ) -> None:
        """Test most problematic tests panel."""
        signal = FlakyTestSignal(
            flaky_test_count=3,
            unstable_test_count=2,
            recovery_rate=0.75,
            most_problematic_tests=[
                {"name": "test_a", "failure_rate": 0.5},
            ],
            category_breakdown={},
        )
        provider = DashboardProvider(
            metrics_collector=mock_metrics_collector,
            health_checker=mock_health_checker,
            flaky_test_signal=signal,
        )

        panel = provider._panel_most_problematic_tests()

        assert panel.title == "Most Problematic Tests"
        assert len(panel.metrics) == 1


class TestDashboardHelperMethods:
    """Test dashboard helper status classification methods."""

    def test_get_error_rate_status_all_levels(self) -> None:
        """Test error rate status classification at all levels."""
        assert DashboardProvider._get_error_rate_status(0) == "HEALTHY"
        assert DashboardProvider._get_error_rate_status(0.5) == "NOMINAL"
        assert DashboardProvider._get_error_rate_status(2.5) == "DEGRADED"
        assert DashboardProvider._get_error_rate_status(10.0) == "CRITICAL"

    def test_get_latency_status_all_levels(self) -> None:
        """Test latency status classification at all levels."""
        assert DashboardProvider._get_latency_status(50) == "HEALTHY"
        assert DashboardProvider._get_latency_status(300) == "NOMINAL"
        assert DashboardProvider._get_latency_status(700) == "DEGRADED"
        assert DashboardProvider._get_latency_status(2000) == "CRITICAL"

    def test_get_flaky_test_status_all_levels(self) -> None:
        """Test flaky test status classification at all levels."""
        assert DashboardProvider._get_flaky_test_status(0) == "HEALTHY"
        assert DashboardProvider._get_flaky_test_status(3) == "NOMINAL"
        assert DashboardProvider._get_flaky_test_status(7) == "DEGRADED"
        assert DashboardProvider._get_flaky_test_status(15) == "CRITICAL"

    def test_get_coverage_health_status_all_levels(self) -> None:
        """Test coverage health status classification at all levels."""
        assert DashboardProvider._get_coverage_health_status(95.0) == "HEALTHY"
        assert DashboardProvider._get_coverage_health_status(85.0) == "NOMINAL"
        assert DashboardProvider._get_coverage_health_status(75.0) == "DEGRADED"
        assert DashboardProvider._get_coverage_health_status(60.0) == "CRITICAL"


class TestDashboardProviderInitialization:
    """Test DashboardProvider initialization with various configurations."""

    def test_provider_init_minimal(self) -> None:
        """Test DashboardProvider with minimal parameters."""
        mock_health = MagicMock(spec=HealthChecker)
        mock_metrics = MagicMock(spec=MetricsCollector)

        provider = DashboardProvider(
            metrics_collector=mock_metrics,
            health_checker=mock_health,
        )

        assert provider.metrics_collector is mock_metrics
        assert provider.health_checker is mock_health
        assert provider.coverage_snapshot is None

    def test_provider_init_with_coverage_data(self) -> None:
        """Test DashboardProvider with coverage data."""
        mock_health = MagicMock(spec=HealthChecker)
        mock_metrics = MagicMock(spec=MetricsCollector)
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(timezone.utc),
            run_id="test",
            source="test",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=80.0,
            overall_line_coverage_pct=87.0,
        )

        provider = DashboardProvider(
            metrics_collector=mock_metrics,
            health_checker=mock_health,
            coverage_snapshot=snapshot,
        )

        assert provider.coverage_snapshot is snapshot

    def test_provider_init_with_flaky_data(self) -> None:
        """Test DashboardProvider with flaky test signal."""
        mock_health = MagicMock(spec=HealthChecker)
        mock_metrics = MagicMock(spec=MetricsCollector)
        signal = FlakyTestSignal(
            flaky_test_count=1,
            unstable_test_count=0,
            recovery_rate=1.0,
            category_breakdown={},
            most_problematic_tests=[],
        )

        provider = DashboardProvider(
            metrics_collector=mock_metrics,
            health_checker=mock_health,
            flaky_test_signal=signal,
        )

        assert provider.flaky_test_signal is signal


class TestDashboardIntegration:
    """Integration tests for dashboard snapshot generation."""

    def test_full_snapshot_generation(self) -> None:
        """Test complete dashboard snapshot with all panels."""
        mock_health = MagicMock(spec=HealthChecker)
        health_report = MagicMock()
        health_report.critical_issues = []
        health_report.warnings = []
        health_report.overall_status.value = "HEALTHY"
        mock_health.run_all_checks.return_value = health_report

        mock_metrics = MagicMock(spec=MetricsCollector)
        system_metrics = MagicMock()
        system_metrics.total_collectors = 5
        system_metrics.healthy_collectors = 5
        system_metrics.degraded_collectors = 0
        system_metrics.critical_collectors = 0
        system_metrics.system_health_status = "HEALTHY"
        system_metrics.overall_error_rate_percent = 0.0
        system_metrics.total_validation_failures = 0
        system_metrics.collector_metrics = {}
        mock_metrics.get_system_metrics.return_value = system_metrics
        mock_metrics.get_all_collector_metrics.return_value = {}

        provider = DashboardProvider(
            metrics_collector=mock_metrics,
            health_checker=mock_health,
        )

        snapshot = provider.generate_snapshot()

        assert isinstance(snapshot, DashboardSnapshot)
        assert snapshot.system_status == "HEALTHY"
        assert len(snapshot.panels) > 0
        assert all(isinstance(p, DashboardPanel) for p in snapshot.panels)

    def test_snapshot_serialization(self) -> None:
        """Test that dashboard snapshots can be serialized to dict."""
        snapshot = DashboardSnapshot(
            timestamp=datetime.now(timezone.utc),
            system_status="HEALTHY",
            panels=[],
            alerts=[],
        )

        result = snapshot.to_dict()

        assert isinstance(result, dict)
        assert "timestamp" in result
        assert "system_status" in result
        assert "panels" in result
        assert "alerts" in result
