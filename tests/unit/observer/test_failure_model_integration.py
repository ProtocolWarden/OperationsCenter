# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for test_name and assertion_message flow through failure models."""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.observer.assertion_extractor import (
    clean_assertion_message,
)
from operations_center.observer.flaky_test_models import (
    FlakyTestMetric,
    FlakyTestResult,
    FlakynessCategory,
    TestOutcome,
)
from operations_center.observer.flaky_test_reporter import FlakyTestReporter
from operations_center.observer.models import TestSignal


class TestFailureModelIntegration:
    """Test integration of test_name and assertion_message through failure models."""

    def test_assertion_message_extraction_and_storage(self) -> None:
        """Test that assertion messages are extracted and stored correctly."""
        message = "assert 5 == 3: Values do not match"
        cleaned = clean_assertion_message(message)
        # clean_assertion_message removes "assert " prefix since pytest adds it
        assert cleaned == "5 == 3: Values do not match"

    def test_long_assertion_message_truncation(self) -> None:
        """Test that very long assertion messages are truncated to 200 chars."""
        long_message = "x" * 300
        cleaned = clean_assertion_message(long_message)
        assert len(cleaned) <= 200 + 3  # 200 + "..."

    def test_flaky_test_result_with_extraction_fields(self) -> None:
        """Test FlakyTestResult stores test_name and assertion_message."""
        result = FlakyTestResult(
            nodeid="tests/test_foo.py::test_addition",
            outcome=TestOutcome.FAILED,
            duration=1.5,
            run_id="run-123",
            test_name="test_addition",
            assertion_message="assert 2 + 2 == 5",
            exception_type="AssertionError",
            exception_message="2 + 2 != 5",
        )

        assert result.test_name == "test_addition"
        assert result.assertion_message == "assert 2 + 2 == 5"

    def test_flaky_test_metric_aggregates_test_names(self, tmp_path: Path) -> None:
        """Test that FlakyTestMetric aggregates test names from results."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        # Add multiple runs with the same test
        runs = [
            FlakyTestResult(
                nodeid="tests/test_math.py::test_add",
                outcome=TestOutcome.PASSED,
                duration=0.1,
                run_id="run-1",
                test_name="test_add",
            ),
            FlakyTestResult(
                nodeid="tests/test_math.py::test_add",
                outcome=TestOutcome.FAILED,
                duration=0.15,
                run_id="run-2",
                test_name="test_add",
                assertion_message="assert 1 + 1 == 3",
                exception_type="AssertionError",
                exception_message="1 + 1 != 3",
            ),
            FlakyTestResult(
                nodeid="tests/test_math.py::test_add",
                outcome=TestOutcome.PASSED,
                duration=0.1,
                run_id="run-3",
                test_name="test_add",
            ),
        ]

        for run in runs:
            reporter.track_test(run)

        # Analyze
        report = reporter.analyze_session()
        assert report.flaky_candidates or report.unstable_candidates

        # Check the metric has test_name and assertion_message
        metric = (
            report.flaky_candidates[0] if report.flaky_candidates else report.unstable_candidates[0]
        )
        assert metric.test_name == "test_add"
        assert metric.assertion_message == "assert 1 + 1 == 3"

    def test_flaky_test_metric_with_multiple_failures(self, tmp_path: Path) -> None:
        """Test that FlakyTestMetric captures last failure reason correctly."""
        reporter = FlakyTestReporter.create_local(tmp_path)

        runs = [
            FlakyTestResult(
                nodeid="tests/test_network.py::test_timeout",
                outcome=TestOutcome.FAILED,
                duration=30.0,
                run_id="run-1",
                test_name="test_timeout",
                assertion_message="Connection timeout after 30s",
                exception_type="TimeoutError",
                exception_message="Request timed out",
            ),
            FlakyTestResult(
                nodeid="tests/test_network.py::test_timeout",
                outcome=TestOutcome.PASSED,
                duration=1.0,
                run_id="run-2",
                test_name="test_timeout",
            ),
            FlakyTestResult(
                nodeid="tests/test_network.py::test_timeout",
                outcome=TestOutcome.FAILED,
                duration=30.0,
                run_id="run-3",
                test_name="test_timeout",
                assertion_message="Connection timeout after 30s",
                exception_type="TimeoutError",
                exception_message="Request timed out",
            ),
        ]

        for run in runs:
            reporter.track_test(run)

        report = reporter.analyze_session()
        metric = (
            report.flaky_candidates[0] if report.flaky_candidates else report.unstable_candidates[0]
        )

        assert metric.test_name == "test_timeout"
        assert metric.assertion_message == "Connection timeout after 30s"
        assert "TimeoutError" in metric.last_failure_reason

    def test_test_signal_with_extracted_fields(self) -> None:
        """Test TestSignal model includes test_name and assertion_message."""
        signal = TestSignal(
            status="failing",
            test_count=10,
            failed_count=3,
            test_name="test_database_query",
            assertion_message="Database connection failed: timeout",
            test_names=["test_database_query", "test_cache_lookup"],
            failure_category="timeout",
            source="pytest",
        )

        assert signal.test_name == "test_database_query"
        assert signal.assertion_message == "Database connection failed: timeout"
        assert signal.test_names == ["test_database_query", "test_cache_lookup"]
        assert signal.failure_category == "timeout"

    def test_flaky_test_result_to_dict_includes_new_fields(self, tmp_path: Path) -> None:
        """Test that FlakyTestMetric.to_dict() includes test_name and assertion_message."""
        metric = FlakyTestMetric(
            nodeid="tests/test_api.py::test_get_user",
            failure_rate=0.5,
            run_count=4,
            test_name="test_get_user",
            assertion_message="Expected status 200 but got 500",
            suspected_category=FlakynessCategory.INTERMITTENT,
            flakiness_score=0.65,
            confidence=0.8,
        )

        result_dict = metric.to_dict()

        assert result_dict["test_name"] == "test_get_user"
        assert result_dict["assertion_message"] == "Expected status 200 but got 500"
        assert result_dict["nodeid"] == "tests/test_api.py::test_get_user"

    def test_flaky_test_metric_json_serialization(self, tmp_path: Path) -> None:
        """Test that FlakyTestMetric can be serialized to JSON with new fields."""
        metric = FlakyTestMetric(
            nodeid="tests/test_service.py::TestService::test_create",
            failure_rate=0.25,
            run_count=8,
            test_name="test_create",
            assertion_message="Service returned error: invalid input",
            suspected_category=FlakynessCategory.UNKNOWN,
            flakiness_score=0.4,
            confidence=0.75,
        )

        # Serialize to dict
        data = metric.to_dict()

        # Try to serialize to JSON
        json_str = json.dumps(data)
        assert json_str is not None

        # Deserialize
        deserialized = json.loads(json_str)
        assert deserialized["test_name"] == "test_create"
        assert deserialized["assertion_message"] == "Service returned error: invalid input"

    def test_multiple_test_names_in_signal(self) -> None:
        """Test TestSignal with multiple test names for aggregates."""
        test_names = ["test_foo", "test_bar", "test_baz"]
        signal = TestSignal(
            status="failing",
            test_count=30,
            failed_count=3,
            test_name="test_foo",
            test_names=test_names,
            assertion_message="assert result == expected",
        )

        assert signal.test_names == test_names
        assert signal.test_name == "test_foo"
        assert len(signal.test_names) == 3

    def test_backward_compatibility_without_new_fields(self) -> None:
        """Test that models work without new fields (backward compatibility)."""
        # Old code that doesn't set new fields should still work
        signal = TestSignal(
            status="passing",
            test_count=100,
            passed_count=100,
            source="pytest",
        )

        assert signal.status == "passing"
        assert signal.test_name is None
        assert signal.assertion_message is None
        assert signal.test_names is None

    def test_flaky_test_result_minimal_required_fields(self) -> None:
        """Test FlakyTestResult with minimal required fields."""
        result = FlakyTestResult(
            nodeid="tests/test_basic.py::test_simple",
            outcome=TestOutcome.PASSED,
            duration=0.5,
            run_id="run-123",
        )

        # Should have optional fields as empty/default
        assert result.nodeid == "tests/test_basic.py::test_simple"
        assert result.outcome == TestOutcome.PASSED
        assert result.test_name == ""
        assert result.assertion_message == ""


class TestAssertionMessageExtractionFlow:
    """Test the flow of assertion messages from extraction to storage."""

    def test_clean_assertion_message_whitespace_collapse(self) -> None:
        """Test that whitespace is properly collapsed."""
        messy = "assert   x   ==    y\n\n  value mismatch"
        cleaned = clean_assertion_message(messy)
        # Should collapse multiple spaces/newlines
        assert "  " not in cleaned
        assert "\n" not in cleaned

    def test_clean_assertion_message_special_chars(self) -> None:
        """Test handling of special characters."""
        message = "assert 'hello' == 'world' (special: @#$%^)"
        cleaned = clean_assertion_message(message)
        assert len(cleaned) <= 200 + 3

    def test_clean_assertion_message_unicode(self) -> None:
        """Test handling of unicode characters."""
        message = "assert '你好' == '世界' — values don't match"
        cleaned = clean_assertion_message(message)
        assert "你好" in cleaned
        assert len(cleaned) <= 200 + 3

    def test_clean_assertion_message_empty(self) -> None:
        """Test handling of empty messages."""
        assert clean_assertion_message("") == ""
        assert clean_assertion_message("   ") == ""


class TestFailureModelDataFlow:
    """Test complete data flow through failure categorization models."""

    def test_flaky_metric_to_test_signal_conversion(self) -> None:
        """Test converting FlakyTestMetric data to TestSignal."""
        metric = FlakyTestMetric(
            nodeid="tests/test_payment.py::test_charge",
            failure_rate=0.4,
            run_count=10,
            test_name="test_charge",
            assertion_message="Payment failed: insufficient funds",
            suspected_category=FlakynessCategory.INFRASTRUCTURE,
            flakiness_score=0.55,
            confidence=0.85,
        )

        # Convert to TestSignal-like data
        signal = TestSignal(
            status="flaky",
            test_count=10,
            failed_count=4,
            passed_count=6,
            test_name=metric.test_name,
            assertion_message=metric.assertion_message,
            failure_category="infrastructure",
            source="flaky-test-reporter",
        )

        assert signal.test_name == "test_charge"
        assert signal.assertion_message == "Payment failed: insufficient funds"
        assert signal.failure_category == "infrastructure"

    def test_aggregate_multiple_metrics_into_signal(self) -> None:
        """Test aggregating multiple FlakyTestMetric into TestSignal."""
        metrics = [
            FlakyTestMetric(
                nodeid="tests/test_api.py::test_endpoint_1",
                failure_rate=0.5,
                run_count=4,
                test_name="test_endpoint_1",
                assertion_message="HTTP 503: Service unavailable",
            ),
            FlakyTestMetric(
                nodeid="tests/test_api.py::test_endpoint_2",
                failure_rate=0.25,
                run_count=8,
                test_name="test_endpoint_2",
                assertion_message="Connection timeout",
            ),
        ]

        # Create aggregate signal
        test_names = [m.test_name for m in metrics]
        primary_test = metrics[0].test_name
        primary_assertion = metrics[0].assertion_message

        signal = TestSignal(
            status="failing",
            test_count=12,
            failed_count=6,
            test_name=primary_test,
            assertion_message=primary_assertion,
            test_names=test_names,
        )

        assert signal.test_name == "test_endpoint_1"
        assert signal.test_names == ["test_endpoint_1", "test_endpoint_2"]
        assert len(signal.test_names) == 2
