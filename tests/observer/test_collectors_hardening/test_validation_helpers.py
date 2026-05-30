# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for validation helper library."""

import pytest

from operations_center.observer.validation import (
    ArtifactValidator,
    DependencyReportValidator,
    ExecutionOutcomeValidator,
    RequestValidator,
    ValidationHistoryValidator,
)
pytestmark = pytest.mark.slow


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

    # Priority 1: Whitespace and case sensitivity
    def test_severity_with_leading_whitespace(self):
        report = {"statuses": [{"severity": " info"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_with_trailing_whitespace(self):
        report = {"statuses": [{"severity": "info "}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_with_surrounding_whitespace(self):
        report = {"statuses": [{"severity": " info "}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_uppercase(self):
        report = {"statuses": [{"severity": "INFO"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_capitalized(self):
        report = {"statuses": [{"severity": "Info"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_mixed_case(self):
        report = {"statuses": [{"severity": "InFo"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_empty_string(self):
        report = {"statuses": [{"severity": ""}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_whitespace_only(self):
        report = {"statuses": [{"severity": "   "}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    # Priority 1: Optional severity field
    def test_status_without_severity_field(self):
        report = {"statuses": [{"name": "dependency1"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_status_with_extra_fields_no_severity(self):
        report = {"statuses": [{"name": "dep1", "version": "1.0"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_multiple_statuses_without_severity(self):
        report = {
            "statuses": [
                {"name": "dep1"},
                {"name": "dep2"},
                {"name": "dep3"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    # Priority 1: Null/None items
    def test_null_item_in_statuses(self):
        report = {"statuses": [None]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    def test_mixed_valid_and_null_items(self):
        report = {"statuses": [{"severity": "info"}, None]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[1]" in msg

    def test_null_item_before_valid_item(self):
        report = {"statuses": [None, {"severity": "info"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    # Priority 2: Statuses field is None
    def test_statuses_is_none(self):
        report = {"statuses": None}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    # Priority 2: Multiple status items with mixed validity
    def test_first_status_invalid_severity(self):
        report = {
            "statuses": [
                {"severity": "invalid"},
                {"severity": "info"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    def test_last_status_invalid_severity(self):
        report = {
            "statuses": [
                {"severity": "info"},
                {"severity": "warning"},
                {"severity": "invalid"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[2]" in msg

    def test_middle_status_invalid_severity(self):
        report = {
            "statuses": [
                {"severity": "info"},
                {"severity": "invalid"},
                {"severity": "error"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[1]" in msg

    def test_status_with_extra_fields_and_valid_severity(self):
        report = {
            "statuses": [
                {
                    "severity": "warning",
                    "name": "dep1",
                    "version": "1.0",
                    "extra_field": "ignored",
                }
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    # Priority 2: Non-string severity types
    def test_severity_is_int(self):
        report = {"statuses": [{"severity": 1}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_is_float(self):
        report = {"statuses": [{"severity": 1.0}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_is_boolean(self):
        report = {"statuses": [{"severity": True}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_is_list(self):
        report = {"statuses": [{"severity": ["info"]}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_is_dict(self):
        report = {"statuses": [{"severity": {"name": "info"}}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    # Priority 3: Large lists and performance
    def test_large_status_list(self):
        statuses = [{"severity": "info"} for _ in range(100)]
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_very_large_status_list(self):
        statuses = [{"severity": "warning"} for _ in range(1000)]
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_large_list_with_one_invalid_at_end(self):
        statuses = [{"severity": "info"} for _ in range(100)]
        statuses.append({"severity": "invalid"})
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[100]" in msg

    # Priority 3: Empty status item
    def test_empty_status_dict(self):
        report = {"statuses": [{}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_multiple_empty_status_dicts(self):
        report = {"statuses": [{}, {}, {}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    # Priority 3: Special characters in severity
    def test_severity_with_newline(self):
        report = {"statuses": [{"severity": "info\n"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_severity_with_tab(self):
        report = {"statuses": [{"severity": "info\t"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    # Additional: All valid severities in one report
    def test_all_valid_severities_in_one_report(self):
        report = {
            "statuses": [
                {"severity": "info"},
                {"severity": "warning"},
                {"severity": "error"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    # Additional: Mixed items with and without severity
    def test_mixed_items_with_and_without_severity(self):
        report = {
            "statuses": [
                {"severity": "info"},
                {"name": "dep2"},
                {"severity": "warning"},
                {"version": "1.0"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    # Additional: Root is other types
    def test_root_is_none(self):
        is_valid, msg = DependencyReportValidator.validate(None)
        assert not is_valid
        assert "must be dict" in msg

    def test_root_is_string(self):
        is_valid, msg = DependencyReportValidator.validate("not_a_dict")
        assert not is_valid
        assert "must be dict" in msg

    def test_root_is_int(self):
        is_valid, msg = DependencyReportValidator.validate(42)
        assert not is_valid
        assert "must be dict" in msg

    def test_root_is_empty_dict(self):
        report = {}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    # Additional: Numeric string severity
    def test_severity_is_numeric_string(self):
        report = {"statuses": [{"severity": "1"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg


class TestDependencyReportValidatorErrorHandlingStage3:
    """Stage 3: Comprehensive error handling, graceful degradation, and recovery tests."""

    # === ERROR HANDLING & DETAILED MESSAGES ===

    def test_error_message_includes_index_for_first_invalid_item(self):
        """Error handling: first invalid item index is clearly reported."""
        report = {
            "statuses": [
                {"severity": "info"},
                {"severity": "WARNING"},  # Invalid at index 1
                {"severity": "error"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[1]" in msg, "should identify item 1 as problematic"

    def test_error_message_includes_expected_and_actual_types(self):
        """Error handling: type mismatch error details both types."""
        report = {"statuses": [{"severity": 123}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "expected str" in msg
        assert "int" in msg

    def test_error_message_for_missing_required_field(self):
        """Error handling: missing statuses field has clear message."""
        report = {"other_field": []}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg
        assert "required" in msg.lower() or "missing" in msg.lower()

    def test_error_message_shows_invalid_severity_value(self):
        """Error handling: invalid severity value is shown in error."""
        report = {"statuses": [{"severity": "critical"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "critical" in msg or "not in allowed values" in msg

    def test_early_stopping_on_first_type_error(self):
        """Error handling: stops at first structural error."""
        report = {"statuses": [{"severity": 1}, {"severity": 2}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    # === GRACEFUL DEGRADATION ===

    def test_graceful_missing_severity_allows_other_fields(self):
        """Graceful: missing severity doesn't invalidate status with other fields."""
        report = {
            "statuses": [
                {"name": "python", "version": "3.9"},
                {"name": "nodejs", "version": "16.0"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_graceful_extra_fields_ignored(self):
        """Graceful: extra unknown fields are silently ignored."""
        report = {
            "statuses": [
                {
                    "severity": "warning",
                    "custom_field": "custom_value",
                    "metadata": {"nested": "data"},
                    "timestamp": "2026-05-30",
                }
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_graceful_deeply_nested_unknown_fields(self):
        """Graceful: nested structures in extra fields don't cause issues."""
        report = {
            "statuses": [
                {
                    "severity": "info",
                    "metadata": {
                        "level1": {
                            "level2": {
                                "level3": {"very": "nested"},
                            }
                        }
                    },
                }
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid

    def test_graceful_empty_root_with_statuses_required(self):
        """Graceful error: empty root dict clearly shows missing statuses."""
        report = {}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    def test_graceful_extra_root_fields_with_valid_statuses(self):
        """Graceful: root-level extra fields are ignored if statuses is valid."""
        report = {
            "statuses": [{"severity": "info"}],
            "metadata": {"version": "1.0"},
            "timestamp": "2026-05-30",
            "source": "collector",
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_graceful_mixed_status_structures(self):
        """Graceful: mix of complete and minimal status items."""
        report = {
            "statuses": [
                {"severity": "info"},  # Minimal
                {},  # Empty
                {"severity": "warning", "name": "dep1"},  # With extra
                {"message": "just a message"},  # No severity
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid

    # === RECOVERY PATHS & ERROR DIAGNOSIS ===

    def test_recovery_identifies_exact_type_mismatch_in_nested_field(self):
        """Recovery: nested type error includes full path."""
        report = {"statuses": [{"severity": {"nested": "dict"}}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg
        assert "severity" in msg
        assert "dict" in msg or "list" in msg

    def test_recovery_handles_mixed_type_list(self):
        """Recovery: list with mixed types fails at first non-dict."""
        report = {"statuses": [{"severity": "info"}, "invalid_string", {}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[1]" in msg

    def test_recovery_null_in_middle_of_list(self):
        """Recovery: None in middle of list is properly identified."""
        statuses = [
            {"severity": "info"},
            {"severity": "warning"},
            None,
            {"severity": "error"},
        ]
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[2]" in msg

    def test_recovery_large_list_error_at_specific_index(self):
        """Recovery: large list error points to exact failing item."""
        statuses = [{"severity": "info"}] * 500
        statuses[250] = {"severity": "INVALID"}
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[250]" in msg

    def test_recovery_whitespace_error_shows_attempted_value(self):
        """Recovery: whitespace errors are detected (not stripped/coerced)."""
        report = {"statuses": [{"severity": " info"}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    def test_recovery_case_error_shows_case_sensitivity(self):
        """Recovery: case sensitivity is enforced consistently."""
        for invalid_case in ["INFO", "Warning", "ERROR", "InFo"]:
            report = {"statuses": [{"severity": invalid_case}]}
            is_valid, msg = DependencyReportValidator.validate(report)
            assert not is_valid, f"'{invalid_case}' should be invalid"
            assert "severity" in msg

    def test_recovery_empty_string_severity_caught(self):
        """Recovery: empty severity is clearly identified as invalid."""
        report = {"statuses": [{"severity": ""}]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "severity" in msg

    # === EDGE CASES FOR EXCEPTION HANDLING ===

    def test_exception_handling_statuses_none_type(self):
        """Exception: None value for statuses field is caught."""
        report = {"statuses": None}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    def test_exception_handling_statuses_is_string(self):
        """Exception: string value for statuses is caught with clear type error."""
        report = {"statuses": "not_a_list"}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg
        assert "expected list" in msg or "list" in msg

    def test_exception_handling_statuses_is_number(self):
        """Exception: numeric value for statuses shows type error."""
        report = {"statuses": 42}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses" in msg

    def test_exception_handling_root_is_list(self):
        """Exception: list as root shows clear root type error."""
        is_valid, msg = DependencyReportValidator.validate([])
        assert not is_valid
        assert "root" in msg.lower() or "must be dict" in msg

    def test_exception_handling_root_is_none(self):
        """Exception: None root is caught with type error."""
        is_valid, msg = DependencyReportValidator.validate(None)
        assert not is_valid
        assert "dict" in msg

    def test_exception_handling_status_item_boolean(self):
        """Exception: boolean item in statuses list is caught."""
        report = {"statuses": [True, False]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    def test_exception_handling_status_item_number(self):
        """Exception: numeric item in statuses list is caught."""
        report = {"statuses": [42]}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert not is_valid
        assert "statuses[0]" in msg

    # === COMPREHENSIVE POSITIVE CASES (NO EXCEPTIONS) ===

    def test_complete_valid_report_with_all_valid_severities(self):
        """Positive: fully valid report with all severity types."""
        report = {
            "statuses": [
                {"severity": "info", "message": "info message"},
                {"severity": "warning", "message": "warning message"},
                {"severity": "error", "message": "error message"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_complete_valid_report_mixed_with_without_severity(self):
        """Positive: valid report mixing severity presence."""
        report = {
            "statuses": [
                {"severity": "info"},
                {"name": "package1"},
                {"severity": "warning", "version": "1.0"},
                {"metadata": "value"},
                {"severity": "error"},
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_complete_valid_report_with_extra_fields(self):
        """Positive: valid report with extra root and item fields."""
        report = {
            "statuses": [
                {
                    "severity": "info",
                    "name": "dep",
                    "version": "1.0",
                    "url": "https://example.com",
                }
            ],
            "generated_at": "2026-05-30",
            "schema_version": "1.0",
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_performance_large_valid_list(self):
        """Performance: large valid list is processed correctly."""
        statuses = [
            {"severity": ("info" if i % 3 == 0 else ("warning" if i % 3 == 1 else "error"))}
            for i in range(500)
        ]
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    def test_performance_very_large_list_all_empty_items(self):
        """Performance: large list of empty status items."""
        statuses = [{} for _ in range(1000)]
        report = {"statuses": statuses}
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid
        assert msg == ""

    # === VALIDATION SEQUENCE TESTS ===

    def test_validation_stops_at_root_type_error(self):
        """Sequence: root type error prevents further validation."""
        is_valid, msg = DependencyReportValidator.validate("not_dict")
        assert not is_valid
        assert "root" in msg.lower() or "dict" in msg

    def test_validation_stops_at_missing_statuses(self):
        """Sequence: missing statuses stops before item validation."""
        is_valid, msg = DependencyReportValidator.validate({})
        assert not is_valid
        assert "statuses" in msg

    def test_validation_stops_at_statuses_type_error(self):
        """Sequence: invalid statuses type prevents item validation."""
        is_valid, msg = DependencyReportValidator.validate({"statuses": "not_list"})
        assert not is_valid
        assert "statuses" in msg

    def test_validation_stops_at_first_item_type_error(self):
        """Sequence: first non-dict item stops validation."""
        is_valid, msg = DependencyReportValidator.validate(
            {"statuses": ["string", {"severity": "info"}]}
        )
        assert not is_valid
        assert "statuses[0]" in msg

    def test_validation_continues_past_missing_optional_severity(self):
        """Sequence: missing severity doesn't stop validation of other items."""
        report = {
            "statuses": [
                {"name": "dep1"},  # No severity
                {"severity": "info"},  # With severity
                {"version": "1.0"},  # No severity
            ]
        }
        is_valid, msg = DependencyReportValidator.validate(report)
        assert is_valid, "should validate all items even with missing optional field"
