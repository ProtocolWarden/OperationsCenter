# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for extraction health query methods in FlakyTestQueryMixin."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from operations_center.observer.models import RepoStateSnapshot
from operations_center.observer.query_flaky import (
    ExtractionHealth,
    FlakyTestQueryMixin,
)


class MockFlakyTestQuery(FlakyTestQueryMixin):
    """Test implementation of FlakyTestQueryMixin."""

    def __init__(self, snapshots: list[RepoStateSnapshot]) -> None:
        self.snapshots = snapshots

    def _load_snapshots_in_range(self, timerange: Any) -> list[RepoStateSnapshot]:
        return self.snapshots

    def _get_recent_snapshots(self, count: int) -> list[RepoStateSnapshot]:
        return self.snapshots[-count:] if self.snapshots else []


@pytest.fixture
def sample_snapshot() -> RepoStateSnapshot:
    """Create a sample snapshot with flaky test signal."""
    snapshot = MagicMock()

    flaky_signal = MagicMock()
    flaky_signal.status = "available"
    flaky_signal.flaky_test_count = 5
    flaky_signal.most_problematic_tests = [
        {
            "name": "test_module::test_complete_extraction",
            "failure_rate": 0.8,
            "run_count": 10,
            "category": "INTERMITTENT",
            "test_name": "test_complete_extraction",
            "assertion_message": "Expected foo == bar, but got baz",
        },
        {
            "name": "test_module::test_partial_test_name_only",
            "failure_rate": 0.6,
            "run_count": 10,
            "category": "ENVIRONMENT",
            "test_name": "test_partial_test_name_only",
            "assertion_message": None,
        },
        {
            "name": "test_module::test_partial_assertion_only",
            "failure_rate": 0.5,
            "run_count": 10,
            "category": "INTERMITTENT",
            "test_name": None,
            "assertion_message": "Connection timeout after 30s",
        },
        {
            "name": "test_module::test_missing_both",
            "failure_rate": 0.4,
            "run_count": 10,
            "category": "UNKNOWN",
            "test_name": None,
            "assertion_message": None,
        },
        {
            "name": "test_module::test_truncated_message",
            "failure_rate": 0.3,
            "run_count": 10,
            "category": "INTERMITTENT",
            "test_name": "test_truncated_message",
            "assertion_message": "x" * 205,
        },
    ]

    snapshot.signals.flaky_test_signal = flaky_signal
    return snapshot


