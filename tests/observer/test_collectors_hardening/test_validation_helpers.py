# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for validation helper library."""
from pathlib import Path
from unittest.mock import patch

from operations_center.observer.validation import (
    ArtifactValidator,
    DependencyReportValidator,
    ExecutionOutcomeValidator,
    LintItemValidator,
    RequestValidator,
    ValidationHistoryValidator,
)


class TestArtifactValidator:
    """Tests for ArtifactValidator base class."""

    def test_type_check_valid(self):
        is_valid, msg = ArtifactValidator.type_check("hello", str, "name")
        assert is_valid
        assert msg == ""

    def test_type_check_invalid(self):
        is_valid, msg = ArtifactValidator.type_check(123, str, "count")
        assert not is_valid
        assert "expected str" in msg
        assert "int" in msg

    def test_enum_check_valid(self):
        is_valid, msg = ArtifactValidator.enum_check(
            "executed", {"executed", "failed", "unknown"}, "status"
        )
        assert is_valid
        assert msg == ""

    def test_enum_check_invalid(self):
        is_valid, msg = ArtifactValidator.enum_check(
            "running", {"executed", "failed", "unknown"}, "status"
        )
        assert not is_valid
        assert "running" in msg

    def test_range_check_valid(self):
        is_valid, msg = ArtifactValidator.range_check(50, 0, 100, "priority")
        assert is_valid
        assert msg == ""

    def test_range_check_too_low(self):
        is_valid, msg = ArtifactValidator.range_check(-1, 0, 100, "priority")
        assert not is_valid
        assert "out of range" in msg

    def test_range_check_too_high(self):
        is_valid, msg = ArtifactValidator.range_check(101, 0, 100, "priority")
        assert not is_valid
        assert "out of range" in msg

    def test_safe_get_valid(self):
        obj = {"a": {"b": {"c": "value"}}}
        result = ArtifactValidator.safe_get(obj, ["a", "b", "c"])
        assert result == "value"

    def test_safe_get_missing_key(self):
        obj = {"a": {"b": {}}}
        result = ArtifactValidator.safe_get(obj, ["a", "b", "c"], "default")
        assert result == "default"

    def test_safe_get_type_error(self):
        obj = {"a": "string_not_dict"}
        result = ArtifactValidator.safe_get(obj, ["a", "b", "c"], "default")
        assert result == "default"

    def test_is_nonempty_string_valid(self):
        assert ArtifactValidator.is_nonempty_string("hello")
        assert ArtifactValidator.is_nonempty_string("  text  ")

    def test_is_nonempty_string_invalid(self):
        assert not ArtifactValidator.is_nonempty_string("")
        assert not ArtifactValidator.is_nonempty_string("   ")
        assert not ArtifactValidator.is_nonempty_string(123)
        assert not ArtifactValidator.is_nonempty_string(None)

    def test_required_field_present(self):
        obj = {"name": "value"}
        is_valid, msg = ArtifactValidator.required_field(obj, "name")
        assert is_valid
        assert msg == ""

    def test_required_field_missing(self):
        obj = {"other": "value"}
        is_valid, msg = ArtifactValidator.required_field(obj, "name")
        assert not is_valid
        assert "Missing required field" in msg

    def test_required_field_with_type(self):
        obj = {"count": 42}
        is_valid, msg = ArtifactValidator.required_field(obj, "count", int)
        assert is_valid

    def test_required_field_type_mismatch(self):
        obj = {"count": "not_int"}
        is_valid, msg = ArtifactValidator.required_field(obj, "count", int)
        assert not is_valid
        assert "expected int" in msg

    def test_type_check_with_different_types(self):
        """Test type_check with various type combinations."""
        test_cases = [
            ([], list, "items"),
            ({}, dict, "config"),
            (True, bool, "flag"),
            (3.14, float, "ratio"),
        ]
        for value, expected_type, field in test_cases:
            is_valid, msg = ArtifactValidator.type_check(
                value, expected_type, field
            )
            assert is_valid, f"{field} should be valid for type {expected_type}"

    def test_range_check_boundaries(self):
        """Test range_check at exact boundaries."""
        is_valid, msg = ArtifactValidator.range_check(0, 0, 100, "value")
        assert is_valid
        is_valid, msg = ArtifactValidator.range_check(100, 0, 100, "value")
        assert is_valid

    def test_safe_get_deeply_nested(self):
        """Test safe_get with deeply nested paths."""
        obj = {
            "level1": {
                "level2": {"level3": {"level4": "deep_value"}}
            }
        }
        result = ArtifactValidator.safe_get(
            obj, ["level1", "level2", "level3", "level4"]
        )
        assert result == "deep_value"

    def test_safe_get_with_none_value(self):
        """Test safe_get when path contains None."""
        obj = {"key": None}
        result = ArtifactValidator.safe_get(obj, ["key"], "default")
        assert result is None

    def test_enum_check_case_sensitive(self):
        """Test that enum_check is case-sensitive."""
        is_valid, msg = ArtifactValidator.enum_check(
            "Executed", {"executed", "failed"}, "status"
        )
        assert not is_valid

    def test_enum_check_empty_allowed_set(self):
        """Test enum_check with empty allowed set."""
        is_valid, msg = ArtifactValidator.enum_check("value", set(), "status")
        assert not is_valid
        assert "not in allowed values" in msg


