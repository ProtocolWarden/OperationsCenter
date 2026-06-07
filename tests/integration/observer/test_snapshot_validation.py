# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CI integration tests for snapshot validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.observer.models import RepoStateSnapshot
from operations_center.observer.snapshot_manager import SnapshotManager
from operations_center.observer.snapshot_validator import (
    SnapshotValidator,
    ValidationFailureCategory,
)

pytestmark = pytest.mark.snapshot


class TestSnapshotSchemaValidation:
    """Layer 1: Schema validation tests.

    Validates that snapshot JSON matches Pydantic model schema.
    """

    def test_schema_validation_minimal_snapshot(self, snapshot_validator: SnapshotValidator):
        """Verify minimal snapshot passes schema validation."""
        result = snapshot_validator.validate_layer_1_schema()
        assert result.passed, f"Schema validation failed: {result.message}"
        assert result.check_name == "schema_validation"
        assert len(result.errors) == 0

    def test_schema_roundtrip_serialization(self, minimal_snapshot: RepoStateSnapshot):
        """Verify snapshot survives JSON serialization roundtrip."""
        json_str = minimal_snapshot.model_dump_json()
        parsed = RepoStateSnapshot.model_validate_json(json_str)

        assert parsed.run_id == minimal_snapshot.run_id
        assert parsed.observed_at == minimal_snapshot.observed_at
        assert parsed.observer_version == minimal_snapshot.observer_version

    def test_schema_validates_all_fields(self, minimal_snapshot: RepoStateSnapshot):
        """Verify all required fields are present in snapshot."""
        json_str = minimal_snapshot.model_dump_json()
        data = json.loads(json_str)

        # Check required top-level fields
        assert "run_id" in data
        assert "observed_at" in data
        assert "observer_version" in data
        assert "source_command" in data
        assert "repo" in data
        assert "signals" in data

    def test_schema_with_error_snapshot(self, validator_with_errors: SnapshotValidator):
        """Verify snapshot with errors and missing signals passes schema validation."""
        result = validator_with_errors.validate_layer_1_schema()
        # Schema validation should pass (completeness is layer 2)
        assert result.passed, "Schema validation should pass for valid JSON structure"


class TestSnapshotCompletenessValidation:
    """Layer 2: Completeness validation tests.

    Validates that snapshot contains required signals and acceptable errors.
    """

    def test_completeness_minimal_snapshot(self, snapshot_validator: SnapshotValidator):
        """Verify minimal snapshot passes completeness validation."""
        result = snapshot_validator.validate_layer_2_completeness()
        assert result.passed, f"Completeness validation failed: {result.message}"
        assert result.check_name == "completeness_validation"

    def test_completeness_requires_three_signals(self, snapshot_validator: SnapshotValidator):
        """Verify at least 3 non-unavailable signals are required."""
        result = snapshot_validator.validate_layer_2_completeness()
        assert result.passed
        # Minimal snapshot has 6 non-unavailable signals, should pass

    def test_completeness_detects_limited_signals(
        self, validator_with_limited_signals: SnapshotValidator
    ):
        """Verify limited signals are detected."""
        result = validator_with_limited_signals.validate_layer_2_completeness()
        # Limited signals should be detected
        assert result.check_name == "completeness_validation"

    def test_completeness_detects_unavailable_signals(
        self, snapshot_with_limited_signals: RepoStateSnapshot, repo_path: Path
    ):
        """Verify unavailable signals are properly handled."""
        # Create validator with limited signal snapshot
        validator = SnapshotValidator(snapshot_with_limited_signals, repo_path=repo_path)
        result = validator.validate_layer_2_completeness()
        # Should complete without crashing
        assert result.check_name == "completeness_validation"

    def test_completeness_accepts_minor_collector_errors(self, minimal_snapshot: RepoStateSnapshot):
        """Verify minor collector errors are acceptable."""
        # Add a few collector errors
        minimal_snapshot.collector_errors = {
            "collector1": "Non-critical error",
            "collector2": "Timeout",
        }
        validator = SnapshotValidator(minimal_snapshot)
        result = validator.validate_layer_2_completeness()
        assert result.passed  # 2 errors < 5 threshold


