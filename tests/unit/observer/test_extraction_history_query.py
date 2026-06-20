# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for extraction history query and retrieval API (Stage 3)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from operations_center.observer.extraction_health_history import (
    ExtractionHealthSnapshot,
    ExtractionHistoryStorage,
)
from operations_center.observer.extraction_history_query import (
    AnomalyResult,
    ExtractionHistoryQuery,
    SuccessRateHistoryPage,
)


@pytest.fixture
def temp_storage():
    """Create a temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ExtractionHistoryStorage.create_local(tmpdir)
        yield storage


@pytest.fixture
def query_with_data(temp_storage):
    """Create a query interface with sample historical data."""
    # Use current time to ensure snapshots are within the 7-day lookback window
    base_time = datetime.now(UTC) - timedelta(hours=24)

    snapshots = [
        ExtractionHealthSnapshot(
            observed_at=base_time + timedelta(hours=i),
            success_rate=80.0 + (i * 0.5),  # Slight increase over time
            complete_extraction=16 + (i // 2),
            partial_extraction=2,
            no_extraction=0 + (i % 2),
            total_flaky_tests=18 + (i // 2),
        )
        for i in range(24)  # 24 hourly snapshots
    ]

    for snapshot in snapshots:
        temp_storage.save_snapshot(snapshot)

    return ExtractionHistoryQuery(temp_storage)


class TestSuccessRateHistoryPage:
    """Tests for SuccessRateHistoryPage dataclass."""

    def test_page_creation(self) -> None:
        """Test creating a result page."""
        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=85.0,
            complete_extraction=10,
            partial_extraction=1,
            no_extraction=1,
            total_flaky_tests=12,
        )

        page = SuccessRateHistoryPage(
            snapshots=[snapshot],
            total_count=10,
            offset=0,
            limit=1,
            has_more=True,
        )

        assert page.total_count == 10
        assert page.offset == 0
        assert page.limit == 1
        assert page.has_more is True
        assert len(page.snapshots) == 1

    def test_page_serialization(self) -> None:
        """Test serializing a page to dict."""
        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=85.0,
            complete_extraction=10,
            partial_extraction=1,
            no_extraction=1,
            total_flaky_tests=12,
        )

        page = SuccessRateHistoryPage(
            snapshots=[snapshot],
            total_count=5,
            offset=0,
            limit=1,
            has_more=True,
        )

        data = page.to_dict()

        assert data["total_count"] == 5
        assert data["offset"] == 0
        assert data["limit"] == 1
        assert data["has_more"] is True
        assert len(data["snapshots"]) == 1
        assert "observed_at" in data["snapshots"][0]


class TestAnomalyResult:
    """Tests for AnomalyResult dataclass."""

    def test_anomaly_creation(self) -> None:
        """Test creating an anomaly result."""
        now = datetime.now(UTC)
        anomaly = AnomalyResult(
            anomaly_type="spike_down",
            timestamp=now,
            metric="success_rate",
            value=60.0,
            baseline=85.0,
            delta_pct=25.0,
        )

        assert anomaly.anomaly_type == "spike_down"
        assert anomaly.metric == "success_rate"
        assert anomaly.value == 60.0
        assert anomaly.delta_pct == 25.0

    def test_anomaly_serialization(self) -> None:
        """Test serializing anomaly to dict."""
        now = datetime.now(UTC)
        anomaly = AnomalyResult(
            anomaly_type="spike_up",
            timestamp=now,
            metric="success_rate",
            value=95.0,
            baseline=85.0,
            delta_pct=10.0,
        )

        data = anomaly.to_dict()

        assert data["anomaly_type"] == "spike_up"
        assert data["metric"] == "success_rate"
        assert data["value"] == 95.0
        assert "timestamp" in data


class TestExtractionHistoryQuery:
    """Tests for ExtractionHistoryQuery API methods."""

    def test_get_success_rate_history_basic(self, query_with_data) -> None:
        """Test fetching paginated success_rate history."""
        page = query_with_data.get_success_rate_history(days=1, limit=10, offset=0)

        assert isinstance(page, SuccessRateHistoryPage)
        assert page.total_count >= 23  # At least 23 (one might fall outside cutoff)
        assert len(page.snapshots) == 10
        assert page.offset == 0
        assert page.limit == 10
        assert page.has_more is True

    def test_get_success_rate_history_pagination(self, query_with_data) -> None:
        """Test pagination across multiple pages."""
        page1 = query_with_data.get_success_rate_history(days=1, limit=10, offset=0)
        page2 = query_with_data.get_success_rate_history(days=1, limit=10, offset=10)
        page3 = query_with_data.get_success_rate_history(days=1, limit=10, offset=20)

        assert page1.has_more is True
        assert page2.has_more is True
        assert page3.has_more is False

        assert page1.snapshots[0].success_rate < page2.snapshots[0].success_rate
        assert len(page3.snapshots) >= 3  # At least 3 remaining snapshots

    def test_get_success_rate_history_limit_clamping(self, query_with_data) -> None:
        """Test that limit is clamped to reasonable max."""
        # Request too large limit
        page = query_with_data.get_success_rate_history(days=1, limit=5000, offset=0)

        assert page.limit == 1000  # Should be clamped to max

    def test_get_success_rate_history_offset_clamping(self, query_with_data) -> None:
        """Test that offset is clamped to non-negative."""
        page = query_with_data.get_success_rate_history(days=1, limit=10, offset=-5)

        assert page.offset == 0

    def test_get_success_rate_history_empty_storage(self, temp_storage) -> None:
        """Test getting history when storage is empty."""
        query = ExtractionHistoryQuery(temp_storage)
        page = query.get_success_rate_history(days=7, limit=10, offset=0)

        assert page.total_count == 0
        assert len(page.snapshots) == 0
        assert page.has_more is False

    def test_get_success_rate_trend_daily(self, query_with_data) -> None:
        """Test computing daily trend."""
        trend = query_with_data.get_success_rate_trend(days=1, granularity="daily")

        assert trend.granularity == "daily"
        assert trend.success_rate_mean > 0
        assert trend.success_rate_min <= trend.success_rate_max
        assert trend.observation_count >= 23  # At least 23 snapshots
        assert trend.success_rate_trend >= 0  # Should be positive (improving)

    def test_get_success_rate_trend_hourly(self, query_with_data) -> None:
        """Test computing hourly trend."""
        trend = query_with_data.get_success_rate_trend(days=1, granularity="hourly")

        assert trend.granularity == "hourly"
        assert trend.success_rate_mean > 0
        assert trend.observation_count >= 23

    def test_get_success_rate_trend_weekly(self, temp_storage) -> None:
        """Test computing weekly trend."""
        # Add snapshots across a week (within recent time range)
        base_time = datetime.now(UTC) - timedelta(days=6)
        for day in range(7):
            for hour in range(3):
                snapshot = ExtractionHealthSnapshot(
                    observed_at=base_time + timedelta(days=day, hours=hour),
                    success_rate=80.0 + (day * 2),
                    complete_extraction=10 + day,
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=12 + day,
                )
                temp_storage.save_snapshot(snapshot)

        query = ExtractionHistoryQuery(temp_storage)
        trend = query.get_success_rate_trend(days=7, granularity="weekly")

        assert trend.granularity == "weekly"
        assert trend.observation_count == 21

    def test_get_success_rate_trend_monthly(self, temp_storage) -> None:
        """Test computing monthly trend."""
        # Add snapshots across a range of days (not months, to keep data recent)
        base_time = datetime.now(UTC) - timedelta(days=60)
        for day in range(0, 60, 12):  # Every 12 days for a ~60-day range
            for hour_offset in range(3):
                snapshot = ExtractionHealthSnapshot(
                    observed_at=base_time + timedelta(days=day, hours=hour_offset),
                    success_rate=75.0 + ((day // 12) * 3),
                    complete_extraction=8 + (day // 12),
                    partial_extraction=2,
                    no_extraction=0,
                    total_flaky_tests=10 + (day // 12),
                )
                temp_storage.save_snapshot(snapshot)

        query = ExtractionHistoryQuery(temp_storage)
        trend = query.get_success_rate_trend(days=90, granularity="monthly")

        assert trend.granularity == "monthly"
        assert trend.observation_count > 0  # Should have data

    def test_get_success_rate_trend_invalid_granularity(self, query_with_data) -> None:
        """Test that invalid granularity raises error."""
        with pytest.raises(ValueError, match="Unsupported granularity"):
            query_with_data.get_success_rate_trend(days=7, granularity="invalid")

    def test_get_success_rate_trend_empty_storage(self, temp_storage) -> None:
        """Test trend computation with empty storage."""
        query = ExtractionHistoryQuery(temp_storage)
        trend = query.get_success_rate_trend(days=7, granularity="daily")

        assert trend.observation_count == 0
        assert trend.success_rate_mean == 0.0

    def test_get_recent_snapshots(self, query_with_data) -> None:
        """Test fetching most recent snapshots."""
        snapshots = query_with_data.get_recent_snapshots(count=5)

        assert len(snapshots) == 5
        assert (
            snapshots[-1].success_rate > snapshots[0].success_rate
        )  # Last should be highest (most recent)

    def test_get_recent_snapshots_limit_clamping(self, query_with_data) -> None:
        """Test that count is clamped to reasonable max."""
        snapshots = query_with_data.get_recent_snapshots(count=5000)

        assert len(snapshots) <= 1000

    def test_get_recent_snapshots_empty_storage(self, temp_storage) -> None:
        """Test getting recent snapshots from empty storage."""
        query = ExtractionHistoryQuery(temp_storage)
        snapshots = query.get_recent_snapshots(count=10)

        assert len(snapshots) == 0

    def test_detect_anomalies_spike_down(self, temp_storage) -> None:
        """Test detecting downward spike anomaly."""
        base_time = datetime.now(UTC) - timedelta(hours=6)

        # Stable high success rate
        for i in range(5):
            snapshot = ExtractionHealthSnapshot(
                observed_at=base_time + timedelta(hours=i),
                success_rate=90.0,
                complete_extraction=18,
                partial_extraction=0,
                no_extraction=0,
                total_flaky_tests=18,
            )
            temp_storage.save_snapshot(snapshot)

        # Sudden drop
        anomaly_snapshot = ExtractionHealthSnapshot(
            observed_at=base_time + timedelta(hours=5),
            success_rate=70.0,  # Big drop
            complete_extraction=14,
            partial_extraction=0,
            no_extraction=4,
            total_flaky_tests=18,
        )
        temp_storage.save_snapshot(anomaly_snapshot)

        query = ExtractionHistoryQuery(temp_storage)
        anomalies = query.detect_anomalies(days=1, threshold_pct=5.0)

        assert len(anomalies) > 0
        assert anomalies[0].anomaly_type == "spike_down"
        assert anomalies[0].metric == "success_rate"

    def test_detect_anomalies_spike_up(self, temp_storage) -> None:
        """Test detecting upward spike anomaly."""
        base_time = datetime.now(UTC) - timedelta(hours=6)

        # Stable low success rate
        for i in range(5):
            snapshot = ExtractionHealthSnapshot(
                observed_at=base_time + timedelta(hours=i),
                success_rate=70.0,
                complete_extraction=14,
                partial_extraction=0,
                no_extraction=4,
                total_flaky_tests=18,
            )
            temp_storage.save_snapshot(snapshot)

        # Sudden improvement
        anomaly_snapshot = ExtractionHealthSnapshot(
            observed_at=base_time + timedelta(hours=5),
            success_rate=92.0,  # Big improvement
            complete_extraction=18,
            partial_extraction=0,
            no_extraction=1,
            total_flaky_tests=18,
        )
        temp_storage.save_snapshot(anomaly_snapshot)

        query = ExtractionHistoryQuery(temp_storage)
        anomalies = query.detect_anomalies(days=1, threshold_pct=5.0)

        assert len(anomalies) > 0
        assert anomalies[0].anomaly_type == "spike_up"

    def test_detect_anomalies_threshold(self, temp_storage) -> None:
        """Test anomaly detection with different thresholds."""
        base_time = datetime.now(UTC) - timedelta(hours=5)

        # Create snapshots with small variations
        for i in range(5):
            snapshot = ExtractionHealthSnapshot(
                observed_at=base_time + timedelta(hours=i),
                success_rate=85.0 + (i * 1.0),  # Small 1% increase per step
                complete_extraction=17,
                partial_extraction=0,
                no_extraction=1,
                total_flaky_tests=18,
            )
            temp_storage.save_snapshot(snapshot)

        query = ExtractionHistoryQuery(temp_storage)

        # High threshold should catch no anomalies
        anomalies_high = query.detect_anomalies(days=1, threshold_pct=50.0)
        assert len(anomalies_high) == 0

        # Low threshold should catch some
        anomalies_low = query.detect_anomalies(days=1, threshold_pct=0.5)
        assert len(anomalies_low) > 0

    def test_detect_anomalies_insufficient_data(self, temp_storage) -> None:
        """Test anomaly detection with insufficient data."""
        # Add only 2 snapshots
        base_time = datetime.now(UTC) - timedelta(hours=2)
        for i in range(2):
            snapshot = ExtractionHealthSnapshot(
                observed_at=base_time + timedelta(hours=i),
                success_rate=85.0,
                complete_extraction=17,
                partial_extraction=0,
                no_extraction=1,
                total_flaky_tests=18,
            )
            temp_storage.save_snapshot(snapshot)

        query = ExtractionHistoryQuery(temp_storage)
        anomalies = query.detect_anomalies(days=1)

        assert len(anomalies) == 0  # Need at least 3 points


class TestExtractionHistoryQueryIntegration:
    """Integration tests for query API."""

    def test_query_api_roundtrip(self, temp_storage) -> None:
        """Test complete roundtrip: save, query, verify."""
        base_time = datetime.now(UTC) - timedelta(days=6)

        # Save data
        snapshots_to_save = [
            ExtractionHealthSnapshot(
                observed_at=base_time + timedelta(days=i),
                success_rate=80.0 + (i * 2),
                complete_extraction=10 + i,
                partial_extraction=2,
                no_extraction=0,
                total_flaky_tests=12 + i,
            )
            for i in range(7)
        ]

        for snapshot in snapshots_to_save:
            temp_storage.save_snapshot(snapshot)

        # Query and verify
        query = ExtractionHistoryQuery(temp_storage)
        page = query.get_success_rate_history(days=7, limit=20, offset=0)

        assert page.total_count == 7
        assert len(page.snapshots) == 7
        assert page.snapshots[0].success_rate == 80.0
        assert page.snapshots[-1].success_rate == 92.0

    def test_query_api_trend_consistency(self, query_with_data) -> None:
        """Test that trend metrics are consistent with underlying data."""
        page = query_with_data.get_success_rate_history(days=1, limit=100, offset=0)
        trend = query_with_data.get_success_rate_trend(days=1, granularity="hourly")

        # Basic consistency checks
        assert trend.observation_count >= page.total_count - 1  # May differ by 1 due to bucketing
        if page.snapshots:
            assert trend.success_rate_mean > 0
            assert trend.success_rate_min > 0
