# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from operations_center.observer.collectors.check_signal import CheckSignalCollector
from operations_center.observer.service import ObserverContext


def _make_context(tmp_path: Path) -> ObserverContext:
    """Build a minimal ObserverContext pointing at *tmp_path*."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return ObserverContext(
        repo_path=repo,
        repo_name="test-repo",
        base_branch="main",
        run_id="obs_test",
        observed_at=datetime(2026, 4, 15, tzinfo=UTC),
        source_command="test",
        settings=None,  # type: ignore[arg-type]
        commit_limit=10,
        hotspot_window=7,
        todo_limit=5,
        logs_root=logs,
    )


# ------------------------------------------------------------------
# 1. Existing behaviour: log file present → read it
# ------------------------------------------------------------------


def test_existing_log_file_passed(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "unit_test.log"
    log.write_text("collected 3 items\n3 passed in 0.5s\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "passed"
    assert sig.source == str(log)
    assert sig.summary is not None and "passed" in sig.summary


def test_existing_log_file_failed(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "integration_test.log"
    log.write_text("collected 5 items\n2 failed, 3 passed in 1.2s\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "failed"


def test_existing_log_file_unknown(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "smoke_test.log"
    log.write_text("some unrecognisable output\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 2. Fallback: discoverable (pytest --collect-only succeeds)
# ------------------------------------------------------------------


def test_discoverable_with_pyproject(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    pyproject = ctx.repo_path / "pyproject.toml"
    pyproject.write_text("[tool.pytest.ini_options]\naddopts = '-v'\n")

    collect_stdout = (
        "tests/test_foo.py::test_bar\ntests/test_foo.py::test_baz\n\n2 tests collected\n"
    )
    fake_result = subprocess.CompletedProcess(
        args=["pytest", "--collect-only", "-q", "--no-header"],
        returncode=0,
        stdout=collect_stdout,
        stderr="",
    )

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        return_value=fake_result,
    ) as mock_run:
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "discoverable"
    assert sig.test_count == 2
    assert sig.source is not None and "collect-only" in sig.source
    assert sig.summary == "2 tests discoverable"
    mock_run.assert_called_once()


def test_discoverable_with_pytest_ini(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\naddopts = -v\n")

    collect_stdout = "tests/test_a.py::test_one\n\n1 tests collected\n"
    fake_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=collect_stdout,
        stderr="",
    )

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        return_value=fake_result,
    ):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "discoverable"
    assert sig.test_count == 1


# ------------------------------------------------------------------
# 3. Fallback: no_config – pyproject exists but no pytest section
# ------------------------------------------------------------------


def test_no_config_pyproject_without_pytest_section(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pyproject.toml").write_text("[build-system]\nrequires = ['setuptools']\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "no_config"


# ------------------------------------------------------------------
# 4. Fallback: no_config – no pyproject.toml, no pytest.ini
# ------------------------------------------------------------------


def test_no_config_no_files(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "no_config"


# ------------------------------------------------------------------
# 5. Fallback: unknown on subprocess timeout
# ------------------------------------------------------------------


def test_unknown_on_timeout(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=5),
    ):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 6. Fallback: unknown on non-zero returncode (e.g. 1)
# ------------------------------------------------------------------


def test_unknown_on_nonzero_returncode(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")

    fake_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="ERROR collecting\n",
    )

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        return_value=fake_result,
    ):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 7. Fallback: unknown when pytest collects zero tests (rc=5)
# ------------------------------------------------------------------


def test_unknown_on_zero_tests_collected(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")

    fake_result = subprocess.CompletedProcess(
        args=[],
        returncode=5,
        stdout="no tests ran in 0.01s\n",
        stderr="",
    )

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        return_value=fake_result,
    ):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 8. Guard mechanism: file deleted during discovery (TOCTOU)
# ------------------------------------------------------------------


def test_guard_single_file_deleted_during_discovery(tmp_path: Path) -> None:
    """Simulate file deleted between glob() and stat() in discovery."""
    ctx = _make_context(tmp_path)

    # Create multiple log files
    log1 = ctx.logs_root / "unit_test.log"
    log2 = ctx.logs_root / "integration_test.log"
    log1.write_text("collected 2 items\n2 passed in 0.5s\n")
    log2.write_text("collected 3 items\n3 passed in 1.2s\n")

    # Mock glob to return paths, but stat() for log1 will raise FileNotFoundError
    # (simulating the file being deleted between glob and stat)

    original_glob = Path.glob
    original_stat = Path.stat

    def mock_glob(self, pattern):
        """Return both paths."""
        paths = list(original_glob(self, pattern))
        return iter(paths)

    def mock_stat(self):
        """Raise FileNotFoundError for log1, normal stat for log2."""
        if self.name == "unit_test.log":
            raise FileNotFoundError(f"File deleted: {self}")
        return original_stat(self)

    with patch.object(Path, "glob", mock_glob):
        with patch.object(Path, "stat", mock_stat):
            sig = CheckSignalCollector().collect(ctx)

    # Should use log2 (the one that didn't get deleted)
    assert sig.status == "passed"
    assert "integration_test.log" in sig.source


def test_guard_all_files_deleted_during_discovery(tmp_path: Path) -> None:
    """Simulate all files deleted during discovery."""
    ctx = _make_context(tmp_path)

    # Create log file
    log = ctx.logs_root / "unit_test.log"
    log.write_text("collected 2 items\n2 passed in 0.5s\n")

    # Mock stat() to always raise FileNotFoundError
    original_glob = Path.glob

    def mock_glob(self, pattern):
        """Return the path."""
        return original_glob(self, pattern)

    def mock_stat(self):
        """Always raise FileNotFoundError."""
        raise FileNotFoundError(f"File deleted: {self}")

    with patch.object(Path, "glob", mock_glob):
        with patch.object(Path, "stat", mock_stat):
            sig = CheckSignalCollector().collect(ctx)

    # Should fall back to discovery (not available → no_config/discoverable/unknown)
    # Since repo has no pytest config, should be no_config
    assert sig.status in ("no_config", "unknown")


def test_guard_uses_captured_mtime_not_new_stat(tmp_path: Path) -> None:
    """Verify that captured mtime from discovery is used, not re-stat'd."""
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "unit_test.log"
    log.write_text("collected 2 items\n2 passed in 0.5s\n")

    # Set mtime to a specific value
    import os

    os.utime(log, (1000, 1000))  # old mtime

    stat_call_count = 0
    original_stat = Path.stat

    def counting_stat(self):
        """Count stat calls and fail on second call to log (simulating file modified)."""
        nonlocal stat_call_count
        if self.name == "unit_test.log":
            stat_call_count += 1
            if stat_call_count == 1:
                # First call (discovery): return old mtime
                original_stat(self)

                # Create new stat_result with old mtime
                class FakeStat:
                    def __init__(self, mtime):
                        self.st_mtime = mtime

                return FakeStat(1000)
            else:
                # Second call should NOT happen (guard should use captured mtime)
                raise RuntimeError("stat() called twice on same file (race condition not guarded!)")
        return original_stat(self)

    with patch.object(Path, "stat", counting_stat):
        sig = CheckSignalCollector().collect(ctx)

    # Should succeed and use the old mtime from first stat
    assert sig.status == "passed"
    # observed_at should be based on old mtime (1000)
    from datetime import UTC, datetime

    expected_time = datetime.fromtimestamp(1000, tz=UTC)
    assert sig.observed_at == expected_time


