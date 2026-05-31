# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Dashboard data providers for visualization of validation metrics.

Provides:
- Formatted data for monitoring dashboards
- Time-series data for graphing
- Summary statistics
- Alert status visualization
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .health_checks import HealthChecker, HealthStatus
from .metrics import CollectorMetrics, MetricsCollector, SystemMetrics
from .security_logging import MalformedPayloadMetrics
from .structured_logging import StructuredLogReader, StructuredLogWriter


@dataclass
class DashboardMetric:
    """Single metric for dashboard display."""

    name: str
    value: float | int | str
    unit: str
    status: str
    threshold_warning: Optional[float] = None
    threshold_critical: Optional[float] = None
    timestamp: datetime = dataclass_field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "threshold_warning": self.threshold_warning,
            "threshold_critical": self.threshold_critical,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DashboardPanel:
    """Panel of related metrics for dashboard."""

    title: str
    description: str
    metrics: list[DashboardMetric] = dataclass_field(default_factory=list)
    timestamp: datetime = dataclass_field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "metrics": [m.to_dict() for m in self.metrics],
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DashboardSnapshot:
    """Complete dashboard snapshot."""

    timestamp: datetime
    system_status: str
    panels: list[DashboardPanel] = dataclass_field(default_factory=list)
    alerts: list[str] = dataclass_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "system_status": self.system_status,
            "panels": [p.to_dict() for p in self.panels],
            "alerts": self.alerts,
        }


