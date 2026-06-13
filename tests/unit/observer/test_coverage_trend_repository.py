# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage trend repository storage backends."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
    FileCoverage,
    ModuleCoverage,
)
from operations_center.observer.coverage_trend_repository import (
    HTTPCoverageTrendRepository,
    LocalCoverageTrendRepository,
    S3CoverageTrendRepository,
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
def sample_snapshot() -> CoverageSnapshot:
    """Create a sample coverage snapshot."""
    return CoverageSnapshot(
        timestamp=datetime.now(tz=timezone.utc),
        run_id="test-run-001",
        source="coverage.py",
        overall_statement_coverage_pct=85.5,
        overall_branch_coverage_pct=78.2,
        overall_line_coverage_pct=87.1,
        test_execution_time_ms=5000,
        test_count=150,
        module_coverages=[
            ModuleCoverage(
                module_path="src/observer",
                statement_coverage_pct=90.0,
                branch_coverage_pct=85.0,
                line_coverage_pct=91.0,
                statement_count=1000,
                branch_count=500,
                line_count=900,
                health_status="healthy",
            ),
        ],
    )


@pytest.fixture
def sample_trend_analysis() -> CoverageTrendAnalysis:
    """Create a sample trend analysis."""
    return CoverageTrendAnalysis(
        metric_type="line",
        granularity="repository",
        scope_id="",
        window_start=datetime.now(tz=timezone.utc) - timedelta(days=7),
        window_end=datetime.now(tz=timezone.utc),
        measurements=[
            (datetime.now(tz=timezone.utc) - timedelta(days=6), 85.0),
            (datetime.now(tz=timezone.utc) - timedelta(days=5), 85.5),
            (datetime.now(tz=timezone.utc) - timedelta(days=4), 86.0),
            (datetime.now(tz=timezone.utc) - timedelta(days=3), 85.8),
            (datetime.now(tz=timezone.utc) - timedelta(days=2), 87.0),
            (datetime.now(tz=timezone.utc) - timedelta(days=1), 87.1),
        ],
        current_value=87.1,
        average_value=86.0,
        min_value=85.0,
        max_value=87.1,
        trend_direction="improving",
        trend_pct=2.5,
        standard_deviation=0.85,
        stability_score=0.95,
    )


@pytest.fixture
def sample_alert() -> CoverageAlert:
    """Create a sample coverage alert."""
    return CoverageAlert(
        alert_id="alert-001",
        timestamp=datetime.now(tz=timezone.utc),
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


class TestLocalCoverageTrendRepository:
    """Tests for local filesystem storage backend."""

    def test_store_and_load_snapshot(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test storing and loading a snapshot."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        metadata = repo.store_snapshot(sample_snapshot)

        assert metadata["run_id"] == "test-run-001"
        assert "checksum" in metadata
        assert "path" in metadata

        loaded = repo.load_snapshot("test-run-001")
        assert loaded.run_id == sample_snapshot.run_id
        assert loaded.overall_line_coverage_pct == sample_snapshot.overall_line_coverage_pct

    def test_list_snapshots(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test listing stored snapshots."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        snapshot1 = sample_snapshot
        snapshot2 = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc) + timedelta(hours=1),
            run_id="test-run-002",
            source="coverage.py",
            overall_statement_coverage_pct=86.0,
            overall_branch_coverage_pct=79.0,
            overall_line_coverage_pct=88.0,
        )

        repo.store_snapshot(snapshot1)
        repo.store_snapshot(snapshot2)

        snapshots = repo.list_snapshots()
        assert len(snapshots) == 2

        limited = repo.list_snapshots(limit=1)
        assert len(limited) == 1

    def test_delete_snapshot(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test deleting a snapshot."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo.store_snapshot(sample_snapshot)

        deleted = repo.delete_snapshot("test-run-001")
        assert deleted is True

        with pytest.raises(FileNotFoundError):
            repo.load_snapshot("test-run-001")

    def test_store_and_load_trend_analysis(
        self,
        temp_storage_dir: Path,
        sample_trend_analysis: CoverageTrendAnalysis,
    ) -> None:
        """Test storing and loading trend analysis."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        metadata = repo.store_trend_analysis(sample_trend_analysis)
        assert "checksum" in metadata

        loaded = repo.load_trend_analysis("line", "repository")
        assert loaded is not None
        assert loaded.metric_type == "line"
        assert loaded.trend_direction == "improving"

    def test_store_and_list_alerts(
        self,
        temp_storage_dir: Path,
        sample_alert: CoverageAlert,
    ) -> None:
        """Test storing and listing alerts."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        metadata = repo.store_alert(sample_alert)
        assert metadata["run_id"] == "alert-001"

        alerts = repo.list_alerts()
        assert len(alerts) >= 1
        assert any(a.alert_id == "alert-001" for a in alerts)

    def test_cleanup_old_snapshots(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test cleaning up old snapshots."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir, retention_days=7)

        old_snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc) - timedelta(days=10),
            run_id="old-run",
            source="coverage.py",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=82.0,
        )

        repo.store_snapshot(old_snapshot)
        repo.store_snapshot(sample_snapshot)

        deleted = repo.cleanup(retention_days=7)
        assert "old-run" in deleted
        assert "test-run-001" not in deleted

    def test_load_nonexistent_snapshot_raises_error(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test that loading nonexistent snapshot raises error."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        with pytest.raises(FileNotFoundError):
            repo.load_snapshot("nonexistent")

    def test_date_range_filtering(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test filtering snapshots by date range."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        now = datetime.now(tz=timezone.utc)
        for i in range(3):
            snapshot = CoverageSnapshot(
                timestamp=now - timedelta(days=i),
                run_id=f"run-{i}",
                source="coverage.py",
                overall_statement_coverage_pct=85.0,
                overall_branch_coverage_pct=78.0,
                overall_line_coverage_pct=87.0,
            )
            repo.store_snapshot(snapshot)

        recent = repo.list_snapshots(
            start_date=now - timedelta(days=0.5),
            end_date=now + timedelta(days=0.5),
        )
        assert len(recent) >= 1


class TestS3CoverageTrendRepository:
    """Tests for S3 storage backend."""

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_store_snapshot_to_s3(
        self,
        mock_boto3: MagicMock,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test storing snapshot to S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        metadata = repo.store_snapshot(sample_snapshot)

        assert metadata["run_id"] == "test-run-001"
        assert "s3://" in metadata["path"]
        mock_client.put_object.assert_called_once()

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_load_snapshot_from_s3(
        self,
        mock_boto3: MagicMock,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test loading snapshot from S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        content = sample_snapshot.model_dump_json()
        mock_response = {"Body": MagicMock(read=MagicMock(return_value=content.encode()))}
        mock_client.get_object.return_value = mock_response

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        loaded = repo.load_snapshot("test-run-001")

        assert loaded.run_id == sample_snapshot.run_id

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_delete_snapshot_from_s3(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test deleting snapshot from S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        result = repo.delete_snapshot("test-run-001")

        assert result is True
        mock_client.delete_object.assert_called_once()

    def test_s3_requires_boto3(self) -> None:
        """Test that S3 repository requires boto3."""
        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(ImportError):
                S3CoverageTrendRepository(bucket="test-bucket")


class TestHTTPCoverageTrendRepository:
    """Tests for HTTP storage backend."""

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_store_snapshot_via_http(
        self,
        mock_requests: MagicMock,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test storing snapshot via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        metadata = repo.store_snapshot(sample_snapshot)

        assert metadata["run_id"] == "test-run-001"
        mock_session.put.assert_called_once()

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_load_snapshot_via_http(
        self,
        mock_requests: MagicMock,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test loading snapshot via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        content = sample_snapshot.model_dump_json()
        mock_response = MagicMock(text=content)
        mock_session.get.return_value = mock_response

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        loaded = repo.load_snapshot("test-run-001")

        assert loaded.run_id == sample_snapshot.run_id

    def test_http_requires_requests(self) -> None:
        """Test that HTTP repository requires requests."""
        with patch.dict("sys.modules", {"requests": None}):
            with pytest.raises(ImportError):
                HTTPCoverageTrendRepository(base_url="http://api.example.com")

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_http_bearer_token_authentication(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test HTTP repository with bearer token."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        repo = HTTPCoverageTrendRepository(
            base_url="http://api.example.com",
            token="test-token",
        )

        mock_session.headers.update.assert_called_once_with(
            {"Authorization": "Bearer test-token"}
        )
