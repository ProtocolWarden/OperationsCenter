# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Flaky Test Aggregator — Historical analysis and trend detection.

Implements Tier 3 aggregation: loads session reports from past N days,
computes aggregate statistics, detects new flaky tests, and generates
recommendations for fixing flakiness.

Usage:
    aggregator = FlakyTestAggregator(storage)
    agg_report = aggregator.aggregate(days=7)
    agg_report.save()
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .flaky_test_storage import FlakyTestAggregationReport, FlakyTestStorageManager


class FlakyTestAggregator:
    """Aggregates session reports into historical trends and metrics."""

    def __init__(self, storage: FlakyTestStorageManager):
        """Initialize aggregator with storage backend.

        Args:
            storage: Storage manager instance
        """
        self.storage = storage

    def aggregate(self, days: int = 7) -> FlakyTestAggregationReport:
        """Aggregate flakiness metrics over a time period.

        Args:
            days: Number of days to aggregate

        Returns:
            Aggregation report with statistics and recommendations
        """
        sessions = self.storage.load_recent_sessions(days=days)

        if not sessions:
            return FlakyTestAggregationReport(
                date=datetime.now(UTC).strftime("%Y-%m-%d"),
                period_days=days,
                total_test_executions=0,
                flaky_test_count=0,
                unstable_test_count=0,
            )

        # Aggregate per-test metrics across all sessions
        test_metrics: dict[str, list[dict]] = {}
        total_executions = 0

        for session in sessions:
            total_executions += session.get("session_count", 0)

            # Collect flaky test candidates
            for flaky_test in session.get("flaky_candidates", []):
                test_name = flaky_test["test_name"]
                if test_name not in test_metrics:
                    test_metrics[test_name] = []
                test_metrics[test_name].append(flaky_test)

            # Collect unstable tests
            for unstable_test in session.get("unstable_candidates", []):
                test_name = unstable_test["test_name"]
                if test_name not in test_metrics:
                    test_metrics[test_name] = []
                test_metrics[test_name].append(unstable_test)

        # Compute aggregate statistics
        flaky_tests = []
        flaky_count = 0
        unstable_count = 0
        module_stats: dict[str, dict] = {}
        category_stats: dict[str, dict] = {}

        for test_name, metrics_list in test_metrics.items():
            if not metrics_list:
                continue

            # Compute aggregate metrics
            failure_rates = [m.get("failure_rate", 0) for m in metrics_list]
            avg_failure_rate = sum(failure_rates) / len(failure_rates)
            max_failure_rate = max(failure_rates)
            first_seen = min(m.get("first_seen", datetime.now(UTC).isoformat()) for m in metrics_list)
            last_failure = max(m.get("last_failure", "") for m in metrics_list)

            # Determine trend
            if len(failure_rates) >= 2:
                trend = (failure_rates[-1] - failure_rates[0]) / failure_rates[0] if failure_rates[0] > 0 else 0
            else:
                trend = 0

            # Check if recovered
            recovered_at = None
            if len(failure_rates) >= 2 and failure_rates[-1] < 0.1:
                recovered_at = datetime.now(UTC).isoformat()

            agg_metric = {
                "test_name": test_name,
                "failure_rate": avg_failure_rate,
                "max_failure_rate": max_failure_rate,
                "run_count": len(metrics_list),
                "trend": trend,
                "first_seen": first_seen,
                "last_failure": last_failure,
                "recovered_at": recovered_at,
                "category": metrics_list[0].get("category", "unknown"),
            }

            # Extract module from test name
            module = test_name.split("::")[0] if "::" in test_name else test_name.split("/")[0]
            if module not in module_stats:
                module_stats[module] = {"flaky_count": 0, "total_count": 0}
            module_stats[module]["total_count"] += 1
            if avg_failure_rate > 0.1:
                module_stats[module]["flaky_count"] += 1

            # Categorize by flakiness level
            if avg_failure_rate > 0.1:
                flaky_tests.append(agg_metric)
                flaky_count += 1
                category = agg_metric["category"]
                if category not in category_stats:
                    category_stats[category] = 0
                category_stats[category] += 1
            elif avg_failure_rate > 0.05:
                unstable_count += 1

        # Sort by failure rate descending
        flaky_tests.sort(key=lambda x: x["failure_rate"], reverse=True)

        # Generate recommendations
        recommendations = self._generate_recommendations(flaky_tests, module_stats)

        return FlakyTestAggregationReport(
            date=datetime.now(UTC).strftime("%Y-%m-%d"),
            period_days=days,
            total_test_executions=total_executions,
            flaky_test_count=flaky_count,
            unstable_test_count=unstable_count,
            flaky_tests=flaky_tests[:20],  # Top 20
            by_module={k: v for k, v in sorted(module_stats.items(), key=lambda x: x[1]["flaky_count"], reverse=True)[:10]},
            by_category={k: v for k, v in sorted(category_stats.items(), key=lambda x: -x[1])},
            recommendations=recommendations,
        )

    def _generate_recommendations(self, flaky_tests: list[dict], module_stats: dict) -> list[dict]:
        """Generate actionable recommendations for fixing flakiness.

        Args:
            flaky_tests: List of flaky test metrics
            module_stats: Module-level statistics

        Returns:
            List of recommendations with priority
        """
        recommendations = []

        # Recommendation 1: Focus on top flaky tests
        if flaky_tests:
            top_test = flaky_tests[0]
            recommendations.append({
                "priority": "high",
                "type": "focus_test",
                "description": f"Fix top flaky test: {top_test['test_name']}",
                "failure_rate": top_test["failure_rate"],
                "category": top_test.get("category", "unknown"),
            })

        # Recommendation 2: Module outbreak detection
        outbreak_modules = [m for m, stats in module_stats.items() if stats["flaky_count"] / max(1, stats["total_count"]) > 0.2]
        if outbreak_modules:
            recommendations.append({
                "priority": "high",
                "type": "module_outbreak",
                "description": f"Module outbreak detected in: {', '.join(outbreak_modules[:3])}",
                "affected_modules": outbreak_modules,
            })

        # Recommendation 3: Environmental/configuration issues
        config_flaky = [t for t in flaky_tests if t.get("category") == "configuration"]
        if config_flaky:
            recommendations.append({
                "priority": "medium",
                "type": "environment_check",
                "description": "Check environment configuration for CI differences",
                "tests": [t["test_name"] for t in config_flaky[:3]],
            })

        # Recommendation 4: Check for recovery patterns
        recovered_tests = [t for t in flaky_tests if t.get("recovered_at")]
        if recovered_tests:
            recommendations.append({
                "priority": "low",
                "type": "monitor_recovery",
                "description": f"Monitor {len(recovered_tests)} recovered tests for regression",
                "recovered_count": len(recovered_tests),
            })

        return recommendations
