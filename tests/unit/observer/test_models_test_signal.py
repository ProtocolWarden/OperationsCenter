# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for TestSignal model enhancements with test name and assertion extraction."""

from datetime import datetime, UTC


from operations_center.observer.models import TestSignal


class TestTestSignalWithNewFields:
    """Test TestSignal model with new test_name and assertion_message fields."""

    def test_test_signal_basic_creation(self) -> None:
        """Test creating TestSignal with minimal required fields."""
        signal = TestSignal(status="passing")
        assert signal.status == "passing"
        assert signal.test_name is None
        assert signal.assertion_message is None
        assert signal.test_names is None

    def test_test_signal_with_test_name(self) -> None:
        """Test TestSignal with test_name field."""
        signal = TestSignal(
            status="failing",
            test_name="test_example",
            failed_count=1,
        )
        assert signal.status == "failing"
        assert signal.test_name == "test_example"
        assert signal.assertion_message is None
        assert signal.failed_count == 1

    def test_test_signal_with_assertion_message(self) -> None:
        """Test TestSignal with assertion_message field."""
        signal = TestSignal(
            status="failing",
            assertion_message="assert 5 == 3",
            failed_count=1,
        )
        assert signal.status == "failing"
        assert signal.test_name is None
        assert signal.assertion_message == "assert 5 == 3"
        assert signal.failed_count == 1

    def test_test_signal_with_all_new_fields(self) -> None:
        """Test TestSignal with all new failure extraction fields."""
        signal = TestSignal(
            status="failing",
            test_count=5,
            failed_count=1,
            test_name="test_addition",
            assertion_message="assert result == expected",
            test_names=["test_addition", "test_subtraction"],
            failure_category="assertion",
            source="pytest",
        )
        assert signal.status == "failing"
        assert signal.test_name == "test_addition"
        assert signal.assertion_message == "assert result == expected"
        assert signal.test_names == ["test_addition", "test_subtraction"]
        assert signal.failure_category == "assertion"
        assert signal.source == "pytest"
        assert signal.test_count == 5
        assert signal.failed_count == 1

    def test_test_signal_backward_compatibility(self) -> None:
        """Test that TestSignal is backward compatible with old code."""
        # Old code that doesn't use new fields should still work
        signal = TestSignal(
            status="passing",
            test_count=10,
            passed_count=10,
            execution_time_ms=500,
            coverage_percent=85.0,
            source="pytest",
        )
        assert signal.status == "passing"
        assert signal.test_count == 10
        assert signal.passed_count == 10
        assert signal.execution_time_ms == 500
        assert signal.coverage_percent == 85.0
        # New fields should be None for backward compatibility
        assert signal.test_name is None
        assert signal.assertion_message is None
        assert signal.test_names is None

    def test_test_signal_with_full_details(self) -> None:
        """Test TestSignal with comprehensive failure details."""
        observed = datetime.now(UTC)
        signal = TestSignal(
            status="flaky",
            test_count=3,
            passed_count=2,
            failed_count=1,
            test_name="test_network_call",
            assertion_message="Connection timeout after 30s",
            test_names=["test_network_call", "test_cache"],
            failure_category="timeout",
            execution_time_ms=35000,
            summary="1 of 3 tests failed due to network timeout",
            source="pytest",
            observed_at=observed,
        )
        assert signal.status == "flaky"
        assert signal.test_count == 3
        assert signal.failed_count == 1
        assert signal.test_name == "test_network_call"
        assert signal.assertion_message == "Connection timeout after 30s"
        assert signal.failure_category == "timeout"
        assert signal.execution_time_ms == 35000
        assert signal.observed_at == observed

    def test_test_signal_assertion_message_truncation(self) -> None:
        """Test that long assertion messages are handled properly."""
        long_message = "x" * 250  # Longer than typical 200 char limit
        signal = TestSignal(
            status="failing",
            assertion_message=long_message,
            failed_count=1,
        )
        # Should accept the long message (truncation is done at extraction time)
        assert signal.assertion_message == long_message
        assert len(signal.assertion_message) == 250

    def test_test_signal_multiple_test_names(self) -> None:
        """Test TestSignal with multiple test names for aggregates."""
        test_names = [
            "test_foo",
            "test_bar",
            "test_baz",
            "test_qux",
        ]
        signal = TestSignal(
            status="failing",
            test_count=10,
            failed_count=4,
            test_names=test_names,
            test_name="test_foo",  # Primary failing test
        )
        assert signal.test_names == test_names
        assert signal.test_name == "test_foo"
        assert len(signal.test_names) == 4


class TestTestSignalFieldTypes:
    """Test that TestSignal field types are correct."""

    def test_test_name_is_optional_string(self) -> None:
        """Test that test_name field accepts string or None."""
        # Should accept string
        signal = TestSignal(status="failing", test_name="test_x")
        assert isinstance(signal.test_name, str)

        # Should accept None
        signal = TestSignal(status="passing")
        assert signal.test_name is None

    def test_assertion_message_is_optional_string(self) -> None:
        """Test that assertion_message field accepts string or None."""
        # Should accept string
        signal = TestSignal(status="failing", assertion_message="assert True == False")
        assert isinstance(signal.assertion_message, str)

        # Should accept None
        signal = TestSignal(status="passing")
        assert signal.assertion_message is None

    def test_test_names_is_optional_list(self) -> None:
        """Test that test_names field accepts list of strings or None."""
        # Should accept list of strings
        signal = TestSignal(status="failing", test_names=["test_a", "test_b"])
        assert isinstance(signal.test_names, list)
        assert len(signal.test_names) == 2

        # Should accept None
        signal = TestSignal(status="passing")
        assert signal.test_names is None

    def test_all_new_fields_optional(self) -> None:
        """Test that all new fields are truly optional."""
        # Should work with only status
        signal = TestSignal(status="unavailable")
        assert signal.status == "unavailable"
        assert signal.test_name is None
        assert signal.assertion_message is None
        assert signal.test_names is None
