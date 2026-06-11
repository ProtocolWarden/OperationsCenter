# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Abstract repository interface and implementations for snapshot storage and retrieval."""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from operations_center.observer.models import RepoStateSnapshot

logger = logging.getLogger(__name__)

# Optional imports for remote backends
if TYPE_CHECKING:
    import boto3  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]
    import requests  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]
else:
    try:
        import boto3
    except ImportError:
        boto3 = None  # type: ignore[assignment,no-redef]

    try:
        import requests
    except ImportError:
        requests = None  # type: ignore[assignment,no-redef]


class SnapshotFormat(str, Enum):
    """Supported snapshot storage formats."""

    JSON = "json"
    JSONL = "jsonl"
    YAML = "yaml"


class SnapshotMetadata(dict[str, Any]):
    """Metadata about a stored snapshot."""

    def __init__(
        self,
        run_id: str,
        observed_at: datetime,
        format: SnapshotFormat,
        version: int,
        path: Path | None = None,
        checksum: str | None = None,
    ):
        super().__init__()
        self["run_id"] = run_id
        self["observed_at"] = observed_at.isoformat()
        self["format"] = format.value
        self["version"] = version
        if path:
            self["path"] = str(path)
        if checksum:
            self["checksum"] = checksum


class SnapshotRepository(ABC):
    """Abstract base class for snapshot storage backends."""

    @abstractmethod
    def store(
        self,
        snapshot: RepoStateSnapshot,
        format: SnapshotFormat = SnapshotFormat.JSON,
    ) -> SnapshotMetadata:
        """Store a snapshot and return its metadata."""
        pass

    @abstractmethod
    def load(self, run_id: str) -> RepoStateSnapshot:
        """Load a snapshot by run_id."""
        pass

    @abstractmethod
    def list_snapshots(self, limit: int | None = None) -> list[SnapshotMetadata]:
        """List all stored snapshots, optionally limited."""
        pass

    @abstractmethod
    def delete(self, run_id: str) -> bool:
        """Delete a snapshot. Return True if deleted, False if not found."""
        pass

    @abstractmethod
    def compare(self, run_id1: str, run_id2: str) -> dict[str, dict[str, Any]]:
        """Compare two snapshots and return a diff."""
        pass

    @abstractmethod
    def cleanup(self) -> list[str]:
        """Clean up old snapshots based on retention policy. Return deleted run_ids."""
        pass


