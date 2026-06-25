# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for extraction health history tracking (Stage 1: Database Schema)."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from operations_center.observer.collectors.extraction_history_collector import (
    ExtractionHistoryCollector,
)
from operations_center.observer.extraction_health_history import (
    ExtractionHealthSnapshot,
    ExtractionHealthTrend,
    ExtractionHistoryStorage,
    aggregate_edge_cases,
    calculate_trend_slope,
    detect_anomalies,
    snapshots_to_trend,
)


class TestExtractionHealthSnapshot:
    """Tests for ExtractionHealthSnapshot data model."""

    def test_snapshot_creation_basic(self) -> None:
        """Test creating a basic snapshot."""
        now = datetime.now(UTC)
        snapshot = ExtractionHealthSnapshot(
            observed_at=now,
            success_rate=87.5,
            complete_extraction=14,
            partial_extraction=2,
            no_extraction=0,
            total_flaky_tests=16,
        )

        assert snapshot.observed_at == now
        assert snapshot.success_rate == 87.5
        assert snapshot.complete_extraction == 14
        assert snapshot.partial_extraction == 2
        assert snapshot.no_extraction == 0
        assert snapshot.total_flaky_tests == 16
        assert snapshot.extracted_count == 16

    def test_snapshot_extracted_count_auto_calculated(self) -> None:
        """Test that extracted_count is calculated from complete + partial."""
        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=75.0,
            complete_extraction=10,
            partial_extraction=5,
            no_extraction=5,
            total_flaky_tests=20,
        )

        assert snapshot.extracted_count == 15

    def test_snapshot_with_edge_cases(self) -> None:
        """Test snapshot with edge case summary."""
        edge_cases = {
            "truncated_messages": 2,
            "special_chars": 3,
            "malformed_exceptions": 1,
        }
        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=80.0,
            complete_extraction=8,
            partial_extraction=2,
            no_extraction=0,
            total_flaky_tests=10,
            edge_case_summary=edge_cases,
        )

        assert snapshot.edge_case_summary == edge_cases

    def test_snapshot_success_rate_validation(self) -> None:
        """Test that success_rate must be 0-100."""
        with pytest.raises(ValueError):
            ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=105.0,  # Invalid
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )

        with pytest.raises(ValueError):
            ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=-5.0,  # Invalid
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )

    def test_snapshot_negative_counts_validation(self) -> None:
        """Test that extraction counts cannot be negative."""
        with pytest.raises(ValueError):
            ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=80.0,
                complete_extraction=-1,  # Invalid
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )

    def test_snapshot_to_dict(self) -> None:
        """Test serialization to dictionary."""
        now = datetime.now(UTC)
        snapshot = ExtractionHealthSnapshot(
            observed_at=now,
            success_rate=87.5,
            complete_extraction=14,
            partial_extraction=2,
            no_extraction=0,
            total_flaky_tests=16,
            snapshot_id="snap-123",
            collection_run_id="run-001",
        )

        data = snapshot.to_dict()
        assert data["observed_at"] == now.isoformat()
        assert data["success_rate"] == 87.5
        assert data["complete_extraction"] == 14
        assert data["partial_extraction"] == 2
        assert data["no_extraction"] == 0
        assert data["total_flaky_tests"] == 16
        assert data["extracted_count"] == 16
        assert data["snapshot_id"] == "snap-123"
        assert data["collection_run_id"] == "run-001"

    def test_snapshot_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        now = datetime.now(UTC)
        data = {
            "observed_at": now.isoformat(),
            "success_rate": 87.5,
            "complete_extraction": 14,
            "partial_extraction": 2,
            "no_extraction": 0,
            "total_flaky_tests": 16,
            "extracted_count": 16,
            "edge_case_summary": {},
            "snapshot_id": "snap-123",
            "collection_run_id": "run-001",
        }

        snapshot = ExtractionHealthSnapshot.from_dict(data)
        assert snapshot.success_rate == 87.5
        assert snapshot.complete_extraction == 14
        assert snapshot.partial_extraction == 2
        assert snapshot.snapshot_id == "snap-123"

    def test_snapshot_roundtrip_serialization(self) -> None:
        """Test that snapshot survives JSON serialization roundtrip."""
        original = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=85.0,
            complete_extraction=17,
            partial_extraction=3,
            no_extraction=0,
            total_flaky_tests=20,
            edge_case_summary={"truncated": 1},
            snapshot_id="snap-456",
        )

        # Serialize and deserialize
        json_str = json.dumps(original.to_dict())
        data = json.loads(json_str)
        restored = ExtractionHealthSnapshot.from_dict(data)

        assert restored.success_rate == original.success_rate
        assert restored.complete_extraction == original.complete_extraction
        assert restored.partial_extraction == original.partial_extraction
        assert restored.no_extraction == original.no_extraction
        assert restored.total_flaky_tests == original.total_flaky_tests
        assert restored.edge_case_summary == original.edge_case_summary
        assert restored.snapshot_id == original.snapshot_id


