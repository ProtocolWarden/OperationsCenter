# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CoverageSignalCollector — reads retained coverage reports without running coverage tools.

Supports:
  - coverage.xml   (Cobertura XML — output of ``coverage xml``)
  - .coverage      (presence-only; version detected from header)
  - pytest-coverage.txt / coverage.txt  (text report)
  - htmlcov/index.html  (HTML report title contains "X%")

NEVER runs coverage tools.  Only reads files that already exist.
Returns CoverageSignal(status="unavailable") when no report is found.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from operations_center.observer.models import CoverageSignal, UncoveredFile
from operations_center.observer.service import ObserverContext

_UNCOVERED_THRESHOLD_PCT: float = 80.0
_MAX_UNCOVERED_LISTED: int = 10
_TEXT_TOTAL_RE: re.Pattern[str] = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+)%")
_HTML_PCT_RE: re.Pattern[str] = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class CoverageSignalCollector:
    """Reads pre-existing coverage reports to surface coverage gaps.

    Checks (in priority order):
    1. ``coverage.xml`` — full file-level data
    2. ``pytest-coverage.txt`` or ``coverage.txt`` — text totals only
    3. ``htmlcov/index.html`` — HTML summary
    """

    def collect(self, context: ObserverContext) -> CoverageSignal:
        """Collect coverage data from pre-existing reports in the repository.

        Searches for coverage reports in priority order:
        1. coverage.xml (Cobertura XML format from coverage.py)
        2. coverage.txt or pytest-coverage.txt (text summary)
        3. htmlcov/index.html (HTML coverage report)

        Args:
            context: ObserverContext with repo path and logs root

        Returns:
            CoverageSignal with status (measured/partial/unavailable) and coverage data
        """
        try:
            return self._analyze(context)
        except Exception:
            return CoverageSignal(status="unavailable")

    def _analyze(self, context: ObserverContext) -> CoverageSignal:
        """Analyze coverage data from available reports.

        Searches both repo_path and logs_root for coverage files in priority order.

        Args:
            context: ObserverContext with repo path and logs root

        Returns:
            CoverageSignal with measured coverage data or unavailable status
        """
        search_roots: list[Path] = [context.repo_path]
        if context.logs_root.is_dir():
            search_roots.append(context.logs_root)

        for root in search_roots:
            xml_path: Path = root / "coverage.xml"
            if xml_path.is_file():
                result: CoverageSignal | None = self._parse_xml(xml_path)
                if result is not None:
                    return result

        for root in search_roots:
            for name in ("pytest-coverage.txt", "coverage.txt", ".coverage_report.txt"):
                txt_path: Path = root / name
                if txt_path.is_file():
                    result = self._parse_text(txt_path)
                    if result is not None:
                        return result

        for root in search_roots:
            html_path: Path = root / "htmlcov" / "index.html"
            if html_path.is_file():
                result = self._parse_html(html_path)
                if result is not None:
                    return result

            cov_path: Path = root / ".coverage"
            if cov_path.is_file():
                return CoverageSignal(
                    status="partial",
                    source=".coverage",
                    observed_at=datetime.now(UTC),
                    summary=".coverage file found but no report generated yet",
                )

        return CoverageSignal(status="unavailable")

    # ------------------------------------------------------------------

    def _parse_xml(self, path: Path) -> CoverageSignal | None:
        """Parse Cobertura XML coverage report (coverage.xml).

        Extracts overall line coverage percentage and identifies files below threshold.

        Args:
            path: Path to coverage.xml file

        Returns:
            CoverageSignal with parsed data, or None if XML is invalid/unparseable
        """
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return None
        root = tree.getroot()
        if root is None:
            return None
        rate_str: str | None = root.get("line-rate")
        if rate_str is None:
            return None
        try:
            total_pct: float = round(float(rate_str) * 100, 1)
        except ValueError:
            return None

        uncovered: list[UncoveredFile] = []
        for cls in root.iter("class"):
            cls_rate: str | None = cls.get("line-rate")
            cls_name: str = cls.get("filename") or cls.get("name") or "unknown"
            try:
                pct: float = round(float(cls_rate) * 100, 1) if cls_rate else 0.0
            except (ValueError, TypeError):
                pct = 0.0
            if pct < _UNCOVERED_THRESHOLD_PCT:
                uncovered.append(UncoveredFile(path=cls_name, coverage_pct=pct))

        uncovered.sort(key=lambda u: u.coverage_pct)
        top: list[UncoveredFile] = uncovered[:_MAX_UNCOVERED_LISTED]
        summary: str = (
            f"{total_pct}% overall coverage; {len(uncovered)} file(s) "
            f"below {_UNCOVERED_THRESHOLD_PCT}%"
        )
        return CoverageSignal(
            status="measured",
            total_coverage_pct=total_pct,
            uncovered_file_count=len(uncovered),
            uncovered_threshold_pct=_UNCOVERED_THRESHOLD_PCT,
            top_uncovered=top,
            source="coverage.xml",
            observed_at=datetime.now(UTC),
            summary=summary,
        )

    def _parse_text(self, path: Path) -> CoverageSignal | None:
        """Parse text-based coverage report (coverage.txt or pytest-coverage.txt).

        Extracts overall coverage percentage from text summary lines matching "TOTAL X% Y%".

        Args:
            path: Path to coverage text file

        Returns:
            CoverageSignal with parsed coverage percentage, or None if no data found
        """
        try:
            text: str = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m: re.Match[str] | None = _TEXT_TOTAL_RE.search(text)
        if not m:
            return None
        total_pct: float = float(m.group(1))
        summary: str = f"{total_pct}% overall coverage (text report)"
        return CoverageSignal(
            status="measured",
            total_coverage_pct=total_pct,
            source=path.name,
            observed_at=datetime.now(UTC),
            summary=summary,
        )

    def _parse_html(self, path: Path) -> CoverageSignal | None:
        """Parse HTML coverage report (htmlcov/index.html).

        Extracts overall coverage percentage from HTML title or body text
        matching percentage patterns.

        Args:
            path: Path to htmlcov/index.html file

        Returns:
            CoverageSignal with parsed coverage percentage, or None if no valid data found
        """
        try:
            text: str = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m: re.Match[str] | None = _HTML_PCT_RE.search(text[:2000])
        if not m:
            return None
        total_pct: float = float(m.group(1))
        if total_pct > 100:
            return None
        summary: str = f"{total_pct}% overall coverage (HTML report)"
        return CoverageSignal(
            status="measured",
            total_coverage_pct=total_pct,
            source="htmlcov/index.html",
            observed_at=datetime.now(UTC),
            summary=summary,
        )

    def _is_coverage_acceptable(self, coverage_pct: float, threshold_pct: float = 75.0) -> bool:
        """Check if coverage percentage meets minimum threshold.

        Args:
            coverage_pct: Coverage percentage to evaluate
            threshold_pct: Minimum acceptable coverage percentage

        Returns:
            True if coverage meets or exceeds threshold
        """
        is_acceptable: bool = coverage_pct >= threshold_pct
        return is_acceptable

    def _get_coverage_status(
        self, coverage_pct: float
    ) -> Literal["excellent", "good", "fair", "poor"]:
        """Classify coverage level based on percentage.

        Args:
            coverage_pct: Coverage percentage to classify

        Returns:
            Status string representing coverage level
        """
        if coverage_pct >= 90.0:
            status: Literal["excellent", "good", "fair", "poor"] = "excellent"
        elif coverage_pct >= 80.0:
            status = "good"
        elif coverage_pct >= 70.0:
            status = "fair"
        else:
            status = "poor"
        return status

    def _count_uncovered_files(self, uncovered: list[UncoveredFile]) -> dict[str, int]:
        """Count uncovered files by severity.

        Args:
            uncovered: List of uncovered files

        Returns:
            Dictionary with counts at each severity level
        """
        critical_count: int = 0
        poor_count: int = 0
        fair_count: int = 0

        for file in uncovered:
            if file.coverage_pct < 50.0:
                critical_count += 1
            elif file.coverage_pct < 70.0:
                poor_count += 1
            else:
                fair_count += 1

        return {
            "critical": critical_count,
            "poor": poor_count,
            "fair": fair_count,
        }

    def _get_coverage_improvement_suggestion(
        self, current_coverage: float, target_coverage: float = 80.0
    ) -> str:
        """Generate suggestion for coverage improvement.

        Args:
            current_coverage: Current coverage percentage
            target_coverage: Target coverage percentage

        Returns:
            Improvement recommendation message
        """
        gap: float = target_coverage - current_coverage
        if gap <= 0:
            suggestion: str = "Coverage meets or exceeds target."
        elif gap <= 5.0:
            suggestion = f"Add {gap:.1f}% more coverage to reach target."
        elif gap <= 15.0:
            suggestion = f"Significant effort needed: {gap:.1f}% gap to target."
        else:
            suggestion = f"Major effort required: {gap:.1f}% gap to reach target."

        return suggestion


