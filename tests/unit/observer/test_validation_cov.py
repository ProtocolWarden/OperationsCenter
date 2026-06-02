# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Complementary coverage tests for observer.validation.

Focuses on branches not exercised by the existing helper tests:
metrics_exporter wiring, export-exception swallowing, dataclass
behaviour, and the full LintItemValidator branch matrix.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.observer.validation import (
    ArtifactValidator,
    DependencyReportValidator,
    ExecutionOutcomeValidator,
    LintItemValidator,
    ParseError,
    ParseErrorMetadata,
    RequestValidator,
    ValidationHistoryValidator,
)


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
class TestParseError:
    def test_str_with_types(self) -> None:
        pe = ParseError(field="x", error="bad", expected_type="int", actual_type="str")
        assert str(pe) == "x: expected int, got str"

    def test_str_without_types(self) -> None:
        pe = ParseError(field="x", error="bad")
        assert str(pe) == "x: bad"

    def test_str_partial_types_falls_back_to_error(self) -> None:
        # Only expected_type set -> the and-condition is False -> error branch.
        pe = ParseError(field="x", error="bad", expected_type="int")
        assert str(pe) == "x: bad"


class TestParseErrorMetadata:
    def test_defaults(self) -> None:
        meta = ParseErrorMetadata()
        assert meta.total_errors == 0
        assert meta.last_error_type is None
        assert meta.last_error_msg is None
        assert meta.error_categories == {}

    def test_independent_dict_instances(self) -> None:
        a = ParseErrorMetadata()
        b = ParseErrorMetadata()
        a.error_categories["json"] = 1
        assert b.error_categories == {}


# --------------------------------------------------------------------------- #
# Static helper edge branches
# --------------------------------------------------------------------------- #
class TestSafeGet:
    def test_returns_value_at_path(self) -> None:
        obj = {"a": {"b": {"c": 5}}}
        assert ArtifactValidator.safe_get(obj, ["a", "b", "c"]) == 5

    def test_non_dict_midway_returns_default(self) -> None:
        obj = {"a": 7}
        assert ArtifactValidator.safe_get(obj, ["a", "b"], default="d") == "d"

    def test_missing_key_returns_default(self) -> None:
        assert ArtifactValidator.safe_get({"a": {}}, ["a", "z"], default=None) is None

    def test_empty_path_returns_obj(self) -> None:
        obj = {"a": 1}
        assert ArtifactValidator.safe_get(obj, []) is obj


class TestTypeAndRangeChecks:
    def test_type_check_ok_and_fail(self) -> None:
        assert ArtifactValidator.type_check(1, int, "n") == (True, "")
        ok, msg = ArtifactValidator.type_check("x", int, "n")
        assert ok is False
        assert "expected int" in msg and "got str" in msg

    def test_enum_check(self) -> None:
        assert ArtifactValidator.enum_check("a", {"a", "b"}, "f") == (True, "")
        ok, msg = ArtifactValidator.enum_check("z", {"a", "b"}, "f")
        assert ok is False
        assert "not in allowed values" in msg

    def test_range_check(self) -> None:
        assert ArtifactValidator.range_check(5, 1, 10, "f") == (True, "")
        ok, _ = ArtifactValidator.range_check(0, 1, 10, "f")
        assert ok is False

    def test_required_field_no_type(self) -> None:
        assert ArtifactValidator.required_field({"f": 1}, "f") == (True, "")

    def test_is_nonempty_string_whitespace(self) -> None:
        assert ArtifactValidator.is_nonempty_string("   ") is False
        assert ArtifactValidator.is_nonempty_string("x") is True
        assert ArtifactValidator.is_nonempty_string(5) is False


