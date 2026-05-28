# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for glob/stat race condition guard mechanism.

These tests verify that the collectors handle files deleted between glob()
and stat() calls (TOCTOU race condition) with graceful degradation and
proper error handling.
"""
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


from operations_center.observer.collectors.check_signal import (
    CheckSignalCollector,
    latest_matching_file,
)
from operations_center.observer.collectors.dependency_drift import (
    DependencyDriftCollector,
)


class TestLatestMatchingFileRaceCondition:
    """Test latest_matching_file() guard against file deletion during discovery."""

    def test_happy_path_single_file(self, tmp_artifact_dir):
        """Single file present is returned with mtime."""
        test_file = tmp_artifact_dir / "test.log"
        test_file.write_text("test content")
        mtime = test_file.stat().st_mtime

        result = latest_matching_file(tmp_artifact_dir, "*.log")

        assert result is not None
        path, returned_mtime = result
        assert path == test_file
        assert returned_mtime == mtime

    def test_happy_path_multiple_files_latest_returned(self, tmp_artifact_dir):
        """When multiple files exist, latest (by mtime) is returned."""
        file1 = tmp_artifact_dir / "old.log"
        file1.write_text("old")
        os.utime(file1, (1000, 1000))

        file2 = tmp_artifact_dir / "new.log"
        file2.write_text("new")
        os.utime(file2, (2000, 2000))

        result = latest_matching_file(tmp_artifact_dir, "*.log")

        assert result is not None
        path, returned_mtime = result
        assert path == file2
        assert returned_mtime == 2000

    def test_no_files_returns_none(self, tmp_artifact_dir):
        """Empty directory returns None."""
        result = latest_matching_file(tmp_artifact_dir, "*.log")
        assert result is None

    def test_file_deleted_during_discovery_skipped(self, tmp_artifact_dir):
        """File deleted between glob() and stat() is skipped, next file used."""
        file1 = tmp_artifact_dir / "good.log"
        file1.write_text("content")

        file2 = tmp_artifact_dir / "deleted.log"
        file2.write_text("will be deleted")

        # Patch stat() to delete file2 on first call, succeed on second
        original_stat = Path.stat
        call_count = [0]

        def stat_with_deletion(self):
            call_count[0] += 1
            if self == file2 and call_count[0] == 1:
                # Delete the file on first stat attempt
                file2.unlink()
                raise FileNotFoundError(f"No such file: {self}")
            return original_stat(self)

        with patch.object(Path, "stat", stat_with_deletion):
            result = latest_matching_file(tmp_artifact_dir, "*.log")

        # Should return file1 since file2 was deleted
        assert result is not None
        path, _ = result
        assert path == file1

    def test_all_files_deleted_during_discovery_returns_none(self, tmp_artifact_dir):
        """All files deleted during discovery returns None."""
        file1 = tmp_artifact_dir / "file1.log"
        file1.write_text("content1")

        file2 = tmp_artifact_dir / "file2.log"
        file2.write_text("content2")

        # Patch stat() to always raise FileNotFoundError
        original_stat = Path.stat

        def stat_with_deletion(self):
            if str(self).endswith(".log"):
                raise FileNotFoundError(f"No such file: {self}")
            return original_stat(self)

        with patch.object(Path, "stat", stat_with_deletion):
            result = latest_matching_file(tmp_artifact_dir, "*.log")

        assert result is None

    def test_permission_error_during_stat_skipped(self, tmp_artifact_dir):
        """Permission error on stat() is caught and file skipped."""
        file1 = tmp_artifact_dir / "accessible.log"
        file1.write_text("accessible")

        file2 = tmp_artifact_dir / "forbidden.log"
        file2.write_text("forbidden")

        # Patch stat() to raise PermissionError for forbidden.log
        original_stat = Path.stat

        def stat_with_permission_error(self):
            if "forbidden" in str(self):
                raise PermissionError(f"Access denied: {self}")
            return original_stat(self)

        with patch.object(Path, "stat", stat_with_permission_error):
            result = latest_matching_file(tmp_artifact_dir, "*.log")

        # Should return accessible.log, skipping forbidden.log
        assert result is not None
        path, _ = result
        assert path == file1

    def test_mtime_from_discovery_time_returned(self, tmp_artifact_dir):
        """Returned mtime is from discovery time, not a second stat()."""
        test_file = tmp_artifact_dir / "test.log"
        test_file.write_text("original")
        original_mtime = test_file.stat().st_mtime

        # Get result with discovery-time mtime
        result = latest_matching_file(tmp_artifact_dir, "*.log")
        assert result is not None
        _, returned_mtime = result

        # Modify file after discovery (simulate time passing)
        time.sleep(0.01)
        test_file.write_text("modified")

        # Returned mtime should be original, not new mtime
        assert returned_mtime == original_mtime
        assert test_file.stat().st_mtime > original_mtime

    def test_glob_pattern_respected(self, tmp_artifact_dir):
        """Only files matching glob pattern are returned."""
        log_file = tmp_artifact_dir / "test.log"
        log_file.write_text("log")

        txt_file = tmp_artifact_dir / "test.txt"
        txt_file.write_text("txt")

        result = latest_matching_file(tmp_artifact_dir, "*.log")

        assert result is not None
        path, _ = result
        assert path == log_file

    def test_nested_glob_pattern(self, tmp_artifact_dir):
        """Nested directory patterns work correctly."""
        subdir = tmp_artifact_dir / "subdir"
        subdir.mkdir()
        nested_file = subdir / "dependency_report.json"
        nested_file.write_text("{}")

        result = latest_matching_file(tmp_artifact_dir, "*/dependency_report.json")

        assert result is not None
        path, _ = result
        assert path == nested_file


class TestCheckSignalCollectorRaceCondition:
    """Test CheckSignalCollector against race condition scenarios."""

    def test_happy_path_test_log_found(self, tmp_artifact_dir):
        """Happy path: test log found and processed."""
        test_log = tmp_artifact_dir / "test_results_test.log"
        test_log.write_text("============================= test session starts ==============================\n5 passed in 0.42s\n")

        context = MagicMock()
        context.logs_root = tmp_artifact_dir

        collector = CheckSignalCollector()
        signal = collector.collect(context)

        assert signal is not None
        assert signal.status == "passed"
        assert signal.source == str(test_log)
        assert "5 passed" in signal.summary

    def test_file_deleted_during_discovery_fallback_to_pytest(self, tmp_artifact_dir):
        """If test log deleted during discovery, fallback to pytest discovery."""
        test_log = tmp_artifact_dir / "test_results.log"
        test_log.write_text("=== Test Results ===\n5 passed\n")

        # Create pytest config so fallback succeeds
        pyproject = tmp_artifact_dir / "pyproject.toml"
        pyproject.write_text("[tool.pytest.ini_options]\ntestpaths = ['.']\n")

        # Patch latest_matching_file to return None (simulating file deletion)
        with patch(
            "operations_center.observer.collectors.check_signal.latest_matching_file",
            return_value=None,
        ):
            context = MagicMock()
            context.logs_root = tmp_artifact_dir
            context.repo_path = tmp_artifact_dir

            collector = CheckSignalCollector()
            signal = collector.collect(context)

            # Should fallback to pytest discovery
            assert signal is not None
            # Status should be one of the fallback states
            assert signal.status in ["discoverable", "unknown", "no_config"]

    def test_multiple_test_logs_latest_used(self, tmp_artifact_dir):
        """Multiple test logs: latest (by mtime) is used."""
        old_log = tmp_artifact_dir / "old_test.log"
        old_log.write_text("1 passed")
        os.utime(old_log, (1000, 1000))

        new_log = tmp_artifact_dir / "new_test.log"
        new_log.write_text("10 passed in 1.5s")
        os.utime(new_log, (2000, 2000))

        context = MagicMock()
        context.logs_root = tmp_artifact_dir

        collector = CheckSignalCollector()
        signal = collector.collect(context)

        assert signal is not None
        assert signal.source == str(new_log)
        assert "10 passed" in signal.summary

    def test_uses_captured_mtime_in_signal(self, tmp_artifact_dir):
        """Verify captured mtime from discovery is used in signal."""
        test_log = tmp_artifact_dir / "test_results_test.log"
        test_log.write_text("===== test session starts =====\n5 passed\n")
        original_mtime = test_log.stat().st_mtime

        context = MagicMock()
        context.logs_root = tmp_artifact_dir

        collector = CheckSignalCollector()
        signal = collector.collect(context)

        # Verify the signal's observed_at timestamp matches captured mtime
        assert signal is not None
        assert signal.status == "passed"
        assert signal.observed_at is not None
        # The timestamp should match the captured mtime (within floating point precision)
        assert abs(signal.observed_at.timestamp() - original_mtime) < 0.01


class TestDependencyDriftCollectorRaceCondition:
    """Test DependencyDriftCollector against race condition scenarios."""

    def test_happy_path_report_found(self, tmp_artifact_dir):
        """Happy path: dependency report found and processed."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_data = {
            "statuses": [{"notes": "Update available"}],
            "created_task_ids": ["task-001"],
        }
        report_file.write_text(json.dumps(report_data))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal is not None
        assert signal.status == "available"
        assert "created_task_ids=1" in signal.summary
        assert signal.source == str(report_file)

    def test_file_deleted_after_discovery_no_crash(self, tmp_artifact_dir):
        """File deleted between discovery and read_text() is handled gracefully."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_file.write_text(json.dumps({"statuses": []}))

        # Patch latest_dependency_report to return a non-existent path
        # This simulates file being deleted after discovery
        fake_path = Path(tmp_artifact_dir / "deleted.json")

        with patch.object(
            DependencyDriftCollector,
            "_latest_dependency_report",
            return_value=(fake_path, 1000.0),
        ):
            context = MagicMock()
            context.settings.report_root = tmp_artifact_dir

            collector = DependencyDriftCollector()
            signal = collector.collect(context)

            # Should return not_available, not crash
            assert signal is not None
            assert signal.status == "not_available"

    def test_multiple_reports_latest_used(self, tmp_artifact_dir):
        """Multiple reports: latest (by mtime) is used."""
        run_dir_1 = tmp_artifact_dir / "run-001"
        run_dir_1.mkdir()
        report_file_1 = run_dir_1 / "dependency_report.json"
        report_file_1.write_text(json.dumps({"statuses": [], "created_task_ids": []}))
        os.utime(report_file_1, (1000, 1000))

        run_dir_2 = tmp_artifact_dir / "run-002"
        run_dir_2.mkdir()
        report_file_2 = run_dir_2 / "dependency_report.json"
        report_data = {
            "statuses": [{"notes": "Critical update"}],
            "created_task_ids": ["task-001", "task-002"],
        }
        report_file_2.write_text(json.dumps(report_data))
        os.utime(report_file_2, (2000, 2000))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal is not None
        assert signal.status == "available"
        assert signal.source == str(report_file_2)
        assert "created_task_ids=2" in signal.summary

    def test_no_stat_call_after_discovery(self, tmp_artifact_dir):
        """Verify no second stat() call after discovery-time capture."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_file.write_text(json.dumps({"statuses": []}))

        stat_call_count = [0]
        original_stat = Path.stat

        def counting_stat(self):
            if "dependency_report.json" in str(self):
                stat_call_count[0] += 1
            return original_stat(self)

        with patch.object(Path, "stat", counting_stat):
            context = MagicMock()
            context.settings.report_root = tmp_artifact_dir

            collector = DependencyDriftCollector()
            signal = collector.collect(context)

            # Only one stat() call during discovery, no second call
            assert stat_call_count[0] == 1
            assert signal is not None
            assert signal.status == "available"

    def test_all_reports_deleted_during_discovery(self, tmp_artifact_dir):
        """All reports deleted during discovery returns not_available."""
        run_dir_1 = tmp_artifact_dir / "run-001"
        run_dir_1.mkdir()
        report_file_1 = run_dir_1 / "dependency_report.json"
        report_file_1.write_text("{}")

        run_dir_2 = tmp_artifact_dir / "run-002"
        run_dir_2.mkdir()
        report_file_2 = run_dir_2 / "dependency_report.json"
        report_file_2.write_text("{}")

        # Patch stat() to always raise FileNotFoundError for .json files
        original_stat = Path.stat

        def stat_with_deletion(self):
            if "dependency_report.json" in str(self):
                raise FileNotFoundError(f"File deleted: {self}")
            return original_stat(self)

        with patch.object(Path, "stat", stat_with_deletion):
            context = MagicMock()
            context.settings.report_root = tmp_artifact_dir

            collector = DependencyDriftCollector()
            signal = collector.collect(context)

            assert signal is not None
            assert signal.status == "not_available"


