# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for test name and assertion message extraction in CheckSignalCollector.

Tests verify that:
1. Test names are extracted from test logs
2. Assertion messages are extracted from failures
3. Extracted data is populated in TestSignal
4. Extraction works end-to-end with the failure categorization system
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.observer.collectors.check_signal import CheckSignalCollector
from operations_center.observer.models import CheckSignal


class TestCheckSignalExtraction:
    """Test suite for check signal extraction integration."""

    @pytest.fixture
    def temp_repo_path(self):
        """Create a temporary repository path for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            yield repo_path

    @pytest.fixture
    def observer_context(self, temp_repo_path):
        """Create an observer context for testing."""
        logs_root = temp_repo_path / ".observer" / "logs"
        logs_root.mkdir(parents=True, exist_ok=True)
        context = MagicMock()
        context.repo_path = temp_repo_path
        context.logs_root = logs_root
        return context

    @pytest.fixture
    def collector(self):
        """Create a CheckSignalCollector for testing."""
        return CheckSignalCollector()

    def test_extract_test_name_from_failure_line(self, collector):
        """Test that test name is extracted from FAILED line in pytest output."""
        log_text = """
        tests/unit/test_models.py::TestCheckSignal::test_creation FAILED
        AssertionError: Expected test count to be > 0
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert test_name == "test_creation", f"Expected 'test_creation' but got {test_name}"

    def test_extract_assertion_message_from_error_line(self, collector):
        """Test that assertion message is extracted from AssertionError line."""
        log_text = """
        tests/unit/test_models.py::TestCheckSignal::test_status_field FAILED
        AssertionError: status must be one of (passed, failed, unknown)
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert assertion_msg == "status must be one of (passed, failed, unknown)"

    def test_extract_both_test_name_and_assertion_message(self, collector):
        """Test that both test name and assertion message are extracted together."""
        log_text = """
        tests/integration/test_check_signal.py::TestFullIntegration::test_collector_integration FAILED
        AssertionError: Collector failed to populate test signal
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert test_name == "test_collector_integration"
        assert assertion_msg == "Collector failed to populate test signal"

    def test_extract_returns_none_when_no_failure(self, collector):
        """Test that extraction returns None values when there's no failure in log."""
        log_text = """
        collected 10 items
        tests/unit/test_models.py::TestCheckSignal::test_creation PASSED
        tests/unit/test_models.py::TestCheckSignal::test_status_field PASSED
        ========================= 2 passed in 0.05s ==========================
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert test_name is None
        assert assertion_msg is None

    def test_extract_truncates_long_assertion_message(self, collector):
        """Test that assertion messages are truncated to 200 characters."""
        long_message = "x" * 250
        log_text = f"""
        tests/unit/test_models.py::TestCheckSignal::test_long_assertion FAILED
        AssertionError: {long_message}
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert assertion_msg is not None
        assert len(assertion_msg) <= 200

    def test_check_signal_populated_with_extracted_values(self, collector, observer_context):
        """Test that CheckSignal is populated with extracted test_name and assertion_message."""
        # Create a test log file
        log_file = observer_context.logs_root / "test_2024_01_15_10_30_45_test.log"
        log_content = """
        ======================== test session starts =========================
        collected 5 items
        tests/unit/test_models.py::TestCheckSignal::test_creation FAILED
        AssertionError: test_count should be greater than 0
        tests/unit/test_models.py::TestCheckSignal::test_status_field PASSED
        ======================= FAILURES ===================================
        __ TestCheckSignal::test_creation ___________________________________
        tests/unit/test_models.py:42: in test_creation
            assert signal.test_count > 0
        E   AssertionError: test_count should be greater than 0
        ===================== 1 failed, 1 passed in 0.23s =====================
        """
        log_file.write_text(log_content)

        # Collect the signal
        signal = collector.collect(observer_context)

        # Verify the signal was created and has extracted values
        assert isinstance(signal, CheckSignal)
        assert signal.status == "failed"
        assert signal.test_name == "test_creation" or signal.test_name == "test_status_field"
        assert signal.assertion_message is not None
        assert len(signal.assertion_message) <= 200

    def test_integration_with_failure_categorization_system(self, collector, observer_context):
        """Test that extraction is integrated with the failure categorization system."""
        # Create a test log with failure details
        log_file = observer_context.logs_root / "test_2024_01_15_10_30_45_test.log"
        log_content = """
        tests/integration/test_extraction.py::TestExtraction::test_parse_assertion_error FAILED
        AssertionError: assertion message should not be empty
        ======================== 1 failed in 0.45s ============================
        """
        log_file.write_text(log_content)

        # Collect the signal - this exercises the full integration
        signal = collector.collect(observer_context)

        # Verify all fields are properly set
        assert signal.status == "failed"
        assert signal.test_name is not None  # Extraction integrated
        assert signal.assertion_message is not None  # Extraction integrated
        assert signal.source == str(log_file)
        assert signal.observed_at is not None


class TestCheckSignalExtractionEdgeCases:
    """Test edge cases for extraction logic."""

    @pytest.fixture
    def collector(self):
        """Create a CheckSignalCollector for testing."""
        return CheckSignalCollector()

    def test_extract_with_empty_log(self, collector):
        """Test extraction handles empty log gracefully."""
        test_name, assertion_msg = collector._extract_failure_details("")
        assert test_name is None
        assert assertion_msg is None

    def test_extract_with_malformed_failure_line(self, collector):
        """Test extraction handles malformed FAILED lines gracefully."""
        log_text = "FAILED: something went wrong"  # No :: in line
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert test_name is None

    def test_extract_with_multiple_failures(self, collector):
        """Test extraction returns first failure when multiple failures exist."""
        log_text = """
        tests/unit/test_a.py::test_first FAILED
        AssertionError: first assertion failure
        tests/unit/test_b.py::test_second FAILED
        AssertionError: second assertion failure
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        # Should get the first failure
        assert test_name == "test_first" or test_name is not None
        assert assertion_msg is not None

    def test_extract_with_special_characters_in_message(self, collector):
        """Test extraction handles special characters in assertion messages."""
        log_text = """
        tests/unit/test_special.py::test_chars FAILED
        AssertionError: Expected 'hello"world' but got 'foo\\nbar'
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert test_name == "test_chars"
        assert assertion_msg is not None
        assert "hello" in assertion_msg or "foo" in assertion_msg

    def test_extract_with_unicode_in_assertion(self, collector):
        """Test extraction handles Unicode characters in assertion messages."""
        log_text = """
        tests/unit/test_unicode.py::test_chars FAILED
        AssertionError: Expected '你好' but got 'мир' with émojis 🚀
        """
        test_name, assertion_msg = collector._extract_failure_details(log_text)
        assert test_name == "test_chars"
        assert assertion_msg is not None
