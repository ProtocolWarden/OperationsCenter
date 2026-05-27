# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for TypeHealthDeriver."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.insights.derivers.type_health import TypeHealthDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    CheckSignal,
    DependencyDriftSignal,
    TypeError,
    TypeSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    TodoSignal,
)


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


def _make_snapshot(
    *,
    type_status: str = "unavailable",
    error_count: int = 0,
    top_errors: list[TypeError] | None = None,
    observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    now = observed_at or datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    if top_errors is None:
        top_errors = []
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
        architecture_signal=ArchitectureSignal(status="unavailable"),
        benchmark_signal=BenchmarkSignal(status="unavailable"),
        security_signal=SecuritySignal(status="unavailable"),
        type_signal=TypeSignal(
            status=type_status,
            error_count=error_count,
            top_errors=top_errors,
            source="pyright",
        ),
    )
    return RepoStateSnapshot(
        run_id="obs_test_001",
        observed_at=now,
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


class TestTypeHealthDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal_no_insights(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        snap = _make_snapshot(type_status="unavailable")
        assert deriver.derive([snap]) == []

    def test_clean_status_no_insights(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        snap = _make_snapshot(type_status="clean", error_count=0)
        assert deriver.derive([snap]) == []

    def test_errors_present(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        errors = [
            TypeError(code="error", path="file1.py", line=10, col=0, message="Type mismatch"),
            TypeError(code="error", path="file2.py", line=20, col=0, message="Type mismatch"),
        ]
        snap = _make_snapshot(
            type_status="errors",
            error_count=2,
            top_errors=errors,
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert "present" in insights[0].dedup_key
        assert insights[0].evidence["error_count"] == 2

    def test_error_count_increase_worsened(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            type_status="errors",
            error_count=5,
            top_errors=[TypeError(code="error", path="f.py", line=1, col=0, message="Type error") for _ in range(5)],
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            type_status="errors",
            error_count=2,
            top_errors=[TypeError(code="error", path="f.py", line=1, col=0, message="Type error") for _ in range(2)],
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 2
        worsened = [i for i in insights if "worsened" in i.dedup_key]
        assert len(worsened) == 1
        assert worsened[0].evidence["delta"] == 3
        assert worsened[0].evidence["current_count"] == 5
        assert worsened[0].evidence["previous_count"] == 2

    def test_error_count_decrease_improved(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            type_status="errors",
            error_count=2,
            top_errors=[TypeError(code="error", path="f.py", line=1, col=0, message="Type error") for _ in range(2)],
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            type_status="errors",
            error_count=5,
            top_errors=[TypeError(code="error", path="f.py", line=1, col=0, message="Type error") for _ in range(5)],
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        improved = [i for i in insights if "improved" in i.dedup_key]
        assert len(improved) == 1
        assert improved[0].evidence["delta"] == 3
        assert improved[0].evidence["current_count"] == 2
        assert improved[0].evidence["previous_count"] == 5

    def test_errors_to_clean_resolved(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            type_status="clean",
            error_count=0,
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            type_status="errors",
            error_count=5,
            top_errors=[TypeError(code="error", path="f.py", line=1, col=0, message="Type error") for _ in range(5)],
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        resolved = [i for i in insights if "resolved" in i.dedup_key]
        assert len(resolved) == 1
        assert resolved[0].evidence["current_count"] == 0
        assert resolved[0].evidence["previous_count"] == 5

    def test_clean_to_errors_regressed(self) -> None:
        deriver = TypeHealthDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            type_status="errors",
            error_count=3,
            top_errors=[TypeError(code="error", path="f.py", line=1, col=0, message="Type error") for _ in range(3)],
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            type_status="clean",
            error_count=0,
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        regressed = [i for i in insights if "regressed" in i.dedup_key]
        assert len(regressed) == 1
        assert regressed[0].evidence["current_count"] == 3
        assert regressed[0].evidence["previous_count"] == 0
