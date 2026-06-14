# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for assertion message extraction utilities."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest  # noqa: F401

from operations_center.observer.assertion_extractor import (
    clean_assertion_message,
    extract_assertion_from_excinfo,
    parse_assertion_error,
    parse_non_assertion_exception,
)


def _mock_excinfo(exc_value: Exception | None = None, tb: Any = None) -> Any:
    """Create a mock excinfo object similar to pytest.ExceptionInfo."""
    return SimpleNamespace(value=exc_value, tb=tb)


class TestExtractAssertionFromExcinfo:
    """Tests for extract_assertion_from_excinfo main entry point."""

    def test_none_excinfo_returns_empty(self) -> None:
        assert extract_assertion_from_excinfo(None) == ""

    def test_missing_value_attribute_returns_empty(self) -> None:
        excinfo = SimpleNamespace()
        assert extract_assertion_from_excinfo(excinfo) == ""

    def test_simple_assertion_error_message(self) -> None:
        exc = AssertionError("expected 42 but got 41")
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        assert "42" in result and "41" in result

    def test_assertion_error_without_message(self) -> None:
        exc = AssertionError()
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        # Should handle gracefully, return empty or minimal string
        assert result == ""

    def test_timeout_error_extraction(self) -> None:
        exc = TimeoutError("Operation timed out after 30s")
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        assert "timed out" in result.lower()

    def test_value_error_extraction(self) -> None:
        exc = ValueError("invalid literal for int(): 'abc'")
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        assert "abc" in result or "invalid" in result.lower()

    def test_connection_error_extraction(self) -> None:
        exc = ConnectionError("Failed to connect to localhost:8000")
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        assert "connect" in result.lower() or "localhost" in result


class TestParseAssertionError:
    """Tests for parse_assertion_error."""

    def test_simple_message(self) -> None:
        exc = AssertionError("x is not y")
        result = parse_assertion_error(exc)
        assert result == "x is not y"

    def test_without_message(self) -> None:
        exc = AssertionError()
        result = parse_assertion_error(exc)
        assert result == ""

    def test_multi_arg_exception(self) -> None:
        exc = AssertionError("first", "second")
        result = parse_assertion_error(exc)
        # str() of multi-arg AssertionError gives tuple repr
        assert result

    def test_with_none_traceback(self) -> None:
        exc = AssertionError("test message")
        result = parse_assertion_error(exc, tb=None)
        assert result == "test message"


class TestParseNonAssertionException:
    """Tests for parse_non_assertion_exception."""

    def test_timeout_error(self) -> None:
        exc = TimeoutError("Timeout after 5s")
        result = parse_non_assertion_exception(exc)
        assert "Timeout" in result or "timeout" in result.lower()

    def test_runtime_error(self) -> None:
        exc = RuntimeError("Something went wrong")
        result = parse_non_assertion_exception(exc)
        assert "wrong" in result.lower()

    def test_none_exception(self) -> None:
        result = parse_non_assertion_exception(None)  # type: ignore
        assert result == ""

    def test_exception_without_args(self) -> None:
        exc = RuntimeError()
        result = parse_non_assertion_exception(exc)
        # Should handle gracefully
        assert result == ""

    def test_timeout_error_without_message(self) -> None:
        exc = TimeoutError()
        result = parse_non_assertion_exception(exc)
        # Should at least return the exception type
        assert "TimeoutError" in result or result == ""

    def test_connection_error_without_message(self) -> None:
        exc = ConnectionError()
        result = parse_non_assertion_exception(exc)
        # Should at least return the exception type
        assert "ConnectionError" in result or result == ""


class TestCleanAssertionMessage:
    """Tests for clean_assertion_message."""

    def test_empty_string(self) -> None:
        assert clean_assertion_message("") == ""

    def test_simple_message(self) -> None:
        msg = "expected 42 but got 41"
        assert clean_assertion_message(msg) == msg

    def test_whitespace_normalization(self) -> None:
        msg = "expected   42   but   got   41"
        result = clean_assertion_message(msg)
        assert result == "expected 42 but got 41"

    def test_newline_to_space(self) -> None:
        msg = "expected\n42\nbut\ngot\n41"
        result = clean_assertion_message(msg)
        assert "\n" not in result
        assert "expected" in result and "42" in result and "41" in result

    def test_multiple_newlines_collapsed(self) -> None:
        msg = "line1\n\n\n\nline2"
        result = clean_assertion_message(msg)
        assert result == "line1 line2"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        msg = "  \n  expected 42  \n  "
        result = clean_assertion_message(msg)
        assert result == "expected 42"
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_assert_keyword_removed(self) -> None:
        msg = "assert x == y"
        result = clean_assertion_message(msg)
        assert result == "x == y"

    def test_assert_keyword_case_insensitive(self) -> None:
        msg = "Assert x == y"
        result = clean_assertion_message(msg)
        assert result == "x == y"

    def test_truncation_at_max_length(self) -> None:
        msg = "a" * 250
        result = clean_assertion_message(msg, max_length=50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_short_message_not_truncated(self) -> None:
        msg = "expected x"
        result = clean_assertion_message(msg, max_length=50)
        assert result == msg
        assert not result.endswith("...")

    def test_exact_max_length(self) -> None:
        msg = "a" * 50
        result = clean_assertion_message(msg, max_length=50)
        # Should not truncate messages that fit exactly
        assert len(result) <= 50

    def test_complex_multiline_message(self) -> None:
        msg = """assert expected == actual
        where expected = 42
        and actual = 41"""
        result = clean_assertion_message(msg)
        # Should collapse to single line
        assert "\n" not in result
        assert "assert" not in result  # keyword removed
        assert "42" in result
        assert "41" in result


class TestAssertionMessageIntegration:
    """Integration tests combining multiple extraction methods."""

    def test_assertion_error_extraction_flow(self) -> None:
        exc = AssertionError("Dict comparison failed: {'a': 1} != {'a': 2}")
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        assert "comparison" in result or "Dict" in result

    def test_chained_exception_extraction(self) -> None:
        # Create a chained exception (inner -> outer)
        try:
            try:
                raise ValueError("inner error")
            except ValueError as e:
                raise RuntimeError("outer error") from e
        except RuntimeError as exc:
            excinfo = _mock_excinfo(exc)
            result = extract_assertion_from_excinfo(excinfo)
            # Should extract from the chain
            assert result

    def test_message_too_long_truncates(self) -> None:
        long_msg = "x" * 500
        exc = AssertionError(long_msg)
        excinfo = _mock_excinfo(exc)
        result = extract_assertion_from_excinfo(excinfo)
        assert len(result) <= 200
        assert result.endswith("...")
