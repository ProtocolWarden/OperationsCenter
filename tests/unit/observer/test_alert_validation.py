# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for alert dry-run validation infrastructure."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from operations_center.observer.alert_config import (
    ALERT_ROUTES,
)
from operations_center.observer.alert_validation import (
    AlertDryRunResult,
    AlertValidationReport,
    AlertValidator,
    evaluate_alerts_dry_run,
)
from operations_center.observer.security_logging import (
    ALERT_CONDITIONS,
    AlertCondition,
    ErrorCategory,
    ErrorSeverity,
    MalformedPayloadMetrics,
    SecurityLogEntry,
)


@pytest.fixture
def sample_metrics() -> MalformedPayloadMetrics:
    """Create sample metrics with some errors."""
    metrics = MalformedPayloadMetrics()

    # Add some parse errors
    now = datetime.now(tz=UTC)
    for i in range(5):
        entry = SecurityLogEntry(
            timestamp=now - timedelta(seconds=i * 10),
            event="parse_error",
            artifact=f"artifact_{i}.json",
            error_type="parse_error",
            error_msg=f"JSON parse error {i}",
            severity=ErrorSeverity.HIGH,
            component="ExecutionArtifactCollector",
            collector="ExecutionArtifactCollector",
        )
        metrics.add_error(entry)

    return metrics


@pytest.fixture
def sample_metrics_high_volume() -> MalformedPayloadMetrics:
    """Create metrics with high volume of errors."""
    metrics = MalformedPayloadMetrics()

    now = datetime.now(tz=UTC)
    # Add 15 parse errors (exceeds threshold of 10)
    for i in range(15):
        entry = SecurityLogEntry(
            timestamp=now - timedelta(seconds=i * 5),
            event="parse_error",
            artifact=f"artifact_{i}.json",
            error_type="parse_error",
            error_msg=f"JSON parse error {i}",
            severity=ErrorSeverity.HIGH,
            component="ExecutionArtifactCollector",
            collector="ExecutionArtifactCollector",
        )
        metrics.add_error(entry)

    return metrics


class TestAlertDryRunResult:
    """Test AlertDryRunResult dataclass."""

    def test_create_result(self) -> None:
        result = AlertDryRunResult(
            condition_name="test_condition",
            would_trigger=True,
            error_count=15,
            threshold=10,
            time_window_minutes=5,
            matching_errors=[],
            routes=["test_condition"],
            channels=["operator_log"],
            severity="HIGH",
            evaluation_time=datetime.now(tz=UTC),
        )

        assert result.condition_name == "test_condition"
        assert result.would_trigger is True
        assert result.error_count == 15
        assert result.threshold == 10

    def test_to_dict(self) -> None:
        now = datetime.now(tz=UTC)
        result = AlertDryRunResult(
            condition_name="test",
            would_trigger=False,
            error_count=5,
            threshold=10,
            time_window_minutes=5,
            matching_errors=[],
            routes=[],
            channels=[],
            severity="LOW",
            evaluation_time=now,
        )

        result_dict = result.to_dict()

        assert result_dict["condition_name"] == "test"
        assert result_dict["would_trigger"] is False
        assert result_dict["error_count"] == 5


class TestAlertValidationReport:
    """Test AlertValidationReport dataclass."""

    def test_create_report(self) -> None:
        now = datetime.now(tz=UTC)
        report = AlertValidationReport(
            evaluation_time=now,
            total_conditions=4,
            triggered_count=1,
            conditions=[],
            collector_thresholds_checked=["Collector1", "Collector2"],
            configuration_issues=[],
        )

        assert report.total_conditions == 4
        assert report.triggered_count == 1

    def test_to_dict(self) -> None:
        report = AlertValidationReport(
            evaluation_time=datetime.now(tz=UTC),
            total_conditions=4,
            triggered_count=0,
            conditions=[],
            collector_thresholds_checked=[],
            configuration_issues=[],
        )

        report_dict = report.to_dict()

        assert report_dict["total_conditions"] == 4
        assert report_dict["triggered_count"] == 0


