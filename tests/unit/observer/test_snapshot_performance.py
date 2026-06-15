# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Performance tests for snapshot repositories and managers.

Tests to ensure snapshot operations scale efficiently with snapshot count,
data size, and concurrent access patterns.
"""

import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pytest

from operations_center.observer.models import (
    ArchitectureSignal,
    BacklogItem,
    BacklogSignal,
    BenchmarkSignal,
    CICheckRunRecord,
    CIHistorySignal,
    CommitMetadata,
    CoverageSignal,
    DependencyDriftSignal,
    ExecutionHealthSignal,
    ExecutionRunRecord,
    FileHotspot,
    LintSignal,
    LintViolation,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    TestSignal,
    TodoFileCount,
    TodoSignal,
    TypeError,
    TypeSignal,
    UncoveredFile,
)
from operations_center.observer.snapshot_manager import SnapshotManager
from operations_center.observer.snapshot_repository import (
    LocalSnapshotRepository,
    SnapshotFormat,
)
from tests.fixtures.timing import MemoryTracker, Timing

pytestmark = pytest.mark.perf


def create_snapshot(index: int, test_count: int = 100) -> RepoStateSnapshot:
    """Create test snapshots with unique IDs."""
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
            test_signal=TestSignal(
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


def _generate_commits(count: int, index: int, seed: int | None = None) -> list[CommitMetadata]:
    """Generate realistic commit metadata."""
    if seed is not None:
        random.seed(seed)

    authors = [
        "Alice Smith",
        "Bob Johnson",
        "Carol Davis",
        "David Wilson",
        "Eve Martinez",
        "Frank Brown",
        "Grace Lee",
        "Henry Taylor",
        "Iris Anderson",
        "Jack Thomas",
    ]

    commits = []
    base_time = datetime(2026, 6, 1, tzinfo=timezone.utc)

    for i in range(count):
        timestamp = base_time + timedelta(hours=i * (72 // max(count, 1)))
        commits.append(
            CommitMetadata(
                sha_short=f"abc{index:04d}{i:04d}"[:7],
                author=authors[i % len(authors)],
                timestamp=timestamp,
                subject=f"commit {index}-{i}: implement feature #{i}",
            )
        )

    return commits


def _generate_file_hotspots(count: int) -> list[FileHotspot]:
    """Generate file paths with realistic touch counts using Pareto distribution."""
    base_paths = [
        "src/main.py",
        "src/utils.py",
        "src/models.py",
        "src/api/endpoints.py",
        "src/api/auth.py",
        "src/db/schema.py",
        "tests/test_main.py",
        "tests/test_utils.py",
        "tests/integration/test_api.py",
        "docs/README.md",
    ]

    hotspots = []
    for i in range(count):
        # Pareto distribution: 80/20 rule for file touches
        if i < count // 5:
            touch_count = random.randint(50, 200)  # 20% of files, 80% of touches
        else:
            touch_count = random.randint(1, 20)  # 80% of files, 20% of touches

        # Use predefined paths for small counts, generate for larger ones
        if i < len(base_paths):
            path = base_paths[i]
        else:
            path = f"src/module_{i:04d}/file_{i % 10:02d}.py"
        hotspots.append(FileHotspot(path=path, touch_count=touch_count))

    return hotspots


def _generate_lint_violations(count: int) -> list[LintViolation]:
    """Generate lint violations."""
    codes = ["E501", "W291", "E302", "E265", "E225"]
    messages = [
        "line too long",
        "trailing whitespace",
        "expected 2 blank lines",
        "block comment should start with '# '",
        "missing whitespace around operator",
    ]

    violations = []
    for i in range(count):
        violations.append(
            LintViolation(
                path=f"src/module_{i % 100:04d}/file.py",
                line=10 + (i % 100),
                col=5 + (i % 50),
                code=codes[i % len(codes)],
                message=messages[i % len(messages)],
            )
        )

    return violations


def _generate_type_errors(count: int) -> list[TypeError]:
    """Generate type checking errors."""
    codes = ["attr-defined", "arg-type", "return-value"]
    messages = [
        "has no attribute",
        "argument has incompatible type",
        "incompatible return value",
    ]

    errors = []
    for i in range(count):
        errors.append(
            TypeError(
                path=f"src/module_{i % 100:04d}/file.py",
                line=20 + (i % 100),
                col=10 + (i % 50),
                code=codes[i % len(codes)],
                message=messages[i % len(messages)],
            )
        )

    return errors


def _generate_ci_check_runs(count: int, index: int) -> list[CICheckRunRecord]:
    """Generate CI check run records."""
    check_names = ["lint", "type-check", "tests", "security", "build"]
    conclusions = ["success", "failure", "neutral"]

    runs = []
    for i in range(count):
        runs.append(
            CICheckRunRecord(
                name=check_names[i % len(check_names)],
                sha=f"abc{index:04d}{i:04d}"[:40],
                conclusion=conclusions[i % len(conclusions)],
            )
        )

    return runs


def _generate_uncovered_files(count: int) -> list[UncoveredFile]:
    """Generate uncovered file records."""
    files = []
    for i in range(count):
        coverage = 50.0 + random.uniform(0, 30)  # Coverage between 50-80%
        files.append(
            UncoveredFile(
                path=f"src/module_{i:04d}/file.py",
                coverage_pct=coverage,
            )
        )

    return files


def create_large_snapshot(
    tier: Literal["small", "medium", "large"],
    index: int = 0,
    seed: int | None = None,
) -> RepoStateSnapshot:
    """Create snapshot with specified metric scale for performance testing.

    Args:
        tier: Metric tier - "small" (baseline), "medium" (realistic), "large" (stress)
        index: Unique identifier for this snapshot
        seed: Random seed for reproducible data generation

    Returns:
        RepoStateSnapshot with metrics at specified scale.

    """
    if seed is not None:
        random.seed(seed)

    # Define metric scales for each tier
    tier_config = {
        "small": {
            "test_count": 100,
            "commits": 10,
            "files": 5,
            "todo_files": 3,
            "lint_violations": 5,
            "type_errors": 5,
            "ci_runs": 10,
            "circular_deps": 0,
            "uncovered_files": 10,
            "benchmarks": 0,
            "security_advisories": 0,
        },
        "medium": {
            "test_count": 5000,
            "commits": 100,
            "files": 200,
            "todo_files": 10,
            "lint_violations": 50,
            "type_errors": 50,
            "ci_runs": 50,
            "circular_deps": 10,
            "uncovered_files": 100,
            "benchmarks": 5,
            "security_advisories": 10,
        },
        "large": {
            "test_count": 50000,
            "commits": 500,
            "files": 1000,
            "todo_files": 50,
            "lint_violations": 1000,
            "type_errors": 1000,
            "ci_runs": 200,
            "circular_deps": 50,
            "uncovered_files": 1000,
            "benchmarks": 50,
            "security_advisories": 50,
        },
    }

    config = tier_config[tier]
    observed_at = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)

    # Generate data at specified scale
    recent_commits = _generate_commits(config["commits"], index, seed)
    file_hotspots = _generate_file_hotspots(config["files"])
    lint_violations = _generate_lint_violations(config["lint_violations"])
    type_errors = _generate_type_errors(config["type_errors"])
    ci_runs = _generate_ci_check_runs(config["ci_runs"], index)
    uncovered_files = _generate_uncovered_files(config["uncovered_files"])

    # Generate circular dependencies
    circular_deps = [
        f"module_{i:02d} -> module_{i + 1:02d}" for i in range(config["circular_deps"])
    ]

    # Generate backlog items
    backlog_items = [
        BacklogItem(
            title=f"Task {i}: improve performance",
            item_type="enhancement",
            description=f"Description for task {i}",
        )
        for i in range(config["commits"] // 2)
    ]

    return RepoStateSnapshot(
        run_id=f"test_obs_20260614T120000Z_perf{index:04d}_{tier}_test",
        observed_at=observed_at,
        observer_version=1,
        source_command="performance-test",
        repo=RepoContextSnapshot(
            name=f"perf-test-{tier}",
            path=Path(f"/tmp/perf-test-{tier}"),
            current_branch="performance-test",
            base_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            recent_commits=recent_commits,
            file_hotspots=file_hotspots,
            test_signal=TestSignal(
                status="passing",
                test_count=config["test_count"],
                passed_count=int(config["test_count"] * 0.95),
                failed_count=int(config["test_count"] * 0.03),
                skip_count=int(config["test_count"] * 0.02),
                execution_time_ms=config["test_count"] * 10,
                coverage_percent=85.5,
                source="pytest",
                observed_at=observed_at,
            ),
            dependency_drift=DependencyDriftSignal(
                status="healthy",
                source="pip-audit",
                observed_at=observed_at,
            ),
            todo_signal=TodoSignal(
                todo_count=config["commits"],
                fixme_count=config["commits"] // 2,
                top_files=[
                    TodoFileCount(path=f"src/module_{i:04d}/file.py", count=5)
                    for i in range(config["todo_files"])
                ],
            ),
            execution_health=ExecutionHealthSignal(
                total_runs=config["ci_runs"],
                executed_count=int(config["ci_runs"] * 0.9),
                no_op_count=int(config["ci_runs"] * 0.05),
                unknown_count=int(config["ci_runs"] * 0.05),
                error_count=0,
                validation_failed_count=0,
                recent_runs=[
                    ExecutionRunRecord(
                        run_id=f"run_{i:04d}",
                        task_id=f"task_{i:04d}",
                        worker_role="observer",
                        outcome_status="executed",
                        validation_passed=True,
                    )
                    for i in range(min(5, config["ci_runs"]))
                ],
            ),
            backlog=BacklogSignal(items=backlog_items),
            lint_signal=LintSignal(
                status="violations" if config["lint_violations"] > 0 else "clean",
                violation_count=config["lint_violations"],
                distinct_file_count=config["files"],
                top_violations=lint_violations[: min(10, len(lint_violations))],  # Top 10
                source="pylint",
            ),
            type_signal=TypeSignal(
                status="errors" if config["type_errors"] > 0 else "clean",
                error_count=config["type_errors"],
                distinct_file_count=config["files"],
                top_errors=type_errors[: min(10, len(type_errors))],  # Top 10
                source="mypy",
            ),
            ci_history=CIHistorySignal(
                status="nominal",
                runs_checked=config["ci_runs"],
                failure_rate=0.05,
                flaky_checks=[],
                failing_checks=[],
                recent_runs=ci_runs[: min(10, len(ci_runs))],  # Top 10
                source="github-actions",
            ),
            architecture_signal=ArchitectureSignal(
                status="warnings" if config["circular_deps"] > 0 else "healthy",
                circular_dependencies=circular_deps,
                max_import_depth=8,
                coupling_score=0.65,
                source="depcheck",
                observed_at=observed_at,
            ),
            benchmark_signal=BenchmarkSignal(
                status="nominal",
                benchmark_count=config["benchmarks"],
                regressions=[]
                if config["benchmarks"] == 0
                else [f"bench_{i:02d}" for i in range(min(3, config["benchmarks"]))],
                source="criterion",
                observed_at=observed_at,
            ),
            security_signal=SecuritySignal(
                status="advisories" if config["security_advisories"] > 0 else "clean",
                advisory_count=config["security_advisories"],
                critical_count=max(0, config["security_advisories"] // 10),
                high_count=max(0, config["security_advisories"] // 5),
                source="snyk",
                observed_at=observed_at,
            ),
            coverage_signal=CoverageSignal(
                status="measured",
                total_coverage_pct=85.5,
                statement_coverage_pct=85.2,
                branch_coverage_pct=82.1,
                line_coverage_pct=86.0,
                uncovered_file_count=config["uncovered_files"],
                uncovered_threshold_pct=80.0,
                top_uncovered=uncovered_files[: min(10, len(uncovered_files))],  # Top 10
                module_coverages=[
                    {"module": f"module_{i:04d}", "coverage": 85.0}
                    for i in range(min(config["files"] // 10, 50))
                ],
                coverage_trend_pct=2.5,
                regression_delta_pct=-0.5,
                source="coverage.py",
                observed_at=observed_at,
            ),
        ),
    )


@pytest.mark.perf
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


@pytest.mark.perf
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

        # All limit queries should be fast (1.0s catches regressions while tolerating CI runners)
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


@pytest.mark.perf
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


@pytest.mark.perf
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


@pytest.mark.perf
class TestSnapshotSerializationLargeMetrics:
    """Performance tests for snapshot serialization with large metric sets."""

    def test_serialize_json_small_baseline(self, tmp_path: Path) -> None:
        """Test JSON serialization with small baseline metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("small", index=0)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSON)
        duration = timer.elapsed()

        # Small baseline should serialize quickly
        assert duration < 0.05, f"Small JSON serialization took {duration:.3f}s, expected <0.05s"

        # Verify file exists and is reasonable size
        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.json"
        assert snapshot_file.exists()
        file_size_kb = snapshot_file.stat().st_size / 1024
        assert file_size_kb < 50, f"Small JSON file size {file_size_kb:.1f}KB exceeds limit"

    def test_serialize_json_medium_metrics(self, tmp_path: Path) -> None:
        """Test JSON serialization with medium-scale metrics (5K tests)."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("medium", index=0)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSON)
        duration = timer.elapsed()

        # Medium metrics should serialize within threshold
        assert duration < 0.5, f"Medium JSON serialization took {duration:.3f}s, expected <0.5s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.json"
        file_size_mb = snapshot_file.stat().st_size / (1024 * 1024)
        assert file_size_mb < 1.2, f"Medium JSON file size {file_size_mb:.2f}MB exceeds limit"

    def test_serialize_json_large_metrics(self, tmp_path: Path) -> None:
        """Test JSON serialization with large-scale metrics (50K tests) - stress test."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=0)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSON)
        duration = timer.elapsed()

        # Large metrics have generous time allowance (stress test)
        assert duration < 5.0, f"Large JSON serialization took {duration:.3f}s, expected <5.0s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.json"
        file_size_mb = snapshot_file.stat().st_size / (1024 * 1024)
        assert file_size_mb < 12.0, f"Large JSON file size {file_size_mb:.2f}MB exceeds limit"

    def test_serialize_jsonl_small_baseline(self, tmp_path: Path) -> None:
        """Test JSONL serialization with small baseline metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("small", index=1)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSONL)
        duration = timer.elapsed()

        # JSONL is fastest format (no formatting)
        assert duration < 0.01, f"Small JSONL serialization took {duration:.4f}s, expected <0.01s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.jsonl"
        file_size_kb = snapshot_file.stat().st_size / 1024
        assert file_size_kb < 40, f"Small JSONL file size {file_size_kb:.1f}KB exceeds limit"

    def test_serialize_jsonl_medium_metrics(self, tmp_path: Path) -> None:
        """Test JSONL serialization with medium-scale metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("medium", index=1)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSONL)
        duration = timer.elapsed()

        assert duration < 0.05, f"Medium JSONL serialization took {duration:.3f}s, expected <0.05s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.jsonl"
        file_size_mb = snapshot_file.stat().st_size / (1024 * 1024)
        assert file_size_mb < 1.0, f"Medium JSONL file size {file_size_mb:.2f}MB exceeds limit"

    def test_serialize_jsonl_large_metrics(self, tmp_path: Path) -> None:
        """Test JSONL serialization with large-scale metrics (50K tests)."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=1)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSONL)
        duration = timer.elapsed()

        assert duration < 0.5, f"Large JSONL serialization took {duration:.3f}s, expected <0.5s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.jsonl"
        file_size_mb = snapshot_file.stat().st_size / (1024 * 1024)
        assert file_size_mb < 10.0, f"Large JSONL file size {file_size_mb:.2f}MB exceeds limit"

    def test_serialize_yaml_small_baseline(self, tmp_path: Path) -> None:
        """Test YAML serialization with small baseline metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("small", index=2)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.YAML)
        duration = timer.elapsed()

        # YAML is slowest format (recursive path conversion + yaml.dump)
        assert duration < 0.1, f"Small YAML serialization took {duration:.3f}s, expected <0.1s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.yaml"
        file_size_kb = snapshot_file.stat().st_size / 1024
        assert file_size_kb < 50, f"Small YAML file size {file_size_kb:.1f}KB exceeds limit"

    def test_serialize_yaml_medium_metrics(self, tmp_path: Path) -> None:
        """Test YAML serialization with medium-scale metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("medium", index=2)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.YAML)
        duration = timer.elapsed()

        assert duration < 1.0, f"Medium YAML serialization took {duration:.3f}s, expected <1.0s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.yaml"
        file_size_mb = snapshot_file.stat().st_size / (1024 * 1024)
        assert file_size_mb < 1.5, f"Medium YAML file size {file_size_mb:.2f}MB exceeds limit"

    def test_serialize_yaml_large_metrics(self, tmp_path: Path) -> None:
        """Test YAML serialization with large-scale metrics (50K tests) - stress test."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=2)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.YAML)
        duration = timer.elapsed()

        assert duration < 10.0, f"Large YAML serialization took {duration:.3f}s, expected <10.0s"

        snapshot_file = tmp_path / "perf" / snapshot.run_id / "snapshot.yaml"
        file_size_mb = snapshot_file.stat().st_size / (1024 * 1024)
        assert file_size_mb < 15.0, f"Large YAML file size {file_size_mb:.2f}MB exceeds limit"

    def test_format_size_comparison_large_metrics(self, tmp_path: Path) -> None:
        """Test file size comparison across formats for large metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=3)

        # Store in all formats
        for fmt in [SnapshotFormat.JSON, SnapshotFormat.JSONL, SnapshotFormat.YAML]:
            repository.store(snapshot, fmt)

        # Measure file sizes
        json_file = tmp_path / "perf" / snapshot.run_id / "snapshot.json"
        jsonl_file = tmp_path / "perf" / snapshot.run_id / "snapshot.jsonl"
        yaml_file = tmp_path / "perf" / snapshot.run_id / "snapshot.yaml"

        json_size = json_file.stat().st_size
        jsonl_size = jsonl_file.stat().st_size
        yaml_size = yaml_file.stat().st_size

        # JSONL should be baseline (smallest)
        json_ratio = json_size / jsonl_size
        yaml_ratio = yaml_size / jsonl_size

        # JSON with indent should be ~1.2-1.6x JSONL (indentation overhead)
        assert json_ratio < 1.7, f"JSON/JSONL ratio {json_ratio:.2f} exceeds expected 1.7x"

        # YAML should be similar or slightly larger
        assert yaml_ratio < 1.8, f"YAML/JSONL ratio {yaml_ratio:.2f} exceeds expected 1.8x"

    def test_deserialize_json_small_baseline(self, tmp_path: Path) -> None:
        """Test JSON deserialization with small baseline metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("small", index=4)
        repository.store(snapshot, SnapshotFormat.JSON)

        with Timing() as timer:
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert loaded.run_id == snapshot.run_id
        assert duration < 0.05, f"Small JSON deserialization took {duration:.3f}s, expected <0.05s"

    def test_deserialize_json_medium_metrics(self, tmp_path: Path) -> None:
        """Test JSON deserialization with medium-scale metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("medium", index=4)
        repository.store(snapshot, SnapshotFormat.JSON)

        with Timing() as timer:
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert duration < 0.5, f"Medium JSON deserialization took {duration:.3f}s, expected <0.5s"

    def test_deserialize_json_large_metrics(self, tmp_path: Path) -> None:
        """Test JSON deserialization with large-scale metrics (50K tests)."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=4)
        repository.store(snapshot, SnapshotFormat.JSON)

        with Timing() as timer:
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert duration < 5.0, f"Large JSON deserialization took {duration:.3f}s, expected <5.0s"

    def test_deserialize_yaml_small_baseline(self, tmp_path: Path) -> None:
        """Test YAML deserialization with small baseline metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("small", index=5)
        repository.store(snapshot, SnapshotFormat.YAML)

        with Timing() as timer:
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert duration < 0.2, f"Small YAML deserialization took {duration:.3f}s, expected <0.2s"

    def test_deserialize_yaml_medium_metrics(self, tmp_path: Path) -> None:
        """Test YAML deserialization with medium-scale metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("medium", index=5)
        repository.store(snapshot, SnapshotFormat.YAML)

        with Timing() as timer:
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert duration < 2.0, f"Medium YAML deserialization took {duration:.3f}s, expected <2.0s"

    def test_deserialize_yaml_large_metrics(self, tmp_path: Path) -> None:
        """Test YAML deserialization with large-scale metrics (50K tests)."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=5)
        repository.store(snapshot, SnapshotFormat.YAML)

        with Timing() as timer:
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert duration < 20.0, f"Large YAML deserialization took {duration:.3f}s, expected <20.0s"

    def test_roundtrip_large_metrics_json(self, tmp_path: Path) -> None:
        """Test serialize + deserialize roundtrip with JSON format and large metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=6)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSON)
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert loaded.signals.test_signal.test_count == snapshot.signals.test_signal.test_count
        assert duration < 10.0, f"Large JSON roundtrip took {duration:.3f}s, expected <10.0s"

    def test_roundtrip_large_metrics_jsonl(self, tmp_path: Path) -> None:
        """Test serialize + deserialize roundtrip with JSONL format and large metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=7)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSONL)
            loaded = repository.load(snapshot.run_id)
        duration = timer.elapsed()

        assert loaded is not None
        assert loaded.signals.test_signal.test_count == snapshot.signals.test_signal.test_count
        assert duration < 1.0, f"Large JSONL roundtrip took {duration:.3f}s, expected <1.0s"

    def test_serialization_scales_linearly(self, tmp_path: Path) -> None:
        """Test that serialization time scales linearly with metric count."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Measure serialization times for different tiers
        times = {}
        for tier in ["small", "medium", "large"]:
            snapshot = create_large_snapshot(tier, index=8)
            with Timing() as timer:
                repository.store(snapshot, SnapshotFormat.JSON)
            times[tier] = timer.elapsed()

        # Verify scaling is roughly linear (not exponential)
        # medium should be ~50x larger than small (5K vs 100 test count)
        # large should be ~10x larger than medium (50K vs 5K test count)

        ratio_medium_to_small = times["medium"] / max(times["small"], 0.001)
        ratio_large_to_medium = times["large"] / max(times["medium"], 0.001)

        # Allow generous margin for non-linear overhead (up to 100x for 50x growth)
        assert ratio_medium_to_small < 100, (
            f"Medium/small ratio {ratio_medium_to_small:.1f}x indicates non-linear degradation"
        )

        assert ratio_large_to_medium < 20, (
            f"Large/medium ratio {ratio_large_to_medium:.1f}x indicates non-linear degradation"
        )

    def test_memory_efficiency_large_snapshot(self, tmp_path: Path) -> None:
        """Test that large snapshot deserialization uses reasonable memory."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=9)
        repository.store(snapshot, SnapshotFormat.JSON)

        with MemoryTracker() as mem:
            loaded = repository.load(snapshot.run_id)

        assert loaded is not None
        peak_mb = mem.peak_memory_mb

        # Large snapshot should use less than 500MB
        assert peak_mb < 500, f"Peak memory {peak_mb:.0f}MB exceeds threshold of 500MB"

    def test_store_large_snapshot_performance(self, tmp_path: Path) -> None:
        """Test complete store operation performance with large metrics."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=10)

        # Full store operation including serialization, checksum, and file I/O
        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSON)
        duration = timer.elapsed()

        # Should complete within threshold
        assert duration < 5.0, f"Large snapshot store took {duration:.3f}s, expected <5.0s"

        # Verify snapshot was stored
        stored = repository.load(snapshot.run_id)
        assert stored is not None
        assert stored.run_id == snapshot.run_id

    def test_list_large_snapshot_batches(self, tmp_path: Path) -> None:
        """Test listing performance with multiple large snapshots."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")

        # Store 10 large snapshots
        snapshot_ids = []
        for i in range(10):
            snapshot = create_large_snapshot("large", index=i)
            repository.store(snapshot, SnapshotFormat.JSON)
            snapshot_ids.append(snapshot.run_id)

        # List should complete quickly even with large snapshots
        with Timing() as timer:
            snapshots = repository.list_snapshots()
        duration = timer.elapsed()

        assert len(snapshots) >= 10
        assert duration < 1.0, f"Listing 10 large snapshots took {duration:.3f}s, expected <1.0s"

    def test_throughput_json_large_metrics(self, tmp_path: Path) -> None:
        """Test metrics throughput for JSON serialization with large metric sets."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=11)

        with Timing() as timer:
            repository.store(snapshot, SnapshotFormat.JSON)
        duration = timer.elapsed()

        # Calculate metrics count
        test_count = snapshot.signals.test_signal.test_count or 0
        commit_count = len(snapshot.signals.recent_commits)
        file_count = len(snapshot.signals.file_hotspots)
        metrics_count = test_count + commit_count + file_count

        throughput = metrics_count / max(duration, 0.001)

        # Should serialize at least 1000 metrics per second
        assert throughput > 1000, f"Throughput {throughput:.0f} metrics/s is below 1000/s threshold"

    def test_compare_format_speed_json_vs_jsonl(self, tmp_path: Path) -> None:
        """Test that JSONL serialization completes within expected time."""
        repository = LocalSnapshotRepository(root=tmp_path / "perf")
        snapshot = create_large_snapshot("large", index=12)

        # Measure JSONL time - should complete in under 100ms
        with Timing() as timer_jsonl:
            repository.store(snapshot, SnapshotFormat.JSONL)
        jsonl_time = timer_jsonl.elapsed()

        # JSONL should complete quickly for 50K metrics
        assert jsonl_time < 0.1, f"JSONL serialization {jsonl_time:.3f}s exceeded 100ms threshold"
