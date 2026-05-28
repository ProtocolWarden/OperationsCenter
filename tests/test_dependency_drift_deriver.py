# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for DependencyDriftDeriver."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    CheckSignal,
    DependencyDriftSignal,
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
    dependency_drift_status: str = "not_available",
    observed_at: datetime | None = None,
    signal_observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    now = observed_at or datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status=dependency_drift_status, observed_at=signal_observed_at),
        todo_signal=TodoSignal(),
        architecture_signal=ArchitectureSignal(status="unavailable"),
        benchmark_signal=BenchmarkSignal(status="unavailable"),
        security_signal=SecuritySignal(status="unavailable"),
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


class TestDependencyDriftDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_single_available_produces_current_insight(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        snap = _make_snapshot(dependency_drift_status="available")
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "dependency_drift_continuity"
        assert "current" in insights[0].dedup_key
        assert insights[0].evidence["current_status"] == "available"

    def test_two_available_produces_current_and_persistent(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(dependency_drift_status="available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="available", observed_at=older)
        # snapshots[0] is the most recent
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 2
        dedup_keys = {i.dedup_key for i in insights}
        assert any("current" in k for k in dedup_keys)
        assert any("persistent" in k for k in dedup_keys)
        persistent = [i for i in insights if "persistent" in i.dedup_key][0]
        assert persistent.evidence["consecutive_snapshots"] == 2

    def test_transition_available_to_not_available(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        # snapshots[0] is most recent (not_available), snapshots[1] was available
        snap_recent = _make_snapshot(dependency_drift_status="not_available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="available", observed_at=older)
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 1
        assert "transition" in insights[0].dedup_key
        assert insights[0].evidence["previous_status"] == "available"
        assert insights[0].evidence["current_status"] == "not_available"

    def test_single_not_available_no_insights(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        snap = _make_snapshot(dependency_drift_status="not_available")
        assert deriver.derive([snap]) == []

    def test_timestamps_first_and_last_seen(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(dependency_drift_status="available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="available", observed_at=older)
        insights = deriver.derive([snap_recent, snap_older])
        current = [i for i in insights if "current" in i.dedup_key][0]
        persistent = [i for i in insights if "persistent" in i.dedup_key][0]
        assert current.first_seen_at == older
        assert current.last_seen_at == newer
        assert persistent.first_seen_at == older
        assert persistent.last_seen_at == newer

    def test_transition_not_available_to_available_recovery(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(dependency_drift_status="available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="not_available", observed_at=older)
        insights = deriver.derive([snap_recent, snap_older])
        # Recovery generates both 'current' and 'recovery' insights
        assert len(insights) == 2
        recovery = [i for i in insights if "recovery" in i.dedup_key][0]
        assert recovery.evidence["previous_status"] == "not_available"
        assert recovery.evidence["current_status"] == "available"

    def test_recovery_followed_by_persistence(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        t1 = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        t3 = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        snap_broken = _make_snapshot(dependency_drift_status="not_available", observed_at=t1)
        snap_recovered = _make_snapshot(dependency_drift_status="available", observed_at=t2)
        snap_persistent = _make_snapshot(dependency_drift_status="available", observed_at=t3)
        insights = deriver.derive([snap_persistent, snap_recovered, snap_broken])
        assert len(insights) == 2
        kinds = {i.dedup_key for i in insights}
        assert any("current" in k for k in kinds)
        assert any("persistent" in k for k in kinds)


# ── Tests for None observed_at (Stage 3 coverage) ─────────────────────


class TestDependencyDriftWithNoneObservedAt:
    """Verify DependencyDriftDeriver handles signal.observed_at=None correctly."""

    def test_available_with_none_signal_observed_at(self) -> None:
        """Signal has None observed_at, but snapshot has valid observed_at."""
        deriver = DependencyDriftDeriver(_normalizer())
        snap = _make_snapshot(
            dependency_drift_status="available",
            signal_observed_at=None,  # signal has no timestamp
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "dependency_drift_continuity"
        # Should use snapshot.observed_at as fallback
        assert insights[0].first_seen_at == snap.observed_at
        assert insights[0].last_seen_at == snap.observed_at

    def test_persistent_with_none_signal_observed_at(self) -> None:
        """Multiple available snapshots with signal.observed_at=None should use snapshot fallback."""
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            dependency_drift_status="available",
            observed_at=newer,
            signal_observed_at=None,
        )
        snap_older = _make_snapshot(
            dependency_drift_status="available",
            observed_at=older,
            signal_observed_at=None,
        )
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 2
        persistent = [i for i in insights if "persistent" in i.dedup_key][0]
        # Should use snapshot.observed_at times as fallback
        assert persistent.first_seen_at == older
        assert persistent.last_seen_at == newer

    def test_transition_with_none_signal_observed_at(self) -> None:
        """Transition from available to not_available with None signal observed_at."""
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(
            dependency_drift_status="not_available",
            observed_at=newer,
            signal_observed_at=None,
        )
        snap_older = _make_snapshot(
            dependency_drift_status="available",
            observed_at=older,
            signal_observed_at=None,
        )
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 1
        assert "transition" in insights[0].dedup_key
        # Should use snapshot times as fallback
        assert insights[0].first_seen_at == older
        assert insights[0].last_seen_at == newer
