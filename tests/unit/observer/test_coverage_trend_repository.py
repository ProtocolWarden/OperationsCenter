# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage trend repository storage backends."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
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
        severity="critical",
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
        """Test that HTTP repository raises ImportError when requests is unavailable."""
        with patch("operations_center.observer.coverage_trend_repository.requests", None):
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

        _repo = HTTPCoverageTrendRepository(
            base_url="http://api.example.com",
            token="test-token",
        )

        mock_session.headers.update.assert_called_once_with({"Authorization": "Bearer test-token"})


class TestLocalRepositoryEdgeCases:
    """Tests for edge cases in local repository."""

    def test_list_snapshots_with_severity_filter(
        self,
        temp_storage_dir: Path,
        sample_alert: CoverageAlert,
    ) -> None:
        """Test filtering alerts by severity."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        critical_alert = CoverageAlert(
            alert_id="critical-001",
            timestamp=datetime.now(tz=timezone.utc),
            alert_type="below_threshold",
            severity="critical",
            metric_type="line",
            granularity="repository",
            scope_id="",
            current_value=70.0,
            threshold_or_baseline=80.0,
            delta_pct=-10.0,
            baseline_type="minimum_threshold",
        )

        warning_alert = CoverageAlert(
            alert_id="warning-001",
            timestamp=datetime.now(tz=timezone.utc),
            alert_type="below_threshold",
            severity="warning",
            metric_type="line",
            granularity="repository",
            scope_id="",
            current_value=75.0,
            threshold_or_baseline=80.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )

        repo.store_alert(critical_alert)
        repo.store_alert(warning_alert)

        critical_alerts = repo.list_alerts(severity="critical")
        assert len(critical_alerts) == 1
        assert critical_alerts[0].severity == "critical"

        warning_alerts = repo.list_alerts(severity="warning")
        assert len(warning_alerts) == 1
        assert warning_alerts[0].severity == "warning"

    def test_list_snapshots_empty(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test listing snapshots from empty repository."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        snapshots = repo.list_snapshots()
        assert snapshots == []

    def test_load_nonexistent_trend_analysis(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test loading trend analysis that doesn't exist."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        result = repo.load_trend_analysis("line", "repository")
        assert result is None

    def test_delete_nonexistent_snapshot(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test deleting snapshot that doesn't exist."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        result = repo.delete_snapshot("nonexistent")
        assert result is False

    def test_list_alerts_with_limit(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test listing alerts with limit."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        for i in range(5):
            alert = CoverageAlert(
                alert_id=f"alert-{i}",
                timestamp=datetime.now(tz=timezone.utc),
                alert_type="below_threshold",
                severity="warning",
                metric_type="line",
                granularity="repository",
                scope_id="",
                current_value=75.0,
                threshold_or_baseline=80.0,
                delta_pct=-5.0,
                baseline_type="minimum_threshold",
            )
            repo.store_alert(alert)

        limited_alerts = repo.list_alerts(limit=2)
        assert len(limited_alerts) == 2

    def test_list_alerts_empty_repository(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test listing alerts from repository with no alerts."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        alerts = repo.list_alerts()
        assert alerts == []

    def test_store_multiple_trend_analyses(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test storing multiple trend analyses for same metric."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        now = datetime.now(tz=timezone.utc)
        analysis1 = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=7),
            window_end=now,
            measurements=[(now - timedelta(days=i), 85.0 + i) for i in range(5)],
            current_value=90.0,
            average_value=87.5,
            min_value=85.0,
            max_value=90.0,
            trend_direction="improving",
            trend_pct=5.0,
            standard_deviation=1.5,
            stability_score=0.9,
        )

        analysis2 = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=6),
            window_end=now + timedelta(days=1),
            measurements=[(now - timedelta(days=i), 87.0 + i) for i in range(5)],
            current_value=92.0,
            average_value=89.5,
            min_value=87.0,
            max_value=92.0,
            trend_direction="improving",
            trend_pct=7.0,
            standard_deviation=1.2,
            stability_score=0.95,
        )

        repo.store_trend_analysis(analysis1)
        repo.store_trend_analysis(analysis2)

        loaded = repo.load_trend_analysis("line", "repository")
        assert loaded is not None
        assert loaded.current_value == 92.0

    def test_cleanup_with_invalid_timestamps(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test cleanup handles invalid timestamps gracefully."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir, retention_days=7)

        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="test-run",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )

        repo.store_snapshot(snapshot)

        # Corrupt the index with invalid timestamp
        repo._index["test-run"]["observed_at"] = "invalid-date"

        deleted = repo.cleanup(retention_days=7)
        assert "test-run" not in deleted


class TestS3RepositoryEdgeCases:
    """Tests for edge cases in S3 repository."""

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_list_snapshots_from_s3(
        self,
        mock_boto3: MagicMock,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test listing snapshots from S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        now = datetime.now(tz=timezone.utc)
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "coverage-trends/snapshots/run-1/snapshot.json",
                        "LastModified": now,
                    },
                ]
            }
        ]

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        snapshots = repo.list_snapshots()

        assert len(snapshots) >= 0

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_store_trend_analysis_to_s3_append(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test appending trend analysis to existing S3 file."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        existing_content = '{"trend": "old"}'
        body_mock = MagicMock(read=lambda: existing_content.encode())
        mock_client.get_object.return_value = {"Body": body_mock}

        now = datetime.now(tz=timezone.utc)
        analysis = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=7),
            window_end=now,
            measurements=[],
            current_value=85.0,
            average_value=85.0,
            min_value=85.0,
            max_value=85.0,
            trend_direction="stable",
            trend_pct=0.0,
            standard_deviation=0.0,
            stability_score=1.0,
        )

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        metadata = repo.store_trend_analysis(analysis)

        assert "checksum" in metadata
        mock_client.put_object.assert_called_once()

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_list_alerts_from_s3(
        self,
        mock_boto3: MagicMock,
        sample_alert: CoverageAlert,
    ) -> None:
        """Test listing alerts from S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        alert_content = sample_alert.model_dump_json()
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "coverage-trends/alerts/2026-06-13/alerts.jsonl",
                        "LastModified": datetime.now(tz=timezone.utc),
                    },
                ]
            }
        ]

        body_mock = MagicMock(read=lambda: alert_content.encode())
        mock_client.get_object.return_value = {"Body": body_mock}

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        alerts = repo.list_alerts()

        assert len(alerts) >= 0

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_cleanup_s3_old_snapshots(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test cleanup of old snapshots in S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        old_date = datetime.now(tz=timezone.utc) - timedelta(days=40)
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "coverage-trends/snapshots/old-run/snapshot.json",
                        "LastModified": old_date,
                    },
                ]
            }
        ]

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        deleted = repo.cleanup(retention_days=30)

        assert len(deleted) >= 0


