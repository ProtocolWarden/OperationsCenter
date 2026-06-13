# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage trend manager storage and analysis."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
    ModuleCoverage,
)
from operations_center.observer.coverage_trend_manager import (
    CoverageTrendManager,
    calculate_measurements_average,
)
from operations_center.observer.coverage_trend_repository import (
    LocalCoverageTrendRepository,
)


@pytest.fixture
def temp_storage_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for storage."""
    storage_dir = tmp_path / "coverage_data"
    storage_dir.mkdir(parents=True)
    yield storage_dir
    if storage_dir.exists():
        shutil.rmtree(storage_dir)


@pytest.fixture
def manager(temp_storage_dir: Path) -> CoverageTrendManager:
    """Create a coverage trend manager with local storage."""
    return CoverageTrendManager.create_local(root=temp_storage_dir)


@pytest.fixture
def sample_snapshots() -> list[CoverageSnapshot]:
    """Create sample snapshots for trend analysis."""
    snapshots = []
    base_time = datetime.now(tz=timezone.utc) - timedelta(days=7)

    for i in range(7):
        timestamp = base_time + timedelta(days=i)
        coverage = 85.0 + (i * 0.3)  # Slight upward trend

        snapshot = CoverageSnapshot(
            timestamp=timestamp,
            run_id=f"run-{i:03d}",
            source="coverage.py",
            overall_statement_coverage_pct=coverage - 1.0,
            overall_branch_coverage_pct=coverage - 5.0,
            overall_line_coverage_pct=coverage,
            test_execution_time_ms=5000 + (i * 100),
            test_count=150 + (i * 10),
            module_coverages=[
                ModuleCoverage(
                    module_path="src/observer",
                    statement_coverage_pct=coverage + 2.0,
                    branch_coverage_pct=coverage - 3.0,
                    line_coverage_pct=coverage + 1.0,
                    statement_count=1000,
                    branch_count=500,
                    line_count=900,
                    health_status="healthy",
                ),
                ModuleCoverage(
                    module_path="src/custodian",
                    statement_coverage_pct=coverage - 5.0,
                    branch_coverage_pct=coverage - 8.0,
                    line_coverage_pct=coverage - 3.0,
                    statement_count=500,
                    branch_count=250,
                    line_count=450,
                    health_status="at_risk",
                ),
            ],
        )
        snapshots.append(snapshot)

    return snapshots


class TestCoverageTrendManager:
    """Tests for coverage trend manager."""

    def test_create_local_manager(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test creating a local storage manager."""
        manager = CoverageTrendManager.create_local(root=temp_storage_dir)
        assert manager is not None
        assert isinstance(manager.repository, LocalCoverageTrendRepository)

    def test_save_and_get_snapshot(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test saving and retrieving snapshots."""
        snapshot = sample_snapshots[0]
        manager.save_snapshot(snapshot)

        retrieved = manager.get_snapshot("run-000")
        assert retrieved is not None
        assert retrieved.run_id == "run-000"

    def test_list_snapshots(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test listing snapshots."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        snapshots = manager.list_snapshots()
        assert len(snapshots) == len(sample_snapshots)

    def test_delete_snapshot(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test deleting a snapshot."""
        manager.save_snapshot(sample_snapshots[0])
        deleted = manager.delete_snapshot("run-000")
        assert deleted is True

        retrieved = manager.get_snapshot("run-000")
        assert retrieved is None

    def test_compute_trend_analysis(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test computing trend analysis."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert analysis.metric_type == "line"
        assert analysis.granularity == "repository"
        assert len(analysis.measurements) >= 5  # At least some measurements
        assert analysis.trend_direction in ["improving", "degrading", "stable"]
        assert analysis.stability_score >= 0.0
        assert analysis.stability_score <= 1.0

    def test_trend_direction_improving(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test trend detection for improving coverage."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=3)
        coverages = [80.0, 81.0, 82.0, 83.0, 84.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert analysis.trend_direction == "improving"
        assert analysis.current_value > analysis.average_value

    def test_trend_direction_degrading(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test trend detection for degrading coverage."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=3)
        coverages = [85.0, 84.0, 83.0, 82.0, 81.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert analysis.trend_direction == "degrading"
        assert analysis.current_value < analysis.average_value

    def test_detect_regression(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test regression detection."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        is_regression = manager.detect_regression(
            current_snapshot=sample_snapshots[-1],
            metric_type="line",
            threshold_pct=2.0,
        )

        assert isinstance(is_regression, bool)

    def test_calculate_trend_slope(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test slope calculation."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        slope = manager.calculate_trend_slope(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert isinstance(slope, float)
        assert slope > 0.0  # Sample data is improving

    def test_calculate_volatility_score(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test volatility score calculation."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        volatility = manager.calculate_volatility_score(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert isinstance(volatility, float)
        assert 0.0 <= volatility <= 1.0

    def test_get_historical_data(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test retrieving historical data."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        data = manager.get_historical_data(
            metric_type="line",
            granularity="repository",
        )

        assert len(data) == len(sample_snapshots)
        assert all(isinstance(v, float) for _, v in data)

    def test_module_level_trend_analysis(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test trend analysis at module level."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="module",
            scope_id="src/observer",
            window_days=7,
        )

        assert analysis.scope_id == "src/observer"
        assert analysis.granularity == "module"

    def test_alert_operations(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test alert storage and retrieval."""
        alert = CoverageAlert(
            alert_id="alert-001",
            timestamp=datetime.now(),
            alert_type="below_threshold",
            severity="critical",
            metric_type="line",
            granularity="repository",
            scope_id="",
            current_value=78.5,
            threshold_or_baseline=80.0,
            delta_pct=-1.5,
            baseline_type="minimum_threshold",
        )

        manager.save_alert(alert)
        alerts = manager.list_alerts(severity="critical")

        assert len(alerts) >= 1
        assert any(a.alert_id == "alert-001" for a in alerts)

    def test_cleanup_old_data(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test cleaning up old data."""
        old_snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc) - timedelta(days=40),
            run_id="old-run",
            source="coverage.py",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=82.0,
        )

        recent_snapshot = CoverageSnapshot(
            timestamp=datetime.now(),
            run_id="recent-run",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )

        manager.save_snapshot(old_snapshot)
        manager.save_snapshot(recent_snapshot)

        deleted = manager.cleanup(retention_days=30)

        assert "old-run" in deleted
        assert "recent-run" not in deleted

    def test_empty_snapshot_list(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test handling empty snapshot list."""
        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert analysis.measurements == []
        assert analysis.current_value == 0.0
        assert analysis.stability_score == 0.0

    def test_single_snapshot_analysis(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test trend analysis with single snapshot."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-single",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )
        manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert len(analysis.measurements) == 1
        assert analysis.trend_direction == "stable"
        assert analysis.trend_pct == 0.0

    def test_projected_value_calculation(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test 7-day projection calculation."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=2)
        coverages = [85.0, 85.5, 86.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        assert analysis.projected_value_7days is not None
        assert analysis.projected_value_7days > analysis.current_value

    def test_save_and_get_trend_analysis(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test saving and retrieving trend analysis."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )
        manager.save_trend_analysis(analysis)

        retrieved = manager.get_trend_analysis(
            metric_type="line",
            granularity="repository",
        )
        assert retrieved is not None
        assert retrieved.metric_type == "line"

    def test_is_trend_stable(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test stable trend detection."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=4)
        coverages = [85.0, 85.1, 85.2, 85.1, 85.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        is_stable = manager.is_trend_stable(
            metric_type="line",
            threshold=1.0,
        )
        assert isinstance(is_stable, bool)

    def test_predict_future_coverage(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test future coverage prediction."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        predicted = manager.predict_future_coverage(
            metric_type="line",
            granularity="repository",
            days_ahead=7,
        )

        assert isinstance(predicted, float)
        assert 0.0 <= predicted <= 100.0

    def test_predict_future_coverage_single_snapshot(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test prediction with insufficient data."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-single",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )
        manager.save_snapshot(snapshot)

        predicted = manager.predict_future_coverage(
            metric_type="line",
            granularity="repository",
            days_ahead=7,
        )

        assert predicted == 87.0  # Should return current value

    def test_get_improvement_rate(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test improvement rate calculation."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=4)
        coverages = [80.0, 81.0, 82.0, 83.0, 84.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        rate = manager.get_improvement_rate(
            metric_type="line",
            window_days=7,
        )

        assert isinstance(rate, float)
        assert rate > 0.0  # Positive trend

    def test_get_critical_modules(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test critical module detection."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-001",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
            module_coverages=[
                ModuleCoverage(
                    module_path="src/observer",
                    statement_coverage_pct=75.0,  # Below threshold
                    branch_coverage_pct=70.0,
                    line_coverage_pct=76.0,
                    statement_count=1000,
                    branch_count=500,
                    line_count=900,
                    health_status="critical",
                ),
                ModuleCoverage(
                    module_path="src/custodian",
                    statement_coverage_pct=85.0,  # Above threshold
                    branch_coverage_pct=78.0,
                    line_coverage_pct=86.0,
                    statement_count=500,
                    branch_count=250,
                    line_count=450,
                    health_status="healthy",
                ),
            ],
        )

        critical = manager.get_critical_modules(snapshot, threshold=80.0)

        assert len(critical) == 1
        assert "src/observer" in critical

    def test_should_escalate_alert(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test alert escalation logic."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        # Create degrading trend
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=4)
        coverages = [85.0, 84.0, 83.0, 82.0, 81.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-degrade-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        should_escalate = manager.should_escalate_alert(analysis, alert_count=3)
        assert isinstance(should_escalate, bool)
        assert should_escalate is True  # Degrading + high frequency

    def test_should_not_escalate_improving_trend(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test no escalation for improving trends."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=4)
        coverages = [80.0, 81.0, 82.0, 83.0, 84.0]

        for i, coverage in enumerate(coverages):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-improve-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=coverage - 1.0,
                overall_branch_coverage_pct=coverage - 5.0,
                overall_line_coverage_pct=coverage,
            )
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="repository",
            window_days=7,
        )

        should_escalate = manager.should_escalate_alert(analysis, alert_count=3)
        assert should_escalate is False  # Improving trend

    def test_file_level_trend_analysis(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test trend analysis at file level."""
        # Add file coverage to snapshots
        for snapshot in sample_snapshots:
            snapshot.file_coverages = [
                {
                    "file_path": "src/observer/main.py",
                    "statement_coverage_pct": 85.0,
                    "branch_coverage_pct": 78.0,
                    "line_coverage_pct": 87.0,
                    "uncovered_lines": [],
                    "uncovered_branches": [],
                }
            ]
            manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="file",
            scope_id="src/observer/main.py",
            window_days=7,
        )

        assert analysis.granularity == "file"
        assert analysis.scope_id == "src/observer/main.py"

    def test_list_snapshots_with_date_range(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test listing snapshots with date filtering."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=10)

        for i in range(5):
            snapshot = CoverageSnapshot(
                timestamp=base_time + timedelta(days=i),
                run_id=f"run-{i:03d}",
                source="coverage.py",
                overall_statement_coverage_pct=80.0 + i,
                overall_branch_coverage_pct=75.0 + i,
                overall_line_coverage_pct=82.0 + i,
            )
            manager.save_snapshot(snapshot)

        start_date = base_time + timedelta(days=1)
        end_date = base_time + timedelta(days=3)

        snapshots = manager.list_snapshots(start_date=start_date, end_date=end_date)
        assert len(snapshots) >= 1

    def test_multiple_metric_types(
        self,
        manager: CoverageTrendManager,
        sample_snapshots: list[CoverageSnapshot],
    ) -> None:
        """Test analysis for all metric types."""
        for snapshot in sample_snapshots:
            manager.save_snapshot(snapshot)

        for metric_type in ["statement", "branch", "line"]:
            analysis = manager.compute_trend_analysis(
                metric_type=metric_type,
                granularity="repository",
                window_days=7,
            )
            assert analysis.metric_type == metric_type


class TestCoverageTrendManagerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_regression_detection_insufficient_data(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test regression detection with insufficient data."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-001",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )
        manager.save_snapshot(snapshot)

        is_regression = manager.detect_regression(
            current_snapshot=snapshot,
            metric_type="line",
            threshold_pct=2.0,
        )

        assert is_regression is False

    def test_calculate_trend_slope_insufficient_data(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test slope calculation with single snapshot."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-001",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )
        manager.save_snapshot(snapshot)

        slope = manager.calculate_trend_slope(
            metric_type="line",
            granularity="repository",
        )

        assert slope == 0.0

    def test_volatility_with_zero_average(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test volatility calculation with zero coverage."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-001",
            source="coverage.py",
            overall_statement_coverage_pct=0.0,
            overall_branch_coverage_pct=0.0,
            overall_line_coverage_pct=0.0,
        )
        manager.save_snapshot(snapshot)

        volatility = manager.calculate_volatility_score(
            metric_type="line",
            granularity="repository",
        )

        assert volatility == 0.0

    def test_extract_nonexistent_module(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test extracting metric from nonexistent module."""
        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-001",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
            module_coverages=[
                ModuleCoverage(
                    module_path="src/observer",
                    statement_coverage_pct=85.0,
                    branch_coverage_pct=78.0,
                    line_coverage_pct=87.0,
                    statement_count=1000,
                    branch_count=500,
                    line_count=900,
                    health_status="healthy",
                ),
            ],
        )
        manager.save_snapshot(snapshot)

        analysis = manager.compute_trend_analysis(
            metric_type="line",
            granularity="module",
            scope_id="nonexistent/module",
            window_days=7,
        )

        assert analysis.measurements == []
        assert analysis.current_value == 0.0

    def test_regression_detection_boundary(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test regression detection at threshold boundary."""
        base_time = datetime.now(tz=timezone.utc) - timedelta(days=1)

        # Create two snapshots with exactly -2% change (at boundary)
        snapshot1 = CoverageSnapshot(
            timestamp=base_time,
            run_id="run-001",
            source="coverage.py",
            overall_statement_coverage_pct=82.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=85.0,
        )
        manager.save_snapshot(snapshot1)

        snapshot2 = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-002",
            source="coverage.py",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=83.0,
        )
        manager.save_snapshot(snapshot2)

        # Just at threshold (should be False)
        is_regression = manager.detect_regression(
            current_snapshot=snapshot2,
            metric_type="line",
            threshold_pct=2.0,
        )

        assert is_regression is False

    def test_alert_cleanup_retention(
        self,
        manager: CoverageTrendManager,
    ) -> None:
        """Test cleanup with various retention periods."""
        # Create old and new snapshots
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=45)
        mid_time = datetime.now(tz=timezone.utc) - timedelta(days=25)
        new_time = datetime.now(tz=timezone.utc)

        manager.save_snapshot(
            CoverageSnapshot(
                timestamp=old_time,
                run_id="run-old",
                source="coverage.py",
                overall_statement_coverage_pct=80.0,
                overall_branch_coverage_pct=75.0,
                overall_line_coverage_pct=82.0,
            )
        )

        manager.save_snapshot(
            CoverageSnapshot(
                timestamp=mid_time,
                run_id="run-mid",
                source="coverage.py",
                overall_statement_coverage_pct=81.0,
                overall_branch_coverage_pct=76.0,
                overall_line_coverage_pct=83.0,
            )
        )

        manager.save_snapshot(
            CoverageSnapshot(
                timestamp=new_time,
                run_id="run-new",
                source="coverage.py",
                overall_statement_coverage_pct=82.0,
                overall_branch_coverage_pct=77.0,
                overall_line_coverage_pct=84.0,
            )
        )

        deleted = manager.cleanup(retention_days=30)
        assert len(deleted) >= 1
        assert "run-old" in deleted


class TestCoverageTrendManagerFactories:
    """Tests for manager factory methods."""

    def test_create_s3_manager(self) -> None:
        """Test creating S3 manager."""
        with patch("operations_center.observer.coverage_trend_repository.boto3"):
            manager = CoverageTrendManager.create_s3(
                bucket="test-bucket",
                region="us-west-2",
            )
            assert manager is not None

    def test_create_http_manager(self) -> None:
        """Test creating HTTP manager."""
        with patch("operations_center.observer.coverage_trend_repository.requests"):
            manager = CoverageTrendManager.create_http(
                base_url="http://api.example.com",
            )
            assert manager is not None

    def test_http_manager_with_token(self) -> None:
        """Test creating HTTP manager with token."""
        with patch("operations_center.observer.coverage_trend_repository.requests"):
            manager = CoverageTrendManager.create_http(
                base_url="http://api.example.com",
                token="secret-token",
            )
            assert manager is not None

    def test_create_local_with_custom_retention(self, tmp_path: Path) -> None:
        """Test creating local manager with custom retention."""
        manager = CoverageTrendManager.create_local(
            root=tmp_path, retention_days=60
        )
        assert manager is not None

    def test_create_s3_with_credentials(self) -> None:
        """Test creating S3 manager with credentials."""
        with patch("operations_center.observer.coverage_trend_repository.boto3"):
            manager = CoverageTrendManager.create_s3(
                bucket="test-bucket",
                access_key="test-key",
                secret_key="test-secret",
                region="us-west-2",
            )
            assert manager is not None

    def test_create_s3_with_prefix(self) -> None:
        """Test creating S3 manager with custom prefix."""
        with patch("operations_center.observer.coverage_trend_repository.boto3"):
            manager = CoverageTrendManager.create_s3(
                bucket="test-bucket",
                prefix="custom-prefix",
            )
            assert manager is not None


class TestModuleLevelFunctions:
    """Tests for module-level utility functions."""

    def test_calculate_measurements_average_empty(self) -> None:
        """Test average calculation with empty list."""
        measurements: list[tuple[datetime, float]] = []
        average = calculate_measurements_average(measurements)
        assert average == 0.0

    def test_calculate_measurements_average_single(self) -> None:
        """Test average calculation with single measurement."""
        measurements = [(datetime.now(tz=timezone.utc), 85.5)]
        average = calculate_measurements_average(measurements)
        assert average == 85.5

    def test_calculate_measurements_average_multiple(self) -> None:
        """Test average calculation with multiple measurements."""
        base_time = datetime.now(tz=timezone.utc)
        measurements = [
            (base_time, 80.0),
            (base_time + timedelta(days=1), 85.0),
            (base_time + timedelta(days=2), 90.0),
        ]
        average = calculate_measurements_average(measurements)
        assert average == 85.0

    def test_calculate_measurements_average_decimal_values(self) -> None:
        """Test average with decimal coverage values."""
        base_time = datetime.now(tz=timezone.utc)
        measurements = [
            (base_time, 85.5),
            (base_time + timedelta(days=1), 86.3),
            (base_time + timedelta(days=2), 87.2),
        ]
        average = calculate_measurements_average(measurements)
        assert 86.0 < average < 87.1
