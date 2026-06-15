# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Utilities for extracting clean assertion messages from pytest exceptions.

Handles assertion error message extraction with support for:
- Simple AssertionError messages
- Multi-line assertions from pytest traceback
- Exception chaining (__cause__, __context__)
- Non-assertion exceptions (ValueError, TimeoutError, etc.)
- Graceful degradation when data is incomplete
"""

from __future__ import annotations

import re
import traceback
from types import TracebackType
from typing import Any


def extract_assertion_from_excinfo(excinfo: Any) -> str:
    """Extract a clean assertion message from pytest ExceptionInfo object.

    Args:
        excinfo: pytest.ExceptionInfo object or None

    Returns:
        Clean assertion message (up to 200 chars), or empty string if no message
    """
    if excinfo is None or not hasattr(excinfo, "value"):
        return ""

    exc_value = excinfo.value
    tb = getattr(excinfo, "tb", None)

    msg = ""

    # Try to extract from AssertionError first
    if isinstance(exc_value, AssertionError):
        msg = parse_assertion_error(exc_value, tb)
    else:
        # For other exceptions, try basic message extraction
        msg = parse_non_assertion_exception(exc_value)

    # If still empty, try exception chaining
    if not msg:
        msg = _extract_from_exception_chain(exc_value)

    return clean_assertion_message(msg)


def parse_assertion_error(exc_value: AssertionError, tb: TracebackType | None = None) -> str:
    """Extract message from an AssertionError, including traceback context.

    Args:
        exc_value: The AssertionError exception instance
        tb: Optional traceback object

    Returns:
        Cleaned assertion message
    """
    msg = str(exc_value) if exc_value.args else ""

    # If no message on exception, try to extract from traceback
    if not msg and tb:
        msg = _extract_from_traceback(tb)

    return msg


def parse_non_assertion_exception(exc_value: Exception) -> str:
    """Extract message from non-assertion exceptions.

    Handles TimeoutError, ValueError, RuntimeError, etc.

    Args:
        exc_value: The exception instance

    Returns:
        Exception message or empty string
    """
    if not exc_value:
        return ""

    exc_type = type(exc_value).__name__

    # Try to get meaningful message
    msg = str(exc_value) if exc_value.args else ""

    # For TimeoutError and similar, add type hint
    if not msg and exc_type in ("TimeoutError", "ConnectionError", "OSError"):
        msg = exc_type

    return msg


def clean_assertion_message(raw_msg: str, max_length: int = 200) -> str:
    """Clean and normalize an assertion message for display.

    Handles:
    - Whitespace normalization (collapse multiple spaces/newlines to single space)
    - Multi-line assertion collapsing (keep first line, truncate continuation)
    - Truncation to max_length
    - Special character handling

    Args:
        raw_msg: Raw assertion message string
        max_length: Maximum output length (default: 200)

    Returns:
        Cleaned message, truncated to max_length with ellipsis if needed
    """
    if not raw_msg:
        return ""

    msg = raw_msg.strip()

    # Collapse multiple spaces
    msg = re.sub(r" +", " ", msg)

    # Replace newlines with space, but preserve some structure
    msg = re.sub(r"\n+", " ", msg)

    # Collapse any double spaces that resulted from newline replacement
    msg = re.sub(r" +", " ", msg)

    # Remove "assert" keyword if it's at the start (pytest already adds it)
    if msg.lower().startswith("assert "):
        msg = msg[7:].strip()

    # If message is too long, truncate with ellipsis
    if len(msg) > max_length:
        msg = msg[: max_length - 3] + "..."

    return msg


def _extract_from_traceback(tb: TracebackType) -> str:
    """Extract assertion context from traceback.

    Looks for pytest-style "E " prefixed lines which contain the actual
    assertion comparison output.

    Args:
        tb: Traceback object

    Returns:
        Extracted assertion message
    """
    try:
        # Format traceback to get lines
        tb_lines = traceback.format_tb(tb)
        all_text = "".join(tb_lines)

        # Look for assertion comparison lines (pytest marks them with "E ")
        # These appear in the formatted traceback
        lines = all_text.split("\n")
        assertion_lines = [line for line in lines if line.strip().startswith("E ")]

        if assertion_lines:
            # Join assertion lines and clean
            msg = " ".join(line.strip()[2:] for line in assertion_lines)
            return msg
    except Exception:
        pass

    return ""


def _extract_from_exception_chain(exc_value: Exception) -> str:
    """Extract message from exception chaining (__cause__, __context__).

    Handles exceptions that wrap other exceptions (e.g., ConnectionError from
    socket.error).

    Args:
        exc_value: The exception instance

    Returns:
        Message from chained exception
    """
    msg = ""

    # Try __cause__ first (explicit raise ... from ...)
    cause = getattr(exc_value, "__cause__", None)
    if cause:
        msg = str(cause) if cause.args else ""

    # If not found, try __context__ (implicit exception during handling)
    if not msg:
        context = getattr(exc_value, "__context__", None)
        if context:
            msg = str(context) if context.args else ""

    return msg
