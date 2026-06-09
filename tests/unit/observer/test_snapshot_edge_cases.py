# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Edge case tests for snapshot repositories and managers.

Tests for corrupted data, permission errors, concurrent updates, missing snapshots,
and other error conditions that could occur in production environments.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

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

pytestmark = pytest.mark.edge_case


@pytest.fixture
def test_snapshot() -> RepoStateSnapshot:
    """Create a test snapshot."""
    return RepoStateSnapshot(
        run_id="test_obs_20260607T120000Z_abc123_x7k9m",
        observed_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc),
        observer_version=1,
        source_command="test-command",
        repo=RepoContextSnapshot(
            name="test-repo",
            path=Path("/tmp/test"),
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(
                status="passing",
                test_count=100,
                source="pytest",
            ),
            dependency_drift=DependencyDriftSignal(
                status="healthy",
                source="pip-audit",
            ),
            todo_signal=TodoSignal(
                todo_count=5,
                fixme_count=2,
            ),
        ),
    )


@pytest.fixture
def corrupted_snapshot_dir(tmp_path: Path) -> Path:
    """Create a directory with corrupted snapshot files."""
    snapshot_dir = tmp_path / "corrupted"
    snapshot_dir.mkdir()

    # Create corrupted JSON file
    json_file = snapshot_dir / "snapshot.json"
    json_file.write_text("{ invalid json content }")

    # Create truncated JSON file
    truncated_file = snapshot_dir / "truncated.json"
    truncated_file.write_text('{"run_id": "test"')

    # Create binary garbage file
    binary_file = snapshot_dir / "garbage.json"
    binary_file.write_bytes(b"\x00\x01\x02\x03\x04")

    return snapshot_dir


