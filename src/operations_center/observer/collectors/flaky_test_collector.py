# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""FlakyTestCollector — Collects and synthesizes flaky test detection signals.

Reads historical test metrics and produces FlakyTestSignal for RepoStateSnapshot.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.observer.flaky_test_reporter import (
    FlakyTestConfig,
    FlakyTestMetric,
    FlakynessCategory,
)
from operations_center.observer.models import FlakyTestSignal
from operations_center.observer.service import ObserverContext

logger = logging.getLogger(__name__)


class FlakyTestCollector:
    """Collects and synthesizes flaky test signals from historical metrics storage.

    Reads metrics from historical storage (local JSONL files), analyzes trends,
    and produces a FlakyTestSignal for inclusion in RepoStateSnapshot.
    """

    def __init__(self, config: FlakyTestConfig) -> None:
        self.config = config

    def collect(self, context: ObserverContext) -> FlakyTestSignal:
        """Collect flaky test metrics and synthesize FlakyTestSignal.

        Args:
            context: Observer context with repo and storage information.

        Returns:
            FlakyTestSignal with synthesis of historical metrics.
        """
        metrics = self._load_metrics()

        if not metrics:
            return FlakyTestSignal(status="unavailable")

        flaky_count = sum(1 for m in metrics if m.failure_rate > self.config.flakiness_threshold)
        unstable_count = sum(
            1
            for m in metrics
            if self.config.unstable_threshold <= m.failure_rate <= self.config.flakiness_threshold
        )

        affected_modules = set()
        for metric in metrics:
            module = self._extract_module(metric.nodeid)
            if module:
                affected_modules.add(module)

        most_problematic = sorted(metrics, key=lambda m: m.flakiness_score, reverse=True)[:5]

        category_breakdown = self._compute_category_breakdown(metrics)
        estimated_impact = self._estimate_impact(metrics)

        return FlakyTestSignal(
            status="measured" if metrics else "partial",
            flaky_test_count=flaky_count,
            unstable_test_count=unstable_count,
            affected_modules=sorted(affected_modules),
            most_problematic_tests=[m.to_dict() for m in most_problematic],
            failure_rate_trend=0.0,  # TODO: Implement trend comparison
            recovery_rate=0.0,  # TODO: Implement recovery tracking
            category_breakdown=category_breakdown,
            estimated_impact=estimated_impact,
            observed_at=datetime.now(UTC),
            summary=self._generate_summary(
                flaky_count,
                unstable_count,
                len(affected_modules),
                len(metrics),
            ),
        )

    def _load_metrics(self) -> list[FlakyTestMetric]:
        """Load historical test metrics from storage.

        Returns:
            List of FlakyTestMetric objects loaded from JSONL files.
        """
        storage_root = self.config.storage_root
        if isinstance(storage_root, str) and (
            storage_root.startswith("s3://") or storage_root.startswith("http://")
        ):
            logger.debug("Remote storage not yet supported: %s", storage_root)
            return []

        if isinstance(storage_root, str):
            storage_root = Path(storage_root)

        if not storage_root.exists():
            logger.debug("Storage root not found: %s", storage_root)
            return []

        metrics = []
        metrics_dir = storage_root / "metrics"
        if not metrics_dir.exists():
            return []

        cutoff_date = datetime.now(UTC) - timedelta(days=self.config.historical_window_days)

        for metrics_file in sorted(metrics_dir.glob("*.jsonl"), reverse=True):
            try:
                with metrics_file.open("r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            # Skip old metrics
                            if "timestamp" in data:
                                ts = datetime.fromisoformat(data["timestamp"])
                                if ts < cutoff_date:
                                    continue
                            metric = self._dict_to_metric(data)
                            if metric:
                                metrics.append(metric)
                        except (json.JSONDecodeError, ValueError) as e:
                            logger.debug("Failed to parse metric line: %s", e)
                            continue
            except OSError as e:
                logger.debug("Failed to read metrics file %s: %s", metrics_file, e)
                continue

        return metrics

    def _dict_to_metric(self, data: dict) -> FlakyTestMetric | None:
        """Convert dictionary to FlakyTestMetric.

        Args:
            data: Dictionary representation of metric.

        Returns:
            FlakyTestMetric or None if data is invalid.
        """
        try:
            return FlakyTestMetric(
                nodeid=data.get("nodeid", ""),
                failure_rate=float(data.get("failure_rate", 0.0)),
                run_count=int(data.get("run_count", 0)),
                retry_success_count=int(data.get("retry_success_count", 0)),
                duration_mean=float(data.get("duration_mean", 0.0)),
                duration_variance=float(data.get("duration_variance", 0.0)),
                pattern_entropy=float(data.get("pattern_entropy", 0.0)),
                streak_length=int(data.get("streak_length", 0)),
                recovery_time_days=float(data.get("recovery_time_days"))
                if "recovery_time_days" in data and data["recovery_time_days"] is not None
                else None,
                suspected_category=FlakynessCategory(data.get("suspected_category", "unknown")),
                flakiness_score=float(data.get("flakiness_score", 0.0)),
                confidence=float(data.get("confidence", 0.0)),
                markers=data.get("markers", []),
                last_failure_reason=data.get("last_failure_reason", ""),
            )
        except (TypeError, ValueError, KeyError) as e:
            logger.debug("Failed to convert metric data: %s", e)
            return None

    def _extract_module(self, nodeid: str) -> str | None:
        """Extract module path from test node ID.

        Args:
            nodeid: Test node ID (e.g., 'tests/unit/test_foo.py::TestClass::test_method')

        Returns:
            Module path (e.g., 'tests/unit') or None if not extractable.
        """
        if not nodeid:
            return None

        parts = nodeid.split("::")
        if not parts:
            return None

        path_part = parts[0]
        if not path_part:
            return None

        path_components = path_part.split("/")

        if len(path_components) >= 2:
            return "/".join(path_components[:2])
        elif path_components and path_components[0]:
            return path_components[0]

        return None

    def _compute_category_breakdown(self, metrics: list[FlakyTestMetric]) -> dict[str, int]:
        """Compute breakdown of flaky tests by category.

        Args:
            metrics: List of FlakyTestMetric objects.

        Returns:
            Dictionary with category names as keys and counts as values.
        """
        breakdown: dict[str, int] = {}

        for metric in metrics:
            if metric.failure_rate > self.config.flakiness_threshold:
                category = metric.suspected_category.value
                breakdown[category] = breakdown.get(category, 0) + 1

        return breakdown

    def _estimate_impact(self, metrics: list[FlakyTestMetric]) -> dict[str, float]:
        """Estimate impact of flaky tests on CI and developer time.

        Args:
            metrics: List of FlakyTestMetric objects.

        Returns:
            Dictionary with impact metrics.
        """
        flaky_metrics = [m for m in metrics if m.failure_rate > self.config.flakiness_threshold]

        if not flaky_metrics:
            return {"ci_slowdown_percent": 0.0, "dev_hours_per_month": 0.0}

        avg_duration = sum(m.duration_mean for m in flaky_metrics) / len(flaky_metrics)
        flakiness_burden = sum(m.failure_rate for m in flaky_metrics) / len(flaky_metrics)

        # Rough estimation: 20 CI runs per developer per month * avg_duration * flakiness_burden
        dev_hours = (20 * avg_duration * flakiness_burden) / 3600
        ci_slowdown = flakiness_burden * 100

        return {
            "ci_slowdown_percent": round(ci_slowdown, 2),
            "dev_hours_per_month": round(dev_hours, 2),
        }

    def _generate_summary(
        self, flaky_count: int, unstable_count: int, module_count: int, total_count: int
    ) -> str:
        """Generate human-readable summary of flaky test status.

        Args:
            flaky_count: Number of flaky tests (>10% failure rate).
            unstable_count: Number of unstable tests (5-10% failure rate).
            module_count: Number of affected modules.
            total_count: Total number of metrics analyzed.

        Returns:
            Human-readable summary string.
        """
        if not total_count:
            return "No test metrics available."

        parts = []

        if flaky_count > 0:
            parts.append(f"{flaky_count} flaky test{'s' if flaky_count != 1 else ''}")

        if unstable_count > 0:
            parts.append(f"{unstable_count} unstable test{'s' if unstable_count != 1 else ''}")

        if module_count > 0:
            parts.append(f"affecting {module_count} module{'s' if module_count != 1 else ''}")

        if not parts:
            return f"All {total_count} tests are stable."

        return f"Found {', '.join(parts)} out of {total_count} total tests."
