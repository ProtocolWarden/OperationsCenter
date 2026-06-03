# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations_center.observer.health_checks import (
    HealthChecker,
    HealthCheckResult,
    HealthStatus,
    SystemHealthReport,
)
from operations_center.observer.security_logging import (
    AlertCondition,
    ErrorCategory,
    ErrorSeverity,
    MalformedPayloadMetrics,
    SecurityLogEntry,
)


def _collector_metrics(
    *,
    health_status="HEALTHY",
    total_runs=10,
    successful_runs=10,
    failed_runs=0,
    error_rate_percent=0.0,
    mean_latency_ms=100.0,
    throughput_artifacts_per_sec=5.0,
):
    return SimpleNamespace(
        health_status=health_status,
        total_runs=total_runs,
        successful_runs=successful_runs,
        failed_runs=failed_runs,
        error_rate_percent=error_rate_percent,
        mean_latency_ms=mean_latency_ms,
        throughput_artifacts_per_sec=throughput_artifacts_per_sec,
    )


def _system_metrics(
    *,
    total_collectors=1,
    overall_error_rate_percent=0.0,
    total_validation_failures=0,
):
    return SimpleNamespace(
        total_collectors=total_collectors,
        overall_error_rate_percent=overall_error_rate_percent,
        total_validation_failures=total_validation_failures,
    )


def _make_checker(
    *,
    collector_metrics=None,
    all_collector_metrics=None,
    system_metrics=None,
    malformed_metrics=None,
    alert_conditions=None,
):
    mc = MagicMock()
    mc.get_collector_metrics.return_value = collector_metrics
    mc.get_all_collector_metrics.return_value = all_collector_metrics or {}
    mc.get_system_metrics.return_value = system_metrics or _system_metrics()
    malformed = malformed_metrics or MalformedPayloadMetrics()
    return HealthChecker(mc, malformed, alert_conditions or {})


def _security_entry(*, error_type=ErrorCategory.PARSE_ERROR.value, minutes_ago=0):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return SecurityLogEntry(
        timestamp=ts,
        event="malformed_payload",
        artifact="artifact.json",
        error_type=error_type,
        error_msg="bad",
        severity=ErrorSeverity.HIGH,
        component="collector",
        collector="c1",
        expected_schema="schema",
    )


def _alert_condition(
    *,
    category=ErrorCategory.PARSE_ERROR,
    threshold=2,
    window=5,
):
    return AlertCondition(
        name="Test",
        description="desc",
        category=category,
        trigger_threshold=threshold,
        time_window_minutes=window,
        severity=ErrorSeverity.HIGH,
        action="log",
    )


# ---------------------------------------------------------------------------
# Dataclass / enum serialization
# ---------------------------------------------------------------------------


def test_health_status_enum_values():
    assert HealthStatus.HEALTHY.value == "HEALTHY"
    assert HealthStatus("CRITICAL") == HealthStatus.CRITICAL


def test_health_check_result_to_dict_with_defaults():
    result = HealthCheckResult(
        check_name="x",
        status=HealthStatus.HEALTHY,
        message="ok",
    )
    d = result.to_dict()
    assert d["check_name"] == "x"
    assert d["status"] == "HEALTHY"
    assert d["message"] == "ok"
    assert d["details"] == {}
    assert d["remediation"] is None
    # default timestamp is tz-aware ISO string
    assert d["timestamp"].endswith("+00:00")


