# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for security logging and malformed payload detection."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.collectors.execution_health import ExecutionArtifactCollector
from operations_center.observer.security_logging import (
    ALERT_CONDITIONS,
    SECURITY_LOG_REQUIREMENTS,
    AlertCondition,
    ErrorCategory,
    ErrorSeverity,
    MalformedPayloadMetrics,
    SecurityLogEntry,
    should_trigger_alert,
)
from operations_center.observer.service import ObserverContext
from operations_center.observer.validation import (
    ArtifactValidator,
)

pytestmark = pytest.mark.slow


def _observer_context(report_root: Path, *, repo_name: str) -> ObserverContext:
    return ObserverContext(
        repo_path=report_root,
        repo_name=repo_name,
        base_branch="main",
        run_id="obs_test_security",
        observed_at=datetime.now(UTC),
        source_command="test",
        settings=SimpleNamespace(report_root=report_root),
        commit_limit=10,
        hotspot_window=30,
        todo_limit=20,
        logs_root=report_root / "logs",
    )


class TestSecurityLogEntry:
    """Test SecurityLogEntry structure and validation."""

    def test_security_log_entry_creation(self) -> None:
        """SecurityLogEntry can be created with required fields."""
        entry = SecurityLogEntry(
            timestamp=datetime.now(UTC),
            event="artifact_parse_error",
            artifact="/path/to/outcome.json",
            error_type="parse_error",
            error_msg="Unexpected character at line 1",
            severity=ErrorSeverity.HIGH,
            component="observer_collector",
            collector="DependencyDriftCollector",
        )
        assert entry.event == "artifact_parse_error"
        assert entry.severity == ErrorSeverity.HIGH
        assert entry.collector == "DependencyDriftCollector"

    def test_security_log_entry_to_dict(self) -> None:
        """SecurityLogEntry serializes to dict correctly."""
        now = datetime.now(UTC)
        entry = SecurityLogEntry(
            timestamp=now,
            event="artifact_structure_error",
            artifact="/path/to/outcome.json",
            error_type="structure_error",
            error_msg="status must be string, got int",
            severity=ErrorSeverity.MEDIUM,
            component="observer_collector",
            line=10,
            col=5,
        )
        data = entry.to_dict()
        assert data["timestamp"] == now.isoformat()
        assert data["line"] == 10
        assert data["col"] == 5
        assert data["severity"] == "MEDIUM"


class TestMalformedPayloadMetrics:
    """Test MalformedPayloadMetrics tracking."""

    def test_metrics_add_error(self) -> None:
        """Metrics correctly track error counts."""
        metrics = MalformedPayloadMetrics()
        assert metrics.total_errors() == 0

        entry = SecurityLogEntry(
            timestamp=datetime.now(UTC),
            event="artifact_parse_error",
            artifact="/path/outcome.json",
            error_type="parse_error",
            error_msg="Invalid JSON",
            severity=ErrorSeverity.HIGH,
            component="observer",
            collector="ExecutionArtifactCollector",
        )

        metrics.add_error(entry)
        assert metrics.total_parse_errors == 1
        assert metrics.total_errors() == 1
        assert "ExecutionArtifactCollector" in metrics.errors_by_collector

    def test_metrics_error_rate_calculation(self) -> None:
        """Metrics correctly calculate error rate per minute."""
        metrics = MalformedPayloadMetrics()
        now = datetime.now(UTC)

        for i in range(5):
            entry = SecurityLogEntry(
                timestamp=now + timedelta(seconds=i * 10),
                event="artifact_parse_error",
                artifact=f"/path/outcome{i}.json",
                error_type="parse_error",
                error_msg="Invalid JSON",
                severity=ErrorSeverity.HIGH,
                component="observer",
            )
            metrics.add_error(entry)

        # 5 errors over 40 seconds = ~7.5 errors/minute
        rate = metrics.get_error_rate_per_minute()
        assert rate > 0


