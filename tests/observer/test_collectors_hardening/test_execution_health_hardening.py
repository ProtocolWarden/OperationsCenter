# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for ExecutionArtifactCollector with hardening."""
import json
from unittest.mock import MagicMock

from operations_center.observer.collectors.execution_health import (
    ExecutionArtifactCollector,
)


class TestExecutionHealthHardening:
    """Tests for collector hardening and validation."""

    def test_valid_artifacts_processed(
        self, tmp_artifact_dir, valid_outcome, valid_request
    ):
        """Valid artifacts are processed normally."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))

        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(valid_request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 1
        assert signal.executed_count == 1

    def test_malformed_outcome_json(self, tmp_artifact_dir, valid_request):
        """Malformed outcome.json is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text("{invalid json")

        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(valid_request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        # Invalid run skipped, total should be 0
        assert signal.total_runs == 0

    def test_malformed_request_json(self, tmp_artifact_dir, valid_outcome):
        """Malformed request.json is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))

        request_file = run_dir / "request.json"
        request_file.write_text("{invalid json")

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 0

    def test_missing_task_id(self, tmp_artifact_dir, valid_request):
        """Missing task_id in outcome is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome = {"status": "executed"}  # Missing task_id
        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(outcome))

        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(valid_request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 0

    def test_invalid_status_type(self, tmp_artifact_dir, valid_request):
        """Invalid status type is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome = {"task_id": "task-123", "status": 404}  # status should be string
        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(outcome))

        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(valid_request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 0

    def test_invalid_task_type(self, tmp_artifact_dir, valid_outcome):
        """Invalid task type is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))

        request = {"task": "not_a_dict"}  # task should be dict
        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 0

    def test_validation_file_parse_error(
        self, tmp_artifact_dir, valid_outcome, valid_request
    ):
        """Malformed validation.json doesn't crash collection."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))

        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(valid_request))

        validation_file = run_dir / "validation.json"
        validation_file.write_text("{invalid json")

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        # Run is still counted, but validation_passed is None
        assert signal.total_runs == 1
        assert signal.recent_runs[0].validation_passed is None

    def test_validation_structure_error(
        self, tmp_artifact_dir, valid_outcome, valid_request
    ):
        """Validation.json with invalid structure is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))

        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(valid_request))

        validation = {"passed": "yes"}  # Should be bool
        validation_file = run_dir / "validation.json"
        validation_file.write_text(json.dumps(validation))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 1
        assert signal.recent_runs[0].validation_passed is None

    def test_repo_key_mismatch(self, tmp_artifact_dir, valid_outcome):
        """Different repo_key is skipped."""
        run_dir = tmp_artifact_dir / "run-001"
        run_dir.mkdir()

        outcome_file = run_dir / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))

        request = {
            "task": {"id": "task-123", "repo_key": "other_repo"},
        }
        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 0

    def test_multiple_runs(self, tmp_artifact_dir, valid_outcome, valid_request):
        """Multiple valid runs are processed."""
        for i in range(3):
            run_dir = tmp_artifact_dir / f"run-{i:03d}"
            run_dir.mkdir()

            outcome_file = run_dir / "control_outcome.json"
            outcome_file.write_text(json.dumps(valid_outcome))

            request_file = run_dir / "request.json"
            request_file.write_text(json.dumps(valid_request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        assert signal.total_runs == 3
        assert signal.executed_count == 3

    def test_mixed_valid_invalid_runs(
        self, tmp_artifact_dir, valid_outcome, valid_request
    ):
        """Valid and invalid runs are processed correctly."""
        # Valid run
        run_dir_1 = tmp_artifact_dir / "run-001"
        run_dir_1.mkdir()
        outcome_file = run_dir_1 / "control_outcome.json"
        outcome_file.write_text(json.dumps(valid_outcome))
        request_file = run_dir_1 / "request.json"
        request_file.write_text(json.dumps(valid_request))

        # Invalid run (malformed)
        run_dir_2 = tmp_artifact_dir / "run-002"
        run_dir_2.mkdir()
        outcome_file = run_dir_2 / "control_outcome.json"
        outcome_file.write_text("{invalid json")
        request_file = run_dir_2 / "request.json"
        request_file.write_text(json.dumps(valid_request))

        context = MagicMock()
        context.settings.report_root = tmp_artifact_dir
        context.repo_name = "test_repo"

        collector = ExecutionArtifactCollector()
        signal = collector.collect(context)

        # Only valid run counted
        assert signal.total_runs == 1
