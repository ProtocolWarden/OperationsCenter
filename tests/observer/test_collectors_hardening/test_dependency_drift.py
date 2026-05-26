# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for DependencyDriftCollector with hardening."""
import json
import os
from unittest.mock import MagicMock

from operations_center.observer.collectors.dependency_drift import (
    DependencyDriftCollector,
)


class TestDependencyDriftHardening:
    """Tests for crash vulnerability fixes and hardening."""

    def test_malformed_json_no_crash(self, tmp_artifact_dir, caplog):
        """Malformed JSON does not crash collector."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_file.write_text("{invalid json")

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal is not None
        assert signal.status == "not_available"

    def test_valid_report(self, tmp_artifact_dir):
        """Valid dependency_report.json is processed normally."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_data = {
            "statuses": [
                {"package": "requests", "severity": "info", "notes": "Update"},
            ],
            "created_task_ids": ["task-001"],
        }
        report_file.write_text(json.dumps(report_data))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "available"
        assert "created_task_ids=1" in signal.summary

    def test_missing_file(self, tmp_artifact_dir):
        """Missing report file returns unavailable."""
        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "not_available"

    def test_invalid_json_type_mismatch(self, tmp_artifact_dir):
        """Minimal valid status payload remains available."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_data = {
            "statuses": [
                {"severity": "info"},  # severity is a string as expected
            ],
        }
        report_file.write_text(json.dumps(report_data))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "available"

    def test_invalid_severity_enum(self, tmp_artifact_dir):
        """Invalid severity enum is caught."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_data = {
            "statuses": [
                {"severity": "critical"},  # Invalid, should be info/warning/error
            ],
        }
        report_file.write_text(json.dumps(report_data))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "not_available"

    def test_status_list_type_mismatch(self, tmp_artifact_dir):
        """Statuses must be a list."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_data = {"statuses": "not_a_list"}
        report_file.write_text(json.dumps(report_data))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "not_available"

    def test_parse_error_logging(self, tmp_artifact_dir, caplog):
        """Parse errors are logged at DEBUG level."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_file.write_text("{invalid json")

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        import logging

        caplog.set_level(logging.DEBUG)
        collector = DependencyDriftCollector()
        collector.collect(context)

        assert any("parse" in record.message.lower() for record in caplog.records)

    def test_structure_error_logging(self, tmp_artifact_dir, caplog):
        """Structure validation errors are logged at WARNING level."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        report_data = {"statuses": "not_a_list"}
        report_file.write_text(json.dumps(report_data))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        import logging

        caplog.set_level(logging.WARNING)
        collector = DependencyDriftCollector()
        collector.collect(context)

        assert any("invalid artifact structure" in record.message.lower() for record in caplog.records)

    def test_unicode_error_handling(self, tmp_artifact_dir):
        """Unicode decode errors are handled gracefully."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()
        report_file = run_dir / "dependency_report.json"
        # Write invalid UTF-8
        report_file.write_bytes(b"\xff\xfe{invalid}")

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "not_available"

    def test_multiple_reports_latest_processed(self, tmp_artifact_dir):
        """Latest report (by mtime) is processed."""
        run_dir_1 = tmp_artifact_dir / "run-001"
        run_dir_1.mkdir()
        report_file_1 = run_dir_1 / "dependency_report.json"
        report_file_1.write_text(json.dumps({"statuses": []}))

        run_dir_2 = tmp_artifact_dir / "run-002"
        run_dir_2.mkdir()
        report_file_2 = run_dir_2 / "dependency_report.json"
        report_data = {
            "statuses": [{"notes": "Update"}],
            "created_task_ids": ["task-001"],
        }
        report_file_2.write_text(json.dumps(report_data))
        os.utime(report_file_1, (1000, 1000))
        os.utime(report_file_2, (2000, 2000))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir

        collector = DependencyDriftCollector()
        signal = collector.collect(context)

        assert signal.status == "available"
        assert "created_task_ids=1" in signal.summary
