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
    ModuleCoverage,
)
from operations_center.observer.coverage_trend_manager import CoverageTrendManager
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
            severity="high",
            metric_type="line",
            granularity="repository",
            scope_id="",
            current_value=78.5,
            threshold_or_baseline=80.0,
            delta_pct=-1.5,
            baseline_type="minimum_threshold",
        )

        manager.save_alert(alert)
        alerts = manager.list_alerts(severity="high")

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
