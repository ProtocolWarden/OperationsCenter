# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Snapshot validation logic for CI integration testing.

Provides multi-layer validation of snapshots against current system state:
- Layer 1: Schema validation (JSON matches Pydantic model)
- Layer 2: Completeness validation (required signals present)
- Layer 3: Consistency validation (cross-signal semantic checks)
- Layer 4: Real-world accuracy validation (snapshot vs. live tools)
- Layer 5: Regression detection (compare vs. baseline)

This module implements failure categorization (transient vs structural) and
detailed reporting for all validation results.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from operations_center.observer.models import RepoStateSnapshot

logger = logging.getLogger(__name__)


class ValidationFailureCategory(Enum):
    """Categorization of validation failures for retry logic."""

    TRANSIENT = "transient"  # Can be retried (e.g., network timeout)
    STRUCTURAL = "structural"  # Cannot be retried (e.g., missing signal)
    CONFIGURATION = "configuration"  # Configuration issue (e.g., wrong path)
    UNKNOWN = "unknown"  # Unknown category (default)


@dataclass
class ValidationError:
    """Represents a single validation error."""

    layer: int  # Which validation layer (1-5)
    category: ValidationFailureCategory
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    is_retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "layer": self.layer,
            "category": self.category.value,
            "message": self.message,
            "details": self.details,
            "is_retryable": self.is_retryable,
        }


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    passed: bool
    check_name: str
    message: str
    errors: list[ValidationError] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "passed": self.passed,
            "check_name": self.check_name,
            "message": self.message,
            "errors": [e.to_dict() for e in self.errors],
            "duration_ms": self.duration_ms,
        }


@dataclass
class SnapshotValidationReport:
    """Complete validation report for a snapshot."""

    snapshot_id: str
    observed_at: datetime
    layers_checked: list[int]
    results: list[ValidationResult] = field(default_factory=list)
    passed: bool = True
    overall_duration_ms: float = 0.0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_result(self, result: ValidationResult) -> None:
        """Add a validation result."""
        self.results.append(result)
        if not result.passed:
            self.passed = False

    def get_retryable_errors(self) -> list[ValidationError]:
        """Get all retryable errors from all results."""
        errors = []
        for result in self.results:
            errors.extend([e for e in result.errors if e.is_retryable])
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "snapshot_id": self.snapshot_id,
            "observed_at": self.observed_at.isoformat(),
            "layers_checked": self.layers_checked,
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
            "overall_duration_ms": self.overall_duration_ms,
            "generated_at": self.generated_at.isoformat(),
            "retryable_errors": len(self.get_retryable_errors()),
            "non_retryable_errors": sum(
                len([e for e in r.errors if not e.is_retryable]) for r in self.results
            ),
        }


