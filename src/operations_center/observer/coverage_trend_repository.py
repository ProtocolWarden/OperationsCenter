# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Abstract repository interface and implementations for coverage trend storage and retrieval."""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
)

logger = logging.getLogger(__name__)

# Optional imports for remote backends
try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore[assignment,no-redef]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment,no-redef]


class CoverageTrendFormat(str, Enum):
    """Supported coverage trend storage formats."""

    JSON = "json"
    JSONL = "jsonl"


def _generate_checksum(content: str) -> str:
    """Generate SHA-256 checksum for content.

    Args:
        content: Content to checksum

    Returns:
        Hexadecimal SHA-256 hash
    """
    return hashlib.sha256(content.encode()).hexdigest()


def _create_snapshot_metadata(
    snapshot: CoverageSnapshot,
    path: str,
    checksum: str | None = None,
) -> dict[str, str | int]:
    """Create metadata dictionary for stored snapshot.

    Args:
        snapshot: Snapshot that was stored
        path: Path or URL where snapshot is stored
        checksum: Optional checksum of snapshot content

    Returns:
        Metadata dictionary
    """
    if checksum is None:
        checksum = _generate_checksum(snapshot.model_dump_json())

    return {
        "run_id": snapshot.run_id,
        "observed_at": snapshot.timestamp.isoformat(),
        "version": 1,
        "path": path,
        "checksum": checksum,
    }


def _create_trend_metadata(
    analysis: CoverageTrendAnalysis,
    path: str,
    checksum: str | None = None,
) -> dict[str, str | int]:
    """Create metadata dictionary for stored trend analysis.

    Args:
        analysis: Trend analysis that was stored
        path: Path or URL where analysis is stored
        checksum: Optional checksum of analysis content

    Returns:
        Metadata dictionary
    """
    if checksum is None:
        checksum = _generate_checksum(analysis.model_dump_json())

    return {
        "run_id": f"{analysis.metric_type}_{analysis.granularity}",
        "observed_at": analysis.window_end.isoformat(),
        "version": 1,
        "path": path,
        "checksum": checksum,
    }


def _create_alert_metadata(
    alert: CoverageAlert,
    path: str,
    checksum: str | None = None,
) -> dict[str, str | int]:
    """Create metadata dictionary for stored alert.

    Args:
        alert: Alert that was stored
        path: Path or URL where alert is stored
        checksum: Optional checksum of alert content

    Returns:
        Metadata dictionary
    """
    if checksum is None:
        checksum = _generate_checksum(alert.model_dump_json())

    return {
        "run_id": alert.alert_id,
        "observed_at": alert.timestamp.isoformat(),
        "version": 1,
        "path": path,
        "checksum": checksum,
    }


class CoverageTrendRepository(ABC):
    """Abstract base class for coverage trend storage backends."""

    @abstractmethod
    def store_snapshot(
        self,
        snapshot: CoverageSnapshot,
    ) -> dict[str, str | int]:
        """Store a coverage metrics snapshot and return its metadata."""
        pass

    @abstractmethod
    def load_snapshot(self, run_id: str) -> CoverageSnapshot:
        """Load a snapshot by run_id."""
        pass

    @abstractmethod
    def list_snapshots(
        self,
        limit: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, str | int]]:
        """List snapshots, optionally filtered by date range."""
        pass

    @abstractmethod
    def delete_snapshot(self, run_id: str) -> bool:
        """Delete a snapshot. Return True if deleted, False if not found."""
        pass

    @abstractmethod
    def store_trend_analysis(
        self,
        analysis: CoverageTrendAnalysis,
    ) -> dict[str, str | int]:
        """Store trend analysis data."""
        pass

    @abstractmethod
    def load_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
    ) -> CoverageTrendAnalysis | None:
        """Load trend analysis for a specific scope."""
        pass

    @abstractmethod
    def store_alert(self, alert: CoverageAlert) -> dict[str, str | int]:
        """Store a coverage alert."""
        pass

    @abstractmethod
    def list_alerts(
        self,
        limit: int | None = None,
        severity: str | None = None,
    ) -> list[CoverageAlert]:
        """List recent alerts, optionally filtered by severity."""
        pass

    @abstractmethod
    def cleanup(self, retention_days: int = 30) -> list[str]:
        """Clean up old data based on retention policy. Return deleted run_ids."""
        pass