class DashboardProvider:
    """Provides formatted data for monitoring dashboards."""

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        health_checker: HealthChecker,
        log_reader: Optional[StructuredLogReader] = None,
    ) -> None:
        self.metrics_collector = metrics_collector
        self.health_checker = health_checker
        self.log_reader = log_reader

    def generate_snapshot(self) -> DashboardSnapshot:
        """Generate complete dashboard snapshot."""
        timestamp = datetime.now(timezone.utc)
        health_report = self.health_checker.run_all_checks()

        panels = []
        panels.append(self._panel_system_overview())
        panels.append(self._panel_error_rates())
        panels.append(self._panel_latency())
        panels.append(self._panel_collector_health())

        if self.log_reader:
            panels.append(self._panel_recent_errors())

        alerts = [issue for issue in health_report.critical_issues] + [
            warning for warning in health_report.warnings
        ]

        return DashboardSnapshot(
            timestamp=timestamp,
            system_status=health_report.overall_status.value,
            panels=panels,
            alerts=alerts,
        )

    def _panel_system_overview(self) -> DashboardPanel:
        """System overview panel."""
        system_metrics = self.metrics_collector.get_system_metrics()

        def get_status(value: int, warning: int, critical: int) -> str:
            if value >= critical:
                return "CRITICAL"
            if value >= warning:
                return "WARNING"
            return "HEALTHY"

        metrics = [
            DashboardMetric(
                name="Total Collectors",
                value=system_metrics.total_collectors,
                unit="count",
                status="HEALTHY",
            ),
            DashboardMetric(
                name="Healthy Collectors",
                value=system_metrics.healthy_collectors,
                unit="count",
                status="HEALTHY",
            ),
            DashboardMetric(
                name="Degraded Collectors",
                value=system_metrics.degraded_collectors,
                unit="count",
                status=get_status(system_metrics.degraded_collectors, 1, 2),
                threshold_warning=1,
                threshold_critical=2,
            ),
            DashboardMetric(
                name="Critical Collectors",
                value=system_metrics.critical_collectors,
                unit="count",
                status=get_status(system_metrics.critical_collectors, 1, 1),
                threshold_warning=1,
                threshold_critical=1,
            ),
            DashboardMetric(
                name="System Health",
                value=system_metrics.system_health_status,
                unit="status",
                status=system_metrics.system_health_status,
            ),
        ]

        return DashboardPanel(
            title="System Overview",
            description="High-level system status and collector counts",
            metrics=metrics,
        )

    def _panel_error_rates(self) -> DashboardPanel:
        """Error rates panel."""
        system_metrics = self.metrics_collector.get_system_metrics()

        metrics = [
            DashboardMetric(
                name="Overall Error Rate",
                value=round(system_metrics.overall_error_rate_percent, 2),
                unit="%",
                status=self._get_error_rate_status(
                    system_metrics.overall_error_rate_percent
                ),
                threshold_warning=1.0,
                threshold_critical=5.0,
            ),
            DashboardMetric(
                name="Total Validation Failures",
                value=system_metrics.total_validation_failures,
                unit="count",
                status="HEALTHY"
                if system_metrics.total_validation_failures == 0
                else "WARNING",
            ),
            DashboardMetric(
                name="Parse Errors",
                value=sum(m.total_parse_errors for m in system_metrics.collector_metrics.values()),
                unit="count",
                status="WARNING" if sum(m.total_parse_errors for m in system_metrics.collector_metrics.values()) > 0 else "HEALTHY",
            ),
            DashboardMetric(
                name="Structure Errors",
                value=sum(m.total_structure_errors for m in system_metrics.collector_metrics.values()),
                unit="count",
                status="WARNING" if sum(m.total_structure_errors for m in system_metrics.collector_metrics.values()) > 0 else "HEALTHY",
            ),
            DashboardMetric(
                name="IO Errors",
                value=sum(m.total_io_errors for m in system_metrics.collector_metrics.values()),
                unit="count",
                status="HEALTHY",
            ),
        ]

        return DashboardPanel(
            title="Error Rates",
            description="Validation failure metrics by error type",
            metrics=metrics,
        )

    def _panel_latency(self) -> DashboardPanel:
        """Latency panel."""
        collector_metrics = self.metrics_collector.get_all_collector_metrics()

        if not collector_metrics:
            metrics = [
                DashboardMetric(
                    name="No Data",
                    value="N/A",
                    unit="",
                    status="UNKNOWN",
                )
            ]
        else:
            max_latency = max(
                (m.max_latency_ms for m in collector_metrics.values()), default=0
            )
            mean_latency = sum(m.mean_latency_ms for m in collector_metrics.values()) / len(
                collector_metrics
            ) if collector_metrics else 0
            throughput = sum(
                m.throughput_artifacts_per_sec for m in collector_metrics.values()
            )

            metrics = [
                DashboardMetric(
                    name="Mean Latency (All)",
                    value=round(mean_latency, 2),
                    unit="ms",
                    status=self._get_latency_status(mean_latency),
                    threshold_warning=500,
                    threshold_critical=1000,
                ),
                DashboardMetric(
                    name="Max Latency",
                    value=round(max_latency, 2),
                    unit="ms",
                    status=self._get_latency_status(max_latency),
                    threshold_warning=1000,
                    threshold_critical=5000,
                ),
                DashboardMetric(
                    name="Total Throughput",
                    value=round(throughput, 2),
                    unit="artifacts/sec",
                    status="HEALTHY",
                ),
            ]

        return DashboardPanel(
            title="Latency & Throughput",
            description="Collection performance metrics",
            metrics=metrics,
        )

    def _panel_collector_health(self) -> DashboardPanel:
        """Collector health panel."""
        collector_metrics = self.metrics_collector.get_all_collector_metrics()

        metrics = []
        for name, m in sorted(collector_metrics.items()):
            metrics.append(
                DashboardMetric(
                    name=f"{name} - Error Rate",
                    value=round(m.error_rate_percent, 2),
                    unit="%",
                    status=m.health_status,
                    threshold_warning=5.0,
                    threshold_critical=20.0,
                )
            )
            metrics.append(
                DashboardMetric(
                    name=f"{name} - Success Rate",
                    value=round(
                        (m.successful_runs / m.total_runs * 100)
                        if m.total_runs > 0
                        else 0,
                        2,
                    ),
                    unit="%",
                    status="HEALTHY" if m.successful_runs == m.total_runs else "WARNING",
                )
            )

        return DashboardPanel(
            title="Collector Health",
            description="Per-collector error and success rates",
            metrics=metrics,
        )

    def _panel_recent_errors(self) -> DashboardPanel:
        """Recent errors panel."""
        if not self.log_reader:
            return DashboardPanel(
                title="Recent Errors",
                description="Latest validation failures",
                metrics=[],
            )

        recent = self.log_reader.read_recent(10)
        metrics = []

        for entry in recent:
            metrics.append(
                DashboardMetric(
                    name=f"{entry.collector or 'unknown'}: {entry.error_type or 'unknown'}",
                    value=entry.message,
                    unit="error",
                    status="WARNING",
                    timestamp=entry.timestamp,
                )
            )

        if not metrics:
            metrics.append(
                DashboardMetric(
                    name="No Recent Errors",
                    value="OK",
                    unit="status",
                    status="HEALTHY",
                )
            )

        return DashboardPanel(
            title="Recent Errors",
            description="Latest 10 validation failures",
            metrics=metrics,
        )

    @staticmethod
    def _get_error_rate_status(rate: float) -> str:
        """Determine status based on error rate."""
        if rate == 0:
            return "HEALTHY"
        if rate < 1:
            return "NOMINAL"
        if rate < 5:
            return "DEGRADED"
        return "CRITICAL"

    @staticmethod
    def _get_latency_status(latency_ms: float) -> str:
        """Determine status based on latency."""
        if latency_ms < 100:
            return "HEALTHY"
        if latency_ms < 500:
            return "NOMINAL"
        if latency_ms < 1000:
            return "DEGRADED"
        return "CRITICAL"