def test_health_check_result_to_dict_with_values():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = HealthCheckResult(
        check_name="x",
        status=HealthStatus.DEGRADED,
        message="m",
        timestamp=ts,
        details={"a": 1},
        remediation="fix it",
    )
    d = result.to_dict()
    assert d["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert d["details"] == {"a": 1}
    assert d["remediation"] == "fix it"


def test_system_health_report_to_dict():
    check = HealthCheckResult("c", HealthStatus.CRITICAL, "boom")
    report = SystemHealthReport(
        overall_status=HealthStatus.CRITICAL,
        checks=[check],
        summary="s",
        critical_issues=["boom"],
        warnings=["w"],
    )
    d = report.to_dict()
    assert d["overall_status"] == "CRITICAL"
    assert d["checks"][0]["message"] == "boom"
    assert d["summary"] == "s"
    assert d["critical_issues"] == ["boom"]
    assert d["warnings"] == ["w"]
    assert "timestamp" in d


def test_system_health_report_defaults():
    report = SystemHealthReport()
    assert report.overall_status == HealthStatus.UNKNOWN
    assert report.checks == []
    assert report.summary == ""
    assert report.critical_issues == []
    assert report.warnings == []


# ---------------------------------------------------------------------------
# check_collector_health
# ---------------------------------------------------------------------------


def test_check_collector_health_no_metrics():
    checker = _make_checker(collector_metrics=None)
    result = checker.check_collector_health("foo")
    assert result.status == HealthStatus.UNKNOWN
    assert result.check_name == "collector_health_foo"
    assert "No metrics available" in result.message
    assert result.remediation is not None


def test_check_collector_health_healthy_no_remediation():
    checker = _make_checker(
        collector_metrics=_collector_metrics(health_status="HEALTHY", error_rate_percent=0.0)
    )
    result = checker.check_collector_health("foo")
    assert result.status == HealthStatus.HEALTHY
    assert result.remediation is None
    assert "is healthy" in result.message
    # low error rate -> no appended error-rate suffix
    assert "error rate:" not in result.message
    assert result.details["total_runs"] == 10


def test_check_collector_health_high_error_rate_message_suffix():
    checker = _make_checker(
        collector_metrics=_collector_metrics(health_status="NOMINAL", error_rate_percent=25.0)
    )
    result = checker.check_collector_health("foo")
    # >20 triggers suffix even though NOMINAL gives no remediation
    assert "error rate: 25.0%" in result.message
    assert result.remediation is None


def test_check_collector_health_critical_remediation():
    checker = _make_checker(
        collector_metrics=_collector_metrics(health_status="CRITICAL", error_rate_percent=50.0)
    )
    result = checker.check_collector_health("foo")
    assert result.status == HealthStatus.CRITICAL
    assert result.remediation is not None
    assert "High error rate" in result.remediation


def test_check_collector_health_degraded_remediation():
    checker = _make_checker(
        collector_metrics=_collector_metrics(health_status="DEGRADED", error_rate_percent=10.0)
    )
    result = checker.check_collector_health("foo")
    assert result.status == HealthStatus.DEGRADED
    assert "Elevated error rate" in result.remediation


# ---------------------------------------------------------------------------
# check_error_rate
# ---------------------------------------------------------------------------


def test_check_error_rate_no_collectors():
    checker = _make_checker(system_metrics=_system_metrics(total_collectors=0))
    result = checker.check_error_rate()
    assert result.status == HealthStatus.UNKNOWN
    assert "No collectors configured" in result.message
    assert result.remediation is not None


def test_check_error_rate_zero_healthy():
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=2, overall_error_rate_percent=0.0)
    )
    result = checker.check_error_rate()
    assert result.status == HealthStatus.HEALTHY
    assert result.message == "Error rate is 0%"
    assert result.remediation is None


def test_check_error_rate_nominal():
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=2, overall_error_rate_percent=0.5)
    )
    result = checker.check_error_rate()
    assert result.status == HealthStatus.NOMINAL
    assert "< 1%" in result.message
    assert result.remediation is None


def test_check_error_rate_degraded():
    checker = _make_checker(
        system_metrics=_system_metrics(
            total_collectors=2, overall_error_rate_percent=3.0, total_validation_failures=4
        )
    )
    result = checker.check_error_rate()
    assert result.status == HealthStatus.DEGRADED
    assert result.remediation is not None
    assert result.details["total_validation_failures"] == 4


