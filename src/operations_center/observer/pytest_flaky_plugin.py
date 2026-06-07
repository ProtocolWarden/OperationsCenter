# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest Flaky Test Detection Plugin — Captures test outcomes and analyzes flakiness.

Integrates with pytest execution to:
1. Capture test outcomes (passed/failed/skipped)
2. Track test duration and exception info
3. Analyze session results for flakiness patterns
4. Save metrics to storage for historical tracking

Usage:
    pytest tests/ --flaky-detection
    # Metrics saved to .flaky-tests/runs/YYYY-MM-DD/HH-MM-SS-session.json

The plugin is opt-in (disabled by default) to avoid overhead in normal test runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


class FlakyTestDetectionPlugin:
    """Pytest plugin for flaky test detection and metrics collection."""

    def __init__(self, flaky_storage_path: str | None = None):
        """Initialize plugin.

        Args:
            flaky_storage_path: Directory to save flaky test metrics
        """
        self.flaky_storage_path = Path(flaky_storage_path or ".flaky-tests")
        self.flaky_storage_path.mkdir(parents=True, exist_ok=True)

        self.test_outcomes: dict[str, dict] = {}
        self.session_start_time = None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Hook into test session start.

        Args:
            session: Pytest session object
        """
        self.session_start_time = datetime.now(UTC)
        self.test_outcomes = {}

    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo) -> None:
        """Hook into test execution to capture outcomes.

        Args:
            item: Pytest item (test)
            call: Call info (setup/call/teardown)
        """
        if call.when == "call":  # Only capture main test execution, not setup/teardown
            test_name = item.nodeid
            outcome = "passed" if call.excinfo is None else "failed"

            if test_name not in self.test_outcomes:
                self.test_outcomes[test_name] = {
                    "test_name": test_name,
                    "outcome": outcome,
                    "duration": call.duration or 0,
                    "exception": str(call.excinfo.value) if call.excinfo else None,
                }
            else:
                # Update with actual result
                self.test_outcomes[test_name].update({
                    "outcome": outcome,
                    "duration": call.duration or 0,
                    "exception": str(call.excinfo.value) if call.excinfo else None,
                })

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Hook into test session end - analyze and save results.

        Args:
            session: Pytest session object
            exitstatus: Exit status code
        """
        if not self.test_outcomes:
            return

        # Analyze flakiness patterns
        flaky_candidates = []
        unstable_candidates = []
        passed_count = sum(1 for t in self.test_outcomes.values() if t["outcome"] == "passed")
        failed_count = sum(1 for t in self.test_outcomes.values() if t["outcome"] == "failed")

        for test_name, result in self.test_outcomes.items():
            # Single run can't show flakiness, but we can flag for monitoring
            # In multi-run scenarios, this would track historical patterns
            if result["outcome"] == "failed":
                # Extract module from test name
                module = test_name.split("::")[0] if "::" in test_name else ""

                flaky_candidates.append({
                    "test_name": test_name,
                    "module": module,
                    "failure_rate": 1.0,  # Single run shows as 100% failure
                    "run_count": 1,
                    "category": "unknown",
                    "first_seen": datetime.now(UTC).isoformat(),
                })

        # Build session report
        session_report = {
            "session_id": session.name or "default",
            "timestamp": datetime.now(UTC).isoformat(),
            "duration": (datetime.now(UTC) - self.session_start_time).total_seconds(),
            "session_count": len(self.test_outcomes),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "skipped_count": sum(1 for t in self.test_outcomes.values() if t["outcome"] == "skipped"),
            "flaky_candidates": flaky_candidates,
            "unstable_candidates": unstable_candidates,
            "test_outcomes": list(self.test_outcomes.values()),
        }

        # Save to storage
        self._save_session_report(session_report)

    def _save_session_report(self, report: dict) -> None:
        """Save session report to storage.

        Args:
            report: Session analysis report
        """
        timestamp = datetime.now(UTC)
        date_dir = self.flaky_storage_path / "runs" / timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        filename = timestamp.strftime("%H-%M-%S") + "-session.json"
        filepath = date_dir / filename

        try:
            with open(filepath, "w") as f:
                json.dump(report, f, indent=2)
        except IOError as e:
            # Silently fail - don't interrupt test execution
            print(f"Warning: Failed to save flaky test metrics: {e}")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add pytest command-line options.

    Args:
        parser: Pytest parser
    """
    parser.addoption(
        "--flaky-detection",
        action="store_true",
        default=False,
        help="Enable flaky test detection metrics collection",
    )
    parser.addoption(
        "--flaky-storage",
        action="store",
        default=".flaky-tests",
        help="Directory to save flaky test metrics (default: .flaky-tests)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with flaky detection plugin if enabled.

    Args:
        config: Pytest config
    """
    if config.getoption("--flaky-detection"):
        storage_path = config.getoption("--flaky-storage")
        plugin = FlakyTestDetectionPlugin(storage_path)
        config.pluginmanager.register(plugin, "flaky_detection")
