# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests: observation_coverage deriver → rule pipeline."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.slow

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.rules.observation_coverage import ObservationCoverageRule
from operations_center.insights.derivers.observation_coverage import ObservationCoverageDeriver
from operations_center.insights.normalizer import InsightNormalizer

from test_insights import _make_snapshot as make_snapshot


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


class TestObservationCoveragePipeline:
    """Wire deriver → rule and verify CandidateSpecs come out."""

    def test_repeated_unknown_test_signal_produces_candidate(self) -> None:
        """3 consecutive unknown test_status snapshots → at least one CandidateSpec."""
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=t0 - timedelta(hours=i),
                test_status="unknown",
                dependency_status="available",
            )
            for i in range(3)
        ]

        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate(insights)

        assert len(candidates) >= 1
        assert all(isinstance(c, CandidateSpec) for c in candidates)

        test_candidates = [c for c in candidates if c.subject == "test_signal"]
        assert len(test_candidates) == 1

        spec = test_candidates[0]
        assert "test_signal" in spec.proposal_outline.title_hint
        assert spec.proposal_outline.title_hint == (
            "Restore repeated missing test_signal coverage"
        )
        assert spec.confidence == "high"  # 3 consecutive → high

    def test_dependency_drift_flows_through_pipeline(self) -> None:
        """3 consecutive not_available dependency_status → dependency_drift candidate."""
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=t0 - timedelta(hours=i),
                test_status="discoverable",
                dependency_status="not_available",
            )
            for i in range(3)
        ]

        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate(insights)

        assert len(candidates) >= 1

        dep_candidates = [c for c in candidates if c.subject == "dependency_drift"]
        assert len(dep_candidates) == 1

        spec = dep_candidates[0]
        assert "dependency_drift" in spec.proposal_outline.title_hint
        assert spec.proposal_outline.title_hint == (
            "Restore repeated missing dependency_drift coverage"
        )


# ── Tests for None observed_at (Stage 3 coverage) ─────────────────────


class TestObservationCoverageWithNoneObservedAt:
    """Verify ObservationCoverageDeriver handles signal.observed_at=None correctly."""

    def test_unknown_test_signal_with_none_observed_at(self) -> None:
        """CheckSignal with None observed_at should use snapshot's observed_at as fallback."""
        from pathlib import Path
        from operations_center.observer.models import CheckSignal, RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot, TodoSignal, DependencyDriftSignal

        deriver = ObservationCoverageDeriver(_normalizer())
        now = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snap = RepoStateSnapshot(
            run_id="obs_none_check",
            observed_at=now,
            source_command="test",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/tmp/test-repo"),
                current_branch="main",
                base_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(status="unknown", observed_at=None),  # signal has no timestamp
                dependency_drift=DependencyDriftSignal(status="available"),
                todo_signal=TodoSignal(),
            ),
        )
        insights = deriver.derive([snap])
        assert len(insights) >= 1
        test_insights = [i for i in insights if i.subject == "test_signal"]
        assert len(test_insights) == 1
        # Should use snapshot.observed_at as fallback
        assert test_insights[0].first_seen_at == now
        assert test_insights[0].last_seen_at == now

    def test_persistent_unknown_with_none_observed_at(self) -> None:
        """Multiple snapshots with CheckSignal.observed_at=None should use snapshot times."""
        from pathlib import Path
        from operations_center.observer.models import CheckSignal, RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot, TodoSignal, DependencyDriftSignal

        deriver = ObservationCoverageDeriver(_normalizer())
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = []
        for i in range(3):
            snap = RepoStateSnapshot(
                run_id=f"obs_none_{i}",
                observed_at=t0 - timedelta(hours=i),
                source_command="test",
                repo=RepoContextSnapshot(
                    name="test-repo",
                    path=Path("/tmp/test-repo"),
                    current_branch="main",
                    base_branch="main",
                    is_dirty=False,
                ),
                signals=RepoSignalsSnapshot(
                    test_signal=CheckSignal(status="unknown", observed_at=None),
                    dependency_drift=DependencyDriftSignal(status="available"),
                    todo_signal=TodoSignal(),
                ),
            )
            snaps.append(snap)

        insights = deriver.derive(snaps)
        test_insights = [i for i in insights if i.subject == "test_signal"]
        # Should get persistent insight (consecutive unavailable)
        persistent = [i for i in test_insights if "persistent" in i.dedup_key]
        assert len(persistent) == 1
        # Timestamps should come from snapshots (fallback)
        assert persistent[0].first_seen_at == t0 - timedelta(hours=2)
        assert persistent[0].last_seen_at == t0
