# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Validation helpers for JSON artifact parsing in observer collectors.

Provides:
- Type guards with detailed error messages
- Nested structure validation
- Safe nested property extraction
- Common enum/range validators
- Security logging for malformed payloads
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParseError:
    """Represents a parse or validation error."""

    field: str
    error: str
    expected_type: Optional[str] = None
    actual_type: Optional[str] = None

    def __str__(self) -> str:
        if self.expected_type and self.actual_type:
            return (
                f"{self.field}: expected {self.expected_type}, "
                f"got {self.actual_type}"
            )
        return f"{self.field}: {self.error}"


@dataclass
class ParseErrorMetadata:
    """Track parse/validation errors in signals."""

    total_errors: int = 0
    last_error_type: Optional[str] = None
    last_error_msg: Optional[str] = None
    error_categories: dict[str, int] = dataclass_field(default_factory=dict)


class ArtifactValidator:
    """Base validator class for artifact-specific validators."""

    @staticmethod
    def type_check(
        value: Any, expected_type: type, field: str
    ) -> tuple[bool, str]:
        """Type validation with detailed error message.

        Args:
            value: Value to check
            expected_type: Expected type
            field: Field name for error message

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(value, expected_type):
            return False, (
                f"{field}: expected {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )
        return True, ""

    @staticmethod
    def enum_check(
        value: str, allowed: set[str], field: str
    ) -> tuple[bool, str]:
        """Enum validation.

        Args:
            value: Value to validate
            allowed: Set of allowed values
            field: Field name for error message

        Returns:
            (is_valid, error_message)
        """
        if value not in allowed:
            return False, (
                f"{field}: '{value}' not in allowed values: "
                f"{sorted(allowed)}"
            )
        return True, ""

    @staticmethod
    def range_check(
        value: int, min_val: int, max_val: int, field: str
    ) -> tuple[bool, str]:
        """Range validation for integers.

        Args:
            value: Value to validate
            min_val: Minimum allowed value (inclusive)
            max_val: Maximum allowed value (inclusive)
            field: Field name for error message

        Returns:
            (is_valid, error_message)
        """
        if not (min_val <= value <= max_val):
            return False, (
                f"{field}: {value} out of range [{min_val}, {max_val}]"
            )
        return True, ""

    @staticmethod
    def safe_get(obj: dict, path: list[str], default: Any = None) -> Any:
        """Safe nested property extraction.

        Validates dict type at each level before continuing.

        Args:
            obj: Object to traverse
            path: List of keys to follow
            default: Value to return if path not found or type error

        Returns:
            Value at path, or default if not found
        """
        current = obj
        for key in path:
            if not isinstance(current, dict):
                return default
            current = current.get(key, default)
            if current is default:
                return default
        return current

    @staticmethod
    def required_field(
        obj: dict, field: str, expected_type: Optional[type] = None
    ) -> tuple[bool, str]:
        """Check that a required field exists with optional type check.

        Args:
            obj: Dictionary to check
            field: Field name
            expected_type: Optional type to validate

        Returns:
            (is_valid, error_message)
        """
        if field not in obj:
            return False, f"Missing required field: {field}"

        value = obj[field]
        if expected_type is not None:
            if not isinstance(value, expected_type):
                return False, (
                    f"{field}: expected {expected_type.__name__}, "
                    f"got {type(value).__name__}"
                )

        return True, ""

    @staticmethod
    def is_nonempty_string(value: Any) -> bool:
        """Check if value is a non-empty string.

        Args:
            value: Value to check

        Returns:
            True if string and non-empty (after strip)
        """
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def log_parse_error(
        artifact_path: Path | str,
        error: Exception,
        context: dict | None = None,
    ) -> None:
        """Log malformed payload with security context.

        Args:
            artifact_path: Path to the malformed artifact
            error: Exception that occurred during parsing
            context: Additional context dict
        """
        if context is None:
            context = {}

        error_class = error.__class__.__name__
        log_data = {
            "event": "artifact_parse_error",
            "artifact": str(artifact_path),
            "error_type": "parse_error",
            "error_msg": "%s: %s" % (error_class, error),
            "severity": "MEDIUM",
            "component": "observer_collector",
            **context,
        }

        if isinstance(error, json.JSONDecodeError):
            log_data.update(
                {
                    "line": error.lineno,
                    "col": error.colno,
                    "severity": "HIGH",
                }
            )

        logger.debug(
            "Malformed JSON artifact: %(artifact)s — %(error_type)s: %(error_msg)s",
            log_data,
            extra=log_data,
        )

    @staticmethod
    def log_structure_error(
        artifact_path: Path | str,
        error_msg: str,
        expected_schema: str | None = None,
        context: dict | None = None,
    ) -> None:
        """Log structure validation failure with security context.

        Args:
            artifact_path: Path to the artifact with invalid structure
            error_msg: Description of the validation error
            expected_schema: Name of expected schema
            context: Additional context dict
        """
        if context is None:
            context = {}

        log_data = {
            "event": "artifact_structure_error",
            "artifact": str(artifact_path),
            "error_type": "structure_error",
            "error_msg": error_msg,
            "expected_schema": expected_schema,
            "severity": "HIGH",
            "component": "observer_collector",
            "action": "skipped_malformed_artifact",
            **context,
        }

        logger.warning(
            "Invalid artifact structure: %(artifact)s — %(error_type)s: %(error_msg)s",
            log_data,
            extra=log_data,
        )

    @staticmethod
    def log_io_error(
        artifact_path: Path | str,
        error: Exception,
        context: dict | None = None,
    ) -> None:
        """Log file I/O errors with security context.

        Args:
            artifact_path: Path to the artifact
            error: Exception that occurred during file read
            context: Additional context dict
        """
        if context is None:
            context = {}

        error_class = error.__class__.__name__
        log_level = logging.WARNING if isinstance(error, PermissionError) else logging.DEBUG

        log_data = {
            "event": "artifact_io_error",
            "artifact": str(artifact_path),
            "error_type": "io_error",
            "error_msg": "%s: %s" % (error_class, error),
            "severity": "MEDIUM" if isinstance(error, PermissionError) else "LOW",
            "component": "observer_collector",
            **context,
        }

        logger.log(
            log_level,
            "Failed to read artifact: %(artifact)s — %(error_type)s: %(error_msg)s",
            log_data,
            extra=log_data,
        )


class ExecutionOutcomeValidator(ArtifactValidator):
    """Validator for control_outcome.json artifacts."""

    @staticmethod
    def validate(outcome: dict) -> tuple[bool, str]:
        """Validate control_outcome.json structure.

        Args:
            outcome: Parsed JSON dict

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(outcome, dict):
            return False, (
                f"Root must be dict, got {type(outcome).__name__}"
            )

        is_valid, msg = ArtifactValidator.required_field(
            outcome, "task_id", str
        )
        if not is_valid:
            return False, msg

        if not ArtifactValidator.is_nonempty_string(outcome["task_id"]):
            return False, "task_id must be non-empty string"

        is_valid, msg = ArtifactValidator.required_field(
            outcome, "status", str
        )
        if not is_valid:
            return False, msg

        status = outcome["status"]
        valid_statuses = {
            "executed",
            "failed",
            "timeout",
            "unknown",
            "no_op",
            "error",
        }
        if status not in valid_statuses:
            return False, (
                f"status '{status}' not in allowed values: "
                f"{sorted(valid_statuses)}"
            )

        if "attempt" in outcome:
            if not isinstance(outcome["attempt"], int):
                return False, (
                    f"attempt: expected int, got {type(outcome['attempt']).__name__}"
                )
            if not (1 <= outcome["attempt"] <= 1000):
                return False, f"attempt {outcome['attempt']} out of range [1, 1000]"

        return True, ""


