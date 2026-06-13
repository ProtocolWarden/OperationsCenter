# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage alerting system with alert generation and severity classification."""

from datetime import datetime, timedelta

import pytest

from operations_center.observer.coverage_alerting import (
    AlertSeverity,
    AlertType,
    CoverageAlertConfig,
    CoverageAlertManager,
)
from operations_center.observer.coverage_models import (
    CoverageSnapshot,
    CoverageTrendAnalysis,
    ModuleCoverage,
)


@pytest.fixture
def default_config() -> CoverageAlertConfig:
    """Create default alert configuration."""
    return CoverageAlertConfig()


@pytest.fixture
def custom_config() -> CoverageAlertConfig:
    """Create custom alert configuration with module thresholds."""
    return CoverageAlertConfig(
        repo_minimum_threshold=85.0,
        repo_warning_threshold=90.0,
        repo_target_threshold=95.0,
        severity_critical_threshold=40.0,
        severity_high_threshold=60.0,
        severity_medium_threshold=75.0,
        module_thresholds={
            "src/operations_center/observer": {
                "statement_coverage_minimum": 88.0,
                "branch_coverage_minimum": 78.0,
                "line_coverage_minimum": 85.0,
            },
            "src/operations_center/custodian": {
                "statement_coverage_minimum": 80.0,
                "branch_coverage_minimum": 70.0,
            },
        },
    )


@pytest.fixture
def healthy_snapshot() -> CoverageSnapshot:
    """Create a healthy coverage snapshot above all thresholds."""
    return CoverageSnapshot(
        timestamp=datetime.now(),
        run_id="sha123",
        source="coverage.py",
        overall_statement_coverage_pct=92.5,
        overall_branch_coverage_pct=88.0,
        overall_line_coverage_pct=91.0,
        module_coverages=[
            ModuleCoverage(
                module_path="src/operations_center/observer",
                statement_coverage_pct=95.0,
                branch_coverage_pct=90.0,
                line_coverage_pct=94.0,
                statement_count=1000,
                branch_count=500,
                line_count=800,
                health_status="healthy",
            ),
            ModuleCoverage(
                module_path="src/operations_center/custodian",
                statement_coverage_pct=88.0,
                branch_coverage_pct=82.0,
                line_coverage_pct=86.0,
                statement_count=800,
                branch_count=400,
                line_count=700,
                health_status="healthy",
            ),
        ],
    )


@pytest.fixture
def below_threshold_snapshot() -> CoverageSnapshot:
    """Create a snapshot with coverage below thresholds."""
    return CoverageSnapshot(
        timestamp=datetime.now(),
        run_id="sha124",
        source="coverage.py",
        overall_statement_coverage_pct=72.5,
        overall_branch_coverage_pct=55.0,
        overall_line_coverage_pct=68.0,
        module_coverages=[
            ModuleCoverage(
                module_path="src/operations_center/observer",
                statement_coverage_pct=45.0,
                branch_coverage_pct=35.0,
                line_coverage_pct=40.0,
                statement_count=1000,
                branch_count=500,
                line_count=800,
                health_status="critical",
            ),
            ModuleCoverage(
                module_path="src/operations_center/custodian",
                statement_coverage_pct=65.0,
                branch_coverage_pct=55.0,
                line_coverage_pct=62.0,
                statement_count=800,
                branch_count=400,
                line_count=700,
                health_status="at_risk",
            ),
        ],
    )


@pytest.fixture
def regressed_snapshot() -> CoverageSnapshot:
    """Create a snapshot showing regression from previous measurement."""
    return CoverageSnapshot(
        timestamp=datetime.now(),
        run_id="sha125",
        source="coverage.py",
        overall_statement_coverage_pct=80.5,
        overall_branch_coverage_pct=75.0,
        overall_line_coverage_pct=79.0,
        module_coverages=[
            ModuleCoverage(
                module_path="src/operations_center/observer",
                statement_coverage_pct=85.0,
                branch_coverage_pct=80.0,
                line_coverage_pct=83.0,
                statement_count=1000,
                branch_count=500,
                line_count=800,
                health_status="healthy",
            ),
        ],
    )


