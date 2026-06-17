# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""High-level snapshot management APIs for reading, comparing, and updating snapshots."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from operations_center.observer.models import RepoStateSnapshot
from operations_center.observer.snapshot_repository import (
    HTTPSnapshotRepository,
    LocalSnapshotRepository,
    S3SnapshotRepository,
    SnapshotFormat,
    SnapshotRepository,
)

logger = logging.getLogger(__name__)


class SnapshotManager:
    """High-level API for snapshot management operations."""

    def __init__(
        self,
        repository: SnapshotRepository | None = None,
        root: Path | None = None,
        retention_days: int = 30,
        retention_count: int = 50,
    ):
        if repository is None:
            repository = LocalSnapshotRepository(
                root=root,
                retention_days=retention_days,
                retention_count=retention_count,
            )
        self.repository = repository

    @classmethod
    def create_local(
        cls,
        root: Path | None = None,
        retention_days: int = 30,
        retention_count: int = 50,
    ) -> "SnapshotManager":
        """Create a manager with local file storage."""
        repo = LocalSnapshotRepository(
            root=root,
            retention_days=retention_days,
            retention_count=retention_count,
        )
        return cls(repository=repo)

    @classmethod
    def create_s3(
        cls,
        bucket_name: str,
        prefix: str = "snapshots",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        region_name: str = "us-east-1",
    ) -> "SnapshotManager":
        """Create a manager with S3 storage.

        Args:
            bucket_name: S3 bucket name
            prefix: S3 key prefix for snapshots
            aws_access_key_id: AWS access key (uses env var if not provided)
            aws_secret_access_key: AWS secret key (uses env var if not provided)
            region_name: AWS region

        Returns:
            SnapshotManager with S3 backend

        Raises:
            ImportError: If boto3 is not installed
        """
        repo = S3SnapshotRepository(
            bucket_name=bucket_name,
            prefix=prefix,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )
        return cls(repository=repo)

    @classmethod
    def create_http(
        cls,
        base_url: str,
        auth_token: str | None = None,
        timeout: int = 30,
    ) -> "SnapshotManager":
        """Create a manager with HTTP-based storage.

        Args:
            base_url: Base URL of the remote server
            auth_token: Optional bearer token for authentication
            timeout: Request timeout in seconds

        Returns:
            SnapshotManager with HTTP backend

        Raises:
            ImportError: If requests is not installed
        """
        repo = HTTPSnapshotRepository(
            base_url=base_url,
            auth_token=auth_token,
            timeout=timeout,
        )
        return cls(repository=repo)

    def save_snapshot(
        self,
        snapshot: RepoStateSnapshot,
        format: SnapshotFormat = SnapshotFormat.JSON,
    ) -> dict[str, Any]:
        """Save a snapshot and return metadata."""
        metadata = self.repository.store(snapshot, format)
        logger.info("Saved snapshot %s", snapshot.run_id)
        return dict(metadata)

    def get_snapshot(self, run_id: str) -> RepoStateSnapshot:
        """Load a snapshot by run_id."""
        return self.repository.load(run_id)

    def get_latest_snapshot(self) -> RepoStateSnapshot | None:
        """Get the most recently saved snapshot."""
        snapshots = self.repository.list_snapshots(limit=1)
        if snapshots:
            return self.repository.load(snapshots[0]["run_id"])
        return None

    def get_snapshots(self, limit: int | None = None) -> list[RepoStateSnapshot]:
        """Get snapshots, optionally limited."""
        metadata_list = self.repository.list_snapshots(limit=limit)
        snapshots = []
        for metadata in metadata_list:
            try:
                snapshot = self.repository.load(metadata["run_id"])
                snapshots.append(snapshot)
            except Exception as e:
                logger.warning("Failed to load snapshot %s: %s", metadata["run_id"], e)
        return snapshots

    def compare_snapshots(self, run_id1: str, run_id2: str) -> SnapshotComparison:
        """Compare two snapshots and return a structured comparison."""
        diff_data = self.repository.compare(run_id1, run_id2)
        snap1 = self.repository.load(run_id1)
        snap2 = self.repository.load(run_id2)
        return SnapshotComparison(snap1, snap2, diff_data)

    def delete_snapshot(self, run_id: str) -> bool:
        """Delete a snapshot."""
        return self.repository.delete(run_id)

    def cleanup_old_snapshots(self) -> list[str]:
        """Remove old snapshots based on retention policy."""
        deleted = self.repository.cleanup()
        if deleted:
            logger.info("Cleaned up %d old snapshots", len(deleted))
        return deleted

    def get_snapshot_by_date(self, target_date: datetime) -> RepoStateSnapshot | None:
        """Get the snapshot closest to a target date."""
        snapshots = self.repository.list_snapshots()
        if not snapshots:
            return None

        closest = None
        min_diff = timedelta.max

        for metadata in snapshots:
            observed_at = datetime.fromisoformat(metadata["observed_at"])
            diff = abs((observed_at - target_date).total_seconds())
            if diff < min_diff.total_seconds():
                min_diff = timedelta(seconds=diff)
                closest = metadata

        if closest:
            return self.repository.load(closest["run_id"])
        return None

    def export_snapshot(
        self, run_id: str, output_path: Path, format: SnapshotFormat = SnapshotFormat.JSON
    ) -> None:
        """Export a snapshot to a file in the specified format."""
        snapshot = self.repository.load(run_id)
        content = self._serialize_snapshot(snapshot, format)
        output_path.write_text(content, encoding="utf-8")
        logger.info("Exported snapshot %s to %s", run_id, output_path)

    def _serialize_snapshot(self, snapshot: RepoStateSnapshot, format: SnapshotFormat) -> str:
        """Serialize a snapshot to string."""
        if format == SnapshotFormat.JSON:
            return snapshot.model_dump_json(indent=2)
        elif format == SnapshotFormat.JSONL:
            return snapshot.model_dump_json()
        elif format == SnapshotFormat.YAML:
            import yaml

            data = snapshot.model_dump()
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
        else:
            raise ValueError(f"Unsupported format: {format}")


class SnapshotComparison:
    """Structured comparison of two snapshots."""

    def __init__(
        self,
        snapshot1: RepoStateSnapshot,
        snapshot2: RepoStateSnapshot,
        diff_data: dict[str, dict[str, Any]],
    ):
        self.snapshot1 = snapshot1
        self.snapshot2 = snapshot2
        self.diff_data = diff_data

    def get_signal_changes(self) -> dict[str, dict[str, Any]]:
        """Get signal-level changes."""
        return self.diff_data.get("signals", {})

    def get_repo_changes(self) -> dict[str, dict[str, Any]]:
        """Get repository context changes."""
        return self.diff_data.get("repo", {})

    def has_changes(self) -> bool:
        """Check if there are any differences."""
        return bool(self.diff_data)

    def to_dict(self) -> dict[str, Any]:
        """Convert comparison to dictionary format."""
        return {
            "snapshot1_id": self.snapshot1.run_id,
            "snapshot2_id": self.snapshot2.run_id,
            "observed_at_1": self.snapshot1.observed_at.isoformat(),
            "observed_at_2": self.snapshot2.observed_at.isoformat(),
            "differences": self.diff_data,
            "has_changes": self.has_changes(),
        }