class TestExecutionOutcomeValidator:
    """Tests for ExecutionOutcomeValidator."""

    def test_valid_outcome(self):
        outcome = {
            "task_id": "task-123",
            "status": "executed",
            "worker_role": "executor",
        }
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert is_valid
        assert msg == ""

    def test_root_type_mismatch(self):
        is_valid, msg = ExecutionOutcomeValidator.validate([])
        assert not is_valid
        assert "must be dict" in msg

    def test_missing_task_id(self):
        outcome = {"status": "executed"}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "task_id" in msg

    def test_empty_task_id(self):
        outcome = {"task_id": "", "status": "executed"}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "non-empty" in msg

    def test_missing_status(self):
        outcome = {"task_id": "task-123"}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "status" in msg

    def test_invalid_status(self):
        outcome = {"task_id": "task-123", "status": "invalid"}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "invalid" in msg

    def test_status_type_mismatch(self):
        outcome = {"task_id": "task-123", "status": 404}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "status" in msg

    def test_invalid_attempt_range(self):
        outcome = {
            "task_id": "task-123",
            "status": "executed",
            "attempt": 2000,
        }
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "attempt" in msg
        assert "out of range" in msg

    def test_valid_attempt_boundaries(self):
        for attempt in [1, 500, 1000]:
            outcome = {
                "task_id": "task-123",
                "status": "executed",
                "attempt": attempt,
            }
            is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
            assert is_valid, f"Attempt {attempt} should be valid"

    def test_attempt_too_low(self):
        outcome = {
            "task_id": "task-123",
            "status": "executed",
            "attempt": 0,
        }
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "out of range" in msg

    def test_attempt_type_mismatch(self):
        outcome = {
            "task_id": "task-123",
            "status": "executed",
            "attempt": "first",
        }
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "attempt" in msg

    def test_all_valid_statuses(self):
        valid_statuses = {
            "executed",
            "failed",
            "timeout",
            "unknown",
            "no_op",
            "error",
        }
        for status in valid_statuses:
            outcome = {"task_id": "task-123", "status": status}
            is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
            assert is_valid, f"Status '{status}' should be valid"

    def test_whitespace_task_id(self):
        outcome = {"task_id": "   ", "status": "executed"}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "non-empty" in msg

    def test_task_id_type_mismatch(self):
        outcome = {"task_id": 123, "status": "executed"}
        is_valid, msg = ExecutionOutcomeValidator.validate(outcome)
        assert not is_valid
        assert "task_id" in msg


class TestRequestValidator:
    """Tests for RequestValidator."""

    def test_valid_request(self):
        request = {"task": {"id": "task-123"}}
        is_valid, msg = RequestValidator.validate(request)
        assert is_valid
        assert msg == ""

    def test_root_type_mismatch(self):
        is_valid, msg = RequestValidator.validate([])
        assert not is_valid
        assert "must be dict" in msg

    def test_missing_task(self):
        request = {"other": "value"}
        is_valid, msg = RequestValidator.validate(request)
        assert not is_valid
        assert "task" in msg

    def test_task_type_mismatch(self):
        request = {"task": "not_a_dict"}
        is_valid, msg = RequestValidator.validate(request)
        assert not is_valid
        assert "task" in msg

    def test_root_type_is_list(self):
        is_valid, msg = RequestValidator.validate({"task": {}})
        assert is_valid

    def test_root_type_is_string(self):
        is_valid, msg = RequestValidator.validate("not_a_dict")
        assert not is_valid

    def test_empty_task_dict(self):
        request = {"task": {}}
        is_valid, msg = RequestValidator.validate(request)
        assert is_valid

    def test_task_with_complex_structure(self):
        request = {
            "task": {
                "id": "task-123",
                "name": "test-task",
                "config": {"key": "value"},
            }
        }
        is_valid, msg = RequestValidator.validate(request)
        assert is_valid


