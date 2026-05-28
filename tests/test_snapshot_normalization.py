# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.insights.service import InsightEngineService
from operations_center.observer.models import (
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    CheckSignal,
    DependencyDriftSignal,
    TodoSignal,
)


@pytest.fixture
def snapshot_builder():
    def _build(
        run_id: str,
        observed_at: datetime | None = None,
    ) -> RepoStateSnapshot:
        return RepoStateSnapshot(
            run_id=run_id,
            observed_at=observed_at,
            observer_version=1,
            source_command="test",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/tmp/test-repo"),
                current_branch="main",
                base_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(status="pass"),
                dependency_drift=DependencyDriftSignal(status="ok"),
                todo_signal=TodoSignal(),
            ),
        )

    return _build


@pytest.fixture
def service():
    mock_loader = MagicMock()
    mock_loader.load.return_value = []
    return InsightEngineService(
        loader=mock_loader,
        derivers=[],
        artifact_writer=None,
    )


class TestRepoStateSnapshotOptional:
    def test_observed_at_can_be_none(self, snapshot_builder):
        snapshot = snapshot_builder(run_id="test1", observed_at=None)
        assert snapshot.observed_at is None

    def test_observed_at_can_be_datetime(self, snapshot_builder):
        now = datetime.now(UTC)
        snapshot = snapshot_builder(run_id="test1", observed_at=now)
        assert snapshot.observed_at == now

    def test_default_observed_at_is_none(self):
        snapshot = RepoStateSnapshot(
            run_id="test1",
            observer_version=1,
            source_command="test",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/tmp/test-repo"),
                current_branch="main",
                base_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=CheckSignal(status="pass"),
                dependency_drift=DependencyDriftSignal(status="ok"),
                todo_signal=TodoSignal(),
            ),
        )
        assert snapshot.observed_at is None


class TestNormalizeSnapshotsHappyPath:
    def test_all_snapshots_have_observed_at(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        t2 = datetime(2026, 5, 28, 11, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=t1),
            snapshot_builder(run_id="a2", observed_at=t2),
        ]

        normalized = service._normalize_snapshots(snapshots)

        assert len(normalized) == 2
        assert normalized[0].observed_at == t1
        assert normalized[1].observed_at == t2

    def test_single_snapshot_with_observed_at(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        snapshots = [snapshot_builder(run_id="a1", observed_at=t1)]

        normalized = service._normalize_snapshots(snapshots)

        assert len(normalized) == 1
        assert normalized[0].observed_at == t1

    def test_empty_snapshots_list(self, service):
        normalized = service._normalize_snapshots([])
        assert normalized == []


class TestNormalizeSnapshotsFallbackPath:
    def test_single_missing_at_start_forward_inference(self, service, snapshot_builder):
        t2 = datetime(2026, 5, 28, 11, 0, tzinfo=UTC)
        t3 = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=t2),
            snapshot_builder(run_id="a3", observed_at=t3),
        ]

        normalized = service._normalize_snapshots(snapshots)

        assert normalized[0].observed_at == t2  # Forward inference to a2
        assert normalized[1].observed_at == t2
        assert normalized[2].observed_at == t3

    def test_single_missing_in_middle_forward_inference(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        t3 = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=t1),
            snapshot_builder(run_id="a2", observed_at=None),
            snapshot_builder(run_id="a3", observed_at=t3),
        ]

        normalized = service._normalize_snapshots(snapshots)

        assert normalized[0].observed_at == t1
        assert normalized[1].observed_at == t3  # Forward inference to a3
        assert normalized[2].observed_at == t3

    def test_single_missing_at_end_backward_inference(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        t2 = datetime(2026, 5, 28, 11, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=t1),
            snapshot_builder(run_id="a2", observed_at=t2),
            snapshot_builder(run_id="a3", observed_at=None),
        ]

        normalized = service._normalize_snapshots(snapshots)

        assert normalized[0].observed_at == t1
        assert normalized[1].observed_at == t2
        assert normalized[2].observed_at == t2  # Backward inference to a2

    def test_multiple_missing_at_start(self, service, snapshot_builder):
        t3 = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=None),
            snapshot_builder(run_id="a3", observed_at=t3),
        ]

        normalized = service._normalize_snapshots(snapshots)

        assert normalized[0].observed_at == t3  # Forward inference to a3
        assert normalized[1].observed_at == t3  # Forward inference to a3
        assert normalized[2].observed_at == t3

    def test_multiple_missing_in_sequence(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        t4 = datetime(2026, 5, 28, 13, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=t1),
            snapshot_builder(run_id="a2", observed_at=None),
            snapshot_builder(run_id="a3", observed_at=None),
            snapshot_builder(run_id="a4", observed_at=t4),
        ]

        normalized = service._normalize_snapshots(snapshots)

        assert normalized[0].observed_at == t1
        assert normalized[1].observed_at == t4  # Forward inference to a4
        assert normalized[2].observed_at == t4  # Forward inference to a4
        assert normalized[3].observed_at == t4

    def test_all_missing_emergency_fallback(self, service, snapshot_builder, caplog):
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=None),
        ]

        with caplog.at_level(logging.WARNING):
            normalized = service._normalize_snapshots(snapshots)

        assert len(normalized) == 2
        assert normalized[0].observed_at is not None
        assert normalized[1].observed_at is not None
        assert normalized[0].observed_at == normalized[1].observed_at
        # Should log warning about emergency fallback
        assert any("observed_at missing" in record.message for record in caplog.records)

    def test_normalization_logs_warning_for_any_missing(self, service, snapshot_builder, caplog):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=t1),
        ]

        with caplog.at_level(logging.WARNING):
            normalized = service._normalize_snapshots(snapshots)

        assert len(normalized) == 2
        assert any("observed_at missing" in record.message for record in caplog.records)


class TestInferTimestamp:
    def test_forward_inference_finds_next_timestamp(self, service, snapshot_builder):
        t2 = datetime(2026, 5, 28, 11, 0, tzinfo=UTC)
        t3 = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=t2),
            snapshot_builder(run_id="a3", observed_at=t3),
        ]

        result = service._infer_timestamp(snapshots[0], 0, snapshots)
        assert result == t2

    def test_backward_inference_when_forward_not_available(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=t1),
            snapshot_builder(run_id="a2", observed_at=None),
        ]

        result = service._infer_timestamp(snapshots[1], 1, snapshots)
        assert result == t1

    def test_emergency_fallback_when_no_timestamps(self, service, snapshot_builder, caplog):
        snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=None),
        ]

        with caplog.at_level(logging.WARNING):
            result = service._infer_timestamp(snapshots[0], 0, snapshots)

        assert result is not None
        assert result.tzinfo == UTC
        assert any("No observed_at timestamps available" in record.message for record in caplog.records)


class TestSnapshotImmutability:
    def test_normalization_does_not_modify_original(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        original_snapshots = [
            snapshot_builder(run_id="a1", observed_at=None),
            snapshot_builder(run_id="a2", observed_at=t1),
        ]

        normalized = service._normalize_snapshots(original_snapshots)

        # Original should be unchanged
        assert original_snapshots[0].observed_at is None
        # Normalized should have the inferred timestamp
        assert normalized[0].observed_at == t1

    def test_normalized_snapshots_are_copies(self, service, snapshot_builder):
        t1 = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
        snapshots = [snapshot_builder(run_id="a1", observed_at=t1)]

        normalized = service._normalize_snapshots(snapshots)

        assert normalized[0] is not snapshots[0]
        assert normalized[0].run_id == snapshots[0].run_id