# --------------------------------------------------------------------------- #
# Logging helpers + metrics_exporter wiring
# --------------------------------------------------------------------------- #
class TestLogParseError:
    def test_jsondecodeerror_high_severity_and_export(self) -> None:
        exporter = MagicMock()
        try:
            json.loads("{bad")
        except json.JSONDecodeError as exc:
            err = exc
        ArtifactValidator.log_parse_error(
            Path("/tmp/a.json"),
            err,
            context={"collector": "depdrift", "extra": "k"},
            metrics_exporter=exporter,
        )
        exporter.export_failure.assert_called_once()
        kw = exporter.export_failure.call_args.kwargs
        assert kw["collector_name"] == "depdrift"
        assert kw["severity"] == "HIGH"
        assert kw["failure_type"] == "parse_error"
        # collector key stripped from nested context, error_class added.
        assert "collector" not in kw["context"]
        assert kw["context"]["error_class"] == "JSONDecodeError"
        assert kw["context"]["extra"] == "k"

    def test_non_json_error_medium_no_context(self) -> None:
        exporter = MagicMock()
        ArtifactValidator.log_parse_error("x.json", ValueError("boom"), metrics_exporter=exporter)
        kw = exporter.export_failure.call_args.kwargs
        assert kw["severity"] == "MEDIUM"
        assert kw["collector_name"] == "unknown"

    def test_no_exporter_does_not_crash(self) -> None:
        result = ArtifactValidator.log_parse_error("x.json", ValueError("boom"))
        assert result is None

    def test_export_exception_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        exporter = MagicMock()
        exporter.export_failure.side_effect = RuntimeError("nope")
        with caplog.at_level(logging.DEBUG):
            ArtifactValidator.log_parse_error(
                "x.json", ValueError("boom"), metrics_exporter=exporter
            )
        assert any("Failed to export parse error" in r.message for r in caplog.records)


class TestLogStructureError:
    def test_export_with_schema(self) -> None:
        exporter = MagicMock()
        ArtifactValidator.log_structure_error(
            "x.json",
            "bad shape",
            expected_schema="request",
            context={"collector": "c1"},
            metrics_exporter=exporter,
        )
        kw = exporter.export_failure.call_args.kwargs
        assert kw["artifact_type"] == "request"
        assert kw["severity"] == "HIGH"
        assert kw["context"]["expected_schema"] == "request"
        assert "collector" not in kw["context"]

    def test_export_schema_none_defaults_unknown(self) -> None:
        exporter = MagicMock()
        ArtifactValidator.log_structure_error("x.json", "bad", metrics_exporter=exporter)
        assert exporter.export_failure.call_args.kwargs["artifact_type"] == "unknown"

    def test_export_exception_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        exporter = MagicMock()
        exporter.export_failure.side_effect = RuntimeError("nope")
        with caplog.at_level(logging.DEBUG):
            ArtifactValidator.log_structure_error("x.json", "bad", metrics_exporter=exporter)
        assert any("Failed to export structure error" in r.message for r in caplog.records)

    def test_no_exporter(self) -> None:
        result = ArtifactValidator.log_structure_error("x.json", "bad")
        assert result is None


class TestLogIoError:
    def test_permission_error_warning_medium(self, caplog: pytest.LogCaptureFixture) -> None:
        exporter = MagicMock()
        with caplog.at_level(logging.WARNING):
            ArtifactValidator.log_io_error(
                "x.json", PermissionError("denied"), metrics_exporter=exporter
            )
        kw = exporter.export_failure.call_args.kwargs
        assert kw["severity"] == "MEDIUM"
        assert kw["artifact_type"] == "file"
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_generic_error_debug_low(self) -> None:
        exporter = MagicMock()
        ArtifactValidator.log_io_error(
            "x.json", OSError("oops"), context={"collector": "c"}, metrics_exporter=exporter
        )
        kw = exporter.export_failure.call_args.kwargs
        assert kw["severity"] == "LOW"
        assert kw["context"]["error_class"] == "OSError"
        assert "collector" not in kw["context"]

    def test_export_exception_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        exporter = MagicMock()
        exporter.export_failure.side_effect = RuntimeError("nope")
        with caplog.at_level(logging.DEBUG):
            ArtifactValidator.log_io_error("x.json", OSError("oops"), metrics_exporter=exporter)
        assert any("Failed to export IO error" in r.message for r in caplog.records)

    def test_no_exporter(self) -> None:
        result = ArtifactValidator.log_io_error("x.json", OSError("oops"))
        assert result is None


