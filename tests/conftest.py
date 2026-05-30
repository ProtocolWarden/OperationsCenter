# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import os
import sys
from pathlib import Path
from typing import Any

# Guard: tests must run inside this project's own .venv, not bare Python or a
# foreign venv. A foreign venv has a different package set and produces
# misleading results (wrong versions, missing packages, extra packages).
#
# The guard is skipped when:
#   1. The .venv directory does not exist (e.g. CI installs into system Python).
#   2. A CI environment variable is set (CI or GITHUB_ACTIONS).
_REPO_ROOT = Path(__file__).parent.parent.resolve()
_EXPECTED_VENV = (_REPO_ROOT / ".venv").resolve()
_ACTIVE_PREFIX = Path(sys.prefix).resolve()
_IN_CI = os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")

import json

if _EXPECTED_VENV.is_dir() and not _IN_CI and _ACTIVE_PREFIX != _EXPECTED_VENV:
    raise SystemExit(
        f"ERROR: Tests must be run inside this project's virtual environment.\n"
        f"Expected: {_EXPECTED_VENV}\n"
        f"Active:   {_ACTIVE_PREFIX}\n\n"
        f"Activate it first:\n"
        f"  source .venv/bin/activate\n"
        f"Or invoke pytest through the venv directly:\n"
        f"  .venv/bin/pytest"
    )


class SlowTestTracker:
    """Track test durations and identify slow tests exceeding a threshold."""

    def __init__(self, threshold_seconds: float = 1.0, json_report_path: str | None = None):
        self.threshold = threshold_seconds
        self.test_durations: list[tuple[str, float, bool]] = []
        self.test_markers: dict[str, bool] = {}
        self.json_report_path = json_report_path

    def record_item_markers(self, nodeid: str, is_marked_slow: bool) -> None:
        """Record slow marker status for a test item (called during setup)."""
        self.test_markers[nodeid] = is_marked_slow

    def record_test(self, nodeid: str, duration: float) -> None:
        """Record a test's execution duration."""
        is_marked = self.test_markers.get(nodeid, False)
        self.test_durations.append((nodeid, duration, is_marked))

    def get_slow_tests(self) -> list[tuple[str, float, bool]]:
        """Return tests exceeding the threshold or marked slow, sorted by duration descending."""
        slow = [
            (nodeid, duration, is_marked)
            for nodeid, duration, is_marked in self.test_durations
            if duration >= self.threshold or is_marked
        ]
        return sorted(slow, key=lambda x: x[1], reverse=True)

    def get_statistics(self) -> dict[str, Any]:
        """Return summary statistics about test durations."""
        if not self.test_durations:
            return {"total": 0, "slow_count": 0, "avg_duration": 0.0, "max_duration": 0.0}

        durations = [d for _, d, _ in self.test_durations]
        slow_count = sum(1 for _, d, _ in self.test_durations if d >= self.threshold)
        return {
            "total": len(self.test_durations),
            "slow_count": slow_count,
            "avg_duration": sum(durations) / len(durations),
            "max_duration": max(durations),
            "threshold": self.threshold,
        }

    def generate_json_report(self) -> dict[str, Any]:
        """Generate JSON report of slow tests and statistics."""
        slow_tests = self.get_slow_tests()
        stats = self.get_statistics()

        marked_slow = [
            {"test": nodeid, "duration": duration, "marked": True}
            for nodeid, duration, is_marked in slow_tests
            if is_marked
        ]
        threshold_slow = [
            {"test": nodeid, "duration": duration, "marked": False}
            for nodeid, duration, is_marked in slow_tests
            if not is_marked
        ]

        return {
            "version": "1.0",
            "threshold_seconds": stats["threshold"],
            "total_tests": stats["total"],
            "slow_tests_count": stats["slow_count"],
            "statistics": {
                "average_duration": round(stats["avg_duration"], 3),
                "max_duration": round(stats["max_duration"], 3),
            },
            "slow_tests": {
                "threshold_exceeded": sorted(threshold_slow, key=lambda x: x["duration"], reverse=True),
                "marked_slow": sorted(marked_slow, key=lambda x: x["duration"], reverse=True),
            },
        }

    def write_json_report(self) -> bool:
        """Write JSON report to file. Returns True if successful."""
        if not self.json_report_path:
            return False

        try:
            report = self.generate_json_report()
            with open(self.json_report_path, "w") as f:
                json.dump(report, f, indent=2)
            return True
        except Exception as e:
            print(f"Warning: Failed to write JSON report to {self.json_report_path}: {e}")
            return False


