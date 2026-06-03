# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Metrics collection and exposure for validation failure monitoring.

Exposes:
- Export success/failure metrics
- Latency and throughput measurements
- Per-collector performance baselines
- Real-time metric snapshots for dashboards
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class MetricUnit(str, Enum):
    """Units for metrics."""

    MILLISECONDS = "ms"
    SECONDS = "s"
    BYTES = "bytes"
    COUNT = "count"
    PERCENT = "%"
    ERRORS_PER_MINUTE = "errors/min"


@dataclass
class PerformanceMetric:
    """Single performance measurement."""

    name: str
    value: float
    unit: MetricUnit
    timestamp: datetime
    collector_name: Optional[str] = None
    artifact_type: Optional[str] = None
    tags: dict[str, str] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit.value,
            "timestamp": self.timestamp.isoformat(),
            "collector": self.collector_name,
            "artifact_type": self.artifact_type,
            "tags": self.tags,
        }


@dataclass
class CollectorMetrics:
    """Metrics for a single collector."""

    collector_name: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    total_artifacts_processed: int = 0
    total_artifacts_skipped: int = 0
    total_parse_errors: int = 0
    total_structure_errors: int = 0
    total_io_errors: int = 0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    mean_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    throughput_artifacts_per_sec: float = 0.0
    error_rate_percent: float = 0.0
    health_status: str = "HEALTHY"
    last_run_timestamp: Optional[datetime] = None
    last_error_timestamp: Optional[datetime] = None

    def update_from_run(
        self,
        latency_ms: float,
        artifacts_processed: int,
        artifacts_skipped: int,
        parse_errors: int,
        structure_errors: int,
        io_errors: int,
        success: bool,
    ) -> None:
        """Update metrics from a single collector run."""
        self.total_runs += 1
        if success:
            self.successful_runs += 1
        else:
            self.failed_runs += 1

        self.total_artifacts_processed += artifacts_processed
        self.total_artifacts_skipped += artifacts_skipped
        self.total_parse_errors += parse_errors
        self.total_structure_errors += structure_errors
        self.total_io_errors += io_errors

        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        self.total_latency_ms += latency_ms

        if self.total_runs > 0:
            self.mean_latency_ms = self.total_latency_ms / self.total_runs

        if self.total_artifacts_processed > 0:
            elapsed_seconds = self.total_latency_ms / 1000.0
            if elapsed_seconds > 0:
                self.throughput_artifacts_per_sec = self.total_artifacts_processed / elapsed_seconds

        total_errors = self.total_parse_errors + self.total_structure_errors + self.total_io_errors
        total_attempted = self.total_artifacts_processed + self.total_artifacts_skipped
        if total_attempted > 0:
            self.error_rate_percent = (total_errors / total_attempted) * 100

        self._update_health_status()
        self.last_run_timestamp = datetime.now(timezone.utc)
        if total_errors > 0:
            self.last_error_timestamp = datetime.now(timezone.utc)

    def _update_health_status(self) -> None:
        """Determine health status based on metrics."""
        if self.total_runs == 0:
            self.health_status = "UNKNOWN"
            return

        if self.error_rate_percent == 0:
            self.health_status = "HEALTHY"
        elif self.error_rate_percent < 5:
            self.health_status = "NOMINAL"
        elif self.error_rate_percent < 20:
            self.health_status = "DEGRADED"
        else:
            self.health_status = "CRITICAL"

    def to_dict(self) -> dict:
        return {
            "collector_name": self.collector_name,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "total_artifacts_processed": self.total_artifacts_processed,
            "total_artifacts_skipped": self.total_artifacts_skipped,
            "total_parse_errors": self.total_parse_errors,
            "total_structure_errors": self.total_structure_errors,
            "total_io_errors": self.total_io_errors,
            "min_latency_ms": self.min_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "mean_latency_ms": self.mean_latency_ms,
            "throughput_artifacts_per_sec": self.throughput_artifacts_per_sec,
            "error_rate_percent": self.error_rate_percent,
            "health_status": self.health_status,
            "last_run_timestamp": (
                self.last_run_timestamp.isoformat() if self.last_run_timestamp else None
            ),
            "last_error_timestamp": (
                self.last_error_timestamp.isoformat() if self.last_error_timestamp else None
            ),
        }