class LocalSnapshotRepository(SnapshotRepository):
    """Local filesystem-based snapshot repository with rotation and retention."""

    def __init__(
        self,
        root: Path | None = None,
        retention_days: int = 30,
        retention_count: int = 50,
        default_format: SnapshotFormat = SnapshotFormat.JSON,
    ):
        self.root = root or Path("tools/report/operations_center/observer")
        self.retention_days = retention_days
        self.retention_count = retention_count
        self.default_format = default_format
        self.root.mkdir(parents=True, exist_ok=True)

    def store(
        self,
        snapshot: RepoStateSnapshot,
        format: SnapshotFormat = SnapshotFormat.JSON,
    ) -> SnapshotMetadata:
        """Store a snapshot in the specified format."""
        if format is None:
            format = self.default_format

        run_dir = self.root / snapshot.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        content = self._serialize_snapshot(snapshot, format)
        extension = format.value
        file_path = run_dir / f"snapshot.{extension}"

        file_path.write_text(content, encoding="utf-8")

        checksum = hashlib.sha256(content.encode()).hexdigest()

        metadata = SnapshotMetadata(
            run_id=snapshot.run_id,
            observed_at=snapshot.observed_at,
            format=format,
            version=snapshot.observer_version,
            path=file_path,
            checksum=checksum,
        )

        self._update_index(metadata)
        logger.info(
            "Stored snapshot %s in %s format",
            snapshot.run_id,
            extension.upper(),
        )

        return metadata

    def load(self, run_id: str) -> RepoStateSnapshot:
        """Load a snapshot from storage."""
        run_dir = self.root / run_id

        if not run_dir.exists():
            raise FileNotFoundError(f"Snapshot {run_id} not found")

        # Try each format in order of preference
        for format in [SnapshotFormat.JSON, SnapshotFormat.JSONL, SnapshotFormat.YAML]:
            file_path = run_dir / f"snapshot.{format.value}"
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                return self._deserialize_snapshot(content, format)

        raise FileNotFoundError(f"No snapshot file found for {run_id}")

    def list_snapshots(self, limit: int | None = None) -> list[SnapshotMetadata]:
        """List all stored snapshots, optionally limited to most recent."""
        snapshots = []

        for run_dir in sorted(self.root.iterdir(), reverse=True):
            if not run_dir.is_dir() or run_dir.name == ".gitkeep":
                continue

            try:
                snapshot = self.load(run_dir.name)
                snapshots.append(
                    SnapshotMetadata(
                        run_id=snapshot.run_id,
                        observed_at=snapshot.observed_at,
                        format=self.default_format,
                        version=snapshot.observer_version,
                        path=run_dir / f"snapshot.{self.default_format.value}",
                    )
                )
            except (FileNotFoundError, Exception) as e:
                logger.warning("Failed to load snapshot %s: %s", run_dir.name, e)

        if limit:
            snapshots = snapshots[:limit]

        return snapshots

    def delete(self, run_id: str) -> bool:
        """Delete a snapshot directory and its contents."""
        run_dir = self.root / run_id

        if not run_dir.exists():
            return False

        import shutil

        shutil.rmtree(run_dir)
        logger.info("Deleted snapshot %s", run_id)
        return True

    def compare(self, run_id1: str, run_id2: str) -> dict[str, dict[str, Any]]:
        """Compare two snapshots and return differences."""
        snap1 = self.load(run_id1)
        snap2 = self.load(run_id2)

        return _compute_snapshot_diff(snap1, snap2)

    def cleanup(self) -> list[str]:
        """Clean up old snapshots based on retention policy."""
        snapshots = self.list_snapshots()

        if not snapshots:
            return []

        # Keep the most recent N snapshots
        to_delete = []
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(
            days=self.retention_days
        )

        for i, snapshot in enumerate(snapshots):
            if i >= self.retention_count:
                to_delete.append(snapshot["run_id"])
            elif datetime.fromisoformat(snapshot["observed_at"]) < cutoff_date:
                to_delete.append(snapshot["run_id"])

        for run_id in to_delete:
            self.delete(run_id)

        return to_delete

    def _serialize_snapshot(self, snapshot: RepoStateSnapshot, format: SnapshotFormat) -> str:
        """Serialize snapshot to string in the specified format."""
        if format == SnapshotFormat.JSON:
            return snapshot.model_dump_json(indent=2)
        elif format == SnapshotFormat.JSONL:
            # JSONL format: one JSON object per line (for streaming)
            return snapshot.model_dump_json()
        elif format == SnapshotFormat.YAML:
            data = snapshot.model_dump()
            # Convert Path objects to strings for YAML serialization
            data = self._convert_paths_to_strings(data)
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _convert_paths_to_strings(self, obj: Any) -> Any:
        """Recursively convert Path objects to strings."""
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_paths_to_strings(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_paths_to_strings(item) for item in obj]
        return obj

    def _deserialize_snapshot(self, content: str, format: SnapshotFormat) -> RepoStateSnapshot:
        """Deserialize snapshot from string."""
        if format == SnapshotFormat.JSON:
            return RepoStateSnapshot.model_validate_json(content)
        elif format == SnapshotFormat.JSONL:
            return RepoStateSnapshot.model_validate_json(content)
        elif format == SnapshotFormat.YAML:
            data = yaml.safe_load(content)
            return RepoStateSnapshot.model_validate(data)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _update_index(self, metadata: SnapshotMetadata) -> None:
        """Update the snapshot index file (JSONL)."""
        index_path = self.root / "snapshots.index"

        existing = {}
        if index_path.exists():
            for line in index_path.read_text(encoding="utf-8").strip().split("\n"):
                if line:
                    entry = json.loads(line)
                    existing[entry["run_id"]] = entry

        existing[metadata["run_id"]] = dict(metadata)

        with index_path.open("w") as f:
            for run_id in sorted(existing.keys()):
                f.write(json.dumps(existing[run_id], ensure_ascii=False) + "\n")


