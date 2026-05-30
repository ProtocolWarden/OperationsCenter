# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the SlowTestTracker class."""
import importlib.util
from pathlib import Path

import pytest

# Import SlowTestTracker directly from the tests/conftest.py module
_conftest_path = Path(__file__).parent / "conftest.py"
_spec = importlib.util.spec_from_file_location("tests_conftest", _conftest_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
SlowTestTracker = _module.SlowTestTracker


class TestSlowTestTrackerBasics:
    """Test basic functionality of SlowTestTracker."""

    def test_init_with_default_threshold(self):
        """Test initialization with default threshold."""
        tracker = SlowTestTracker()
        assert tracker.threshold == 1.0

    def test_init_with_custom_threshold(self):
        """Test initialization with custom threshold."""
        tracker = SlowTestTracker(threshold_seconds=0.5)
        assert tracker.threshold == 0.5

    def test_record_item_markers(self):
        """Test recording marker status."""
        tracker = SlowTestTracker()
        tracker.record_item_markers("test_fast", False)
        tracker.record_item_markers("test_slow", True)

        assert tracker.test_markers["test_fast"] is False
        assert tracker.test_markers["test_slow"] is True

    def test_record_test(self):
        """Test recording test duration."""
        tracker = SlowTestTracker()
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.5)

        assert len(tracker.test_durations) == 1
        assert tracker.test_durations[0] == ("test1", 0.5, False)

    def test_record_test_with_marker(self):
        """Test recording test duration with marker."""
        tracker = SlowTestTracker()
        tracker.record_item_markers("test_slow", True)
        tracker.record_test("test_slow", 1.5)

        assert tracker.test_durations[0] == ("test_slow", 1.5, True)


class TestSlowTestDetection:
    """Test slow test detection logic."""

    def test_get_slow_tests_empty(self):
        """Test with no tests recorded."""
        tracker = SlowTestTracker(threshold_seconds=1.0)
        assert tracker.get_slow_tests() == []

    def test_get_slow_tests_all_fast(self):
        """Test when all tests are below threshold."""
        tracker = SlowTestTracker(threshold_seconds=1.0)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.5)
        tracker.record_item_markers("test2", False)
        tracker.record_test("test2", 0.8)

        assert tracker.get_slow_tests() == []

    def test_get_slow_tests_above_threshold(self):
        """Test detection of tests exceeding threshold."""
        tracker = SlowTestTracker(threshold_seconds=0.5)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.3)
        tracker.record_item_markers("test2", False)
        tracker.record_test("test2", 0.7)

        slow = tracker.get_slow_tests()
        assert len(slow) == 1
        assert slow[0][0] == "test2"
        assert slow[0][1] == 0.7

    def test_get_slow_tests_sorted_by_duration(self):
        """Test that slow tests are sorted by duration descending."""
        tracker = SlowTestTracker(threshold_seconds=0.1)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.5)
        tracker.record_item_markers("test2", False)
        tracker.record_test("test2", 0.9)
        tracker.record_item_markers("test3", False)
        tracker.record_test("test3", 0.3)

        slow = tracker.get_slow_tests()
        durations = [t[1] for t in slow]
        assert durations == [0.9, 0.5, 0.3]

    def test_get_slow_tests_marked_included(self):
        """Test that marked slow tests are included."""
        tracker = SlowTestTracker(threshold_seconds=1.0)
        tracker.record_item_markers("test_marked", True)
        tracker.record_test("test_marked", 0.5)

        slow = tracker.get_slow_tests()
        # Marked slow test below threshold is still in slow_tests
        assert len(slow) == 1
        assert slow[0][0] == "test_marked"
        assert slow[0][2] is True  # is_marked flag


