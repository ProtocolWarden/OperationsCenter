# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for validation metrics exporter.

Tests cover:
- JSONL file writing with correct format
- Daily file rotation
- 30-day retention policy
- Metrics aggregation
- Error handling for I/O failures
- Factory method for creating metrics from errors
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.observer.exporters import (
    ValidationFailureMetric,
    ValidationMetricsExporter,
)


class TestValidationFailureMetric:
    """Tests for ValidationFailureMetric dataclass."""

    def test_metric_creation(self) -> None:
        """Test creating a validation failure metric."""
        timestamp = datetime.now(UTC)
        metric = ValidationFailureMetric(
            timestamp=timestamp,
            collector_name="ExecutionArtifactCollector",
            artifact_type="control_outcome.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="JSON parse error at line 15",
            artifact_path="/path/to/artifact.json",
            context={"line": 15, "col": 8},
            metrics_snapshot={"total_parse_errors": 5},
        )

        assert metric.collector_name == "ExecutionArtifactCollector"
        assert metric.failure_type == "parse_error"
        assert metric.severity == "HIGH"

    def test_metric_to_dict(self) -> None:
        """Test converting metric to dictionary format."""
        timestamp = datetime(2026, 5, 31, 14, 23, 45, 123000, tzinfo=UTC)
        metric = ValidationFailureMetric(
            timestamp=timestamp,
            collector_name="DependencyDriftCollector",
            artifact_type="dependency_report.json",
            failure_type="structure_error",
            severity="HIGH",
            error_message="Missing required field: dependencies",
            artifact_path="/path/to/report.json",
            context={"expected_schema": "dependency_report.json"},
            metrics_snapshot={"total_structure_errors": 3},
        )

        result = metric.to_dict()
        assert "validation_failure_metric" in result
        inner = result["validation_failure_metric"]
        assert inner["version"] == "1.0"
        assert inner["timestamp"] == "2026-05-31T14:23:45.123000+00:00"
        assert inner["collector_name"] == "DependencyDriftCollector"
        assert inner["failure_type"] == "structure_error"


