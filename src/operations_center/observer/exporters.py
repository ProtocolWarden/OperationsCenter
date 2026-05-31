# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Validation failure metrics export pipeline for alerting.

Provides:
- ValidationFailureMetric dataclass for structured failure data
- ValidationMetricsExporter for JSONL export with daily rotation
- Alert condition evaluation on exported metrics
- 30-day retention and file rotation
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from operations_center.observer.security_logging import (
    ErrorCategory,
    ErrorSeverity,
    MalformedPayloadMetrics,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationFailureMetric:
    """Single validation failure export metric."""

    timestamp: datetime
    collector_name: str
    artifact_type: str
    failure_type: str  # "parse_error", "structure_error", "io_error"
    severity: str  # "LOW", "MEDIUM", "HIGH"
    error_message: str
    artifact_path: str
    context: dict[str, Any]
    metrics_snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "validation_failure_metric": {
                "version": "1.0",
                "timestamp": self.timestamp.isoformat(),
                "collector_name": self.collector_name,
                "artifact_type": self.artifact_type,
                "failure_type": self.failure_type,
                "severity": self.severity,
                "error_message": self.error_message,
                "artifact_path": self.artifact_path,
                "context": self.context,
                "metrics": self.metrics_snapshot,
            }
        }


class ValidationMetricsExporter:
    """Export validation failure metrics to configured destinations.

    Supports:
    - JSONL file export with daily rotation
    - Configurable retention policy (30 days default)
    - Error handling for I/O failures
    - Metrics aggregation and snapshots
    """

    def __init__(
        self,
        export_dir: Path | str | None = None,
        retention_days: int = 30,
        auto_rotate: bool = True,
    ) -> None:
        """Initialize metrics exporter.

        Args:
            export_dir: Directory for metrics files. If None, exports disabled.
            retention_days: How long to keep metrics files (default 30 days)
            auto_rotate: Enable automatic daily rotation (default True)
        """
        self.export_dir = Path(export_dir) if export_dir else None
        self.retention_days = retention_days
        self.auto_rotate = auto_rotate
        self._current_date: Optional[str] = None

        if self.export_dir:
            self.export_dir.mkdir(parents=True, exist_ok=True)

    def export_failure(
        self,
        metric: ValidationFailureMetric,
    ) -> None:
        """Export a validation failure metric to configured destinations.

        Args:
            metric: The validation failure metric to export
        """
        if not self.export_dir:
            return

        try:
            self._export_to_file(metric)
            self._rotate_if_needed()
        except Exception as e:
            logger.error(
                "Failed to export validation failure metric: %s",
                e,
                exc_info=True,
            )

    def _export_to_file(self, metric: ValidationFailureMetric) -> None:
        """Write metric to JSONL file.

        Args:
            metric: The metric to export
        """
        if not self.export_dir:
            return

        # Get the metrics file path for today
        metrics_file = self._get_metrics_file_path()

        # Write metric as single JSON line
        json_line = json.dumps(metric.to_dict())
        try:
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            with open(metrics_file, "a", encoding="utf-8") as f:
                f.write(json_line)
                f.write("\n")
        except IOError as e:
            logger.error(
                "Failed to write metrics to %s: %s",
                metrics_file,
                e,
            )

    def _get_metrics_file_path(self) -> Path:
        """Get the current metrics file path (with daily rotation).

        Returns:
            Path to the metrics file for today
        """
        if not self.export_dir:
            raise ValueError("export_dir is not set")

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return self.export_dir / f"metrics-{today}.jsonl"

    def _rotate_if_needed(self) -> None:
        """Clean up old metrics files based on retention policy.

        Removes metrics files older than retention_days.
        """
        if not self.export_dir or not self.auto_rotate:
            return

        try:
            cutoff_date = datetime.now(UTC) - timedelta(days=self.retention_days)

            for metrics_file in self.export_dir.glob("metrics-*.jsonl"):
                try:
                    # Extract date from filename: metrics-YYYY-MM-DD.jsonl
                    date_str = metrics_file.stem.replace("metrics-", "")
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")
                    file_date = file_date.replace(tzinfo=UTC)

                    if file_date < cutoff_date:
                        metrics_file.unlink()
                        logger.debug(
                            "Rotated old metrics file: %s",
                            metrics_file.name,
                        )
                except (ValueError, OSError) as e:
                    logger.debug(
                        "Could not process metrics file %s: %s",
                        metrics_file.name,
                        e,
                    )
        except Exception as e:
            logger.error(
                "Error during metrics rotation: %s",
                e,
                exc_info=True,
            )

    def read_metrics(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Read metrics from exported files within optional date range.

        Args:
            from_date: Optional start date (inclusive)
            to_date: Optional end date (inclusive)

        Returns:
            List of parsed metric dictionaries
        """
        if not self.export_dir:
            return []

        metrics = []

        try:
            for metrics_file in sorted(self.export_dir.glob("metrics-*.jsonl")):
                # Extract date from filename
                try:
                    date_str = metrics_file.stem.replace("metrics-", "")
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")
                    file_date = file_date.replace(tzinfo=UTC)

                    # Check date range if specified
                    if from_date and file_date < from_date.replace(tzinfo=UTC):
                        continue
                    if to_date and file_date > to_date.replace(tzinfo=UTC):
                        continue
                except ValueError:
                    continue

                # Read JSONL file
                try:
                    with open(metrics_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                metric_dict = json.loads(line)
                                metrics.append(metric_dict)
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    "Failed to parse metric line in %s: %s",
                                    metrics_file.name,
                                    e,
                                )
                except IOError as e:
                    logger.warning(
                        "Failed to read metrics file %s: %s",
                        metrics_file.name,
                        e,
                    )
        except Exception as e:
            logger.error(
                "Error reading metrics: %s",
                e,
                exc_info=True,
            )

        return metrics

    def aggregate_metrics(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Aggregate metrics from exported files.

        Args:
            from_date: Optional start date (inclusive)
            to_date: Optional end date (inclusive)

        Returns:
            Aggregated metrics dictionary with counts and rates
        """
        metrics = self.read_metrics(from_date, to_date)

        if not metrics:
            return {
                "total_errors": 0,
                "parse_errors": 0,
                "structure_errors": 0,
                "io_errors": 0,
                "by_collector": {},
                "by_artifact_type": {},
                "by_severity": {},
            }

        parse_errors = 0
        structure_errors = 0
        io_errors = 0
        by_collector: dict[str, int] = {}
        by_artifact_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}

        for metric in metrics:
            metric_data = metric.get("validation_failure_metric", {})
            failure_type = metric_data.get("failure_type")
            collector = metric_data.get("collector_name", "unknown")
            artifact_type = metric_data.get("artifact_type", "unknown")
            severity = metric_data.get("severity", "unknown")

            if failure_type == "parse_error":
                parse_errors += 1
            elif failure_type == "structure_error":
                structure_errors += 1
            elif failure_type == "io_error":
                io_errors += 1

            by_collector[collector] = by_collector.get(collector, 0) + 1
            by_artifact_type[artifact_type] = by_artifact_type.get(artifact_type, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1

        total_errors = parse_errors + structure_errors + io_errors
        time_range_hours = 24  # Default to 24 hours
        error_rate_per_minute = (total_errors / time_range_hours / 60) if total_errors > 0 else 0.0

        return {
            "total_errors": total_errors,
            "parse_errors": parse_errors,
            "structure_errors": structure_errors,
            "io_errors": io_errors,
            "error_rate_per_minute": error_rate_per_minute,
            "by_collector": by_collector,
            "by_artifact_type": by_artifact_type,
            "by_severity": by_severity,
        }

    @staticmethod
    def create_metric_from_error(
        collector_name: str,
        artifact_type: str,
        failure_type: str,
        severity: str,
        error_message: str,
        artifact_path: str | Path,
        context: Optional[dict[str, Any]] = None,
        metrics_snapshot: Optional[dict[str, Any]] = None,
    ) -> ValidationFailureMetric:
        """Factory method to create a metric from error information.

        Args:
            collector_name: Name of the collector that detected the error
            artifact_type: Type of artifact (e.g., "control_outcome.json")
            failure_type: Type of failure ("parse_error", "structure_error", "io_error")
            severity: Severity level ("LOW", "MEDIUM", "HIGH")
            error_message: Error message describing the failure
            artifact_path: Path to the artifact
            context: Optional context dictionary
            metrics_snapshot: Optional metrics snapshot dictionary

        Returns:
            ValidationFailureMetric instance
        """
        return ValidationFailureMetric(
            timestamp=datetime.now(UTC),
            collector_name=collector_name,
            artifact_type=artifact_type,
            failure_type=failure_type,
            severity=severity,
            error_message=error_message,
            artifact_path=str(artifact_path),
            context=context or {},
            metrics_snapshot=metrics_snapshot or {},
        )
