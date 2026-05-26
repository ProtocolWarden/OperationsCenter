# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Security logging configuration and observability for malformed JSON detection.

Defines:
- Security log format and requirements
- Alert conditions and thresholds
- Observability metrics for malformed payloads
- Log validation rules
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


class ErrorSeverity(str, Enum):
    """Severity levels for security events."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ErrorCategory(str, Enum):
    """Categories of malformed payload errors."""

    PARSE_ERROR = "parse_error"
    STRUCTURE_ERROR = "structure_error"
    IO_ERROR = "io_error"


@dataclass
class SecurityLogEntry:
    """Structured security log entry for malformed payload detection."""

    timestamp: datetime
    event: str
    artifact: str
    error_type: str
    error_msg: str
    severity: ErrorSeverity
    component: str
    collector: Optional[str] = None
    expected_schema: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None

    def normalized_timestamp(self) -> datetime:
        if self.timestamp.tzinfo is None:
            return self.timestamp.replace(tzinfo=timezone.utc)
        return self.timestamp.astimezone(timezone.utc)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event": self.event,
            "artifact": self.artifact,
            "error_type": self.error_type,
            "error_msg": self.error_msg,
            "severity": self.severity.value,
            "component": self.component,
            "collector": self.collector,
            "expected_schema": self.expected_schema,
            "line": self.line,
            "col": self.col,
        }


@dataclass
class AlertCondition:
    """Condition that triggers an alert for malformed payloads."""

    name: str
    description: str
    category: ErrorCategory
    trigger_threshold: int
    time_window_minutes: int
    severity: ErrorSeverity
    action: str

    def __post_init__(self) -> None:
        if self.trigger_threshold < 1:
            raise ValueError("trigger_threshold must be >= 1")
        if self.time_window_minutes < 1:
            raise ValueError("time_window_minutes must be >= 1")


@dataclass
class MalformedPayloadMetrics:
    """Track metrics for malformed payload detection."""

    total_parse_errors: int = 0
    total_structure_errors: int = 0
    total_io_errors: int = 0
    errors_by_collector: dict[str, int] = dataclass_field(default_factory=dict)
    errors_by_artifact_type: dict[str, int] = dataclass_field(default_factory=dict)
    recent_errors: list[SecurityLogEntry] = dataclass_field(default_factory=list)
    last_error_time: Optional[datetime] = None
    alert_fired_at: Optional[datetime] = None

    def add_error(
        self,
        entry: SecurityLogEntry,
        keep_recent_count: int = 100,
    ) -> None:
        """Record a malformed payload error."""
        if entry.error_type == ErrorCategory.PARSE_ERROR.value:
            self.total_parse_errors += 1
        elif entry.error_type == ErrorCategory.STRUCTURE_ERROR.value:
            self.total_structure_errors += 1
        elif entry.error_type == ErrorCategory.IO_ERROR.value:
            self.total_io_errors += 1

        collector = entry.collector or "unknown"
        self.errors_by_collector[collector] = self.errors_by_collector.get(collector, 0) + 1

        artifact_type = entry.expected_schema or "unknown"
        self.errors_by_artifact_type[artifact_type] = (
            self.errors_by_artifact_type.get(artifact_type, 0) + 1
        )

        self.recent_errors.append(entry)
        self.recent_errors = self.recent_errors[-keep_recent_count:]
        self.last_error_time = entry.normalized_timestamp()

    def total_errors(self) -> int:
        """Return total count of all errors."""
        return self.total_parse_errors + self.total_structure_errors + self.total_io_errors

    def get_error_rate_per_minute(self) -> float:
        """Calculate error rate (errors per minute) if time_window available."""
        if not self.recent_errors or len(self.recent_errors) < 2:
            return 0.0

        first = self.recent_errors[0].normalized_timestamp()
        last = self.recent_errors[-1].normalized_timestamp()
        elapsed = (last - first).total_seconds() / 60.0
        if elapsed <= 0:
            return float(len(self.recent_errors))

        return len(self.recent_errors) / elapsed


# Alert Conditions (Stage 4 Observability)
ALERT_CONDITIONS: dict[str, AlertCondition] = {
    "parse_error_spike": AlertCondition(
        name="Parse Error Spike",
        description="High frequency of JSON parse errors detected",
        category=ErrorCategory.PARSE_ERROR,
        trigger_threshold=10,
        time_window_minutes=5,
        severity=ErrorSeverity.HIGH,
        action="log_and_notify_operators",
    ),
    "structure_error_surge": AlertCondition(
        name="Structure Validation Error Surge",
        description="Unexpected schema changes or format violations detected",
        category=ErrorCategory.STRUCTURE_ERROR,
        trigger_threshold=5,
        time_window_minutes=5,
        severity=ErrorSeverity.HIGH,
        action="log_and_notify_operators",
    ),
    "permission_denied_pattern": AlertCondition(
        name="Permission Denied Pattern",
        description="Repeated permission errors reading artifacts",
        category=ErrorCategory.IO_ERROR,
        trigger_threshold=3,
        time_window_minutes=10,
        severity=ErrorSeverity.MEDIUM,
        action="log_and_escalate_to_sre",
    ),
    "collector_health_degradation": AlertCondition(
        name="Collector Health Degradation",
        description="Single collector experiencing >20% error rate",
        category=ErrorCategory.PARSE_ERROR,
        trigger_threshold=5,
        time_window_minutes=5,
        severity=ErrorSeverity.MEDIUM,
        action="log_and_notify_operators",
    ),
}


def should_trigger_alert(
    metrics: MalformedPayloadMetrics,
    condition: AlertCondition,
    lookback_minutes: int = 5,
) -> bool:
    """Determine if an alert should be triggered based on metrics and condition.

    Args:
        metrics: Current malformed payload metrics
        condition: Alert condition to evaluate
        lookback_minutes: How far back to look in recent_errors

    Returns:
        True if alert should be triggered
    """
    if not metrics.recent_errors:
        return False

    # Filter errors from the lookback window
    cutoff_time = datetime.now(tz=timezone.utc) - timedelta(minutes=lookback_minutes)
    recent_errors = [
        e
        for e in metrics.recent_errors
        if e.normalized_timestamp() >= cutoff_time
        and e.error_type == condition.category.value
    ]

    return len(recent_errors) >= condition.trigger_threshold


# Security Log Validation Rules
SECURITY_LOG_REQUIREMENTS = {
    "mandatory_fields": [
        "timestamp",
        "event",
        "artifact",
        "error_type",
        "error_msg",
        "severity",
        "component",
    ],
    "severity_values": ["LOW", "MEDIUM", "HIGH"],
    "timestamp_format": "ISO 8601",
    "log_levels": {
        "parse_error": "DEBUG",
        "io_error": "DEBUG",
        "permission_error": "WARNING",
        "structure_error": "WARNING",
    },
    "pii_exclusion": [
        "Must not contain user credentials, API keys, or secrets",
        "Must not contain internal IP addresses without anonymization",
        "File paths allowed only when necessary for debugging",
    ],
}