class LocalCoverageTrendRepository(CoverageTrendRepository):
    """Local filesystem-based coverage trend repository with rotation and retention."""

    def __init__(
        self,
        root: Path | None = None,
        retention_days: int = 30,
        default_format: CoverageTrendFormat = CoverageTrendFormat.JSONL,
    ) -> None:
        self.root = root or Path(".coverage_data")
        self.retention_days = retention_days
        self.default_format = default_format
        self.root.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict[str, Any]] = self._load_index()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        """Load the index of stored snapshots."""
        index_file = self.root / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                return {k: v for k, v in data.items()}
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_index(self) -> None:
        """Save the index of stored snapshots."""
        index_file = self.root / "index.json"
        data = {k: dict(v) if isinstance(v, dict) else v for k, v in self._index.items()}
        json_str = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        index_file.write_text(json_str, encoding="utf-8")

    def store_snapshot(
        self,
        snapshot: CoverageSnapshot,
    ) -> dict[str, str | int]:
        """Store a coverage metrics snapshot."""
        snapshots_dir = self.root / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        run_dir = snapshots_dir / snapshot.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        content = snapshot.model_dump_json(indent=2)
        file_path = run_dir / "snapshot.json"
        file_path.write_text(content, encoding="utf-8")

        checksum = _generate_checksum(content)
        metadata = _create_snapshot_metadata(snapshot, str(file_path), checksum)

        self._index[snapshot.run_id] = metadata
        self._save_index()

        return metadata

    def load_snapshot(self, run_id: str) -> CoverageSnapshot:
        """Load a snapshot by run_id."""
        file_path = self.root / "snapshots" / run_id / "snapshot.json"
        if not file_path.exists():
            raise FileNotFoundError(f"Snapshot not found: {run_id}")

        content = file_path.read_text(encoding="utf-8")
        return CoverageSnapshot.model_validate_json(content)

    def list_snapshots(
        self,
        limit: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, str | int]]:
        """List snapshots, optionally filtered by date range."""
        snapshots: list[dict[str, Any]] = []

        for metadata in self._index.values():
            observed_at_str = metadata.get("observed_at")
            if observed_at_str:
                try:
                    observed_at = datetime.fromisoformat(observed_at_str)
                    if observed_at.tzinfo is None:
                        observed_at = observed_at.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue

                if start_date:
                    start_cmp = (
                        start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
                    )
                    if observed_at < start_cmp:
                        continue
                if end_date:
                    end_cmp = end_date if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)
                    if observed_at > end_cmp:
                        continue

            snapshots.append(metadata)

        snapshots.sort(key=lambda m: m.get("observed_at", ""), reverse=True)

        if limit:
            return snapshots[:limit]
        return snapshots

    def delete_snapshot(self, run_id: str) -> bool:
        """Delete a snapshot."""
        import shutil

        file_path = self.root / "snapshots" / run_id
        if file_path.exists():
            shutil.rmtree(file_path)
            if run_id in self._index:
                del self._index[run_id]
                self._save_index()
            return True
        return False

    def store_trend_analysis(
        self,
        analysis: CoverageTrendAnalysis,
    ) -> dict[str, str | int]:
        """Store trend analysis data."""
        trends_dir = self.root / "trends"
        trends_dir.mkdir(parents=True, exist_ok=True)

        metric_dir = trends_dir / analysis.metric_type
        metric_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{analysis.granularity}_{analysis.scope_id or 'repo'}.jsonl"
        file_path = metric_dir / filename

        content = analysis.model_dump_json()
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content + "\n")

        checksum = _generate_checksum(content)
        return _create_trend_metadata(analysis, str(file_path), checksum)

    def load_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
    ) -> CoverageTrendAnalysis | None:
        """Load the latest trend analysis for a specific scope."""
        filename = f"{granularity}_{scope_id or 'repo'}.jsonl"
        file_path = self.root / "trends" / metric_type / filename

        if not file_path.exists():
            return None

        lines = file_path.read_text(encoding="utf-8").strip().split("\n")
        if not lines:
            return None

        try:
            return CoverageTrendAnalysis.model_validate_json(lines[-1])
        except (json.JSONDecodeError, ValueError):
            return None

    def store_alert(self, alert: CoverageAlert) -> dict[str, str | int]:
        """Store a coverage alert."""
        alerts_dir = self.root / "alerts"
        alerts_dir.mkdir(parents=True, exist_ok=True)

        date_dir = alerts_dir / alert.timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        alerts_file = date_dir / "alerts.jsonl"

        content = alert.model_dump_json()
        with open(alerts_file, "a", encoding="utf-8") as f:
            f.write(content + "\n")

        checksum = _generate_checksum(content)
        return _create_alert_metadata(alert, str(alerts_file), checksum)

    def list_alerts(
        self,
        limit: int | None = None,
        severity: str | None = None,
    ) -> list[CoverageAlert]:
        """List recent alerts, optionally filtered by severity."""
        alerts_dir = self.root / "alerts"
        if not alerts_dir.exists():
            return []

        alerts: list[CoverageAlert] = []
        for date_dir in sorted(alerts_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue

            alerts_file = date_dir / "alerts.jsonl"
            if alerts_file.exists():
                try:
                    for line in alerts_file.read_text(encoding="utf-8").strip().split("\n"):
                        if line:
                            alert = CoverageAlert.model_validate_json(line)
                            if severity and alert.severity != severity:
                                continue
                            alerts.append(alert)

                            if limit and len(alerts) >= limit:
                                return alerts
                except (json.JSONDecodeError, ValueError):
                    continue

        return alerts[:limit] if limit else alerts

    def cleanup(self, retention_days: int = 30) -> list[str]:
        """Clean up old snapshots based on retention policy."""
        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
        deleted = []

        for run_id, metadata in list(self._index.items()):
            observed_at_str = metadata.get("observed_at")
            if observed_at_str:
                try:
                    observed_at = datetime.fromisoformat(str(observed_at_str))
                    if observed_at.tzinfo is None:
                        observed_at = observed_at.replace(tzinfo=timezone.utc)
                    if observed_at < cutoff_date:
                        if self.delete_snapshot(run_id):
                            deleted.append(run_id)
                except (ValueError, TypeError):
                    continue

        return deleted


class S3CoverageTrendRepository(CoverageTrendRepository):
    """S3-based coverage trend repository for cloud storage."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "coverage-trends",
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        if boto3 is None:
            raise ImportError("boto3 is required for S3 storage")

        self.bucket = bucket
        self.prefix = prefix
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def store_snapshot(
        self,
        snapshot: CoverageSnapshot,
    ) -> dict[str, str | int]:
        """Store a coverage metrics snapshot to S3."""
        key = f"{self.prefix}/snapshots/{snapshot.run_id}/snapshot.json"
        content = snapshot.model_dump_json(indent=2)

        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/json",
        )

        checksum = _generate_checksum(content)
        return _create_snapshot_metadata(snapshot, f"s3://{self.bucket}/{key}", checksum)

    def load_snapshot(self, run_id: str) -> CoverageSnapshot:
        """Load a snapshot from S3."""
        key = f"{self.prefix}/snapshots/{run_id}/snapshot.json"
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            return CoverageSnapshot.model_validate_json(content)
        except self.s3_client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"Snapshot not found: {run_id}")

    def list_snapshots(
        self,
        limit: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, str | int]]:
        """List snapshots from S3, optionally filtered by date range."""
        prefix = f"{self.prefix}/snapshots/"
        snapshots: list[dict[str, Any]] = []

        paginator = self.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                if obj["Key"].endswith("snapshot.json"):
                    run_id = obj["Key"].split("/")[-2]
                    observed_at = obj["LastModified"]

                    if start_date and observed_at < start_date:
                        continue
                    if end_date and observed_at > end_date:
                        continue

                    snapshots.append(
                        {
                            "run_id": run_id,
                            "observed_at": observed_at.isoformat(),
                            "version": 1,
                            "path": f"s3://{self.bucket}/{obj['Key']}",
                        }
                    )

        snapshots.sort(key=lambda m: m.get("observed_at", ""), reverse=True)

        if limit:
            return snapshots[:limit]
        return snapshots

    def delete_snapshot(self, run_id: str) -> bool:
        """Delete a snapshot from S3."""
        key = f"{self.prefix}/snapshots/{run_id}/snapshot.json"
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def store_trend_analysis(
        self,
        analysis: CoverageTrendAnalysis,
    ) -> dict[str, str | int]:
        """Store trend analysis data to S3."""
        key = (
            f"{self.prefix}/trends/{analysis.metric_type}/"
            f"{analysis.granularity}_{analysis.scope_id or 'repo'}.jsonl"
        )

        content = analysis.model_dump_json()
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            existing = response["Body"].read().decode("utf-8")
            content = existing + "\n" + content
        except self.s3_client.exceptions.NoSuchKey:
            pass

        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/jsonl",
        )

        checksum = _generate_checksum(content)
        return _create_trend_metadata(analysis, f"s3://{self.bucket}/{key}", checksum)

    def load_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
    ) -> CoverageTrendAnalysis | None:
        """Load the latest trend analysis from S3."""
        key = f"{self.prefix}/trends/{metric_type}/{granularity}_{scope_id or 'repo'}.jsonl"

        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            lines = content.strip().split("\n")
            if lines:
                return CoverageTrendAnalysis.model_validate_json(lines[-1])
        except self.s3_client.exceptions.NoSuchKey:
            pass

        return None

    def store_alert(self, alert: CoverageAlert) -> dict[str, str | int]:
        """Store a coverage alert to S3."""
        date_str = alert.timestamp.strftime("%Y-%m-%d")
        key = f"{self.prefix}/alerts/{date_str}/alerts.jsonl"

        content = alert.model_dump_json()
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            existing = response["Body"].read().decode("utf-8")
            content = existing + "\n" + content
        except self.s3_client.exceptions.NoSuchKey:
            pass

        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/jsonl",
        )

        checksum = _generate_checksum(content)
        return _create_alert_metadata(alert, f"s3://{self.bucket}/{key}", checksum)

    def list_alerts(
        self,
        limit: int | None = None,
        severity: str | None = None,
    ) -> list[CoverageAlert]:
        """List recent alerts from S3, optionally filtered by severity."""
        prefix = f"{self.prefix}/alerts/"
        alerts: list[CoverageAlert] = []

        paginator = self.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            if "Contents" not in page:
                continue

            for obj in sorted(page["Contents"], key=lambda x: x["LastModified"], reverse=True):
                if obj["Key"].endswith("alerts.jsonl"):
                    try:
                        response = self.s3_client.get_object(Bucket=self.bucket, Key=obj["Key"])
                        content = response["Body"].read().decode("utf-8")

                        for line in content.strip().split("\n"):
                            if line:
                                alert = CoverageAlert.model_validate_json(line)
                                if severity and alert.severity != severity:
                                    continue
                                alerts.append(alert)

                                if limit and len(alerts) >= limit:
                                    return alerts
                    except Exception:
                        continue

        return alerts[:limit] if limit else alerts

    def cleanup(self, retention_days: int = 30) -> list[str]:
        """Clean up old snapshots from S3 based on retention policy."""
        utc_now = datetime.now(tz=timezone.utc)
        cutoff_date = (utc_now - timedelta(days=retention_days)).replace(tzinfo=None)
        deleted = []

        prefix = f"{self.prefix}/snapshots/"
        paginator = self.s3_client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                if obj["Key"].endswith("snapshot.json"):
                    if obj["LastModified"].replace(tzinfo=None) < cutoff_date:
                        run_id = obj["Key"].split("/")[-2]
                        if self.delete_snapshot(run_id):
                            deleted.append(run_id)

        return deleted


class HTTPCoverageTrendRepository(CoverageTrendRepository):
    """HTTP-based coverage trend repository for RESTful API backends."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
    ) -> None:
        if requests is None:
            raise ImportError("requests is required for HTTP storage")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})

    def store_snapshot(
        self,
        snapshot: CoverageSnapshot,
    ) -> dict[str, str | int]:
        """Store a coverage metrics snapshot via HTTP."""
        url = f"{self.base_url}/snapshots/{snapshot.run_id}"
        data = snapshot.model_dump_json()

        response = self.session.put(url, data=data, headers={"Content-Type": "application/json"})
        response.raise_for_status()

        checksum = _generate_checksum(data)
        return _create_snapshot_metadata(snapshot, url, checksum)

    def load_snapshot(self, run_id: str) -> CoverageSnapshot:
        """Load a snapshot via HTTP."""
        url = f"{self.base_url}/snapshots/{run_id}"
        response = self.session.get(url)
        response.raise_for_status()

        return CoverageSnapshot.model_validate_json(response.text)

    def list_snapshots(
        self,
        limit: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, str | int]]:
        """List snapshots via HTTP."""
        url = f"{self.base_url}/snapshots"
        params: dict[str, Any] = {}
        if limit:
            params["limit"] = limit
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()

        response = self.session.get(url, params=params)
        response.raise_for_status()

        snapshots: list[dict[str, str | int]] = []
        for item in response.json():
            snapshots.append(item)

        return snapshots

    def delete_snapshot(self, run_id: str) -> bool:
        """Delete a snapshot via HTTP."""
        url = f"{self.base_url}/snapshots/{run_id}"
        try:
            response = self.session.delete(url)
            return response.status_code in (200, 204)
        except Exception:
            return False

    def store_trend_analysis(
        self,
        analysis: CoverageTrendAnalysis,
    ) -> dict[str, str | int]:
        """Store trend analysis data via HTTP."""
        url = (
            f"{self.base_url}/trends/{analysis.metric_type}/"
            f"{analysis.granularity}/{analysis.scope_id or 'repo'}"
        )
        data = analysis.model_dump_json()

        response = self.session.put(url, data=data, headers={"Content-Type": "application/json"})
        response.raise_for_status()

        checksum = _generate_checksum(data)
        return _create_trend_metadata(analysis, url, checksum)

    def load_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
    ) -> CoverageTrendAnalysis | None:
        """Load trend analysis via HTTP."""
        url = f"{self.base_url}/trends/{metric_type}/{granularity}/{scope_id or 'repo'}"

        try:
            response = self.session.get(url)
            response.raise_for_status()
            return CoverageTrendAnalysis.model_validate_json(response.text)
        except Exception:
            return None

    def store_alert(self, alert: CoverageAlert) -> dict[str, str | int]:
        """Store a coverage alert via HTTP."""
        url = f"{self.base_url}/alerts"
        data = alert.model_dump_json()

        response = self.session.post(url, data=data, headers={"Content-Type": "application/json"})
        response.raise_for_status()

        checksum = _generate_checksum(data)
        return _create_alert_metadata(alert, f"{url}/{alert.alert_id}", checksum)

    def list_alerts(
        self,
        limit: int | None = None,
        severity: str | None = None,
    ) -> list[CoverageAlert]:
        """List recent alerts via HTTP."""
        url = f"{self.base_url}/alerts"
        params: dict[str, Any] = {}
        if limit:
            params["limit"] = limit
        if severity:
            params["severity"] = severity

        response = self.session.get(url, params=params)
        response.raise_for_status()

        alerts: list[CoverageAlert] = []
        for item in response.json():
            alerts.append(CoverageAlert(**item))

        return alerts

    def cleanup(self, retention_days: int = 30) -> list[str]:
        """Clean up old snapshots via HTTP."""
        url = f"{self.base_url}/cleanup"
        params = {"retention_days": retention_days}

        response = self.session.post(url, params=params)
        response.raise_for_status()

        return response.json().get("deleted", [])