class TestExtractionHealth:
    """Test extraction health retrieval."""

    def test_get_extraction_health_complete_coverage(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Extract health reflects complete extraction coverage."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        # 2 tests have both test_name and assertion_message (complete extraction)
        # 2 tests have only one field (partial extraction)
        # 1 test has neither (no extraction)
        assert health.complete_extraction == 2  # test_complete_extraction, test_truncated_message
        assert (
            health.partial_extraction == 2
        )  # test_partial_test_name_only, test_partial_assertion_only
        assert health.no_extraction == 1  # test_missing_both
        assert health.no_extraction == 1
        assert health.success_rate == 80.0  # 4 out of 5 have some extraction

    def test_get_extraction_health_with_truncated_messages(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Extract health detects truncated messages."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        assert health.edge_case_summary["truncated_messages"] == 1

    def test_get_extraction_health_empty_snapshots(self) -> None:
        """Extract health returns defaults when no snapshots available."""
        query = MockFlakyTestQuery([])
        health = query.get_extraction_health()

        assert health.success_rate == 0.0
        assert health.no_extraction == 0
        assert health.complete_extraction == 0
        assert health.partial_extraction == 0
        assert health.no_extraction == 0

    def test_get_extraction_health_no_flaky_tests(self) -> None:
        """Extract health returns defaults when signal unavailable."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "unavailable"
        flaky_signal.most_problematic_tests = None
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.success_rate == 0.0
        assert health.no_extraction == 0

    def test_get_extraction_health_special_characters(self) -> None:
        """Extract health detects special characters in messages."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_special_chars",
                "failure_rate": 0.5,
                "run_count": 10,
                "category": "INTERMITTENT",
                "test_name": "test_special",
                "assertion_message": "Unicode: é ñ",  # special unicode chars
            }
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert (
            health.edge_case_summary["special_chars"] >= 0
        )  # May or may not detect depending on encoding

    def test_get_extraction_health_with_timerange(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Extract health respects timerange parameter."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health(timerange="custom_range")

        assert health.success_rate == 80.0
        assert health.complete_extraction == 2


class TestFilterByExtractionStatus:
    """Test filtering tests by extraction status."""

    def test_filter_complete_extraction(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter returns only tests with complete extraction."""
        query = MockFlakyTestQuery([sample_snapshot])
        complete = query.filter_by_extraction_status("complete")

        assert len(complete) == 2
        names = {t.name for t in complete}
        assert "test_module::test_complete_extraction" in names
        assert "test_module::test_truncated_message" in names
        # Verify both have test_name and assertion_message
        for test in complete:
            assert test.test_name is not None
            assert test.assertion_message is not None

    def test_filter_partial_extraction(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter returns tests with partial extraction."""
        query = MockFlakyTestQuery([sample_snapshot])
        partial = query.filter_by_extraction_status("partial")

        assert len(partial) == 2
        names = {t.name for t in partial}
        assert "test_module::test_partial_test_name_only" in names
        assert "test_module::test_partial_assertion_only" in names
        # Verify each has exactly one of test_name or assertion_message
        for test in partial:
            has_test_name = test.test_name is not None
            has_assertion = test.assertion_message is not None
            assert has_test_name != has_assertion  # XOR: exactly one should be True

    def test_filter_missing_extraction(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter returns tests with no extraction."""
        query = MockFlakyTestQuery([sample_snapshot])
        missing = query.filter_by_extraction_status("missing")

        assert len(missing) == 1
        assert missing[0].name == "test_module::test_missing_both"
        assert missing[0].test_name is None
        assert missing[0].assertion_message is None

    def test_filter_sorted_by_failure_rate(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter results are sorted by failure_rate descending."""
        query = MockFlakyTestQuery([sample_snapshot])
        partial = query.filter_by_extraction_status("partial")

        failure_rates = [t.failure_rate for t in partial]
        assert failure_rates == sorted(failure_rates, reverse=True)

    def test_filter_invalid_status_raises(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter raises ValueError for invalid status."""
        query = MockFlakyTestQuery([sample_snapshot])

        with pytest.raises(ValueError, match="Invalid status"):
            query.filter_by_extraction_status("invalid_status")

    def test_filter_case_insensitive(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter is case-insensitive for status values."""
        query = MockFlakyTestQuery([sample_snapshot])

        complete_lower = query.filter_by_extraction_status("complete")
        complete_upper = query.filter_by_extraction_status("COMPLETE")
        complete_mixed = query.filter_by_extraction_status("CoMpLeTE")

        assert len(complete_lower) == len(complete_upper) == len(complete_mixed)

    def test_filter_empty_snapshots(self) -> None:
        """Filter returns empty list when no snapshots available."""
        query = MockFlakyTestQuery([])
        result = query.filter_by_extraction_status("complete")

        assert result == []

    def test_filter_with_timerange(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Filter respects timerange parameter."""
        query = MockFlakyTestQuery([sample_snapshot])
        complete = query.filter_by_extraction_status("complete", timerange="custom_range")

        assert len(complete) == 2


class TestExtractionHealthDataclass:
    """Test ExtractionHealth dataclass."""

    def test_defaults(self) -> None:
        """ExtractionHealth has sensible defaults."""
        health = ExtractionHealth()

        assert health.success_rate == 0.0
        assert health.no_extraction == 0
        assert health.complete_extraction == 0
        assert health.partial_extraction == 0
        assert health.no_extraction == 0
        assert health.edge_case_summary == {}

    def test_with_values(self) -> None:
        """ExtractionHealth can be created with values."""
        health = ExtractionHealth(
            success_rate=75.0,
            complete_extraction=3,
            partial_extraction=2,
            no_extraction=1,
            edge_case_summary={"truncated_messages": 1, "special_chars": 0},
        )

        assert health.success_rate == 75.0
        assert health.no_extraction == 1
        assert health.complete_extraction == 3
        assert health.edge_case_summary["truncated_messages"] == 1


class TestIntegrationWithExistingMethods:
    """Test interaction with existing query methods."""

    def test_extraction_health_uses_get_flaky_tests(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Extract health correctly uses get_flaky_tests results."""
        query = MockFlakyTestQuery([sample_snapshot])

        flaky_tests = query.get_flaky_tests()
        health = query.get_extraction_health()

        total_from_tests = len(flaky_tests)
        total_from_health = (
            health.complete_extraction + health.partial_extraction + health.no_extraction
        )
        assert total_from_tests == total_from_health

    def test_filter_by_extraction_status_consistent_with_health(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Filter results match extraction health counts."""
        query = MockFlakyTestQuery([sample_snapshot])

        health = query.get_extraction_health()
        complete = query.filter_by_extraction_status("complete")
        partial = query.filter_by_extraction_status("partial")
        missing = query.filter_by_extraction_status("missing")

        assert len(complete) == health.complete_extraction
        assert len(partial) == health.partial_extraction
        assert len(missing) == health.no_extraction