class RequestValidator(ArtifactValidator):
    """Validator for request.json artifacts."""

    @staticmethod
    def validate(request: dict) -> tuple[bool, str]:
        """Validate request.json structure.

        Args:
            request: Parsed JSON dict

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(request, dict):
            return False, (
                f"Root must be dict, got {type(request).__name__}"
            )

        is_valid, msg = ArtifactValidator.required_field(
            request, "task", dict
        )
        if not is_valid:
            return False, msg

        if not isinstance(request["task"], dict):
            return False, (
                f"task: expected dict, got {type(request['task']).__name__}"
            )

        return True, ""


class ValidationHistoryValidator(ArtifactValidator):
    """Validator for validation.json artifacts."""

    @staticmethod
    def validate(validation: dict) -> tuple[bool, str]:
        """Validate validation.json structure.

        Args:
            validation: Parsed JSON dict

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(validation, dict):
            return False, (
                f"Root must be dict, got {type(validation).__name__}"
            )

        is_valid, msg = ArtifactValidator.required_field(
            validation, "passed", bool
        )
        if not is_valid:
            return False, msg

        if "errors" in validation:
            errors = validation["errors"]
            if not isinstance(errors, list):
                return False, (
                    f"errors: expected list, got {type(errors).__name__}"
                )
            for idx, err in enumerate(errors):
                if not isinstance(err, dict):
                    return False, (
                        f"errors[{idx}]: expected dict, "
                        f"got {type(err).__name__}"
                    )
                if "code" in err:
                    code = err["code"]
                    if not ArtifactValidator.is_nonempty_string(code):
                        return False, (
                            f"errors[{idx}].code: must be non-empty string"
                        )

        if "warnings" in validation:
            warnings = validation["warnings"]
            if not isinstance(warnings, list):
                return False, (
                    f"warnings: expected list, "
                    f"got {type(warnings).__name__}"
                )

        return True, ""


