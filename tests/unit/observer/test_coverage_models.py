# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Comprehensive tests for coverage models and data structures."""

from __future__ import annotations

from datetime import UTC, datetime
import pytest

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageMetric,
    CoverageSnapshot,
    CoverageTrendAnalysis,
    FileCoverage,
    ModuleCoverage,
    compare_snapshots,
    get_baseline_coverage,
    is_snapshot_valid,
)


class TestCoverageMetric:
    """Tests for CoverageMetric dataclass and methods."""

    def test_coverage_metric_creation(self) -> None:
        """Test creating a basic coverage metric."""
        metric = CoverageMetric(
            scope="src/operations_center/observer",
            scope_type="module",
            timestamp=datetime.now(UTC),
            source="pytest-cov",
            statement_coverage_pct=85.5,
            branch_coverage_pct=75.2,
            line_coverage_pct=86.0,
        )
        assert metric.scope == "src/operations_center/observer"
        assert metric.scope_type == "module"
        assert metric.statement_coverage_pct == 85.5

    def test_coverage_metric_with_execution_counts(self) -> None:
        """Test metric with execution count details."""
        metric = CoverageMetric(
            scope="repo",
            scope_type="repository",
            timestamp=datetime.now(UTC),
            source="pytest-cov",
            statement_coverage_pct=80.0,
            branch_coverage_pct=70.0,
            line_coverage_pct=82.0,
            statement_count=1000,
            branch_count=500,
            executed_statements=800,
            executed_branches=350,
        )
        assert metric.statement_count == 1000
        assert metric.executed_statements == 800

    def test_get_coverage_by_type_statement(self) -> None:
        """Test getting statement coverage via get_coverage_by_type."""
        metric = CoverageMetric(
            scope="test",
            scope_type="file",
            timestamp=datetime.now(UTC),
            source="coverage.py",
            statement_coverage_pct=88.5,
            branch_coverage_pct=72.0,
            line_coverage_pct=89.0,
        )
        assert metric.get_coverage_by_type("statement") == 88.5

    def test_get_coverage_by_type_branch(self) -> None:
        """Test getting branch coverage via get_coverage_by_type."""
        metric = CoverageMetric(
            scope="test",
            scope_type="file",
            timestamp=datetime.now(UTC),
            source="coverage.py",
            statement_coverage_pct=88.5,
            branch_coverage_pct=72.0,
            line_coverage_pct=89.0,
        )
        assert metric.get_coverage_by_type("branch") == 72.0

    def test_get_coverage_by_type_line(self) -> None:
        """Test getting line coverage via get_coverage_by_type."""
        metric = CoverageMetric(
            scope="test",
            scope_type="file",
            timestamp=datetime.now(UTC),
            source="coverage.py",
            statement_coverage_pct=88.5,
            branch_coverage_pct=72.0,
            line_coverage_pct=89.0,
        )
        assert metric.get_coverage_by_type("line") == 89.0

    def test_get_execution_count_statement(self) -> None:
        """Test getting executed statement count."""
        metric = CoverageMetric(
            scope="test",
            scope_type="module",
            timestamp=datetime.now(UTC),
            source="pytest-cov",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            executed_statements=850,
            executed_branches=300,
            executed_lines=600,
        )
        assert metric.get_execution_count("statement") == 850

    def test_get_execution_count_branch(self) -> None:
        """Test getting executed branch count."""
        metric = CoverageMetric(
            scope="test",
            scope_type="module",
            timestamp=datetime.now(UTC),
            source="pytest-cov",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            executed_statements=850,
            executed_branches=300,
            executed_lines=600,
        )
        assert metric.get_execution_count("branch") == 300

    def test_get_execution_count_line(self) -> None:
        """Test getting executed line count."""
        metric = CoverageMetric(
            scope="test",
            scope_type="module",
            timestamp=datetime.now(UTC),
            source="pytest-cov",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            executed_statements=850,
            executed_branches=300,
            executed_lines=600,
        )
        assert metric.get_execution_count("line") == 600


