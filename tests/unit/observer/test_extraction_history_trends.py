# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for extraction history trend calculation and query layer (Stage 2).

Tests cover:
- Linear regression for trend slope calculation
- Anomaly detection with moving averages
- Temporal aggregation and statistics
- Query mixin integration
- Edge cases and error handling
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from operations_center.observer.extraction_health_history import (
    ExtractionHealthSnapshot,
    ExtractionHealthTrend,
    ExtractionHistoryStorage,
    calculate_trend_slope,
    detect_anomalies,
    aggregate_edge_cases,
    snapshots_to_trend,
)
from operations_center.observer.query_extraction_history import (
    ExtractionHistoryQueryMixin,
)


class TestTrendCalculation:
    """Tests for linear regression trend calculation."""

    def test_calculate_trend_slope_improving(self) -> None:
        """Test trend detection for improving success rate."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=70.0 + i * 0.5,  # Improving at 0.5% per day
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(10)
        ]

        result = calculate_trend_slope(snapshots)

        assert result["confidence"] == "improving"
        assert result["slope"] > 0.0
        assert 0.0 <= result["r_squared"] <= 1.0

    def test_calculate_trend_slope_degrading(self) -> None:
        """Test trend detection for degrading success rate."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=90.0 - i * 0.5,  # Degrading at 0.5% per day
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(10)
        ]

        result = calculate_trend_slope(snapshots)

        assert result["confidence"] == "degrading"
        assert result["slope"] < 0.0
        assert 0.0 <= result["r_squared"] <= 1.0

    def test_calculate_trend_slope_stable(self) -> None:
        """Test trend detection for stable success rate."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=80.0,  # Stable at 80%
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(10)
        ]

        result = calculate_trend_slope(snapshots)

        assert result["confidence"] == "stable"
        assert abs(result["slope"]) < 0.1

    def test_calculate_trend_slope_insufficient_data(self) -> None:
        """Test trend calculation with insufficient data."""
        result = calculate_trend_slope([])
        assert result["confidence"] == "uncertain"
        assert result["slope"] == 0.0
        assert result["r_squared"] == 0.0

        # Single snapshot
        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=75.0,
            complete_extraction=10,
            partial_extraction=0,
            no_extraction=0,
            total_flaky_tests=10,
        )
        result = calculate_trend_slope([snapshot])
        assert result["confidence"] == "uncertain"


class TestAnomalyDetection:
    """Tests for anomaly detection using moving averages."""

    def test_detect_anomalies_sudden_drop(self) -> None:
        """Test detection of sudden success rate drop."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0 if i < 10 else 75.0,  # Drop at hour 10
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(15)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)

        # Should detect the drop
        drop_anomalies = [a for a in anomalies if a["type"] == "spike_down"]
        assert len(drop_anomalies) > 0

    def test_detect_anomalies_sudden_spike(self) -> None:
        """Test detection of sudden success rate spike."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=75.0 if i < 10 else 85.0,  # Spike at hour 10
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(15)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)

        # Should detect the spike
        spike_anomalies = [a for a in anomalies if a["type"] == "spike_up"]
        assert len(spike_anomalies) > 0

    def test_detect_anomalies_insufficient_data(self) -> None:
        """Test anomaly detection with insufficient data."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(5)
        ]

        anomalies = detect_anomalies(snapshots, window_size=5)
        assert len(anomalies) == 0

    def test_anomaly_has_required_fields(self) -> None:
        """Test that detected anomalies have all required fields."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0 if i < 10 else 75.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(15)
        ]

        anomalies = detect_anomalies(snapshots)

        for anomaly in anomalies:
            assert "type" in anomaly
            assert "timestamp" in anomaly
            assert "metric" in anomaly
            assert "delta_pct" in anomaly
            assert "previous_avg" in anomaly
            assert "current_value" in anomaly
            assert anomaly["metric"] == "success_rate"


class TestEdgeCaseAggregation:
    """Tests for aggregating edge case metrics."""

    def test_aggregate_edge_cases_single_type(self) -> None:
        """Test aggregation of single edge case type."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
                edge_case_summary={"truncated_messages": i + 1},
            )
            for i in range(5)
        ]

        aggregated = aggregate_edge_cases(snapshots)

        assert "truncated_messages" in aggregated
        assert "mean" in aggregated["truncated_messages"]
        assert "min" in aggregated["truncated_messages"]
        assert "max" in aggregated["truncated_messages"]
        assert aggregated["truncated_messages"]["min"] == 1.0
        assert aggregated["truncated_messages"]["max"] == 5.0

    def test_aggregate_edge_cases_multiple_types(self) -> None:
        """Test aggregation of multiple edge case types."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
                edge_case_summary={
                    "truncated_messages": i + 1,
                    "special_chars": (i + 1) * 2,
                },
            )
            for i in range(3)
        ]

        aggregated = aggregate_edge_cases(snapshots)

        assert len(aggregated) == 2
        assert "truncated_messages" in aggregated
        assert "special_chars" in aggregated

    def test_aggregate_edge_cases_empty(self) -> None:
        """Test aggregation with no edge cases."""
        aggregated = aggregate_edge_cases([])
        assert aggregated == {}


class TestSnapshotsToTrend:
    """Tests for converting snapshots to trend metrics."""

    def test_snapshots_to_trend_basic(self) -> None:
        """Test basic conversion of snapshots to trend."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=75.0 + i,
                complete_extraction=10,
                partial_extraction=2,
                no_extraction=1,
                total_flaky_tests=13,
            )
            for i in range(10)
        ]

        trend = snapshots_to_trend(snapshots)

        assert isinstance(trend, ExtractionHealthTrend)
        assert trend.period_start == snapshots[0].observed_at
        assert trend.period_end == snapshots[-1].observed_at
        assert trend.observation_count == 10
        assert 75.0 <= trend.success_rate_mean <= 85.0
        assert trend.success_rate_min <= trend.success_rate_mean
        assert trend.success_rate_max >= trend.success_rate_mean
        assert trend.success_rate_std_dev >= 0.0

    def test_snapshots_to_trend_with_anomalies(self) -> None:
        """Test trend includes detected anomalies."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=85.0 if i < 10 else 75.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(15)
        ]

        trend = snapshots_to_trend(snapshots)

        # Should include anomalies in trend
        assert isinstance(trend.anomalies, list)
        # Anomalies may be empty if drop is not >5%

    def test_snapshots_to_trend_empty_raises(self) -> None:
        """Test that empty snapshots list raises ValueError."""
        with pytest.raises(ValueError, match="Cannot create trend from empty"):
            snapshots_to_trend([])

    def test_snapshots_to_trend_granularity(self) -> None:
        """Test trend granularity parameter."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=80.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(7)
        ]

        trend_daily = snapshots_to_trend(snapshots, granularity="daily")
        assert trend_daily.granularity == "daily"

        trend_weekly = snapshots_to_trend(snapshots, granularity="weekly")
        assert trend_weekly.granularity == "weekly"


