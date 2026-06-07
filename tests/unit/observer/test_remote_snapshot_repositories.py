# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for remote snapshot repository implementations (S3, HTTP)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from operations_center.observer.models import (
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    CheckSignal,
    DependencyDriftSignal,
    TodoSignal,
)
from operations_center.observer.snapshot_repository import (
    HTTPSnapshotRepository,
    S3SnapshotRepository,
    SnapshotFormat,
)


@pytest.fixture
def test_snapshot() -> RepoStateSnapshot:
    """Create a test snapshot."""
    return RepoStateSnapshot(
        run_id="test_obs_20260607T120000Z_abc123_x7k9m",
        observed_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc),
        observer_version=1,
        source_command="test-command",
        repo=RepoContextSnapshot(
            name="test-repo",
            path=Path("/tmp/test"),
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(
                status="passing",
                test_count=100,
                source="pytest",
            ),
            dependency_drift=DependencyDriftSignal(
                status="healthy",
                source="pip-audit",
            ),
            todo_signal=TodoSignal(
                todo_count=5,
                fixme_count=2,
            ),
        ),
    )


class TestS3SnapshotRepository:
    """Tests for S3SnapshotRepository."""

    def test_s3_import_error_without_boto3(self) -> None:
        """Test that S3Repository raises ImportError if boto3 not available."""
        import operations_center.observer.snapshot_repository as sr

        original_boto3 = sr.boto3
        try:
            sr.boto3 = None
            with pytest.raises(ImportError, match="boto3 is required"):
                S3SnapshotRepository(bucket_name="test")
        finally:
            sr.boto3 = original_boto3

    def test_s3_store_snapshot_json(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test storing a snapshot in S3 with JSON format."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            # Mock boto3 module
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            repo = S3SnapshotRepository(bucket_name="test-bucket")
            metadata = repo.store(test_snapshot, SnapshotFormat.JSON)

            assert metadata["run_id"] == test_snapshot.run_id
            assert metadata["format"] == "json"
            assert "checksum" in metadata

            # Verify S3 put_object was called multiple times (snapshot + index)
            assert mock_client.put_object.call_count >= 1
            # Check that at least one call contains the snapshot run_id
            calls = mock_client.put_object.call_args_list
            keys = [call[1]["Key"] for call in calls]
            assert any(test_snapshot.run_id in key for key in keys)
        finally:
            sr.boto3 = original_boto3

    def test_s3_store_snapshot_yaml(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test storing a snapshot in S3 with YAML format."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            repo = S3SnapshotRepository(bucket_name="test-bucket")
            metadata = repo.store(test_snapshot, SnapshotFormat.YAML)

            assert metadata["format"] == "yaml"
            mock_client.put_object.assert_called()
        finally:
            sr.boto3 = original_boto3

    def test_s3_load_snapshot(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test loading a snapshot from S3."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            # Mock get_object to return JSON content
            json_content = test_snapshot.model_dump_json()
            mock_client.get_object.return_value = {
                "Body": mock.MagicMock(read=mock.MagicMock(return_value=json_content.encode()))
            }

            repo = S3SnapshotRepository(bucket_name="test-bucket")
            loaded = repo.load(test_snapshot.run_id)

            assert loaded.run_id == test_snapshot.run_id
            assert loaded.observer_version == 1
        finally:
            sr.boto3 = original_boto3

    def test_s3_load_snapshot_not_found(self) -> None:
        """Test loading non-existent snapshot raises FileNotFoundError."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            mock_client.get_object.side_effect = Exception("NoSuchKey")

            repo = S3SnapshotRepository(bucket_name="test-bucket")

            with pytest.raises(FileNotFoundError, match="not found in S3"):
                repo.load("nonexistent-run-id")
        finally:
            sr.boto3 = original_boto3

    def test_s3_list_snapshots(self) -> None:
        """Test listing snapshots from S3 index."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            # Mock index file
            index_content = (
                '{"run_id": "snap1", "observed_at": "2026-06-07T12:00:00+00:00"}\n'
                '{"run_id": "snap2", "observed_at": "2026-06-07T13:00:00+00:00"}\n'
            )
            mock_client.get_object.return_value = {
                "Body": mock.MagicMock(read=mock.MagicMock(return_value=index_content.encode()))
            }

            repo = S3SnapshotRepository(bucket_name="test-bucket")
            snapshots = repo.list_snapshots()

            assert len(snapshots) == 2
            assert snapshots[0]["run_id"] == "snap1"
            assert snapshots[1]["run_id"] == "snap2"
        finally:
            sr.boto3 = original_boto3

    def test_s3_delete_snapshot(self) -> None:
        """Test deleting a snapshot from S3."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            repo = S3SnapshotRepository(bucket_name="test-bucket")
            result = repo.delete("test-run-id")

            assert result is True
            mock_client.delete_object.assert_called()
        finally:
            sr.boto3 = original_boto3

    def test_s3_compare_snapshots(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test comparing two snapshots from S3."""
        import operations_center.observer.snapshot_repository as sr

        mock_client = mock.MagicMock()
        original_boto3 = sr.boto3

        try:
            mock_boto3 = mock.MagicMock()
            mock_boto3.client.return_value = mock_client
            sr.boto3 = mock_boto3

            # Create a second snapshot with different branch
            snap2_data = test_snapshot.model_dump()
            snap2_data["repo"]["current_branch"] = "develop"
            snap2 = RepoStateSnapshot(**snap2_data)

            json_content1 = test_snapshot.model_dump_json()
            json_content2 = snap2.model_dump_json()

            # Mock get_object to return different content
            mock_client.get_object.side_effect = [
                {"Body": mock.MagicMock(read=mock.MagicMock(return_value=json_content1.encode()))},
                {"Body": mock.MagicMock(read=mock.MagicMock(return_value=json_content2.encode()))},
            ]

            repo = S3SnapshotRepository(bucket_name="test-bucket")
            diff = repo.compare("snap1", "snap2")

            assert "repo" in diff
            assert "current_branch" in diff["repo"]
        finally:
            sr.boto3 = original_boto3


class TestHTTPSnapshotRepository:
    """Tests for HTTPSnapshotRepository."""

    def test_http_import_error_without_requests(self) -> None:
        """Test that HTTPRepository raises ImportError if requests not available."""
        import operations_center.observer.snapshot_repository as sr

        original_requests = sr.requests
        try:
            sr.requests = None
            with pytest.raises(ImportError, match="requests is required"):
                HTTPSnapshotRepository(base_url="https://example.com")
        finally:
            sr.requests = original_requests

    def test_http_store_snapshot_json(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test storing a snapshot via HTTP with JSON format."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 201
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.put.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")
            metadata = repo.store(test_snapshot, SnapshotFormat.JSON)

            assert metadata["run_id"] == test_snapshot.run_id
            assert metadata["format"] == "json"
            assert "checksum" in metadata

            # Verify request was made
            mock_requests.put.assert_called_once()
            call_args = mock_requests.put.call_args
            assert "snapshots" in call_args[0][0]
        finally:
            sr.requests = original_requests

    def test_http_store_snapshot_with_auth(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test storing a snapshot via HTTP with authentication."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 201
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.put.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(
                base_url="https://snapshots.example.com", auth_token="secret-token"
            )
            repo.store(test_snapshot, SnapshotFormat.JSON)

            # Verify auth header was included
            call_kwargs = mock_requests.put.call_args[1]
            assert "Authorization" in call_kwargs["headers"]
            assert "Bearer secret-token" in call_kwargs["headers"]["Authorization"]
        finally:
            sr.requests = original_requests

    def test_http_store_snapshot_failure(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test handling of HTTP store failure."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.put.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")

            with pytest.raises(RuntimeError, match="Failed to store snapshot"):
                repo.store(test_snapshot, SnapshotFormat.JSON)
        finally:
            sr.requests = original_requests

    def test_http_load_snapshot(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test loading a snapshot via HTTP."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.text = test_snapshot.model_dump_json()
        mock_response.headers = {"Content-Type": "application/json"}
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")
            loaded = repo.load(test_snapshot.run_id)

            assert loaded.run_id == test_snapshot.run_id
            assert loaded.observer_version == 1
        finally:
            sr.requests = original_requests

    def test_http_load_snapshot_not_found(self) -> None:
        """Test loading non-existent snapshot via HTTP."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 404
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")

            with pytest.raises(FileNotFoundError, match="not found"):
                repo.load("nonexistent-run-id")
        finally:
            sr.requests = original_requests

    def test_http_load_snapshot_with_auth(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test loading a snapshot via HTTP with authentication."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.text = test_snapshot.model_dump_json()
        mock_response.headers = {"Content-Type": "application/json"}
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(
                base_url="https://snapshots.example.com", auth_token="secret-token"
            )
            repo.load(test_snapshot.run_id)

            # Verify auth header was included
            call_kwargs = mock_requests.get.call_args[1]
            assert "Authorization" in call_kwargs["headers"]
            assert "Bearer secret-token" in call_kwargs["headers"]["Authorization"]
        finally:
            sr.requests = original_requests

    def test_http_load_snapshot_server_error(self) -> None:
        """Test handling of HTTP load server error."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")

            with pytest.raises(RuntimeError, match="Failed to load snapshot"):
                repo.load("test-run-id")
        finally:
            sr.requests = original_requests

    def test_http_list_snapshots(self) -> None:
        """Test listing snapshots via HTTP."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"run_id": "snap1", "observed_at": "2026-06-07T12:00:00+00:00"},
            {"run_id": "snap2", "observed_at": "2026-06-07T13:00:00+00:00"},
        ]
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")
            snapshots = repo.list_snapshots()

            assert len(snapshots) == 2
            assert snapshots[0]["run_id"] == "snap1"
        finally:
            sr.requests = original_requests

    def test_http_delete_snapshot(self) -> None:
        """Test deleting a snapshot via HTTP."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 204
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.delete.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")
            result = repo.delete("test-run-id")

            assert result is True
            mock_requests.delete.assert_called_once()
        finally:
            sr.requests = original_requests

    def test_http_delete_snapshot_not_found(self) -> None:
        """Test deleting non-existent snapshot via HTTP."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 404
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.delete.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")
            result = repo.delete("nonexistent-run-id")

            assert result is False
        finally:
            sr.requests = original_requests

    def test_http_delete_snapshot_failure(self) -> None:
        """Test handling of HTTP delete failure."""
        import operations_center.observer.snapshot_repository as sr

        mock_response = mock.MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            mock_requests.delete.return_value = mock_response
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")

            with pytest.raises(RuntimeError, match="Failed to delete snapshot"):
                repo.delete("test-run-id")
        finally:
            sr.requests = original_requests

    def test_http_compare_snapshots(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test comparing two snapshots via HTTP."""
        import operations_center.observer.snapshot_repository as sr

        # Create a second snapshot with different branch
        snap2_data = test_snapshot.model_dump()
        snap2_data["repo"]["current_branch"] = "develop"
        snap2 = RepoStateSnapshot(**snap2_data)
        original_requests = sr.requests

        try:
            mock_requests = mock.MagicMock()
            responses = [
                mock.MagicMock(
                    status_code=200,
                    text=test_snapshot.model_dump_json(),
                    headers={"Content-Type": "application/json"},
                ),
                mock.MagicMock(
                    status_code=200,
                    text=snap2.model_dump_json(),
                    headers={"Content-Type": "application/json"},
                ),
            ]
            mock_requests.get.side_effect = responses
            sr.requests = mock_requests

            repo = HTTPSnapshotRepository(base_url="https://snapshots.example.com")
            diff = repo.compare("snap1", "snap2")

            assert "repo" in diff
            assert "current_branch" in diff["repo"]
        finally:
            sr.requests = original_requests