class TestAlertValidator:
    """Test AlertValidator."""

    def test_create_validator(self) -> None:
        validator = AlertValidator()
        assert validator.alert_conditions == ALERT_CONDITIONS
        assert validator.alert_routes == ALERT_ROUTES

    def test_validate_configuration_valid(self) -> None:
        validator = AlertValidator()
        is_valid, issues = validator.validate_configuration()

        assert is_valid is True
        assert len(issues) == 0

    def test_validate_configuration_missing_route(self) -> None:
        # Create validator with condition but no route
        conditions = {
            "test_condition": AlertCondition(
                name="Test",
                description="Test condition",
                category=ErrorCategory.PARSE_ERROR,
                trigger_threshold=10,
                time_window_minutes=5,
                severity=ErrorSeverity.HIGH,
                action="log",
            )
        }
        routes = {}

        validator = AlertValidator(
            alert_conditions=conditions,
            alert_routes=routes,
        )

        is_valid, issues = validator.validate_configuration()
        # Should be valid but have a warning logged

    def test_evaluate_condition_dry_run(
        self,
        sample_metrics: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        result = validator.evaluate_condition_dry_run(
            "parse_error_spike",
            sample_metrics,
        )

        assert result is not None
        assert result.condition_name == "parse_error_spike"
        assert result.would_trigger is False  # Only 5 errors, threshold is 10
        assert result.error_count == 5

    def test_evaluate_condition_would_trigger(
        self,
        sample_metrics_high_volume: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        result = validator.evaluate_condition_dry_run(
            "parse_error_spike",
            sample_metrics_high_volume,
        )

        assert result is not None
        assert result.would_trigger is True
        assert result.error_count >= 10

    def test_evaluate_condition_unknown(
        self,
        sample_metrics: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        result = validator.evaluate_condition_dry_run(
            "nonexistent_condition",
            sample_metrics,
        )

        assert result is None

    def test_evaluate_all_conditions_dry_run(
        self,
        sample_metrics_high_volume: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        report = validator.evaluate_all_conditions_dry_run(sample_metrics_high_volume)

        assert report.total_conditions == 4
        assert report.triggered_count >= 1
        assert len(report.conditions) == 4

    def test_evaluate_per_collector_thresholds(
        self,
        sample_metrics: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        results = validator.evaluate_per_collector_thresholds(sample_metrics)

        assert "ExecutionArtifactCollector" in results
        assert results["ExecutionArtifactCollector"]["error_count"] == 5
        assert results["ExecutionArtifactCollector"]["status"] == "warning"

    def test_evaluate_per_collector_thresholds_critical(self) -> None:
        metrics = MalformedPayloadMetrics()

        now = datetime.now(tz=UTC)
        # Add 15 errors (exceeds threshold of 10)
        for i in range(15):
            entry = SecurityLogEntry(
                timestamp=now - timedelta(seconds=i * 5),
                event="error",
                artifact=f"artifact_{i}.json",
                error_type="parse_error",
                error_msg=f"Error {i}",
                severity=ErrorSeverity.HIGH,
                component="ExecutionArtifactCollector",
                collector="ExecutionArtifactCollector",
            )
            metrics.add_error(entry)

        validator = AlertValidator()
        results = validator.evaluate_per_collector_thresholds(metrics)

        assert results["ExecutionArtifactCollector"]["status"] == "critical"

    def test_format_report_text(
        self,
        sample_metrics_high_volume: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        report = validator.evaluate_all_conditions_dry_run(sample_metrics_high_volume)
        text = validator.format_report_text(report)

        assert "ALERT DRY-RUN VALIDATION REPORT" in text
        assert "parse_error_spike" in text
        assert "CONDITION STATUS" in text

    def test_save_report_json(
        self,
        sample_metrics_high_volume: MalformedPayloadMetrics,
    ) -> None:
        validator = AlertValidator()
        report = validator.evaluate_all_conditions_dry_run(sample_metrics_high_volume)

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.json"
            validator.save_report_json(report, output_path)

            assert output_path.exists()
            import json

            with open(output_path) as f:
                data = json.load(f)
            assert data["total_conditions"] == 4


class TestEvaluateAlertsDryRun:
    """Test evaluate_alerts_dry_run function."""

    def test_evaluate_alerts_no_errors(self) -> None:
        metrics = MalformedPayloadMetrics()
        report = evaluate_alerts_dry_run(metrics)

        assert report.total_conditions == 4
        assert report.triggered_count == 0

    def test_evaluate_alerts_with_trigger(
        self,
        sample_metrics_high_volume: MalformedPayloadMetrics,
    ) -> None:
        report = evaluate_alerts_dry_run(sample_metrics_high_volume)

        assert report.total_conditions == 4
        assert report.triggered_count >= 1

    def test_evaluate_alerts_with_custom_lookback(
        self,
        sample_metrics: MalformedPayloadMetrics,
    ) -> None:
        report = evaluate_alerts_dry_run(
            sample_metrics,
            lookback_minutes=1,
        )

        assert report.total_conditions == 4


class TestIntegrationScenarios:
    """Integration test scenarios for alert validation."""

    def test_multiple_error_types(self) -> None:
        metrics = MalformedPayloadMetrics()
        now = datetime.now(tz=UTC)

        # Add parse errors
        for i in range(12):
            metrics.add_error(
                SecurityLogEntry(
                    timestamp=now - timedelta(seconds=i * 5),
                    event="parse_error",
                    artifact=f"artifact_{i}.json",
                    error_type="parse_error",
                    error_msg="Parse error",
                    severity=ErrorSeverity.HIGH,
                    component="ExecutionArtifactCollector",
                    collector="ExecutionArtifactCollector",
                )
            )

        # Add structure errors
        for i in range(6):
            metrics.add_error(
                SecurityLogEntry(
                    timestamp=now - timedelta(seconds=i * 5),
                    event="structure_error",
                    artifact=f"artifact_{i}.json",
                    error_type="structure_error",
                    error_msg="Structure error",
                    severity=ErrorSeverity.HIGH,
                    component="DependencyDriftCollector",
                    collector="DependencyDriftCollector",
                )
            )

        validator = AlertValidator()
        report = validator.evaluate_all_conditions_dry_run(metrics)

        # Both parse and structure errors should trigger
        assert report.triggered_count >= 2

    def test_collector_health_degradation(self) -> None:
        metrics = MalformedPayloadMetrics()
        now = datetime.now(tz=UTC)

        # Add many errors for one collector
        for i in range(12):
            metrics.add_error(
                SecurityLogEntry(
                    timestamp=now - timedelta(seconds=i * 5),
                    event="error",
                    artifact=f"artifact_{i}.json",
                    error_type="parse_error",
                    error_msg=f"Error {i}",
                    severity=ErrorSeverity.HIGH,
                    component="ExecutionArtifactCollector",
                    collector="ExecutionArtifactCollector",
                )
            )

        validator = AlertValidator()
        validator.evaluate_all_conditions_dry_run(metrics)

        # Collector health degradation should be checked
        per_collector = validator.evaluate_per_collector_thresholds(metrics)
        assert "ExecutionArtifactCollector" in per_collector
