# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for Phase 5 insight derivers: architecture_drift, benchmark_regression, security_vuln."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.insights.derivers.architecture_drift import ArchitectureDriftDeriver
from operations_center.insights.derivers.benchmark_regression import BenchmarkRegressionDeriver
from operations_center.insights.derivers.security_vuln import SecurityVulnDeriver
from operations_center.insights.derivers.coverage_gap import CoverageGapDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    CoverageSignal,
    CheckSignal,
    TodoSignal,
)


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


def _make_snapshot(
    *,
    architecture_signal: ArchitectureSignal | None = None,
    benchmark_signal: BenchmarkSignal | None = None,
    security_signal: SecuritySignal | None = None,
    coverage_signal: CoverageSignal | None = None,
) -> RepoStateSnapshot:
    now = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
        architecture_signal=architecture_signal or ArchitectureSignal(status="unavailable"),
        benchmark_signal=benchmark_signal or BenchmarkSignal(status="unavailable"),
        security_signal=security_signal or SecuritySignal(status="unavailable"),
        coverage_signal=coverage_signal or CoverageSignal(status="unavailable"),
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


# ── ArchitectureDriftDeriver ─────────────────────────────────────────


class TestArchitectureDriftDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(architecture_signal=ArchitectureSignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_healthy_no_insights(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="healthy",
                coupling_score=0.3,
                max_import_depth=2,
                circular_dependencies=[],
                summary="all good",
            )
        )
        assert deriver.derive([snap]) == []

    def test_coupling_high(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.85,
                max_import_depth=3,
                circular_dependencies=["a -> b -> a"],
                summary="high coupling",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "arch_drift"
        assert insights[0].subject == "coupling"
        assert insights[0].status == "high"
        assert insights[0].evidence["coupling_score"] == 0.85
        assert insights[0].evidence["circular_dependencies"] == ["a -> b -> a"]
        assert "arch_drift" in insights[0].dedup_key
        assert "coupling_high" in insights[0].dedup_key

    def test_module_bloat(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.2,
                max_import_depth=8,
                summary="deep imports",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "arch_drift"
        assert insights[0].subject == "module_depth"
        assert insights[0].status == "bloated"
        assert insights[0].evidence["max_import_depth"] == 8
        assert "module_bloat" in insights[0].dedup_key

    def test_both_coupling_and_bloat(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=1.2,
                max_import_depth=10,
                circular_dependencies=[],
                summary="both issues",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 2
        kinds = {i.dedup_key for i in insights}
        assert any("coupling_high" in k for k in kinds)
        assert any("module_bloat" in k for k in kinds)

    def test_coupling_at_threshold(self) -> None:
        """Exactly 0.7 should trigger."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings", coupling_score=0.7, max_import_depth=2, summary=""
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert "coupling_high" in insights[0].dedup_key

    def test_depth_at_threshold(self) -> None:
        """Exactly 6 should trigger."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings", coupling_score=0.1, max_import_depth=6, summary=""
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert "module_bloat" in insights[0].dedup_key

    def test_below_thresholds(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="healthy", coupling_score=0.69, max_import_depth=5, summary=""
            )
        )
        assert deriver.derive([snap]) == []

    def test_timestamps(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings", coupling_score=0.9, max_import_depth=2, summary=""
            )
        )
        insights = deriver.derive([snap])
        assert insights[0].first_seen_at == snap.observed_at
        assert insights[0].last_seen_at == snap.observed_at


# ── BenchmarkRegressionDeriver ───────────────────────────────────────


class TestBenchmarkRegressionDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(benchmark_signal=BenchmarkSignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_nominal_no_insights(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="nominal", benchmark_count=5, regressions=[], summary="ok"
            )
        )
        assert deriver.derive([snap]) == []

    def test_regression_present(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression",
                source="pytest_benchmark",
                benchmark_count=3,
                regressions=["test_slow: stddev (0.05) > 2x mean (0.01)"],
                summary="3 benchmark(s) found; 1 regression(s)",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        i = insights[0]
        assert i.kind == "benchmark_regression"
        assert i.subject == "benchmark"
        assert i.status == "regression"
        assert i.evidence["benchmark_count"] == 3
        assert len(i.evidence["regressions"]) == 1
        assert "benchmark_regression" in i.dedup_key
        assert "present" in i.dedup_key
        assert i.first_seen_at == snap.observed_at
        assert i.last_seen_at == snap.observed_at

    def test_regression_status_but_empty_list(self) -> None:
        """status=regression but empty regressions list should not emit."""
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression", benchmark_count=1, regressions=[]
            )
        )
        assert deriver.derive([snap]) == []


# ── SecurityVulnDeriver ──────────────────────────────────────────────


class TestSecurityVulnDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(security_signal=SecuritySignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_clean_no_insights(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(
                status="clean", advisory_count=0, critical_count=0, high_count=0
            )
        )
        assert deriver.derive([snap]) == []

    def test_advisories_present(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(
                status="advisories",
                source="npm_audit",
                advisory_count=5,
                critical_count=1,
                high_count=2,
                summary="5 advisory(ies); 1 critical; 2 high",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        i = insights[0]
        assert i.kind == "security_vuln"
        assert i.subject == "security"
        assert i.status == "advisories"
        assert i.evidence["advisory_count"] == 5
        assert i.evidence["critical_count"] == 1
        assert i.evidence["high_count"] == 2
        assert "security_vuln" in i.dedup_key
        assert "present" in i.dedup_key
        assert i.first_seen_at == snap.observed_at
        assert i.last_seen_at == snap.observed_at

    def test_advisories_status_but_zero_count(self) -> None:
        """status=advisories but advisory_count=0 should not emit."""
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(status="advisories", advisory_count=0)
        )
        assert deriver.derive([snap]) == []


# ── Coverage Gap Deriver ────────────────────────────────────────────

class TestCoverageGapDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = CoverageGapDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = CoverageGapDeriver(_normalizer())
        snap = _make_snapshot(coverage_signal=CoverageSignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_measured_good_coverage(self) -> None:
        deriver = CoverageGapDeriver(_normalizer())
        snap = _make_snapshot(
            coverage_signal=CoverageSignal(
                status="measured",
                total_coverage_pct=85.0,
                uncovered_file_count=2,
                uncovered_threshold_pct=80.0,
                source="coverage.py",
                summary="coverage is healthy",
            )
        )
        assert deriver.derive([snap]) == []

    def test_low_overall_coverage(self) -> None:
        deriver = CoverageGapDeriver(_normalizer())
        snap = _make_snapshot(
            coverage_signal=CoverageSignal(
                status="measured",
                total_coverage_pct=45.0,
                uncovered_file_count=10,
                uncovered_threshold_pct=80.0,
                source="coverage.py",
                summary="coverage is low",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) >= 1
        assert any(i.kind == "coverage_gap/low_overall" for i in insights)


# ── Tests for None observed_at (Stage 3 coverage) ─────────────────────

class TestArchitectureDriftWithNoneObservedAt:
    """Verify ArchitectureDriftDeriver handles signal.observed_at=None correctly."""

    def test_coupling_high_with_none_signal_observed_at(self) -> None:
        """Signal has None observed_at, but snapshot has valid observed_at."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.85,
                circular_dependencies=["a -> b -> a"],
                summary="high coupling",
                observed_at=None,  # signal has no timestamp
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "arch_drift"
        # Should use snapshot.observed_at as fallback
        assert insights[0].first_seen_at == snap.observed_at
        assert insights[0].last_seen_at == snap.observed_at

    def test_module_bloat_with_none_signal_observed_at(self) -> None:
        """Module depth issue with signal.observed_at=None should use snapshot fallback."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.2,
                max_import_depth=8,
                summary="deep imports",
                observed_at=None,
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].subject == "module_depth"
        assert insights[0].first_seen_at == snap.observed_at

    def test_both_issues_with_none_signal_observed_at(self) -> None:
        """Both coupling and depth issues with signal.observed_at=None."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.9,
                max_import_depth=8,
                summary="multiple issues",
                observed_at=None,
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 2
        for insight in insights:
            assert insight.first_seen_at == snap.observed_at


class TestBenchmarkRegressionWithNoneObservedAt:
    """Verify BenchmarkRegressionDeriver handles signal.observed_at=None correctly."""

    def test_regression_present_with_none_signal_observed_at(self) -> None:
        """Benchmark regression with signal.observed_at=None should use snapshot fallback."""
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression",
                source="pytest_benchmark",
                benchmark_count=3,
                regressions=["test_slow: stddev (0.05) > 2x mean (0.01)"],
                summary="benchmarks regressed",
                observed_at=None,  # signal has no timestamp
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "benchmark_regression"
        # Should use snapshot.observed_at as fallback
        assert insights[0].first_seen_at == snap.observed_at
        assert insights[0].last_seen_at == snap.observed_at
        assert insights[0].evidence["benchmark_count"] == 3


class TestSecurityVulnWithNoneObservedAt:
    """Verify SecurityVulnDeriver handles signal.observed_at=None correctly."""

    def test_advisories_present_with_none_signal_observed_at(self) -> None:
        """Security advisories with signal.observed_at=None should use snapshot fallback."""
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(
                status="advisories",
                source="npm_audit",
                advisory_count=5,
                critical_count=1,
                high_count=2,
                summary="5 advisory(ies); 1 critical; 2 high",
                observed_at=None,  # signal has no timestamp
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "security_vuln"
        # Should use snapshot.observed_at as fallback
        assert insights[0].first_seen_at == snap.observed_at
        assert insights[0].last_seen_at == snap.observed_at
        assert insights[0].evidence["advisory_count"] == 5


class TestCoverageGapWithNoneObservedAt:
    """Verify CoverageGapDeriver handles signal.observed_at=None correctly."""

    def test_low_coverage_with_none_signal_observed_at(self) -> None:
        """Low coverage with signal.observed_at=None should use snapshot fallback."""
        deriver = CoverageGapDeriver(_normalizer())
        snap = _make_snapshot(
            coverage_signal=CoverageSignal(
                status="measured",
                total_coverage_pct=45.0,
                uncovered_file_count=10,
                uncovered_threshold_pct=80.0,
                source="coverage.py",
                summary="coverage is low",
                observed_at=None,  # signal has no timestamp
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) >= 1
        low_overall = next(
            (i for i in insights if i.kind == "coverage_gap/low_overall"), None
        )
        assert low_overall is not None
        # Should use snapshot.observed_at as fallback
        assert low_overall.first_seen_at == snap.observed_at
        assert low_overall.last_seen_at == snap.observed_at


class TestNoneObservedAtEdgeCases:
    """Edge cases for None observed_at across signal types."""

    def test_multiple_snapshots_with_none_signal_observed_at(self) -> None:
        """Multiple snapshots; only first has the signal issue with None observed_at."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        now = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        past = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)

        snap1 = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.85,
                summary="high coupling",
                observed_at=None,
            )
        )
        snap1.observed_at = now

        snap2 = _make_snapshot(
            architecture_signal=ArchitectureSignal(status="healthy", observed_at=None)
        )
        snap2.observed_at = past

        # Deriver looks at snapshots[0], which should use its snapshot.observed_at
        insights = deriver.derive([snap1, snap2])
        assert len(insights) == 1
        assert insights[0].first_seen_at == now

    def test_signal_data_present_but_observed_at_none(self) -> None:
        """Signal has complete data but observed_at is None (e.g., cached result)."""
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression",
                source="cached_result",
                benchmark_count=10,
                regressions=[
                    "test_1: slower",
                    "test_2: slower",
                ],
                summary="2 regressions",
                observed_at=None,  # result was cached
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        # Data should be captured in evidence
        assert insights[0].evidence["benchmark_count"] == 10
        # Timestamp should come from snapshot
        assert insights[0].first_seen_at == snap.observed_at


class TestNoneObservedAtNoDataScenarios:
    """Edge case: signal with None observed_at AND no meaningful data (unavailable/empty)."""

    def test_architecture_signal_no_data_with_none_observed_at(self) -> None:
        """Architecture signal with None observed_at and unavailable status produces no insights."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="unavailable",
                observed_at=None,  # No timestamp
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 0

    def test_benchmark_signal_no_data_with_none_observed_at(self) -> None:
        """Benchmark signal with None observed_at and empty regressions produces no insights."""
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression",
                benchmark_count=0,
                regressions=[],  # No data
                observed_at=None,
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 0

    def test_security_signal_no_data_with_none_observed_at(self) -> None:
        """Security signal with None observed_at and zero advisories produces no insights."""
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(
                status="advisories",
                advisory_count=0,
                critical_count=0,
                high_count=0,
                observed_at=None,  # No timestamp
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 0

    def test_coverage_signal_no_data_with_none_observed_at(self) -> None:
        """Coverage signal with None observed_at and good coverage produces no insights."""
        deriver = CoverageGapDeriver(_normalizer())
        snap = _make_snapshot(
            coverage_signal=CoverageSignal(
                status="measured",
                total_coverage_pct=95.0,  # Good coverage, no gap
                uncovered_file_count=0,
                uncovered_threshold_pct=80.0,
                observed_at=None,
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 0


# ── Wiring test ──────────────────────────────────────────────────────


class TestBuildInsightServiceWiring:
    def test_derivers_include_phase5_before_cross_signal(self) -> None:
        """All three Phase 5 derivers appear in the derivers list before CrossSignalDeriver."""
        from operations_center.entrypoints.autonomy_cycle.main import build_insight_service

        service = build_insight_service()
        deriver_types = [type(d).__name__ for d in service.derivers]

        assert "ArchitectureDriftDeriver" in deriver_types
        assert "BenchmarkRegressionDeriver" in deriver_types
        assert "SecurityVulnDeriver" in deriver_types
        assert "CrossSignalDeriver" in deriver_types

        cross_idx = deriver_types.index("CrossSignalDeriver")
        assert deriver_types.index("ArchitectureDriftDeriver") < cross_idx
        assert deriver_types.index("BenchmarkRegressionDeriver") < cross_idx
        assert deriver_types.index("SecurityVulnDeriver") < cross_idx
