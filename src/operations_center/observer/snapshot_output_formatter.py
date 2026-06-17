# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Output formatting for validation results.

Provides multiple output formats for validation reports:
- Table (human-readable with formatting)
- JSON (structured for automation)
- Markdown (documentation)
- Text (plain, minimal)
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operations_center.observer.snapshot_validator import SnapshotValidationReport

from operations_center.observer.snapshot_validator import (
    SnapshotValidationReport,
)


class OutputFormat(str, Enum):
    """Supported output formats."""

    TABLE = "table"
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"


class SnapshotOutputFormatter:
    """Format validation reports in multiple output formats."""

    def format(
        self, report: SnapshotValidationReport, format: OutputFormat | str = OutputFormat.TABLE
    ) -> str:
        """Format validation report in specified format.

        Args:
            report: Validation report to format
            format: Output format (table, json, markdown, text)

        Returns:
            Formatted output string
        """
        if isinstance(format, str):
            format = OutputFormat(format)

        if format == OutputFormat.TABLE:
            return self.format_table(report)
        elif format == OutputFormat.JSON:
            return self.format_json(report)
        elif format == OutputFormat.MARKDOWN:
            return self.format_markdown(report)
        elif format == OutputFormat.TEXT:
            return self.format_text(report)
        else:
            raise ValueError(f"Unknown output format: {format}")

    def format_table(self, report: SnapshotValidationReport) -> str:
        """Format report as human-readable table.

        Args:
            report: Validation report

        Returns:
            Formatted table string
        """
        lines = []

        status_icon = "✓" if report.passed else "✗"
        status_text = "PASSED" if report.passed else "FAILED"

        lines.append(f"{status_icon} Snapshot Validation: {status_text}")
        lines.append("")
        lines.append(f"Snapshot: {report.snapshot_id}")
        lines.append(f"Observed: {report.observed_at.isoformat()}")
        lines.append(f"Duration: {report.overall_duration_ms:.1f} ms")
        lines.append("")

        layer_names = {
            1: "Schema Validation",
            2: "Completeness Validation",
            3: "Consistency Validation",
            4: "Accuracy Validation",
            5: "Regression Detection",
        }

        for result in report.results:
            layer = self._get_layer_from_check_name(result.check_name)
            layer_name = layer_names.get(layer, result.check_name)

            icon = "✓" if result.passed else "✗"
            passed_text = "PASS" if result.passed else "FAIL"

            error_count = len(result.errors)
            details = f"({error_count} error{'s' if error_count != 1 else ''})"

            line = f"  {icon} {layer_name:<40} {passed_text:<8} {details}"
            lines.append(line)

            if result.errors and error_count > 0:
                for error in result.errors:
                    category = error.category.value
                    lines.append(f"      • {error.message} [{category}]")

        lines.append("")
        lines.append(
            f"Summary: {len(report.results)} checks run, {len(report.results) - sum(1 for r in report.results if r.passed)} failed"
        )

        return "\n".join(lines)

    def format_json(self, report: SnapshotValidationReport) -> str:
        """Format report as JSON.

        Args:
            report: Validation report

        Returns:
            Formatted JSON string
        """
        return json.dumps(
            report.to_dict(), indent=2, default=self._json_serializer, ensure_ascii=False
        )

    def format_markdown(self, report: SnapshotValidationReport) -> str:
        """Format report as Markdown.

        Args:
            report: Validation report

        Returns:
            Formatted Markdown string
        """
        lines = []

        status = "✅ PASSED" if report.passed else "❌ FAILED"
        lines.append(f"# Snapshot Validation Report: {status}")
        lines.append("")

        lines.append("## Snapshot Information")
        lines.append(f"- **ID**: `{report.snapshot_id}`")
        lines.append(f"- **Observed**: {report.observed_at.isoformat()}")
        lines.append(f"- **Duration**: {report.overall_duration_ms:.1f} ms")
        lines.append("")

        lines.append("## Validation Results")
        lines.append("")

        layer_names = {
            1: "Schema Validation",
            2: "Completeness Validation",
            3: "Consistency Validation",
            4: "Accuracy Validation",
            5: "Regression Detection",
        }

        for result in report.results:
            layer = self._get_layer_from_check_name(result.check_name)
            layer_name = layer_names.get(layer, result.check_name)
            status = "✅ PASS" if result.passed else "❌ FAIL"

            lines.append(f"### {layer_name} {status}")
            lines.append("")
            lines.append(result.message)
            lines.append("")

            if result.errors:
                lines.append("**Errors:**")
                lines.append("")
                for error in result.errors:
                    category = error.category.value.upper()
                    retryable = "Retryable" if error.is_retryable else "Not retryable"
                    lines.append(f"- {error.message}")
                    lines.append(f"  - Category: `{category}`")
                    lines.append(f"  - {retryable}")
                    if error.details:
                        lines.append(f"  - Details: {error.details}")
                lines.append("")

        lines.append("## Summary")
        total_checks = len(report.results)
        failed_checks = sum(1 for r in report.results if not r.passed)
        lines.append(f"- **Total Checks**: {total_checks}")
        lines.append(f"- **Failed**: {failed_checks}")
        lines.append(f"- **Retryable Errors**: {len(report.get_retryable_errors())}")
        lines.append(
            f"- **Non-Retryable Errors**: {sum(len([e for e in r.errors if not e.is_retryable]) for r in report.results)}"
        )

        return "\n".join(lines)

    def format_text(self, report: SnapshotValidationReport) -> str:
        """Format report as plain text (minimal).

        Args:
            report: Validation report

        Returns:
            Formatted text string
        """
        lines = []

        status = "PASSED" if report.passed else "FAILED"
        lines.append(f"Snapshot Validation: {status}")
        lines.append(f"  ID: {report.snapshot_id}")
        lines.append(f"  Observed: {report.observed_at.isoformat()}")
        lines.append(f"  Duration: {report.overall_duration_ms:.1f} ms")
        lines.append("")

        for result in report.results:
            status = "PASS" if result.passed else "FAIL"
            lines.append(f"  {result.check_name}: {status}")

            if result.errors:
                for error in result.errors:
                    lines.append(f"    - {error.message} [{error.category.value}]")

        return "\n".join(lines)

    def _get_layer_from_check_name(self, check_name: str) -> int:
        """Extract layer number from check name.

        Args:
            check_name: Check name (e.g., "schema_validation", "consistency_validation")

        Returns:
            Layer number 1-5, or 0 if unknown
        """
        if "schema" in check_name:
            return 1
        elif "completeness" in check_name:
            return 2
        elif "consistency" in check_name:
            return 3
        elif "accuracy" in check_name:
            return 4
        elif "regression" in check_name:
            return 5
        return 0

    def _json_serializer(self, obj: Any) -> Any:
        """JSON serializer for non-standard types.

        Args:
            obj: Object to serialize

        Returns:
            Serializable representation
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