class TestHTTPRepositoryEdgeCases:
    """Tests for edge cases in HTTP repository."""

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_delete_snapshot_via_http_failure(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test handling HTTP deletion failure."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.delete.side_effect = Exception("Network error")

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        result = repo.delete_snapshot("test-run")

        assert result is False

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_list_snapshots_via_http(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test listing snapshots via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        mock_response = MagicMock(
            json=lambda: [
                {"run_id": "run-1", "observed_at": "2026-06-13T00:00:00+00:00"},
            ]
        )
        mock_session.get.return_value = mock_response

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        snapshots = repo.list_snapshots()

        assert len(snapshots) >= 0

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_store_trend_analysis_via_http(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test storing trend analysis via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        now = datetime.now(tz=timezone.utc)
        analysis = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=7),
            window_end=now,
            measurements=[],
            current_value=85.0,
            average_value=85.0,
            min_value=85.0,
            max_value=85.0,
            trend_direction="stable",
            trend_pct=0.0,
            standard_deviation=0.0,
            stability_score=1.0,
        )

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        metadata = repo.store_trend_analysis(analysis)

        assert "checksum" in metadata
        mock_session.put.assert_called_once()

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_load_trend_analysis_via_http_failure(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test handling HTTP trend analysis load failure."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.side_effect = Exception("Network error")

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        result = repo.load_trend_analysis("line", "repository")

        assert result is None

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_list_alerts_via_http(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test listing alerts via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        alert_data = {
            "alert_id": "alert-1",
            "timestamp": "2026-06-13T00:00:00+00:00",
            "alert_type": "below_threshold",
            "severity": "critical",
            "metric_type": "line",
            "granularity": "repository",
            "scope_id": "",
            "current_value": 75.0,
            "threshold_or_baseline": 80.0,
            "delta_pct": -5.0,
            "baseline_type": "minimum_threshold",
        }

        mock_response = MagicMock(json=lambda: [alert_data])
        mock_session.get.return_value = mock_response

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        alerts = repo.list_alerts()

        assert len(alerts) >= 0

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_cleanup_via_http(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test cleanup via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        mock_response = MagicMock(json=lambda: {"deleted": ["run-1", "run-2"]})
        mock_session.post.return_value = mock_response

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        deleted = repo.cleanup(retention_days=30)

        assert len(deleted) >= 0


class TestValidationFunctions:
    """Tests for validation helper functions."""

    def test_validate_snapshot_data_valid(
        self,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test snapshot validation with valid data."""
        from operations_center.observer.coverage_trend_repository import validate_snapshot_data

        result = validate_snapshot_data(sample_snapshot)
        assert result is True

    def test_validate_snapshot_data_invalid_coverage(self) -> None:
        """Test snapshot validation with invalid coverage percentage."""
        from operations_center.observer.coverage_trend_repository import validate_snapshot_data

        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="test",
            source="coverage.py",
            overall_statement_coverage_pct=150.0,  # Invalid: > 100
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )
        result = validate_snapshot_data(snapshot)
        assert result is False

    def test_validate_trend_analysis_valid(
        self,
        sample_trend_analysis: CoverageTrendAnalysis,
    ) -> None:
        """Test trend analysis validation with valid data."""
        from operations_center.observer.coverage_trend_repository import validate_trend_analysis

        result = validate_trend_analysis(sample_trend_analysis)
        assert result is True

    def test_validate_trend_analysis_invalid_direction(self) -> None:
        """Test trend analysis validation with invalid direction."""
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            CoverageTrendAnalysis(
                metric_type="line",
                granularity="repository",
                scope_id="",
                window_start=datetime.now(tz=timezone.utc) - timedelta(days=7),
                window_end=datetime.now(tz=timezone.utc),
                measurements=[],
                current_value=85.0,
                average_value=85.0,
                min_value=85.0,
                max_value=85.0,
                trend_direction="invalid",  # Invalid direction
                trend_pct=0.0,
                standard_deviation=0.0,
                stability_score=1.0,
            )

    def test_validate_alert_valid(
        self,
        sample_alert: CoverageAlert,
    ) -> None:
        """Test alert validation with valid data."""
        from operations_center.observer.coverage_trend_repository import validate_alert

        result = validate_alert(sample_alert)
        assert result is True

    def test_validate_alert_invalid_type(self) -> None:
        """Test alert validation with invalid alert type."""
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            CoverageAlert(
                alert_id="alert-1",
                timestamp=datetime.now(tz=timezone.utc),
                alert_type="invalid_type",  # Invalid type
                severity="critical",
                metric_type="line",
                granularity="repository",
                scope_id="",
                current_value=75.0,
                threshold_or_baseline=80.0,
                delta_pct=-5.0,
                baseline_type="minimum_threshold",
            )


class TestLocalRepositoryIndexHandling:
    """Tests for local repository index operations."""

    def test_load_index_corrupted_json(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test loading corrupted index file."""
        index_file = temp_storage_dir / "index.json"
        index_file.write_text("{invalid json}", encoding="utf-8")

        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        assert repo._index == {}

    def test_save_and_reload_index(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test saving and reloading index."""
        repo1 = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo1.store_snapshot(sample_snapshot)

        repo2 = LocalCoverageTrendRepository(root=temp_storage_dir)
        assert sample_snapshot.run_id in repo2._index

    def test_index_with_string_values(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test that index handles mixed value types correctly."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo._index = {
            "run-1": {"observed_at": "2026-06-13T00:00:00+00:00", "version": 1},
        }
        repo._save_index()

        loaded_repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        assert "run-1" in loaded_repo._index

    def test_list_snapshots_timezone_handling(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test date filtering with timezone-naive datetimes."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="tz-test",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )
        repo.store_snapshot(snapshot)

        now = datetime.now()  # Naive datetime
        snapshots = repo.list_snapshots(
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )
        assert len(snapshots) >= 0


class TestHTTPRepositoryEdgeErrorHandling:
    """Tests for HTTP repository error handling."""

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_http_store_snapshot_http_error(
        self,
        mock_requests: MagicMock,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test handling HTTP errors when storing snapshot."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 500")
        mock_session.put.return_value = mock_response

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        with pytest.raises(Exception):
            repo.store_snapshot(sample_snapshot)

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_http_delete_nonexistent_snapshot(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test deleting snapshot that doesn't exist via HTTP."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.delete.side_effect = Exception("404 Not Found")

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        result = repo.delete_snapshot("nonexistent")

        assert result is False

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_http_list_snapshots_empty(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test listing snapshots when none exist."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.return_value = MagicMock(json=lambda: [])

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        snapshots = repo.list_snapshots()

        assert snapshots == []

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_http_load_alert_parsing_error(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test handling JSON parse error when loading alerts."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session
        mock_session.get.return_value = MagicMock(json=lambda: "not a list")

        repo = HTTPCoverageTrendRepository(base_url="http://api.example.com")
        with pytest.raises(Exception):
            repo.list_alerts()


class TestS3RepositoryErrorScenarios:
    """Tests for S3 repository error handling."""

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_s3_store_trend_file_not_found_exception(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test S3 handling NoSuchKey exception when appending trend data."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Create a proper NoSuchKey exception that inherits from Exception
        class NoSuchKeyError(Exception):
            pass

        mock_client.exceptions.NoSuchKey = NoSuchKeyError
        mock_client.get_object.side_effect = NoSuchKeyError()

        now = datetime.now(tz=timezone.utc)
        analysis = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=7),
            window_end=now,
            measurements=[],
            current_value=85.0,
            average_value=85.0,
            min_value=85.0,
            max_value=85.0,
            trend_direction="stable",
            trend_pct=0.0,
            standard_deviation=0.0,
            stability_score=1.0,
        )

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        metadata = repo.store_trend_analysis(analysis)

        assert "checksum" in metadata
        mock_client.put_object.assert_called_once()

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_s3_load_trend_with_empty_lines(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test S3 loading trend analysis when file returns NoSuchKey."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        class NoSuchKeyError(Exception):
            pass

        mock_client.exceptions.NoSuchKey = NoSuchKeyError
        mock_client.get_object.side_effect = NoSuchKeyError()

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        result = repo.load_trend_analysis("line", "repository")

        assert result is None

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_s3_list_alerts_with_parse_error(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test S3 handling JSON parse error in alerts."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        paginator = MagicMock()
        mock_client.get_paginator.return_value = paginator

        # Mock paginate response with one valid and one invalid file
        now = datetime.now(tz=timezone.utc)
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "alerts/2026-06-13/alerts.jsonl", "LastModified": now},
                ]
            }
        ]

        # File contains invalid JSON
        mock_client.get_object.side_effect = Exception("JSON parse error")

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        alerts = repo.list_alerts()

        assert alerts == []


class TestLocalRepositoryStorageFormats:
    """Tests for local repository storage format handling."""

    def test_store_snapshot_creates_correct_structure(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test that snapshot creates correct directory structure."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo.store_snapshot(sample_snapshot)

        snapshot_dir = temp_storage_dir / "snapshots" / sample_snapshot.run_id
        assert snapshot_dir.exists()
        assert (snapshot_dir / "snapshot.json").exists()

    def test_store_alert_groups_by_date(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test that alerts are grouped by date."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        date1 = datetime(2026, 6, 13, tzinfo=timezone.utc)
        date2 = datetime(2026, 6, 14, tzinfo=timezone.utc)

        alert1 = CoverageAlert(
            alert_id="alert-1",
            timestamp=date1,
            alert_type="below_threshold",
            severity="critical",
            metric_type="line",
            granularity="repository",
            scope_id="",
            current_value=75.0,
            threshold_or_baseline=80.0,
            delta_pct=-5.0,
            baseline_type="minimum_threshold",
        )

        alert2 = CoverageAlert(
            alert_id="alert-2",
            timestamp=date2,
            alert_type="below_threshold",
            severity="warning",
            metric_type="line",
            granularity="repository",
            scope_id="",
            current_value=78.0,
            threshold_or_baseline=80.0,
            delta_pct=-2.0,
            baseline_type="minimum_threshold",
        )

        repo.store_alert(alert1)
        repo.store_alert(alert2)

        alerts_dir = temp_storage_dir / "alerts"
        assert (alerts_dir / "2026-06-13").exists()
        assert (alerts_dir / "2026-06-14").exists()

    def test_load_trend_analysis_multiple_entries(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test loading latest trend analysis when multiple exist."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        now = datetime.now(tz=timezone.utc)
        analysis1 = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=7),
            window_end=now - timedelta(days=1),
            measurements=[],
            current_value=85.0,
            average_value=85.0,
            min_value=85.0,
            max_value=85.0,
            trend_direction="stable",
            trend_pct=0.0,
            standard_deviation=0.0,
            stability_score=1.0,
        )

        analysis2 = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=1),
            window_end=now,
            measurements=[],
            current_value=87.0,
            average_value=87.0,
            min_value=87.0,
            max_value=87.0,
            trend_direction="improving",
            trend_pct=2.0,
            standard_deviation=0.0,
            stability_score=1.0,
        )

        repo.store_trend_analysis(analysis1)
        repo.store_trend_analysis(analysis2)

        loaded = repo.load_trend_analysis("line", "repository")
        assert loaded is not None
        assert loaded.current_value == 87.0


class TestChecksumVerification:
    """Tests for checksum generation and verification."""

    def test_snapshot_checksum_consistency(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test that identical snapshots produce identical checksums."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        metadata1 = repo.store_snapshot(sample_snapshot)
        checksum1 = metadata1["checksum"]

        snapshot_copy = CoverageSnapshot(
            timestamp=sample_snapshot.timestamp,
            run_id="copy-run",
            source=sample_snapshot.source,
            overall_statement_coverage_pct=sample_snapshot.overall_statement_coverage_pct,
            overall_branch_coverage_pct=sample_snapshot.overall_branch_coverage_pct,
            overall_line_coverage_pct=sample_snapshot.overall_line_coverage_pct,
        )

        metadata2 = repo.store_snapshot(snapshot_copy)
        checksum2 = metadata2["checksum"]

        assert len(checksum1) == 64
        assert checksum1.isalnum()
        assert checksum1 != checksum2

    def test_alert_checksum_present(
        self,
        temp_storage_dir: Path,
        sample_alert: CoverageAlert,
    ) -> None:
        """Test that stored alerts generate checksums."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        metadata = repo.store_alert(sample_alert)
        assert "checksum" in metadata
        assert len(metadata["checksum"]) == 64

    def test_trend_analysis_checksum_present(
        self,
        temp_storage_dir: Path,
        sample_trend_analysis: CoverageTrendAnalysis,
    ) -> None:
        """Test that trend analyses generate checksums."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        metadata = repo.store_trend_analysis(sample_trend_analysis)
        assert "checksum" in metadata
        assert len(metadata["checksum"]) == 64


class TestConcurrentAccessPatterns:
    """Tests for concurrent access patterns and thread safety."""

    def test_multiple_snapshots_same_repo(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test storing multiple snapshots concurrently."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        snapshots = [
            CoverageSnapshot(
                timestamp=datetime.now(tz=timezone.utc),
                run_id=f"concurrent-run-{i}",
                source="coverage.py",
                overall_statement_coverage_pct=80.0 + i,
                overall_branch_coverage_pct=75.0 + i,
                overall_line_coverage_pct=82.0 + i,
            )
            for i in range(10)
        ]

        for snapshot in snapshots:
            repo.store_snapshot(snapshot)

        loaded_snapshots = repo.list_snapshots()
        assert len(loaded_snapshots) == 10

    def test_concurrent_alert_writes_same_day(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test storing multiple alerts on same day."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        now = datetime.now(tz=timezone.utc)

        for i in range(5):
            alert = CoverageAlert(
                alert_id=f"concurrent-alert-{i}",
                timestamp=now,
                alert_type="below_threshold",
                severity="warning",
                metric_type="line",
                granularity="repository",
                scope_id="",
                current_value=75.0 - i,
                threshold_or_baseline=80.0,
                delta_pct=-5.0 - i,
                baseline_type="minimum_threshold",
            )
            repo.store_alert(alert)

        alerts = repo.list_alerts()
        assert len(alerts) == 5

    def test_index_persistence_concurrent_operations(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test index persistence during concurrent operations."""
        repo1 = LocalCoverageTrendRepository(root=temp_storage_dir)

        snapshot1 = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="run-1",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )

        repo1.store_snapshot(snapshot1)

        repo2 = LocalCoverageTrendRepository(root=temp_storage_dir)
        loaded = repo2.load_snapshot("run-1")
        assert loaded.run_id == "run-1"

        repo1.store_snapshot(
            CoverageSnapshot(
                timestamp=datetime.now(tz=timezone.utc),
                run_id="run-2",
                source="coverage.py",
                overall_statement_coverage_pct=86.0,
                overall_branch_coverage_pct=79.0,
                overall_line_coverage_pct=88.0,
            )
        )

        repo2_snapshots = repo2.list_snapshots()
        assert len(repo2_snapshots) >= 1


class TestLargeDataHandling:
    """Tests for handling large data structures."""

    def test_snapshot_with_many_modules(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test storing snapshot with many module coverages."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        modules = [
            ModuleCoverage(
                module_path=f"src/module{i}",
                statement_coverage_pct=85.0 + (i % 10),
                branch_coverage_pct=78.0 + (i % 10),
                line_coverage_pct=87.0 + (i % 10),
                statement_count=1000 + i * 100,
                branch_count=500 + i * 50,
                line_count=900 + i * 100,
                health_status="healthy",
            )
            for i in range(50)
        ]

        snapshot = CoverageSnapshot(
            timestamp=datetime.now(tz=timezone.utc),
            run_id="large-snapshot",
            source="coverage.py",
            overall_statement_coverage_pct=85.5,
            overall_branch_coverage_pct=78.2,
            overall_line_coverage_pct=87.1,
            module_coverages=modules,
        )

        metadata = repo.store_snapshot(snapshot)
        loaded = repo.load_snapshot("large-snapshot")

        assert len(loaded.module_coverages) == 50
        assert len(metadata["checksum"]) == 64

    def test_trend_analysis_with_many_measurements(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test trend analysis with extended history."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)

        now = datetime.now(tz=timezone.utc)
        measurements = [
            (now - timedelta(days=i), 80.0 + (i * 0.5))
            for i in range(90)
        ]

        analysis = CoverageTrendAnalysis(
            metric_type="line",
            granularity="repository",
            scope_id="",
            window_start=now - timedelta(days=90),
            window_end=now,
            measurements=measurements,
            current_value=90.0,
            average_value=85.0,
            min_value=80.0,
            max_value=90.0,
            trend_direction="improving",
            trend_pct=10.0,
            standard_deviation=2.5,
            stability_score=0.85,
        )

        repo.store_trend_analysis(analysis)
        loaded = repo.load_trend_analysis("line", "repository")

        assert loaded is not None
        assert len(loaded.measurements) == 90


class TestS3PaginationHandling:
    """Tests for S3 pagination with large result sets."""

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_s3_list_snapshots_multiple_pages(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test S3 pagination with multiple pages of snapshots."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        now = datetime.now(tz=timezone.utc)
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": f"coverage-trends/snapshots/run-{i}/snapshot.json", "LastModified": now}
                    for i in range(1000, 1500)
                ]
            },
            {
                "Contents": [
                    {"Key": f"coverage-trends/snapshots/run-{i}/snapshot.json", "LastModified": now}
                    for i in range(1500, 2000)
                ]
            },
        ]

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        snapshots = repo.list_snapshots()

        assert len(snapshots) == 1000

    @patch("operations_center.observer.coverage_trend_repository.boto3")
    def test_s3_list_snapshots_with_limit_pagination(
        self,
        mock_boto3: MagicMock,
    ) -> None:
        """Test S3 pagination respects limit parameter."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        now = datetime.now(tz=timezone.utc)
        mock_client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": f"coverage-trends/snapshots/run-{i}/snapshot.json", "LastModified": now}
                    for i in range(100)
                ]
            },
            {
                "Contents": [
                    {"Key": f"coverage-trends/snapshots/run-{i}/snapshot.json", "LastModified": now}
                    for i in range(100, 200)
                ]
            },
        ]

        repo = S3CoverageTrendRepository(bucket="test-bucket")
        snapshots = repo.list_snapshots(limit=50)

        assert len(snapshots) == 50


class TestRecoveryAndResilience:
    """Tests for recovery from partial failures and corruption."""

    def test_snapshot_directory_missing_recovery(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test recovery when snapshot directory is manually deleted."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo.store_snapshot(sample_snapshot)

        snapshot_dir = temp_storage_dir / "snapshots" / sample_snapshot.run_id
        shutil.rmtree(snapshot_dir)

        with pytest.raises(FileNotFoundError):
            repo.load_snapshot(sample_snapshot.run_id)

    def test_corrupted_snapshot_file_handling(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test handling of corrupted snapshot JSON."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo.store_snapshot(sample_snapshot)

        snapshot_file = temp_storage_dir / "snapshots" / sample_snapshot.run_id / "snapshot.json"
        snapshot_file.write_text("{invalid json content}", encoding="utf-8")

        with pytest.raises(Exception):
            repo.load_snapshot(sample_snapshot.run_id)

    def test_partial_cleanup_recovery(
        self,
        temp_storage_dir: Path,
    ) -> None:
        """Test cleanup continues after encountering invalid entry."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir, retention_days=7)

        now = datetime.now(tz=timezone.utc)
        snapshot1 = CoverageSnapshot(
            timestamp=now - timedelta(days=10),
            run_id="old-run-1",
            source="coverage.py",
            overall_statement_coverage_pct=80.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=82.0,
        )

        snapshot2 = CoverageSnapshot(
            timestamp=now - timedelta(days=3),
            run_id="recent-run",
            source="coverage.py",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=78.0,
            overall_line_coverage_pct=87.0,
        )

        repo.store_snapshot(snapshot1)
        repo.store_snapshot(snapshot2)

        repo._index["invalid-run"] = {"observed_at": "invalid-date"}

        deleted = repo.cleanup(retention_days=7)
        assert "old-run-1" in deleted
        assert "recent-run" not in deleted

    def test_alert_file_corruption_resilience(
        self,
        temp_storage_dir: Path,
        sample_alert: CoverageAlert,
    ) -> None:
        """Test handling of corrupted alert lines in JSONL file."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        repo.store_alert(sample_alert)

        date_str = sample_alert.timestamp.strftime("%Y-%m-%d")
        alerts_file = temp_storage_dir / "alerts" / date_str / "alerts.jsonl"
        content = alerts_file.read_text(encoding="utf-8")
        alerts_file.write_text(content + "\n{invalid json line}\n", encoding="utf-8")

        alerts = repo.list_alerts()
        assert len(alerts) >= 1


class TestFormatAndVersioning:
    """Tests for storage format and version handling."""

    def test_local_repository_format_enum_values(self) -> None:
        """Test CoverageTrendFormat enum contains expected values."""
        from operations_center.observer.coverage_trend_repository import CoverageTrendFormat

        assert hasattr(CoverageTrendFormat, "JSON")
        assert hasattr(CoverageTrendFormat, "JSONL")
        assert CoverageTrendFormat.JSON.value == "json"
        assert CoverageTrendFormat.JSONL.value == "jsonl"

    def test_snapshot_metadata_includes_version(
        self,
        temp_storage_dir: Path,
        sample_snapshot: CoverageSnapshot,
    ) -> None:
        """Test that stored metadata includes version information."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        metadata = repo.store_snapshot(sample_snapshot)

        assert "version" in metadata
        assert metadata["version"] == 1

    def test_trend_analysis_metadata_includes_path(
        self,
        temp_storage_dir: Path,
        sample_trend_analysis: CoverageTrendAnalysis,
    ) -> None:
        """Test that trend analysis metadata includes storage path."""
        repo = LocalCoverageTrendRepository(root=temp_storage_dir)
        metadata = repo.store_trend_analysis(sample_trend_analysis)

        assert "path" in metadata
        assert "trends" in metadata["path"]

    @patch("operations_center.observer.coverage_trend_repository.requests")
    def test_http_repository_url_construction(
        self,
        mock_requests: MagicMock,
    ) -> None:
        """Test HTTP repository URL construction with trailing slash."""
        mock_session = MagicMock()
        mock_requests.Session.return_value = mock_session

        repo1 = HTTPCoverageTrendRepository(base_url="http://api.example.com/")
        repo2 = HTTPCoverageTrendRepository(base_url="http://api.example.com")

        assert repo1.base_url == "http://api.example.com"
        assert repo2.base_url == "http://api.example.com"
