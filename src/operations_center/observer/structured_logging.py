# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Structured logging configuration for validation failures and metrics.

Provides:
- Structured log format (JSON lines)
- Log rotation and retention
- Metrics aggregation from logs
- Log queries and filtering
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class StructuredLogEntry:
    """Single structured log entry."""

    timestamp: datetime
    level: str
    logger: str
    message: str
    event_type: str
    collector: Optional[str] = None
    artifact_type: Optional[str] = None
    error_type: Optional[str] = None
    error_severity: Optional[str] = None
    latency_ms: Optional[float] = None
    artifacts_processed: Optional[int] = None
    error_count: Optional[int] = None
    context: dict = None

    def __post_init__(self) -> None:
        if self.context is None:
            self.context = {}

    def to_json(self) -> str:
        """Convert to JSON line."""
        entry_dict = {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "event_type": self.event_type,
            "collector": self.collector,
            "artifact_type": self.artifact_type,
            "error_type": self.error_type,
            "error_severity": self.error_severity,
            "latency_ms": self.latency_ms,
            "artifacts_processed": self.artifacts_processed,
            "error_count": self.error_count,
            "context": self.context,
        }
        # Remove None values
        return json.dumps({k: v for k, v in entry_dict.items() if v is not None}, ensure_ascii=False)


class StructuredLogWriter:
    """Writes structured logs to file with rotation."""

    def __init__(
        self,
        log_dir: Path,
        log_filename: str = "observer_validation_failures.jsonl",
        max_file_size_bytes: int = 100 * 1024 * 1024,  # 100MB
        backup_count: int = 10,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / log_filename
        self.max_file_size_bytes = max_file_size_bytes
        self.backup_count = backup_count

    def write(self, entry: StructuredLogEntry) -> None:
        """Write a structured log entry."""
        self._rotate_if_needed()

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")

    def _rotate_if_needed(self) -> None:
        """Rotate log file if it exceeds max size."""
        if not self.log_path.exists():
            return

        if self.log_path.stat().st_size < self.max_file_size_bytes:
            return

        # Rotate existing backups
        for i in range(self.backup_count - 1, 0, -1):
            old_path = self.log_path.with_suffix(f".{i}.jsonl")
            new_path = self.log_path.with_suffix(f".{i + 1}.jsonl")
            if old_path.exists():
                if new_path.exists():
                    new_path.unlink()
                old_path.rename(new_path)

        # Create backup of current file
        backup_path = self.log_path.with_suffix(".1.jsonl")
        if backup_path.exists():
            backup_path.unlink()
        self.log_path.rename(backup_path)

    def list_log_files(self) -> list[Path]:
        """List all log files (current + backups)."""
        if not self.log_path.exists():
            return []

        stem = self.log_path.stem
        logs = [self.log_path]

        for i in range(1, self.backup_count + 1):
            backup = self.log_path.with_name(f"{stem}.{i}.jsonl")
            if backup.exists():
                logs.append(backup)

        return logs


class StructuredLogReader:
    """Reads and queries structured logs."""

    def __init__(self, log_writer: StructuredLogWriter) -> None:
        self.log_writer = log_writer

    def read_recent(self, limit: int = 100) -> list[StructuredLogEntry]:
        """Read recent log entries."""
        entries = []
        files = self.log_writer.list_log_files()

        # Read from most recent file first
        for log_file in reversed(files):
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry_dict = json.loads(line)
                        entry = self._dict_to_entry(entry_dict)
                        entries.append(entry)
                    except (json.JSONDecodeError, KeyError):
                        continue

            if len(entries) >= limit:
                break

        return sorted(
            entries[-limit:],
            key=lambda e: e.timestamp,
            reverse=True,
        )

    def query(
        self,
        event_type: Optional[str] = None,
        collector: Optional[str] = None,
        error_type: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> list[StructuredLogEntry]:
        """Query log entries with filters."""
        entries = self.read_recent(limit * 10)  # Read more to account for filtering

        filtered = entries
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if collector:
            filtered = [e for e in filtered if e.collector == collector]
        if error_type:
            filtered = [e for e in filtered if e.error_type == error_type]
        if level:
            filtered = [e for e in filtered if e.level == level]

        return filtered[-limit:]

    def _dict_to_entry(self, data: dict) -> StructuredLogEntry:
        """Convert dict to StructuredLogEntry."""
        timestamp_str = data.get("timestamp", datetime.now(timezone.utc).isoformat())
        if isinstance(timestamp_str, str):
            timestamp = datetime.fromisoformat(timestamp_str)
        else:
            timestamp = timestamp_str

        return StructuredLogEntry(
            timestamp=timestamp,
            level=data.get("level", "INFO"),
            logger=data.get("logger", "unknown"),
            message=data.get("message", ""),
            event_type=data.get("event_type", "unknown"),
            collector=data.get("collector"),
            artifact_type=data.get("artifact_type"),
            error_type=data.get("error_type"),
            error_severity=data.get("error_severity"),
            latency_ms=data.get("latency_ms"),
            artifacts_processed=data.get("artifacts_processed"),
            error_count=data.get("error_count"),
            context=data.get("context", {}),
        )


class StructuredLogger:
    """Logs events in structured format for metrics and analysis."""

    def __init__(self, log_writer: StructuredLogWriter) -> None:
        self.log_writer = log_writer

    def log_validation_failure(
        self,
        collector: str,
        artifact_type: str,
        error_type: str,
        error_severity: str,
        message: str,
        context: Optional[dict] = None,
    ) -> None:
        """Log a validation failure."""
        entry = StructuredLogEntry(
            timestamp=datetime.now(timezone.utc),
            level="ERROR" if error_severity == "HIGH" else "WARNING",
            logger="observer.validation",
            message=message,
            event_type="validation_failure",
            collector=collector,
            artifact_type=artifact_type,
            error_type=error_type,
            error_severity=error_severity,
            context=context or {},
        )
        self.log_writer.write(entry)

    def log_collector_run(
        self,
        collector: str,
        latency_ms: float,
        artifacts_processed: int,
        error_count: int,
        success: bool,
        context: Optional[dict] = None,
    ) -> None:
        """Log a collector run."""
        entry = StructuredLogEntry(
            timestamp=datetime.now(timezone.utc),
            level="INFO" if success else "WARNING",
            logger="observer.collection",
            message=f"Collector {collector} completed: {artifacts_processed} artifacts, {error_count} errors in {latency_ms:.0f}ms",
            event_type="collector_run",
            collector=collector,
            latency_ms=latency_ms,
            artifacts_processed=artifacts_processed,
            error_count=error_count,
            context=context or {},
        )
        self.log_writer.write(entry)

    def log_health_status(
        self,
        status: str,
        message: str,
        context: Optional[dict] = None,
    ) -> None:
        """Log a health status update."""
        level = (
            "ERROR"
            if status == "CRITICAL"
            else "WARNING"
            if status in ("DEGRADED", "NOMINAL")
            else "INFO"
        )
        entry = StructuredLogEntry(
            timestamp=datetime.now(timezone.utc),
            level=level,
            logger="observer.health",
            message=message,
            event_type="health_status",
            context=context or {},
        )
        self.log_writer.write(entry)