class S3SnapshotRepository(SnapshotRepository):
    """AWS S3-based snapshot repository for cloud storage."""

    def __init__(
        self,
        bucket_name: str,
        prefix: str = "snapshots",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        region_name: str = "us-east-1",
        default_format: SnapshotFormat = SnapshotFormat.JSON,
    ):
        """Initialize S3 repository.

        Args:
            bucket_name: S3 bucket name
            prefix: S3 key prefix for snapshots
            aws_access_key_id: AWS access key (uses env var if not provided)
            aws_secret_access_key: AWS secret key (uses env var if not provided)
            region_name: AWS region
            default_format: Default serialization format
        """
        if boto3 is None:
            raise ImportError("boto3 is required for S3 support. Install with: pip install boto3")

        self.bucket_name = bucket_name
        self.prefix = prefix
        self.default_format = default_format

        self.s3_client = boto3.client(
            "s3",
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    def store(
        self,
        snapshot: RepoStateSnapshot,
        format: SnapshotFormat = SnapshotFormat.JSON,
    ) -> SnapshotMetadata:
        """Store snapshot in S3."""
        if format is None:
            format = self.default_format

        content = self._serialize_snapshot(snapshot, format)
        extension = format.value
        key = f"{self.prefix}/{snapshot.run_id}/snapshot.{extension}"

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="application/json" if format != SnapshotFormat.YAML else "text/yaml",
        )

        # Store index entry
        self._update_index(snapshot, format)

        checksum = hashlib.sha256(content.encode()).hexdigest()

        metadata = SnapshotMetadata(
            run_id=snapshot.run_id,
            observed_at=snapshot.observed_at,
            format=format,
            version=snapshot.observer_version,
            checksum=checksum,
        )

        logger.info("Stored snapshot %s in S3 (bucket=%s)", snapshot.run_id, self.bucket_name)
        return metadata

    def load(self, run_id: str) -> RepoStateSnapshot:
        """Load snapshot from S3."""
        for format in [SnapshotFormat.JSON, SnapshotFormat.JSONL, SnapshotFormat.YAML]:
            key = f"{self.prefix}/{run_id}/snapshot.{format.value}"
            try:
                response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
                content = response["Body"].read().decode("utf-8")
                return self._deserialize_snapshot(content, format)
            except Exception:
                continue

        raise FileNotFoundError(f"Snapshot {run_id} not found in S3")

    def list_snapshots(self, limit: int | None = None) -> list[SnapshotMetadata]:
        """List snapshots from S3 index."""
        snapshots = []
        index_key = f"{self.prefix}/snapshots.index"

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=index_key)
            content = response["Body"].read().decode("utf-8")

            for line in content.strip().split("\n"):
                if line:
                    entry = json.loads(line)
                    snapshots.append(entry)
        except Exception:
            pass

        if limit:
            snapshots = snapshots[-limit:]

        return snapshots

    def delete(self, run_id: str) -> bool:
        """Delete snapshot from S3."""
        deleted = False

        for format in [SnapshotFormat.JSON, SnapshotFormat.JSONL, SnapshotFormat.YAML]:
            key = f"{self.prefix}/{run_id}/snapshot.{format.value}"
            try:
                self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
                deleted = True
            except Exception:
                pass

        if deleted:
            logger.info("Deleted snapshot %s from S3", run_id)

        return deleted

    def compare(self, run_id1: str, run_id2: str) -> dict[str, dict[str, Any]]:
        """Compare two snapshots from S3."""
        snap1 = self.load(run_id1)
        snap2 = self.load(run_id2)
        return _compute_snapshot_diff(snap1, snap2)

    def cleanup(self) -> list[str]:
        """Clean up old snapshots in S3 based on retention policy."""
        snapshots = self.list_snapshots()

        if not snapshots:
            return []

        to_delete = []
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=30)

        # Keep most recent N snapshots and delete old ones
        for i, snapshot in enumerate(snapshots):
            if i < len(snapshots) - 50:  # Keep last 50
                to_delete.append(snapshot.get("run_id", ""))
            elif "observed_at" in snapshot:
                try:
                    observed = datetime.fromisoformat(snapshot["observed_at"])
                    if observed < cutoff_date:
                        to_delete.append(snapshot.get("run_id", ""))
                except Exception:
                    pass

        for run_id in to_delete:
            if run_id:
                self.delete(run_id)

        return to_delete

    def _serialize_snapshot(self, snapshot: RepoStateSnapshot, format: SnapshotFormat) -> str:
        """Serialize snapshot to string."""
        if format == SnapshotFormat.JSON:
            return snapshot.model_dump_json(indent=2)
        elif format == SnapshotFormat.JSONL:
            return snapshot.model_dump_json()
        elif format == SnapshotFormat.YAML:
            data = snapshot.model_dump()
            data = self._convert_paths_to_strings(data)
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _deserialize_snapshot(self, content: str, format: SnapshotFormat) -> RepoStateSnapshot:
        """Deserialize snapshot from string."""
        if format == SnapshotFormat.JSON:
            return RepoStateSnapshot.model_validate_json(content)
        elif format == SnapshotFormat.JSONL:
            return RepoStateSnapshot.model_validate_json(content)
        elif format == SnapshotFormat.YAML:
            data = yaml.safe_load(content)
            return RepoStateSnapshot.model_validate(data)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _convert_paths_to_strings(self, obj: Any) -> Any:
        """Recursively convert Path objects to strings."""
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_paths_to_strings(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_paths_to_strings(item) for item in obj]
        return obj

    def _update_index(self, snapshot: RepoStateSnapshot, format: SnapshotFormat) -> None:
        """Update the snapshot index in S3."""
        index_key = f"{self.prefix}/snapshots.index"

        existing = {}
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=index_key)
            content = response["Body"].read().decode("utf-8")
            for line in content.strip().split("\n"):
                if line:
                    entry = json.loads(line)
                    existing[entry["run_id"]] = entry
        except Exception:
            pass

        existing[snapshot.run_id] = {
            "run_id": snapshot.run_id,
            "observed_at": snapshot.observed_at.isoformat(),
            "format": format.value,
            "version": snapshot.observer_version,
        }

        index_content = "\n".join(
            json.dumps(existing[run_id], ensure_ascii=False) for run_id in sorted(existing.keys())
        )

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=index_key,
            Body=index_content.encode("utf-8"),
            ContentType="text/plain",
        )