@dataclass
class SystemMetrics:
    """System-wide metrics across all collectors."""

    timestamp: datetime = dataclass_field(default_factory=lambda: datetime.now(timezone.utc))
    total_collectors: int = 0
    healthy_collectors: int = 0
    degraded_collectors: int = 0
    critical_collectors: int = 0
    total_validation_failures: int = 0
    parse_errors_per_minute: float = 0.0
    structure_errors_per_minute: float = 0.0
    io_errors_per_minute: float = 0.0
    overall_error_rate_percent: float = 0.0
    system_health_status: str = "UNKNOWN"
    time_window_minutes: int = 5
    collector_metrics: dict[str, CollectorMetrics] = dataclass_field(default_factory=dict)

    def update_from_collectors(self, collector_metrics: dict[str, CollectorMetrics]) -> None:
        """Update system metrics from individual collector metrics."""
        self.collector_metrics = collector_metrics
        self.total_collectors = len(collector_metrics)
        self.timestamp = datetime.now(timezone.utc)

        healthy = sum(1 for m in collector_metrics.values() if m.health_status == "HEALTHY")
        degraded = sum(1 for m in collector_metrics.values() if m.health_status == "DEGRADED")
        critical = sum(1 for m in collector_metrics.values() if m.health_status == "CRITICAL")

        self.healthy_collectors = healthy
        self.degraded_collectors = degraded
        self.critical_collectors = critical

        total_errors = sum(
            m.total_parse_errors + m.total_structure_errors + m.total_io_errors
            for m in collector_metrics.values()
        )
        self.total_validation_failures = total_errors

        total_processed = sum(
            m.total_artifacts_processed + m.total_artifacts_skipped
            for m in collector_metrics.values()
        )
        if total_processed > 0:
            self.overall_error_rate_percent = (total_errors / total_processed) * 100

        if self.critical_collectors > 0:
            self.system_health_status = "CRITICAL"
        elif self.degraded_collectors > 0:
            self.system_health_status = "DEGRADED"
        elif self.healthy_collectors == self.total_collectors:
            self.system_health_status = "HEALTHY"
        else:
            self.system_health_status = "NOMINAL"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_collectors": self.total_collectors,
            "healthy_collectors": self.healthy_collectors,
            "degraded_collectors": self.degraded_collectors,
            "critical_collectors": self.critical_collectors,
            "total_validation_failures": self.total_validation_failures,
            "parse_errors_per_minute": self.parse_errors_per_minute,
            "structure_errors_per_minute": self.structure_errors_per_minute,
            "io_errors_per_minute": self.io_errors_per_minute,
            "overall_error_rate_percent": self.overall_error_rate_percent,
            "system_health_status": self.system_health_status,
            "time_window_minutes": self.time_window_minutes,
            "collector_metrics": {
                name: metrics.to_dict() for name, metrics in self.collector_metrics.items()
            },
        }


class MetricsCollector:
    """Collects and exposes metrics for monitoring and dashboards."""

    def __init__(self) -> None:
        self.collector_metrics: dict[str, CollectorMetrics] = {}
        self.system_metrics = SystemMetrics()
        self.performance_metrics: list[PerformanceMetric] = []
        self._start_time = datetime.now(timezone.utc)

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
        """Record a collector run with performance metrics."""
        if collector_name not in self.collector_metrics:
            self.collector_metrics[collector_name] = CollectorMetrics(collector_name=collector_name)

        metrics = self.collector_metrics[collector_name]
        metrics.update_from_run(
            latency_ms=latency_ms,
            artifacts_processed=artifacts_processed,
            artifacts_skipped=artifacts_skipped,
            parse_errors=parse_errors,
            structure_errors=structure_errors,
            io_errors=io_errors,
            success=success,
        )

        self.system_metrics.update_from_collectors(self.collector_metrics)

    def record_performance_metric(
        self,
        name: str,
        value: float,
        unit: MetricUnit,
        collector_name: Optional[str] = None,
        artifact_type: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> None:
        """Record a single performance metric."""
        metric = PerformanceMetric(
            name=name,
            value=value,
            unit=unit,
            timestamp=datetime.now(timezone.utc),
            collector_name=collector_name,
            artifact_type=artifact_type,
            tags=tags or {},
        )
        self.performance_metrics.append(metric)
        # Keep only recent metrics (last 1000)
        if len(self.performance_metrics) > 1000:
            self.performance_metrics = self.performance_metrics[-1000:]

    def get_system_metrics(self) -> SystemMetrics:
        """Get current system-wide metrics."""
        self.system_metrics.update_from_collectors(self.collector_metrics)
        return self.system_metrics

    def get_collector_metrics(self, collector_name: str) -> Optional[CollectorMetrics]:
        """Get metrics for a specific collector."""
        return self.collector_metrics.get(collector_name)

    def get_all_collector_metrics(self) -> dict[str, CollectorMetrics]:
        """Get metrics for all collectors."""
        return self.collector_metrics.copy()

    def get_recent_performance_metrics(self, limit: int = 100) -> list[PerformanceMetric]:
        """Get recent performance metrics."""
        return self.performance_metrics[-limit:] if self.performance_metrics else []

    def export_snapshot(self) -> dict:
        """Export complete metrics snapshot for dashboard consumption."""
        return {
            "snapshot_time": datetime.now(timezone.utc).isoformat(),
            "system_metrics": self.system_metrics.to_dict(),
            "collector_metrics": {
                name: metrics.to_dict() for name, metrics in self.collector_metrics.items()
            },
            "performance_metrics": [m.to_dict() for m in self.get_recent_performance_metrics(50)],
        }