class TestModuleCoverage:
    """Tests for ModuleCoverage dataclass and methods."""

    def test_module_coverage_creation(self) -> None:
        """Test creating a module coverage entry."""
        module = ModuleCoverage(
            module_path="src/operations_center/observer",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        assert module.module_path == "src/operations_center/observer"
        assert module.health_status == "healthy"

    def test_is_healthy_true(self) -> None:
        """Test is_healthy returns True for healthy status."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=80.0,
            branch_coverage_pct=70.0,
            line_coverage_pct=81.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        assert module.is_healthy() is True

    def test_is_healthy_false(self) -> None:
        """Test is_healthy returns False for non-healthy status."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=60.0,
            branch_coverage_pct=50.0,
            line_coverage_pct=61.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="critical",
        )
        assert module.is_healthy() is False

    def test_is_at_risk_true(self) -> None:
        """Test is_at_risk returns True for at_risk status."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=76.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="at_risk",
        )
        assert module.is_at_risk() is True

    def test_is_at_risk_false(self) -> None:
        """Test is_at_risk returns False for other statuses."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        assert module.is_at_risk() is False

    def test_is_critical_true(self) -> None:
        """Test is_critical returns True for critical status."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=40.0,
            branch_coverage_pct=30.0,
            line_coverage_pct=41.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="critical",
        )
        assert module.is_critical() is True

    def test_is_critical_false(self) -> None:
        """Test is_critical returns False for non-critical status."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        assert module.is_critical() is False

    def test_get_average_coverage(self) -> None:
        """Test calculating average coverage across metrics."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=90.0,
            branch_coverage_pct=80.0,
            line_coverage_pct=70.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        assert module.get_average_coverage() == pytest.approx(80.0, abs=0.1)

    def test_get_average_coverage_with_different_values(self) -> None:
        """Test average coverage with asymmetric values."""
        module = ModuleCoverage(
            module_path="src/test",
            statement_coverage_pct=60.0,
            branch_coverage_pct=50.0,
            line_coverage_pct=40.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="critical",
        )
        assert module.get_average_coverage() == pytest.approx(50.0, abs=0.1)


class TestFileCoverage:
    """Tests for FileCoverage dataclass and methods."""

    def test_file_coverage_creation(self) -> None:
        """Test creating a file coverage entry."""
        file_cov = FileCoverage(
            file_path="src/operations_center/observer/coverage_models.py",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
        )
        assert file_cov.file_path == "src/operations_center/observer/coverage_models.py"
        assert file_cov.statement_coverage_pct == 85.0

    def test_file_coverage_with_uncovered_lines(self) -> None:
        """Test file coverage with uncovered line ranges."""
        file_cov = FileCoverage(
            file_path="test.py",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=76.0,
            uncovered_lines=[(10, 15), (20, 25)],
        )
        assert len(file_cov.uncovered_lines) == 2
        assert file_cov.uncovered_lines[0] == (10, 15)

    def test_get_uncovered_line_count(self) -> None:
        """Test calculating total uncovered line count."""
        file_cov = FileCoverage(
            file_path="test.py",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=76.0,
            uncovered_lines=[(10, 15), (20, 25)],
        )
        assert file_cov.get_uncovered_line_count() == 10

    def test_get_uncovered_line_count_empty(self) -> None:
        """Test uncovered line count with no uncovered lines."""
        file_cov = FileCoverage(
            file_path="test.py",
            statement_coverage_pct=100.0,
            branch_coverage_pct=100.0,
            line_coverage_pct=100.0,
        )
        assert file_cov.get_uncovered_line_count() == 0

    def test_is_below_threshold_true(self) -> None:
        """Test is_below_threshold returns True when below."""
        file_cov = FileCoverage(
            file_path="test.py",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=70.0,
        )
        assert file_cov.is_below_threshold(75.0) is True

    def test_is_below_threshold_false(self) -> None:
        """Test is_below_threshold returns False when above."""
        file_cov = FileCoverage(
            file_path="test.py",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
        )
        assert file_cov.is_below_threshold(75.0) is False

    def test_is_below_threshold_equal(self) -> None:
        """Test is_below_threshold at exact threshold."""
        file_cov = FileCoverage(
            file_path="test.py",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=75.0,
        )
        assert file_cov.is_below_threshold(75.0) is False


class TestCoverageSnapshot:
    """Tests for CoverageSnapshot dataclass and methods."""

    def test_coverage_snapshot_creation(self) -> None:
        """Test creating a coverage snapshot."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test-run-123",
            source="pytest-cov",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=86.0,
        )
        assert snapshot.run_id == "test-run-123"
        assert snapshot.overall_statement_coverage_pct == 85.0

    def test_get_critical_modules(self) -> None:
        """Test getting critical modules from snapshot."""
        critical_module = ModuleCoverage(
            module_path="src/critical",
            statement_coverage_pct=40.0,
            branch_coverage_pct=30.0,
            line_coverage_pct=41.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="critical",
        )
        healthy_module = ModuleCoverage(
            module_path="src/healthy",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test",
            source="pytest-cov",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=70.0,
            overall_line_coverage_pct=81.0,
            module_coverages=[critical_module, healthy_module],
        )
        critical = snapshot.get_critical_modules()
        assert len(critical) == 1
        assert critical[0].module_path == "src/critical"

    def test_get_at_risk_modules(self) -> None:
        """Test getting at-risk modules from snapshot."""
        at_risk_module = ModuleCoverage(
            module_path="src/at_risk",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=76.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="at_risk",
        )
        healthy_module = ModuleCoverage(
            module_path="src/healthy",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test",
            source="pytest-cov",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=70.0,
            overall_line_coverage_pct=81.0,
            module_coverages=[at_risk_module, healthy_module],
        )
        at_risk = snapshot.get_at_risk_modules()
        assert len(at_risk) == 1
        assert at_risk[0].module_path == "src/at_risk"

    def test_get_files_below_threshold(self) -> None:
        """Test getting files below threshold from snapshot."""
        below_threshold_file = FileCoverage(
            file_path="low_coverage.py",
            statement_coverage_pct=60.0,
            branch_coverage_pct=50.0,
            line_coverage_pct=61.0,
        )
        above_threshold_file = FileCoverage(
            file_path="good_coverage.py",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
        )
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test",
            source="pytest-cov",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=70.0,
            overall_line_coverage_pct=81.0,
            file_coverages=[below_threshold_file, above_threshold_file],
        )
        below_threshold = snapshot.get_files_below_threshold(75.0)
        assert len(below_threshold) == 1
        assert below_threshold[0].file_path == "low_coverage.py"


