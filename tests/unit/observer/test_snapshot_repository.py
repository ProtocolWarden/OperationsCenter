# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for snapshot repository implementations."""

import json
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
def temp_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repository root."""
    return tmp_path / "observer"


@pytest.fixture
def repository(temp_repo_root: Path) -> LocalSnapshotRepository:
    """Create a local snapshot repository."""
    return LocalSnapshotRepository(
        root=temp_repo_root,
        retention_days=7,
        retention_count=10,
    )


class TestLocalSnapshotRepositoryStore:
    """Tests for storing snapshots."""

    def test_store_json_format(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test storing a snapshot in JSON format."""
        metadata = repository.store(test_snapshot, SnapshotFormat.JSON)

        assert metadata["run_id"] == test_snapshot.run_id
        assert metadata["format"] == "json"
        assert metadata["version"] == 1
        assert "checksum" in metadata

        # Verify file was created
        snapshot_file = repository.root / test_snapshot.run_id / "snapshot.json"
        assert snapshot_file.exists()
        assert snapshot_file.read_text()

    def test_store_jsonl_format(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test storing a snapshot in JSONL format."""
        metadata = repository.store(test_snapshot, SnapshotFormat.JSONL)

        assert metadata["format"] == "jsonl"

        snapshot_file = repository.root / test_snapshot.run_id / "snapshot.jsonl"
        assert snapshot_file.exists()

    def test_store_yaml_format(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test storing a snapshot in YAML format."""
        metadata = repository.store(test_snapshot, SnapshotFormat.YAML)

        assert metadata["format"] == "yaml"

        snapshot_file = repository.root / test_snapshot.run_id / "snapshot.yaml"
        assert snapshot_file.exists()
        content = snapshot_file.read_text()
        assert "run_id:" in content
        assert "observed_at:" in content

    def test_store_creates_index(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that storing snapshots updates the index."""
        repository.store(test_snapshot, SnapshotFormat.JSON)

        index_path = repository.root / "snapshots.index"
        assert index_path.exists()

        lines = index_path.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["run_id"] == test_snapshot.run_id
        assert entry["format"] == "json"

    def test_store_multiple_snapshots_updates_index(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that multiple snapshots are tracked in the index."""
        snap1 = test_snapshot
        repository.store(snap1, SnapshotFormat.JSON)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test-command-2",
            repo=snap1.repo,
            signals=snap1.signals,
        )
        repository.store(snap2, SnapshotFormat.JSON)

        index_path = repository.root / "snapshots.index"
        lines = index_path.read_text().strip().split("\n")
        assert len(lines) == 2


class TestLocalSnapshotRepositoryLoad:
    """Tests for loading snapshots."""

    def test_load_json_snapshot(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test loading a snapshot from JSON."""
        repository.store(test_snapshot, SnapshotFormat.JSON)
        loaded = repository.load(test_snapshot.run_id)

        assert loaded.run_id == test_snapshot.run_id
        assert loaded.observed_at == test_snapshot.observed_at
        assert loaded.repo.name == test_snapshot.repo.name

    def test_load_jsonl_snapshot(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test loading a snapshot from JSONL."""
        repository.store(test_snapshot, SnapshotFormat.JSONL)
        loaded = repository.load(test_snapshot.run_id)

        assert loaded.run_id == test_snapshot.run_id

    def test_load_yaml_snapshot(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test loading a snapshot from YAML."""
        repository.store(test_snapshot, SnapshotFormat.YAML)
        loaded = repository.load(test_snapshot.run_id)

        assert loaded.run_id == test_snapshot.run_id

    def test_load_nonexistent_snapshot(self, repository: LocalSnapshotRepository) -> None:
        """Test loading a nonexistent snapshot raises error."""
        with pytest.raises(FileNotFoundError):
            repository.load("nonexistent_run_id")

    def test_load_preserves_data_integrity(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that loaded snapshot matches the original."""
        repository.store(test_snapshot, SnapshotFormat.JSON)
        loaded = repository.load(test_snapshot.run_id)

        # Verify key fields are preserved
        assert loaded.observer_version == test_snapshot.observer_version
        assert loaded.source_command == test_snapshot.source_command
        assert loaded.repo.current_branch == test_snapshot.repo.current_branch
        assert loaded.signals.test_signal.test_count == test_snapshot.signals.test_signal.test_count


class TestLocalSnapshotRepositoryList:
    """Tests for listing snapshots."""

    def test_list_snapshots_empty(self, repository: LocalSnapshotRepository) -> None:
        """Test listing snapshots when none exist."""
        snapshots = repository.list_snapshots()
        assert snapshots == []

    def test_list_snapshots_single(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test listing a single snapshot."""
        repository.store(test_snapshot, SnapshotFormat.JSON)
        snapshots = repository.list_snapshots()

        assert len(snapshots) == 1
        assert snapshots[0]["run_id"] == test_snapshot.run_id

    def test_list_snapshots_with_limit(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test listing snapshots with a limit."""
        for i in range(5):
            snap = RepoStateSnapshot(
                run_id=f"test_obs_20260607T{i:02d}0000Z_abc123_x7k9m",
                observed_at=datetime(2026, 6, 7, i, 0, 0, tzinfo=timezone.utc),
                observer_version=1,
                source_command="test",
                repo=test_snapshot.repo,
                signals=test_snapshot.signals,
            )
            repository.store(snap, SnapshotFormat.JSON)

        snapshots = repository.list_snapshots(limit=2)
        assert len(snapshots) == 2

    def test_list_snapshots_sorted_by_date(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that snapshots are listed in reverse chronological order."""
        snaps = []
        for i in range(3):
            snap = RepoStateSnapshot(
                run_id=f"test_obs_20260607T{i:02d}0000Z_abc123_x7k9m",
                observed_at=datetime(2026, 6, 7, i, 0, 0, tzinfo=timezone.utc),
                observer_version=1,
                source_command="test",
                repo=test_snapshot.repo,
                signals=test_snapshot.signals,
            )
            snaps.append(snap)
            repository.store(snap, SnapshotFormat.JSON)

        listed = repository.list_snapshots()
        assert len(listed) == 3
        # Most recent first
        for i, metadata in enumerate(listed):
            assert metadata["run_id"] == snaps[2 - i].run_id


class TestLocalSnapshotRepositoryDelete:
    """Tests for deleting snapshots."""

    def test_delete_snapshot(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test deleting a snapshot."""
        repository.store(test_snapshot, SnapshotFormat.JSON)
        assert repository.delete(test_snapshot.run_id)

        # Verify it's gone
        with pytest.raises(FileNotFoundError):
            repository.load(test_snapshot.run_id)

    def test_delete_nonexistent_snapshot(self, repository: LocalSnapshotRepository) -> None:
        """Test deleting a nonexistent snapshot returns False."""
        assert not repository.delete("nonexistent_id")


class TestLocalSnapshotRepositoryCompare:
    """Tests for comparing snapshots."""

    def test_compare_snapshots(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test comparing two snapshots."""
        snap1 = test_snapshot
        repository.store(snap1, SnapshotFormat.JSON)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=snap1.repo,
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(
                    status="passing",
                    test_count=150,  # Different from snap1
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
        repository.store(snap2, SnapshotFormat.JSON)

        diff = repository.compare(snap1.run_id, snap2.run_id)

        # Should detect test count difference
        assert "signals" in diff
        assert "test_count" in diff["signals"]
        assert diff["signals"]["test_count"]["before"] == 100
        assert diff["signals"]["test_count"]["after"] == 150

    def test_compare_identical_snapshots(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test comparing identical snapshots."""
        repository.store(test_snapshot, SnapshotFormat.JSON)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=test_snapshot.signals,
        )
        repository.store(snap2, SnapshotFormat.JSON)

        diff = repository.compare(test_snapshot.run_id, snap2.run_id)

        # Should be empty (no differences)
        assert diff == {}


class TestLocalSnapshotRepositoryCleanup:
    """Tests for cleanup and retention policies."""

    def test_cleanup_retains_recent_snapshots(
        self, repository: LocalSnapshotRepository, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that recent snapshots are retained."""
        # Store snapshots within retention period
        for i in range(3):
            snap = RepoStateSnapshot(
                run_id=f"test_obs_20260607T{i:02d}0000Z_abc123_x7k9m",
                observed_at=datetime(2026, 6, 7, i, 0, 0, tzinfo=timezone.utc),
                observer_version=1,
                source_command="test",
                repo=test_snapshot.repo,
                signals=test_snapshot.signals,
            )
            repository.store(snap, SnapshotFormat.JSON)

        deleted = repository.cleanup()
        assert deleted == []

        # All should still exist
        assert len(repository.list_snapshots()) == 3

    def test_cleanup_respects_count_limit(
        self, temp_repo_root: Path, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that cleanup respects retention count."""
        repo = LocalSnapshotRepository(
            root=temp_repo_root,
            retention_days=365,  # High enough to not trigger date-based cleanup
            retention_count=3,  # Keep only 3 most recent
        )

        # Store 5 snapshots
        for i in range(5):
            snap = RepoStateSnapshot(
                run_id=f"test_obs_20260607T{i:02d}0000Z_abc123_x7k9m",
                observed_at=datetime(2026, 6, 7, i, 0, 0, tzinfo=timezone.utc),
                observer_version=1,
                source_command="test",
                repo=test_snapshot.repo,
                signals=test_snapshot.signals,
            )
            repo.store(snap, SnapshotFormat.JSON)

        deleted = repo.cleanup()
        # Should delete 2 oldest
        assert len(deleted) == 2

        # Only 3 should remain
        assert len(repo.list_snapshots()) == 3
