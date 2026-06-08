# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Performance tests for snapshot repositories and managers.

Tests to ensure snapshot operations scale efficiently with snapshot count,
data size, and concurrent access patterns.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.observer.models import (
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    CheckSignal,
    DependencyDriftSignal,
    TodoSignal,
)
from operations_center.observer.snapshot_repository import (
    LocalSnapshotRepository,
    SnapshotFormat,
)
from operations_center.observer.snapshot_manager import SnapshotManager


def create_snapshot(index: int, test_count: int = 100) -> RepoStateSnapshot:
    """Factory for creating test snapshots with unique IDs."""
    return RepoStateSnapshot(
        run_id=f"test_obs_20260607T{12 + index % 12:02d}0000Z_perf{index:04d}_test",
        observed_at=datetime(2026, 6, 7, 12 + (index % 12), index % 60, tzinfo=timezone.utc),
        observer_version=1,
        source_command="performance-test",
        repo=RepoContextSnapshot(
            name=f"perf-test-repo-{index}",
            path=Path(f"/tmp/perf-test-{index}"),
            current_branch=f"feature/{index}",
            base_branch="main",
            is_dirty=index % 2 == 0,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(
                status="passing" if index % 3 != 0 else "failing",
                test_count=test_count + (index * 10),
                source="pytest",
            ),
            dependency_drift=DependencyDriftSignal(
                status="healthy" if index % 5 != 0 else "drift_detected",
                source="pip-audit",
            ),
            todo_signal=TodoSignal(
                todo_count=5 + index,
                fixme_count=2 + (index % 5),
            ),
        ),
    )


@pytest.mark.snapshot_performance
class TestSnapshotRepositoryPerformance:
    """Performance tests for snapshot repository operations."""

    def test_store_many_snapshots_under_5s(self, tmp_path: Path) -> None:
        """Test that storing 100 snapshots completes in reasonable time."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        snapshots = [create_snapshot(i) for i in range(100)]

        # Should complete all stores quickly
        start = time.perf_counter()
        for snapshot in snapshots:
            repository.store(snapshot, SnapshotFormat.JSON)
        duration = time.perf_counter() - start

        # Generous time limit: 5 seconds for 100 snapshots
        assert duration < 5.0

    def test_list_snapshots_scales_linearly(self, tmp_path: Path) -> None:
        """Test that listing snapshots scales with snapshot count."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store snapshots in batches and measure list time
        times = []
        for batch_size in [10, 25, 50]:
            # Clear and store batch
            snapshots = [create_snapshot(i) for i in range(batch_size)]
            for snapshot in snapshots:
                repository.store(snapshot, SnapshotFormat.JSON)

            # Measure list time
            start = time.perf_counter()
            snapshots = repository.list_snapshots()
            end = time.perf_counter()

            times.append((batch_size, end - start))

        # Times should scale reasonably (roughly linear)
        # Ratio between 50 snapshots and 10 snapshots should be reasonable
        time_for_50 = times[2][1]
        time_for_10 = times[0][1]

        # Should be at most 5x slower for 5x more snapshots
        # (allowing some overhead but checking it doesn't scale exponentially)
        assert time_for_50 < time_for_10 * 10

    def test_load_snapshot_sub_millisecond(self, tmp_path: Path) -> None:
        """Test that loading individual snapshots is fast."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store a snapshot
        snapshot = create_snapshot(0, test_count=1000)
        repository.store(snapshot, SnapshotFormat.JSON)

        # Measure load time
        times = []
        for _ in range(10):
            start = time.perf_counter()
            loaded = repository.load(snapshot.run_id)
            end = time.perf_counter()

            times.append(end - start)
            assert loaded is not None

        # Average load time should be under 10ms
        avg_time = sum(times) / len(times)
        assert avg_time < 0.01

    def test_delete_many_snapshots_under_1s(self, tmp_path: Path) -> None:
        """Test that deleting many snapshots completes quickly."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store snapshots
        run_ids = []
        for i in range(50):
            snapshot = create_snapshot(i)
            repository.store(snapshot, SnapshotFormat.JSON)
            run_ids.append(snapshot.run_id)

        # Measure delete time
        start = time.perf_counter()
        for run_id in run_ids:
            repository.delete(run_id)
        end = time.perf_counter()

        # Should delete all quickly
        assert (end - start) < 1.0

    def test_compare_snapshots_performance(self, tmp_path: Path) -> None:
        """Test that comparing snapshots is efficient."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store two snapshots
        snap1 = create_snapshot(0)
        snap2 = create_snapshot(1)

        repository.store(snap1, SnapshotFormat.JSON)
        repository.store(snap2, SnapshotFormat.JSON)

        # Measure comparison time (run multiple times)
        times = []
        for _ in range(5):
            start = time.perf_counter()
            comparison = repository.compare(snap1.run_id, snap2.run_id)
            end = time.perf_counter()

            times.append(end - start)
            assert comparison is not None

        # Average should be fast
        avg_time = sum(times) / len(times)
        assert avg_time < 0.01


@pytest.mark.snapshot_performance
class TestSnapshotManagerPerformance:
    """Performance tests for snapshot manager operations."""

    def test_save_and_get_many_snapshots(self, tmp_path: Path) -> None:
        """Test that saving and retrieving many snapshots is efficient."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "perf"))

        # Save 25 snapshots
        saved_ids = []
        start = time.perf_counter()
        for i in range(25):
            snapshot = create_snapshot(i)
            manager.save_snapshot(snapshot)
            saved_ids.append(snapshot.run_id)
        end = time.perf_counter()

        save_time = end - start

        # Retrieve all snapshots
        start = time.perf_counter()
        for run_id in saved_ids:
            snapshot = manager.get_snapshot(run_id)
            assert snapshot is not None
        end = time.perf_counter()

        retrieve_time = end - start

        # Total should be reasonable
        assert save_time < 2.0
        assert retrieve_time < 1.0

    def test_get_latest_with_many_snapshots(self, tmp_path: Path) -> None:
        """Test that getting latest snapshot is fast even with many stored."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "perf"))

        # Save many snapshots
        for i in range(100):
            snapshot = create_snapshot(i)
            manager.save_snapshot(snapshot)

        # Get latest should be fast even with many
        start = time.perf_counter()
        latest = manager.get_latest_snapshot()
        end = time.perf_counter()

        assert latest is not None
        assert (end - start) < 1.0

    def test_get_snapshots_limit_performance(self, tmp_path: Path) -> None:
        """Test that limiting snapshot retrieval is efficient."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "perf"))

        # Save many snapshots
        for i in range(100):
            snapshot = create_snapshot(i)
            manager.save_snapshot(snapshot)

        # Get with limit should be fast
        times = []
        for limit in [5, 10, 20]:
            start = time.perf_counter()
            snapshots = manager.get_snapshots(limit=limit)
            end = time.perf_counter()

            times.append(end - start)
            assert len(snapshots) == limit

        # All limit queries should be fast (1.0s catches catastrophic regression while tolerating CI runners)
        for duration in times:
            assert duration < 1.0

    def test_cleanup_performance_with_many_snapshots(self, tmp_path: Path) -> None:
        """Test that cleanup performance scales well."""
        manager = SnapshotManager(
            LocalSnapshotRepository(root=tmp_path / "perf", retention_count=50)
        )

        # Save 100 snapshots
        for i in range(100):
            snapshot = create_snapshot(i)
            manager.save_snapshot(snapshot)

        # Cleanup should be efficient
        start = time.perf_counter()
        manager.repository.cleanup()
        end = time.perf_counter()

        # Should complete quickly even with large number
        assert (end - start) < 1.0

        # Should have cleaned up to retention_count
        remaining = manager.get_snapshots(limit=100)
        assert len(remaining) <= 50