class TestValidationHistoryValidator:
    """Tests for ValidationHistoryValidator."""

    def test_valid_validation(self):
        validation = {"passed": True}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert is_valid
        assert msg == ""

    def test_root_type_mismatch(self):
        is_valid, msg = ValidationHistoryValidator.validate([])
        assert not is_valid
        assert "must be dict" in msg

    def test_missing_passed(self):
        validation = {"errors": []}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid
        assert "passed" in msg

    def test_passed_type_mismatch(self):
        validation = {"passed": "yes"}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid

    def test_invalid_errors_type(self):
        validation = {"passed": True, "errors": "not_a_list"}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid
        assert "errors" in msg

    def test_invalid_error_item(self):
        validation = {"passed": True, "errors": [{"code": "E001"}, "not_a_dict"]}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid
        assert "errors[1]" in msg

    def test_warnings_valid(self):
        validation = {"passed": True, "warnings": []}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert is_valid

    def test_warnings_with_items(self):
        validation = {
            "passed": False,
            "errors": [{"code": "E001"}],
            "warnings": [{"code": "W001"}],
        }
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert is_valid

    def test_warnings_type_mismatch(self):
        validation = {"passed": True, "warnings": "not_a_list"}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid
        assert "warnings" in msg

    def test_error_code_empty(self):
        validation = {"passed": True, "errors": [{"code": ""}]}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid
        assert "code" in msg

    def test_error_code_whitespace(self):
        validation = {"passed": True, "errors": [{"code": "   "}]}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert not is_valid
        assert "code" in msg

    def test_error_without_code(self):
        validation = {"passed": True, "errors": [{"message": "something"}]}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert is_valid

    def test_multiple_errors(self):
        validation = {
            "passed": False,
            "errors": [{"code": "E001"}, {"code": "E002"}, {"code": "E003"}],
        }
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert is_valid

    def test_passed_false(self):
        validation = {"passed": False}
        is_valid, msg = ValidationHistoryValidator.validate(validation)
        assert is_valid