class HTTPSnapshotRepository(SnapshotRepository):
    """HTTP-based snapshot repository for generic remote backends."""

    def __init__(
        self,
        base_url: str,
        auth_token: str | None = None,
        default_format: SnapshotFormat = SnapshotFormat.JSON,
        timeout: int = 30,
    ):
        """Initialize HTTP repository.

        Args:
            base_url: Base URL of the remote server (e.g., https://snapshots.example.com)
            auth_token: Optional bearer token for authentication
            default_format: Default serialization format
            timeout: Request timeout in seconds
        """
        if requests is None:
            raise ImportError(
                "requests is required for HTTP support. Install with: pip install requests"
            )

        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.default_format = default_format
        self.timeout = timeout

    def store(
        self,
        snapshot: RepoStateSnapshot,
        format: SnapshotFormat = SnapshotFormat.JSON,
    ) -> SnapshotMetadata:
        """Store snapshot via HTTP."""
        if format is None:
            format = self.default_format

        content = self._serialize_snapshot(snapshot, format)

        url = f"{self.base_url}/snapshots/{snapshot.run_id}"
        headers = {
            "Content-Type": "application/json" if format != SnapshotFormat.YAML else "text/yaml",
        }

        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        response = requests.put(
            url,
            data=content.encode("utf-8"),
            headers=headers,
            timeout=self.timeout,
        )

        if response.status_code not in (200, 201, 204):
            raise RuntimeError(f"Failed to store snapshot: {response.status_code} {response.text}")

        checksum = hashlib.sha256(content.encode()).hexdigest()

        metadata = SnapshotMetadata(
            run_id=snapshot.run_id,
            observed_at=snapshot.observed_at,
            format=format,
            version=snapshot.observer_version,
            checksum=checksum,
        )

        logger.info("Stored snapshot %s via HTTP", snapshot.run_id)
        return metadata

    def load(self, run_id: str) -> RepoStateSnapshot:
        """Load snapshot via HTTP."""
        url = f"{self.base_url}/snapshots/{run_id}"
        headers = {}

        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        response = requests.get(url, headers=headers, timeout=self.timeout)

        if response.status_code == 404:
            raise FileNotFoundError(f"Snapshot {run_id} not found")

        if response.status_code != 200:
            raise RuntimeError(f"Failed to load snapshot: {response.status_code} {response.text}")

        content = response.text

        # Try to detect format from content-type
        content_type = response.headers.get("Content-Type", "application/json")
        if "yaml" in content_type:
            format = SnapshotFormat.YAML
        elif "jsonl" in content_type:
            format = SnapshotFormat.JSONL
        else:
            format = SnapshotFormat.JSON

        return self._deserialize_snapshot(content, format)

    def list_snapshots(self, limit: int | None = None) -> list[SnapshotMetadata]:
        """List snapshots via HTTP."""
        url = f"{self.base_url}/snapshots"
        headers = {}

        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        params = {}
        if limit:
            params["limit"] = limit

        response = requests.get(url, headers=headers, params=params, timeout=self.timeout)

        if response.status_code != 200:
            return []

        try:
            data = response.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "snapshots" in data:
                return data["snapshots"]
        except Exception:
            pass

        return []

    def delete(self, run_id: str) -> bool:
        """Delete snapshot via HTTP."""
        url = f"{self.base_url}/snapshots/{run_id}"
        headers = {}

        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        response = requests.delete(url, headers=headers, timeout=self.timeout)

        if response.status_code == 404:
            return False

        if response.status_code not in (200, 204):
            raise RuntimeError(f"Failed to delete snapshot: {response.status_code} {response.text}")

        logger.info("Deleted snapshot %s via HTTP", run_id)
        return True

    def compare(self, run_id1: str, run_id2: str) -> dict[str, dict[str, Any]]:
        """Compare two snapshots via HTTP."""
        snap1 = self.load(run_id1)
        snap2 = self.load(run_id2)
        return _compute_snapshot_diff(snap1, snap2)

    def cleanup(self) -> list[str]:
        """Clean up old snapshots via HTTP."""
        snapshots = self.list_snapshots()

        if not snapshots:
            return []

        to_delete = []
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=30)

        for i, snapshot in enumerate(snapshots):
            if i < len(snapshots) - 50:  # Keep last 50
                to_delete.append(snapshot.get("run_id", ""))
            elif "observed_at" in snapshot:
                try:
                    observed = datetime.fromisoformat(snapshot["observed_at"])
                    if observed < cutoff_date:
                        to_delete.append(snapshot.get("run_id", ""))
                except Exception:
                    pass

        for run_id in to_delete:
            if run_id:
                try:
                    self.delete(run_id)
                except Exception:
                    pass

        return to_delete

    def _serialize_snapshot(self, snapshot: RepoStateSnapshot, format: SnapshotFormat) -> str:
        """Serialize snapshot to string."""
        if format == SnapshotFormat.JSON:
            return snapshot.model_dump_json(indent=2)
        elif format == SnapshotFormat.JSONL:
            return snapshot.model_dump_json()
        elif format == SnapshotFormat.YAML:
            data = snapshot.model_dump()
            data = self._convert_paths_to_strings(data)
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _deserialize_snapshot(self, content: str, format: SnapshotFormat) -> RepoStateSnapshot:
        """Deserialize snapshot from string."""
        if format == SnapshotFormat.JSON:
            return RepoStateSnapshot.model_validate_json(content)
        elif format == SnapshotFormat.JSONL:
            return RepoStateSnapshot.model_validate_json(content)
        elif format == SnapshotFormat.YAML:
            data = yaml.safe_load(content)
            return RepoStateSnapshot.model_validate(data)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _convert_paths_to_strings(self, obj: Any) -> Any:
        """Recursively convert Path objects to strings."""
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_paths_to_strings(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_paths_to_strings(item) for item in obj]
        return obj