class TestConcurrentFileOperations:
    """Test collectors resilience to concurrent file deletion."""

    def test_check_signal_concurrent_deletion(self, tmp_artifact_dir):
        """CheckSignalCollector handles concurrent file deletion."""
        test_logs = []
        for i in range(5):
            log_file = tmp_artifact_dir / f"test_{i}.log"
            log_file.write_text(f"{i} passed\n")
            os.utime(log_file, (1000 + i, 1000 + i))
            test_logs.append(log_file)

        deletion_count = [0]

        def delete_files_during_glob():
            """Delete files in background while glob is happening."""
            time.sleep(0.001)
            # Delete some files (simulating concurrent cleanup)
            for log_file in test_logs[:2]:
                if log_file.exists():
                    log_file.unlink()
                    deletion_count[0] += 1

        # Start deletion in background
        deletion_thread = threading.Thread(target=delete_files_during_glob)
        deletion_thread.daemon = True
        deletion_thread.start()

        context = MagicMock()
        context.logs_root = tmp_artifact_dir

        collector = CheckSignalCollector()
        signal = collector.collect(context)

        deletion_thread.join(timeout=1.0)

        # Should successfully use one of the remaining files
        assert signal is not None
        # Status should be from one of the files that wasn't deleted
        assert signal.status in ["passed", "unknown", "no_config"]

    def test_dependency_drift_concurrent_deletion(self, tmp_artifact_dir):
        """DependencyDriftCollector handles concurrent file deletion."""
        reports = []
        for i in range(3):
            run_dir = tmp_artifact_dir / f"run-{i:03d}"
            run_dir.mkdir()
            report_file = run_dir / "dependency_report.json"
            report_file.write_text(json.dumps({"statuses": []}))
            os.utime(report_file, (1000 + i, 1000 + i))
            reports.append(report_file)

        deletion_count = [0]

        def delete_files_during_discovery():
            """Delete files in background during discovery."""
            time.sleep(0.001)
            for report_file in reports[:2]:
                if report_file.exists():
                    report_file.unlink()
                    deletion_count[0] += 1

        deletion_thread = threading.Thread(target=delete_files_during_discovery)
        deletion_thread.daemon = True
        deletion_thread.start()

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        deletion_thread.join(timeout=1.0)

        # Should handle concurrent deletion gracefully
        assert signal is not None
        assert isinstance(signal.status, str)
        # Should either use remaining file or return not_available
        assert signal.status in ["available", "not_available"]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_symlink_deleted_during_discovery(self, tmp_artifact_dir):
        """Symlink deleted during discovery is skipped."""
        actual_file = tmp_artifact_dir / "actual.log"
        actual_file.write_text("content")

        symlink = tmp_artifact_dir / "link.log"
        symlink.symlink_to(actual_file)

        # Patch stat() to fail for symlinks
        original_stat = Path.stat

        def stat_fail_symlinks(self):
            if "link" in str(self):
                raise FileNotFoundError(f"Symlink deleted: {self}")
            return original_stat(self)

        with patch.object(Path, "stat", stat_fail_symlinks):
            result = latest_matching_file(tmp_artifact_dir, "*.log")

            # Should skip broken symlink, use actual file
            assert result is not None
            path, _ = result
            assert path == actual_file

    def test_special_characters_in_filename(self, tmp_artifact_dir):
        """Files with special characters are handled."""
        special_file = tmp_artifact_dir / "test-2026-05-27_12-30-45.log"
        special_file.write_text("5 passed\n")

        result = latest_matching_file(tmp_artifact_dir, "*_12-30-45.log")

        assert result is not None
        path, _ = result
        assert path == special_file

    def test_very_large_mtime_values(self, tmp_artifact_dir):
        """Files with very large mtime values are handled correctly."""
        test_file = tmp_artifact_dir / "test.log"
        test_file.write_text("content")
        # Set mtime to a large value (far future)
        large_mtime = 9999999999.0
        os.utime(test_file, (large_mtime, large_mtime))

        result = latest_matching_file(tmp_artifact_dir, "*.log")

        assert result is not None
        path, returned_mtime = result
        assert returned_mtime == large_mtime

    def test_empty_glob_result_with_error_on_fallback(self, tmp_artifact_dir):
        """Empty glob with OSError on fallback returns None."""
        # Directory exists but is empty
        empty_subdir = tmp_artifact_dir / "empty"
        empty_subdir.mkdir()

        original_stat = Path.stat

        def stat_with_oserror(self):
            if "empty" in str(self):
                raise OSError("I/O error")
            return original_stat(self)

        with patch.object(Path, "stat", stat_with_oserror):
            result = latest_matching_file(empty_subdir, "*.log")

            assert result is None