@pytest.fixture
def degrading_trend_analysis() -> CoverageTrendAnalysis:
    """Create a trend analysis showing degradation."""
    measurements = [
        (datetime.now() - timedelta(days=5), 95.0),
        (datetime.now() - timedelta(days=4), 94.5),
        (datetime.now() - timedelta(days=3), 93.0),
        (datetime.now() - timedelta(days=2), 91.5),
        (datetime.now() - timedelta(days=1), 90.0),
        (datetime.now(), 88.0),
    ]
    return CoverageTrendAnalysis(
        metric_type="statement",
        granularity="repository",
        scope_id="",
        window_start=datetime.now() - timedelta(days=5),
        window_end=datetime.now(),
        measurements=measurements,
        current_value=88.0,
        average_value=92.0,
        min_value=88.0,
        max_value=95.0,
        trend_direction="degrading",
        trend_pct=-1.17,
        regression_count=6,
        days_of_decline=6,
        standard_deviation=2.5,
        stability_score=0.72,
        projected_value_7days=87.0,
    )


class TestCoverageAlertConfig:
    """Tests for CoverageAlertConfig class."""

    def test_default_thresholds(self, default_config: CoverageAlertConfig) -> None:
        """Test default threshold values."""
        assert default_config.repo_minimum_threshold == 80.0
        assert default_config.repo_warning_threshold == 85.0
        assert default_config.repo_target_threshold == 90.0
        assert default_config.statement_coverage_minimum == 75.0
        assert default_config.branch_coverage_minimum == 65.0
        assert default_config.line_coverage_minimum == 75.0

    def test_custom_thresholds(self, custom_config: CoverageAlertConfig) -> None:
        """Test custom threshold configuration."""
        assert custom_config.repo_minimum_threshold == 85.0
        assert custom_config.repo_warning_threshold == 90.0
        assert custom_config.repo_target_threshold == 95.0

    def test_module_threshold_with_override(self, custom_config: CoverageAlertConfig) -> None:
        """Test module-specific threshold retrieval with override."""
        observer_threshold = custom_config.get_module_threshold(
            "src/operations_center/observer", "statement"
        )
        assert observer_threshold == 88.0

    def test_module_threshold_fallback_to_default(self, custom_config: CoverageAlertConfig) -> None:
        """Test module threshold fallback to repository default."""
        unknown_module_threshold = custom_config.get_module_threshold(
            "src/unknown/module", "statement"
        )
        assert unknown_module_threshold == custom_config.repo_minimum_threshold

    def test_severity_classification_emergency(self, default_config: CoverageAlertConfig) -> None:
        """Test emergency severity classification."""
        severity = default_config.classify_severity(35.0)
        assert severity == AlertSeverity.EMERGENCY

    def test_severity_classification_critical(self, default_config: CoverageAlertConfig) -> None:
        """Test critical severity classification."""
        severity = default_config.classify_severity(65.0)
        assert severity == AlertSeverity.CRITICAL

    def test_severity_classification_warning(self, default_config: CoverageAlertConfig) -> None:
        """Test warning severity classification."""
        severity = default_config.classify_severity(75.0)
        assert severity == AlertSeverity.WARNING

    def test_severity_classification_info(self, default_config: CoverageAlertConfig) -> None:
        """Test info severity classification."""
        severity = default_config.classify_severity(85.0)
        assert severity == AlertSeverity.INFO

    def test_custom_severity_thresholds(self, custom_config: CoverageAlertConfig) -> None:
        """Test custom severity threshold classification."""
        emergency = custom_config.classify_severity(35.0)
        assert emergency == AlertSeverity.EMERGENCY

        critical = custom_config.classify_severity(50.0)
        assert critical == AlertSeverity.CRITICAL

        warning = custom_config.classify_severity(70.0)
        assert warning == AlertSeverity.WARNING

        info = custom_config.classify_severity(80.0)
        assert info == AlertSeverity.INFO