def format_coverage_percentage(value: float, decimal_places: int = 1) -> str:
    """Format a coverage percentage with specified precision.

    Args:
        value: Coverage value to format
        decimal_places: Number of decimal places

    Returns:
        Formatted percentage string
    """
    formatted_value: str = f"{value:.{decimal_places}f}%"
    return formatted_value


def is_coverage_below_minimum(coverage: float, minimum: float = 50.0) -> bool:
    """Check if coverage is below critical minimum.

    Args:
        coverage: Coverage percentage to check
        minimum: Minimum acceptable threshold

    Returns:
        True if coverage is below minimum
    """
    below_minimum: bool = coverage < minimum
    return below_minimum


def summarize_uncovered_files(uncovered_files: list[UncoveredFile], max_to_show: int = 5) -> str:
    """Create a summary of most critical uncovered files.

    Args:
        uncovered_files: List of uncovered files sorted by coverage
        max_to_show: Maximum number of files to include in summary

    Returns:
        Summary text of critical uncovered files
    """
    if not uncovered_files:
        return "No critical files identified."

    critical: list[UncoveredFile] = uncovered_files[:max_to_show]
    summary_lines: list[str] = ["Critical uncovered files:"]

    for file in critical:
        line: str = f"  • {file.path}: {file.coverage_pct:.1f}%"
        summary_lines.append(line)

    if len(uncovered_files) > max_to_show:
        remaining: int = len(uncovered_files) - max_to_show
        summary_lines.append(f"  ... and {remaining} more files")

    return "\n".join(summary_lines)