class DependencyReportValidator(ArtifactValidator):
    """Validator for dependency_report.json artifacts."""

    @staticmethod
    def validate(payload: dict) -> tuple[bool, str]:
        """Validate dependency_report.json structure.

        Args:
            payload: Parsed JSON dict

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(payload, dict):
            return False, (
                f"Root must be dict, got {type(payload).__name__}"
            )

        is_valid, msg = ArtifactValidator.required_field(
            payload, "statuses", list
        )
        if not is_valid:
            return False, msg

        statuses = payload["statuses"]
        if not isinstance(statuses, list):
            return False, (
                f"statuses: expected list, got {type(statuses).__name__}"
            )

        for idx, status in enumerate(statuses):
            if not isinstance(status, dict):
                return False, (
                    f"statuses[{idx}]: expected dict, "
                    f"got {type(status).__name__}"
                )

            if "severity" in status:
                severity = status["severity"]
                if not isinstance(severity, str):
                    return False, (
                        f"statuses[{idx}].severity: expected str, "
                        f"got {type(severity).__name__}"
                    )
                valid_severities = {"info", "warning", "error"}
                if severity not in valid_severities:
                    return False, (
                        f"statuses[{idx}].severity '{severity}' not in "
                        f"allowed values: {sorted(valid_severities)}"
                    )

        return True, ""


class LintItemValidator(ArtifactValidator):
    """Validator for individual ruff lint items."""

    @staticmethod
    def validate(item: dict, item_idx: int) -> tuple[bool, str]:
        """Validate a single ruff lint issue.

        Args:
            item: Lint issue dict
            item_idx: Index in the list (for error messages)

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(item, dict):
            return False, (
                f"[{item_idx}]: expected dict, got {type(item).__name__}"
            )

        if "filename" not in item:
            return False, (
                f"[{item_idx}]: missing required field 'filename'"
            )

        filename = item["filename"]
        if not ArtifactValidator.is_nonempty_string(filename):
            return False, (
                f"[{item_idx}].filename: must be non-empty string"
            )

        if "location" not in item:
            return False, (
                f"[{item_idx}]: missing required field 'location'"
            )

        loc = item["location"]
        if not isinstance(loc, dict):
            return False, (
                f"[{item_idx}].location: expected dict, "
                f"got {type(loc).__name__}"
            )

        # ruff output uses location.row/column, not location.start.line/column
        # Validate that at least one coordinate exists and is valid
        has_row = "row" in loc
        has_column = "column" in loc

        if has_row:
            row = loc["row"]
            if not isinstance(row, int):
                return False, (
                    f"[{item_idx}].location.row: expected int, "
                    f"got {type(row).__name__}"
                )
            if not (1 <= row <= 1000000):
                return False, (
                    f"[{item_idx}].location.row {row} "
                    f"out of range [1, 1000000]"
                )

        if has_column:
            column = loc["column"]
            if not isinstance(column, int):
                return False, (
                    f"[{item_idx}].location.column: expected int, "
                    f"got {type(column).__name__}"
                )
            if not (0 <= column <= 1000000):
                return False, (
                    f"[{item_idx}].location.column {column} "
                    f"out of range [0, 1000000]"
                )

        # At least one coordinate should be present
        if not (has_row or has_column):
            return False, (
                f"[{item_idx}].location: missing required fields 'row' or 'column'"
            )

        # Validate optional but expected fields
        if "code" in item:
            code = item["code"]
            if not ArtifactValidator.is_nonempty_string(code):
                return False, (
                    f"[{item_idx}].code: must be non-empty string if present"
                )

        if "message" in item:
            message = item["message"]
            if not isinstance(message, str):
                return False, (
                    f"[{item_idx}].message: expected str, "
                    f"got {type(message).__name__}"
                )

        return True, ""
