# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for LintSignalCollector with malformed payload hardening.

Note: Tests are written using the actual ruff output format (location.row/column).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from operations_center.observer.collectors.lint_signal import LintSignalCollector


def valid_lint_item():
    """Valid lint item matching validator expectations."""
    return {
        "filename": "test.py",
        "code": "E501",
        "message": "Line too long",
        "location": {
            "row": 1,
            "column": 0
        }
    }


class TestLintSignalParseErrors:
    """Tests for parse-level malformations (JSONDecodeError)."""

    def test_parse_error_trailing_comma(self):
        """P1: Trailing comma in JSON array."""
        malformed = '[{"filename": "test.py", "code": "E501",}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_missing_colon(self):
        """P2: Missing colon between key and value."""
        malformed = '[{"filename" "test.py"}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_single_quotes(self):
        """P3: Single quotes instead of double quotes."""
        malformed = "[{'filename': 'test.py'}]"
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_unquoted_key(self):
        """P4: Unquoted string key."""
        malformed = '[{filename: "test.py"}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_unclosed_brace(self):
        """P5: Unclosed brace/bracket."""
        malformed = '[{"filename": "test.py"'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_unclosed_string(self):
        """P6: Unclosed string."""
        malformed = '[{"filename": "test.py}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_invalid_escape(self):
        """P7: Invalid escape sequence."""
        malformed = r'[{"message": "line\q"}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_extra_commas(self):
        """P8: Extra commas in array."""
        malformed = '[{"code": "E501"},,{"code": "E502"}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_truncated_json(self):
        """P9: Truncated JSON payload."""
        malformed = '[{"filename": "test'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_parse_error"

    def test_parse_error_nan_value(self):
        """P10: NaN/Infinity values (not JSON compliant)."""
        # Note: Python 3.14's json.loads() accepts NaN by default,
        # but it's rejected by the validator since it's not an integer
        malformed = '[{"filename": "test.py", "location": {"row": NaN}}]'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        # NaN is parsed as float, but validator rejects non-int row
        assert signal.status == "clean"
        assert signal.violation_count == 1
        assert len(signal.top_violations) == 0  # Item rejected due to NaN


