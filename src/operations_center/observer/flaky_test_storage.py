# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Flaky Test Storage Manager — Handles persistence and retention for flakiness data.

Manages JSONL storage of Tier 2 session reports and Tier 3 aggregations with
configurable retention policies (3-day for sessions, 90-day for aggregations).

Usage:
    storage = FlakyTestStorageManager.create_local("/var/flaky-tests")
    storage.save_session_results(session_report)
    sessions = storage.load_recent_sessions(days=7)
    storage.cleanup_old_sessions()
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class FlakyTestAggregationReport:
    """Aggregated flakiness report over a time period."""

    date: str
    period_days: int
    total_test_executions: int
    flaky_test_count: int
    unstable_test_count: int
    flaky_tests: list[dict] = field(default_factory=list)
    by_module: dict[str, dict] = field(default_factory=dict)
    by_category: dict[str, dict] = field(default_factory=dict)
    recommendations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "date": self.date,
            "period_days": self.period_days,
            "total_test_executions": self.total_test_executions,
            "flaky_test_count": self.flaky_test_count,
            "unstable_test_count": self.unstable_test_count,
            "flaky_tests": self.flaky_tests,
            "by_module": self.by_module,
            "by_category": self.by_category,
            "recommendations": self.recommendations,
        }

    @staticmethod
    def from_dict(data: dict) -> FlakyTestAggregationReport:
        """Create from dictionary."""
        return FlakyTestAggregationReport(
            date=data["date"],
            period_days=data["period_days"],
            total_test_executions=data["total_test_executions"],
            flaky_test_count=data["flaky_test_count"],
            unstable_test_count=data["unstable_test_count"],
            flaky_tests=data.get("flaky_tests", []),
            by_module=data.get("by_module", {}),
            by_category=data.get("by_category", {}),
            recommendations=data.get("recommendations", []),
        )


class FlakyTestStorageManager:
    """Manages storage and retrieval of flaky test data."""

    def __init__(
        self,
        base_path: Path,
        session_retention_days: int = 3,
        aggregation_retention_days: int = 90,
    ):
        """Initialize storage manager.

        Args:
            base_path: Root directory for storage
            session_retention_days: How long to keep session reports
            aggregation_retention_days: How long to keep aggregations
        """
        self.base_path = Path(base_path)
        self.session_dir = self.base_path / "runs"
        self.aggregation_dir = self.base_path / "aggregations"
        self.session_retention_days = session_retention_days
        self.aggregation_retention_days = aggregation_retention_days

        # Create directories
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.aggregation_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def create_local(base_path: str) -> FlakyTestStorageManager:
        """Create local file-based storage manager.

        Args:
            base_path: Root directory for storage

        Returns:
            Configured storage manager
        """
        return FlakyTestStorageManager(Path(base_path))

    @staticmethod
    def create_s3(bucket: str, prefix: str = "flaky-tests") -> FlakyTestStorageManager:
        """Create S3-based storage manager (stub for S3 support).

        Args:
            bucket: S3 bucket name
            prefix: S3 key prefix

        Returns:
            Configured storage manager (currently returns local, S3 support deferred)
        """
        # For Stage 5, defer S3 implementation to Stage 6
        return FlakyTestStorageManager(Path(".flaky-tests"))

    def save_session_results(self, session_data: dict) -> Path:
        """Save session analysis results.

        Args:
            session_data: Session report dictionary

        Returns:
            Path to saved file
        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d")
        hour_dir = self.session_dir / timestamp
        hour_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        time_str = datetime.now(UTC).strftime("%H-%M-%S")
        filename = f"{time_str}-session.json"
        filepath = hour_dir / filename

        # Write JSONL format (one record per session)
        with open(filepath, "w") as f:
            json.dump(session_data, f)

        return filepath

    def save_aggregation(self, agg_report: FlakyTestAggregationReport) -> Path:
        """Save aggregation report.

        Args:
            agg_report: Aggregation report object

        Returns:
            Path to saved file
        """
        filename = f"{agg_report.date}-aggregation.json"
        filepath = self.aggregation_dir / filename

        with open(filepath, "w") as f:
            json.dump(agg_report.to_dict(), f, indent=2)

        return filepath

    def load_recent_sessions(self, days: int = 7) -> list[dict]:
        """Load all session reports from past N days.

        Args:
            days: Number of days to look back

        Returns:
            List of session report dictionaries
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        sessions = []

        if not self.session_dir.exists():
            return sessions

        for date_dir in sorted(self.session_dir.iterdir()):
            if not date_dir.is_dir():
                continue

            # Parse directory name as date
            try:
                date_obj = datetime.strptime(date_dir.name, "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
                if date_obj < cutoff:
                    continue
            except ValueError:
                continue

            # Load all session files in this directory
            for session_file in sorted(date_dir.glob("*-session.json")):
                try:
                    with open(session_file) as f:
                        sessions.append(json.load(f))
                except (json.JSONDecodeError, IOError):
                    # Skip corrupted files
                    continue

        return sessions

    def load_recent_aggregations(self, days: int = 90) -> list[FlakyTestAggregationReport]:
        """Load aggregation reports from past N days.

        Args:
            days: Number of days to look back

        Returns:
            List of aggregation reports
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        aggregations = []

        if not self.aggregation_dir.exists():
            return aggregations

        for agg_file in sorted(self.aggregation_dir.glob("*-aggregation.json")):
            try:
                # Parse date from filename
                date_str = agg_file.stem.replace("-aggregation", "")
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)

                if date_obj < cutoff:
                    continue

                with open(agg_file) as f:
                    data = json.load(f)
                    aggregations.append(FlakyTestAggregationReport.from_dict(data))
            except (json.JSONDecodeError, IOError, ValueError):
                continue

        return aggregations

    def cleanup_old_sessions(self) -> int:
        """Remove session reports older than retention period.

        Returns:
            Count of deleted files
        """
        cutoff = datetime.now(UTC) - timedelta(days=self.session_retention_days)
        deleted_count = 0

        if not self.session_dir.exists():
            return deleted_count

        for date_dir in self.session_dir.iterdir():
            if not date_dir.is_dir():
                continue

            try:
                date_obj = datetime.strptime(date_dir.name, "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
                if date_obj < cutoff:
                    deleted_count += len(list(date_dir.glob("*.json")))
                    shutil.rmtree(date_dir)
            except ValueError:
                continue

        return deleted_count

    def cleanup_old_aggregations(self) -> int:
        """Remove aggregations older than retention period.

        Returns:
            Count of deleted files
        """
        cutoff = datetime.now(UTC) - timedelta(days=self.aggregation_retention_days)
        deleted_count = 0

        if not self.aggregation_dir.exists():
            return deleted_count

        for agg_file in self.aggregation_dir.glob("*-aggregation.json"):
            try:
                date_str = agg_file.stem.replace("-aggregation", "")
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)

                if date_obj < cutoff:
                    agg_file.unlink()
                    deleted_count += 1
            except ValueError:
                continue

        return deleted_count
