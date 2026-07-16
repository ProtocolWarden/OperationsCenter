# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for extraction report formatter with JSON, table, and markdown outputs."""

from __future__ import annotations

import json


from operations_center.observer.extraction_report_formatter import ExtractionReportFormatter


class TestJsonFormatting:
    """Test JSON output formatting for extracted data."""

    def test_format_test_names_as_json_empty(self) -> None:
        """JSON for empty test names returns empty structure."""
        formatter = ExtractionReportFormatter()
        result = formatter.format_test_names_as_json(None)
        parsed = json.loads(result)

        assert parsed["test_names"] == []
        assert parsed["total_count"] == 0

    def test_format_test_names_as_json_single(self) -> None:
        """JSON for single test name."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_foo": 5}
        result = formatter.format_test_names_as_json(test_names)
        parsed = json.loads(result)

        assert len(parsed["test_names"]) == 1
        assert parsed["test_names"][0]["name"] == "test_foo"
        assert parsed["test_names"][0]["count"] == 5
        assert parsed["test_names"][0]["percentage"] == "100.0%"
        assert parsed["total_count"] == 5
        assert parsed["unique_tests"] == 1

    def test_format_test_names_as_json_multiple(self) -> None:
        """JSON for multiple test names, sorted by count."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_foo": 10, "test_bar": 5, "test_baz": 3}
        result = formatter.format_test_names_as_json(test_names)
        parsed = json.loads(result)

        # Should be sorted by count descending
        assert parsed["test_names"][0]["name"] == "test_foo"
        assert parsed["test_names"][0]["count"] == 10
        assert parsed["test_names"][1]["name"] == "test_bar"
        assert parsed["test_names"][1]["count"] == 5
        assert parsed["test_names"][2]["name"] == "test_baz"
        assert parsed["test_names"][2]["count"] == 3
        assert parsed["total_count"] == 18
        assert parsed["unique_tests"] == 3

    def test_format_test_names_as_json_percentages(self) -> None:
        """JSON percentages are calculated correctly."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_a": 3, "test_b": 6, "test_c": 1}
        result = formatter.format_test_names_as_json(test_names)
        parsed = json.loads(result)

        # 6/10 = 60%, 3/10 = 30%, 1/10 = 10%
        assert parsed["test_names"][0]["percentage"] == "60.0%"
        assert parsed["test_names"][1]["percentage"] == "30.0%"
        assert parsed["test_names"][2]["percentage"] == "10.0%"

    def test_format_assertion_messages_as_json_empty(self) -> None:
        """JSON for empty assertion messages returns empty structure."""
        formatter = ExtractionReportFormatter()
        result = formatter.format_assertion_messages_as_json(None)
        parsed = json.loads(result)

        assert parsed["assertion_messages"] == []
        assert parsed["total_count"] == 0

    def test_format_assertion_messages_as_json_multiple(self) -> None:
        """JSON for multiple assertion messages."""
        formatter = ExtractionReportFormatter()
        assertions = {
            "assert x > 0": 7,
            "assert y == expected": 3,
            "assert not error": 2,
        }
        result = formatter.format_assertion_messages_as_json(assertions)
        parsed = json.loads(result)

        assert len(parsed["assertion_messages"]) == 3
        assert parsed["assertion_messages"][0]["message"] == "assert x > 0"
        assert parsed["assertion_messages"][0]["count"] == 7
        assert parsed["total_count"] == 12
        assert parsed["unique_assertions"] == 3

    def test_format_assertion_messages_as_json_unicode(self) -> None:
        """JSON properly handles unicode characters in assertions."""
        formatter = ExtractionReportFormatter()
        assertions = {"assert α > β": 2, "expected → actual": 1}
        result = formatter.format_assertion_messages_as_json(assertions)
        parsed = json.loads(result)

        assert parsed["assertion_messages"][0]["message"] == "assert α > β"
        assert parsed["assertion_messages"][1]["message"] == "expected → actual"


class TestTableFormatting:
    """Test table output formatting for extracted data."""

    def test_format_test_names_as_table_empty(self) -> None:
        """Table for empty test names."""
        formatter = ExtractionReportFormatter()
        result = formatter.format_test_names_as_table(None)

        assert result == "No failing tests found."

    def test_format_test_names_as_table_single(self) -> None:
        """Table for single test name."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_foo": 5}
        result = formatter.format_test_names_as_table(test_names)

        assert "test_foo" in result
        assert "5" in result
        assert "100.0%" in result

    def test_format_test_names_as_table_multiple(self) -> None:
        """Table for multiple test names, sorted by count."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_foo": 10, "test_bar": 5}
        result = formatter.format_test_names_as_table(test_names)

        # Verify both tests are present
        assert "test_foo" in result
        assert "test_bar" in result
        # Verify test_foo comes before test_bar (sorted by count)
        assert result.index("test_foo") < result.index("test_bar")
        # Verify percentages
        assert "66.7%" in result or "66.6%" in result
        assert "33.3%" in result or "33.4%" in result

    def test_format_assertion_messages_as_table_empty(self) -> None:
        """Table for empty assertion messages."""
        formatter = ExtractionReportFormatter()
        result = formatter.format_assertion_messages_as_table(None)

        assert result == "No assertion failures found."

    def test_format_assertion_messages_as_table_truncate(self) -> None:
        """Table truncates long assertion messages."""
        formatter = ExtractionReportFormatter()
        long_msg = "x" * 100
        assertions = {long_msg: 3}
        result = formatter.format_assertion_messages_as_table(assertions)

        # Should be truncated
        assert "..." in result
        assert long_msg not in result

    def test_format_assertion_messages_as_table_multiple(self) -> None:
        """Table for multiple assertion messages."""
        formatter = ExtractionReportFormatter()
        assertions = {"assert a > b": 5, "assert c == d": 3}
        result = formatter.format_assertion_messages_as_table(assertions)

        assert "assert a > b" in result
        assert "assert c == d" in result

    def test_format_test_names_as_table_matches_diagnostics_doc_example(self) -> None:
        """Locks in the exact byte layout of docs/operator/diagnostics.md command 4's
        example — column widths are data-driven (name_width, count_width=len(str(total))),
        so this pins the specific 5/3/2/1 dataset the doc uses."""
        formatter = ExtractionReportFormatter()
        test_names = {
            "test_token_refresh": 5,
            "test_invalidation": 3,
            "test_timeout": 2,
            "test_retry": 1,
        }
        result = formatter.format_test_names_as_table(test_names)

        assert result == (
            "Test Name          │ Count │ Percentage\n"
            "────────────────────────────────\n"
            "test_token_refresh │  5 │  45.5%\n"
            "test_invalidation  │  3 │  27.3%\n"
            "test_timeout       │  2 │  18.2%\n"
            "test_retry         │  1 │   9.1%"
        )

    def test_format_assertion_messages_as_table_matches_diagnostics_doc_example(self) -> None:
        """Locks in the exact byte layout of docs/operator/diagnostics.md command 5's
        assertion-message example — the Percentage field width is fixed
        (len("100.0%")), so its padding never exceeds one leading space regardless
        of the data, unlike the Count column."""
        formatter = ExtractionReportFormatter()
        assertions = {
            "Expected status 200, got 503 after 3 retries": 3,
            "assert elapsed <= 30, got 47.2": 2,
        }
        result = formatter.format_assertion_messages_as_table(assertions)

        assert result == (
            "Assertion Message                            │ Count │ Percentage\n"
            "─────────────────────────────────────────────────────────\n"
            "Expected status 200, got 503 after 3 retries │ 3 │  60.0%\n"
            "assert elapsed <= 30, got 47.2               │ 2 │  40.0%"
        )


class TestMarkdownFormatting:
    """Test markdown output formatting for extracted data."""

    def test_format_test_names_as_markdown_empty(self) -> None:
        """Markdown for empty test names."""
        formatter = ExtractionReportFormatter()
        result = formatter.format_test_names_as_markdown(None)

        assert result == "No failing tests found."

    def test_format_test_names_as_markdown_structure(self) -> None:
        """Markdown has proper table structure."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_foo": 5}
        result = formatter.format_test_names_as_markdown(test_names)

        assert "## Failing Test Names" in result
        assert "| Test Name | Count | Percentage |" in result
        assert "| test_foo | 5 | 100.0% |" in result
        assert "**Total failing tests**: 1" in result
        assert "**Total failures**: 5" in result

    def test_format_test_names_as_markdown_multiple(self) -> None:
        """Markdown for multiple test names."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_foo": 8, "test_bar": 2}
        result = formatter.format_test_names_as_markdown(test_names)

        assert "| test_foo | 8 | 80.0% |" in result
        assert "| test_bar | 2 | 20.0% |" in result
        assert "**Total failing tests**: 2" in result
        assert "**Total failures**: 10" in result

    def test_format_assertion_messages_as_markdown_empty(self) -> None:
        """Markdown for empty assertion messages."""
        formatter = ExtractionReportFormatter()
        result = formatter.format_assertion_messages_as_markdown(None)

        assert result == "No assertion failures found."

    def test_format_assertion_messages_as_markdown_structure(self) -> None:
        """Markdown has proper table structure for assertions."""
        formatter = ExtractionReportFormatter()
        assertions = {"assert x > 0": 3}
        result = formatter.format_assertion_messages_as_markdown(assertions)

        assert "## Failing Assertion Messages" in result
        assert "| Assertion Message | Count | Percentage |" in result
        assert "| assert x > 0 | 3 | 100.0% |" in result
        assert "**Unique assertions**: 1" in result
        assert "**Total assertion failures**: 3" in result

    def test_format_assertion_messages_as_markdown_truncate(self) -> None:
        """Markdown truncates very long assertion messages."""
        formatter = ExtractionReportFormatter()
        long_msg = "x" * 150
        assertions = {long_msg: 2}
        result = formatter.format_assertion_messages_as_markdown(assertions)

        # Should contain truncated version
        assert "..." in result
        # Should not contain full long message
        assert long_msg not in result


class TestFormatterConsistency:
    """Test consistency across output formats."""

    def test_totals_consistent_across_formats(self) -> None:
        """Total counts are consistent across all formats."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_a": 10, "test_b": 5, "test_c": 3}

        json_out = json.loads(formatter.format_test_names_as_json(test_names))
        table_out = formatter.format_test_names_as_table(test_names)
        md_out = formatter.format_test_names_as_markdown(test_names)

        # JSON should have correct total
        assert json_out["total_count"] == 18

        # Table should mention all tests
        assert table_out.count("test_") == 3

        # Markdown should show correct total
        assert "18" in md_out

    def test_sorted_consistently_across_formats(self) -> None:
        """Tests sorted by count in all formats."""
        formatter = ExtractionReportFormatter()
        test_names = {"test_a": 1, "test_b": 10, "test_c": 5}

        json_out = json.loads(formatter.format_test_names_as_json(test_names))

        # JSON should be sorted
        assert json_out["test_names"][0]["name"] == "test_b"
        assert json_out["test_names"][1]["name"] == "test_c"
        assert json_out["test_names"][2]["name"] == "test_a"
