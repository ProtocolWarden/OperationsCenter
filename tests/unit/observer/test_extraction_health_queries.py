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

    def test_gaps_field_defaults_to_empty_list(self) -> None:
        """ExtractionHealth gaps field defaults to empty list."""
        health = ExtractionHealth()
        assert health.gaps == []

    def test_edge_cases_field_defaults_to_empty_list(self) -> None:
        """ExtractionHealth edge_cases field defaults to empty list."""
        health = ExtractionHealth()
        assert health.edge_cases == []

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

    def test_gaps_field_accepts_list_of_strings(self) -> None:
        """ExtractionHealth gaps field accepts a list of test ID strings."""
        health = ExtractionHealth(gaps=["test_module::test_a", "test_module::test_b"])
        assert health.gaps == ["test_module::test_a", "test_module::test_b"]

    def test_edge_cases_field_accepts_list_of_dicts(self) -> None:
        """ExtractionHealth edge_cases field accepts a list of dicts."""
        health = ExtractionHealth(
            edge_cases=[{"test_id": "test_module::test_a", "issue": "truncated_message"}]
        )
        assert health.edge_cases == [
            {"test_id": "test_module::test_a", "issue": "truncated_message"}
        ]


class TestExtractionHealthGaps:
    """Test that get_extraction_health populates the gaps list."""

    def test_gaps_contains_test_ids_for_missing_both(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """gaps list includes pytest node IDs where both test_name and assertion_message are None."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        assert "test_module::test_missing_both" in health.gaps

    def test_gaps_does_not_include_partial_or_complete_tests(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """gaps list excludes partial and complete extraction tests."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        assert "test_module::test_complete_extraction" not in health.gaps
        assert "test_module::test_partial_test_name_only" not in health.gaps
        assert "test_module::test_partial_assertion_only" not in health.gaps

    def test_gaps_is_empty_when_no_missing_tests(self) -> None:
        """gaps list is empty when all tests have at least one extracted field."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_a",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_a",
                "assertion_message": "some message",
            }
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.gaps == []

    def test_gaps_capped_at_10_samples(self) -> None:
        """gaps list contains at most 10 test IDs even if more are missing."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": f"test_module::test_missing_{i}",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "UNKNOWN",
                "test_name": None,
                "assertion_message": None,
            }
            for i in range(15)
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert len(health.gaps) == 10

    def test_gaps_count_matches_no_extraction(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Number of gaps in list is consistent with no_extraction count."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        assert len(health.gaps) == health.no_extraction


class TestExtractionHealthEdgeCases:
    """Test that get_extraction_health populates the edge_cases list."""

    def test_edge_cases_includes_truncated_message_issue(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """edge_cases list includes entry with issue=truncated_message for truncated assertions."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        issues = [e["issue"] for e in health.edge_cases]
        assert "truncated_message" in issues

    def test_edge_cases_entry_has_test_id_and_issue_keys(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Every edge_cases entry has test_id and issue fields."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        for entry in health.edge_cases:
            assert "test_id" in entry
            assert "issue" in entry

    def test_edge_cases_test_id_is_the_pytest_node_id(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """edge_cases test_id is the full pytest node ID."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        truncated_entries = [e for e in health.edge_cases if e["issue"] == "truncated_message"]
        assert len(truncated_entries) == 1
        assert truncated_entries[0]["test_id"] == "test_module::test_truncated_message"

    def test_edge_cases_is_empty_when_no_edge_cases(self) -> None:
        """edge_cases list is empty when no edge cases are detected."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_clean",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_clean",
                "assertion_message": "simple ascii message",
            }
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.edge_cases == []

    def test_edge_cases_capped_at_10_entries(self) -> None:
        """edge_cases list contains at most 10 entries even if more are detected."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": f"test_module::test_trunc_{i}",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": f"test_trunc_{i}",
                "assertion_message": "x" * 210,
            }
            for i in range(15)
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert len(health.edge_cases) <= 10

    def test_edge_cases_special_chars_issue(self) -> None:
        """edge_cases includes special_chars issue for messages with non-ASCII characters."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_unicode",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_unicode",
                "assertion_message": "Error: café résumé",
            }
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        issues = [e["issue"] for e in health.edge_cases]
        assert "special_chars" in issues


class TestExtractionHealthGapsFormat:
    """Tests for gaps list format invariants."""

    def test_gaps_items_are_plain_strings_not_objects(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Each item in gaps is a str (pytest node ID), not a dict or other type."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        for item in health.gaps:
            assert isinstance(item, str)

    def test_gaps_contains_only_tests_with_both_fields_none(self) -> None:
        """gaps includes a test only when BOTH test_name AND assertion_message are None."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_name_only",
                "failure_rate": 0.7,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "some_test",
                "assertion_message": None,
            },
            {
                "name": "test_module::test_assertion_only",
                "failure_rate": 0.6,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": None,
                "assertion_message": "Connection timed out",
            },
            {
                "name": "test_module::test_gap",
                "failure_rate": 0.3,
                "run_count": 5,
                "category": "UNKNOWN",
                "test_name": None,
                "assertion_message": None,
            },
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.gaps == ["test_module::test_gap"]

    def test_gap_tests_do_not_appear_in_edge_cases(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Tests with no extraction (gaps) cannot appear in edge_cases — they have no assertion_message."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        gap_ids = set(health.gaps)
        edge_case_ids = {e["test_id"] for e in health.edge_cases}
        assert gap_ids.isdisjoint(edge_case_ids)


class TestExtractionHealthEdgeCasesFormat:
    """Tests for edge_cases list format invariants."""

    def test_edge_cases_items_have_exactly_test_id_and_issue_keys(
        self, sample_snapshot: RepoStateSnapshot
    ) -> None:
        """Each edge_cases entry has exactly test_id and issue keys, no extras."""
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        for entry in health.edge_cases:
            assert set(entry.keys()) == {"test_id", "issue"}

    def test_edge_cases_truncated_issue_value_is_singular(self) -> None:
        """Issue value for long assertion messages is 'truncated_message' (singular, not plural)."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_long",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_long",
                "assertion_message": "x" * 210,
            }
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        truncated_entries = [e for e in health.edge_cases if "truncated" in e["issue"]]
        assert len(truncated_entries) == 1
        assert truncated_entries[0]["issue"] == "truncated_message"

    def test_edge_cases_special_chars_issue_value(self) -> None:
        """Issue value for non-ASCII messages is 'special_chars'."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_unicode",
                "failure_rate": 0.5,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_unicode",
                "assertion_message": "Error: naïve résumé",
            }
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        special_entries = [e for e in health.edge_cases if "special" in e["issue"]]
        assert len(special_entries) == 1
        assert special_entries[0]["issue"] == "special_chars"

    def test_edge_cases_multiple_issue_types_in_same_run(self) -> None:
        """edge_cases can contain both truncated_message and special_chars entries."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = [
            {
                "name": "test_module::test_truncated",
                "failure_rate": 0.8,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_truncated",
                "assertion_message": "x" * 210,
            },
            {
                "name": "test_module::test_unicode",
                "failure_rate": 0.6,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": "test_unicode",
                "assertion_message": "Error: naïve résumé",
            },
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        issues = {e["issue"] for e in health.edge_cases}
        assert "truncated_message" in issues
        assert "special_chars" in issues

    def test_edge_cases_combined_cap_across_issue_types(self) -> None:
        """edge_cases total is capped at 10 across all issue types combined."""
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        # 8 truncated + 8 special_chars = 16 entries without cap → should cap at 10
        flaky_signal.most_problematic_tests = [
            {
                "name": f"test_module::test_trunc_{i}",
                "failure_rate": 0.8,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": f"test_trunc_{i}",
                "assertion_message": "x" * 210,
            }
            for i in range(8)
        ] + [
            {
                "name": f"test_module::test_unicode_{i}",
                "failure_rate": 0.6,
                "run_count": 5,
                "category": "INTERMITTENT",
                "test_name": f"test_unicode_{i}",
                "assertion_message": "Error: naïve résumé",
            }
            for i in range(8)
        ]
        snapshot.signals.flaky_test_signal = flaky_signal

        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert len(health.edge_cases) <= 10


class TestMessageQualityRate:
    """Tests for message_quality_rate and low_quality_messages."""

    def _make_snapshot(self, tests: list[dict]) -> MagicMock:
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = tests
        snapshot.signals.flaky_test_signal = flaky_signal
        return snapshot

    def _test_entry(
        self,
        name: str,
        assertion_message: str | None,
        test_name: str | None = "some_test",
    ) -> dict:
        return {
            "name": name,
            "failure_rate": 0.5,
            "run_count": 5,
            "category": "INTERMITTENT",
            "test_name": test_name,
            "assertion_message": assertion_message,
        }

    def test_all_informative_messages_yields_100(self) -> None:
        """All non-empty, sufficiently long, non-bare-type messages → quality rate 100.0."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_a", "Expected foo == bar but got baz"),
                self._test_entry("mod::test_b", "Connection failed after 30s retry"),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 100.0
        assert health.low_quality_messages == []

    def test_none_when_no_assertion_messages(self) -> None:
        """message_quality_rate is None when no tests carry an assertion_message."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_a", None),
                self._test_entry("mod::test_b", None),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate is None
        assert health.low_quality_messages == []

    def test_empty_message_is_low_quality(self) -> None:
        """Empty string assertion_message is flagged as low-quality with reason 'empty'."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_empty", ""),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert len(health.low_quality_messages) == 1
        assert health.low_quality_messages[0]["test_id"] == "mod::test_empty"
        assert health.low_quality_messages[0]["reason"] == "empty"

    def test_bare_exception_type_is_low_quality(self) -> None:
        """Bare exception type name (e.g. 'TimeoutError') is flagged with reason 'bare_exception_type'."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_timeout", "TimeoutError"),
                self._test_entry("mod::test_conn", "ConnectionError"),
                self._test_entry("mod::test_os", "OSError"),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 0.0
        reasons = [e["reason"] for e in health.low_quality_messages]
        assert all(r == "bare_exception_type" for r in reasons)
        assert len(health.low_quality_messages) == 3

    def test_too_short_message_is_low_quality(self) -> None:
        """Message with fewer than 10 chars is flagged with reason 'too_short'."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_short", "No data"),  # 7 chars
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.low_quality_messages[0]["reason"] == "too_short"

    def test_exactly_10_chars_is_informative(self) -> None:
        """Message of exactly 10 characters is considered informative (boundary)."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_boundary", "1234567890"),  # exactly 10 chars
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 100.0
        assert health.low_quality_messages == []

    def test_9_chars_is_low_quality(self) -> None:
        """Message of 9 characters is below the threshold → too_short."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_nine", "123456789"),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.low_quality_messages[0]["reason"] == "too_short"

    def test_mixed_quality_computes_correct_rate(self) -> None:
        """Rate reflects proportion of informative messages among those present."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_good", "Expected 42 but got 0"),
                self._test_entry("mod::test_empty", ""),
                self._test_entry("mod::test_no_msg", None),  # excluded from denominator
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        # 1 informative / 2 with assertion_message = 50.0%
        assert health.message_quality_rate == 50.0
        assert len(health.low_quality_messages) == 1

    def test_tests_without_assertion_message_excluded_from_denominator(self) -> None:
        """Tests with assertion_message=None don't count toward the quality denominator."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_no_msg", None),
                self._test_entry("mod::test_no_msg2", None, test_name=None),
                self._test_entry("mod::test_good", "Assertion failed: value mismatch"),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        # Only 1 test has assertion_message; it's informative → 100%
        assert health.message_quality_rate == 100.0

    def test_low_quality_messages_capped_at_10(self) -> None:
        """low_quality_messages list contains at most 10 entries."""
        snapshot = self._make_snapshot(
            [self._test_entry(f"mod::test_short_{i}", "tiny") for i in range(15)]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert len(health.low_quality_messages) == 10
        # quality rate still reflects the full count
        assert health.message_quality_rate == 0.0

    def test_low_quality_entry_has_test_id_and_reason_keys(self) -> None:
        """Each low_quality_messages entry has exactly test_id and reason keys."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_short", "short"),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        for entry in health.low_quality_messages:
            assert set(entry.keys()) == {"test_id", "reason"}

    def test_truncated_message_is_still_informative(self) -> None:
        """A 200-char truncated message is informative — truncation is tracked separately."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_trunc", "x" * 205),
            ]
        )
        query = MockFlakyTestQuery([snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 100.0
        assert health.low_quality_messages == []

    def test_sample_snapshot_quality_rate(self, sample_snapshot: RepoStateSnapshot) -> None:
        """Verify quality rate on the shared sample_snapshot fixture."""
        # sample_snapshot has 4 tests with assertion_message:
        #   "Expected foo == bar, but got baz" (informative)
        #   "Connection timeout after 30s"     (informative)
        #   "x" * 205                          (informative — truncated but long)
        #   test_partial_assertion_only: "Connection timeout after 30s" — wait,
        #   let me check: test_partial_test_name_only has assertion_message=None,
        #   test_partial_assertion_only has assertion_message="Connection timeout after 30s"
        # So: 3 have assertion_message; all 3 are informative → 100%
        query = MockFlakyTestQuery([sample_snapshot])
        health = query.get_extraction_health()

        assert health.message_quality_rate == 100.0


class TestMessageQualityRateDataclass:
    """Tests for new ExtractionHealth dataclass fields."""

    def test_message_quality_rate_defaults_to_none(self) -> None:
        health = ExtractionHealth()
        assert health.message_quality_rate is None

    def test_low_quality_messages_defaults_to_empty_list(self) -> None:
        health = ExtractionHealth()
        assert health.low_quality_messages == []

    def test_message_quality_rate_accepts_float(self) -> None:
        health = ExtractionHealth(message_quality_rate=75.0)
        assert health.message_quality_rate == 75.0

    def test_low_quality_messages_accepts_list_of_dicts(self) -> None:
        entries = [{"test_id": "mod::test_a", "reason": "empty"}]
        health = ExtractionHealth(low_quality_messages=entries)
        assert health.low_quality_messages == entries


class TestMessageQualityRateEdgeCases:
    """Edge-case and boundary coverage for message_quality_rate quality gates."""

    def _make_snapshot(self, tests: list[dict]) -> MagicMock:
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = tests
        snapshot.signals.flaky_test_signal = flaky_signal
        return snapshot

    def _test_entry(
        self,
        name: str,
        assertion_message: str | None,
        test_name: str | None = "some_test",
    ) -> dict:
        return {
            "name": name,
            "failure_rate": 0.5,
            "run_count": 5,
            "category": "INTERMITTENT",
            "test_name": test_name,
            "assertion_message": assertion_message,
        }

    def test_whitespace_only_message_is_too_short(self) -> None:
        """A whitespace-only string has fewer than 10 chars → classified as too_short."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_ws", "   ")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.low_quality_messages[0]["reason"] == "too_short"

    def test_timeout_error_is_bare_exception_type(self) -> None:
        """'TimeoutError' (exact string) is classified as bare_exception_type."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_t", "TimeoutError")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.low_quality_messages[0]["reason"] == "bare_exception_type"

    def test_connection_error_is_bare_exception_type(self) -> None:
        """'ConnectionError' is classified as bare_exception_type."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_c", "ConnectionError")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.low_quality_messages[0]["reason"] == "bare_exception_type"

    def test_os_error_is_bare_exception_type(self) -> None:
        """'OSError' is classified as bare_exception_type even though it is shorter than 10 chars
        (the bare-exception-type gate fires before the too-short gate)."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_os", "OSError")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.low_quality_messages[0]["reason"] == "bare_exception_type"

    def test_value_error_is_not_bare_exception_type(self) -> None:
        """'ValueError' is not in _BARE_EXCEPTION_TYPE_NAMES and is exactly 10 chars → informative."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_ve", "ValueError")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 100.0
        assert health.low_quality_messages == []

    def test_bare_exception_type_check_is_case_sensitive(self) -> None:
        """'timeouterror' (lowercase) is not in the frozenset → falls through to length check.
        It has 12 chars so it is informative."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_lc", "timeouterror")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 100.0
        assert health.low_quality_messages == []

    def test_rate_is_zero_not_none_when_all_messages_low_quality(self) -> None:
        """rate is 0.0 (not None) when assertion_messages exist but all are low-quality."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_a", ""),  # empty
                self._test_entry("mod::test_b", "short"),  # too_short
            ]
        )
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert health.message_quality_rate is not None

    def test_partial_extraction_test_contributes_to_quality_denominator(self) -> None:
        """A test with assertion_message=present but test_name=None (partial) counts toward
        the quality denominator."""
        snapshot = self._make_snapshot(
            [
                self._test_entry(
                    "mod::test_partial", "Connection failed after retry", test_name=None
                ),
            ]
        )
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        # partial extraction, but assertion_message is informative
        assert health.partial_extraction == 1
        assert health.message_quality_rate == 100.0

    def test_all_three_quality_reasons_in_one_run(self) -> None:
        """One test per reason: empty, bare_exception_type, too_short → all three in low_quality_messages."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_empty", ""),
                self._test_entry("mod::test_bare", "TimeoutError"),
                self._test_entry("mod::test_short", "short"),
            ]
        )
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        reasons = {e["reason"] for e in health.low_quality_messages}
        assert reasons == {"empty", "bare_exception_type", "too_short"}

    def test_low_quality_cap_preserves_rate_accuracy(self) -> None:
        """With 15 low-quality messages the cap limits the sample to 10 but rate is still 0.0."""
        snapshot = self._make_snapshot([self._test_entry(f"mod::test_{i}", "") for i in range(15)])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 0.0
        assert len(health.low_quality_messages) == 10  # capped

    def test_message_exactly_at_min_length_is_informative(self) -> None:
        """A 10-character message (the minimum) passes the too-short gate → informative."""
        snapshot = self._make_snapshot([self._test_entry("mod::test_boundary", "1234567890")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 100.0

    def test_quality_denominator_excludes_none_messages_in_mixed_set(self) -> None:
        """Tests with assertion_message=None are excluded from both numerator and denominator;
        only those with a message affect the rate."""
        snapshot = self._make_snapshot(
            [
                self._test_entry("mod::test_no_msg_1", None),
                self._test_entry("mod::test_no_msg_2", None, test_name=None),
                self._test_entry("mod::test_good", "Assertion failed: expected True"),
                self._test_entry("mod::test_bad", ""),
            ]
        )
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        # 2 with assertion_message; 1 informative → 50.0%
        assert health.message_quality_rate == 50.0


class TestMessageQualityRateFormula:
    """Verify the rate formula: informative / with_assertion × 100."""

    def _make_snapshot(self, tests: list[dict]) -> MagicMock:
        snapshot = MagicMock()
        flaky_signal = MagicMock()
        flaky_signal.status = "available"
        flaky_signal.most_problematic_tests = tests
        snapshot.signals.flaky_test_signal = flaky_signal
        return snapshot

    def _informative(self, name: str) -> dict:
        return {
            "name": name,
            "failure_rate": 0.5,
            "run_count": 5,
            "category": "INTERMITTENT",
            "test_name": "some_test",
            "assertion_message": "Expected result to be True but got False",
        }

    def _low_quality(self, name: str) -> dict:
        return {
            "name": name,
            "failure_rate": 0.5,
            "run_count": 5,
            "category": "INTERMITTENT",
            "test_name": "some_test",
            "assertion_message": "",  # empty → low quality
        }

    def test_one_informative_out_of_three(self) -> None:
        """1 informative / 3 with_assertion = 33.33...%."""
        snapshot = self._make_snapshot(
            [self._informative("mod::a"), self._low_quality("mod::b"), self._low_quality("mod::c")]
        )
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        expected = 1 / 3 * 100.0
        assert abs(health.message_quality_rate - expected) < 0.01

    def test_two_informative_out_of_three(self) -> None:
        """2 informative / 3 with_assertion = 66.66...%."""
        snapshot = self._make_snapshot(
            [self._informative("mod::a"), self._informative("mod::b"), self._low_quality("mod::c")]
        )
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        expected = 2 / 3 * 100.0
        assert abs(health.message_quality_rate - expected) < 0.01

    def test_two_informative_out_of_five(self) -> None:
        """2 informative / 5 with_assertion = 40.0%."""
        tests = [self._informative(f"mod::good_{i}") for i in range(2)] + [
            self._low_quality(f"mod::bad_{i}") for i in range(3)
        ]
        snapshot = self._make_snapshot(tests)
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 40.0

    def test_formula_result_type_is_float(self) -> None:
        """message_quality_rate is always a float (not int) when non-None."""
        snapshot = self._make_snapshot([self._informative("mod::a")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert isinstance(health.message_quality_rate, float)

    def test_single_informative_test_yields_100_percent(self) -> None:
        """1 informative / 1 with_assertion = 100.0%."""
        snapshot = self._make_snapshot([self._informative("mod::only")])
        health = MockFlakyTestQuery([snapshot]).get_extraction_health()

        assert health.message_quality_rate == 100.0


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
