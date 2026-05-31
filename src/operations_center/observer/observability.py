# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Observability service orchestrating all monitoring, metrics, and health infrastructure.

Integrates:
- Metrics collection (MetricsCollector)
- Health checks (HealthChecker)
- Structured logging (StructuredLogger)
- Dashboard generation (DashboardProvider)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .dashboard import DashboardProvider, DashboardSnapshot
from .health_checks import HealthChecker, SystemHealthReport
from .metrics import MetricsCollector
from .security_logging import AlertCondition, MalformedPayloadMetrics
from .structured_logging import StructuredLogReader, StructuredLogger, StructuredLogWriter


class ObservabilityService:
    """Central service for all observability: metrics, health, logging, dashboards."""

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        alert_conditions: Optional[dict[str, AlertCondition]] = None,
    ) -> None:
        self.log_dir = Path(log_dir) if log_dir else Path(".operations_center/logs")
        self.metrics_collector = MetricsCollector()
        self.malformed_metrics = MalformedPayloadMetrics()
        self.alert_conditions = alert_conditions or {}

        # Structured logging
        self.log_writer = StructuredLogWriter(self.log_dir)
        self.log_reader = StructuredLogReader(self.log_writer)
        self.logger = StructuredLogger(self.log_writer)

        # Health checks
        self.health_checker = HealthChecker(
            self.metrics_collector,
            self.malformed_metrics,
            self.alert_conditions,
        )

        # Dashboard
        self.dashboard_provider = DashboardProvider(
            self.metrics_collector,
            self.health_checker,
            self.log_reader,
        )

    # Metrics API
    def record_collector_run(
        self,
        collector_name: str,
        latency_ms: float,
        artifacts_processed: int,
        artifacts_skipped: int,
        parse_errors: int,
        structure_errors: int,
        io_errors: int,
        success: bool,
    ) -> None:
        """Record a collector run's metrics."""
        self.metrics_collector.record_collector_run(
            collector_name=collector_name,
            latency_ms=latency_ms,
            artifacts_processed=artifacts_processed,
            artifacts_skipped=artifacts_skipped,
            parse_errors=parse_errors,
            structure_errors=structure_errors,
            io_errors=io_errors,
            success=success,
        )

        # Also log to structured logging
        total_errors = parse_errors + structure_errors + io_errors
        self.logger.log_collector_run(
            collector=collector_name,
            latency_ms=latency_ms,
            artifacts_processed=artifacts_processed,
            error_count=total_errors,
            success=success,
            context={
                "parse_errors": parse_errors,
                "structure_errors": structure_errors,
                "io_errors": io_errors,
                "artifacts_skipped": artifacts_skipped,
            },
        )

    def get_metrics_snapshot(self) -> dict:
        """Get complete metrics snapshot."""
        return self.metrics_collector.export_snapshot()

    # Health Check API
    def get_health_report(self) -> SystemHealthReport:
        """Get system health assessment."""
        return self.health_checker.run_all_checks()

    def get_system_health_status(self) -> str:
        """Get overall system health status."""
        return self.get_health_report().overall_status.value

    # Logging API
    def log_validation_failure(
        self,
        collector: str,
        artifact_type: str,
        error_type: str,
        error_severity: str,
        message: str,
        context: Optional[dict] = None,
    ) -> None:
        """Log a validation failure in structured format."""
        self.logger.log_validation_failure(
            collector=collector,
            artifact_type=artifact_type,
            error_type=error_type,
            error_severity=error_severity,
            message=message,
            context=context,
        )

    def log_health_status(
        self,
        status: str,
        message: str,
        context: Optional[dict] = None,
    ) -> None:
        """Log a health status update."""
        self.logger.log_health_status(status, message, context)

    def query_logs(
        self,
        event_type: Optional[str] = None,
        collector: Optional[str] = None,
        error_type: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query structured logs with filters."""
        entries = self.log_reader.query(
            event_type=event_type,
            collector=collector,
            error_type=error_type,
            level=level,
            limit=limit,
        )
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "level": e.level,
                "logger": e.logger,
                "message": e.message,
                "event_type": e.event_type,
                "collector": e.collector,
                "artifact_type": e.artifact_type,
                "error_type": e.error_type,
                "error_severity": e.error_severity,
                "latency_ms": e.latency_ms,
                "artifacts_processed": e.artifacts_processed,
                "error_count": e.error_count,
                "context": e.context,
            }
            for e in entries
        ]

    # Dashboard API
    def get_dashboard_snapshot(self) -> DashboardSnapshot:
        """Get dashboard snapshot for visualization."""
        return self.dashboard_provider.generate_snapshot()

    def get_dashboard_json(self) -> dict:
        """Get dashboard snapshot as JSON."""
        snapshot = self.get_dashboard_snapshot()
        return snapshot.to_dict()

    # Convenience methods
    def export_all(self) -> dict:
        """Export all observability data (metrics, health, logs, dashboard)."""
        health_report = self.get_health_report()
        dashboard = self.get_dashboard_snapshot()
        metrics = self.get_metrics_snapshot()

        return {
            "metrics": metrics,
            "health": health_report.to_dict(),
            "dashboard": dashboard.to_dict(),
            "logs": self.query_logs(limit=50),
        }

    def get_status_summary(self) -> dict:
        """Get brief status summary."""
        health = self.get_health_report()
        system_metrics = self.metrics_collector.get_system_metrics()

        return {
            "system_status": health.overall_status.value,
            "critical_issues": len(health.critical_issues),
            "warnings": len(health.warnings),
            "total_collectors": system_metrics.total_collectors,
            "healthy_collectors": system_metrics.healthy_collectors,
            "overall_error_rate_percent": system_metrics.overall_error_rate_percent,
            "total_validation_failures": system_metrics.total_validation_failures,
        }