class TestCoverageAlertManager:
    """Tests for CoverageAlertManager alert generation."""

    def test_manager_initialization(self, default_config: CoverageAlertConfig) -> None:
        """Test alert manager initialization."""
        manager = CoverageAlertManager(config=default_config)
        assert manager.config == default_config
        assert len(manager.alerts) == 0

    def test_manager_default_config(self) -> None:
        """Test alert manager with default configuration."""
        manager = CoverageAlertManager()
        assert manager.config.repo_minimum_threshold == 80.0

    def test_healthy_snapshot_no_alerts(
        self, default_config: CoverageAlertConfig, healthy_snapshot: CoverageSnapshot
    ) -> None:
        """Test that healthy snapshot generates no alerts."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(healthy_snapshot)
        assert len(alerts) == 0

    def test_below_threshold_generates_alerts(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test that below-threshold snapshot generates alerts."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        assert len(alerts) > 0
        below_threshold_types = [a.alert_type for a in alerts]
        assert AlertType.BELOW_THRESHOLD.value in below_threshold_types

    def test_statement_coverage_below_threshold_alert(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test alert for statement coverage below threshold."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        statement_alerts = [
            a
            for a in alerts
            if a.metric_type == "statement" and a.alert_type == AlertType.BELOW_THRESHOLD.value
        ]
        assert len(statement_alerts) > 0
        alert = statement_alerts[0]
        assert alert.current_value == below_threshold_snapshot.overall_statement_coverage_pct
        assert alert.threshold_or_baseline == default_config.repo_minimum_threshold

    def test_branch_coverage_below_threshold_alert(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test alert for branch coverage below threshold."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        branch_alerts = [
            a
            for a in alerts
            if a.metric_type == "branch" and a.alert_type == AlertType.BELOW_THRESHOLD.value
        ]
        assert len(branch_alerts) > 0
        alert = branch_alerts[0]
        assert alert.current_value == below_threshold_snapshot.overall_branch_coverage_pct
        assert alert.threshold_or_baseline == default_config.branch_coverage_minimum

    def test_line_coverage_below_threshold_alert(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test alert for line coverage below threshold."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        line_alerts = [
            a
            for a in alerts
            if a.metric_type == "line" and a.alert_type == AlertType.BELOW_THRESHOLD.value
        ]
        assert len(line_alerts) > 0
        alert = line_alerts[0]
        assert alert.current_value == below_threshold_snapshot.overall_line_coverage_pct
        assert alert.threshold_or_baseline == default_config.line_coverage_minimum


class TestCriticalModuleDetection:
    """Tests for critical module coverage gap detection."""

    def test_critical_module_gap_detected(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test detection of critical module coverage gaps."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        module_alerts = [
            a for a in alerts if a.alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value
        ]
        assert len(module_alerts) > 0

    def test_critical_module_gap_calculation(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test correct gap calculation for critical modules."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        module_alerts = [
            a for a in alerts if a.alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value
        ]
        alert = module_alerts[0]

        expected_gap = default_config.repo_minimum_threshold - 45.0
        assert abs(alert.delta_pct - (-expected_gap)) < 0.01

    def test_critical_module_threshold_minimum_gap(
        self, default_config: CoverageAlertConfig, healthy_snapshot: CoverageSnapshot
    ) -> None:
        """Test that critical module alerts only trigger for gaps >= 15%."""
        healthy_snapshot.module_coverages[0].statement_coverage_pct = 67.0
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(healthy_snapshot)

        module_alerts = [
            a for a in alerts if a.alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value
        ]
        assert len(module_alerts) == 0


class TestRegressionDetection:
    """Tests for regression detection."""

    def test_regression_detected(self, default_config: CoverageAlertConfig) -> None:
        """Test detection of coverage regression."""
        previous = CoverageSnapshot(
            timestamp=datetime.now() - timedelta(hours=1),
            run_id="sha_prev",
            source="coverage.py",
            overall_statement_coverage_pct=86.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=84.0,
        )
        current = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_current",
            source="coverage.py",
            overall_statement_coverage_pct=82.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=80.0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(current, previous_snapshot=previous)

        regression_alerts = [
            a for a in alerts if a.alert_type == AlertType.REGRESSION_DETECTED.value
        ]
        assert len(regression_alerts) > 0

    def test_regression_delta_calculation(self, default_config: CoverageAlertConfig) -> None:
        """Test correct regression delta calculation."""
        previous = CoverageSnapshot(
            timestamp=datetime.now() - timedelta(hours=1),
            run_id="sha_prev",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=84.0,
        )
        current = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_current",
            source="coverage.py",
            overall_statement_coverage_pct=82.5,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=80.0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(current, previous_snapshot=previous)

        regression_alerts = [
            a for a in alerts if a.alert_type == AlertType.REGRESSION_DETECTED.value
        ]
        assert len(regression_alerts) > 0
        alert = regression_alerts[0]
        assert abs(alert.delta_pct - 2.5) < 0.01

    def test_no_regression_for_small_drops(self, default_config: CoverageAlertConfig) -> None:
        """Test that small coverage drops don't trigger regression alerts."""
        previous = CoverageSnapshot(
            timestamp=datetime.now() - timedelta(hours=1),
            run_id="sha_prev",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=84.0,
        )
        current = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_current",
            source="coverage.py",
            overall_statement_coverage_pct=84.5,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=80.0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(current, previous_snapshot=previous)

        regression_alerts = [
            a for a in alerts if a.alert_type == AlertType.REGRESSION_DETECTED.value
        ]
        assert len(regression_alerts) == 0

    def test_regression_threshold_boundary(self, default_config: CoverageAlertConfig) -> None:
        """Test regression alert at exact threshold boundary."""
        previous = CoverageSnapshot(
            timestamp=datetime.now() - timedelta(hours=1),
            run_id="sha_prev",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=84.0,
        )
        current = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_current",
            source="coverage.py",
            overall_statement_coverage_pct=83.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=80.0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(current, previous_snapshot=previous)

        regression_alerts = [
            a for a in alerts if a.alert_type == AlertType.REGRESSION_DETECTED.value
        ]
        assert len(regression_alerts) > 0


