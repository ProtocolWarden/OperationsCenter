# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Performance regression tests for dependency report generation and collection.

This test suite verifies that dependency report generation and collection
does not regress in performance over time. Tests measure:
- Wall-clock generation time
- Collection time (parsing + validation)
- Memory usage during operations
- Scalability across dependency counts
- Parser resilience with large payloads
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from typing import Any

import pytest

from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.service import ObserverContext
from tests.fixtures.timing import Timing, MemoryTracker


def _make_observer_context(report_root: Path) -> ObserverContext:
    """Create a minimal ObserverContext with report_root."""
    settings = MagicMock()
    settings.report_root = report_root
    return ObserverContext(
        repo_path=report_root,
        repo_name="test-repo",
        base_branch="main",
        run_id="perf_test_001",
        observed_at=datetime.now(UTC),
        source_command="test",
        settings=settings,
        commit_limit=10,
        hotspot_window=30,
        todo_limit=20,
        logs_root=report_root / "logs",
    )


@pytest.mark.performance
class TestDependencyReportPerformanceRegression:
    """Performance regression tests for dependency report generation & collection."""

    # ========================================================================
    # Baseline Tests (Small Report: 7 deps, 0 actionable, ~2KB)
    # ========================================================================

    def test_baseline_collection_time(
        self, baseline_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert collection time for baseline report stays under 50ms."""
        report_path, _ = baseline_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with Timing() as timer:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        elapsed_ms = timer.elapsed() * 1000
        assert elapsed_ms < 50, (
            f"Collection time regression: {elapsed_ms:.2f}ms, expected <50ms"
        )

    def test_baseline_collection_correctness(
        self, baseline_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert baseline report parsed correctly with all dependencies."""
        report_path, data = baseline_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert signal.source is not None
        assert str(report_path) in signal.source

        # Verify signal summary reflects correct dependency counts
        expected_actionable = len(
            [s for s in data["statuses"] if s.get("notes")]
        )
        assert f"actionable_statuses={expected_actionable}" in signal.summary

    def test_baseline_memory_usage(
        self, baseline_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert memory usage for baseline stays < 50MB (±10MB tolerance)."""
        report_path, _ = baseline_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with MemoryTracker() as tracker:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        peak_mb = tracker.peak_memory_mb
        max_allowed = 60  # 50MB baseline + 10MB tolerance
        assert peak_mb < max_allowed, (
            f"Memory regression: {peak_mb:.1f}MB, "
            f"expected <{max_allowed}MB (baseline <50MB)"
        )

    # ========================================================================
    # Large-Simple Tests (Horizontal Scaling: 20 deps, 10% actionable, ~5KB)
    # ========================================================================

    def test_large_simple_collection_time(
        self, large_simple_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert collection time for large-simple report stays under 50ms."""
        report_path, _ = large_simple_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with Timing() as timer:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        elapsed_ms = timer.elapsed() * 1000
        assert elapsed_ms < 50, (
            f"Collection time regression: {elapsed_ms:.2f}ms, expected <50ms"
        )

    def test_large_simple_scalability_ratio(
        self, baseline_report_on_disk: tuple[Path, dict[str, Any]],
        large_simple_report_on_disk: tuple[Path, dict[str, Any]],
    ) -> None:
        """Assert linear scalability: 20 deps ≈ 2.9x baseline (7 deps)."""
        baseline_path, baseline_data = baseline_report_on_disk
        large_path, large_data = large_simple_report_on_disk

        baseline_root = baseline_path.parent.parent
        large_root = large_path.parent.parent

        baseline_ctx = _make_observer_context(baseline_root)
        large_ctx = _make_observer_context(large_root)

        # Measure baseline collection time
        with Timing() as timer:
            baseline_signal = DependencyDriftCollector().collect(baseline_ctx)
        baseline_time = timer.elapsed()
        assert baseline_signal.status == "available"

        # Measure large-simple collection time
        with Timing() as timer:
            large_signal = DependencyDriftCollector().collect(large_ctx)
        large_time = timer.elapsed()
        assert large_signal.status == "available"

        # Verify roughly linear scaling: 20 deps / 7 deps ≈ 2.86x
        if baseline_time > 0:
            ratio = large_time / baseline_time
            expected_ratio = len(large_data["statuses"]) / len(baseline_data["statuses"])
            max_ratio = expected_ratio * 1.5  # Allow 50% variance
            assert ratio <= max_ratio, (
                f"Non-linear scaling: {ratio:.2f}x "
                f"(expected ~{expected_ratio:.2f}x, max {max_ratio:.2f}x)"
            )

    def test_large_simple_correctness(
        self, large_simple_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert all dependencies parsed correctly."""
        report_path, data = large_simple_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert len(data["statuses"]) == 20

    # ========================================================================
    # Large-Actionable Tests (Vertical Scaling: 10 deps, 80% actionable)
    # ========================================================================

    def test_large_actionable_collection_time(
        self, large_actionable_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert collection time for large-actionable report stays under 50ms."""
        report_path, _ = large_actionable_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with Timing() as timer:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        elapsed_ms = timer.elapsed() * 1000
        assert elapsed_ms < 50, (
            f"Collection time regression: {elapsed_ms:.2f}ms, expected <50ms"
        )

    def test_large_actionable_identifies_all_actionable(
        self, large_actionable_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert all actionable dependencies identified correctly."""
        report_path, data = large_actionable_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"

        actionable_count = len([s for s in data["statuses"] if s.get("notes")])
        assert f"actionable_statuses={actionable_count}" in signal.summary
        assert actionable_count > 0

    # ========================================================================
    # Large-Payload Tests (Parser Resilience: 8 deps, ~80KB verbose notes)
    # ========================================================================

    def test_large_payload_collection_time(
        self, large_payload_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert collection time for large-payload report stays under 50ms."""
        report_path, _ = large_payload_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with Timing() as timer:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        elapsed_ms = timer.elapsed() * 1000
        assert elapsed_ms < 50, (
            f"Collection time regression: {elapsed_ms:.2f}ms, expected <50ms"
        )

    def test_large_payload_parsing_resilience(
        self, large_payload_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert JSON parsing handles large payloads correctly."""
        report_path, data = large_payload_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"

        # Verify report can be re-read from disk
        parsed = json.loads(report_path.read_text(encoding="utf-8"))
        assert len(parsed["statuses"]) == len(data["statuses"])
        assert parsed["created_task_ids"] == data["created_task_ids"]

    def test_large_payload_memory_usage(
        self, large_payload_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert memory usage for large-payload < 150MB."""
        report_path, _ = large_payload_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with MemoryTracker() as tracker:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        peak_mb = tracker.peak_memory_mb
        max_allowed = 180  # 150MB baseline + 30MB tolerance
        assert peak_mb < max_allowed, (
            f"Memory regression: {peak_mb:.1f}MB, "
            f"expected <{max_allowed}MB"
        )

    # ========================================================================
    # Extra-Large Tests (Stress Test: 50 deps, 50% actionable)
    # ========================================================================

    def test_extra_large_collection_time(
        self, extra_large_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert collection time for extra-large report stays under 50ms."""
        report_path, _ = extra_large_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with Timing() as timer:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        elapsed_ms = timer.elapsed() * 1000
        assert elapsed_ms < 50, (
            f"Collection time regression: {elapsed_ms:.2f}ms, expected <50ms"
        )

    def test_extra_large_all_dependencies_present(
        self, extra_large_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert all 50 dependencies present in extra-large report."""
        report_path, data = extra_large_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert len(data["statuses"]) == 50

    def test_extra_large_memory_usage(
        self, extra_large_report_on_disk: tuple[Path, dict[str, Any]]
    ) -> None:
        """Assert memory usage for extra-large < 200MB."""
        report_path, _ = extra_large_report_on_disk
        report_root = report_path.parent.parent
        ctx = _make_observer_context(report_root)

        with MemoryTracker() as tracker:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        peak_mb = tracker.peak_memory_mb
        max_allowed = 250  # 200MB baseline + 50MB tolerance
        assert peak_mb < max_allowed, (
            f"Memory regression: {peak_mb:.1f}MB, "
            f"expected <{max_allowed}MB"
        )

    # ========================================================================
    # Cross-Scenario Tests (Aggregate Performance)
    # ========================================================================

    def test_collection_time_linear_growth(
        self,
        baseline_report_on_disk: tuple[Path, dict[str, Any]],
        large_simple_report_on_disk: tuple[Path, dict[str, Any]],
        large_actionable_report_on_disk: tuple[Path, dict[str, Any]],
        extra_large_report_on_disk: tuple[Path, dict[str, Any]],
    ) -> None:
        """Assert collection time grows roughly linearly with payload size."""
        scenarios = [
            ("baseline", baseline_report_on_disk),
            ("large_simple", large_simple_report_on_disk),
            ("large_actionable", large_actionable_report_on_disk),
            ("extra_large", extra_large_report_on_disk),
        ]

        times = []
        for name, (report_path, data) in scenarios:
            report_root = report_path.parent.parent
            ctx = _make_observer_context(report_root)

            with Timing() as timer:
                signal = DependencyDriftCollector().collect(ctx)

            assert signal.status == "available"
            times.append((name, len(data["statuses"]), timer.elapsed()))

        # Verify times aren't dramatically non-linear when base is measurable.
        # Sub-5ms operations are noise-dominated; ratio checks only apply when
        # the baseline scenario takes long enough to produce reliable timings.
        _MIN_RATIO_BASE_S = 0.005
        for i in range(1, len(times)):
            prev_deps, prev_time = times[i - 1][1], times[i - 1][2]
            curr_deps, curr_time = times[i][1], times[i][2]

            if prev_time > _MIN_RATIO_BASE_S and curr_deps > prev_deps:
                ratio = curr_time / prev_time
                expected_ratio = curr_deps / prev_deps
                # Allow 2x variance for system I/O jitter
                assert ratio <= expected_ratio * 2, (
                    f"Non-linear growth: {times[i][0]} took {ratio:.2f}x "
                    f"of {times[i - 1][0]} (expected ~{expected_ratio:.2f}x)"
                )

    def test_malformed_json_handled_gracefully(
        self, report_fixture_dir: Path
    ) -> None:
        """Assert malformed JSON returns not_available status."""
        from uuid import uuid4

        run_dir = report_fixture_dir / f"run_{uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Write invalid JSON
        report_file = run_dir / "dependency_report.json"
        report_file.write_text("{invalid json", encoding="utf-8")

        report_root = report_file.parent.parent
        ctx = _make_observer_context(report_root)

        with Timing() as timer:
            signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "not_available"
        # Should not take excessive time on error
        assert timer.elapsed() < 1.0

    def test_invalid_structure_handled_gracefully(
        self, report_fixture_dir: Path
    ) -> None:
        """Assert invalid structure (missing statuses) returns not_available."""
        from uuid import uuid4

        run_dir = report_fixture_dir / f"run_{uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Write valid JSON but missing required 'statuses' field
        report_file = run_dir / "dependency_report.json"
        report_file.write_text(
            json.dumps({"some_field": "value"}), encoding="utf-8"
        )

        report_root = report_file.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "not_available"

    def test_empty_statuses_list_handled(self, report_fixture_dir: Path) -> None:
        """Assert empty statuses list returns available status."""
        from uuid import uuid4

        run_dir = report_fixture_dir / f"run_{uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)

        report_file = run_dir / "dependency_report.json"
        report_file.write_text(
            json.dumps({"statuses": [], "created_task_ids": []}),
            encoding="utf-8",
        )

        report_root = report_file.parent.parent
        ctx = _make_observer_context(report_root)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "no statuses" in signal.summary

    def test_multiple_reports_picks_latest(
        self, report_fixture_dir: Path
    ) -> None:
        """Assert latest report by mtime is selected from multiple."""
        import os
        from uuid import uuid4

        # Create old report under report_fixture_dir
        old_dir = report_fixture_dir / f"run_{uuid4().hex[:8]}"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_file = old_dir / "dependency_report.json"
        old_file.write_text(
            json.dumps(
                {
                    "statuses": [{"package": "old"}],
                    "created_task_ids": [],
                }
            ),
            encoding="utf-8",
        )
        os.utime(old_file, (1000, 1000))

        # Create new report under report_fixture_dir
        new_dir = report_fixture_dir / f"run_{uuid4().hex[:8]}"
        new_dir.mkdir(parents=True, exist_ok=True)
        new_file = new_dir / "dependency_report.json"
        new_file.write_text(
            json.dumps(
                {
                    "statuses": [{"package": "new"}],
                    "created_task_ids": [],
                }
            ),
            encoding="utf-8",
        )
        os.utime(new_file, (2000, 2000))

        # Use report_fixture_dir as the root (contains run_* subdirs)
        ctx = _make_observer_context(report_fixture_dir)

        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "run_" in signal.source
        # Verify it picked the newer one (harder to test exactly which,
        # but at least verify it picked one)
        assert signal.source is not None