def test_check_error_rate_critical():
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=2, overall_error_rate_percent=12.0)
    )
    result = checker.check_error_rate()
    assert result.status == HealthStatus.CRITICAL
    assert "Critical error rate" in result.remediation


def test_check_error_rate_boundary_five_is_critical():
    # rate == 5 is not < 5, so falls to CRITICAL branch
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=1, overall_error_rate_percent=5.0)
    )
    result = checker.check_error_rate()
    assert result.status == HealthStatus.CRITICAL


# ---------------------------------------------------------------------------
# check_latency
# ---------------------------------------------------------------------------


def test_check_latency_no_collectors():
    checker = _make_checker(system_metrics=_system_metrics(total_collectors=0))
    result = checker.check_latency()
    assert result.status == HealthStatus.UNKNOWN
    assert result.remediation is None


def test_check_latency_all_within_threshold():
    metrics = {
        "a": _collector_metrics(mean_latency_ms=200.0),
        "b": _collector_metrics(mean_latency_ms=500.0),
    }
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=2),
        all_collector_metrics=metrics,
    )
    result = checker.check_latency()
    assert result.status == HealthStatus.HEALTHY
    assert result.details["max_mean_latency_ms"] == 500.0


def test_check_latency_some_slow_degraded():
    metrics = {
        "fast": _collector_metrics(mean_latency_ms=100.0),
        "slow": _collector_metrics(mean_latency_ms=2000.0),
    }
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=2),
        all_collector_metrics=metrics,
    )
    result = checker.check_latency()
    assert result.status == HealthStatus.DEGRADED
    names = [c["name"] for c in result.details["slow_collectors"]]
    assert names == ["slow"]
    assert result.remediation is not None


def test_check_latency_all_slow_critical():
    metrics = {
        "a": _collector_metrics(mean_latency_ms=1500.0),
        "b": _collector_metrics(mean_latency_ms=3000.0),
    }
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=2),
        all_collector_metrics=metrics,
    )
    result = checker.check_latency()
    assert result.status == HealthStatus.CRITICAL
    assert len(result.details["slow_collectors"]) == 2


# ---------------------------------------------------------------------------
# check_alert_conditions
# ---------------------------------------------------------------------------


def test_check_alert_conditions_none_triggered_empty():
    checker = _make_checker(alert_conditions={})
    result = checker.check_alert_conditions()
    assert result.status == HealthStatus.HEALTHY
    assert "No alert conditions" in result.message


def test_check_alert_conditions_below_threshold():
    malformed = MalformedPayloadMetrics(recent_errors=[_security_entry(minutes_ago=1)])
    checker = _make_checker(
        malformed_metrics=malformed,
        alert_conditions={"spike": _alert_condition(threshold=5)},
    )
    result = checker.check_alert_conditions()
    assert result.status == HealthStatus.HEALTHY


def test_check_alert_conditions_outside_window_not_counted():
    # error 100 min ago, window 5 min -> excluded
    malformed = MalformedPayloadMetrics(recent_errors=[_security_entry(minutes_ago=100)])
    checker = _make_checker(
        malformed_metrics=malformed,
        alert_conditions={"spike": _alert_condition(threshold=1, window=5)},
    )
    result = checker.check_alert_conditions()
    assert result.status == HealthStatus.HEALTHY


def test_check_alert_conditions_wrong_category_not_counted():
    malformed = MalformedPayloadMetrics(
        recent_errors=[_security_entry(error_type=ErrorCategory.IO_ERROR.value, minutes_ago=1)]
    )
    checker = _make_checker(
        malformed_metrics=malformed,
        alert_conditions={
            "spike": _alert_condition(category=ErrorCategory.PARSE_ERROR, threshold=1)
        },
    )
    result = checker.check_alert_conditions()
    assert result.status == HealthStatus.HEALTHY