class TestExtractionHealthTrend:
    """Tests for ExtractionHealthTrend data model."""

    def test_trend_creation(self) -> None:
        """Test creating a trend object."""
        start = datetime.now(UTC)
        end = start + timedelta(days=1)

        trend = ExtractionHealthTrend(
            period_start=start,
            period_end=end,
            granularity="daily",
            success_rate_mean=85.5,
            success_rate_min=82.0,
            success_rate_max=89.0,
            success_rate_std_dev=2.5,
            success_rate_trend=0.5,
            complete_extraction_mean=14.5,
            partial_extraction_mean=2.0,
            no_extraction_mean=1.0,
            observation_count=10,
        )

        assert trend.granularity == "daily"
        assert trend.success_rate_mean == 85.5
        assert trend.observation_count == 10

    def test_trend_with_anomalies(self) -> None:
        """Test trend with detected anomalies."""
        trend = ExtractionHealthTrend(
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC) + timedelta(days=1),
            granularity="daily",
            success_rate_mean=85.0,
            success_rate_min=80.0,
            success_rate_max=90.0,
            success_rate_std_dev=3.0,
            success_rate_trend=0.2,
            complete_extraction_mean=14.0,
            partial_extraction_mean=2.0,
            no_extraction_mean=1.0,
            observation_count=8,
            anomalies=[
                {
                    "type": "spike_down",
                    "timestamp": "2026-06-19T10:00:00Z",
                    "metric": "success_rate",
                    "delta_pct": -5.2,
                }
            ],
        )

        assert len(trend.anomalies) == 1
        assert trend.anomalies[0]["type"] == "spike_down"

    def test_trend_to_dict(self) -> None:
        """Test serialization to dictionary."""
        start = datetime.now(UTC)
        end = start + timedelta(days=7)

        trend = ExtractionHealthTrend(
            period_start=start,
            period_end=end,
            granularity="weekly",
            success_rate_mean=85.5,
            success_rate_min=82.0,
            success_rate_max=89.0,
            success_rate_std_dev=2.5,
            success_rate_trend=0.5,
            complete_extraction_mean=14.5,
            partial_extraction_mean=2.0,
            no_extraction_mean=1.0,
            observation_count=70,
        )

        data = trend.to_dict()
        assert data["period_start"] == start.isoformat()
        assert data["granularity"] == "weekly"
        assert data["success_rate_mean"] == 85.5
        assert data["observation_count"] == 70

    def test_trend_roundtrip_serialization(self) -> None:
        """Test that trend survives JSON serialization roundtrip."""
        start = datetime.now(UTC)
        end = start + timedelta(days=1)

        original = ExtractionHealthTrend(
            period_start=start,
            period_end=end,
            granularity="daily",
            success_rate_mean=85.5,
            success_rate_min=82.0,
            success_rate_max=89.0,
            success_rate_std_dev=2.5,
            success_rate_trend=0.5,
            complete_extraction_mean=14.5,
            partial_extraction_mean=2.0,
            no_extraction_mean=1.0,
            observation_count=10,
        )

        # Serialize and deserialize
        json_str = json.dumps(original.to_dict())
        data = json.loads(json_str)
        restored = ExtractionHealthTrend.from_dict(data)

        assert restored.success_rate_mean == original.success_rate_mean
        assert restored.success_rate_trend == original.success_rate_trend
        assert restored.observation_count == original.observation_count


