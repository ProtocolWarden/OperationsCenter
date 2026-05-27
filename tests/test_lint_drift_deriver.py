# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for LintDriftDeriver."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.insights.derivers.lint_drift import LintDriftDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    CheckSignal,
    DependencyDriftSignal,
    LintViolation,
    LintSignal,
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
    lint_status: str = "unavailable",
    violation_count: int = 0,
    top_violations: list[LintViolation] | None = None,
    observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    now = observed_at or datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    if top_violations is None:
        top_violations = []
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
        architecture_signal=ArchitectureSignal(status="unavailable"),
        benchmark_signal=BenchmarkSignal(status="unavailable"),
        security_signal=SecuritySignal(status="unavailable"),
        lint_signal=LintSignal(
            status=lint_status,
            violation_count=violation_count,
            top_violations=top_violations,
            source="ruff",
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


class TestLintDriftDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal_no_insights(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        snap = _make_snapshot(lint_status="unavailable")
        assert deriver.derive([snap]) == []

    def test_clean_status_no_insights(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        snap = _make_snapshot(lint_status="clean", violation_count=0)
        assert deriver.derive([snap]) == []

    def test_violations_present(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        violations = [
            LintViolation(code="E501", path="file1.py", line=10, col=0, message="Line too long"),
            LintViolation(code="F401", path="file2.py", line=20, col=0, message="Unused import"),
        ]
        snap = _make_snapshot(
            lint_status="violations",
            violation_count=2,
            top_violations=violations,
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert "present" in insights[0].dedup_key
        assert insights[0].evidence["violation_count"] == 2

    def test_violation_count_increase_worsened(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            lint_status="violations",
            violation_count=5,
            top_violations=[LintViolation(code="E501", path="f.py", line=1, col=0, message="Error") for _ in range(5)],
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            lint_status="violations",
            violation_count=2,
            top_violations=[LintViolation(code="E501", path="f.py", line=1, col=0, message="Error") for _ in range(2)],
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 2
        worsened = [i for i in insights if "worsened" in i.dedup_key]
        assert len(worsened) == 1
        assert worsened[0].evidence["delta"] == 3
        assert worsened[0].evidence["current_count"] == 5
        assert worsened[0].evidence["previous_count"] == 2

    def test_violation_count_decrease_improved(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            lint_status="violations",
            violation_count=2,
            top_violations=[LintViolation(code="E501", path="f.py", line=1, col=0, message="Error") for _ in range(2)],
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            lint_status="violations",
            violation_count=5,
            top_violations=[LintViolation(code="E501", path="f.py", line=1, col=0, message="Error") for _ in range(5)],
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        improved = [i for i in insights if "improved" in i.dedup_key]
        assert len(improved) == 1
        assert improved[0].evidence["delta"] == 3
        assert improved[0].evidence["current_count"] == 2
        assert improved[0].evidence["previous_count"] == 5

    def test_violations_to_clean_resolved(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            lint_status="clean",
            violation_count=0,
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            lint_status="violations",
            violation_count=5,
            top_violations=[LintViolation(code="E501", path="f.py", line=1, col=0, message="Error") for _ in range(5)],
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        resolved = [i for i in insights if "resolved" in i.dedup_key]
        assert len(resolved) == 1
        assert resolved[0].evidence["current_count"] == 0
        assert resolved[0].evidence["previous_count"] == 5

    def test_clean_to_violations_regressed(self) -> None:
        deriver = LintDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            lint_status="violations",
            violation_count=3,
            top_violations=[LintViolation(code="E501", path="f.py", line=1, col=0, message="Error") for _ in range(3)],
            observed_at=newer,
        )
        snap_older = _make_snapshot(
            lint_status="clean",
            violation_count=0,
            observed_at=older,
        )
        insights = deriver.derive([snap_recent, snap_older])
        regressed = [i for i in insights if "regressed" in i.dedup_key]
        assert len(regressed) == 1
        assert regressed[0].evidence["current_count"] == 3
        assert regressed[0].evidence["previous_count"] == 0
