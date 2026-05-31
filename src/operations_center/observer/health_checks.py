# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Health checks for observer system and validation failures.

Provides:
- Collector health assessment
- System-wide health status
- Remediation recommendations
- Health check endpoints
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from .metrics import CollectorMetrics, MetricsCollector, SystemMetrics
from .security_logging import AlertCondition, ErrorCategory, MalformedPayloadMetrics


class HealthStatus(str, Enum):
    """Health status levels."""

    HEALTHY = "HEALTHY"
    NOMINAL = "NOMINAL"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class HealthCheckResult:
    """Result of a single health check."""

    check_name: str
    status: HealthStatus
    message: str
    timestamp: datetime = dataclass_field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    details: dict = dataclass_field(default_factory=dict)
    remediation: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
            "remediation": self.remediation,
        }


@dataclass
class SystemHealthReport:
    """Complete system health assessment."""

    timestamp: datetime = dataclass_field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    checks: list[HealthCheckResult] = dataclass_field(default_factory=list)
    summary: str = ""
    critical_issues: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "overall_status": self.overall_status.value,
            "checks": [check.to_dict() for check in self.checks],
            "summary": self.summary,
            "critical_issues": self.critical_issues,
            "warnings": self.warnings,
        }


class HealthChecker:
    """Performs health checks on the observer system."""

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        malformed_metrics: MalformedPayloadMetrics,
        alert_conditions: dict[str, AlertCondition],
    ) -> None:
        self.metrics_collector = metrics_collector
        self.malformed_metrics = malformed_metrics
        self.alert_conditions = alert_conditions

    def check_collector_health(
        self, collector_name: str
    ) -> HealthCheckResult:
        """Check health of a specific collector."""
        metrics = self.metrics_collector.get_collector_metrics(collector_name)

        if not metrics:
            return HealthCheckResult(
                check_name=f"collector_health_{collector_name}",
                status=HealthStatus.UNKNOWN,
                message=f"No metrics available for collector: {collector_name}",
                remediation="Ensure collector has completed at least one run.",
            )

        status = HealthStatus(metrics.health_status)
        details = {
            "total_runs": metrics.total_runs,
            "successful_runs": metrics.successful_runs,
            "failed_runs": metrics.failed_runs,
            "error_rate_percent": metrics.error_rate_percent,
            "mean_latency_ms": metrics.mean_latency_ms,
            "throughput_artifacts_per_sec": metrics.throughput_artifacts_per_sec,
        }

        message = f"Collector {collector_name} is {status.value.lower()}"
        if metrics.error_rate_percent > 20:
            message += f" (error rate: {metrics.error_rate_percent:.1f}%)"

        remediation = None
        if status == HealthStatus.CRITICAL:
            remediation = (
                f"High error rate ({metrics.error_rate_percent:.1f}%) detected. "
                f"Check recent validation failures and artifact quality."
            )
        elif status == HealthStatus.DEGRADED:
            remediation = (
                f"Elevated error rate ({metrics.error_rate_percent:.1f}%) detected. "
                f"Monitor for patterns in validation failures."
            )

        return HealthCheckResult(
            check_name=f"collector_health_{collector_name}",
            status=status,
            message=message,
            details=details,
            remediation=remediation,
        )

    def check_error_rate(self) -> HealthCheckResult:
        """Check overall error rate against thresholds."""
        system_metrics = self.metrics_collector.get_system_metrics()

        if system_metrics.total_collectors == 0:
            return HealthCheckResult(
                check_name="error_rate",
                status=HealthStatus.UNKNOWN,
                message="No collectors configured",
                remediation="Configure and run collectors to establish baseline metrics.",
            )

        rate = system_metrics.overall_error_rate_percent
        if rate == 0:
            status = HealthStatus.HEALTHY
            message = "Error rate is 0%"
        elif rate < 1:
            status = HealthStatus.NOMINAL
            message = f"Error rate is {rate:.2f}% (< 1%)"
        elif rate < 5:
            status = HealthStatus.DEGRADED
            message = f"Error rate is {rate:.2f}% (5-10% threshold)"
            remediation = (
                f"Error rate elevated to {rate:.2f}%. "
                "Review recent validation failures and check artifact quality."
            )
        else:
            status = HealthStatus.CRITICAL
            message = f"Error rate is {rate:.2f}% (> 5%)"
            remediation = (
                f"Critical error rate {rate:.2f}%. "
                "Immediate investigation required. Check parse/structure/IO errors."
            )

        return HealthCheckResult(
            check_name="error_rate",
            status=status,
            message=message,
            details={
                "overall_error_rate_percent": rate,
                "total_validation_failures": system_metrics.total_validation_failures,
            },
            remediation=remediation if status in (HealthStatus.DEGRADED, HealthStatus.CRITICAL) else None,
        )

    def check_latency(self) -> HealthCheckResult:
        """Check collector latency against acceptable thresholds."""
        system_metrics = self.metrics_collector.get_system_metrics()

        if system_metrics.total_collectors == 0:
            return HealthCheckResult(
                check_name="latency",
                status=HealthStatus.UNKNOWN,
                message="No collectors configured",
            )

        metrics_list = self.metrics_collector.get_all_collector_metrics()
        slow_collectors = [
            (name, m.mean_latency_ms)
            for name, m in metrics_list.items()
            if m.mean_latency_ms > 1000
        ]

        if not slow_collectors:
            return HealthCheckResult(
                check_name="latency",
                status=HealthStatus.HEALTHY,
                message="All collectors within latency thresholds",
                details={
                    "max_mean_latency_ms": max(
                        (m.mean_latency_ms for m in metrics_list.values()),
                        default=0,
                    )
                },
            )

        status = HealthStatus.DEGRADED if len(slow_collectors) < len(metrics_list) else HealthStatus.CRITICAL
        message = f"{len(slow_collectors)} collector(s) exceeding latency threshold (1s)"
        details = {
            "slow_collectors": [
                {"name": name, "mean_latency_ms": latency}
                for name, latency in slow_collectors
            ]
        }
        remediation = (
            f"{len(slow_collectors)} collector(s) have high latency. "
            "Check file I/O performance and artifact sizes."
        )

        return HealthCheckResult(
            check_name="latency",
            status=status,
            message=message,
            details=details,
            remediation=remediation,
        )

    def check_alert_conditions(self) -> HealthCheckResult:
        """Check if any alert conditions are triggered."""
        triggered = []
        for name, condition in self.alert_conditions.items():
            recent_errors = [
                e
                for e in self.malformed_metrics.recent_errors
                if (
                    datetime.now(timezone.utc) - e.normalized_timestamp()
                    <= timedelta(minutes=condition.time_window_minutes)
                )
                and e.error_type == condition.category.value
            ]
            if len(recent_errors) >= condition.trigger_threshold:
                triggered.append(
                    {
                        "condition": name,
                        "threshold": condition.trigger_threshold,
                        "actual": len(recent_errors),
                        "window_minutes": condition.time_window_minutes,
                    }
                )

        if not triggered:
            return HealthCheckResult(
                check_name="alert_conditions",
                status=HealthStatus.HEALTHY,
                message="No alert conditions triggered",
            )

        status = HealthStatus.CRITICAL
        message = f"{len(triggered)} alert condition(s) triggered"
        details = {"triggered_alerts": triggered}
        remediation = (
            "Alert conditions triggered. "
            "Review error logs and investigate root causes of failures."
        )

        return HealthCheckResult(
            check_name="alert_conditions",
            status=status,
            message=message,
            details=details,
            remediation=remediation,
        )

    def run_all_checks(self) -> SystemHealthReport:
        """Run all health checks and generate report."""
        checks = []

        # Check overall error rate
        checks.append(self.check_error_rate())

        # Check latency
        checks.append(self.check_latency())

        # Check alert conditions
        checks.append(self.check_alert_conditions())

        # Check individual collectors
        for collector_name in self.metrics_collector.get_all_collector_metrics():
            checks.append(self.check_collector_health(collector_name))

        # Determine overall status
        statuses = [c.status for c in checks]
        if HealthStatus.CRITICAL in statuses:
            overall_status = HealthStatus.CRITICAL
        elif HealthStatus.DEGRADED in statuses:
            overall_status = HealthStatus.DEGRADED
        elif HealthStatus.NOMINAL in statuses:
            overall_status = HealthStatus.NOMINAL
        elif HealthStatus.HEALTHY in statuses:
            overall_status = HealthStatus.HEALTHY
        else:
            overall_status = HealthStatus.UNKNOWN

        critical_issues = [c.message for c in checks if c.status == HealthStatus.CRITICAL]
        warnings = [c.message for c in checks if c.status == HealthStatus.DEGRADED]

        summary = f"System health: {overall_status.value} ({len(critical_issues)} critical, {len(warnings)} warnings)"

        return SystemHealthReport(
            timestamp=datetime.now(timezone.utc),
            overall_status=overall_status,
            checks=checks,
            summary=summary,
            critical_issues=critical_issues,
            warnings=warnings,
        )