class TestAlertConditions:
    """Test alert condition definitions."""

    def test_alert_condition_validation(self) -> None:
        """AlertCondition validates required parameters."""
        with pytest.raises(ValueError):
            AlertCondition(
                name="Invalid",
                description="Test",
                category=ErrorCategory.PARSE_ERROR,
                trigger_threshold=0,  # Invalid: must be >= 1
                time_window_minutes=5,
                severity=ErrorSeverity.HIGH,
                action="test",
            )

    def test_alert_condition_defined(self) -> None:
        """Alert conditions are properly defined."""
        assert "parse_error_spike" in ALERT_CONDITIONS
        assert "structure_error_surge" in ALERT_CONDITIONS
        assert "permission_denied_pattern" in ALERT_CONDITIONS

        parse_spike = ALERT_CONDITIONS["parse_error_spike"]
        assert parse_spike.category == ErrorCategory.PARSE_ERROR
        assert parse_spike.trigger_threshold >= 1
        assert parse_spike.severity in [ErrorSeverity.LOW, ErrorSeverity.MEDIUM, ErrorSeverity.HIGH]


class TestShouldTriggerAlert:
    """Test alert triggering logic."""

    def test_alert_triggered_on_threshold(self) -> None:
        """Alert triggers when error threshold exceeded."""
        metrics = MalformedPayloadMetrics()
        now = datetime.now(UTC)

        condition = ALERT_CONDITIONS["parse_error_spike"]
        # Add enough errors to trigger
        for i in range(condition.trigger_threshold + 1):
            entry = SecurityLogEntry(
                timestamp=now - timedelta(minutes=2),
                event="artifact_parse_error",
                artifact=f"/path/outcome{i}.json",
                error_type="parse_error",
                error_msg="Invalid JSON",
                severity=ErrorSeverity.HIGH,
                component="observer",
            )
            metrics.add_error(entry)

        assert should_trigger_alert(metrics, condition, lookback_minutes=5)

    def test_alert_not_triggered_below_threshold(self) -> None:
        """Alert does not trigger below threshold."""
        metrics = MalformedPayloadMetrics()
        now = datetime.now(UTC)

        condition = ALERT_CONDITIONS["parse_error_spike"]
        # Add fewer errors than threshold
        for i in range(condition.trigger_threshold - 1):
            entry = SecurityLogEntry(
                timestamp=now - timedelta(minutes=2),
                event="artifact_parse_error",
                artifact=f"/path/outcome{i}.json",
                error_type="parse_error",
                error_msg="Invalid JSON",
                severity=ErrorSeverity.HIGH,
                component="observer",
            )
            metrics.add_error(entry)

        assert not should_trigger_alert(metrics, condition, lookback_minutes=5)


class TestSecurityLogRequirements:
    """Test security log format requirements."""

    def test_security_log_requirements_defined(self) -> None:
        """Security log requirements are properly documented."""
        assert "mandatory_fields" in SECURITY_LOG_REQUIREMENTS
        assert "severity_values" in SECURITY_LOG_REQUIREMENTS
        assert "log_levels" in SECURITY_LOG_REQUIREMENTS

        mandatory = SECURITY_LOG_REQUIREMENTS["mandatory_fields"]
        assert "timestamp" in mandatory
        assert "event" in mandatory
        assert "severity" in mandatory

    def test_log_levels_for_error_types(self) -> None:
        """Log levels are appropriately assigned to error types."""
        levels = SECURITY_LOG_REQUIREMENTS["log_levels"]

        # Parse errors should be DEBUG (transient)
        assert levels["parse_error"] == "DEBUG"

        # Structure errors should be WARNING (unexpected)
        assert levels["structure_error"] == "WARNING"