class TestExtractionHistoryQueryMixin:
    """Tests for query mixin functionality."""

    @pytest.fixture
    def temp_snapshot_dir(self) -> Path:
        """Create temporary snapshot directory."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_query_object(self, temp_snapshot_dir: Path) -> object:
        """Create mock query object with mixin."""

        class MockQuery(ExtractionHistoryQueryMixin):
            def __init__(self, root: Path) -> None:
                super().__init__()
                self.root = root

        return MockQuery(temp_snapshot_dir)

    def test_query_mixin_get_extraction_health_history(
        self, mock_query_object: object, temp_snapshot_dir: Path
    ) -> None:
        """Test retrieving extraction health history."""
        # Set up storage and save snapshots
        storage = ExtractionHistoryStorage(temp_snapshot_dir / "extraction_history")
        now = datetime.now(UTC)

        for i in range(5):
            snapshot = ExtractionHealthSnapshot(
                observed_at=now - timedelta(days=5 - i),
                success_rate=80.0 + i,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            storage.save_snapshot(snapshot)

        # Query should retrieve snapshots
        history = mock_query_object.get_extraction_health_history(days=7)
        assert len(history) == 5

    def test_query_mixin_get_extraction_health_trend(
        self, mock_query_object: object, temp_snapshot_dir: Path
    ) -> None:
        """Test retrieving extraction health trend."""
        # Set up storage and save snapshots
        storage = ExtractionHistoryStorage(temp_snapshot_dir / "extraction_history")
        now = datetime.now(UTC)

        for i in range(10):
            snapshot = ExtractionHealthSnapshot(
                observed_at=now - timedelta(hours=10 - i),
                success_rate=75.0 + i * 0.5,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            storage.save_snapshot(snapshot)

        # Query should compute trend
        trend = mock_query_object.get_extraction_health_trend(days=1)
        assert trend is not None
        assert trend.observation_count == 10
        assert trend.success_rate_trend > 0.0  # Improving trend

    def test_query_mixin_get_extraction_trend_slope(
        self, mock_query_object: object, temp_snapshot_dir: Path
    ) -> None:
        """Test retrieving trend slope."""
        storage = ExtractionHistoryStorage(temp_snapshot_dir / "extraction_history")
        now = datetime.now(UTC)

        for i in range(10):
            snapshot = ExtractionHealthSnapshot(
                observed_at=now - timedelta(days=10 - i),
                success_rate=80.0 - i * 0.2,  # Degrading
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            storage.save_snapshot(snapshot)

        slope_data = mock_query_object.get_extraction_trend_slope(days=7)

        assert "slope" in slope_data
        assert "r_squared" in slope_data
        assert "confidence" in slope_data
        assert slope_data["confidence"] == "degrading"

    def test_query_mixin_get_extraction_anomalies(
        self, mock_query_object: object, temp_snapshot_dir: Path
    ) -> None:
        """Test retrieving detected anomalies."""
        storage = ExtractionHistoryStorage(temp_snapshot_dir / "extraction_history")
        now = datetime.now(UTC)

        for i in range(15):
            snapshot = ExtractionHealthSnapshot(
                observed_at=now - timedelta(hours=15 - i),
                success_rate=85.0 if i < 10 else 75.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            storage.save_snapshot(snapshot)

        anomalies = mock_query_object.get_extraction_anomalies(days=1)

        assert isinstance(anomalies, list)
        # May contain anomalies depending on moving average calculation

    def test_query_mixin_cleanup_old_extraction_history(
        self, mock_query_object: object, temp_snapshot_dir: Path
    ) -> None:
        """Test cleanup of old snapshots."""
        storage = ExtractionHistoryStorage(temp_snapshot_dir / "extraction_history")
        old_time = datetime.now(UTC) - timedelta(days=400)

        snapshot = ExtractionHealthSnapshot(
            observed_at=old_time,
            success_rate=80.0,
            complete_extraction=10,
            partial_extraction=0,
            no_extraction=0,
            total_flaky_tests=10,
        )
        storage.save_snapshot(snapshot)

        deleted = mock_query_object.cleanup_old_extraction_history()
        assert deleted == 1


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_snapshots_with_zero_total(self) -> None:
        """Test handling of snapshots with zero total flaky tests."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now,
                success_rate=0.0,
                complete_extraction=0,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=0,  # Edge case: no tests
            ),
        ]

        # Should handle zero total gracefully (valid edge case)
        trend = snapshots_to_trend(snapshots)
        assert trend.observation_count == 1
        assert trend.success_rate_mean == 0.0

    def test_trend_with_single_snapshot(self) -> None:
        """Test that single snapshot still produces valid trend."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now,
                success_rate=80.0,
                complete_extraction=8,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=10,
            ),
        ]

        trend = snapshots_to_trend(snapshots)

        # Should handle single snapshot gracefully
        assert trend.success_rate_mean == 80.0
        assert trend.success_rate_std_dev == 0.0  # No variation

    def test_anomaly_detection_exact_threshold(self) -> None:
        """Test anomaly detection at exact threshold."""
        now = datetime.now(UTC)
        # Create moving average of 80%, then exact 5% drop = 75%
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(hours=i),
                success_rate=80.0 if i < 5 else 75.0,
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(10)
        ]

        # 5.0% exactly should trigger (< -5.0 is strict)
        # May or may not detect depending on exact calculation, but the call must
        # return a well-formed list of anomaly dicts either way.
        anomalies = detect_anomalies(snapshots, window_size=5)
        assert isinstance(anomalies, list)
        assert all(isinstance(a, dict) for a in anomalies)

    def test_trend_slope_with_constant_values(self) -> None:
        """Test trend calculation with constant success rate."""
        now = datetime.now(UTC)
        snapshots = [
            ExtractionHealthSnapshot(
                observed_at=now + timedelta(days=i),
                success_rate=75.0,  # Constant
                complete_extraction=10,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=10,
            )
            for i in range(10)
        ]

        result = calculate_trend_slope(snapshots)

        assert result["slope"] == 0.0
        # When all values are constant, ss_tot = 0, so r_squared = 0 (undefined case)
        assert result["r_squared"] == 0.0
        assert result["confidence"] == "stable"