class TestExtractionHistoryStorage:
    """Tests for ExtractionHistoryStorage file management."""

    def test_storage_creation_creates_directory(self) -> None:
        """Test that storage creation creates necessary directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)
            assert storage.base_path.exists()
            assert storage.base_path.is_dir()

    def test_save_and_load_single_snapshot(self) -> None:
        """Test saving and loading a single snapshot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Create and save snapshot
            snapshot = ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=87.5,
                complete_extraction=14,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=16,
            )
            storage.save_snapshot(snapshot)

            # Load and verify
            loaded = storage.load_all_snapshots()
            assert len(loaded) == 1
            assert loaded[0].success_rate == 87.5

    def test_save_and_load_multiple_snapshots(self) -> None:
        """Test saving and loading multiple snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Save multiple snapshots
            base_time = datetime.now(UTC)
            for i in range(5):
                snapshot = ExtractionHealthSnapshot(
                    observed_at=base_time + timedelta(hours=i),
                    success_rate=80.0 + i,
                    complete_extraction=10 + i,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12 + i,
                )
                storage.save_snapshot(snapshot)

            # Load and verify
            loaded = storage.load_all_snapshots()
            assert len(loaded) == 5
            assert loaded[0].success_rate == 80.0
            assert loaded[4].success_rate == 84.0

    def test_load_snapshots_range(self) -> None:
        """Test loading snapshots within a date range."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Save snapshots across multiple days
            now = datetime.now(UTC)
            for days_ago in range(10):
                snapshot = ExtractionHealthSnapshot(
                    observed_at=now - timedelta(days=days_ago),
                    success_rate=80.0,
                    complete_extraction=10,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12,
                )
                storage.save_snapshot(snapshot)

            # Load last 5 days
            recent = storage.load_snapshots_range(days=5)
            assert len(recent) == 5  # Days 0-4

    def test_load_snapshots_since(self) -> None:
        """Test loading snapshots since a specific time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Save snapshots
            now = datetime.now(UTC)
            for i in range(5):
                snapshot = ExtractionHealthSnapshot(
                    observed_at=now - timedelta(hours=i),
                    success_rate=80.0,
                    complete_extraction=10,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12,
                )
                storage.save_snapshot(snapshot)

            # Load since 2 hours ago
            cutoff = now - timedelta(hours=2)
            recent = storage.load_snapshots_since(cutoff)
            assert len(recent) == 3  # Hours 0, 1, 2

    def test_cleanup_old_snapshots(self) -> None:
        """Test cleanup of old snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Save snapshots from different dates
            now = datetime.now(UTC)
            old_snapshot = ExtractionHealthSnapshot(
                observed_at=now - timedelta(days=400),  # Older than 365 day retention
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            new_snapshot = ExtractionHealthSnapshot(
                observed_at=now,
                success_rate=85.0,
                complete_extraction=12,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=14,
            )
            storage.save_snapshot(old_snapshot)
            storage.save_snapshot(new_snapshot)

            # Cleanup
            deleted = storage.cleanup_old_snapshots()
            assert deleted == 1

            # Verify only new snapshot remains
            remaining = storage.load_all_snapshots()
            assert len(remaining) == 1
            assert remaining[0].success_rate == 85.0

    def test_jsonl_format_compliance(self) -> None:
        """Test that storage uses proper JSONL format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Save snapshots
            for i in range(3):
                snapshot = ExtractionHealthSnapshot(
                    observed_at=datetime.now(UTC),
                    success_rate=80.0 + i,
                    complete_extraction=10,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12,
                )
                storage.save_snapshot(snapshot)

            # Read file and verify JSONL format
            with open(storage.snapshots_file, "r") as f:
                lines = f.readlines()

            assert len(lines) == 3
            for line in lines:
                # Each line should be valid JSON
                data = json.loads(line)
                assert "observed_at" in data
                assert "success_rate" in data


class TestExtractionHistoryCollector:
    """Tests for ExtractionHistoryCollector."""

    def test_collector_creation(self) -> None:
        """Test creating a collector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = ExtractionHistoryCollector(tmpdir)
            assert collector.storage is not None

    def test_collector_collect_snapshot_basic(self) -> None:
        """Test collecting a basic snapshot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = ExtractionHistoryCollector(tmpdir)

            snapshot = collector.collect_snapshot(
                success_rate=87.5,
                complete_extraction=14,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=16,
            )

            assert snapshot.success_rate == 87.5
            assert snapshot.complete_extraction == 14
            assert snapshot.extracted_count == 16

    def test_collector_collect_snapshot_with_metadata(self) -> None:
        """Test collecting snapshot with metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = ExtractionHistoryCollector(tmpdir)

            snapshot = collector.collect_snapshot(
                success_rate=85.0,
                complete_extraction=12,
                partial_extraction=3,
                no_extraction=1,
                total_flaky_tests=16,
                edge_case_summary={"truncated": 2},
                snapshot_id="snap-789",
                collection_run_id="run-456",
            )

            assert snapshot.snapshot_id == "snap-789"
            assert snapshot.collection_run_id == "run-456"
            assert snapshot.edge_case_summary["truncated"] == 2

    def test_collector_persists_snapshots(self) -> None:
        """Test that collected snapshots are persisted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = ExtractionHistoryCollector(tmpdir)

            # Collect snapshot
            collector.collect_snapshot(
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )

            # Create new collector instance and verify snapshot exists
            new_collector = ExtractionHistoryCollector(tmpdir)
            snapshots = new_collector.storage.load_all_snapshots()
            assert len(snapshots) == 1
            assert snapshots[0].success_rate == 80.0

    def test_collector_multiple_snapshots(self) -> None:
        """Test collecting multiple snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = ExtractionHistoryCollector(tmpdir)

            # Collect multiple snapshots
            for i in range(5):
                collector.collect_snapshot(
                    success_rate=80.0 + i,
                    complete_extraction=10 + i,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12 + i,
                )

            # Verify all stored
            snapshots = collector.storage.load_all_snapshots()
            assert len(snapshots) == 5


class TestIntegrationExtractionHistory:
    """Integration tests for extraction history system."""

    def test_end_to_end_snapshot_collection_and_retrieval(self) -> None:
        """Test complete workflow: collect, store, retrieve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Collect snapshots
            collector = ExtractionHistoryCollector(tmpdir)

            for i in range(3):
                collector.collect_snapshot(
                    success_rate=85.0 + i,
                    complete_extraction=14 + i,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=16 + i,
                    edge_case_summary={"truncated": i},
                    snapshot_id=f"snap-{i}",
                )

            # Retrieve and verify
            storage = ExtractionHistoryStorage.create_local(tmpdir)
            snapshots = storage.load_all_snapshots()

            assert len(snapshots) == 3
            assert snapshots[0].success_rate == 85.0
            assert snapshots[2].success_rate == 87.0
            assert snapshots[1].edge_case_summary["truncated"] == 1

    def test_snapshot_data_preservation_through_json_roundtrip(self) -> None:
        """Test that snapshot data is preserved through JSON serialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            original = ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=82.5,
                complete_extraction=13,
                partial_extraction=3,
                no_extraction=1,
                total_flaky_tests=17,
                edge_case_summary={
                    "truncated_messages": 2,
                    "special_chars": 1,
                },
                snapshot_id="snap-test",
            )

            # Store and retrieve
            storage.save_snapshot(original)
            loaded = storage.load_all_snapshots()

            assert loaded[0].success_rate == original.success_rate
            assert loaded[0].complete_extraction == original.complete_extraction
            assert loaded[0].edge_case_summary == original.edge_case_summary
            assert loaded[0].snapshot_id == original.snapshot_id