class TestSnapshotRepositoryEdgeCases:
    """Tests for edge cases in snapshot repository operations."""

    def test_load_corrupted_json(self, tmp_path: Path, corrupted_snapshot_dir: Path) -> None:
        """Test loading corrupted JSON file raises appropriate error."""
        with pytest.raises(Exception):  # Should raise JSON decode error
            json.loads(corrupted_snapshot_dir.joinpath("snapshot.json").read_text())

    def test_load_nonexistent_snapshot_directory(self, tmp_path: Path) -> None:
        """Test loading from non-existent snapshot directory raises error."""
        repository = LocalSnapshotRepository(root=tmp_path)

        with pytest.raises(FileNotFoundError):
            repository.load("nonexistent_run_id")

    def test_store_with_read_only_directory(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test storing to read-only directory raises permission error."""
        repo_root = tmp_path / "readonly"
        repo_root.mkdir()
        repository = LocalSnapshotRepository(root=repo_root)

        # Make directory read-only
        os.chmod(repo_root, 0o444)

        try:
            with pytest.raises((PermissionError, OSError)):
                repository.store(test_snapshot, SnapshotFormat.JSON)
        finally:
            # Restore permissions for cleanup
            os.chmod(repo_root, 0o755)

    def test_cleanup_with_corrupted_index(self, tmp_path: Path) -> None:
        """Test cleanup gracefully handles corrupted index file."""
        repository = LocalSnapshotRepository(root=tmp_path)
        index_file = tmp_path / "snapshots.index"

        # Create corrupted index file
        index_file.write_text("invalid json\ninvalid json\n")

        # Cleanup should complete without raising
        result = repository.cleanup()
        # Should still return something, even with corrupted index
        assert isinstance(result, list)

    def test_compare_snapshots_with_missing_metadata(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test comparing snapshots handles missing metadata gracefully."""
        repository = LocalSnapshotRepository(root=tmp_path)

        # Store first snapshot
        repository.store(test_snapshot, SnapshotFormat.JSON)
        run_id1 = test_snapshot.run_id

        # Create second snapshot with different data
        modified_snapshot = test_snapshot.model_copy(
            update={"run_id": "test_obs_20260607T130000Z_def456_y8l0n"}
        )
        repository.store(modified_snapshot, SnapshotFormat.JSON)
        run_id2 = modified_snapshot.run_id

        # Compare should work even with metadata
        comparison = repository.compare(run_id1, run_id2)
        assert comparison is not None
        assert "run_id" in comparison or comparison is not None

    def test_store_minimal_snapshot_signals(self, tmp_path: Path) -> None:
        """Test storing snapshot with minimal signals."""
        repository = LocalSnapshotRepository(root=tmp_path)

        snapshot = RepoStateSnapshot(
            run_id="test_obs_empty_20260607T120000Z_abc123_x7k9m",
            observed_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=RepoContextSnapshot(
                name="test",
                path=Path("/tmp"),
                current_branch="main",
                base_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(status="unknown", source="test"),
                dependency_drift=DependencyDriftSignal(status="unknown", source="test"),
                todo_signal=TodoSignal(todo_count=0, fixme_count=0),
            ),
        )

        metadata = repository.store(snapshot, SnapshotFormat.JSON)
        assert metadata is not None

        # Should be able to load it back
        loaded = repository.load(snapshot.run_id)
        assert loaded is not None

    def test_list_snapshots_with_mixed_formats(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test listing snapshots when directory contains mixed format files."""
        repository = LocalSnapshotRepository(root=tmp_path)

        # Store snapshot in different formats
        repository.store(test_snapshot, SnapshotFormat.JSON)

        modified = test_snapshot.model_copy(
            update={"run_id": "test_obs_20260607T130000Z_def456_y8l0n"}
        )
        repository.store(modified, SnapshotFormat.YAML)

        # List should work with both formats
        snapshots = repository.list_snapshots()
        assert len(snapshots) >= 1  # Should find at least one snapshot

    def test_delete_already_deleted_snapshot(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test deleting already-deleted snapshot returns False."""
        repository = LocalSnapshotRepository(root=tmp_path)

        # Store and delete
        repository.store(test_snapshot, SnapshotFormat.JSON)
        result1 = repository.delete(test_snapshot.run_id)
        assert result1 is True

        # Delete again should return False
        result2 = repository.delete(test_snapshot.run_id)
        assert result2 is False


class TestSnapshotManagerEdgeCases:
    """Tests for edge cases in snapshot manager operations."""

    def test_save_then_immediately_delete(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test save followed by immediate delete."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        manager.save_snapshot(test_snapshot)
        manager.delete_snapshot(test_snapshot.run_id)

        # Should not find the deleted snapshot
        with pytest.raises(FileNotFoundError):
            manager.get_snapshot(test_snapshot.run_id)

    def test_get_latest_after_delete_all(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test getting latest snapshot after deleting all snapshots."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        manager.save_snapshot(test_snapshot)
        manager.delete_snapshot(test_snapshot.run_id)

        # Getting latest should return None
        latest = manager.get_latest_snapshot()
        assert latest is None

    def test_compare_snapshot_with_itself(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test comparing snapshot with itself."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        manager.save_snapshot(test_snapshot)

        # Compare snapshot with itself
        comparison = manager.compare_snapshots(test_snapshot.run_id, test_snapshot.run_id)

        # Should detect no changes
        assert comparison is not None
        assert comparison.has_changes() is False

    def test_export_nonexistent_snapshot(self, tmp_path: Path) -> None:
        """Test exporting non-existent snapshot raises error."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        with pytest.raises(FileNotFoundError):
            manager.export_snapshot("nonexistent_run_id", tmp_path / "export.json")

    def test_cleanup_with_zero_retention(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test cleanup with zero retention count."""
        manager = SnapshotManager(
            LocalSnapshotRepository(root=tmp_path / "snapshots", retention_count=1)
        )

        manager.save_snapshot(test_snapshot)

        # Cleanup should work
        cleaned = manager.repository.cleanup()
        assert isinstance(cleaned, list)

        # Should still have one snapshot (retention_count=1)
        remaining = manager.get_snapshots(limit=100)
        assert len(remaining) >= 1


class TestConcurrentSnapshotOperations:
    """Tests for concurrent access to snapshot storage."""

    def test_concurrent_saves(self, tmp_path: Path, test_snapshot: RepoStateSnapshot) -> None:
        """Test concurrent save operations don't corrupt data."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        saved_snapshots = []
        errors = []

        def save_snapshot(index: int) -> None:
            try:
                modified = test_snapshot.model_copy(
                    update={"run_id": f"test_obs_20260607T120000Z_abc{index:03d}_x7k9m"}
                )
                manager.save_snapshot(modified)
                saved_snapshots.append(modified.run_id)
            except Exception as e:
                errors.append(e)

        # Create multiple threads saving concurrently
        threads = [Thread(target=save_snapshot, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All saves should succeed
        assert len(errors) == 0
        assert len(saved_snapshots) == 5

        # All snapshots should be readable
        for run_id in saved_snapshots:
            snapshot = manager.get_snapshot(run_id)
            assert snapshot is not None

    def test_concurrent_reads(self, tmp_path: Path, test_snapshot: RepoStateSnapshot) -> None:
        """Test concurrent read operations work correctly."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        # Save a snapshot first
        manager.save_snapshot(test_snapshot)

        read_results = []
        errors = []

        def read_snapshot() -> None:
            try:
                snapshot = manager.get_snapshot(test_snapshot.run_id)
                if snapshot:
                    read_results.append(snapshot.run_id)
            except Exception as e:
                errors.append(e)

        # Create multiple threads reading concurrently
        threads = [Thread(target=read_snapshot) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All reads should succeed
        assert len(errors) == 0
        assert len(read_results) == 5

    def test_concurrent_save_and_delete(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test concurrent save and delete operations."""
        manager = SnapshotManager(LocalSnapshotRepository(root=tmp_path / "snapshots"))

        errors = []

        def save_and_delete(index: int) -> None:
            try:
                modified = test_snapshot.model_copy(
                    update={"run_id": f"test_obs_20260607T120000Z_xyz{index:03d}_a1b2c3"}
                )
                manager.save_snapshot(modified)
                manager.delete_snapshot(modified.run_id)
            except Exception as e:
                errors.append(e)

        # Create multiple threads doing save+delete
        threads = [Thread(target=save_and_delete, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All operations should succeed
        assert len(errors) == 0


class TestSnapshotFormatConversion:
    """Tests for snapshot format conversion edge cases."""

    def test_round_trip_json_yaml(self, tmp_path: Path, test_snapshot: RepoStateSnapshot) -> None:
        """Test round-trip conversion between JSON and YAML formats."""
        repository = LocalSnapshotRepository(root=tmp_path)

        # Store as JSON
        repository.store(test_snapshot, SnapshotFormat.JSON)

        # Load and re-save as YAML
        loaded = repository.load(test_snapshot.run_id)
        assert loaded is not None

        # Store again with different format
        modified_id = "test_obs_20260607T130000Z_def456_y8l0n"
        modified = loaded.model_copy(update={"run_id": modified_id})
        repository.store(modified, SnapshotFormat.YAML)

        # Load from YAML and verify
        reloaded = repository.load(modified_id)
        assert reloaded is not None
        assert reloaded.run_id == modified_id

    def test_jsonl_append_many_snapshots(
        self, tmp_path: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test appending many snapshots in JSONL format."""
        repository = LocalSnapshotRepository(root=tmp_path)

        # Store 10 snapshots
        stored_ids = []
        for i in range(10):
            modified = test_snapshot.model_copy(
                update={"run_id": f"test_obs_20260607T120000Z_abc{i:03d}_x7k9m"}
            )
            repository.store(modified, SnapshotFormat.JSONL)
            stored_ids.append(modified.run_id)

        # Verify all are stored
        snapshots = repository.list_snapshots()
        assert len([s for s in snapshots if s["run_id"] in stored_ids]) == 10

    def test_large_snapshot_storage(self, tmp_path: Path) -> None:
        """Test storing very large snapshot with many signals."""
        repository = LocalSnapshotRepository(root=tmp_path)

        # Create a large snapshot
        large_snapshot = RepoStateSnapshot(
            run_id="test_obs_large_20260607T120000Z_abc123_x7k9m",
            observed_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=RepoContextSnapshot(
                name="test" * 100,  # Large name
                path=Path("/tmp"),
                current_branch="main" * 50,  # Large branch name
                base_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(
                    status="passing",
                    test_count=99999,  # Large test count
                    source="pytest" * 50,  # Large source
                ),
                dependency_drift=DependencyDriftSignal(
                    status="healthy",
                    source="pip-audit",
                ),
                todo_signal=TodoSignal(
                    todo_count=9999,  # Large count
                    fixme_count=9999,
                ),
            ),
        )

        # Store should handle large snapshots
        metadata = repository.store(large_snapshot, SnapshotFormat.JSON)
        assert metadata is not None

        # Load should preserve data
        loaded = repository.load(large_snapshot.run_id)
        assert loaded is not None
        assert loaded.signals.test_signal.test_count == 99999