# --------------------------------------------------------------------------- #
# ExecutionOutcomeValidator branches
# --------------------------------------------------------------------------- #
class TestExecutionOutcomeValidator:
    def test_valid_with_attempt(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate(
            {"task_id": "t1", "status": "executed", "attempt": 3}
        )
        assert ok is True and msg == ""

    def test_root_not_dict(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate([])  # type: ignore[arg-type]
        assert ok is False and "Root must be dict" in msg

    def test_task_id_wrong_type(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate({"task_id": 5, "status": "executed"})
        assert ok is False and "task_id" in msg

    def test_task_id_empty(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate({"task_id": "  ", "status": "executed"})
        assert ok is False and "non-empty" in msg

    def test_missing_status(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate({"task_id": "t"})
        assert ok is False and "status" in msg

    def test_bad_status_value(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate({"task_id": "t", "status": "weird"})
        assert ok is False and "not in allowed values" in msg

    def test_attempt_wrong_type(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate(
            {"task_id": "t", "status": "executed", "attempt": "1"}
        )
        assert ok is False and "attempt: expected int" in msg

    def test_attempt_out_of_range(self) -> None:
        ok, msg = ExecutionOutcomeValidator.validate(
            {"task_id": "t", "status": "executed", "attempt": 0}
        )
        assert ok is False and "out of range" in msg

    def test_attempt_bool_is_int_subclass_in_range(self) -> None:
        # bool is an int subclass; True == 1 is within [1, 1000].
        ok, _ = ExecutionOutcomeValidator.validate(
            {"task_id": "t", "status": "executed", "attempt": True}
        )
        assert ok is True


# --------------------------------------------------------------------------- #
# RequestValidator branches
# --------------------------------------------------------------------------- #
class TestRequestValidator:
    def test_valid(self) -> None:
        assert RequestValidator.validate({"task": {}}) == (True, "")

    def test_root_not_dict(self) -> None:
        ok, msg = RequestValidator.validate("nope")  # type: ignore[arg-type]
        assert ok is False and "Root must be dict" in msg

    def test_missing_task(self) -> None:
        ok, msg = RequestValidator.validate({})
        assert ok is False and "task" in msg

    def test_task_wrong_type(self) -> None:
        ok, msg = RequestValidator.validate({"task": 1})
        assert ok is False and "expected dict" in msg


# --------------------------------------------------------------------------- #
# ValidationHistoryValidator branches
# --------------------------------------------------------------------------- #
class TestValidationHistoryValidator:
    def test_valid_minimal(self) -> None:
        assert ValidationHistoryValidator.validate({"passed": True}) == (True, "")

    def test_valid_with_errors_and_warnings(self) -> None:
        ok, msg = ValidationHistoryValidator.validate(
            {
                "passed": False,
                "errors": [{"code": "E1"}, {}],
                "warnings": ["w"],
            }
        )
        assert ok is True and msg == ""

    def test_root_not_dict(self) -> None:
        ok, msg = ValidationHistoryValidator.validate(3)  # type: ignore[arg-type]
        assert ok is False and "Root must be dict" in msg

    def test_missing_passed(self) -> None:
        ok, msg = ValidationHistoryValidator.validate({})
        assert ok is False and "passed" in msg

    def test_errors_not_list(self) -> None:
        ok, msg = ValidationHistoryValidator.validate({"passed": True, "errors": {}})
        assert ok is False and "errors: expected list" in msg

    def test_error_item_not_dict(self) -> None:
        ok, msg = ValidationHistoryValidator.validate({"passed": True, "errors": ["x"]})
        assert ok is False and "errors[0]: expected dict" in msg

    def test_error_code_empty(self) -> None:
        ok, msg = ValidationHistoryValidator.validate({"passed": True, "errors": [{"code": ""}]})
        assert ok is False and "errors[0].code" in msg

    def test_warnings_not_list(self) -> None:
        ok, msg = ValidationHistoryValidator.validate({"passed": True, "warnings": "x"})
        assert ok is False and "warnings: expected list" in msg


# --------------------------------------------------------------------------- #
# DependencyReportValidator branches
# --------------------------------------------------------------------------- #
class TestDependencyReportValidator:
    def test_valid(self) -> None:
        ok, msg = DependencyReportValidator.validate({"statuses": [{"severity": "info"}, {}]})
        assert ok is True and msg == ""

    def test_root_not_dict(self) -> None:
        ok, msg = DependencyReportValidator.validate(0)  # type: ignore[arg-type]
        assert ok is False and "Root must be dict" in msg

    def test_missing_statuses(self) -> None:
        ok, msg = DependencyReportValidator.validate({})
        assert ok is False and "statuses" in msg

    def test_status_item_not_dict(self) -> None:
        ok, msg = DependencyReportValidator.validate({"statuses": ["x"]})
        assert ok is False and "statuses[0]: expected dict" in msg

    def test_severity_wrong_type(self) -> None:
        ok, msg = DependencyReportValidator.validate({"statuses": [{"severity": 1}]})
        assert ok is False and "severity: expected str" in msg

    def test_severity_bad_value(self) -> None:
        ok, msg = DependencyReportValidator.validate({"statuses": [{"severity": "critical"}]})
        assert ok is False and "not in" in msg


# --------------------------------------------------------------------------- #
# LintItemValidator branches
# --------------------------------------------------------------------------- #
class TestLintItemValidator:
    def test_valid_full(self) -> None:
        ok, msg = LintItemValidator.validate(
            {"filename": "f.py", "location": {"row": 5, "column": 2}}, 0
        )
        assert ok is True and msg == ""

    def test_valid_no_column(self) -> None:
        ok, _ = LintItemValidator.validate({"filename": "f.py", "location": {"row": 1}}, 0)
        assert ok is True

    def test_item_not_dict(self) -> None:
        ok, msg = LintItemValidator.validate("x", 2)  # type: ignore[arg-type]
        assert ok is False and "[2]: expected dict" in msg

    def test_missing_filename(self) -> None:
        ok, msg = LintItemValidator.validate({"location": {"row": 1}}, 0)
        assert ok is False and "'filename'" in msg

    def test_empty_filename(self) -> None:
        ok, msg = LintItemValidator.validate({"filename": "  ", "location": {"row": 1}}, 0)
        assert ok is False and "filename: must be non-empty" in msg

    def test_missing_location(self) -> None:
        ok, msg = LintItemValidator.validate({"filename": "f.py"}, 0)
        assert ok is False and "'location'" in msg

    def test_location_not_dict(self) -> None:
        ok, msg = LintItemValidator.validate({"filename": "f.py", "location": 5}, 0)
        assert ok is False and "location: expected dict" in msg

    def test_missing_row(self) -> None:
        ok, msg = LintItemValidator.validate({"filename": "f.py", "location": {}}, 0)
        assert ok is False and "missing required field 'row'" in msg

    def test_row_wrong_type(self) -> None:
        ok, msg = LintItemValidator.validate({"filename": "f.py", "location": {"row": "1"}}, 0)
        assert ok is False and "row: expected int" in msg

    def test_row_out_of_range(self) -> None:
        ok, msg = LintItemValidator.validate({"filename": "f.py", "location": {"row": 0}}, 0)
        assert ok is False and "out of range" in msg

    def test_column_wrong_type(self) -> None:
        ok, msg = LintItemValidator.validate(
            {"filename": "f.py", "location": {"row": 1, "column": "0"}}, 0
        )
        assert ok is False and "column: expected int" in msg

    def test_column_out_of_range(self) -> None:
        ok, msg = LintItemValidator.validate(
            {"filename": "f.py", "location": {"row": 1, "column": 1000001}}, 0
        )
        assert ok is False and "out of range" in msg