class TestLintSignalStructureErrors:
    """Tests for structure-level malformations (validation errors)."""

    def test_structure_error_wrong_root_type_string(self):
        """S2: Wrong root type (string instead of array)."""
        malformed = '"not_an_array"'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_unexpected_format"

    def test_structure_error_wrong_root_type_object(self):
        """S2: Wrong root type (object instead of array)."""
        malformed = '{"filename": "test.py"}'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_unexpected_format"

    def test_structure_error_wrong_root_type_number(self):
        """S2: Wrong root type (number instead of array)."""
        malformed = '123'
        signal = LintSignalCollector._parse_ruff_output(malformed)
        assert signal.status == "unavailable"
        assert signal.source == "ruff_unexpected_format"

    def test_structure_error_missing_required_field_filename(self):
        """S1: Missing required field (filename)."""
        payload = [{"code": "E501", "message": "Line too long"}]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        # Item is invalid, should skip it
        assert signal.violation_count == 1
        assert signal.status == "clean"  # No valid violations

    def test_structure_error_missing_required_field_code(self):
        """S1: Missing required field (code)."""
        payload = [{"filename": "test.py", "message": "Line too long"}]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        assert signal.violation_count == 1
        assert signal.status == "clean"  # No valid violations

    def test_structure_error_missing_required_field_location(self):
        """S1: Missing required field (location)."""
        payload = [{"filename": "test.py", "code": "E501", "message": "Line too long"}]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        assert signal.violation_count == 1
        # Item is rejected by validator due to missing location
        assert signal.status == "clean"  # No valid violations
        assert len(signal.top_violations) == 0

    def test_structure_error_type_mismatch_filename_not_string(self):
        """S5: Type mismatch (filename should be string)."""
        payload = [{
            "filename": 123,  # Should be string, not int
            "code": "E501",
            "message": "error",
            "location": {"row": 1, "column": 0}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        # Invalid item should be skipped
        assert signal.violation_count == 1
        assert len(signal.top_violations) == 0  # Item rejected by validator

    def test_structure_error_type_mismatch_code_not_string(self):
        """S5: Type mismatch (code should be string)."""
        payload = [{
            "filename": "test.py",
            "code": 123,  # Should be string
            "message": "error",
            "location": {"row": 1, "column": 0}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        assert signal.violation_count == 1

    def test_structure_error_null_in_required_field(self):
        """S6: Null in required field."""
        payload = [{
            "filename": None,  # Should not be null
            "code": "E501",
            "message": "error",
            "location": {"row": 1, "column": 0}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        assert signal.violation_count == 1
        # Should be skipped due to null filename

    def test_structure_error_invalid_location_type(self):
        """S2: Wrong type for location (should be object)."""
        payload = [{
            "filename": "test.py",
            "code": "E501",
            "message": "error",
            "location": "not_an_object"  # Should be object with row/column
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        # Should still process but with defaults
        assert signal.violation_count == 1

    def test_structure_error_empty_filename_string(self):
        """S8: Empty required string (filename)."""
        payload = [{
            "filename": "",  # Empty string
            "code": "E501",
            "message": "error",
            "location": {"row": 1, "column": 0}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        # Empty filename is accepted but might produce invalid violation
        assert signal.violation_count == 1

    def test_structure_error_negative_row_value(self):
        """S7: Out-of-range value (row should be >= 1)."""
        payload = [{
            "filename": "test.py",
            "code": "E501",
            "message": "error",
            "location": {"row": -1, "column": 0}  # Negative row
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        assert signal.violation_count == 1
        # Should be accepted but with negative value (graceful degradation)

    def test_structure_error_missing_message_field(self):
        """S1: Missing optional message field (handled gracefully)."""
        payload = [{
            "filename": "test.py",
            "code": "E501",
            "location": {"row": 1, "column": 0}
            # Missing message field - but validator doesn't require it
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(payload))
        assert signal.violation_count == 1
        # Item is valid according to validator
        assert signal.status == "violations"
        assert len(signal.top_violations) == 1


class TestLintSignalEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_output(self):
        """E?: Empty ruff output."""
        signal = LintSignalCollector._parse_ruff_output("")
        assert signal.status == "clean"
        assert signal.violation_count == 0

    def test_empty_array(self):
        """E?: Empty JSON array (no violations)."""
        signal = LintSignalCollector._parse_ruff_output("[]")
        assert signal.status == "clean"
        assert signal.violation_count == 0

    def test_large_violation_count(self):
        """E1: Large payload (many violations)."""
        # Create a large payload with many items
        items = []
        for i in range(100):
            items.append({
                "filename": f"test{i}.py",
                "code": f"E{i:03d}",
                "message": f"error {i}",
                "location": {"row": i + 1, "column": 0}
            })
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 100
        # But only top _MAX_VIOLATIONS are returned (default 20)
        assert len(signal.top_violations) <= 20

    def test_deeply_nested_location(self):
        """E2: Deeply nested location object with extra fields."""
        items = [{
            "filename": "test.py",
            "code": "E501",
            "message": "error",
            "location": {
                "row": 1,
                "column": 0,
                "nested": {
                    "deep": {
                        "path": {
                            "value": 42
                        }
                    }
                }
            }
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 1
        assert signal.status == "violations"
        assert len(signal.top_violations) == 1

    def test_mixed_valid_and_invalid_items(self):
        """Mixed valid/invalid items in output."""
        items = [
            {"filename": "test1.py", "code": "E501", "message": "error", "location": {"row": 1, "column": 0}},  # Valid
            {"filename": "test2.py"},  # Missing code and location
            {"filename": "test3.py", "code": "E502", "message": "error", "location": {"row": 3, "column": 0}},  # Valid
        ]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 3
        # Should have 2 valid violations (first and third items)
        assert len(signal.top_violations) == 2

    def test_very_long_field_values(self):
        """E5: Very long string values."""
        long_message = "x" * 10000
        items = [{
            "filename": "test.py",
            "code": "E501",
            "message": long_message,
            "location": {"row": 1, "column": 0}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 1
        assert signal.status == "violations"
        assert len(signal.top_violations[0].message) == 10000

    def test_extra_unknown_fields(self):
        """S10: Extra unknown fields (should be ignored)."""
        items = [{
            "filename": "test.py",
            "code": "E501",
            "message": "error",
            "location": {"row": 1, "column": 0},
            "extra_field_1": "ignored",
            "extra_field_2": 123,
            "extra_field_3": {"nested": "value"}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 1
        assert signal.status == "violations"
        assert len(signal.top_violations) == 1

    def test_unicode_in_message(self):
        """E4: Unicode characters in message."""
        items = [{
            "filename": "test.py",
            "code": "E501",
            "message": "Error: émojis 🎉 and special chars: ñ, é, ü",
            "location": {"row": 1, "column": 0}
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 1
        assert len(signal.top_violations) == 1
        assert "🎉" in signal.top_violations[0].message

    def test_whitespace_in_json(self):
        """Whitespace handling in JSON."""
        items = [
            {
                "filename": "test.py",
                "code": "E501",
                "message": "error",
                "location": {"row": 1, "column": 0}
            }
        ]
        # Pretty-printed JSON
        formatted = json.dumps(items, indent=2)
        signal = LintSignalCollector._parse_ruff_output(formatted)
        assert signal.violation_count == 1
        assert signal.status == "violations"
        assert len(signal.top_violations) == 1

    def test_zero_values(self):
        """Boundary: Zero values in numeric fields are valid."""
        items = [{
            "filename": "test.py",
            "code": "E501",
            "message": "error",
            "location": {"row": 0, "column": 0}  # Both 0 are valid (0-1000000)
        }]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        assert signal.violation_count == 1
        # Item is valid; both row and column are allowed to be 0
        assert signal.status == "violations"
        assert len(signal.top_violations) == 1
        assert signal.top_violations[0].line == 0
        assert signal.top_violations[0].col == 0

    def test_distinct_file_count_calculation(self):
        """Distinct file count correctly calculated."""
        items = [
            {"filename": "test1.py", "code": "E501", "message": "error", "location": {"row": 1, "column": 0}},
            {"filename": "test1.py", "code": "E502", "message": "error", "location": {"row": 2, "column": 0}},
            {"filename": "test2.py", "code": "E503", "message": "error", "location": {"row": 1, "column": 0}},
            {"code": "E504", "message": "error", "location": {"row": 1, "column": 0}},  # No filename - rejected by validator
        ]
        signal = LintSignalCollector._parse_ruff_output(json.dumps(items))
        # Should have 2 distinct files (test1.py, test2.py; item without filename is excluded)
        assert signal.distinct_file_count == 2
        # 3 valid violations + 1 invalid
        assert signal.violation_count == 4
        assert len(signal.top_violations) == 3


class TestLintSignalCollectorIntegration:
    """Integration tests for LintSignalCollector with subprocess."""

    def test_ruff_not_found(self):
        """Handles case when ruff is not installed."""
        context = MagicMock()
        context.repo_path = "/tmp/test"

        with patch("subprocess.run", side_effect=FileNotFoundError()):
            collector = LintSignalCollector()
            signal = collector.collect(context)

        assert signal.status == "unavailable"
        assert signal.source == "ruff_not_found"

    def test_ruff_timeout(self):
        """Handles ruff timeout."""
        context = MagicMock()
        context.repo_path = "/tmp/test"

        with patch("subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("ruff", 60)):
            collector = LintSignalCollector()
            signal = collector.collect(context)

        assert signal.status == "unavailable"
        assert signal.source == "ruff_timeout"

    def test_ruff_general_error(self):
        """Handles general ruff execution error."""
        context = MagicMock()
        context.repo_path = "/tmp/test"

        with patch("subprocess.run", side_effect=RuntimeError("Unexpected error")):
            collector = LintSignalCollector()
            signal = collector.collect(context)

        assert signal.status == "unavailable"
        assert "ruff_error" in signal.source

    def test_successful_collection_clean(self):
        """Successfully collects clean lint signal."""
        context = MagicMock()
        context.repo_path = "/tmp/test"

        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            collector = LintSignalCollector()
            signal = collector.collect(context)

        assert signal.status == "clean"
        assert signal.violation_count == 0

    def test_successful_collection_with_violations(self):
        """Successfully collects lint signal with violations."""
        context = MagicMock()
        context.repo_path = "/tmp/test"

        # Use validator-compatible format with location.start
        violations_data = [
            {"filename": "test.py", "code": "E501", "message": "Line too long", "location": {"row": 1, "column": 88}},
            {"filename": "test.py", "code": "F841", "message": "Unused variable", "location": {"row": 5, "column": 0}},
        ]

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(violations_data)

        with patch("subprocess.run", return_value=mock_result):
            collector = LintSignalCollector()
            signal = collector.collect(context)

        assert signal.status == "violations"
        assert signal.violation_count == 2
        assert len(signal.top_violations) == 2
