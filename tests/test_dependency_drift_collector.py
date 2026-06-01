# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for DependencyDriftCollector."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.models import DependencyDriftSignal
from operations_center.observer.service import ObserverContext


def _make_context(tmp_path: Path) -> ObserverContext:
    """Create a minimal ObserverContext with report_root pointing at *tmp_path*."""
    settings = MagicMock()
    settings.report_root = tmp_path
    return ObserverContext(
        repo_path=tmp_path,
        repo_name="test-repo",
        base_branch="main",
        run_id="obs_test_001",
        observed_at=datetime.now(UTC),
        source_command="test",
        settings=settings,
        commit_limit=10,
        hotspot_window=30,
        todo_limit=20,
        logs_root=tmp_path / "logs",
    )


class TestDependencyDriftCollector:
    def test_not_available_when_no_report_files(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert isinstance(signal, DependencyDriftSignal)
        assert signal.status == "not_available"

    def test_valid_report_with_actionable_statuses(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        data = {
            "statuses": [
                {"package": "requests", "notes": "outdated by 2 major versions"},
                {"package": "flask", "notes": "security patch available"},
                {"package": "numpy"},  # no notes → not actionable
            ],
            "created_task_ids": ["TASK-1", "TASK-2"],
        }
        (run_dir / "dependency_report.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "actionable_statuses=2" in signal.summary
        assert "created_task_ids=2" in signal.summary

    def test_report_with_no_statuses_key(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        data = {"some_other_key": "value"}
        (run_dir / "dependency_report.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "not_available"

    def test_report_with_empty_statuses_list(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        data = {"statuses": []}
        (run_dir / "dependency_report.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "no statuses" in signal.summary

    def test_multiple_report_dirs_picks_latest(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "run_old"
        old_dir.mkdir()
        old_report = old_dir / "dependency_report.json"
        old_report.write_text(json.dumps({"statuses": [{"package": "old", "notes": "x"}]}))
        os.utime(old_report, (1000, 1000))

        new_dir = tmp_path / "run_new"
        new_dir.mkdir()
        new_report = new_dir / "dependency_report.json"
        new_report.write_text(json.dumps({"statuses": [{"package": "new", "notes": "y"}]}))
        os.utime(new_report, (2000, 2000))

        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "run_new" in signal.source

    def test_malformed_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        (run_dir / "dependency_report.json").write_text("not valid json {{{")
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "not_available"


# ------------------------------------------------------------------
# Guard mechanism tests: file deleted during discovery (TOCTOU)
# ------------------------------------------------------------------


class TestDependencyDriftGuardMechanism:
    """Tests for race condition guard mechanism in dependency report discovery."""

    def test_guard_single_file_deleted_during_discovery(self, tmp_path: Path) -> None:
        """Simulate file deleted between glob() and stat() in discovery."""
        # Create multiple report directories with files
        run_old = tmp_path / "run_old"
        run_old.mkdir()
        old_report = run_old / "dependency_report.json"
        old_report.write_text(json.dumps({"statuses": [{"package": "old", "notes": "x"}]}))

        run_new = tmp_path / "run_new"
        run_new.mkdir()
        new_report = run_new / "dependency_report.json"
        new_report.write_text(json.dumps({"statuses": [{"package": "new", "notes": "y"}]}))

        # Set mtimes: old gets deleted during discovery, new doesn't
        os.utime(old_report, (1000, 1000))
        os.utime(new_report, (2000, 2000))

        # Mock stat() to raise FileNotFoundError for old_report
        from pathlib import Path as PathlibPath
        from unittest.mock import patch

        original_stat = PathlibPath.stat

        def mock_stat(self):
            """Raise FileNotFoundError for old_report (simulating deletion)."""
            if "run_old" in str(self):
                raise FileNotFoundError(f"File deleted during discovery: {self}")
            return original_stat(self)

        with patch.object(PathlibPath, "stat", mock_stat):
            ctx = _make_context(tmp_path)
            signal = DependencyDriftCollector().collect(ctx)

        # Should use new_report since old_report was skipped
        assert signal.status == "available"
        assert "run_new" in signal.source
        assert "package" in signal.summary or "statuses" in signal.summary

    def test_guard_all_files_deleted_during_discovery(self, tmp_path: Path) -> None:
        """Simulate all files deleted during discovery — should return not_available."""
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        report = run_dir / "dependency_report.json"
        report.write_text(json.dumps({"statuses": [{"package": "test", "notes": "x"}]}))

        # Mock stat() to always raise FileNotFoundError
        from pathlib import Path as PathlibPath
        from unittest.mock import patch

        def mock_stat(self):
            """Always raise FileNotFoundError."""
            raise FileNotFoundError(f"File deleted during discovery: {self}")

        with patch.object(PathlibPath, "stat", mock_stat):
            ctx = _make_context(tmp_path)
            signal = DependencyDriftCollector().collect(ctx)

        # No files available → not_available
        assert signal.status == "not_available"

    def test_guard_uses_captured_mtime_not_new_stat(self, tmp_path: Path) -> None:
        """Verify that captured mtime from discovery is used, not re-stat'd."""
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        report = run_dir / "dependency_report.json"
        report.write_text(json.dumps({"statuses": [{"package": "test", "notes": "x"}]}))

        # Set mtime to a specific value
        os.utime(report, (1000, 1000))

        # Count stat calls
        stat_call_count = {"count": 0}
        from pathlib import Path as PathlibPath
        from unittest.mock import patch

        original_stat = PathlibPath.stat

        def counting_stat(self):
            """Count stat calls and verify it's only called once per file."""
            if "dependency_report.json" in str(self):
                stat_call_count["count"] += 1
                if stat_call_count["count"] == 1:
                    # First call (discovery): return specific mtime
                    original_stat(self)

                    # Create fake stat with old mtime
                    class FakeStat:
                        def __init__(self, mtime):
                            self.st_mtime = mtime

                    return FakeStat(1000)
                else:
                    # Should NOT reach here (would indicate guard didn't work)
                    raise RuntimeError(
                        f"stat() called {stat_call_count['count']} times on {self} "
                        "(race condition guard not working properly)"
                    )
            return original_stat(self)

        with patch.object(PathlibPath, "stat", counting_stat):
            ctx = _make_context(tmp_path)
            signal = DependencyDriftCollector().collect(ctx)

        # Should succeed
        assert signal.status == "available"
        # observed_at should be based on first stat (mtime=1000)
        from datetime import UTC, datetime

        expected_time = datetime.fromtimestamp(1000, tz=UTC)
        assert signal.observed_at == expected_time
        # Verify stat was called exactly once per discovery phase
        assert stat_call_count["count"] == 1

    def test_guard_oserror_also_skipped(self, tmp_path: Path) -> None:
        """Verify that OSError (not just FileNotFoundError) is handled during discovery."""
        # Create two reports
        run1 = tmp_path / "run1"
        run1.mkdir()
        report1 = run1 / "dependency_report.json"
        report1.write_text(json.dumps({"statuses": [{"package": "old", "notes": "x"}]}))

        run2 = tmp_path / "run2"
        run2.mkdir()
        report2 = run2 / "dependency_report.json"
        report2.write_text(json.dumps({"statuses": [{"package": "new", "notes": "y"}]}))

        # Set mtimes
        os.utime(report1, (1000, 1000))
        os.utime(report2, (2000, 2000))

        # Mock stat() to raise OSError for report1
        from pathlib import Path as PathlibPath
        from unittest.mock import patch

        original_stat = PathlibPath.stat

        def mock_stat(self):
            """Raise OSError for run1 (e.g., permission denied)."""
            if "run1" in str(self):
                raise OSError("Permission denied or I/O error")
            return original_stat(self)

        with patch.object(PathlibPath, "stat", mock_stat):
            ctx = _make_context(tmp_path)
            signal = DependencyDriftCollector().collect(ctx)

        # Should use run2 (run1 failed)
        assert signal.status == "available"
        assert "run2" in signal.source

    def test_guard_multiple_failures_falls_back_gracefully(self, tmp_path: Path) -> None:
        """Multiple failures during discovery with fallback to next valid file."""
        # Create three reports
        for i in range(3):
            run_dir = tmp_path / f"run{i}"
            run_dir.mkdir()
            report = run_dir / "dependency_report.json"
            report.write_text(
                json.dumps({"statuses": [{"package": f"pkg{i}", "notes": f"note{i}"}]})
            )
            os.utime(report, (1000 + i, 1000 + i))

        # Mock stat() to fail for run0 and run1, succeed for run2
        from pathlib import Path as PathlibPath
        from unittest.mock import patch

        original_stat = PathlibPath.stat

        def mock_stat(self):
            """Fail for run0 and run1."""
            path_str = str(self)
            if "run0" in path_str or "run1" in path_str:
                raise FileNotFoundError(f"File deleted: {self}")
            return original_stat(self)

        with patch.object(PathlibPath, "stat", mock_stat):
            ctx = _make_context(tmp_path)
            signal = DependencyDriftCollector().collect(ctx)

        # Should use run2 (the only one that succeeds)
        assert signal.status == "available"
        assert "run2" in signal.source

    def test_guard_read_text_still_fails_after_successful_discovery(self, tmp_path: Path) -> None:
        """File deleted after discovery but before read_text() should be caught gracefully."""
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        report = run_dir / "dependency_report.json"
        report.write_text(json.dumps({"statuses": [{"package": "test", "notes": "x"}]}))

        # Mock read_text() to fail (file deleted after discovery)
        from pathlib import Path as PathlibPath
        from unittest.mock import patch

        original_read_text = PathlibPath.read_text

        def mock_read_text(self, **kwargs):
            """Fail on read after successful stat."""
            if "dependency_report.json" in str(self):
                raise FileNotFoundError(f"File deleted before read: {self}")
            return original_read_text(self, **kwargs)

        with patch.object(PathlibPath, "read_text", mock_read_text):
            ctx = _make_context(tmp_path)
            signal = DependencyDriftCollector().collect(ctx)

        # Should handle gracefully and return not_available
        assert signal.status == "not_available"

    def test_guard_preserves_mtime_accuracy(self, tmp_path: Path) -> None:
        """Verify that exact mtime from discovery is preserved in signal."""
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        report = run_dir / "dependency_report.json"
        report.write_text(json.dumps({"statuses": [{"package": "test", "notes": "x"}]}))

        # Set specific mtime
        target_mtime = 1234567890.5
        os.utime(report, (target_mtime, target_mtime))

        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)

        assert signal.status == "available"
        # Verify observed_at is from the captured mtime, not re-stat
        from datetime import UTC, datetime

        expected_time = datetime.fromtimestamp(target_mtime, tz=UTC)
        assert signal.observed_at == expected_time