class TestArtifactValidatorLogging:
    """Test ArtifactValidator security logging methods."""

    def test_log_parse_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Parse error logging includes required context."""
        with caplog.at_level(logging.DEBUG):
            try:
                json.loads("{invalid")
            except json.JSONDecodeError as e:
                ArtifactValidator.log_parse_error(
                    "/path/outcome.json",
                    e,
                    context={"collector": "TestCollector"},
                )

        # Should have logged at DEBUG level
        assert len(caplog.records) > 0
        record = caplog.records[0]
        assert record.levelname == "DEBUG"
        assert "parse_error" in record.message.lower() or "parse" in record.message.lower()

    def test_log_structure_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Structure error logging at WARNING level."""
        with caplog.at_level(logging.WARNING):
            ArtifactValidator.log_structure_error(
                "/path/outcome.json",
                "status must be string, got int",
                expected_schema="control_outcome.json",
                context={"collector": "TestCollector"},
            )

        # Should have logged at WARNING level
        assert len(caplog.records) > 0
        record = caplog.records[0]
        assert record.levelname == "WARNING"

    def test_log_io_error_permission(self, caplog: pytest.LogCaptureFixture) -> None:
        """Permission errors logged at WARNING level."""
        with caplog.at_level(logging.WARNING):
            error = PermissionError("Permission denied")
            ArtifactValidator.log_io_error(
                "/path/outcome.json",
                error,
                context={"collector": "TestCollector"},
            )

        record = caplog.records[0]
        assert record.levelname == "WARNING"


class TestDependencyDriftSecurityLogging:
    """Test DependencyDriftCollector security logging on malformed JSON."""

    def test_malformed_json_no_crash(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed JSON does not crash collector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report_root = Path(tmpdir)
            run_dir = report_root / "run123"
            run_dir.mkdir()

            # Write malformed JSON
            report_file = run_dir / "dependency_report.json"
            report_file.write_text("{invalid json")

            # Create mock context
            context = _observer_context(report_root, repo_name="test")

            collector = DependencyDriftCollector()
            with caplog.at_level(logging.DEBUG):
                signal = collector.collect(context)

            # Should return unavailable, not crash
            assert signal.status == "not_available"
            # Should have logged parse error
            assert any("parse" in r.message.lower() for r in caplog.records)

    def test_structure_error_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Structure validation errors are logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report_root = Path(tmpdir)
            run_dir = report_root / "run123"
            run_dir.mkdir()

            # Write valid JSON but invalid structure
            report_file = run_dir / "dependency_report.json"
            report_file.write_text(json.dumps({"statuses": "not_a_list"}))

            context = _observer_context(report_root, repo_name="test")

            collector = DependencyDriftCollector()
            with caplog.at_level(logging.WARNING):
                signal = collector.collect(context)

            assert signal.status == "not_available"
            assert any("structure" in r.message.lower() for r in caplog.records)


class TestExecutionHealthSecurityLogging:
    """Test ExecutionArtifactCollector security logging."""

    def test_execution_health_malformed_outcome(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed outcome.json is logged and skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report_root = Path(tmpdir)
            run_dir = report_root / "run123"
            run_dir.mkdir()

            # Malformed outcome
            outcome_file = run_dir / "control_outcome.json"
            outcome_file.write_text("{incomplete")

            # Valid request
            request_file = run_dir / "request.json"
            request_file.write_text(json.dumps({"task": {"repo_key": "test"}}))

            context = _observer_context(report_root, repo_name="test")

            collector = ExecutionArtifactCollector()
            with caplog.at_level(logging.DEBUG):
                signal = collector.collect(context)

            # Should complete without crash
            assert signal is not None
            # Should have logged parse error
            assert any("parse" in r.message.lower() for r in caplog.records)

    def test_execution_health_invalid_status_type(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid status type is logged as structure error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report_root = Path(tmpdir)
            run_dir = report_root / "run123"
            run_dir.mkdir()

            # Status is int, not string
            outcome_file = run_dir / "control_outcome.json"
            outcome_file.write_text(
                json.dumps(
                    {
                        "task_id": "abc123",
                        "status": 404,  # Should be string
                    }
                )
            )

            request_file = run_dir / "request.json"
            request_file.write_text(json.dumps({"task": {"repo_key": "test"}}))

            context = _observer_context(report_root, repo_name="test")

            collector = ExecutionArtifactCollector()
            with caplog.at_level(logging.WARNING):
                collector.collect(context)

            # Should log structure error
            assert any("structure" in r.message.lower() for r in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