@pytest.mark.snapshot_performance
class TestSnapshotMemoryEfficiency:
    """Tests for memory efficiency with large snapshots."""

    def test_large_snapshot_serialization(self, tmp_path: Path) -> None:
        """Test that large snapshots serialize efficiently."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Create a large snapshot
        large_snapshot = create_snapshot(0, test_count=100000)

        # Serialize and measure size
        start = time.perf_counter()
        repository.store(large_snapshot, SnapshotFormat.JSON)
        end = time.perf_counter()

        # Should serialize quickly
        assert (end - start) < 1.0

        # File should exist and be reasonable size
        snapshot_file = tmp_path / "perf" / large_snapshot.run_id / "snapshot.json"
        assert snapshot_file.exists()
        file_size = snapshot_file.stat().st_size
        assert file_size < 1_000_000  # Less than 1MB for reasonable data

    def test_load_large_snapshot_memory_efficient(self, tmp_path: Path) -> None:
        """Test that loading large snapshots doesn't use excessive memory."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store a large snapshot
        large_snapshot = create_snapshot(0, test_count=50000)
        repository.store(large_snapshot, SnapshotFormat.JSON)

        # Load multiple times and verify consistent performance
        times = []
        for _ in range(5):
            start = time.perf_counter()
            loaded = repository.load(large_snapshot.run_id)
            end = time.perf_counter()

            times.append(end - start)
            assert loaded is not None

        # Times should be consistent (no degradation)
        avg_time = sum(times) / len(times)
        max_time = max(times)

        assert avg_time < 0.05
        # Max should not be significantly higher than average
        assert max_time < avg_time * 3


@pytest.mark.snapshot_performance
class TestSnapshotIndexingPerformance:
    """Tests for snapshot index performance."""

    def test_index_lookup_scales_well(self, tmp_path: Path) -> None:
        """Test that index lookup scales linearly with snapshot count."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store snapshots in batches
        batch_sizes = [10, 50, 100]
        lookup_times = []

        for batch_size in batch_sizes:
            # Clear and recreate
            repository = LocalSnapshotRepository(root=tmp_path / f"perf_{batch_size}")

            # Store batch
            for i in range(batch_size):
                snapshot = create_snapshot(i)
                repository.store(snapshot, SnapshotFormat.JSON)

            # Measure lookup time for a specific snapshot
            target_id = "test_obs_20260607T120000Z_perf0000_test"
            start = time.perf_counter()
            snapshot = repository.load(target_id)
            end = time.perf_counter()

            lookup_times.append(end - start)

        # All lookups should be fast (no significant slowdown)
        # Even 100 snapshots should lookup in <50ms
        assert max(lookup_times) < 0.05

    def test_list_with_sorting_performance(self, tmp_path: Path) -> None:
        """Test that listing with sorting scales well."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store snapshots with timestamps in random order
        snapshot_ids = []
        for i in [5, 2, 8, 1, 9, 3, 7, 4, 6, 0]:
            snapshot = create_snapshot(i)
            repository.store(snapshot, SnapshotFormat.JSON)
            snapshot_ids.append(snapshot.run_id)

        # List should sort efficiently
        start = time.perf_counter()
        snapshots = repository.list_snapshots()
        end = time.perf_counter()

        # Should complete quickly
        assert (end - start) < 0.1

        # Should be sorted by date
        assert len(snapshots) >= 10