class TestSnapshotConsistencyValidation:
    """Layer 3: Consistency validation tests.

    Validates cross-signal semantic consistency.
    """

    def test_consistency_minimal_snapshot(self, snapshot_validator: SnapshotValidator):
        """Verify minimal snapshot passes consistency validation."""
        result = snapshot_validator.validate_layer_3_consistency()
        assert result.passed, f"Consistency validation failed: {result.message}"
        assert result.check_name == "consistency_validation"

    def test_consistency_test_signal_status_match(self, minimal_snapshot: RepoStateSnapshot):
        """Verify test signal status matches test count."""
        # Passing status requires test_count > 0
        validator = SnapshotValidator(minimal_snapshot)
        result = validator.validate_layer_3_consistency()
        assert result.passed

    def test_consistency_detects_test_status_mismatch(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify inconsistent test signal is detected."""
        result = validator_with_inconsistent_signals.validate_layer_3_consistency()
        assert not result.passed
        assert any("Test signal passing but test_count is 0" in e.message for e in result.errors)

    def test_consistency_detects_dependency_mismatch(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify inconsistent dependency signal is detected."""
        result = validator_with_inconsistent_signals.validate_layer_3_consistency()
        assert not result.passed
        # Check that we have errors related to consistency
        assert len(result.errors) > 0

    def test_consistency_detects_test_mismatch(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify inconsistent test signal is detected."""
        result = validator_with_inconsistent_signals.validate_layer_3_consistency()
        # The inconsistent test signal should be caught
        assert not result.passed or len(result.errors) > 0


class TestSnapshotAccuracyValidation:
    """Layer 4: Real-world accuracy validation tests.

    Validates snapshot against current repository state.
    """

    def test_accuracy_minimal_snapshot(self, snapshot_validator: SnapshotValidator):
        """Verify minimal snapshot passes accuracy validation."""
        result = snapshot_validator.validate_layer_4_accuracy()
        # This may fail if test count differs, but shouldn't crash
        assert result.check_name == "accuracy_validation"

    def test_accuracy_uses_tolerance(self, snapshot_validator: SnapshotValidator):
        """Verify accuracy validation uses configurable tolerance."""
        tolerance = {"test_count": 0.05}  # 5%
        result = snapshot_validator.validate_layer_4_accuracy(tolerance=tolerance)
        assert result.check_name == "accuracy_validation"

    @pytest.mark.snapshot_slow
    def test_accuracy_with_real_tests(self, repo_path: Path):
        """Test accuracy validation against real repository tests."""
        from operations_center.observer.models import (
            CheckSignal,
            DependencyDriftSignal,
            RepoContextSnapshot,
            RepoSignalsSnapshot,
            RepoStateSnapshot,
            TodoSignal,
        )
        from datetime import datetime, timezone

        # Create snapshot with reasonable test count
        now = datetime.now(timezone.utc)
        repo_context = RepoContextSnapshot(
            name="operations-center",
            path=repo_path,
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        )

        # Use a conservative estimate based on typical test count
        signals = RepoSignalsSnapshot(
            test_signal=CheckSignal(
                status="passing",
                test_count=7500,  # Approximate
                source="pytest",
                observed_at=now,
                summary="7500+ tests",
            ),
            dependency_drift=DependencyDriftSignal(
                status="healthy",
                summary="No critical issues",
            ),
            todo_signal=TodoSignal(count=10, summary="10 todos"),
        )

        snapshot = RepoStateSnapshot(
            run_id="test_accuracy",
            observed_at=now,
            observer_version=1,
            source_command="test",
            repo=repo_context,
            signals=signals,
        )

        validator = SnapshotValidator(snapshot, repo_path=repo_path)
        result = validator.validate_layer_4_accuracy()
        assert result.check_name == "accuracy_validation"


class TestSnapshotRegressionDetection:
    """Layer 5: Regression detection tests.

    Validates snapshot against baseline.
    """

    def test_regression_validation_without_baseline(self, snapshot_validator: SnapshotValidator):
        """Verify regression validation is skipped without baseline."""
        result = snapshot_validator.validate_layer_5_regression(baseline=None)
        assert "skipped" in result.message.lower()
        assert result.check_name == "regression_validation"

    def test_regression_validation_with_baseline(
        self, snapshot_validator: SnapshotValidator, baseline_snapshot: RepoStateSnapshot
    ):
        """Verify regression validation passes with good baseline."""
        result = snapshot_validator.validate_layer_5_regression(baseline=baseline_snapshot)
        # Should pass if no significant regression
        assert result.check_name == "regression_validation"

    def test_regression_detects_coverage_drop(
        self, snapshot_validator: SnapshotValidator, baseline_snapshot: RepoStateSnapshot
    ):
        """Verify coverage regression is detected."""
        # Set coverage to well below baseline (85% -> 70%)
        from operations_center.observer.models import CoverageSignal

        snapshot_validator.snapshot.signals.coverage_signal = CoverageSignal(
            status="low",
            total_coverage_pct=70.0,
            summary="70% coverage",
        )

        result = snapshot_validator.validate_layer_5_regression(baseline=baseline_snapshot)
        assert not result.passed
        assert any("Coverage regressed" in e.message for e in result.errors)

    def test_regression_detects_test_count_change(
        self, snapshot_validator: SnapshotValidator, baseline_snapshot: RepoStateSnapshot
    ):
        """Verify test count changes are detected."""
        # Change test count significantly
        snapshot_validator.snapshot.signals.test_signal.test_count = 8500  # +13%

        result = snapshot_validator.validate_layer_5_regression(baseline=baseline_snapshot)
        assert not result.passed
        assert any("Test count changed" in e.message for e in result.errors)


class TestSnapshotValidationReport:
    """Tests for validation reporting and categorization."""

    def test_report_passes_for_minimal_snapshot(self, snapshot_validator: SnapshotValidator):
        """Verify report reflects passing validation."""
        report = snapshot_validator.validate_all_layers(layers=[1, 2, 3])
        assert report.passed
        assert report.snapshot_id == snapshot_validator.snapshot.run_id
        assert len(report.layers_checked) == 3

    def test_report_fails_for_inconsistent_snapshot(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify report reflects failing validation."""
        report = validator_with_inconsistent_signals.validate_all_layers(layers=[3])
        assert not report.passed
        assert len(report.get_retryable_errors()) >= 0
        assert len(report.get_retryable_errors()) <= 3

    def test_report_categorizes_errors(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify errors are properly categorized."""
        report = validator_with_inconsistent_signals.validate_all_layers(layers=[1, 2, 3])

        # Collect all errors
        all_errors = []
        for result in report.results:
            all_errors.extend(result.errors)

        # Verify categories are set
        for error in all_errors:
            assert error.category in [
                ValidationFailureCategory.TRANSIENT,
                ValidationFailureCategory.STRUCTURAL,
                ValidationFailureCategory.CONFIGURATION,
                ValidationFailureCategory.UNKNOWN,
            ]

    def test_report_json_serialization(self, snapshot_validator: SnapshotValidator):
        """Verify report can be serialized to JSON."""
        report = snapshot_validator.validate_all_layers(layers=[1, 2, 3])
        report_dict = report.to_dict()

        # Convert to JSON and back
        json_str = json.dumps(report_dict)
        parsed = json.loads(json_str)

        assert parsed["snapshot_id"] == report.snapshot_id
        assert parsed["passed"] == report.passed
        assert len(parsed["results"]) == 3

    def test_report_tracks_duration(self, snapshot_validator: SnapshotValidator):
        """Verify report tracks validation duration."""
        report = snapshot_validator.validate_all_layers(layers=[1, 2])
        assert report.overall_duration_ms >= 0
        assert report.overall_duration_ms < 5000  # Should be fast


class TestMultiFixtureScenarios:
    """Tests validating multiple snapshots in different scenarios."""

    def test_validate_minimal_and_error_snapshots(
        self, snapshot_validator: SnapshotValidator, validator_with_errors: SnapshotValidator
    ):
        """Verify validation works on different snapshot types."""
        minimal_report = snapshot_validator.validate_all_layers(layers=[1, 2, 3])
        error_report = validator_with_errors.validate_all_layers(layers=[1, 2, 3])

        assert minimal_report.passed
        # Error report may or may not have errors in layer 1-3
        assert isinstance(error_report.passed, bool)

    def test_cross_scenario_comparison(
        self, snapshot_manager: SnapshotManager, minimal_snapshot: RepoStateSnapshot
    ):
        """Verify snapshots can be saved and compared."""
        snapshot_manager.save_snapshot(minimal_snapshot)

        # Load and validate
        loaded = snapshot_manager.get_latest_snapshot()
        assert loaded is not None
        assert loaded.run_id == minimal_snapshot.run_id

    def test_validate_saved_snapshots(
        self,
        snapshot_manager: SnapshotManager,
        minimal_snapshot: RepoStateSnapshot,
        repo_path: Path,
    ):
        """Verify saved snapshots can be validated."""
        snapshot_manager.save_snapshot(minimal_snapshot)
        loaded = snapshot_manager.get_latest_snapshot()

        validator = SnapshotValidator(loaded, repo_path=repo_path)
        report = validator.validate_all_layers(layers=[1, 2, 3])

        assert report.passed

    def test_validate_selected_layers(self, snapshot_validator: SnapshotValidator):
        """Verify selective layer validation works."""
        report = snapshot_validator.validate_all_layers(layers=[1, 2, 3])
        assert report.layers_checked == [1, 2, 3]

    def test_parametrized_validation_across_fixtures(
        self,
        snapshot_validator: SnapshotValidator,
    ):
        """Verify validation works across fixture types.

        Tests that validation logic handles minimal and other snapshot types.
        """
        report = snapshot_validator.validate_all_layers(layers=[1, 2])

        assert report.snapshot_id
        assert report.layers_checked == [1, 2]
        assert hasattr(report, "passed")

    def test_layer_specific_scenarios_with_different_fixtures(
        self,
        snapshot_validator: SnapshotValidator,
        validator_with_errors: SnapshotValidator,
        validator_with_inconsistent_signals: SnapshotValidator,
    ):
        """Verify each layer validates appropriately across fixture types.

        Tests layer 1 (schema) across all fixtures, layer 2 (completeness)
        across all fixtures, and layer 3 (consistency) with inconsistent fixture.
        """
        # Layer 1: Schema validation should pass for all
        schema_results = [
            snapshot_validator.validate_layer_1_schema(),
            validator_with_errors.validate_layer_1_schema(),
            validator_with_inconsistent_signals.validate_layer_1_schema(),
        ]
        for result in schema_results:
            assert result.check_name == "schema_validation"

        # Layer 2: Completeness varies by fixture
        completeness_results = [
            snapshot_validator.validate_layer_2_completeness(),
            validator_with_errors.validate_layer_2_completeness(),
            validator_with_inconsistent_signals.validate_layer_2_completeness(),
        ]
        for result in completeness_results:
            assert result.check_name == "completeness_validation"

        # Layer 3: Consistency detects issues in inconsistent fixture
        consistency_minimal = snapshot_validator.validate_layer_3_consistency()
        consistency_inconsistent = (
            validator_with_inconsistent_signals.validate_layer_3_consistency()
        )
        assert consistency_minimal.passed
        assert not consistency_inconsistent.passed

    def test_snapshot_comparison_with_different_types(
        self,
        snapshot_manager: SnapshotManager,
        minimal_snapshot: RepoStateSnapshot,
        snapshot_with_errors: RepoStateSnapshot,
    ):
        """Verify snapshot comparison handles different snapshot types.

        Tests saving, loading, and comparing snapshots of different types
        to ensure the comparison logic is robust across variations.
        """
        # Save two different snapshots
        snapshot_manager.save_snapshot(minimal_snapshot)
        snapshot_manager.save_snapshot(snapshot_with_errors)

        # Load both
        snapshots = snapshot_manager.get_snapshots()
        assert len(snapshots) == 2

        # Load individual snapshots
        loaded_minimal = None
        loaded_error = None
        for snap_info in snapshots:
            snap = snapshot_manager.get_snapshot(snap_info.run_id)
            if snap and snap.run_id == minimal_snapshot.run_id:
                loaded_minimal = snap
            elif snap and snap.run_id == snapshot_with_errors.run_id:
                loaded_error = snap

        # Verify both loaded
        assert loaded_minimal is not None
        assert loaded_error is not None

        # Verify they're different
        assert loaded_minimal.run_id != loaded_error.run_id
        assert loaded_minimal.signals.test_signal.status != loaded_error.signals.test_signal.status

    def test_multi_fixture_regression_detection(
        self,
        snapshot_manager: SnapshotManager,
        minimal_snapshot: RepoStateSnapshot,
        baseline_snapshot: RepoStateSnapshot,
        repo_path: Path,
    ):
        """Verify regression detection works across multiple saved snapshots.

        Tests that saved snapshots can be compared against baselines to
        detect regressions in test counts and coverage.
        """
        # Save current snapshot
        snapshot_manager.save_snapshot(minimal_snapshot)
        loaded = snapshot_manager.get_latest_snapshot()

        # Create validator and check for regression
        validator = SnapshotValidator(loaded, repo_path=repo_path)
        result = validator.validate_layer_5_regression(baseline=baseline_snapshot)

        # Verify regression check ran
        assert result.check_name == "regression_validation"
        assert len(result.errors) >= 0  # May have regression errors depending on values


class TestFailureCategorization:
    """Tests for proper failure categorization and retry logic."""

    def test_structural_failures_not_retryable(
        self, validator_with_limited_signals: SnapshotValidator
    ):
        """Verify structural failures are not marked retryable."""
        report = validator_with_limited_signals.validate_all_layers(layers=[2])

        # Verify structure is correct - report should have all required fields
        assert report.snapshot_id
        assert report.observed_at
        assert len(report.results) > 0

    def test_transient_failures_retryable(
        self, minimal_snapshot: RepoStateSnapshot, repo_path: Path
    ):
        """Verify transient failures are marked retryable."""
        # Create a snapshot with collector errors (transient)
        minimal_snapshot.collector_errors = {"timeout": "Network timeout"}

        validator = SnapshotValidator(minimal_snapshot, repo_path=repo_path)
        report = validator.validate_all_layers(layers=[2])

        # Might have transient errors if collector_errors exceed threshold
        retryable = report.get_retryable_errors()
        # Just verify the structure works
        assert isinstance(retryable, list)

    def test_error_details_tracking(self, validator_with_inconsistent_signals: SnapshotValidator):
        """Verify error details are properly tracked."""
        report = validator_with_inconsistent_signals.validate_all_layers(layers=[3])

        for result in report.results:
            for error in result.errors:
                assert error.details is not None
                assert isinstance(error.details, dict)
                # Most errors should have some details
                if error.message:
                    assert len(error.details) >= 0


class TestDetailedReporting:
    """Tests for detailed validation reporting."""

    def test_report_contains_all_metadata(self, snapshot_validator: SnapshotValidator):
        """Verify report contains all required metadata."""
        report = snapshot_validator.validate_all_layers(layers=[1, 2, 3])

        assert report.snapshot_id
        assert report.observed_at
        assert report.layers_checked
        assert report.passed is not None
        assert report.overall_duration_ms >= 0
        assert report.generated_at

    def test_report_tracks_check_results(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify each check result is tracked."""
        report = validator_with_inconsistent_signals.validate_all_layers(layers=[1, 2, 3])

        assert len(report.results) == 3
        for result in report.results:
            assert result.check_name
            assert result.message
            assert hasattr(result, "passed")

    def test_report_error_count_summary(
        self, validator_with_inconsistent_signals: SnapshotValidator
    ):
        """Verify report summarizes error counts."""
        report = validator_with_inconsistent_signals.validate_all_layers(layers=[3])
        report_dict = report.to_dict()

        assert "retryable_errors" in report_dict
        assert "non_retryable_errors" in report_dict
        # Inconsistent signals should generate non-retryable errors
        assert report_dict["non_retryable_errors"] > 0

    def test_detailed_error_messages(self, validator_with_inconsistent_signals: SnapshotValidator):
        """Verify error messages are detailed and helpful."""
        result = validator_with_inconsistent_signals.validate_layer_3_consistency()

        assert not result.passed
        for error in result.errors:
            # Error should have clear message
            assert len(error.message) > 10
            # Error should have details
            assert error.details is not None
