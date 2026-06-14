# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for snapshot manager."""

from datetime import datetime, timezone, timedelta
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
from operations_center.observer.snapshot_manager import SnapshotManager, SnapshotComparison
from operations_center.observer.snapshot_repository import (
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
def snapshot_manager(temp_repo_root: Path) -> SnapshotManager:
    """Create a snapshot manager."""
    return SnapshotManager(
        root=temp_repo_root,
        retention_days=7,
        retention_count=10,
    )


class TestSnapshotManagerSave:
    """Tests for saving snapshots."""

    def test_save_snapshot_default_format(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test saving a snapshot with default format."""
        metadata = snapshot_manager.save_snapshot(test_snapshot)

        assert metadata["run_id"] == test_snapshot.run_id
        assert metadata["format"] == "json"

    def test_save_snapshot_custom_format(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test saving a snapshot with custom format."""
        metadata = snapshot_manager.save_snapshot(test_snapshot, SnapshotFormat.YAML)
        assert metadata["format"] == "yaml"

    def test_save_multiple_snapshots(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test saving multiple snapshots."""
        snap1 = test_snapshot
        snapshot_manager.save_snapshot(snap1)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=snap1.repo,
            signals=snap1.signals,
        )
        snapshot_manager.save_snapshot(snap2)

        snapshots = snapshot_manager.get_snapshots()
        assert len(snapshots) == 2


class TestSnapshotManagerGet:
    """Tests for getting snapshots."""

    def test_get_snapshot(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test getting a specific snapshot."""
        snapshot_manager.save_snapshot(test_snapshot)
        loaded = snapshot_manager.get_snapshot(test_snapshot.run_id)

        assert loaded.run_id == test_snapshot.run_id

    def test_get_latest_snapshot(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test getting the latest snapshot."""
        snapshot_manager.save_snapshot(test_snapshot)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=test_snapshot.signals,
        )
        snapshot_manager.save_snapshot(snap2)

        latest = snapshot_manager.get_latest_snapshot()
        assert latest is not None
        assert latest.run_id == snap2.run_id

    def test_get_latest_snapshot_when_none_exist(self, snapshot_manager: SnapshotManager) -> None:
        """Test getting latest snapshot when none exist."""
        latest = snapshot_manager.get_latest_snapshot()
        assert latest is None

    def test_get_snapshots_with_limit(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test getting snapshots with a limit."""
        for i in range(5):
            ts = datetime.now(timezone.utc) - timedelta(days=1) + timedelta(hours=i)
            snap = RepoStateSnapshot(
                run_id=f"test_obs_{ts.strftime('%Y%m%dT%H%M%S')}Z_abc123_x7k9m",
                observed_at=ts,
                observer_version=1,
                source_command="test",
                repo=test_snapshot.repo,
                signals=test_snapshot.signals,
            )
            snapshot_manager.save_snapshot(snap)

        snapshots = snapshot_manager.get_snapshots(limit=2)
        assert len(snapshots) == 2


class TestSnapshotManagerCompare:
    """Tests for comparing snapshots."""

    def test_compare_snapshots(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test comparing two snapshots."""
        snapshot_manager.save_snapshot(test_snapshot)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(
                    status="passing",
                    test_count=150,
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
        snapshot_manager.save_snapshot(snap2)

        comparison = snapshot_manager.compare_snapshots(test_snapshot.run_id, snap2.run_id)

        assert isinstance(comparison, SnapshotComparison)
        assert comparison.has_changes()
        assert comparison.get_signal_changes()

    def test_compare_returns_structured_comparison(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test that comparison returns structured data."""
        snapshot_manager.save_snapshot(test_snapshot)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=test_snapshot.signals,
        )
        snapshot_manager.save_snapshot(snap2)

        comparison = snapshot_manager.compare_snapshots(test_snapshot.run_id, snap2.run_id)

        comparison_dict = comparison.to_dict()
        assert comparison_dict["snapshot1_id"] == test_snapshot.run_id
        assert comparison_dict["snapshot2_id"] == snap2.run_id
        assert "observed_at_1" in comparison_dict
        assert "observed_at_2" in comparison_dict
        assert "differences" in comparison_dict
        assert "has_changes" in comparison_dict


class TestSnapshotManagerDelete:
    """Tests for deleting snapshots."""

    def test_delete_snapshot(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test deleting a snapshot."""
        snapshot_manager.save_snapshot(test_snapshot)
        assert snapshot_manager.delete_snapshot(test_snapshot.run_id)

        # Should no longer be retrievable
        with pytest.raises(FileNotFoundError):
            snapshot_manager.get_snapshot(test_snapshot.run_id)

    def test_delete_nonexistent_snapshot(self, snapshot_manager: SnapshotManager) -> None:
        """Test deleting a nonexistent snapshot."""
        assert not snapshot_manager.delete_snapshot("nonexistent")


class TestSnapshotManagerCleanup:
    """Tests for cleanup operations."""

    def test_cleanup_old_snapshots(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test cleanup removes old snapshots."""
        # Create snapshots with different dates
        manager = SnapshotManager(
            root=snapshot_manager.repository.root,
            retention_days=7,
            retention_count=2,
        )

        for i in range(4):
            ts = datetime.now(timezone.utc) - timedelta(days=1) + timedelta(hours=i)
            snap = RepoStateSnapshot(
                run_id=f"test_obs_{ts.strftime('%Y%m%dT%H%M%S')}Z_abc123_x7k9m",
                observed_at=ts,
                observer_version=1,
                source_command="test",
                repo=test_snapshot.repo,
                signals=test_snapshot.signals,
            )
            manager.save_snapshot(snap)

        deleted = manager.cleanup_old_snapshots()
        # Should delete oldest 2 (keeping only 2 most recent)
        assert len(deleted) == 2


class TestSnapshotManagerGetByDate:
    """Tests for getting snapshots by date."""

    def test_get_snapshot_by_date(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot
    ) -> None:
        """Test getting snapshot closest to a target date."""
        snap1 = test_snapshot
        snapshot_manager.save_snapshot(snap1)

        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=test_snapshot.signals,
        )
        snapshot_manager.save_snapshot(snap2)

        # Query for time between the two
        target = datetime(2026, 6, 7, 12, 30, 0, tzinfo=timezone.utc)
        found = snapshot_manager.get_snapshot_by_date(target)

        assert found is not None
        # Should return the closer one
        assert found.run_id in [snap1.run_id, snap2.run_id]

    def test_get_snapshot_by_date_no_snapshots(self, snapshot_manager: SnapshotManager) -> None:
        """Test getting snapshot by date when none exist."""
        target = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
        found = snapshot_manager.get_snapshot_by_date(target)
        assert found is None


class TestSnapshotManagerExport:
    """Tests for exporting snapshots."""

    def test_export_snapshot(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot, tmp_path: Path
    ) -> None:
        """Test exporting a snapshot to file."""
        snapshot_manager.save_snapshot(test_snapshot)

        export_path = tmp_path / "exported.json"
        snapshot_manager.export_snapshot(test_snapshot.run_id, export_path, SnapshotFormat.JSON)

        assert export_path.exists()
        content = export_path.read_text()
        assert test_snapshot.run_id in content

    def test_export_snapshot_yaml(
        self, snapshot_manager: SnapshotManager, test_snapshot: RepoStateSnapshot, tmp_path: Path
    ) -> None:
        """Test exporting a snapshot in YAML format."""
        snapshot_manager.save_snapshot(test_snapshot)

        export_path = tmp_path / "exported.yaml"
        snapshot_manager.export_snapshot(test_snapshot.run_id, export_path, SnapshotFormat.YAML)

        assert export_path.exists()
        content = export_path.read_text()
        assert "run_id:" in content


class TestSnapshotComparison:
    """Tests for SnapshotComparison class."""

    def test_snapshot_comparison_has_changes(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test SnapshotComparison.has_changes()."""
        snap1 = test_snapshot
        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(
                    status="passing",
                    test_count=150,  # Different
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

        diff_data = {"signals": {"test_count": {"before": 100, "after": 150}}}
        comparison = SnapshotComparison(snap1, snap2, diff_data)

        assert comparison.has_changes()

    def test_snapshot_comparison_no_changes(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test SnapshotComparison with no changes."""
        snap1 = test_snapshot
        snap2 = RepoStateSnapshot(
            run_id="test_obs_20260607T130000Z_def456_y2p3q",
            observed_at=datetime(2026, 6, 7, 13, 0, 0, tzinfo=timezone.utc),
            observer_version=1,
            source_command="test",
            repo=test_snapshot.repo,
            signals=test_snapshot.signals,
        )

        comparison = SnapshotComparison(snap1, snap2, {})
        assert not comparison.has_changes()

    def test_snapshot_comparison_get_changes(self, test_snapshot: RepoStateSnapshot) -> None:
        """Test getting specific change types."""
        snap1 = test_snapshot
        snap2 = test_snapshot

        diff_data = {
            "signals": {"test_count": {"before": 100, "after": 150}},
            "repo": {"is_dirty": {"before": False, "after": True}},
        }
        comparison = SnapshotComparison(snap1, snap2, diff_data)

        assert comparison.get_signal_changes() == {"test_count": {"before": 100, "after": 150}}
        assert comparison.get_repo_changes() == {"is_dirty": {"before": False, "after": True}}