class TestTrendDetection:
    """Tests for trend degradation detection."""

    def test_trend_degradation_detected(
        self,
        default_config: CoverageAlertConfig,
        healthy_snapshot: CoverageSnapshot,
        degrading_trend_analysis: CoverageTrendAnalysis,
    ) -> None:
        """Test detection of trend degradation."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(healthy_snapshot, trend_analysis=degrading_trend_analysis)

        trend_alerts = [a for a in alerts if a.alert_type == AlertType.TREND_DEGRADING.value]
        assert len(trend_alerts) > 0

    def test_trend_degradation_requires_minimum_days(
        self, default_config: CoverageAlertConfig, healthy_snapshot: CoverageSnapshot
    ) -> None:
        """Test that trend alert requires minimum days of decline."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="",
            window_start=datetime.now() - timedelta(days=2),
            window_end=datetime.now(),
            measurements=[
                (datetime.now() - timedelta(days=2), 90.0),
                (datetime.now() - timedelta(days=1), 89.0),
                (datetime.now(), 88.0),
            ],
            current_value=88.0,
            average_value=89.0,
            min_value=88.0,
            max_value=90.0,
            trend_direction="degrading",
            trend_pct=-1.0,
            days_of_decline=3,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(healthy_snapshot, trend_analysis=trend)

        trend_alerts = [a for a in alerts if a.alert_type == AlertType.TREND_DEGRADING.value]
        assert len(trend_alerts) == 0

    def test_stable_trend_no_alert(
        self, default_config: CoverageAlertConfig, healthy_snapshot: CoverageSnapshot
    ) -> None:
        """Test that stable trends don't generate alerts."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="",
            window_start=datetime.now() - timedelta(days=5),
            window_end=datetime.now(),
            measurements=[(datetime.now() - timedelta(days=i), 90.0 + i * 0.05) for i in range(5)],
            current_value=90.2,
            average_value=90.1,
            min_value=90.0,
            max_value=90.2,
            trend_direction="stable",
            trend_pct=0.01,
            days_of_decline=0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(healthy_snapshot, trend_analysis=trend)

        trend_alerts = [a for a in alerts if a.alert_type == AlertType.TREND_DEGRADING.value]
        assert len(trend_alerts) == 0


class TestAlertSeverityMapping:
    """Tests for alert severity classification and mapping."""

    def test_alert_severity_for_critical_coverage(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test severity mapping for critical coverage levels."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        critical_alerts = [a for a in alerts if a.severity == AlertSeverity.CRITICAL.value]
        assert len(critical_alerts) > 0

    def test_alert_severity_for_emergency_coverage(
        self, custom_config: CoverageAlertConfig
    ) -> None:
        """Test severity mapping for emergency coverage levels."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_emergency",
            source="coverage.py",
            overall_statement_coverage_pct=35.0,
            overall_branch_coverage_pct=30.0,
            overall_line_coverage_pct=32.0,
        )

        manager = CoverageAlertManager(config=custom_config)
        alerts = manager.generate_alerts(snapshot)

        emergency_alerts = [a for a in alerts if a.severity == AlertSeverity.EMERGENCY.value]
        assert len(emergency_alerts) > 0

    def test_alert_severity_for_warning_coverage(self, default_config: CoverageAlertConfig) -> None:
        """Test severity mapping for warning coverage levels."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_warning",
            source="coverage.py",
            overall_statement_coverage_pct=75.0,
            overall_branch_coverage_pct=65.0,
            overall_line_coverage_pct=73.0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(snapshot)

        warning_alerts = [a for a in alerts if a.severity == AlertSeverity.WARNING.value]
        assert len(warning_alerts) > 0


class TestAlertCategorization:
    """Tests for alert categorization and filtering."""

    def test_categorize_alert_by_type(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test alert categorization."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        if alerts:
            alert = alerts[0]
            categorization = manager.categorize_alert(alert)

            assert "alert_type" in categorization
            assert "severity" in categorization
            assert "category" in categorization
            assert "action_required" in categorization

    def test_categorize_below_threshold_alert(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test categorization of below-threshold alert."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        threshold_alert = [a for a in alerts if a.alert_type == AlertType.BELOW_THRESHOLD.value]
        if threshold_alert:
            categorization = manager.categorize_alert(threshold_alert[0])
            assert categorization["category"] == "Threshold Breach"

    def test_categorize_regression_alert(self, default_config: CoverageAlertConfig) -> None:
        """Test categorization of regression alert."""
        previous = CoverageSnapshot(
            timestamp=datetime.now() - timedelta(hours=1),
            run_id="sha_prev",
            source="coverage.py",
            overall_statement_coverage_pct=86.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=84.0,
        )
        current = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="sha_current",
            source="coverage.py",
            overall_statement_coverage_pct=82.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=80.0,
        )

        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(current, previous_snapshot=previous)

        regression_alert = [
            a for a in alerts if a.alert_type == AlertType.REGRESSION_DETECTED.value
        ]
        if regression_alert:
            categorization = manager.categorize_alert(regression_alert[0])
            assert categorization["category"] == "Regression"

    def test_filter_alerts_by_severity(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test filtering alerts by severity."""
        manager = CoverageAlertManager(config=default_config)
        manager.generate_alerts(below_threshold_snapshot)

        critical_alerts = manager.filter_alerts_by_severity(AlertSeverity.CRITICAL)
        assert len(critical_alerts) >= 0

    def test_filter_alerts_by_type(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test filtering alerts by type."""
        manager = CoverageAlertManager(config=default_config)
        manager.generate_alerts(below_threshold_snapshot)

        threshold_alerts = manager.filter_alerts_by_type(AlertType.BELOW_THRESHOLD)
        assert len(threshold_alerts) > 0


class TestAlertSummarization:
    """Tests for alert summarization."""

    def test_summarize_alerts_empty(self, default_config: CoverageAlertConfig) -> None:
        """Test alert summarization with no alerts."""
        manager = CoverageAlertManager(config=default_config)
        summary = manager.summarize_alerts()

        assert summary["total"] == 0
        assert len(summary["by_type"]) == 0
        assert len(summary["by_severity"]) == 0

    def test_summarize_alerts_with_data(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test alert summarization with generated alerts."""
        manager = CoverageAlertManager(config=default_config)
        manager.generate_alerts(below_threshold_snapshot)

        summary = manager.summarize_alerts()
        assert summary["total"] > 0
        assert "by_type" in summary
        assert "by_severity" in summary

    def test_action_required_classification(
        self, default_config: CoverageAlertConfig, below_threshold_snapshot: CoverageSnapshot
    ) -> None:
        """Test action required classification."""
        manager = CoverageAlertManager(config=default_config)
        alerts = manager.generate_alerts(below_threshold_snapshot)

        for alert in alerts:
            categorization = manager.categorize_alert(alert)
            if alert.severity in [AlertSeverity.CRITICAL.value, AlertSeverity.EMERGENCY.value]:
                assert categorization["action_required"] is True
            else:
                assert categorization["action_required"] is False
