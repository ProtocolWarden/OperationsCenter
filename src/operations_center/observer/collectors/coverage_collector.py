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
from typing import Any, Literal, Optional

from operations_center.observer.coverage_models import (
    CoverageSnapshot,
    ModuleCoverage,
)
from operations_center.observer.models import CoverageSignal
from operations_center.observer.service import ObserverContext

logger: logging.Logger = logging.getLogger(__name__)


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
        snapshot: Optional[CoverageSnapshot] = self._load_coverage_snapshot()

        if not snapshot:
            return CoverageSignal(status="unavailable")

        module_coverages: list[dict[str, Any]] = []
        for module in snapshot.module_coverages:
            module_dict: dict[str, Any] = {
                "module_path": module.module_path,
                "statement_coverage_pct": module.statement_coverage_pct,
                "branch_coverage_pct": module.branch_coverage_pct,
                "line_coverage_pct": module.line_coverage_pct,
                "health_status": module.health_status,
            }
            module_coverages.append(module_dict)

        status: Literal["measured", "partial"] = "measured" if snapshot else "partial"
        summary: str = self._generate_summary(snapshot)

        return CoverageSignal(
            status=status,
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
            summary=summary,
        )

    def _load_coverage_snapshot(self) -> Optional[CoverageSnapshot]:
        """Load coverage snapshot from file.

        Returns:
            CoverageSnapshot if data is available, None otherwise.
        """
        if not self.coverage_json_path:
            logger.debug("Coverage file not found: %s", self.coverage_json_path)
            return None

        try:
            exists = Path(self.coverage_json_path).exists()
        except PermissionError:
            logger.error("Permission denied accessing coverage file: %s", self.coverage_json_path)
            return None

        if not exists:
            logger.debug("Coverage file not found: %s", self.coverage_json_path)
            return None

        try:
            with open(self.coverage_json_path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
            return self._parse_coverage_json(data)
        except PermissionError:
            logger.error("Permission denied accessing coverage file: %s", self.coverage_json_path)
            return None
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Failed to parse coverage file: %s", e)
            return None

    def _parse_coverage_json(self, data: dict[str, Any]) -> Optional[CoverageSnapshot]:
        """Parse pytest-cov JSON output into CoverageSnapshot.

        Args:
            data: Coverage JSON data from pytest-cov.

        Returns:
            CoverageSnapshot or None if parsing fails.
        """
        try:
            totals: dict[str, Any] = data.get("totals", {})
            overall_statement: float = totals.get("percent_covered", 0.0)
            overall_branch: float = totals.get("percent_covered_branch", overall_statement)
            overall_line: float = overall_statement

            module_coverages: list[ModuleCoverage] = []
            files: dict[str, Any] = data.get("files", {})

            module_map: dict[str, dict[str, Any]] = {}

            for file_path, file_data in files.items():
                summary: dict[str, Any] = file_data.get("summary", {})
                percent_covered: float = summary.get("percent_covered", 0.0)

                module_path: str = self._extract_module_path(file_path)
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

            for module_path, module_data in module_map.items():
                if module_data["files"]:
                    file_list: list[dict[str, Any]] = module_data["files"]
                    avg_coverage: float = sum(f["percent_covered"] for f in file_list) / len(
                        file_list
                    )
                    health: Literal["healthy", "at_risk", "critical"] = self._determine_health(
                        avg_coverage
                    )
                    module_coverages.append(
                        ModuleCoverage(
                            module_path=module_path,
                            statement_coverage_pct=avg_coverage,
                            branch_coverage_pct=avg_coverage,
                            line_coverage_pct=avg_coverage,
                            statement_count=len(file_list),
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
                    1
                    for f in files.values()
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
        parts: tuple[str, ...] = Path(file_path).parts
        if "src" in parts:
            src_idx: int = parts.index("src")
            if len(parts) > src_idx + 2:
                return "/".join(parts[: src_idx + 3])
            else:
                return "/".join(parts[: src_idx + 2])
        return str(Path(file_path).parent)

    def _determine_health(self, coverage_pct: float) -> Literal["healthy", "at_risk", "critical"]:
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
        overall: float = snapshot.overall_line_coverage_pct
        module_count: int = len(snapshot.module_coverages)
        critical_modules: int = sum(
            1 for m in snapshot.module_coverages if m.health_status == "critical"
        )

        summary: str = f"Overall coverage: {overall:.1f}%"
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
        candidates: list[str] = [
            ".coverage.json",
            "coverage.json",
            ".coverage",
            "htmlcov/status.json",
        ]

        for candidate in candidates:
            path: Path = Path(candidate)
            if path.exists():
                logger.debug("Found coverage file: %s", path)
                return str(path)

        return None

    def _validate_snapshot(self, snapshot: CoverageSnapshot) -> bool:
        """Validate that snapshot has required fields.

        Args:
            snapshot: Coverage snapshot to validate

        Returns:
            True if snapshot is valid
        """
        return (
            snapshot.overall_statement_coverage_pct >= 0.0
            and snapshot.overall_statement_coverage_pct <= 100.0
            and snapshot.overall_branch_coverage_pct >= 0.0
            and snapshot.overall_branch_coverage_pct <= 100.0
            and snapshot.overall_line_coverage_pct >= 0.0
            and snapshot.overall_line_coverage_pct <= 100.0
        )

    def _filter_modules_by_health(
        self, snapshot: CoverageSnapshot, health_status: Literal["healthy", "at_risk", "critical"]
    ) -> list[ModuleCoverage]:
        """Get modules with specific health status.

        Args:
            snapshot: Coverage snapshot to filter
            health_status: Health status to filter by

        Returns:
            List of modules with matching health status
        """
        return [m for m in snapshot.module_coverages if m.health_status == health_status]

    def _count_by_health_status(self, snapshot: CoverageSnapshot) -> dict[str, int]:
        """Count modules by health status.

        Args:
            snapshot: Coverage snapshot to analyze

        Returns:
            Dictionary with counts of modules at each health status
        """
        health_counts: dict[str, int] = {
            "healthy": 0,
            "at_risk": 0,
            "critical": 0,
        }
        for module in snapshot.module_coverages:
            if module.health_status in health_counts:
                health_counts[module.health_status] += 1
        return health_counts

    def _get_average_coverage(
        self,
        snapshot: CoverageSnapshot,
        metric_type: Literal["statement", "branch", "line"],
    ) -> float:
        """Calculate average coverage across all modules for a metric type.

        Args:
            snapshot: Coverage snapshot to analyze
            metric_type: Type of metric to average

        Returns:
            Average coverage percentage
        """
        if not snapshot.module_coverages:
            return 0.0

        if metric_type == "statement":
            values: list[float] = [m.statement_coverage_pct for m in snapshot.module_coverages]
        elif metric_type == "branch":
            values = [m.branch_coverage_pct for m in snapshot.module_coverages]
        else:
            values = [m.line_coverage_pct for m in snapshot.module_coverages]

        return sum(values) / len(values) if values else 0.0

    def _get_min_coverage_module(
        self,
        snapshot: CoverageSnapshot,
        metric_type: Literal["statement", "branch", "line"],
    ) -> ModuleCoverage | None:
        """Find module with lowest coverage for a metric type.

        Args:
            snapshot: Coverage snapshot to search
            metric_type: Type of metric to evaluate

        Returns:
            Module with minimum coverage, or None if no modules
        """
        if not snapshot.module_coverages:
            return None

        if metric_type == "statement":
            min_module: ModuleCoverage = min(
                snapshot.module_coverages,
                key=lambda m: m.statement_coverage_pct,
            )
        elif metric_type == "branch":
            min_module = min(
                snapshot.module_coverages,
                key=lambda m: m.branch_coverage_pct,
            )
        else:
            min_module = min(
                snapshot.module_coverages,
                key=lambda m: m.line_coverage_pct,
            )

        return min_module

    def _get_max_coverage_module(
        self,
        snapshot: CoverageSnapshot,
        metric_type: Literal["statement", "branch", "line"],
    ) -> ModuleCoverage | None:
        """Find module with highest coverage for a metric type.

        Args:
            snapshot: Coverage snapshot to search
            metric_type: Type of metric to evaluate

        Returns:
            Module with maximum coverage, or None if no modules
        """
        if not snapshot.module_coverages:
            return None

        if metric_type == "statement":
            max_module: ModuleCoverage = max(
                snapshot.module_coverages,
                key=lambda m: m.statement_coverage_pct,
            )
        elif metric_type == "branch":
            max_module = max(
                snapshot.module_coverages,
                key=lambda m: m.branch_coverage_pct,
            )
        else:
            max_module = max(
                snapshot.module_coverages,
                key=lambda m: m.line_coverage_pct,
            )

        return max_module

    def _should_alert_on_module(self, module: ModuleCoverage, threshold: float) -> bool:
        """Determine if a module should trigger an alert based on health status.

        Args:
            module: Module to evaluate
            threshold: Threshold for alert

        Returns:
            True if module health indicates alert is needed
        """
        is_critical: bool = module.health_status == "critical"
        is_below_threshold: bool = module.statement_coverage_pct < threshold
        return is_critical and is_below_threshold


def calculate_module_coverage_average(
    modules: list[ModuleCoverage],
    metric_type: Literal["statement", "branch", "line"],
) -> float:
    """Calculate average coverage across modules for a metric type.

    Args:
        modules: List of module coverage objects
        metric_type: Type of metric to average

    Returns:
        Average coverage percentage
    """
    if not modules:
        return 0.0

    if metric_type == "statement":
        values: list[float] = [m.statement_coverage_pct for m in modules]
    elif metric_type == "branch":
        values = [m.branch_coverage_pct for m in modules]
    else:
        values = [m.line_coverage_pct for m in modules]

    average: float = sum(values) / len(values) if values else 0.0
    return average


def get_module_health_summary(modules: list[ModuleCoverage]) -> dict[str, int]:
    """Get count of modules at each health status level.

    Args:
        modules: List of module coverage objects

    Returns:
        Dictionary with counts at each health level
    """
    summary: dict[str, int] = {
        "healthy": 0,
        "at_risk": 0,
        "critical": 0,
    }

    for module in modules:
        if module.health_status in summary:
            summary[module.health_status] += 1

    return summary