_slow_test_tracker: SlowTestTracker | None = None


def pytest_configure(config: Any) -> None:
    """Initialize slow test tracker at session start."""
    global _slow_test_tracker
    threshold = config.option.slow_threshold if hasattr(config.option, "slow_threshold") else 1.0
    json_report = config.option.slow_report if hasattr(config.option, "slow_report") else None
    _slow_test_tracker = SlowTestTracker(threshold_seconds=float(threshold), json_report_path=json_report)


def pytest_addoption(parser: Any) -> None:
    """Add custom command-line options."""
    parser.addoption(
        "--slow-threshold",
        action="store",
        default="1.0",
        type=str,
        help="Duration threshold in seconds for marking tests as slow (default: 1.0)",
    )
    parser.addoption(
        "--slow-report",
        action="store",
        default=None,
        type=str,
        help="Write slow test report to JSON file (e.g., --slow-report=slow_tests.json)",
    )


def pytest_runtest_setup(item: Any) -> None:
    """Record slow marker status before test execution."""
    if _slow_test_tracker is None:
        return

    is_marked_slow = item.get_closest_marker("slow") is not None
    _slow_test_tracker.record_item_markers(item.nodeid, is_marked_slow)


def pytest_runtest_logreport(report: Any) -> None:
    """Record test duration after execution completes."""
    if _slow_test_tracker is None:
        return

    if report.when == "call" and report.outcome != "skipped":
        _slow_test_tracker.record_test(report.nodeid, report.duration)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Emit slow test warnings and summary at session end."""
    if _slow_test_tracker is None:
        return

    slow_tests = _slow_test_tracker.get_slow_tests()
    stats = _slow_test_tracker.get_statistics()

    if not slow_tests or stats["total"] == 0:
        # Still write JSON report even if no slow tests
        _slow_test_tracker.write_json_report()
        return

    print("\n" + "=" * 80)
    print(f"SLOW TEST THRESHOLD WARNING (threshold: {stats['threshold']:.2f}s)")
    print("=" * 80)

    marked_slow = [t for t in slow_tests if t[2]]
    threshold_slow = [t for t in slow_tests if not t[2]]

    if threshold_slow:
        print(f"\n⚠️  {len(threshold_slow)} test(s) exceeded the threshold:\n")
        for nodeid, duration, _ in threshold_slow:
            print(f"  {duration:7.3f}s  {nodeid}")

    if marked_slow:
        print(f"\n📌 {len(marked_slow)} test(s) marked @pytest.mark.slow:\n")
        for nodeid, duration, _ in marked_slow:
            marker_note = "(also exceeds threshold)" if duration >= stats["threshold"] else ""
            print(f"  {duration:7.3f}s  {nodeid} {marker_note}")

    print("\n" + "-" * 80)
    print(f"Summary: {stats['slow_count']}/{stats['total']} slow tests")
    print(f"  Average duration: {stats['avg_duration']:.3f}s")
    print(f"  Max duration: {stats['max_duration']:.3f}s")
    print("=" * 80 + "\n")

    # Write JSON report if requested
    if _slow_test_tracker.write_json_report():
        print(f"Slow test report written to: {_slow_test_tracker.json_report_path}\n")