class TestTrendCalculation:
    """Tests for trend slope calculation with linear regression."""

    def test_trend_slope_with_stable_data(self) -> None:
        """Test that stable success_rate produces near-zero slope."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,  # Constant
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ]

        result = calculate_trend_slope(snapshots)
        assert result["slope"] == 0.0
        assert result["confidence"] == "stable"
        assert result["r_squared"] == 0.0

    def test_trend_slope_with_improving_data(self) -> None:
        """Test that increasing success_rate produces positive slope."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=80.0 + i * 2.0,  # +2% per hour
                complete_extraction=10 + i,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12 + i,
            )
            for i in range(5)
        ]

        result = calculate_trend_slope(snapshots)
        assert result["slope"] > 0
        assert result["confidence"] == "improving"
        assert 0 <= result["r_squared"] <= 1

    def test_trend_slope_with_degrading_data(self) -> None:
        """Test that decreasing success_rate produces negative slope."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=95.0 - i * 2.0,  # -2% per hour
                complete_extraction=15 - i,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=17 - i,
            )
            for i in range(5)
        ]

        result = calculate_trend_slope(snapshots)
        assert result["slope"] < 0
        assert result["confidence"] == "degrading"
        assert 0 <= result["r_squared"] <= 1

    def test_trend_slope_with_single_snapshot(self) -> None:
        """Test that single snapshot returns uncertain with zero slope."""
        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=85.0,
            complete_extraction=10,
            partial_extraction=2,
            no_extraction=0,
            total_flaky_tests=12,
        )

        result = calculate_trend_slope([snapshot])
        assert result["slope"] == 0.0
        assert result["confidence"] == "uncertain"
        assert result["r_squared"] == 0.0

    def test_trend_slope_with_two_snapshots(self) -> None:
        """Test trend slope calculation with minimum data points."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now,
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            ),
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=1),
                success_rate=81.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            ),
        ]

        result = calculate_trend_slope(snapshots)
        assert result["slope"] > 0  # Should show improvement
        assert result["r_squared"] == 1.0  # Perfect fit for 2 points

    def test_trend_slope_r_squared_quality(self) -> None:
        """Test that R-squared reflects model fit quality."""
        now = datetime.now(UTC)

        # Create snapshots with perfect linear trend
        perfect_snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=80.0 + i * 0.5,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(10)
        ]

        perfect_result = calculate_trend_slope(perfect_snapshots)
        assert perfect_result["r_squared"] > 0.99  # Very high fit

    def test_trend_slope_noisy_data(self) -> None:
        """Test trend slope with noisy data."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=85.0 + (i % 3 - 1),  # Oscillates slightly
                complete_extraction=10 + (i % 2),
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12 + (i % 2),
            )
            for i in range(10)
        ]

        result = calculate_trend_slope(snapshots)
        assert abs(result["slope"]) < 0.5  # Should be small slope (days not hours)
        assert result["confidence"] in ("stable", "improving", "degrading")


class TestAnomalyDetection:
    """Tests for anomaly detection in extraction metrics."""

    def test_detect_anomalies_with_spike_down(self) -> None:
        """Test detection of success_rate drops."""
        now = datetime.now(UTC)
        # Need at least 10 snapshots for window_size=5 (requires len >= window_size * 2)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ] + [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=5 + i),
                success_rate=79.0 if i == 0 else 85.0,  # Drop of 6% at index 5
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)
        assert len(anomalies) > 0
        assert anomalies[0]["type"] == "spike_down"
        assert anomalies[0]["delta_pct"] < -5.0

    def test_detect_anomalies_with_spike_up(self) -> None:
        """Test detection of success_rate spikes."""
        now = datetime.now(UTC)
        # Need at least 10 snapshots for window_size=5 (requires len >= window_size * 2)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=70.0,
                complete_extraction=8,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(5)
        ] + [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=5 + i),
                success_rate=76.0 if i == 0 else 70.0,  # Spike of 6% at index 5
                complete_extraction=8,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(5)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)
        assert len(anomalies) > 0
        assert anomalies[0]["type"] == "spike_up"
        assert anomalies[0]["delta_pct"] > 5.0

    def test_detect_anomalies_no_anomalies(self) -> None:
        """Test that stable data produces no anomalies."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(20)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)
        assert len(anomalies) == 0

    def test_detect_anomalies_insufficient_data(self) -> None:
        """Test that insufficient data returns empty anomalies."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ]

        # With window_size=5, need at least 10 points
        anomalies = detect_anomalies(snapshots, window_size=5)
        assert len(anomalies) == 0

    def test_detect_anomalies_custom_window_size(self) -> None:
        """Test anomaly detection with custom window size."""
        now = datetime.now(UTC)
        # Need at least window_size * 2 snapshots (6 for window_size=3)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(3)
        ] + [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=3 + i),
                success_rate=78.0 if i == 0 else 85.0,  # Drop of 7% at index 3
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(3)
        ]

        # With window_size=3, should detect anomaly
        anomalies = detect_anomalies(snapshots, window_size=3)
        assert len(anomalies) > 0

    def test_detect_anomalies_anomaly_fields(self) -> None:
        """Test that anomaly records contain all required fields."""
        now = datetime.now(UTC)
        # Need at least 10 snapshots for window_size=5
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ] + [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=5 + i),
                success_rate=78.0 if i == 0 else 85.0,  # Anomaly at index 5
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)
        assert len(anomalies) > 0

        anomaly = anomalies[0]
        assert "type" in anomaly
        assert "timestamp" in anomaly
        assert "metric" in anomaly
        assert "delta_pct" in anomaly
        assert "previous_avg" in anomaly
        assert "current_value" in anomaly


class TestEdgeCaseAggregation:
    """Tests for aggregating edge case metrics across snapshots."""

    def test_aggregate_edge_cases_single_type(self) -> None:
        """Test aggregating a single edge case type."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={"truncated_messages": i + 1},
            )
            for i in range(3)
        ]

        result = aggregate_edge_cases(snapshots)
        assert "truncated_messages" in result
        assert result["truncated_messages"]["mean"] == 2.0
        assert result["truncated_messages"]["min"] == 1.0
        assert result["truncated_messages"]["max"] == 3.0

    def test_aggregate_edge_cases_multiple_types(self) -> None:
        """Test aggregating multiple edge case types."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={
                    "truncated_messages": i + 1,
                    "special_chars": i * 2,
                },
            )
            for i in range(3)
        ]

        result = aggregate_edge_cases(snapshots)
        assert len(result) == 2
        assert "truncated_messages" in result
        assert "special_chars" in result
        assert result["special_chars"]["mean"] == 2.0

    def test_aggregate_edge_cases_empty_snapshot_cases(self) -> None:
        """Test aggregating when some snapshots have no edge cases."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now,
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={"truncated_messages": 5},
            ),
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=1),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={},  # No edge cases
            ),
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=2),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={"truncated_messages": 3},
            ),
        ]

        result = aggregate_edge_cases(snapshots)
        assert result["truncated_messages"]["mean"] == 2.67  # (5 + 0 + 3) / 3
        assert result["truncated_messages"]["min"] == 0.0

    def test_aggregate_edge_cases_no_snapshots(self) -> None:
        """Test aggregating with no snapshots."""
        result = aggregate_edge_cases([])
        assert result == {}

    def test_aggregate_edge_cases_no_edge_cases_anywhere(self) -> None:
        """Test aggregating when no snapshots have edge cases."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={},
            )
            for i in range(3)
        ]

        result = aggregate_edge_cases(snapshots)
        assert result == {}


class TestSnapshotsToTrend:
    """Tests for main trend aggregation function."""

    def test_snapshots_to_trend_basic(self) -> None:
        """Test basic trend creation from snapshots."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0 + i,
                complete_extraction=10 + i,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12 + i,
            )
            for i in range(5)
        ]

        trend = snapshots_to_trend(snapshots)

        assert trend.period_start == snapshots[0].observed_at
        assert trend.period_end == snapshots[-1].observed_at
        assert trend.granularity == "daily"
        assert trend.success_rate_mean > 0
        assert trend.observation_count == 5

    def test_snapshots_to_trend_with_granularity(self) -> None:
        """Test trend creation with specified granularity."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(7)
        ]

        trend = snapshots_to_trend(snapshots, granularity="weekly")
        assert trend.granularity == "weekly"

    def test_snapshots_to_trend_statistics_calculation(self) -> None:
        """Test that statistics are correctly calculated."""
        now = datetime.now(UTC)
        # Create snapshots with known values
        success_rates = [80.0, 85.0, 90.0, 85.0, 80.0]
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=sr,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i, sr in enumerate(success_rates)
        ]

        trend = snapshots_to_trend(snapshots)

        # Mean should be 84.0 (sum=420, count=5, 420/5=84)
        assert trend.success_rate_mean == 84.0
        # Min should be 80.0, max should be 90.0
        assert trend.success_rate_min == 80.0
        assert trend.success_rate_max == 90.0

    def test_snapshots_to_trend_with_anomalies(self) -> None:
        """Test trend creation with anomalies detected."""
        now = datetime.now(UTC)
        # Need at least 10 snapshots for default window_size=5
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ] + [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=5 + i),
                success_rate=78.0 if i == 0 else 85.0,  # Anomaly at index 5
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            for i in range(5)
        ]

        trend = snapshots_to_trend(snapshots)
        assert len(trend.anomalies) > 0

    def test_snapshots_to_trend_with_edge_cases(self) -> None:
        """Test trend creation with edge case aggregation."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
                edge_case_summary={"truncated": i + 1, "special_chars": i},
            )
            for i in range(3)
        ]

        trend = snapshots_to_trend(snapshots)
        assert len(trend.edge_case_trends) > 0
        assert "truncated" in trend.edge_case_trends
        assert "special_chars" in trend.edge_case_trends

    def test_snapshots_to_trend_empty_raises_error(self) -> None:
        """Test that empty snapshots list raises ValueError."""
        with pytest.raises(ValueError):
            snapshots_to_trend([])

    def test_snapshots_to_trend_single_snapshot(self) -> None:
        """Test trend creation with single snapshot."""
        now = datetime.now(UTC)
        snapshot = ExtractionHealthSnapshot(
            observed_at=now,
            success_rate=85.0,
            complete_extraction=10,
            partial_extraction=2,
            no_extraction=0,
            total_flaky_tests=12,
        )

        trend = snapshots_to_trend([snapshot])

        assert trend.success_rate_mean == 85.0
        assert trend.success_rate_min == 85.0
        assert trend.success_rate_max == 85.0
        assert trend.observation_count == 1