def test_check_alert_conditions_triggered():
    malformed = MalformedPayloadMetrics(
        recent_errors=[
            _security_entry(minutes_ago=1),
            _security_entry(minutes_ago=2),
        ]
    )
    checker = _make_checker(
        malformed_metrics=malformed,
        alert_conditions={"spike": _alert_condition(threshold=2, window=5)},
    )
    result = checker.check_alert_conditions()
    assert result.status == HealthStatus.CRITICAL
    assert result.details["triggered_alerts"][0]["condition"] == "spike"
    assert result.details["triggered_alerts"][0]["actual"] == 2
    assert result.remediation is not None


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


def test_run_all_checks_critical_overall():
    metrics = {"slow": _collector_metrics(mean_latency_ms=2000.0, health_status="HEALTHY")}
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=1, overall_error_rate_percent=20.0),
        all_collector_metrics=metrics,
        collector_metrics=_collector_metrics(health_status="HEALTHY"),
    )
    report = checker.run_all_checks()
    assert report.overall_status == HealthStatus.CRITICAL
    assert len(report.critical_issues) >= 1
    assert "CRITICAL" in report.summary
    # error_rate, latency, alert_conditions + 1 collector = 4 checks
    assert len(report.checks) == 4


def test_run_all_checks_degraded_overall():
    metrics = {"c": _collector_metrics(mean_latency_ms=100.0, health_status="DEGRADED")}
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=1, overall_error_rate_percent=3.0),
        all_collector_metrics=metrics,
        collector_metrics=_collector_metrics(health_status="DEGRADED", error_rate_percent=10.0),
    )
    report = checker.run_all_checks()
    assert report.overall_status == HealthStatus.DEGRADED
    assert len(report.warnings) >= 1


def test_run_all_checks_nominal_overall():
    metrics = {"c": _collector_metrics(mean_latency_ms=100.0, health_status="NOMINAL")}
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=1, overall_error_rate_percent=0.5),
        all_collector_metrics=metrics,
        collector_metrics=_collector_metrics(health_status="NOMINAL", error_rate_percent=0.5),
    )
    report = checker.run_all_checks()
    assert report.overall_status == HealthStatus.NOMINAL


def test_run_all_checks_healthy_overall():
    metrics = {"c": _collector_metrics(mean_latency_ms=100.0, health_status="HEALTHY")}
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=1, overall_error_rate_percent=0.0),
        all_collector_metrics=metrics,
        collector_metrics=_collector_metrics(health_status="HEALTHY", error_rate_percent=0.0),
    )
    report = checker.run_all_checks()
    assert report.overall_status == HealthStatus.HEALTHY
    assert report.critical_issues == []
    assert report.warnings == []


def test_run_all_checks_unknown_overall():
    # zero collectors -> error_rate UNKNOWN, latency UNKNOWN, alerts HEALTHY...
    # alert_conditions returns HEALTHY by default, so to get UNKNOWN overall we
    # need no HEALTHY either; supply no alert conditions still yields HEALTHY.
    # Instead force all checks to UNKNOWN by patching: zero collectors gives
    # error_rate UNKNOWN + latency UNKNOWN + alerts HEALTHY -> overall HEALTHY.
    # So verify the HEALTHY-wins path here as the realistic zero-collector case.
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=0),
        all_collector_metrics={},
    )
    report = checker.run_all_checks()
    # alert_conditions HEALTHY beats the two UNKNOWNs
    assert report.overall_status == HealthStatus.HEALTHY
    assert len(report.checks) == 3


def test_run_all_checks_pure_unknown(monkeypatch):
    # Force every check to UNKNOWN to exercise the final else branch.
    checker = _make_checker(
        system_metrics=_system_metrics(total_collectors=0),
        all_collector_metrics={},
    )
    unknown = HealthCheckResult("x", HealthStatus.UNKNOWN, "u")
    monkeypatch.setattr(checker, "check_error_rate", lambda: unknown)
    monkeypatch.setattr(checker, "check_latency", lambda: unknown)
    monkeypatch.setattr(checker, "check_alert_conditions", lambda: unknown)
    report = checker.run_all_checks()
    assert report.overall_status == HealthStatus.UNKNOWN


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
