# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Report formatter for extracted test failure data (test names and assertion messages).

Provides multiple output formats (table, JSON, markdown) for displaying extracted
test failure information extracted from assertions and pytest execution.

## Usage

    query = TestSignalQuery()
    formatter = ExtractionReportFormatter()

    # Get extracted test failures
    test_names = query.get_failing_test_names(TimeRange.last_hours(24))
    assertions = query.get_failing_assertion_messages(TimeRange.last_hours(24))

    # Format as JSON
    json_output = formatter.format_test_names_as_json(test_names)
    print(json_output)

    # Format as table
    table_output = formatter.format_test_names_as_table(test_names)
    print(table_output)

    # Format as markdown
    md_output = formatter.format_test_names_as_markdown(test_names)
    print(md_output)

## Output Formats

### JSON Format
- Compact, machine-readable format
- Supports all extracted data types
- Suitable for programmatic consumption

### Table Format
- Human-readable, columnar layout
- Shows counts and percentages
- Rich formatting with colors

### Markdown Format
- Markdown-formatted table
- Suitable for documentation and reports
- Includes section headers and annotations
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class ExtractionReportFormatter:
    """Format extracted test failure data for various output formats.

    Supports:
    - JSON: Machine-readable format for programmatic access
    - Table: Rich-formatted tables for terminal display
    - Markdown: Markdown tables for documentation and reports
    """

    def format_test_names_as_json(self, test_names: dict[str, int] | None) -> str:
        """Format failing test names as JSON.

        Args:
            test_names: Dict mapping test name to failure count

        Returns:
            JSON string with test names and counts

        Example:
            >>> formatter = ExtractionReportFormatter()
            >>> names = {"test_foo": 3, "test_bar": 1}
            >>> json_str = formatter.format_test_names_as_json(names)
            >>> # Output: {"test_names": [{"name": "test_foo", "count": 3}, ...]}
        """
        if not test_names:
            return json.dumps({"test_names": [], "total_count": 0}, indent=2)

        test_list = [
            {
                "name": name,
                "count": count,
                "percentage": f"{(count / sum(test_names.values())) * 100:.1f}%",
            }
            for name, count in sorted(test_names.items(), key=lambda x: x[1], reverse=True)
        ]
        return json.dumps(
            {
                "test_names": test_list,
                "total_count": sum(test_names.values()),
                "unique_tests": len(test_names),
            },
            indent=2,
            ensure_ascii=False,
        )

    def format_assertion_messages_as_json(self, assertions: dict[str, int] | None) -> str:
        """Format failing assertion messages as JSON.

        Args:
            assertions: Dict mapping assertion message to failure count

        Returns:
            JSON string with assertion messages and counts
        """
        if not assertions:
            return json.dumps(
                {"assertion_messages": [], "total_count": 0},
                indent=2,
            )

        assertion_list = [
            {
                "message": msg,
                "count": count,
                "percentage": f"{(count / sum(assertions.values())) * 100:.1f}%",
            }
            for msg, count in sorted(assertions.items(), key=lambda x: x[1], reverse=True)
        ]
        return json.dumps(
            {
                "assertion_messages": assertion_list,
                "total_count": sum(assertions.values()),
                "unique_assertions": len(assertions),
            },
            indent=2,
            ensure_ascii=False,
        )

    def format_test_names_as_table(self, test_names: dict[str, int] | None) -> str:
        """Format failing test names as ASCII table.

        Args:
            test_names: Dict mapping test name to failure count

        Returns:
            Formatted table string

        Example:
            >>> formatter = ExtractionReportFormatter()
            >>> names = {"test_foo": 3, "test_bar": 1}
            >>> table = formatter.format_test_names_as_table(names)
            >>> print(table)
            ┌─────────────┬───────┬────────────┐
            │ Test Name   │ Count │ Percentage │
            ├─────────────┼───────┼────────────┤
            │ test_foo    │   3   │   75.0%    │
            │ test_bar    │   1   │   25.0%    │
            └─────────────┴───────┴────────────┘
        """
        if not test_names:
            return "No failing tests found."

        lines = []
        sorted_tests = sorted(test_names.items(), key=lambda x: x[1], reverse=True)
        total = sum(test_names.values())

        # Calculate column widths
        name_width = max(len("Test Name"), max(len(name) for name, _ in sorted_tests))
        count_width = len(str(total))
        pct_width = len("100.0%")

        # Header
        lines.append(
            f"{'Test Name':<{name_width}} │ {'Count':>{count_width}} │ {'Percentage':>{pct_width}}"
        )
        lines.append("─" * (name_width + count_width + pct_width + 6))

        # Data rows
        for name, count in sorted_tests:
            pct = (count / total) * 100
            lines.append(
                f"{name:<{name_width}} │ {count:>{count_width}} │ {pct:>{pct_width - 1}.1f}%"
            )

        return "\n".join(lines)

    def format_assertion_messages_as_table(self, assertions: dict[str, int] | None) -> str:
        """Format failing assertion messages as ASCII table.

        Args:
            assertions: Dict mapping assertion message to failure count

        Returns:
            Formatted table string
        """
        if not assertions:
            return "No assertion failures found."

        lines = []
        sorted_assertions = sorted(assertions.items(), key=lambda x: x[1], reverse=True)
        total = sum(assertions.values())

        # Calculate column widths
        msg_width = max(
            len("Assertion Message"),
            max(min(len(msg), 80) for msg, _ in sorted_assertions),
        )
        count_width = len(str(total))
        pct_width = len("100.0%")

        # Header
        lines.append(
            f"{'Assertion Message':<{msg_width}} │ {'Count':>{count_width}} │ {'Percentage':>{pct_width}}"
        )
        lines.append("─" * (msg_width + count_width + pct_width + 6))

        # Data rows
        for msg, count in sorted_assertions:
            truncated = (msg[:77] + "...") if len(msg) > 80 else msg
            pct = (count / total) * 100
            lines.append(
                f"{truncated:<{msg_width}} │ {count:>{count_width}} │ {pct:>{pct_width - 1}.1f}%"
            )

        return "\n".join(lines)

    def format_test_names_as_markdown(self, test_names: dict[str, int] | None) -> str:
        """Format failing test names as markdown table.

        Args:
            test_names: Dict mapping test name to failure count

        Returns:
            Markdown-formatted table
        """
        if not test_names:
            return "No failing tests found."

        lines = [
            "## Failing Test Names",
            "",
            "| Test Name | Count | Percentage |",
            "|-----------|-------|-----------|",
        ]

        sorted_tests = sorted(test_names.items(), key=lambda x: x[1], reverse=True)
        total = sum(test_names.values())

        for name, count in sorted_tests:
            pct = (count / total) * 100
            lines.append(f"| {name} | {count} | {pct:.1f}% |")

        lines.extend(
            ["", f"**Total failing tests**: {len(test_names)}", f"**Total failures**: {total}"]
        )
        return "\n".join(lines)

    def format_assertion_messages_as_markdown(self, assertions: dict[str, int] | None) -> str:
        """Format failing assertion messages as markdown table.

        Args:
            assertions: Dict mapping assertion message to failure count

        Returns:
            Markdown-formatted table
        """
        if not assertions:
            return "No assertion failures found."

        lines = [
            "## Failing Assertion Messages",
            "",
            "| Assertion Message | Count | Percentage |",
            "|-------------------|-------|-----------|",
        ]

        sorted_assertions = sorted(assertions.items(), key=lambda x: x[1], reverse=True)
        total = sum(assertions.values())

        for msg, count in sorted_assertions:
            truncated = (msg[:100] + "...") if len(msg) > 100 else msg
            pct = (count / total) * 100
            lines.append(f"| {truncated} | {count} | {pct:.1f}% |")

        lines.extend(
            [
                "",
                f"**Unique assertions**: {len(assertions)}",
                f"**Total assertion failures**: {total}",
            ]
        )
        return "\n".join(lines)
