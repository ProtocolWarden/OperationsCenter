# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for test name and assertion message extraction.

Tests the end-to-end flow from pytest test failures through extraction,
aggregation, and categorization in the observer pipeline.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from operations_center.observer.assertion_extractor import extract_assertion_from_excinfo
from operations_center.observer.pytest_flaky_plugin import FlakyTestDetectionPlugin


def _call_info(when: str = "call", excinfo=None, duration: float = 0.5):
    return SimpleNamespace(when=when, excinfo=excinfo, duration=duration)


def _item(nodeid: str, function=None):
    return SimpleNamespace(nodeid=nodeid, function=function)


class TestExtractionIntegration:
    """Integration tests for extraction pipeline."""

    def test_extract_test_name_and_assertion_together(self) -> None:
        """Test that we can extract test name and assertion message together."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin = FlakyTestDetectionPlugin(tmp_dir)

            def test_addition():
                pass

            item = _item("tests/math.py::test_addition", test_addition)
            exc = SimpleNamespace(value=AssertionError("2 + 2 should be 4, got 5"), traceback=None)
            call = _call_info(excinfo=exc)

            plugin.pytest_runtest_makereport(item, call)

            entry = plugin.test_outcomes["tests/math.py::test_addition"]
            assert entry["test_function"] == "test_addition"
            assert "4" in entry["assertion_message"]
            assert "5" in entry["assertion_message"]

    def test_extract_from_multiple_tests_with_different_failures(self) -> None:
        """Test extraction from multiple failing tests with different error types."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin = FlakyTestDetectionPlugin(tmp_dir)
            plugin.pytest_sessionstart(session=SimpleNamespace(name="test"))

            # Test 1: AssertionError
            def test_assert():
                pass

            item1 = _item("tests/test_suite.py::test_assert", test_assert)
            exc1 = SimpleNamespace(value=AssertionError("Expected True"), traceback=None)
            plugin.pytest_runtest_makereport(item1, _call_info(excinfo=exc1))

            # Test 2: TimeoutError
            def test_timeout():
                pass

            item2 = _item("tests/test_suite.py::test_timeout", test_timeout)
            exc2 = SimpleNamespace(value=TimeoutError("Timeout after 30s"), traceback=None)
            plugin.pytest_runtest_makereport(item2, _call_info(excinfo=exc2))

            # Test 3: ValueError
            def test_value():
                pass

            item3 = _item("tests/test_suite.py::test_value", test_value)
            exc3 = SimpleNamespace(value=ValueError("Invalid value: -1"), traceback=None)
            plugin.pytest_runtest_makereport(item3, _call_info(excinfo=exc3))

            assert len(plugin.test_outcomes) == 3
            assert plugin.test_outcomes["tests/test_suite.py::test_assert"]["test_function"] == "test_assert"
            assert plugin.test_outcomes["tests/test_suite.py::test_timeout"]["test_function"] == "test_timeout"
            assert plugin.test_outcomes["tests/test_suite.py::test_value"]["test_function"] == "test_value"

            # Check that assertion messages were extracted
            assert plugin.test_outcomes["tests/test_suite.py::test_assert"]["assertion_message"]
            assert plugin.test_outcomes["tests/test_suite.py::test_timeout"]["assertion_message"]
            assert plugin.test_outcomes["tests/test_suite.py::test_value"]["assertion_message"]

    def test_session_report_generation_with_extraction_data(self) -> None:
        """Test that session reports include extracted test names and assertions."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_path = Path(tmp_dir) / "flaky"
            plugin = FlakyTestDetectionPlugin(str(storage_path))
            plugin.pytest_sessionstart(session=SimpleNamespace(name="session"))

            def test_example():
                pass

            item = _item("tests/example.py::test_example", test_example)
            exc = SimpleNamespace(value=AssertionError("Assertion message"), traceback=None)
            plugin.pytest_runtest_makereport(item, _call_info(excinfo=exc))

            plugin.pytest_sessionfinish(session=SimpleNamespace(name="test-session"), exitstatus=1)

            # Read and verify the generated report
            reports = list(storage_path.glob("runs/*/*-session.json"))
            assert len(reports) == 1

            report = json.loads(reports[0].read_text(encoding="utf-8"))
            assert len(report["test_outcomes"]) == 1

            outcome = report["test_outcomes"][0]
            assert outcome["test_function"] == "test_example"
            assert outcome["assertion_message"] == "Assertion message"
            assert outcome["outcome"] == "failed"

    def test_extraction_preserves_data_through_report_serialization(self) -> None:
        """Test that extracted data survives JSON serialization."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_path = Path(tmp_dir) / "flaky"
            plugin = FlakyTestDetectionPlugin(str(storage_path))
            plugin.pytest_sessionstart(session=SimpleNamespace(name="session"))

            # Test with special characters to ensure proper serialization
            def test_unicode():
                pass

            item = _item("tests/unicode.py::test_unicode", test_unicode)
            exc = SimpleNamespace(
                value=AssertionError("Expected δεσμός but got δεσμό"),
                traceback=None
            )
            plugin.pytest_runtest_makereport(item, _call_info(excinfo=exc))

            plugin.pytest_sessionfinish(session=SimpleNamespace(name="test-session"), exitstatus=1)

            # Read and verify data integrity
            reports = list(storage_path.glob("runs/*/*-session.json"))
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            outcome = report["test_outcomes"][0]

            assert outcome["test_function"] == "test_unicode"
            assert "δεσμός" in outcome["assertion_message"]

    def test_parameterized_test_extraction(self) -> None:
        """Test extraction from parameterized tests."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin = FlakyTestDetectionPlugin(tmp_dir)

            def test_parametrized(x, y):
                pass

            # Parameterized test nodeid includes parameters
            item = _item("tests/param.py::test_parametrized[2-3]", test_parametrized)
            exc = SimpleNamespace(value=AssertionError("2 + 3 != 6"), traceback=None)
            plugin.pytest_runtest_makereport(item, _call_info(excinfo=exc))

            # Should extract just the base function name, not parameters
            entry = plugin.test_outcomes["tests/param.py::test_parametrized[2-3]"]
            assert entry["test_function"] == "test_parametrized"
            assert "2 + 3" in entry["assertion_message"]

    def test_class_based_test_extraction(self) -> None:
        """Test extraction from class-based tests."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin = FlakyTestDetectionPlugin(tmp_dir)

            class TestExample:
                def test_method(self):
                    pass

            item = _item("tests/classes.py::TestExample::test_method", TestExample.test_method)
            exc = SimpleNamespace(value=AssertionError("Method assertion"), traceback=None)
            plugin.pytest_runtest_makereport(item, _call_info(excinfo=exc))

            entry = plugin.test_outcomes["tests/classes.py::TestExample::test_method"]
            assert entry["test_function"] == "test_method"
            assert entry["assertion_message"] == "Method assertion"

    def test_mixed_pass_fail_extraction(self) -> None:
        """Test extraction from a mix of passing and failing tests."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_path = Path(tmp_dir) / "flaky"
            plugin = FlakyTestDetectionPlugin(str(storage_path))
            plugin.pytest_sessionstart(session=SimpleNamespace(name="session"))

            # Passing test
            def test_pass():
                pass

            item1 = _item("tests/mixed.py::test_pass", test_pass)
            plugin.pytest_runtest_makereport(item1, _call_info(excinfo=None))

            # Failing test
            def test_fail():
                pass

            item2 = _item("tests/mixed.py::test_fail", test_fail)
            exc = SimpleNamespace(value=AssertionError("Failed"), traceback=None)
            plugin.pytest_runtest_makereport(item2, _call_info(excinfo=exc))

            plugin.pytest_sessionfinish(session=SimpleNamespace(name="test-session"), exitstatus=1)

            reports = list(storage_path.glob("runs/*/*-session.json"))
            report = json.loads(reports[0].read_text(encoding="utf-8"))

            assert len(report["test_outcomes"]) == 2
            outcomes_by_name = {o["test_function"]: o for o in report["test_outcomes"]}

            assert outcomes_by_name["test_pass"]["outcome"] == "passed"
            assert outcomes_by_name["test_pass"]["assertion_message"] == ""

            assert outcomes_by_name["test_fail"]["outcome"] == "failed"
            assert outcomes_by_name["test_fail"]["assertion_message"] == "Failed"


class TestExtractionErrorHandling:
    """Tests for error handling during extraction."""

    def test_extraction_handles_malformed_exception(self) -> None:
        """Test that extraction handles malformed exception gracefully."""
        exc = SimpleNamespace(value=None, tb=None)
        result = extract_assertion_from_excinfo(exc)
        # Should not crash
        assert result == ""

    def test_extraction_from_exception_without_message(self) -> None:
        """Test extraction from exception without args."""
        exc = RuntimeError()
        excinfo = SimpleNamespace(value=exc, tb=None)
        result = extract_assertion_from_excinfo(excinfo)
        # Should handle gracefully
        assert isinstance(result, str)

    def test_extraction_truncates_very_long_messages(self) -> None:
        """Test that extremely long messages are properly truncated."""
        long_msg = "x" * 1000
        exc = AssertionError(long_msg)
        excinfo = SimpleNamespace(value=exc, tb=None)
        result = extract_assertion_from_excinfo(excinfo)

        assert len(result) <= 200
        assert result.endswith("...")

    def test_extraction_handles_nested_attributes_gracefully(self) -> None:
        """Test extraction handles complex exception hierarchies."""
        try:
            try:
                raise ValueError("inner")
            except ValueError as e:
                raise RuntimeError("middle") from e
        except RuntimeError as e:
            excinfo = SimpleNamespace(value=e, tb=None)
            result = extract_assertion_from_excinfo(excinfo)
            # Should extract something from the exception chain
            assert isinstance(result, str)


class TestExtractionAccuracy:
    """Tests verifying accuracy of extraction."""

    def test_extraction_preserves_exact_message(self) -> None:
        """Test that short messages are preserved exactly."""
        msg = "Expected 42 but got 43"
        exc = AssertionError(msg)
        excinfo = SimpleNamespace(value=exc, tb=None)
        result = extract_assertion_from_excinfo(excinfo)
        assert result == msg

    def test_extraction_handles_assertion_message_with_newlines(self) -> None:
        """Test that newlines are properly converted to spaces."""
        msg = "Expected:\n42\nBut got:\n43"
        exc = AssertionError(msg)
        excinfo = SimpleNamespace(value=exc, tb=None)
        result = extract_assertion_from_excinfo(excinfo)

        # Should not have newlines
        assert "\n" not in result
        # Should preserve the core message
        assert "Expected" in result and "42" in result and "43" in result

    def test_extraction_test_name_from_various_formats(self) -> None:
        """Test test name extraction from various nodeid formats."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin = FlakyTestDetectionPlugin(tmp_dir)

            test_cases = [
                ("tests/test_simple.py::test_func", "test_func"),
                ("tests/nested/test_module.py::TestClass::test_method", "test_method"),
                ("tests/test_param.py::test_func[param1]", "test_func"),
                ("tests/test_multi.py::TestClass::test_method[a-b-c]", "test_method"),
            ]

            for idx, (nodeid, expected_name) in enumerate(test_cases):
                def test_fn():
                    pass

                item = _item(nodeid, test_fn)
                # Need to set function to have __name__ for extraction
                test_fn.__name__ = expected_name
                item.function = type("F", (), {"__name__": expected_name})()

                # Extract just the name
                name = plugin._extract_test_name(item)
                assert name == expected_name, f"Failed for nodeid: {nodeid}"
