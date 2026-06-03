# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Alert configuration, routing, and context for validation failure alerting.

Defines:
- Per-collector failure thresholds
- Alert routing configuration
- Alert context for notification channels
- Alert rule composition
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CollectorThresholds:
    """Per-collector failure thresholds for alert triggering."""

    collector_name: str
    high_water_mark: int
    error_threshold: int
    time_window_minutes: int = 5
    recovery_action: str = "graceful_degradation"

    def __post_init__(self) -> None:
        if self.high_water_mark < 1:
            raise ValueError("high_water_mark must be >= 1")
        if self.error_threshold < 1:
            raise ValueError("error_threshold must be >= 1")
        if self.error_threshold < self.high_water_mark:
            raise ValueError("error_threshold must be >= high_water_mark")
        if self.time_window_minutes < 1:
            raise ValueError("time_window_minutes must be >= 1")


# Per-collector thresholds from Stage 0 Section 4.3
COLLECTOR_THRESHOLDS: dict[str, CollectorThresholds] = {
    "ExecutionArtifactCollector": CollectorThresholds(
        collector_name="ExecutionArtifactCollector",
        high_water_mark=5,
        error_threshold=10,
        time_window_minutes=5,
        recovery_action="skip_failed_runs",
    ),
    "DependencyDriftCollector": CollectorThresholds(
        collector_name="DependencyDriftCollector",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="return_not_available",
    ),
    "CheckSignal": CollectorThresholds(
        collector_name="CheckSignal",
        high_water_mark=5,
        error_threshold=8,
        time_window_minutes=5,
        recovery_action="treat_as_unavailable",
    ),
    "LintSignal": CollectorThresholds(
        collector_name="LintSignal",
        high_water_mark=5,
        error_threshold=8,
        time_window_minutes=5,
        recovery_action="treat_as_unavailable",
    ),
    "ValidationHistoryCollector": CollectorThresholds(
        collector_name="ValidationHistoryCollector",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="graceful_degradation",
    ),
    "SecuritySignal": CollectorThresholds(
        collector_name="SecuritySignal",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="graceful_degradation",
    ),
    "BenchmarkSignal": CollectorThresholds(
        collector_name="BenchmarkSignal",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="graceful_degradation",
    ),
    "CoverageSignal": CollectorThresholds(
        collector_name="CoverageSignal",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="graceful_degradation",
    ),
    "CIHistoryCollector": CollectorThresholds(
        collector_name="CIHistoryCollector",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="graceful_degradation",
    ),
    "ArchitectureSignal": CollectorThresholds(
        collector_name="ArchitectureSignal",
        high_water_mark=3,
        error_threshold=5,
        time_window_minutes=5,
        recovery_action="graceful_degradation",
    ),
}


@dataclass
class AlertRoute:
    """Route alerts to specific notification channels."""

    condition_name: str
    channels: list[str]
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_channels = {"operator_log", "plane", "slack", "pagerduty"}
        for channel in self.channels:
            if channel not in valid_channels:
                raise ValueError(f"Unknown channel: {channel}")


# Alert routing from Stage 0 Section 4.1 + Stage 2 decisions
ALERT_ROUTES: dict[str, AlertRoute] = {
    "parse_error_spike": AlertRoute(
        condition_name="parse_error_spike",
        channels=["operator_log", "plane"],
        context={
            "task_type": "improve",
            "priority": "high",
            "tags": ["validation", "json", "critical"],
        },
    ),
    "structure_error_surge": AlertRoute(
        condition_name="structure_error_surge",
        channels=["operator_log", "plane"],
        context={
            "task_type": "improve",
            "priority": "high",
            "tags": ["validation", "schema", "critical"],
        },
    ),
    "permission_denied_pattern": AlertRoute(
        condition_name="permission_denied_pattern",
        channels=["operator_log", "plane"],
        context={
            "task_type": "improve",
            "priority": "medium",
            "tags": ["validation", "permissions", "io"],
        },
    ),
    "collector_health_degradation": AlertRoute(
        condition_name="collector_health_degradation",
        channels=["operator_log", "plane"],
        context={
            "task_type": "improve",
            "priority": "medium",
            "tags": ["validation", "health", "monitoring"],
        },
    ),
}


@dataclass
class AlertContext:
    """Context information for alert notification channels."""

    condition_name: str
    collector_name: str | None = None
    error_count: int = 0
    threshold: int = 0
    time_window_minutes: int = 5
    severity: str = "MEDIUM"
    artifact_type: str | None = None
    sample_errors: list[str] = field(default_factory=list)
    metrics_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for routing and context passing."""
        return {
            "condition_name": self.condition_name,
            "collector_name": self.collector_name,
            "error_count": self.error_count,
            "threshold": self.threshold,
            "time_window_minutes": self.time_window_minutes,
            "severity": self.severity,
            "artifact_type": self.artifact_type,
            "sample_errors": self.sample_errors,
            "metrics_summary": self.metrics_summary,
        }


def get_collector_thresholds(collector_name: str) -> CollectorThresholds | None:
    """Get thresholds for a specific collector.

    Args:
        collector_name: Name of the collector

    Returns:
        CollectorThresholds if found, None otherwise
    """
    return COLLECTOR_THRESHOLDS.get(collector_name)


def get_alert_route(condition_name: str) -> AlertRoute | None:
    """Get routing configuration for a specific alert condition.

    Args:
        condition_name: Name of the alert condition

    Returns:
        AlertRoute if found, None otherwise
    """
    return ALERT_ROUTES.get(condition_name)


def list_collector_names() -> list[str]:
    """List all configured collectors."""
    return sorted(COLLECTOR_THRESHOLDS.keys())


def list_condition_names() -> list[str]:
    """List all configured alert conditions."""
    return sorted(ALERT_ROUTES.keys())
