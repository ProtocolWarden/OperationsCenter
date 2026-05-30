# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import importlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Callable

import pytest

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


# ============================================================================
# Import Error Test Fixtures
# ============================================================================


@pytest.fixture
def optional_import(request: pytest.FixtureRequest) -> Callable | types.ModuleType:
    """
    Skip test if module cannot be imported.

    Use with parametrize + indirect=True:
        @pytest.mark.parametrize('optional_import', ['module.path'], indirect=True)
        def test_foo(optional_import):
            module = optional_import

    Or use as a function in test:
        def test_bar(optional_import):
            module = optional_import('module.path')
    """

    def _import_optional(module_path: str) -> types.ModuleType:
        try:
            return importlib.import_module(module_path)
        except (ImportError, ModuleNotFoundError) as e:
            pytest.skip(f"Module '{module_path}' import failed: {type(e).__name__}: {e}")

    if hasattr(request, 'param') and request.param is not None:
        return _import_optional(request.param)
    return _import_optional


@pytest.fixture
def require_module(request: pytest.FixtureRequest) -> Callable | types.ModuleType:
    """
    Assert module is importable (test fails if unavailable).

    Use with parametrize + indirect=True:
        @pytest.mark.parametrize('require_module', ['module.path'], indirect=True)
        def test_foo(require_module):
            module = require_module

    Or use as a function in test:
        def test_bar(require_module):
            module = require_module('module.path')
    """

    def _import_required(module_path: str) -> types.ModuleType:
        try:
            return importlib.import_module(module_path)
        except (ImportError, ModuleNotFoundError) as e:
            raise AssertionError(f"Required module '{module_path}' could not be imported: {type(e).__name__}: {e}")

    if hasattr(request, 'param') and request.param is not None:
        return _import_required(request.param)
    return _import_required


@pytest.fixture
def module_with_env(request: pytest.FixtureRequest) -> Callable:
    """
    Re-import module with environment variables set and module cache cleared.

    Usage:
        def test_import_with_env(module_with_env):
            module = module_with_env(
                module_path='module.path',
                env={'ENV_VAR': 'value'}
            )

    Environment variables are restored automatically after test.
    """
    saved_env: dict[str, str | None] = {}

    def _import_with_env(
        module_path: str,
        env: dict[str, str],
        clear_cache: bool = True,
    ) -> types.ModuleType:
        nonlocal saved_env

        if clear_cache and module_path in sys.modules:
            del sys.modules[module_path]

        for key, value in env.items():
            saved_env[key] = os.environ.get(key)
            os.environ[key] = value

        try:
            return importlib.import_module(module_path)
        finally:
            for key, original_value in saved_env.items():
                if original_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original_value
            saved_env.clear()

    yield _import_with_env


@pytest.fixture
def assert_module_unavailable(request: pytest.FixtureRequest) -> Callable:
    """
    Assert that a module cannot be imported (expects ModuleNotFoundError).

    Usage:
        def test_removed_module(assert_module_unavailable):
            assert_module_unavailable('operations_center.legacy_module')
            assert_module_unavailable('operations_center.deprecated_path')
    """

    def _assert_unavailable(module_path: str) -> None:
        try:
            importlib.import_module(module_path)
        except (ImportError, ModuleNotFoundError):
            return
        raise AssertionError(f"Expected ModuleNotFoundError, but '{module_path}' imported successfully")

    return _assert_unavailable



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
        slow_count = sum(1 for _, d, marked in self.test_durations if d >= self.threshold or marked)
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
    # Skip reporting on xdist worker processes — they each call this hook;
    # only the master (or non-xdist) session should emit the report.
    if os.environ.get("PYTEST_XDIST_WORKER"):
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


# ============================================================================
# Dependency Report Fixtures (Performance Regression Testing)
# ============================================================================


@pytest.fixture
def report_fixture_dir(tmp_path: Path) -> Path:
    """Temporary directory for synthetic dependency report files."""
    report_dir = tmp_path / "dependency_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _write_report_to_disk(
    report_root: Path, data: dict[str, Any]
) -> Path:
    """Write dependency report data to disk as JSON."""
    from uuid import uuid4

    run_dir = report_root / f"run_{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)

    report_file = run_dir / "dependency_report.json"
    report_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return report_file


@pytest.fixture
def baseline_report_on_disk(
    report_fixture_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Generate and write baseline report (7 deps, 0 actionable) to disk."""
    from tests.fixtures.dependency_reports.generators import DependencyReportGenerator

    gen = DependencyReportGenerator.baseline()
    data = gen.to_dict()
    report_path = _write_report_to_disk(report_fixture_dir, data)
    return report_path, data


@pytest.fixture
def large_simple_report_on_disk(
    report_fixture_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Generate and write large-simple report (20 deps, 10% actionable) to disk."""
    from tests.fixtures.dependency_reports.generators import DependencyReportGenerator

    gen = DependencyReportGenerator.large_simple()
    data = gen.to_dict()
    report_path = _write_report_to_disk(report_fixture_dir, data)
    return report_path, data


@pytest.fixture
def large_actionable_report_on_disk(
    report_fixture_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Generate and write large-actionable report (10 deps, 80% actionable) to disk."""
    from tests.fixtures.dependency_reports.generators import DependencyReportGenerator

    gen = DependencyReportGenerator.large_actionable()
    data = gen.to_dict()
    report_path = _write_report_to_disk(report_fixture_dir, data)
    return report_path, data


@pytest.fixture
def large_payload_report_on_disk(
    report_fixture_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Generate and write large-payload report (8 deps with verbose notes) to disk."""
    from tests.fixtures.dependency_reports.generators import DependencyReportGenerator

    gen = DependencyReportGenerator.large_payload()
    data = gen.to_dict()
    report_path = _write_report_to_disk(report_fixture_dir, data)
    return report_path, data


@pytest.fixture
def extra_large_report_on_disk(
    report_fixture_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Generate and write extra-large report (50 deps, stress test) to disk."""
    from tests.fixtures.dependency_reports.generators import DependencyReportGenerator

    gen = DependencyReportGenerator.extra_large()
    data = gen.to_dict()
    report_path = _write_report_to_disk(report_fixture_dir, data)
    return report_path, data
