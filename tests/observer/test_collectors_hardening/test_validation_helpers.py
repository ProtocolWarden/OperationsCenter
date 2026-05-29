# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for validation helper library."""

import pytest

pytestmark = pytest.mark.slow

from operations_center.observer.validation import (
    ArtifactValidator,
    DependencyReportValidator,
    ExecutionOutcomeValidator,
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
