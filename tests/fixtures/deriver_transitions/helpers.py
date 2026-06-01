# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Fixture builders for transition testing across derivers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    CheckSignal,
    DependencyDriftSignal,
    LintSignal,
    LintViolation,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    TodoSignal,
    TypeError,
    TypeSignal,
)


class TransitionFixture:
    """Build snapshot pairs for deriver transition testing."""

    @staticmethod
    def _base_snapshot(
        timestamp: datetime,
        run_id: str = "test_run",
        dependency_drift_status: str = "available",
        lint_status: str = "clean",
        lint_count: int = 0,
        lint_violations: list[LintViolation] | None = None,
        type_status: str = "clean",
        type_count: int = 0,
        type_errors: list[TypeError] | None = None,
    ) -> RepoStateSnapshot:
        """Create a base snapshot with configurable signals."""
        signals = RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status=dependency_drift_status),
            todo_signal=TodoSignal(),
            architecture_signal=ArchitectureSignal(status="unavailable"),
            benchmark_signal=BenchmarkSignal(status="unavailable"),
            security_signal=SecuritySignal(status="unavailable"),
            lint_signal=LintSignal(
                status=lint_status,
                violation_count=lint_count,
                top_violations=lint_violations or [],
                distinct_file_count=len({v.path for v in (lint_violations or [])})
                if lint_violations
                else 0,
            ),
            type_signal=TypeSignal(
                status=type_status,
                error_count=type_count,
                top_errors=type_errors or [],
                distinct_file_count=len({e.path for e in (type_errors or [])})
                if type_errors
                else 0,
            ),
        )
        return RepoStateSnapshot(
            run_id=run_id,
            observed_at=timestamp,
            source_command="test",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/tmp/test-repo"),
                current_branch="main",
                base_branch="main",
                is_dirty=False,
            ),
            signals=signals,
        )

    @staticmethod
    def dependency_drift_pair(
        from_status: str,
        to_status: str,
        timestamp_offset_seconds: int = 60,
    ) -> tuple[RepoStateSnapshot, RepoStateSnapshot]:
        """Create prev/curr snapshots with dependency_drift status transition.

        Returns (curr, prev) where curr is more recent (snapshots[0], snapshots[1]).
        """
        ts_prev = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        ts_curr = ts_prev + timedelta(seconds=timestamp_offset_seconds)

        prev = TransitionFixture._base_snapshot(ts_prev, dependency_drift_status=from_status)
        curr = TransitionFixture._base_snapshot(ts_curr, dependency_drift_status=to_status)
        return (curr, prev)

    @staticmethod
    def lint_signal_pair(
        from_status: str,
        to_status: str,
        from_count: int = 0,
        to_count: int = 0,
        timestamp_offset_seconds: int = 60,
    ) -> tuple[RepoStateSnapshot, RepoStateSnapshot]:
        """Create prev/curr snapshots with lint_signal status/count transition.

        Returns (curr, prev) where curr is more recent.
        """
        ts_prev = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        ts_curr = ts_prev + timedelta(seconds=timestamp_offset_seconds)

        from_violations = []
        if from_count > 0:
            from_violations = [
                LintViolation(
                    path=f"src/file{i}.py",
                    line=i + 1,
                    col=0,
                    code=f"E{100 + i}",
                    message=f"Error {i}",
                )
                for i in range(from_count)
            ]

        to_violations = []
        if to_count > 0:
            to_violations = [
                LintViolation(
                    path=f"src/file{i}.py",
                    line=i + 1,
                    col=0,
                    code=f"E{100 + i}",
                    message=f"Error {i}",
                )
                for i in range(to_count)
            ]

        prev = TransitionFixture._base_snapshot(
            ts_prev,
            lint_status=from_status,
            lint_count=from_count,
            lint_violations=from_violations,
        )
        curr = TransitionFixture._base_snapshot(
            ts_curr,
            lint_status=to_status,
            lint_count=to_count,
            lint_violations=to_violations,
        )
        return (curr, prev)

    @staticmethod
    def type_signal_pair(
        from_status: str,
        to_status: str,
        from_count: int = 0,
        to_count: int = 0,
        timestamp_offset_seconds: int = 60,
    ) -> tuple[RepoStateSnapshot, RepoStateSnapshot]:
        """Create prev/curr snapshots with type_signal status/count transition.

        Returns (curr, prev) where curr is more recent.
        """
        ts_prev = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        ts_curr = ts_prev + timedelta(seconds=timestamp_offset_seconds)

        from_errors = []
        if from_count > 0:
            from_errors = [
                TypeError(
                    path=f"src/file{i}.py",
                    line=i + 1,
                    col=0,
                    code=f"T{100 + i}",
                    message=f"Type error {i}",
                )
                for i in range(from_count)
            ]

        to_errors = []
        if to_count > 0:
            to_errors = [
                TypeError(
                    path=f"src/file{i}.py",
                    line=i + 1,
                    col=0,
                    code=f"T{100 + i}",
                    message=f"Type error {i}",
                )
                for i in range(to_count)
            ]

        prev = TransitionFixture._base_snapshot(
            ts_prev,
            type_status=from_status,
            type_count=from_count,
            type_errors=from_errors,
        )
        curr = TransitionFixture._base_snapshot(
            ts_curr,
            type_status=to_status,
            type_count=to_count,
            type_errors=to_errors,
        )
        return (curr, prev)