class TestStatistics:
    """Test statistics computation."""

    def test_statistics_empty(self):
        """Test statistics with no tests."""
        tracker = SlowTestTracker()
        stats = tracker.get_statistics()

        assert stats["total"] == 0
        assert stats["slow_count"] == 0
        assert stats["avg_duration"] == 0.0
        assert stats["max_duration"] == 0.0

    def test_statistics_single_test(self):
        """Test statistics with single test."""
        tracker = SlowTestTracker(threshold_seconds=1.0)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.5)

        stats = tracker.get_statistics()
        assert stats["total"] == 1
        assert stats["slow_count"] == 0
        assert stats["avg_duration"] == 0.5
        assert stats["max_duration"] == 0.5
        assert stats["threshold"] == 1.0

    def test_statistics_multiple_tests(self):
        """Test statistics with multiple tests."""
        tracker = SlowTestTracker(threshold_seconds=0.5)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.3)
        tracker.record_item_markers("test2", False)
        tracker.record_test("test2", 0.7)
        tracker.record_item_markers("test3", False)
        tracker.record_test("test3", 0.6)

        stats = tracker.get_statistics()
        assert stats["total"] == 3
        assert stats["slow_count"] == 2
        assert stats["avg_duration"] == pytest.approx(0.5333, rel=0.01)
        assert stats["max_duration"] == 0.7
        assert stats["threshold"] == 0.5

    def test_statistics_all_slow(self):
        """Test statistics when all tests are slow."""
        tracker = SlowTestTracker(threshold_seconds=0.1)
        for i in range(5):
            tracker.record_item_markers(f"test{i}", False)
            tracker.record_test(f"test{i}", 0.2 * (i + 1))

        stats = tracker.get_statistics()
        assert stats["total"] == 5
        assert stats["slow_count"] == 5


class TestMarkerDetection:
    """Test marker detection and correlation."""

    def test_marker_false_by_default(self):
        """Test that tests default to unmarked."""
        tracker = SlowTestTracker()
        tracker.record_test("test_unknown", 0.5)

        # When test not in markers dict, default to False
        durations = tracker.get_slow_tests()
        # Note: slow_tests includes tests >= threshold regardless of marker status
        # So we need to check the underlying list instead
        assert tracker.test_durations[0][2] is False

    def test_mixed_marked_and_unmarked(self):
        """Test mixed marked and unmarked tests."""
        tracker = SlowTestTracker(threshold_seconds=0.1)

        # Marked slow tests
        tracker.record_item_markers("test_marked_slow", True)
        tracker.record_test("test_marked_slow", 0.5)

        # Unmarked tests exceeding threshold
        tracker.record_item_markers("test_unmarked_slow", False)
        tracker.record_test("test_unmarked_slow", 0.7)

        # Fast test
        tracker.record_item_markers("test_fast", False)
        tracker.record_test("test_fast", 0.05)

        slow = tracker.get_slow_tests()
        assert len(slow) == 2

        marked = [t for t in slow if t[2]]
        unmarked = [t for t in slow if not t[2]]

        assert len(marked) == 1
        assert len(unmarked) == 1


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_threshold(self):
        """Test with zero threshold (all tests are slow)."""
        tracker = SlowTestTracker(threshold_seconds=0.0)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.001)

        slow = tracker.get_slow_tests()
        assert len(slow) == 1

    def test_very_small_threshold(self):
        """Test with very small threshold."""
        tracker = SlowTestTracker(threshold_seconds=0.0001)
        tracker.record_item_markers("test1", False)
        tracker.record_test("test1", 0.0001)

        slow = tracker.get_slow_tests()
        assert len(slow) == 1

    def test_large_duration_values(self):
        """Test with large duration values."""
        tracker = SlowTestTracker(threshold_seconds=100.0)
        tracker.record_item_markers("test_slow", False)
        tracker.record_test("test_slow", 150.5)

        stats = tracker.get_statistics()
        assert stats["max_duration"] == 150.5
        assert len(tracker.get_slow_tests()) == 1

    def test_many_tests(self):
        """Test with many tests."""
        tracker = SlowTestTracker(threshold_seconds=0.5)

        for i in range(1000):
            tracker.record_item_markers(f"test{i}", False)
            tracker.record_test(f"test{i}", 0.1 * (i % 10))

        stats = tracker.get_statistics()
        assert stats["total"] == 1000
        assert stats["slow_count"] == 500  # Tests with 0.5-0.9 duration
