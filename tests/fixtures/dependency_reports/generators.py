# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Generators for synthetic dependency report data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DependencyStatus:
    """Synthetic dependency status entry."""

    package: str
    installed_version: str
    upstream_latest: str
    healthy: bool
    severity: str = "info"
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = {
            "package": self.package,
            "installed_version": self.installed_version,
            "upstream_latest": self.upstream_latest,
            "healthy": self.healthy,
            "severity": self.severity,
        }
        if self.notes:
            d["notes"] = self.notes
        return d


@dataclass
class DependencyReportData:
    """Synthetic dependency report payload."""

    statuses: list[DependencyStatus] = field(default_factory=list)
    created_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "statuses": [s.to_dict() for s in self.statuses],
            "created_task_ids": self.created_task_ids,
        }


class DependencyReportGenerator:
    """Generates synthetic dependency report data for testing."""

    # Realistic dependency names from OC ecosystem
    REALISTIC_DEPS = [
        ("plane", "0.20.0", "0.21.0"),
        ("team-executor", "1.2.0", "1.3.0"),
        ("custodian", "2.1.0", "2.2.0"),
        ("switchboard", "0.15.0", "0.16.0"),
        ("operations-center", "1.5.0", "1.6.0"),
        ("platformmanifest", "0.8.0", "0.9.0"),
        ("aider", "0.35.0", "0.36.0"),
        ("ruff", "0.1.0", "0.2.0"),
        ("pytest", "7.4.0", "7.5.0"),
        ("pydantic", "2.4.0", "2.5.0"),
        ("httpx", "0.25.0", "0.26.0"),
        ("cryptography", "41.0.0", "42.0.0"),
        ("requests", "2.31.0", "2.32.0"),
        ("flask", "2.3.0", "3.0.0"),
        ("sqlalchemy", "2.0.20", "2.0.21"),
        ("numpy", "1.24.0", "1.25.0"),
        ("pandas", "2.0.0", "2.1.0"),
        ("django", "4.2.0", "5.0.0"),
        ("celery", "5.3.0", "5.4.0"),
        ("redis", "5.0.0", "5.1.0"),
        ("psycopg2", "2.9.0", "2.10.0"),
        ("black", "23.9.0", "23.10.0"),
        ("mypy", "1.5.0", "1.6.0"),
        ("pre-commit", "3.3.0", "3.4.0"),
        ("docker", "6.1.0", "7.0.0"),
        ("boto3", "1.28.0", "1.29.0"),
        ("tensorflow", "2.13.0", "2.14.0"),
        ("torch", "2.0.0", "2.1.0"),
        ("huggingface-hub", "0.17.0", "0.18.0"),
        ("langchain", "0.0.300", "0.0.310"),
        ("openai", "0.28.0", "1.0.0"),
        ("anthropic", "0.7.0", "0.8.0"),
        ("protobuf", "4.24.0", "4.25.0"),
        ("grpcio", "1.59.0", "1.60.0"),
        ("elasticsearch", "8.10.0", "8.11.0"),
        ("pymongo", "4.5.0", "4.6.0"),
        ("pika", "1.3.0", "1.4.0"),
        ("kafka-python", "2.0.0", "2.1.0"),
        ("aiokafka", "0.8.0", "0.9.0"),
        ("aioredis", "2.0.0", "2.1.0"),
        ("websockets", "11.0.0", "12.0.0"),
        ("fastapi", "0.103.0", "0.104.0"),
        ("starlette", "0.27.0", "0.28.0"),
        ("uvicorn", "0.23.0", "0.24.0"),
        ("gunicorn", "21.2.0", "22.0.0"),
        ("nginx", "1.25.0", "1.26.0"),
        ("consul", "2.1.0", "2.2.0"),
        ("prometheus-client", "0.17.0", "0.18.0"),
    ]

    @staticmethod
    def baseline() -> DependencyReportData:
        """Generate baseline report: 7 deps, 0 actionable, ~2KB."""
        deps = []
        for pkg, installed, latest in DependencyReportGenerator.REALISTIC_DEPS[:7]:
            deps.append(
                DependencyStatus(
                    package=pkg,
                    installed_version=installed,
                    upstream_latest=latest,
                    healthy=True,
                    severity="info",
                    notes=None,
                )
            )
        return DependencyReportData(statuses=deps, created_task_ids=[])

    @staticmethod
    def large_simple(dep_count: int = 20, actionable_pct: float = 0.1) -> DependencyReportData:
        """Generate large-simple report: N deps, ~10% actionable, ~5KB."""
        deps = []
        action_count = max(1, int(dep_count * actionable_pct))

        dep_list = (
            DependencyReportGenerator.REALISTIC_DEPS
            * ((dep_count // len(DependencyReportGenerator.REALISTIC_DEPS)) + 1)
        )[:dep_count]

        for idx, (pkg, installed, latest) in enumerate(dep_list):
            is_actionable = idx < action_count
            deps.append(
                DependencyStatus(
                    package=pkg,
                    installed_version=installed,
                    upstream_latest=latest,
                    healthy=not is_actionable,
                    severity="warning" if is_actionable else "info",
                    notes=f"Update available: {installed} -> {latest}" if is_actionable else None,
                )
            )

        task_ids = [f"TASK-{i:03d}" for i in range(action_count)]
        return DependencyReportData(statuses=deps, created_task_ids=task_ids)

    @staticmethod
    def large_actionable(dep_count: int = 10, actionable_pct: float = 0.8) -> DependencyReportData:
        """Generate large-actionable report: N deps, 80% actionable, ~10KB."""
        deps = []
        action_count = max(1, int(dep_count * actionable_pct))

        dep_list = (
            DependencyReportGenerator.REALISTIC_DEPS
            * ((dep_count // len(DependencyReportGenerator.REALISTIC_DEPS)) + 1)
        )[:dep_count]

        for idx, (pkg, installed, latest) in enumerate(dep_list):
            is_actionable = idx < action_count
            notes = None
            if is_actionable:
                notes = f"Security update required: {installed} has CVE-2026-{1000 + idx}. Upgrade to {latest} immediately."

            deps.append(
                DependencyStatus(
                    package=pkg,
                    installed_version=installed,
                    upstream_latest=latest,
                    healthy=not is_actionable,
                    severity="error" if is_actionable else "info",
                    notes=notes,
                )
            )

        task_ids = [f"TASK-{i:03d}" for i in range(action_count)]
        return DependencyReportData(statuses=deps, created_task_ids=task_ids)

    @staticmethod
    def large_payload(dep_count: int = 8, note_length: int = 1000) -> DependencyReportData:
        """Generate large-payload report: N deps with verbose notes, ~80KB."""
        deps = []
        action_count = max(1, int(dep_count * 0.5))

        dep_list = (
            DependencyReportGenerator.REALISTIC_DEPS
            * ((dep_count // len(DependencyReportGenerator.REALISTIC_DEPS)) + 1)
        )[:dep_count]

        verbose_note = "x" * note_length

        for idx, (pkg, installed, latest) in enumerate(dep_list):
            is_actionable = idx < action_count
            notes = None
            if is_actionable:
                notes = f"Update {pkg} from {installed} to {latest}. {verbose_note}"

            deps.append(
                DependencyStatus(
                    package=pkg,
                    installed_version=installed,
                    upstream_latest=latest,
                    healthy=not is_actionable,
                    severity="warning" if is_actionable else "info",
                    notes=notes,
                )
            )

        task_ids = [f"TASK-{i:03d}" for i in range(action_count)]
        return DependencyReportData(statuses=deps, created_task_ids=task_ids)

    @staticmethod
    def extra_large(dep_count: int = 50, actionable_pct: float = 0.5) -> DependencyReportData:
        """Generate extra-large report: 50+ deps, monorepo scale, ~50KB."""
        deps = []
        action_count = max(1, int(dep_count * actionable_pct))

        dep_list = (
            DependencyReportGenerator.REALISTIC_DEPS
            * ((dep_count // len(DependencyReportGenerator.REALISTIC_DEPS)) + 1)
        )[:dep_count]

        for idx, (pkg, installed, latest) in enumerate(dep_list):
            is_actionable = idx < action_count
            notes = None
            if is_actionable:
                notes = f"Dependency {pkg} version {installed} is outdated. Latest: {latest}. Security patches: critical."

            deps.append(
                DependencyStatus(
                    package=pkg,
                    installed_version=installed,
                    upstream_latest=latest,
                    healthy=not is_actionable,
                    severity="error" if is_actionable else "info",
                    notes=notes,
                )
            )

        task_ids = [f"TASK-{i:04d}" for i in range(action_count)]
        return DependencyReportData(statuses=deps, created_task_ids=task_ids)

    @staticmethod
    def custom(
        dep_count: int = 10,
        actionable_pct: float = 0.2,
        note_length: int = 100,
    ) -> DependencyReportData:
        """Generate custom report with arbitrary parameters."""
        deps = []
        action_count = max(0, int(dep_count * actionable_pct))

        dep_list = (
            DependencyReportGenerator.REALISTIC_DEPS
            * ((dep_count // len(DependencyReportGenerator.REALISTIC_DEPS)) + 1)
        )[:dep_count]

        verbose_note = "x" * note_length if note_length > 0 else ""

        for idx, (pkg, installed, latest) in enumerate(dep_list):
            is_actionable = idx < action_count
            notes = None
            if is_actionable and note_length > 0:
                notes = f"Action required for {pkg}: {verbose_note}"
            elif is_actionable:
                notes = f"Update {pkg} to {latest}"

            deps.append(
                DependencyStatus(
                    package=pkg,
                    installed_version=installed,
                    upstream_latest=latest,
                    healthy=not is_actionable,
                    severity="warning" if is_actionable else "info",
                    notes=notes,
                )
            )

        task_ids = [f"TASK-{i:04d}" for i in range(action_count)]
        return DependencyReportData(statuses=deps, created_task_ids=task_ids)