# ------------------------------------------------------------------
# 9. Pytest command selection: repo-local venv vs sys.executable
# ------------------------------------------------------------------


def test_uses_repo_venv_pytest_when_present(tmp_path: Path) -> None:
    """When .venv/bin/pytest exists in the repo root, it is used directly."""
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")
    venv_pytest = ctx.repo_path / ".venv" / "bin" / "pytest"
    venv_pytest.parent.mkdir(parents=True)
    venv_pytest.touch()

    collect_stdout = "tests/test_x.py::test_a\n\n1 test collected\n"
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=collect_stdout, stderr="")

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        return_value=fake_result,
    ) as mock_run:
        CheckSignalCollector()._fallback_discovery(ctx)

    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[0] == str(venv_pytest), "should use repo-local venv pytest"
    assert "--collect-only" in called_cmd


def test_falls_back_to_sys_executable_when_no_venv(tmp_path: Path) -> None:
    """When no .venv/bin/pytest in repo root, fall back to sys.executable -m pytest."""
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")
    # No .venv/bin/pytest created

    collect_stdout = "tests/test_x.py::test_a\n\n1 test collected\n"
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=collect_stdout, stderr="")

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        return_value=fake_result,
    ) as mock_run:
        CheckSignalCollector()._fallback_discovery(ctx)

    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[0] == sys.executable
    assert called_cmd[1] == "-m"
    assert called_cmd[2] == "pytest"


def test_guard_oserror_also_skipped(tmp_path: Path) -> None:
    """Verify that OSError (not just FileNotFoundError) is handled in discovery."""
    ctx = _make_context(tmp_path)

    # Create multiple log files
    log1 = ctx.logs_root / "unit_test.log"
    log2 = ctx.logs_root / "integration_test.log"
    log1.write_text("collected 1 items\n1 passed in 0.5s\n")
    log2.write_text("collected 2 items\n2 passed in 1.2s\n")

    original_glob = Path.glob
    original_stat = Path.stat

    def mock_stat(self):
        """Raise OSError for log1 (e.g., permission denied), normal stat for log2."""
        if self.name == "unit_test.log":
            raise OSError("Permission denied")
        return original_stat(self)

    with patch.object(Path, "glob", lambda self, p: original_glob(self, p)):
        with patch.object(Path, "stat", mock_stat):
            sig = CheckSignalCollector().collect(ctx)

    # Should use log2 since log1 failed
    assert sig.status == "passed"
    assert "integration_test.log" in sig.source
