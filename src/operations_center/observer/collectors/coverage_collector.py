# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CoverageCollector — Collects and synthesizes coverage measurement signals.

Reads coverage data from pytest-cov output or .coverage files and produces
CoverageSignal for RepoStateSnapshot.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from operations_center.observer.coverage_models import (
    CoverageAlert,
    CoverageSnapshot,
    CoverageTrendAnalysis,
    FileCoverage,
    ModuleCoverage,
)
from operations_center.observer.models import CoverageSignal
from operations_center.observer.service import ObserverContext

logger = logging.getLogger(__name__)


class CoverageCollector:
    """Collects and synthesizes coverage signals from test output.

    Reads coverage data from pytest-cov JSON output or .coverage files,
    analyzes trends, and produces a CoverageSignal for inclusion in RepoStateSnapshot.
    """

    def __init__(self, coverage_json_path: Optional[str] = None) -> None:
        """Initialize the collector.

        Args:
            coverage_json_path: Path to pytest-cov JSON output file or .coverage file.
                              If None, attempts to find default locations.
        """
        self.coverage_json_path = coverage_json_path or self._find_coverage_file()

    def collect(self, context: ObserverContext) -> CoverageSignal:
        """Collect coverage metrics and synthesize CoverageSignal.

        Args:
            context: Observer context with repo and storage information.

        Returns:
            CoverageSignal with coverage measurements and analysis.
        """
        snapshot = self._load_coverage_snapshot()

        if not snapshot:
            return CoverageSignal(status="unavailable")

        # Extract module-level coverages for signal
        module_coverages = []
        for module in snapshot.module_coverages:
            module_coverages.append(
                {
                    "module_path": module.module_path,
                    "statement_coverage_pct": module.statement_coverage_pct,
                    "branch_coverage_pct": module.branch_coverage_pct,
                    "line_coverage_pct": module.line_coverage_pct,
                    "health_status": module.health_status,
                }
            )

        return CoverageSignal(
            status="measured" if snapshot else "partial",
            total_coverage_pct=snapshot.overall_line_coverage_pct,
            statement_coverage_pct=snapshot.overall_statement_coverage_pct,
            branch_coverage_pct=snapshot.overall_branch_coverage_pct,
            line_coverage_pct=snapshot.overall_line_coverage_pct,
            module_coverages=module_coverages,
            uncovered_file_count=snapshot.uncovered_file_count,
            source=snapshot.source,
            observed_at=snapshot.timestamp,
            coverage_trend_pct=0.0,
            regression_delta_pct=0.0,
            active_alerts=[],
            summary=self._generate_summary(snapshot),
        )

    def _load_coverage_snapshot(self) -> Optional[CoverageSnapshot]:
        """Load coverage snapshot from file.

        Returns:
            CoverageSnapshot if data is available, None otherwise.
        """
        if not self.coverage_json_path or not Path(self.coverage_json_path).exists():
            logger.debug("Coverage file not found: %s", self.coverage_json_path)
            return None

        try:
            with open(self.coverage_json_path) as f:
                data = json.load(f)

            return self._parse_coverage_json(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Failed to parse coverage file: %s", e)
            return None

    def _parse_coverage_json(self, data: dict) -> Optional[CoverageSnapshot]:
        """Parse pytest-cov JSON output into CoverageSnapshot.

        Args:
            data: Coverage JSON data from pytest-cov.

        Returns:
            CoverageSnapshot or None if parsing fails.
        """
        try:
            # Extract overall coverage
            totals = data.get("totals", {})
            overall_statement = totals.get("percent_covered", 0.0)
            overall_branch = totals.get("percent_covered_branch", overall_statement)
            overall_line = overall_statement  # Line coverage approximation

            # Extract module-level data
            module_coverages = []
            files = data.get("files", {})

            module_map: dict[str, dict] = {}

            for file_path, file_data in files.items():
                summary = file_data.get("summary", {})
                percent_covered = summary.get("percent_covered", 0.0)

                # Group by module (extract parent directory)
                module_path = self._extract_module_path(file_path)
                if module_path not in module_map:
                    module_map[module_path] = {
                        "files": [],
                        "statement_coverage_pct": 0.0,
                        "branch_coverage_pct": 0.0,
                        "line_coverage_pct": 0.0,
                    }
                module_map[module_path]["files"].append(
                    {
                        "file_path": file_path,
                        "percent_covered": percent_covered,
                    }
                )

            # Calculate module averages
            for module_path, module_data in module_map.items():
                if module_data["files"]:
                    avg_coverage = sum(
                        f["percent_covered"] for f in module_data["files"]
                    ) / len(module_data["files"])
                    health = self._determine_health(avg_coverage)
                    module_coverages.append(
                        ModuleCoverage(
                            module_path=module_path,
                            statement_coverage_pct=avg_coverage,
                            branch_coverage_pct=avg_coverage,
                            line_coverage_pct=avg_coverage,
                            statement_count=len(module_data["files"]),
                            branch_count=0,
                            line_count=0,
                            health_status=health,
                        )
                    )

            return CoverageSnapshot(
                timestamp=datetime.now(UTC),
                run_id="",
                source="pytest-cov",
                overall_statement_coverage_pct=overall_statement,
                overall_branch_coverage_pct=overall_branch,
                overall_line_coverage_pct=overall_line,
                module_coverages=module_coverages,
                file_coverages=[],
                uncovered_file_count=sum(
                    1 for f in files.values()
                    if f.get("summary", {}).get("percent_covered", 100.0) < 80.0
                ),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error("Error parsing coverage JSON: %s", e)
            return None

    def _extract_module_path(self, file_path: str) -> str:
        """Extract module path from file path.

        Args:
            file_path: Full file path.

        Returns:
            Module path (parent directory of the file).
        """
        parts = Path(file_path).parts
        # Find the first non-src part and take up to that
        if "src" in parts:
            src_idx = parts.index("src")
            # Return up to 2 levels deep in src/
            if len(parts) > src_idx + 2:
                return "/".join(parts[: src_idx + 3])
            else:
                return "/".join(parts[: src_idx + 2])
        # Fallback: return parent directory
        return str(Path(file_path).parent)

    def _determine_health(self, coverage_pct: float) -> str:
        """Determine module health status based on coverage.

        Args:
            coverage_pct: Coverage percentage.

        Returns:
            Health status: "healthy", "at_risk", or "critical".
        """
        if coverage_pct >= 80.0:
            return "healthy"
        elif coverage_pct >= 70.0:
            return "at_risk"
        else:
            return "critical"

    def _generate_summary(self, snapshot: CoverageSnapshot) -> str:
        """Generate human-readable coverage summary.

        Args:
            snapshot: Coverage snapshot.

        Returns:
            Summary string.
        """
        overall = snapshot.overall_line_coverage_pct
        module_count = len(snapshot.module_coverages)
        critical_modules = sum(
            1 for m in snapshot.module_coverages if m.health_status == "critical"
        )

        summary = f"Overall coverage: {overall:.1f}%"
        if module_count > 0:
            summary += f" ({module_count} modules"
            if critical_modules > 0:
                summary += f", {critical_modules} critical"
            summary += ")"

        return summary

    def _find_coverage_file(self) -> Optional[str]:
        """Attempt to find coverage file in default locations.

        Returns:
            Path to coverage file if found, None otherwise.
        """
        # Check common pytest-cov output locations
        candidates = [
            ".coverage.json",
            "coverage.json",
            ".coverage",
            "htmlcov/status.json",
        ]

        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                logger.debug("Found coverage file: %s", path)
                return str(path)

        return None