class TestValidationMetricsExporter:
    """Tests for ValidationMetricsExporter class."""

    def test_exporter_creation_no_export(self) -> None:
        """Test creating exporter with no export directory."""
        exporter = ValidationMetricsExporter(export_dir=None)
        assert exporter.export_dir is None

    def test_exporter_creation_with_export_dir(self, tmp_path: Path) -> None:
        """Test creating exporter with export directory."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)
        assert exporter.export_dir == tmp_path
        assert exporter.export_dir.exists()

    def test_exporter_creates_directory(self, tmp_path: Path) -> None:
        """Test that exporter creates export directory if it doesn't exist."""
        export_dir = tmp_path / "metrics" / "new"
        exporter = ValidationMetricsExporter(export_dir=export_dir)
        assert export_dir.exists()

    def test_export_failure_to_file(self, tmp_path: Path) -> None:
        """Test exporting a failure metric to file."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)
        metric = ValidationFailureMetric(
            timestamp=datetime(2026, 5, 31, 14, 23, 45, tzinfo=UTC),
            collector_name="ExecutionArtifactCollector",
            artifact_type="control_outcome.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="JSON parse error",
            artifact_path="/path/to/artifact.json",
            context={"line": 15},
            metrics_snapshot={"total_parse_errors": 1},
        )

        exporter.export_failure(metric)

        # Check file was created with today's date
        metrics_file = tmp_path / f"metrics-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
        assert metrics_file.exists()

        # Verify JSONL content
        with open(metrics_file) as f:
            content = f.read()
            assert "validation_failure_metric" in content
            data = json.loads(content)
            assert data["validation_failure_metric"]["failure_type"] == "parse_error"

    def test_export_multiple_metrics_to_same_file(self, tmp_path: Path) -> None:
        """Test exporting multiple metrics appends to same file."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        # Export two metrics
        metric1 = ValidationFailureMetric(
            timestamp=datetime.now(UTC),
            collector_name="Collector1",
            artifact_type="type1.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="Error 1",
            artifact_path="/path/1.json",
            context={},
            metrics_snapshot={},
        )
        metric2 = ValidationFailureMetric(
            timestamp=datetime.now(UTC),
            collector_name="Collector2",
            artifact_type="type2.json",
            failure_type="structure_error",
            severity="MEDIUM",
            error_message="Error 2",
            artifact_path="/path/2.json",
            context={},
            metrics_snapshot={},
        )

        exporter.export_failure(metric1)
        exporter.export_failure(metric2)

        # Verify both metrics in file
        metrics_file = tmp_path / f"metrics-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
        with open(metrics_file) as f:
            lines = f.readlines()
            assert len(lines) == 2
            assert "Collector1" in lines[0]
            assert "Collector2" in lines[1]

    def test_export_with_no_export_dir_is_noop(self, tmp_path: Path) -> None:
        """Test that export is a no-op when export_dir is None."""
        exporter = ValidationMetricsExporter(export_dir=None)
        metric = ValidationFailureMetric(
            timestamp=datetime.now(UTC),
            collector_name="Test",
            artifact_type="test.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="Test",
            artifact_path="/test.json",
            context={},
            metrics_snapshot={},
        )

        # Should not raise, should not write
        exporter.export_failure(metric)

    def test_export_failure_handles_io_errors(self, tmp_path: Path) -> None:
        """Test that export handles I/O errors gracefully."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)
        metric = ValidationFailureMetric(
            timestamp=datetime.now(UTC),
            collector_name="Test",
            artifact_type="test.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="Test",
            artifact_path="/test.json",
            context={},
            metrics_snapshot={},
        )

        # Mock open to raise OSError
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            # Should handle gracefully
            exporter.export_failure(metric)

    def test_get_metrics_file_path(self, tmp_path: Path) -> None:
        """Test getting metrics file path with date."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        file_path = exporter._get_metrics_file_path()
        assert file_path.name == f"metrics-{today}.jsonl"

    def test_rotate_if_needed_removes_old_files(self, tmp_path: Path) -> None:
        """Test that rotation removes metrics files older than retention_days."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path, retention_days=1)

        # Create a file with an old date
        old_date = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")
        old_file = tmp_path / f"metrics-{old_date}.jsonl"
        old_file.write_text("old metrics\n")

        # Create a file with today's date
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        new_file = tmp_path / f"metrics-{today}.jsonl"
        new_file.write_text("new metrics\n")

        # Run rotation
        exporter._rotate_if_needed()

        # Old file should be deleted, new file should remain
        assert not old_file.exists()
        assert new_file.exists()

    def test_rotate_if_needed_keeps_files_within_retention(self, tmp_path: Path) -> None:
        """Test that rotation keeps files within retention period."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path, retention_days=7)

        # Create files with various dates
        dates = [
            (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d"),  # Too old
            (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d"),   # Keep
            (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d"),   # Keep
            datetime.now(UTC).strftime("%Y-%m-%d"),                         # Keep
        ]

        for date_str in dates:
            (tmp_path / f"metrics-{date_str}.jsonl").write_text(f"{date_str} metrics\n")

        exporter._rotate_if_needed()

        # Only the first file should be deleted
        assert not (tmp_path / f"metrics-{dates[0]}.jsonl").exists()
        for date_str in dates[1:]:
            assert (tmp_path / f"metrics-{date_str}.jsonl").exists()

    def test_rotate_if_needed_with_invalid_filenames(self, tmp_path: Path) -> None:
        """Test that rotation handles files with invalid date formats gracefully."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path, retention_days=1)

        # Create files with various invalid formats
        (tmp_path / "metrics-invalid.jsonl").write_text("data\n")
        (tmp_path / "metrics-2026-5-31.jsonl").write_text("data\n")
        (tmp_path / "metrics-20260531.jsonl").write_text("data\n")

        # Valid file
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        (tmp_path / f"metrics-{today}.jsonl").write_text("data\n")

        # Should not raise, should process valid files
        exporter._rotate_if_needed()

        # Invalid files should remain (can't parse date)
        assert (tmp_path / "metrics-invalid.jsonl").exists()

    def test_read_metrics_empty_directory(self, tmp_path: Path) -> None:
        """Test reading metrics from empty directory."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)
        metrics = exporter.read_metrics()
        assert metrics == []

    def test_read_metrics_single_file(self, tmp_path: Path) -> None:
        """Test reading metrics from a single file."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        # Write a metric
        metric = ValidationFailureMetric(
            timestamp=datetime.now(UTC),
            collector_name="Test",
            artifact_type="test.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="Test error",
            artifact_path="/test.json",
            context={},
            metrics_snapshot={},
        )
        exporter.export_failure(metric)

        # Read metrics
        metrics = exporter.read_metrics()
        assert len(metrics) == 1
        assert metrics[0]["validation_failure_metric"]["collector_name"] == "Test"

    def test_read_metrics_with_date_filter(self, tmp_path: Path) -> None:
        """Test reading metrics with date range filter."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        # Create files for different dates
        old_date = datetime.now(UTC) - timedelta(days=5)
        today = datetime.now(UTC)

        # Write old metric
        old_file = tmp_path / old_date.strftime("metrics-%Y-%m-%d.jsonl")
        old_file.write_text('{"validation_failure_metric": {"collector_name": "Old"}}\n')

        # Write new metric
        exporter.export_failure(
            ValidationFailureMetric(
                timestamp=today,
                collector_name="New",
                artifact_type="test.json",
                failure_type="parse_error",
                severity="HIGH",
                error_message="Test",
                artifact_path="/test.json",
                context={},
                metrics_snapshot={},
            )
        )

        # Read only recent metrics
        recent = exporter.read_metrics(from_date=today - timedelta(days=1))
        assert len(recent) == 1
        assert recent[0]["validation_failure_metric"]["collector_name"] == "New"

    def test_aggregate_metrics_empty(self, tmp_path: Path) -> None:
        """Test aggregating metrics from empty directory."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)
        agg = exporter.aggregate_metrics()

        assert agg["total_errors"] == 0
        assert agg["parse_errors"] == 0
        assert agg["structure_errors"] == 0
        assert agg["io_errors"] == 0

    def test_aggregate_metrics_by_type(self, tmp_path: Path) -> None:
        """Test aggregating metrics by error type."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        # Export different error types
        for i in range(3):
            exporter.export_failure(
                ValidationFailureMetric(
                    timestamp=datetime.now(UTC),
                    collector_name="Test",
                    artifact_type="test.json",
                    failure_type="parse_error",
                    severity="HIGH",
                    error_message="Error",
                    artifact_path="/test.json",
                    context={},
                    metrics_snapshot={},
                )
            )

        for i in range(2):
            exporter.export_failure(
                ValidationFailureMetric(
                    timestamp=datetime.now(UTC),
                    collector_name="Test",
                    artifact_type="test.json",
                    failure_type="structure_error",
                    severity="MEDIUM",
                    error_message="Error",
                    artifact_path="/test.json",
                    context={},
                    metrics_snapshot={},
                )
            )

        agg = exporter.aggregate_metrics()
        assert agg["total_errors"] == 5
        assert agg["parse_errors"] == 3
        assert agg["structure_errors"] == 2
        assert agg["io_errors"] == 0

    def test_aggregate_metrics_by_collector(self, tmp_path: Path) -> None:
        """Test aggregating metrics grouped by collector."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        # Export metrics from different collectors
        for collector in ["Collector1", "Collector2", "Collector1"]:
            exporter.export_failure(
                ValidationFailureMetric(
                    timestamp=datetime.now(UTC),
                    collector_name=collector,
                    artifact_type="test.json",
                    failure_type="parse_error",
                    severity="HIGH",
                    error_message="Error",
                    artifact_path="/test.json",
                    context={},
                    metrics_snapshot={},
                )
            )

        agg = exporter.aggregate_metrics()
        assert agg["by_collector"]["Collector1"] == 2
        assert agg["by_collector"]["Collector2"] == 1

    def test_aggregate_metrics_by_severity(self, tmp_path: Path) -> None:
        """Test aggregating metrics grouped by severity."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        severities = ["HIGH", "MEDIUM", "LOW", "HIGH"]
        for severity in severities:
            exporter.export_failure(
                ValidationFailureMetric(
                    timestamp=datetime.now(UTC),
                    collector_name="Test",
                    artifact_type="test.json",
                    failure_type="parse_error",
                    severity=severity,
                    error_message="Error",
                    artifact_path="/test.json",
                    context={},
                    metrics_snapshot={},
                )
            )

        agg = exporter.aggregate_metrics()
        assert agg["by_severity"]["HIGH"] == 2
        assert agg["by_severity"]["MEDIUM"] == 1
        assert agg["by_severity"]["LOW"] == 1

    def test_create_metric_from_error_factory(self) -> None:
        """Test factory method for creating metric from error information."""
        metric = ValidationMetricsExporter.create_metric_from_error(
            collector_name="TestCollector",
            artifact_type="artifact.json",
            failure_type="parse_error",
            severity="HIGH",
            error_message="Test error",
            artifact_path="/path/to/artifact.json",
            context={"line": 10, "col": 5},
            metrics_snapshot={"total_errors": 1},
        )

        assert metric.collector_name == "TestCollector"
        assert metric.artifact_type == "artifact.json"
        assert metric.failure_type == "parse_error"
        assert metric.error_message == "Test error"
        assert metric.context == {"line": 10, "col": 5}

    def test_create_metric_from_error_with_path_object(self) -> None:
        """Test factory method accepts Path objects."""
        artifact_path = Path("/path/to/artifact.json")
        metric = ValidationMetricsExporter.create_metric_from_error(
            collector_name="Test",
            artifact_type="test.json",
            failure_type="io_error",
            severity="MEDIUM",
            error_message="IO Error",
            artifact_path=artifact_path,
        )

        assert metric.artifact_path == str(artifact_path)

    def test_retention_days_configuration(self, tmp_path: Path) -> None:
        """Test retention days can be configured."""
        exporter_30 = ValidationMetricsExporter(export_dir=tmp_path, retention_days=30)
        assert exporter_30.retention_days == 30

        exporter_7 = ValidationMetricsExporter(export_dir=tmp_path, retention_days=7)
        assert exporter_7.retention_days == 7

    def test_auto_rotate_configuration(self, tmp_path: Path) -> None:
        """Test auto_rotate can be disabled."""
        exporter = ValidationMetricsExporter(
            export_dir=tmp_path, auto_rotate=False
        )
        assert exporter.auto_rotate is False

        # _rotate_if_needed should be a no-op
        exporter._rotate_if_needed()

    def test_error_rate_per_minute_calculation(self, tmp_path: Path) -> None:
        """Test error rate calculation in aggregation."""
        exporter = ValidationMetricsExporter(export_dir=tmp_path)

        # Export some metrics
        for _ in range(5):
            exporter.export_failure(
                ValidationFailureMetric(
                    timestamp=datetime.now(UTC),
                    collector_name="Test",
                    artifact_type="test.json",
                    failure_type="parse_error",
                    severity="HIGH",
                    error_message="Error",
                    artifact_path="/test.json",
                    context={},
                    metrics_snapshot={},
                )
            )

        agg = exporter.aggregate_metrics()
        assert agg["error_rate_per_minute"] > 0
