# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Test suite for Stage 3 monitoring and observability implementation.

Tests:
- Metrics collection and exposure
- Latency and throughput measurement
- Dashboard generation
- Structured logging
- Health checks
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from operations_center.observer.dashboard import DashboardMetric, DashboardPanel, DashboardProvider
from operations_center.observer.health_checks import HealthChecker, HealthStatus
from operations_center.observer.metrics import CollectorMetrics, MetricUnit, MetricsCollector
from operations_center.observer.observability import ObservabilityService
from operations_center.observer.security_logging import AlertCondition, ErrorCategory, ErrorSeverity, MalformedPayloadMetrics
from operations_center.observer.structured_logging import StructuredLogEntry, StructuredLogger, StructuredLogReader, StructuredLogWriter


class TestMetricsCollector:
    """Test metrics collection and exposure."""

    def test_record_collector_run_updates_metrics(self) -> None:
        collector = MetricsCollector()

        collector.record_collector_run(
            collector_name="test_collector",
            latency_ms=100.0,
            artifacts_processed=10,
            artifacts_skipped=2,
            parse_errors=1,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        metrics = collector.get_collector_metrics("test_collector")
        assert metrics is not None
        assert metrics.total_runs == 1
        assert metrics.successful_runs == 1
        assert metrics.total_artifacts_processed == 10
        assert metrics.total_parse_errors == 1

    def test_collector_metrics_calculates_error_rate(self) -> None:
        collector = MetricsCollector()

        collector.record_collector_run(
            collector_name="test",
            latency_ms=50.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=5,
            structure_errors=5,
            io_errors=0,
            success=True,
        )

        metrics = collector.get_collector_metrics("test")
        assert metrics is not None
        assert metrics.error_rate_percent == 10.0

    def test_collector_metrics_calculates_throughput(self) -> None:
        collector = MetricsCollector()

        collector.record_collector_run(
            collector_name="test",
            latency_ms=1000.0,  # 1 second
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        metrics = collector.get_collector_metrics("test")
        assert metrics is not None
        assert metrics.throughput_artifacts_per_sec == 100.0

    def test_system_metrics_aggregation(self) -> None:
        collector = MetricsCollector()

        collector.record_collector_run(
            collector_name="collector1",
            latency_ms=100.0,
            artifacts_processed=50,
            artifacts_skipped=0,
            parse_errors=2,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        collector.record_collector_run(
            collector_name="collector2",
            latency_ms=100.0,
            artifacts_processed=50,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=3,
            io_errors=0,
            success=True,
        )

        system_metrics = collector.get_system_metrics()
        assert system_metrics.total_collectors == 2
        assert system_metrics.total_validation_failures == 5

    def test_metrics_export_snapshot(self) -> None:
        collector = MetricsCollector()

        collector.record_collector_run(
            collector_name="test",
            latency_ms=100.0,
            artifacts_processed=10,
            artifacts_skipped=0,
            parse_errors=1,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        snapshot = collector.export_snapshot()
        assert "system_metrics" in snapshot
        assert "collector_metrics" in snapshot
        assert "test" in snapshot["collector_metrics"]


class TestHealthChecks:
    """Test health check functionality."""

    def test_health_check_error_rate(self) -> None:
        metrics = MetricsCollector()
        malformed = MalformedPayloadMetrics()
        conditions: dict[str, AlertCondition] = {}

        metrics.record_collector_run(
            collector_name="test",
            latency_ms=100.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        checker = HealthChecker(metrics, malformed, conditions)
        result = checker.check_error_rate()

        assert result.status == HealthStatus.HEALTHY
        assert "0%" in result.message

    def test_health_check_degraded_error_rate(self) -> None:
        metrics = MetricsCollector()
        malformed = MalformedPayloadMetrics()
        conditions: dict[str, AlertCondition] = {}

        metrics.record_collector_run(
            collector_name="test",
            latency_ms=100.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=5,
            structure_errors=5,
            io_errors=0,
            success=True,
        )

        checker = HealthChecker(metrics, malformed, conditions)
        result = checker.check_error_rate()

        assert result.status == HealthStatus.DEGRADED
        assert result.remediation is not None

    def test_collector_health_check(self) -> None:
        metrics = MetricsCollector()
        malformed = MalformedPayloadMetrics()
        conditions: dict[str, AlertCondition] = {}

        metrics.record_collector_run(
            collector_name="test_collector",
            latency_ms=100.0,
            artifacts_processed=50,
            artifacts_skipped=0,
            parse_errors=2,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        checker = HealthChecker(metrics, malformed, conditions)
        result = checker.check_collector_health("test_collector")

        assert result.status in (HealthStatus.HEALTHY, HealthStatus.NOMINAL, HealthStatus.DEGRADED)
        assert "test_collector" in result.message

    def test_run_all_health_checks(self) -> None:
        metrics = MetricsCollector()
        malformed = MalformedPayloadMetrics()
        conditions: dict[str, AlertCondition] = {}

        metrics.record_collector_run(
            collector_name="test",
            latency_ms=100.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        checker = HealthChecker(metrics, malformed, conditions)
        report = checker.run_all_checks()

        assert report.overall_status is not None
        assert len(report.checks) > 0
        assert report.summary is not None


class TestStructuredLogging:
    """Test structured logging functionality."""

    def test_structured_log_entry_to_json(self) -> None:
        entry = StructuredLogEntry(
            timestamp=datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc),
            level="ERROR",
            logger="test.logger",
            message="Test error",
            event_type="test_event",
            collector="test_collector",
            error_type="parse_error",
        )

        json_str = entry.to_json()
        data = json.loads(json_str)

        assert data["message"] == "Test error"
        assert data["event_type"] == "test_event"
        assert data["collector"] == "test_collector"

    def test_structured_log_writer_write_and_read(self) -> None:
        with TemporaryDirectory() as tmpdir:
            writer = StructuredLogWriter(Path(tmpdir))

            entry = StructuredLogEntry(
                timestamp=datetime.now(timezone.utc),
                level="ERROR",
                logger="test",
                message="Test message",
                event_type="test",
            )
            writer.write(entry)

            reader = StructuredLogReader(writer)
            recent = reader.read_recent(limit=1)

            assert len(recent) == 1
            assert recent[0].message == "Test message"

    def test_structured_log_rotation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            writer = StructuredLogWriter(
                Path(tmpdir),
                max_file_size_bytes=100,  # Small size to trigger rotation
            )

            # Write multiple entries to trigger rotation
            for i in range(5):
                entry = StructuredLogEntry(
                    timestamp=datetime.now(timezone.utc),
                    level="INFO",
                    logger="test",
                    message=f"Test message {i}",
                    event_type="test",
                )
                writer.write(entry)

            # Check that rotation occurred
            files = writer.list_log_files()
            assert len(files) > 1

    def test_structured_logger_log_validation_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            writer = StructuredLogWriter(Path(tmpdir))
            logger = StructuredLogger(writer)

            logger.log_validation_failure(
                collector="test_collector",
                artifact_type="test_artifact",
                error_type="parse_error",
                error_severity="HIGH",
                message="Parse failed",
            )

            reader = StructuredLogReader(writer)
            entries = reader.query(event_type="validation_failure")

            assert len(entries) == 1
            assert entries[0].collector == "test_collector"

    def test_structured_log_query(self) -> None:
        with TemporaryDirectory() as tmpdir:
            writer = StructuredLogWriter(Path(tmpdir))
            logger = StructuredLogger(writer)

            logger.log_validation_failure(
                collector="collector1",
                artifact_type="artifact1",
                error_type="parse_error",
                error_severity="HIGH",
                message="Error 1",
            )

            logger.log_validation_failure(
                collector="collector2",
                artifact_type="artifact2",
                error_type="structure_error",
                error_severity="MEDIUM",
                message="Error 2",
            )

            reader = StructuredLogReader(writer)
            collector1_errors = reader.query(collector="collector1")

            assert len(collector1_errors) == 1
            assert collector1_errors[0].collector == "collector1"


class TestDashboard:
    """Test dashboard generation."""

    def test_dashboard_panel_creation(self) -> None:
        panel = DashboardPanel(
            title="Test Panel",
            description="Test description",
            metrics=[
                DashboardMetric(
                    name="Test Metric",
                    value=100,
                    unit="count",
                    status="HEALTHY",
                )
            ],
        )

        assert panel.title == "Test Panel"
        assert len(panel.metrics) == 1

    def test_dashboard_provider_system_overview(self) -> None:
        metrics = MetricsCollector()
        malformed = MalformedPayloadMetrics()
        conditions: dict[str, AlertCondition] = {}

        metrics.record_collector_run(
            collector_name="test",
            latency_ms=100.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        checker = HealthChecker(metrics, malformed, conditions)
        provider = DashboardProvider(metrics, checker)

        with TemporaryDirectory() as tmpdir:
            writer = StructuredLogWriter(Path(tmpdir))
            reader = StructuredLogReader(writer)
            provider.log_reader = reader

            snapshot = provider.generate_snapshot()

            assert snapshot.system_status in (
                "HEALTHY",
                "NOMINAL",
                "DEGRADED",
                "CRITICAL",
                "UNKNOWN",
            )
            assert len(snapshot.panels) > 0

    def test_dashboard_snapshot_to_dict(self) -> None:
        metrics = MetricsCollector()
        malformed = MalformedPayloadMetrics()
        conditions: dict[str, AlertCondition] = {}

        metrics.record_collector_run(
            collector_name="test",
            latency_ms=100.0,
            artifacts_processed=10,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        checker = HealthChecker(metrics, malformed, conditions)
        provider = DashboardProvider(metrics, checker)

        snapshot = provider.generate_snapshot()
        data = snapshot.to_dict()

        assert "timestamp" in data
        assert "system_status" in data
        assert "panels" in data


class TestObservabilityService:
    """Test integrated observability service."""

    def test_observability_service_initialization(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            assert service.metrics_collector is not None
            assert service.health_checker is not None
            assert service.logger is not None
            assert service.dashboard_provider is not None

    def test_observability_record_collector_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            service.record_collector_run(
                collector_name="test",
                latency_ms=100.0,
                artifacts_processed=50,
                artifacts_skipped=0,
                parse_errors=2,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

            metrics = service.get_metrics_snapshot()
            assert "collector_metrics" in metrics

    def test_observability_health_report(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            service.record_collector_run(
                collector_name="test",
                latency_ms=100.0,
                artifacts_processed=100,
                artifacts_skipped=0,
                parse_errors=0,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

            health = service.get_health_report()
            assert health.overall_status is not None

    def test_observability_logging(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            service.log_validation_failure(
                collector="test",
                artifact_type="test",
                error_type="parse_error",
                error_severity="HIGH",
                message="Test error",
            )

            logs = service.query_logs(limit=1)
            assert len(logs) == 1

    def test_observability_dashboard(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            service.record_collector_run(
                collector_name="test",
                latency_ms=100.0,
                artifacts_processed=10,
                artifacts_skipped=0,
                parse_errors=0,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

            dashboard = service.get_dashboard_snapshot()
            assert dashboard.system_status is not None

    def test_observability_export_all(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            service.record_collector_run(
                collector_name="test",
                latency_ms=100.0,
                artifacts_processed=10,
                artifacts_skipped=0,
                parse_errors=0,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

            export = service.export_all()

            assert "metrics" in export
            assert "health" in export
            assert "dashboard" in export
            assert "logs" in export

    def test_observability_status_summary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            service = ObservabilityService(log_dir=Path(tmpdir))

            service.record_collector_run(
                collector_name="test",
                latency_ms=100.0,
                artifacts_processed=100,
                artifacts_skipped=0,
                parse_errors=5,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

            summary = service.get_status_summary()

            assert "system_status" in summary
            assert "critical_issues" in summary
            assert "total_collectors" in summary
            assert "overall_error_rate_percent" in summary