class TestCoverageTrendAnalysis:
    """Tests for CoverageTrendAnalysis dataclass and methods."""

    def test_trend_analysis_creation(self) -> None:
        """Test creating a trend analysis."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=85.0,
            average_value=82.0,
            min_value=78.0,
            max_value=88.0,
            trend_direction="improving",
            trend_pct=2.5,
        )
        assert trend.metric_type == "statement"
        assert trend.current_value == 85.0

    def test_is_improving_true(self) -> None:
        """Test is_improving returns True for improving trend."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=85.0,
            average_value=82.0,
            min_value=78.0,
            max_value=88.0,
            trend_direction="improving",
            trend_pct=2.5,
        )
        assert trend.is_improving() is True

    def test_is_improving_false(self) -> None:
        """Test is_improving returns False for non-improving trend."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=82.0,
            average_value=84.0,
            min_value=78.0,
            max_value=88.0,
            trend_direction="degrading",
            trend_pct=-2.0,
        )
        assert trend.is_improving() is False

    def test_is_degrading_true(self) -> None:
        """Test is_degrading returns True for degrading trend."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=82.0,
            average_value=84.0,
            min_value=78.0,
            max_value=88.0,
            trend_direction="degrading",
            trend_pct=-2.0,
        )
        assert trend.is_degrading() is True

    def test_is_degrading_false(self) -> None:
        """Test is_degrading returns False for non-degrading trend."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=85.0,
            average_value=85.0,
            min_value=84.0,
            max_value=86.0,
            trend_direction="stable",
            trend_pct=0.0,
        )
        assert trend.is_degrading() is False

    def test_is_stable_true(self) -> None:
        """Test is_stable returns True for stable trend."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=85.0,
            average_value=85.0,
            min_value=84.0,
            max_value=86.0,
            trend_direction="stable",
            trend_pct=0.0,
        )
        assert trend.is_stable() is True

    def test_is_stable_false(self) -> None:
        """Test is_stable returns False for non-stable trend."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            current_value=85.0,
            average_value=82.0,
            min_value=78.0,
            max_value=88.0,
            trend_direction="improving",
            trend_pct=2.5,
        )
        assert trend.is_stable() is False

    def test_get_total_change_with_measurements(self) -> None:
        """Test calculating total change from measurements."""
        start_time = datetime.now(UTC)
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=start_time,
            window_end=datetime.now(UTC),
            measurements=[(start_time, 80.0), (datetime.now(UTC), 85.0)],
            current_value=85.0,
            average_value=82.5,
            min_value=80.0,
            max_value=85.0,
            trend_direction="improving",
            trend_pct=5.0,
        )
        assert trend.get_total_change() == pytest.approx(5.0, abs=0.1)

    def test_get_total_change_empty_measurements(self) -> None:
        """Test total change with no measurements."""
        trend = CoverageTrendAnalysis(
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            window_start=datetime.now(UTC),
            window_end=datetime.now(UTC),
            measurements=[],
            current_value=85.0,
            average_value=82.0,
            min_value=78.0,
            max_value=88.0,
            trend_direction="improving",
            trend_pct=2.5,
        )
        assert trend.get_total_change() == 0.0


class TestCoverageAlert:
    """Tests for CoverageAlert dataclass and methods."""

    def test_coverage_alert_creation(self) -> None:
        """Test creating a coverage alert."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.alert_id == "alert-001"
        assert alert.severity == "warning"

    def test_is_critical_true(self) -> None:
        """Test is_critical returns True for critical severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="critical",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=50.0,
            threshold_or_baseline=75.0,
            delta_pct=-25.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_critical() is True

    def test_is_critical_true_emergency(self) -> None:
        """Test is_critical returns True for emergency severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="emergency",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=30.0,
            threshold_or_baseline=75.0,
            delta_pct=-45.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_critical() is True

    def test_is_critical_false(self) -> None:
        """Test is_critical returns False for non-critical severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="info",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=85.0,
            threshold_or_baseline=75.0,
            delta_pct=10.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_critical() is False

    def test_is_acknowledged_true(self) -> None:
        """Test is_acknowledged returns True when acknowledged."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
            acknowledged=True,
            acknowledged_by="dev-team",
        )
        assert alert.is_acknowledged() is True

    def test_is_acknowledged_false(self) -> None:
        """Test is_acknowledged returns False when not acknowledged."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
            acknowledged=False,
        )
        assert alert.is_acknowledged() is False

    def test_is_dismissed_true(self) -> None:
        """Test is_dismissed returns True when dismissed."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
            dismissal_reason="temporary coverage gap",
        )
        assert alert.is_dismissed() is True

    def test_is_dismissed_false(self) -> None:
        """Test is_dismissed returns False when not dismissed."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_dismissed() is False

    def test_get_severity_level_info(self) -> None:
        """Test severity level for info."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="info",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=85.0,
            threshold_or_baseline=75.0,
            delta_pct=10.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_severity_level() == 0

    def test_get_severity_level_warning(self) -> None:
        """Test severity level for warning."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_severity_level() == 1

    def test_get_severity_level_critical(self) -> None:
        """Test severity level for critical."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="critical",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=50.0,
            threshold_or_baseline=75.0,
            delta_pct=-25.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_severity_level() == 2

    def test_get_severity_level_emergency(self) -> None:
        """Test severity level for emergency."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="emergency",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=30.0,
            threshold_or_baseline=75.0,
            delta_pct=-45.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_severity_level() == 3

    def test_exceeds_threshold_true(self) -> None:
        """Test exceeds_threshold returns True when current exceeds baseline."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="regression_detected",
            severity="info",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=80.0,
            threshold_or_baseline=75.0,
            delta_pct=5.0,
            baseline_type="previous_run",
        )
        assert alert.exceeds_threshold() is True

    def test_exceeds_threshold_false(self) -> None:
        """Test exceeds_threshold returns False when below baseline."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.exceeds_threshold() is False

    def test_exceeds_threshold_none(self) -> None:
        """Test exceeds_threshold with None baseline."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=None,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.exceeds_threshold() is False

    def test_is_below_target_true(self) -> None:
        """Test is_below_target returns True when below target."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=85.0,
            threshold_or_baseline=75.0,
            delta_pct=10.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_below_target(90.0) is True

    def test_is_below_target_false(self) -> None:
        """Test is_below_target returns False when above target."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="info",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=95.0,
            threshold_or_baseline=75.0,
            delta_pct=20.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_below_target(90.0) is False

    def test_is_below_target_default(self) -> None:
        """Test is_below_target with default target."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=85.0,
            threshold_or_baseline=75.0,
            delta_pct=10.0,
            baseline_type="minimum_threshold",
        )
        assert alert.is_below_target() is True

    def test_get_alert_emoji_info(self) -> None:
        """Test emoji for info severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="info",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=85.0,
            threshold_or_baseline=75.0,
            delta_pct=10.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_alert_emoji() == "ℹ️"

    def test_get_alert_emoji_warning(self) -> None:
        """Test emoji for warning severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_alert_emoji() == "⚠️"

    def test_get_alert_emoji_critical(self) -> None:
        """Test emoji for critical severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="critical",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=50.0,
            threshold_or_baseline=75.0,
            delta_pct=-25.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_alert_emoji() == "🚨"

    def test_get_alert_emoji_emergency(self) -> None:
        """Test emoji for emergency severity."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="emergency",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=30.0,
            threshold_or_baseline=75.0,
            delta_pct=-45.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_alert_emoji() == "🚨🚨"

    def test_get_alert_type_label_below_threshold(self) -> None:
        """Test label for below_threshold alert type."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="below_threshold",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=70.0,
            threshold_or_baseline=75.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )
        assert alert.get_alert_type_label() == "Below Threshold"

    def test_get_alert_type_label_regression(self) -> None:
        """Test label for regression_detected alert type."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="regression_detected",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=80.0,
            threshold_or_baseline=85.0,
            delta_pct=-5.0,
            baseline_type="previous_run",
        )
        assert alert.get_alert_type_label() == "Regression Detected"

    def test_get_alert_type_label_trend(self) -> None:
        """Test label for trend_degrading alert type."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="trend_degrading",
            severity="warning",
            metric_type="statement",
            granularity="repository",
            scope_id="repo",
            current_value=82.0,
            threshold_or_baseline=84.0,
            delta_pct=-2.0,
            baseline_type="7day_avg",
        )
        assert alert.get_alert_type_label() == "Trend Degrading"

    def test_get_alert_type_label_module_gap(self) -> None:
        """Test label for critical_module_coverage alert type."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(UTC),
            alert_type="critical_module_coverage",
            severity="critical",
            metric_type="statement",
            granularity="module",
            scope_id="src/critical",
            current_value=50.0,
            threshold_or_baseline=75.0,
            delta_pct=-25.0,
            baseline_type="minimum_threshold",
            affected_modules=["src/critical"],
        )
        assert alert.get_alert_type_label() == "Module Coverage Gap"


class TestModuleFunctions:
    """Tests for module-level functions in coverage_models."""

    def test_compare_snapshots_improvement(self) -> None:
        """Test comparing two snapshots with improvement."""
        prev_snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="run-1",
            source="pytest-cov",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=70.0,
            overall_line_coverage_pct=81.0,
        )
        curr_snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="run-2",
            source="pytest-cov",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=86.0,
        )
        deltas = compare_snapshots(curr_snapshot, prev_snapshot)
        assert deltas["statement_delta"] == 5.0
        assert deltas["branch_delta"] == 5.0
        assert deltas["line_delta"] == 5.0

    def test_compare_snapshots_regression(self) -> None:
        """Test comparing two snapshots with regression."""
        prev_snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="run-1",
            source="pytest-cov",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=86.0,
        )
        curr_snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="run-2",
            source="pytest-cov",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=70.0,
            overall_line_coverage_pct=81.0,
        )
        deltas = compare_snapshots(curr_snapshot, prev_snapshot)
        assert deltas["statement_delta"] == -5.0
        assert deltas["branch_delta"] == -5.0
        assert deltas["line_delta"] == -5.0

    def test_is_snapshot_valid_true(self) -> None:
        """Test snapshot validation returns True for valid snapshot."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test-run",
            source="pytest-cov",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=86.0,
        )
        assert is_snapshot_valid(snapshot) is True

    def test_is_snapshot_valid_boundary_zero(self) -> None:
        """Test snapshot validation with 0% coverage."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test-run",
            source="pytest-cov",
            overall_statement_coverage_pct=0.0,
            overall_branch_coverage_pct=0.0,
            overall_line_coverage_pct=0.0,
        )
        assert is_snapshot_valid(snapshot) is True

    def test_is_snapshot_valid_boundary_hundred(self) -> None:
        """Test snapshot validation with 100% coverage."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test-run",
            source="pytest-cov",
            overall_statement_coverage_pct=100.0,
            overall_branch_coverage_pct=100.0,
            overall_line_coverage_pct=100.0,
        )
        assert is_snapshot_valid(snapshot) is True

    def test_get_baseline_coverage_minimum_threshold(self) -> None:
        """Test getting baseline for minimum_threshold type."""
        baseline = get_baseline_coverage("minimum_threshold", 80.0, 75.0)
        assert baseline == 75.0

    def test_get_baseline_coverage_trend(self) -> None:
        """Test getting baseline for trend type."""
        baseline = get_baseline_coverage("trend", 80.0, 75.0)
        assert baseline == 75.0

    def test_get_baseline_coverage_other_types(self) -> None:
        """Test getting baseline for other baseline types."""
        baseline = get_baseline_coverage("previous_run", 80.0, 75.0)
        assert baseline == 80.0

    def test_get_baseline_coverage_7day_avg(self) -> None:
        """Test getting baseline for 7day_avg type."""
        baseline = get_baseline_coverage("7day_avg", 82.0, 75.0)
        assert baseline == 82.0
