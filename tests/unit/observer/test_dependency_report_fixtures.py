# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for dependency report generator fixtures.

Validates that synthetic data generators produce correct report structures
for testing dependency report processing.
"""
from __future__ import annotations

import json

import pytest

from tests.fixtures.dependency_reports import (
    DependencyReportData,
    DependencyReportGenerator,
    DependencyStatus,
)
from tests.fixtures.timing import MemoryTracker, Timing


class TestDependencyReportGenerator:
    """Unit tests for DependencyReportGenerator factory methods."""

    def test_baseline_produces_7_dependencies(self) -> None:
        """Verify baseline generator creates exactly 7 healthy dependencies."""
        data = DependencyReportGenerator.baseline()

        assert isinstance(data, DependencyReportData)
        assert len(data.statuses) == 7
        assert len(data.created_task_ids) == 0

        for status in data.statuses:
            assert isinstance(status, DependencyStatus)
            assert status.healthy is True
            assert status.notes is None

    def test_baseline_deterministic(self) -> None:
        """Verify baseline generator is deterministic (same output each call)."""
        data1 = DependencyReportGenerator.baseline()
        data2 = DependencyReportGenerator.baseline()

        assert len(data1.statuses) == len(data2.statuses)
        assert data1.created_task_ids == data2.created_task_ids

        for s1, s2 in zip(data1.statuses, data2.statuses):
            assert s1.package == s2.package
            assert s1.installed_version == s2.installed_version
            assert s1.upstream_latest == s2.upstream_latest

    def test_large_simple_default_20_deps(self) -> None:
        """Verify large-simple generator creates 20 dependencies by default."""
        data = DependencyReportGenerator.large_simple()

        assert len(data.statuses) == 20
        # Default actionable_pct=0.1 → ~2 actionable
        actionable = [s for s in data.statuses if not s.healthy]
        assert len(actionable) == 2
        assert len(data.created_task_ids) == 2

    def test_large_simple_scales_with_parameters(self) -> None:
        """Verify large-simple scales correctly with custom parameters."""
        test_cases = [
            (10, 0.1, 1),   # 10 deps, 10% actionable → 1
            (20, 0.2, 4),   # 20 deps, 20% actionable → 4
            (50, 0.5, 25),  # 50 deps, 50% actionable → 25
        ]

        for dep_count, actionable_pct, expected_actionable in test_cases:
            data = DependencyReportGenerator.large_simple(
                dep_count=dep_count, actionable_pct=actionable_pct
            )
            assert len(data.statuses) == dep_count
            actionable = [s for s in data.statuses if not s.healthy]
            assert len(actionable) == expected_actionable

    def test_large_actionable_high_density(self) -> None:
        """Verify large-actionable generates 80% actionable items by default."""
        data = DependencyReportGenerator.large_actionable()

        assert len(data.statuses) == 10
        actionable = [s for s in data.statuses if not s.healthy]
        assert len(actionable) == 8

        # All actionable items should have notes
        for status in actionable:
            assert status.notes is not None
            assert len(status.notes) > 0

    def test_large_actionable_notes_quality(self) -> None:
        """Verify large-actionable notes contain useful security information."""
        data = DependencyReportGenerator.large_actionable()

        actionable = [s for s in data.statuses if s.notes]
        assert len(actionable) > 0

        for status in actionable:
            assert "CVE" in status.notes or "update" in status.notes.lower()

    def test_large_payload_verbose_notes(self) -> None:
        """Verify large-payload generates verbose notes for actionable items."""
        data = DependencyReportGenerator.large_payload(note_length=1000)

        assert len(data.statuses) == 8
        actionable = [s for s in data.statuses if s.notes]
        assert len(actionable) == 4

        # All notes should be substantial
        for status in actionable:
            assert len(status.notes) > 500

    def test_large_payload_size_grows_with_note_length(self) -> None:
        """Verify payload size increases with note_length parameter."""
        sizes = []
        for note_length in [100, 500, 1000]:
            data = DependencyReportGenerator.large_payload(note_length=note_length)
            payload_json = json.dumps(data.to_dict())
            sizes.append(len(payload_json.encode("utf-8")))

        # Sizes should increase
        assert sizes[1] > sizes[0]
        assert sizes[2] > sizes[1]

    def test_extra_large_50_dependencies(self) -> None:
        """Verify extra-large generator creates 50 dependencies."""
        data = DependencyReportGenerator.extra_large()

        assert len(data.statuses) == 50
        actionable = [s for s in data.statuses if not s.healthy]
        # Default 50% actionable
        assert len(actionable) == 25

    def test_extra_large_scalable(self) -> None:
        """Verify extra-large can scale to different dependency counts."""
        for dep_count in [30, 50, 100]:
            data = DependencyReportGenerator.extra_large(dep_count=dep_count)
            assert len(data.statuses) == dep_count

    def test_custom_respects_all_parameters(self) -> None:
        """Verify custom generator respects all parameters."""
        data = DependencyReportGenerator.custom(
            dep_count=15,
            actionable_pct=0.3,
            note_length=500,
        )

        assert len(data.statuses) == 15
        actionable = [s for s in data.statuses if not s.healthy]
        assert len(actionable) == 4  # int(15 * 0.3)

        # Check note lengths
        for status in actionable:
            if status.notes:
                assert len(status.notes) > 100

    def test_all_statuses_have_required_fields(self) -> None:
        """Verify all generators produce valid DependencyStatus objects."""
        generators = [
            DependencyReportGenerator.baseline(),
            DependencyReportGenerator.large_simple(),
            DependencyReportGenerator.large_actionable(),
            DependencyReportGenerator.large_payload(),
            DependencyReportGenerator.extra_large(),
            DependencyReportGenerator.custom(),
        ]

        for data in generators:
            assert isinstance(data, DependencyReportData)
            for status in data.statuses:
                assert hasattr(status, "package")
                assert hasattr(status, "installed_version")
                assert hasattr(status, "upstream_latest")
                assert hasattr(status, "healthy")
                assert hasattr(status, "notes")
                assert hasattr(status, "severity")

                assert isinstance(status.package, str)
                assert isinstance(status.installed_version, str)
                assert isinstance(status.upstream_latest, str)
                assert isinstance(status.healthy, bool)
                assert status.notes is None or isinstance(status.notes, str)
                assert isinstance(status.severity, str)

    def test_serialization_round_trip(self) -> None:
        """Verify all generators produce JSON-serializable data."""
        generators = [
            DependencyReportGenerator.baseline(),
            DependencyReportGenerator.large_simple(),
            DependencyReportGenerator.large_actionable(),
            DependencyReportGenerator.large_payload(),
            DependencyReportGenerator.extra_large(),
        ]

        for data in generators:
            # Serialize to JSON
            payload_dict = data.to_dict()
            payload_json = json.dumps(payload_dict)

            # Deserialize back
            deserialized = json.loads(payload_json)

            # Verify structure is preserved
            assert "statuses" in deserialized
            assert "created_task_ids" in deserialized
            assert len(deserialized["statuses"]) == len(data.statuses)
            assert deserialized["created_task_ids"] == data.created_task_ids

    def test_actionable_consistency(self) -> None:
        """Verify created_task_ids count matches actionable items."""
        test_cases = [
            DependencyReportGenerator.baseline(),
            DependencyReportGenerator.large_simple(),
            DependencyReportGenerator.large_actionable(),
            DependencyReportGenerator.large_payload(),
            DependencyReportGenerator.extra_large(),
        ]

        for data in test_cases:
            actionable_count = len([s for s in data.statuses if not s.healthy])
            # created_task_ids length should match actionable count
            assert len(data.created_task_ids) == actionable_count


class TestTimingUtilities:
    """Unit tests for timing measurement utilities."""

    def test_timing_context_manager(self) -> None:
        """Verify Timing context manager captures elapsed time."""
        import time

        with Timing() as timer:
            time.sleep(0.01)  # Sleep 10ms

        elapsed = timer.elapsed()
        # Should be at least 10ms (allow some variance)
        assert elapsed >= 0.009
        assert elapsed < 1.0

    def test_timing_precision(self) -> None:
        """Verify Timing provides high precision timing."""
        with Timing() as timer:
            # Immediate execution
            pass

        elapsed = timer.elapsed()
        # Should be very small but not zero
        assert 0 <= elapsed < 0.001

    def test_timing_raises_on_improper_use(self) -> None:
        """Verify Timing raises if elapsed() called before context exit."""
        timer = Timing()
        with pytest.raises(RuntimeError):
            timer.elapsed()

    def test_memory_tracker_context_manager(self) -> None:
        """Verify MemoryTracker context manager captures peak memory."""
        with MemoryTracker() as tracker:
            # Allocate some memory
            large_list = [i for i in range(1000000)]

        peak_mb = tracker.peak_memory_mb
        # Should capture some memory usage
        assert peak_mb > 0
        assert peak_mb < 1000  # Should be less than 1GB

    def test_memory_tracker_peak_memory(self) -> None:
        """Verify MemoryTracker accurately tracks peak memory."""
        with MemoryTracker() as tracker:
            # Create lists of different sizes
            small = [1] * 1000
            medium = [2] * 100000
            large = [3] * 1000000

        peak_mb = tracker.peak_memory_mb
        # Peak should be captured (larger list allocation)
        assert peak_mb > 0

    def test_memory_tracker_raises_on_improper_use(self) -> None:
        """Verify MemoryTracker raises if accessed before context exit."""
        tracker = MemoryTracker()
        with pytest.raises(RuntimeError):
            _ = tracker.peak_memory_mb