def _compute_snapshot_diff(
    snapshot1: RepoStateSnapshot, snapshot2: RepoStateSnapshot
) -> dict[str, dict[str, Any]]:
    """Compute differences between two snapshots."""
    diff: dict[str, Any] = {}

    # Compare repo context
    repo_diff: dict[str, Any] = {}
    if snapshot1.repo.current_branch != snapshot2.repo.current_branch:
        repo_diff["current_branch"] = {
            "before": snapshot1.repo.current_branch,
            "after": snapshot2.repo.current_branch,
        }
    if snapshot1.repo.is_dirty != snapshot2.repo.is_dirty:
        repo_diff["is_dirty"] = {
            "before": snapshot1.repo.is_dirty,
            "after": snapshot2.repo.is_dirty,
        }

    if repo_diff:
        diff["repo"] = repo_diff

    # Compare signals
    signals_diff: dict[str, Any] = {}

    # Test signal
    if snapshot1.signals.test_signal.test_count != snapshot2.signals.test_signal.test_count:
        signals_diff["test_count"] = {
            "before": snapshot1.signals.test_signal.test_count,
            "after": snapshot2.signals.test_signal.test_count,
        }

    # Coverage signal
    if (
        snapshot1.signals.coverage_signal.status != "unavailable"
        and snapshot2.signals.coverage_signal.status != "unavailable"
    ):
        cov1 = snapshot1.signals.coverage_signal.total_coverage_pct or 0
        cov2 = snapshot2.signals.coverage_signal.total_coverage_pct or 0
        if cov1 != cov2:
            signals_diff["coverage"] = {"before": cov1, "after": cov2}

    if signals_diff:
        diff["signals"] = signals_diff

    return diff