class TestDependencyReportValidator:
    """Tests for DependencyReportValidator."""

    def test_valid_report(self):
        report = {"statuses": []}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_root_type_mismatch(self):
        is_valid, msg = DependencyReportValidator.validate([])
        assert not is_valid
        assert "must be dict" in msg

    def test_missing_statuses(self):
        report = {"created_task_ids": []}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    def test_statuses_type_mismatch(self):
        report = {"statuses": "not_a_list"}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    def test_invalid_status_item(self):
        report = {"statuses": ["not_a_dict"]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    def test_invalid_severity(self):
        report = {
            "statuses": [{"severity": "invalid"}],
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_valid_severities(self):
        for severity in ["info", "warning", "error"]:
            report = {"statuses": [{"severity": severity}]}
            is_valid, msg = DependencyReportValidator.validate(report)
            assert is_valid, f"severity '{severity}' should be valid"

    def test_severity_type_mismatch(self):
        report = {"statuses": [{"severity": 123}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg
        assert "expected str" in msg

    def test_multiple_statuses_valid(self):
        report = {
            "statuses": [
                {"severity": "info"},
                {"severity": "warning"},
                {"severity": "error"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid

    def test_status_without_severity(self):
        report = {"statuses": [{"name": "some_status"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid


class TestLintItemValidator:
    """Tests for LintItemValidator."""

    def test_valid_lint_item(self):
        item = {
            "filename": "test.py",
            "code": "E501",
            "message": "Line too long",
            "location": {"row": 10, "column": 88},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert is_valid, f"Expected valid, got: {msg}"

    def test_missing_filename(self):
        item = {"code": "E501", "location": {"row": 10}}
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "filename" in msg

    def test_empty_filename(self):
        item = {
            "filename": "",
            "code": "E501",
            "location": {"row": 10},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "filename" in msg

    def test_missing_location(self):
        item = {"filename": "test.py", "code": "E501"}
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "location" in msg

    def test_location_type_mismatch(self):
        item = {
            "filename": "test.py",
            "location": "not_a_dict",
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "location" in msg

    def test_location_missing_both_row_and_column(self):
        item = {"filename": "test.py", "location": {}}
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "row" in msg or "column" in msg

    def test_location_with_row_only(self):
        item = {"filename": "test.py", "location": {"row": 10}}
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert is_valid

    def test_location_with_column_only(self):
        item = {"filename": "test.py", "location": {"column": 88}}
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert is_valid

    def test_row_type_mismatch(self):
        item = {
            "filename": "test.py",
            "location": {"row": "not_an_int", "column": 0},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "row" in msg

    def test_column_type_mismatch(self):
        item = {
            "filename": "test.py",
            "location": {"row": 10, "column": "not_an_int"},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "column" in msg

    def test_row_out_of_range_low(self):
        item = {
            "filename": "test.py",
            "location": {"row": 0},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "out of range" in msg

    def test_row_out_of_range_high(self):
        item = {
            "filename": "test.py",
            "location": {"row": 1000001},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "out of range" in msg

    def test_column_out_of_range_negative(self):
        item = {
            "filename": "test.py",
            "location": {"column": -1},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "out of range" in msg

    def test_column_out_of_range_high(self):
        item = {
            "filename": "test.py",
            "location": {"column": 1000001},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "out of range" in msg

    def test_valid_row_boundaries(self):
        for row in [1, 500, 1000000]:
            item = {"filename": "test.py", "location": {"row": row}}
            is_valid, msg = LintItemValidator.validate(item, 0)
            assert is_valid, f"Row {row} should be valid"

    def test_valid_column_boundaries(self):
        for col in [0, 500, 1000000]:
            item = {"filename": "test.py", "location": {"column": col}}
            is_valid, msg = LintItemValidator.validate(item, 0)
            assert is_valid, f"Column {col} should be valid"

    def test_empty_code(self):
        item = {
            "filename": "test.py",
            "code": "",
            "location": {"row": 10},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "code" in msg

    def test_whitespace_only_code(self):
        item = {
            "filename": "test.py",
            "code": "   ",
            "location": {"row": 10},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "code" in msg

    def test_message_type_mismatch(self):
        item = {
            "filename": "test.py",
            "message": 123,
            "location": {"row": 10},
        }
        is_valid, msg = LintItemValidator.validate(item, 0)
        assert not is_valid
        assert "message" in msg

    def test_item_type_mismatch(self):
        is_valid, msg = LintItemValidator.validate("not_a_dict", 0)
        assert not is_valid
        assert "expected dict" in msg


class TestArtifactValidatorLogging:
    """Tests for ArtifactValidator logging methods."""

    @patch("operations_center.observer.validation.logger")
    def test_log_parse_error_json_decode_error(self, mock_logger):
        """Parse errors are logged with JSON-specific details."""
        error = json.JSONDecodeError("Expecting value", "doc", 10)
        error.lineno = 1
        error.colno = 5
        path = Path("/tmp/test.json")

        ArtifactValidator.log_parse_error(path, error)

        assert mock_logger.debug.called
        call_args = mock_logger.debug.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["artifact"] == str(path)
        assert extra_data["error_type"] == "parse_error"
        assert extra_data["line"] == 1
        assert extra_data["col"] == 5
        assert extra_data["severity"] == "HIGH"

    @patch("operations_center.observer.validation.logger")
    def test_log_parse_error_generic_exception(self, mock_logger):
        """Generic exceptions are logged without JSON-specific fields."""
        error = ValueError("Something went wrong")
        path = Path("/tmp/test.json")

        ArtifactValidator.log_parse_error(path, error)

        assert mock_logger.debug.called
        call_args = mock_logger.debug.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["artifact"] == str(path)
        assert extra_data["error_type"] == "parse_error"
        assert extra_data["severity"] == "MEDIUM"
        assert "line" not in extra_data or extra_data.get("line") is None

    @patch("operations_center.observer.validation.logger")
    def test_log_parse_error_with_context(self, mock_logger):
        """Parse errors include additional context."""
        error = json.JSONDecodeError("Invalid", "doc", 0)
        error.lineno = 1
        error.colno = 1
        path = Path("/tmp/test.json")
        context = {"collector": "test_collector", "run_id": "12345"}

        ArtifactValidator.log_parse_error(path, error, context)

        call_args = mock_logger.debug.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["collector"] == "test_collector"
        assert extra_data["run_id"] == "12345"

    @patch("operations_center.observer.validation.logger")
    def test_log_structure_error(self, mock_logger):
        """Structure errors are logged at WARNING level."""
        path = Path("/tmp/test.json")
        error_msg = "Missing required field 'status'"

        ArtifactValidator.log_structure_error(path, error_msg)

        assert mock_logger.warning.called
        call_args = mock_logger.warning.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["artifact"] == str(path)
        assert extra_data["error_type"] == "structure_error"
        assert extra_data["error_msg"] == error_msg
        assert extra_data["severity"] == "HIGH"
        assert extra_data["action"] == "skipped_malformed_artifact"

    @patch("operations_center.observer.validation.logger")
    def test_log_structure_error_with_schema(self, mock_logger):
        """Structure errors can include expected schema info."""
        path = Path("/tmp/test.json")
        error_msg = "Invalid field type"
        schema = "ExecutionOutcome v1"

        ArtifactValidator.log_structure_error(
            path, error_msg, expected_schema=schema
        )

        call_args = mock_logger.warning.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["expected_schema"] == schema

    @patch("operations_center.observer.validation.logger")
    def test_log_structure_error_with_context(self, mock_logger):
        """Structure errors include additional context."""
        path = Path("/tmp/test.json")
        error_msg = "Invalid enum value"
        context = {"field": "status", "value": "invalid"}

        ArtifactValidator.log_structure_error(path, error_msg, context=context)

        call_args = mock_logger.warning.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["field"] == "status"
        assert extra_data["value"] == "invalid"

    @patch("operations_center.observer.validation.logger")
    def test_log_io_error_permission_denied(self, mock_logger):
        """Permission errors are logged at WARNING level."""
        error = PermissionError("Access denied")
        path = Path("/tmp/protected.json")

        ArtifactValidator.log_io_error(path, error)

        assert mock_logger.log.called
        call_args = mock_logger.log.call_args
        log_level = call_args[0][0]
        assert log_level == logging.WARNING
        extra_data = call_args[1]["extra"]
        assert extra_data["severity"] == "MEDIUM"

    @patch("operations_center.observer.validation.logger")
    def test_log_io_error_file_not_found(self, mock_logger):
        """File not found errors are logged at DEBUG level."""
        error = FileNotFoundError("File not found")
        path = Path("/tmp/missing.json")

        ArtifactValidator.log_io_error(path, error)

        assert mock_logger.log.called
        call_args = mock_logger.log.call_args
        log_level = call_args[0][0]
        assert log_level == logging.DEBUG
        extra_data = call_args[1]["extra"]
        assert extra_data["severity"] == "LOW"
        assert extra_data["error_type"] == "io_error"

    @patch("operations_center.observer.validation.logger")
    def test_log_io_error_with_context(self, mock_logger):
        """IO errors include additional context."""
        error = OSError("Read error")
        path = Path("/tmp/test.json")
        context = {"attempt": 2, "retry_count": 3}

        ArtifactValidator.log_io_error(path, error, context)

        call_args = mock_logger.log.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["attempt"] == 2
        assert extra_data["retry_count"] == 3

    @patch("operations_center.observer.validation.logger")
    def test_log_io_error_unicode_error(self, mock_logger):
        """Unicode decode errors are logged appropriately."""
        error = UnicodeDecodeError(
            "utf-8", b"invalid", 0, 1, "invalid start byte"
        )
        path = Path("/tmp/corrupted.json")

        ArtifactValidator.log_io_error(path, error)

        assert mock_logger.log.called
        call_args = mock_logger.log.call_args
        extra_data = call_args[1]["extra"]
        assert extra_data["error_type"] == "io_error"
        assert "UnicodeDecodeError" in extra_data["error_msg"]