def validate_snapshot_data(snapshot: CoverageSnapshot) -> bool:
    """Validate that a snapshot has all required fields and valid values.

    Args:
        snapshot: Snapshot to validate

    Returns:
        True if snapshot is valid
    """
    has_valid_coverage: bool = (
        0.0 <= snapshot.overall_statement_coverage_pct <= 100.0
        and 0.0 <= snapshot.overall_branch_coverage_pct <= 100.0
        and 0.0 <= snapshot.overall_line_coverage_pct <= 100.0
    )

    has_modules: bool = len(snapshot.module_coverages) >= 0
    has_timestamp: bool = snapshot.timestamp is not None
    has_source: bool = len(snapshot.source) > 0

    return has_valid_coverage and has_modules and has_timestamp and has_source


def validate_trend_analysis(analysis: CoverageTrendAnalysis) -> bool:
    """Validate that trend analysis has all required fields.

    Args:
        analysis: Trend analysis to validate

    Returns:
        True if analysis is valid
    """
    has_measurements: bool = len(analysis.measurements) >= 0
    has_valid_direction: bool = analysis.trend_direction in ("improving", "stable", "degrading")
    has_valid_values: bool = (
        0.0 <= analysis.current_value <= 100.0
        and 0.0 <= analysis.average_value <= 100.0
        and analysis.min_value <= analysis.max_value
    )

    return has_measurements and has_valid_direction and has_valid_values


def validate_alert(alert: CoverageAlert) -> bool:
    """Validate that an alert has all required fields.

    Args:
        alert: Alert to validate

    Returns:
        True if alert is valid
    """
    has_valid_type: bool = alert.alert_type in (
        "below_threshold",
        "regression_detected",
        "trend_degrading",
        "critical_module_coverage",
    )
    has_valid_severity: bool = alert.severity in ("info", "warning", "critical", "emergency")
    has_valid_value: bool = 0.0 <= alert.current_value <= 100.0
    has_id: bool = len(alert.alert_id) > 0

    return has_valid_type and has_valid_severity and has_valid_value and has_id