class TestHistoryTrackingEdgeCases:
    """Tests for edge cases in history tracking system."""

    def test_empty_history_retrieval(self) -> None:
        """Test loading from empty history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)
            snapshots = storage.load_all_snapshots()
            assert snapshots == []

    def test_load_snapshots_range_empty_result(self) -> None:
        """Test loading range when no snapshots exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)
            recent = storage.load_snapshots_range(days=7)
            assert recent == []

    def test_single_data_point_trend(self) -> None:
        """Test creating trend from single snapshot."""
        now = datetime.now(UTC)
        snapshot = ExtractionHealthSnapshot(
            observed_at=now,
            success_rate=85.0,
            complete_extraction=10,
            partial_extraction=2,
            no_extraction=0,
            total_flaky_tests=12,
        )

        trend = snapshots_to_trend([snapshot])

        assert trend.observation_count == 1
        assert trend.success_rate_mean == 85.0
        assert trend.success_rate_trend == 0.0
        assert trend.success_rate_std_dev == 0.0

    def test_handling_malformed_jsonl_line(self) -> None:
        """Test graceful handling of malformed JSON in JSONL file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)

            # Save a good snapshot
            snapshot1 = ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=85.0,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12,
            )
            storage.save_snapshot(snapshot1)

            # Manually write malformed JSON
            with open(storage.snapshots_file, "a", encoding="utf-8") as f:
                f.write("not valid json\n")

            # Save another good snapshot
            snapshot2 = ExtractionHealthSnapshot(
                observed_at=datetime.now(UTC),
                success_rate=86.0,
                complete_extraction=11,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=13,
            )
            storage.save_snapshot(snapshot2)

            # Should load the two good snapshots, skip the bad one
            snapshots = storage.load_all_snapshots()
            assert len(snapshots) == 2
            assert snapshots[0].success_rate == 85.0
            assert snapshots[1].success_rate == 86.0

    def test_very_large_snapshot_set(self) -> None:
        """Test handling large number of snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ExtractionHistoryStorage.create_local(tmpdir)
            collector = ExtractionHistoryCollector(tmpdir)

            # Save 1000 snapshots
            for i in range(1000):
                collector.collect_snapshot(
                    success_rate=80.0 + (i % 20),
                    complete_extraction=10 + (i % 5),
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12 + (i % 5),
                )

            # Load and verify
            snapshots = storage.load_all_snapshots()
            assert len(snapshots) == 1000

            # Trend calculation should still work
            trend = snapshots_to_trend(snapshots[:100])
            assert trend.observation_count == 100

    def test_concurrent_collector_writes(self) -> None:
        """Test that multiple collectors can write to same storage safely."""
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            results: list[int] = []

            def collector_thread(thread_id: int) -> None:
                collector = ExtractionHistoryCollector(tmpdir)
                for i in range(10):
                    collector.collect_snapshot(
                        success_rate=80.0 + thread_id,
                        complete_extraction=10,
                        partial_extraction=2,
                        no_extraction=0,
                        total_flaky_tests=12,
                    )
                results.append(thread_id)

            # Run 5 threads concurrently
            threads = []
            for tid in range(5):
                t = threading.Thread(target=collector_thread, args=(tid,))
                threads.append(t)
                t.start()

            # Wait for all threads
            for t in threads:
                t.join()

            # Should have all threads completed
            assert len(results) == 5

            # Check that all snapshots were persisted
            storage = ExtractionHistoryStorage.create_local(tmpdir)
            snapshots = storage.load_all_snapshots()
            assert len(snapshots) == 50  # 5 threads * 10 snapshots

    def test_snapshot_with_zero_total_flaky_tests(self) -> None:
        """Test handling snapshot with no flaky tests."""
        now = datetime.now(UTC)
        snapshot = ExtractionHealthSnapshot(
            observed_at=now,
            success_rate=100.0,  # Perfect because no tests failed
            complete_extraction=0,
            partial_extraction=0,
            no_extraction=0,
            total_flaky_tests=0,  # No failures
        )

        storage = ExtractionHistoryStorage.create_local(tempfile.mkdtemp())
        storage.save_snapshot(snapshot)
        loaded = storage.load_all_snapshots()

        assert loaded[0].total_flaky_tests == 0
        assert loaded[0].success_rate == 100.0

    def test_trend_with_boundary_success_rates(self) -> None:
        """Test trend calculation with boundary values (0% and 100%)."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now,
                success_rate=0.0,  # Worst case
                complete_extraction=0,
                partial_extraction=0,
                no_extraction=10,
                total_flaky_tests=10,
            ),
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=1),
                success_rate=100.0,  # Best case
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            ),
        ]

        trend = snapshots_to_trend(snapshots)
        assert trend.success_rate_min == 0.0
        assert trend.success_rate_max == 100.0
        assert trend.success_rate_mean == 50.0


class TestSnapshotMessageQualityRate:
    """Tests for message_quality_rate storage in ExtractionHealthSnapshot (Stage 3)."""

    def _make_snapshot(self, message_quality_rate: float | None = None) -> ExtractionHealthSnapshot:
        return ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=80.0,
            complete_extraction=4,
            partial_extraction=0,
            no_extraction=1,
            total_flaky_tests=5,
            message_quality_rate=message_quality_rate,
        )

    def test_snapshot_accepts_message_quality_rate(self) -> None:
        """ExtractionHealthSnapshot accepts message_quality_rate as a field."""
        snap = self._make_snapshot(message_quality_rate=85.0)
        assert snap.message_quality_rate == 85.0

    def test_snapshot_message_quality_rate_defaults_to_none(self) -> None:
        """message_quality_rate defaults to None when not supplied."""
        snap = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=80.0,
            complete_extraction=4,
            partial_extraction=0,
            no_extraction=1,
            total_flaky_tests=5,
        )
        assert snap.message_quality_rate is None

    def test_to_dict_includes_message_quality_rate_value(self) -> None:
        """to_dict() serialises message_quality_rate when it has a value."""
        snap = self._make_snapshot(message_quality_rate=75.5)
        d = snap.to_dict()
        assert "message_quality_rate" in d
        assert d["message_quality_rate"] == 75.5

    def test_to_dict_includes_message_quality_rate_null(self) -> None:
        """to_dict() serialises message_quality_rate as None when unset."""
        snap = self._make_snapshot(message_quality_rate=None)
        d = snap.to_dict()
        assert "message_quality_rate" in d
        assert d["message_quality_rate"] is None

    def test_from_dict_loads_message_quality_rate(self) -> None:
        """from_dict() restores message_quality_rate from serialised form."""
        now = datetime.now(UTC)
        data = {
            "observed_at": now.isoformat(),
            "success_rate": 80.0,
            "complete_extraction": 4,
            "partial_extraction": 0,
            "no_extraction": 1,
            "total_flaky_tests": 5,
            "message_quality_rate": 66.7,
        }
        snap = ExtractionHealthSnapshot.from_dict(data)
        assert snap.message_quality_rate == 66.7

    def test_from_dict_null_quality_rate_stays_none(self) -> None:
        """from_dict() with null message_quality_rate gives None."""
        now = datetime.now(UTC)
        data = {
            "observed_at": now.isoformat(),
            "success_rate": 80.0,
            "complete_extraction": 4,
            "partial_extraction": 0,
            "no_extraction": 1,
            "total_flaky_tests": 5,
            "message_quality_rate": None,
        }
        snap = ExtractionHealthSnapshot.from_dict(data)
        assert snap.message_quality_rate is None

    def test_from_dict_missing_quality_rate_key_defaults_none(self) -> None:
        """Old JSONL rows without message_quality_rate load with None (backwards compat)."""
        now = datetime.now(UTC)
        data = {
            "observed_at": now.isoformat(),
            "success_rate": 80.0,
            "complete_extraction": 4,
            "partial_extraction": 0,
            "no_extraction": 1,
            "total_flaky_tests": 5,
            # no message_quality_rate key
        }
        snap = ExtractionHealthSnapshot.from_dict(data)
        assert snap.message_quality_rate is None

    def test_storage_round_trip_preserves_quality_rate(self, tmp_path: any) -> None:
        """Saving and reloading a snapshot preserves message_quality_rate."""
        storage = ExtractionHistoryStorage(tmp_path / "hist")
        snap = self._make_snapshot(message_quality_rate=92.3)
        storage.save_snapshot(snap)
        loaded = storage.load_all_snapshots()
        assert len(loaded) == 1
        assert loaded[0].message_quality_rate == 92.3

    def test_collector_collect_snapshot_accepts_quality_rate(self, tmp_path: any) -> None:
        """ExtractionHistoryCollector.collect_snapshot() stores message_quality_rate."""
        collector = ExtractionHistoryCollector(tmp_path / "hist")
        snap = collector.collect_snapshot(
            success_rate=80.0,
            complete_extraction=4,
            partial_extraction=0,
            no_extraction=1,
            total_flaky_tests=5,
            message_quality_rate=88.0,
        )
        assert snap.message_quality_rate == 88.0
        loaded = collector.storage.load_all_snapshots()
        assert loaded[0].message_quality_rate == 88.0

    def test_collector_collect_snapshot_quality_rate_none(self, tmp_path: any) -> None:
        """collect_snapshot() persists message_quality_rate=None correctly."""
        collector = ExtractionHistoryCollector(tmp_path / "hist")
        snap = collector.collect_snapshot(
            success_rate=0.0,
            complete_extraction=0,
            partial_extraction=0,
            no_extraction=3,
            total_flaky_tests=3,
            message_quality_rate=None,
        )
        assert snap.message_quality_rate is None
        loaded = collector.storage.load_all_snapshots()
        assert loaded[0].message_quality_rate is None