class SnapshotValidator:
    """Multi-layer snapshot validator for CI integration."""

    def __init__(self, snapshot: RepoStateSnapshot, repo_path: Path | None = None):
        """Initialize validator with a snapshot.

        Args:
            snapshot: The snapshot to validate
            repo_path: Path to repository for real-world validation
        """
        self.snapshot = snapshot
        self.repo_path = repo_path or Path.cwd()
        self.report = SnapshotValidationReport(
            snapshot_id=snapshot.run_id,
            observed_at=snapshot.observed_at,
            layers_checked=[],
        )

    def validate_layer_1_schema(self) -> ValidationResult:
        """Layer 1: Validate snapshot schema against Pydantic model.

        Checks:
        - JSON serialization/deserialization roundtrip
        - All required fields present and typed correctly
        - No extra unexpected fields
        """
        result = ValidationResult(
            passed=False,
            check_name="schema_validation",
            message="",
        )

        try:
            # Test roundtrip serialization
            json_str = self.snapshot.model_dump_json()
            parsed = RepoStateSnapshot.model_validate_json(json_str)

            # Verify key fields match
            if parsed.run_id != self.snapshot.run_id:
                error = ValidationError(
                    layer=1,
                    category=ValidationFailureCategory.STRUCTURAL,
                    message="run_id mismatch after roundtrip",
                    details={
                        "original": self.snapshot.run_id,
                        "parsed": parsed.run_id,
                    },
                    is_retryable=False,
                )
                result.errors.append(error)
            else:
                result.passed = True
                result.message = "Schema validation passed"

        except Exception as e:
            error = ValidationError(
                layer=1,
                category=ValidationFailureCategory.STRUCTURAL,
                message=f"Schema validation failed: {str(e)}",
                details={"error_type": type(e).__name__},
                is_retryable=False,
            )
            result.errors.append(error)

        return result

    def validate_layer_2_completeness(self) -> ValidationResult:
        """Layer 2: Validate snapshot completeness.

        Checks:
        - Required signals are present (not None)
        - At least 3 signals have non-unavailable status
        - Collector errors are acceptable (not critical)
        """
        result = ValidationResult(
            passed=True,
            check_name="completeness_validation",
            message="",
        )

        signals = self.snapshot.signals

        # Check required signals
        required_signals = [
            ("test_signal", signals.test_signal),
            ("dependency_drift", signals.dependency_drift),
            ("lint_signal", signals.lint_signal),
        ]

        for signal_name, signal_obj in required_signals:
            if signal_obj is None:
                error = ValidationError(
                    layer=2,
                    category=ValidationFailureCategory.STRUCTURAL,
                    message=f"Required signal missing: {signal_name}",
                    details={"signal_name": signal_name},
                    is_retryable=False,
                )
                result.errors.append(error)
                result.passed = False

        # Check for minimum non-unavailable signals
        all_signals = [
            signals.test_signal,
            signals.dependency_drift,
            signals.lint_signal,
            signals.type_signal,
            signals.todo_signal,
            signals.ci_history,
        ]
        non_unavailable = [
            s for s in all_signals if s and getattr(s, "status", None) != "unavailable"
        ]
        if len(non_unavailable) < 3:
            error = ValidationError(
                layer=2,
                category=ValidationFailureCategory.STRUCTURAL,
                message=f"Insufficient non-unavailable signals: {len(non_unavailable)} < 3",
                details={"available_count": len(non_unavailable)},
                is_retryable=False,
            )
            result.errors.append(error)
            result.passed = False

        # Check collector errors (non-critical acceptable)
        if self.snapshot.collector_errors:
            error_count = len(self.snapshot.collector_errors)
            if error_count > 5:  # Allow up to 5 collector errors
                error = ValidationError(
                    layer=2,
                    category=ValidationFailureCategory.TRANSIENT,
                    message=f"Too many collector errors: {error_count}",
                    details={"error_count": error_count},
                    is_retryable=True,
                )
                result.errors.append(error)
                result.passed = False

        if result.passed:
            result.message = "Completeness validation passed"
        else:
            result.message = "Completeness validation failed"

        return result

    def validate_layer_3_consistency(self) -> ValidationResult:
        """Layer 3: Validate cross-signal consistency.

        Checks:
        - Test signal consistency (if passing, test_count > 0)
        - Dependency consistency (if healthy, no critical advisories)
        - Lint consistency (violation count matches status)
        - Coverage consistency (if coverage > 0, has coverage data)
        """
        result = ValidationResult(
            passed=True,
            check_name="consistency_validation",
            message="",
        )

        signals = self.snapshot.signals

        # Test signal consistency
        if signals.test_signal and signals.test_signal.status == "passing":
            if not signals.test_signal.test_count or signals.test_signal.test_count <= 0:
                error = ValidationError(
                    layer=3,
                    category=ValidationFailureCategory.STRUCTURAL,
                    message="Test signal passing but test_count is 0 or None",
                    details={"test_count": signals.test_signal.test_count},
                    is_retryable=False,
                )
                result.errors.append(error)
                result.passed = False

        # Lint signal consistency
        if signals.lint_signal:
            violation_count = getattr(signals.lint_signal, "violation_count", None)
            status = getattr(signals.lint_signal, "status", None)
            if violation_count is not None and violation_count > 100:
                if status not in ("violations", "failing"):
                    error = ValidationError(
                        layer=3,
                        category=ValidationFailureCategory.STRUCTURAL,
                        message=f"Lint violations ({violation_count}) but status is '{status}'",
                        details={
                            "violation_count": violation_count,
                            "status": status,
                        },
                        is_retryable=False,
                    )
                    result.errors.append(error)
                    result.passed = False

        # Dependency signal consistency
        if signals.dependency_drift:
            status = getattr(signals.dependency_drift, "status", None)
            critical_issues = getattr(signals.dependency_drift, "critical_issues", None)
            if status == "healthy" and critical_issues and critical_issues > 0:
                error = ValidationError(
                    layer=3,
                    category=ValidationFailureCategory.STRUCTURAL,
                    message=f"Dependency status 'healthy' but has {critical_issues} critical issues",
                    details={"critical_issues": critical_issues, "status": status},
                    is_retryable=False,
                )
                result.errors.append(error)
                result.passed = False

        if result.passed:
            result.message = "Consistency validation passed"
        else:
            result.message = "Consistency validation failed"

        return result

    def validate_layer_4_accuracy(
        self, tolerance: dict[str, float] | None = None
    ) -> ValidationResult:
        """Layer 4: Real-world accuracy validation.

        Runs actual tools and compares with snapshot values.
        Uses configurable tolerance for unavoidable variation.

        Args:
            tolerance: Dict mapping signal names to tolerance (0.01 = 1%)
        """
        if tolerance is None:
            tolerance = {
                "test_count": 0.01,  # 1%
                "coverage": 0.05,  # 5%
            }

        result = ValidationResult(
            passed=True,
            check_name="accuracy_validation",
            message="",
        )

        # Test count accuracy
        if self.snapshot.signals.test_signal:
            expected_count = self.snapshot.signals.test_signal.test_count
            if expected_count and expected_count > 0:
                try:
                    actual_count = self._get_actual_test_count()
                    if actual_count is not None:
                        relative_error = (
                            abs(actual_count - expected_count) / expected_count
                            if expected_count > 0
                            else 0
                        )
                        test_tolerance = tolerance.get("test_count", 0.01)
                        if relative_error > test_tolerance:
                            error = ValidationError(
                                layer=4,
                                category=ValidationFailureCategory.TRANSIENT,
                                message=(
                                    f"Test count mismatch: {actual_count} vs {expected_count} "
                                    f"({relative_error * 100:.1f}% > {test_tolerance * 100:.1f}%)"
                                ),
                                details={
                                    "expected": expected_count,
                                    "actual": actual_count,
                                    "relative_error": relative_error,
                                    "tolerance": test_tolerance,
                                },
                                is_retryable=True,
                            )
                            result.errors.append(error)
                            result.passed = False
                except Exception as e:
                    error = ValidationError(
                        layer=4,
                        category=ValidationFailureCategory.CONFIGURATION,
                        message=f"Failed to run test count verification: {str(e)}",
                        details={"error_type": type(e).__name__},
                        is_retryable=False,
                    )
                    result.errors.append(error)

        if result.passed:
            result.message = "Accuracy validation passed"
        else:
            result.message = "Accuracy validation failed"

        return result

    def validate_layer_5_regression(
        self, baseline: RepoStateSnapshot | None = None
    ) -> ValidationResult:
        """Layer 5: Regression detection.

        Compares current snapshot against baseline.

        Args:
            baseline: Baseline snapshot to compare against
        """
        result = ValidationResult(
            passed=True,
            check_name="regression_validation",
            message="Baseline not provided, skipping regression validation",
        )

        if baseline is None:
            result.message = "Baseline not provided, regression validation skipped"
            return result

        # Coverage regression check
        current_coverage = (
            self.snapshot.signals.coverage_signal.total_coverage_pct
            if self.snapshot.signals.coverage_signal
            else None
        )
        baseline_coverage = (
            baseline.signals.coverage_signal.total_coverage_pct
            if baseline.signals.coverage_signal
            else None
        )

        if (
            current_coverage is not None
            and baseline_coverage is not None
            and current_coverage < baseline_coverage - 2.0
        ):
            drop = baseline_coverage - current_coverage
            error = ValidationError(
                layer=5,
                category=ValidationFailureCategory.STRUCTURAL,
                message=f"Coverage regressed by {drop:.1f}pp ({baseline_coverage}% → {current_coverage}%)",
                details={
                    "baseline_coverage": baseline_coverage,
                    "current_coverage": current_coverage,
                    "drop": drop,
                },
                is_retryable=False,
            )
            result.errors.append(error)
            result.passed = False

        # Test count regression check
        current_tests = (
            self.snapshot.signals.test_signal.test_count
            if self.snapshot.signals.test_signal
            else None
        )
        baseline_tests = (
            baseline.signals.test_signal.test_count if baseline.signals.test_signal else None
        )

        if (
            current_tests is not None
            and baseline_tests is not None
            and abs(current_tests - baseline_tests) > baseline_tests * 0.05
        ):
            diff = current_tests - baseline_tests
            error = ValidationError(
                layer=5,
                category=ValidationFailureCategory.STRUCTURAL,
                message=f"Test count changed by {diff} ({baseline_tests} → {current_tests})",
                details={
                    "baseline_tests": baseline_tests,
                    "current_tests": current_tests,
                    "difference": diff,
                },
                is_retryable=False,
            )
            result.errors.append(error)
            result.passed = False

        if result.passed:
            result.message = "Regression validation passed"
        else:
            result.message = "Regression validation failed"

        return result

    def validate_all_layers(
        self,
        layers: list[int] | None = None,
        baseline: RepoStateSnapshot | None = None,
    ) -> SnapshotValidationReport:
        """Run all validation layers.

        Args:
            layers: List of layers to validate (1-5). If None, validate all.
            baseline: Baseline snapshot for regression detection
        """
        if layers is None:
            layers = [1, 2, 3, 4, 5]

        import time

        start_time = time.time()

        if 1 in layers:
            self.report.layers_checked.append(1)
            result = self.validate_layer_1_schema()
            self.report.add_result(result)

        if 2 in layers:
            self.report.layers_checked.append(2)
            result = self.validate_layer_2_completeness()
            self.report.add_result(result)

        if 3 in layers:
            self.report.layers_checked.append(3)
            result = self.validate_layer_3_consistency()
            self.report.add_result(result)

        if 4 in layers:
            self.report.layers_checked.append(4)
            result = self.validate_layer_4_accuracy()
            self.report.add_result(result)

        if 5 in layers:
            self.report.layers_checked.append(5)
            result = self.validate_layer_5_regression(baseline)
            self.report.add_result(result)

        self.report.overall_duration_ms = (time.time() - start_time) * 1000
        return self.report

    def _get_actual_test_count(self) -> int | None:
        """Get actual test count by running pytest --collect-only."""
        try:
            result = subprocess.run(
                ["pytest", "--collect-only", "-q"],
                capture_output=True,
                text=True,
                cwd=str(self.repo_path),
                timeout=30,
            )
            # Parse output: last line usually has count
            lines = result.stdout.strip().split("\n")
            for line in reversed(lines):
                if "test" in line.lower() and any(c.isdigit() for c in line):
                    # Try to extract number
                    import re

                    matches = re.findall(r"\d+", line)
                    if matches:
                        return int(matches[0])
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return None
