# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from operations_center.observer.metrics import (
    CollectorMetrics,
    MetricsCollector,
    MetricUnit,
    PerformanceMetric,
    SystemMetrics,
)


# --------------------------------------------------------------------------
# MetricUnit
# --------------------------------------------------------------------------
def test_metric_unit_values():
    assert MetricUnit.MILLISECONDS.value == "ms"
    assert MetricUnit.SECONDS.value == "s"
    assert MetricUnit.BYTES.value == "bytes"
    assert MetricUnit.COUNT.value == "count"
    assert MetricUnit.PERCENT.value == "%"
    assert MetricUnit.ERRORS_PER_MINUTE.value == "errors/min"
    # str-Enum behaves like a string
    assert MetricUnit.COUNT == "count"


# --------------------------------------------------------------------------
# PerformanceMetric
# --------------------------------------------------------------------------
def test_performance_metric_to_dict_full():
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    pm = PerformanceMetric(
        name="latency",
        value=12.5,
        unit=MetricUnit.MILLISECONDS,
        timestamp=ts,
        collector_name="c1",
        artifact_type="json",
        tags={"k": "v"},
    )
    d = pm.to_dict()
    assert d == {
        "name": "latency",
        "value": 12.5,
        "unit": "ms",
        "timestamp": ts.isoformat(),
        "collector": "c1",
        "artifact_type": "json",
        "tags": {"k": "v"},
    }


def test_performance_metric_defaults():
    ts = datetime.now(timezone.utc)
    pm = PerformanceMetric(name="x", value=1.0, unit=MetricUnit.COUNT, timestamp=ts)
    assert pm.collector_name is None
    assert pm.artifact_type is None
    assert pm.tags == {}
    d = pm.to_dict()
    assert d["collector"] is None
    assert d["artifact_type"] is None
    assert d["tags"] == {}


# --------------------------------------------------------------------------
# CollectorMetrics.update_from_run
# --------------------------------------------------------------------------
def test_update_from_run_success_no_errors_healthy():
    cm = CollectorMetrics(collector_name="c")
    cm.update_from_run(
        latency_ms=100.0,
        artifacts_processed=10,
        artifacts_skipped=0,
        parse_errors=0,
        structure_errors=0,
        io_errors=0,
        success=True,
    )
    assert cm.total_runs == 1
    assert cm.successful_runs == 1
    assert cm.failed_runs == 0
    assert cm.total_artifacts_processed == 10
    assert cm.min_latency_ms == 100.0
    assert cm.max_latency_ms == 100.0
    assert cm.mean_latency_ms == 100.0
    # throughput: 10 artifacts over 0.1s = 100/s
    assert cm.throughput_artifacts_per_sec == pytest.approx(100.0)
    assert cm.error_rate_percent == 0
    assert cm.health_status == "HEALTHY"
    assert cm.last_run_timestamp is not None
    assert cm.last_error_timestamp is None


def test_update_from_run_failure_and_errors():
    cm = CollectorMetrics(collector_name="c")
    cm.update_from_run(
        latency_ms=50.0,
        artifacts_processed=8,
        artifacts_skipped=2,
        parse_errors=1,
        structure_errors=1,
        io_errors=0,
        success=False,
    )
    assert cm.failed_runs == 1
    assert cm.successful_runs == 0
    assert cm.total_parse_errors == 1
    assert cm.total_structure_errors == 1
    assert cm.total_io_errors == 0
    # total_errors=2, total_attempted=10 -> 20%
    assert cm.error_rate_percent == pytest.approx(20.0)
    assert cm.health_status == "CRITICAL"
    assert cm.last_error_timestamp is not None


def test_update_from_run_min_max_latency_across_runs():
    cm = CollectorMetrics(collector_name="c")
    cm.update_from_run(200.0, 1, 0, 0, 0, 0, True)
    cm.update_from_run(50.0, 1, 0, 0, 0, 0, True)
    cm.update_from_run(120.0, 1, 0, 0, 0, 0, True)
    assert cm.min_latency_ms == 50.0
    assert cm.max_latency_ms == 200.0
    assert cm.total_runs == 3
    assert cm.mean_latency_ms == pytest.approx((200.0 + 50.0 + 120.0) / 3)


def test_update_from_run_zero_latency_skips_throughput():
    cm = CollectorMetrics(collector_name="c")
    cm.update_from_run(0.0, 5, 0, 0, 0, 0, True)
    # elapsed_seconds == 0 -> throughput stays default
    assert cm.throughput_artifacts_per_sec == 0.0
    assert cm.min_latency_ms == 0.0


def test_update_from_run_no_attempted_skips_error_rate():
    cm = CollectorMetrics(collector_name="c")
    # zero processed and skipped -> total_attempted == 0 branch skipped
    cm.update_from_run(10.0, 0, 0, 0, 0, 0, True)
    assert cm.error_rate_percent == 0.0
    assert cm.health_status == "HEALTHY"


def test_update_from_run_no_processed_skips_throughput_branch():
    cm = CollectorMetrics(collector_name="c")
    # processed == 0 -> throughput branch skipped, but skipped triggers error_rate path
    cm.update_from_run(10.0, 0, 4, 0, 0, 0, True)
    assert cm.throughput_artifacts_per_sec == 0.0
    assert cm.error_rate_percent == 0.0


# --------------------------------------------------------------------------
# CollectorMetrics._update_health_status (all bands)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rate,expected",
    [
        (0.0, "HEALTHY"),
        (4.9, "NOMINAL"),
        (5.0, "DEGRADED"),
        (19.9, "DEGRADED"),
        (20.0, "CRITICAL"),
        (75.0, "CRITICAL"),
    ],
)
def test_update_health_status_bands(rate, expected):
    cm = CollectorMetrics(collector_name="c")
    cm.total_runs = 1
    cm.error_rate_percent = rate
    cm._update_health_status()
    assert cm.health_status == expected


def test_update_health_status_unknown_when_no_runs():
    cm = CollectorMetrics(collector_name="c")
    assert cm.total_runs == 0
    cm._update_health_status()
    assert cm.health_status == "UNKNOWN"


# --------------------------------------------------------------------------
# CollectorMetrics.to_dict
# --------------------------------------------------------------------------
def test_collector_metrics_to_dict_with_none_timestamps():
    cm = CollectorMetrics(collector_name="c")
    d = cm.to_dict()
    assert d["collector_name"] == "c"
    assert d["last_run_timestamp"] is None
    assert d["last_error_timestamp"] is None
    assert d["health_status"] == "HEALTHY"


def test_collector_metrics_to_dict_with_timestamps():
    cm = CollectorMetrics(collector_name="c")
    cm.update_from_run(10.0, 1, 0, 1, 0, 0, True)
    d = cm.to_dict()
    assert isinstance(d["last_run_timestamp"], str)
    assert isinstance(d["last_error_timestamp"], str)
    assert d["total_parse_errors"] == 1


# --------------------------------------------------------------------------
# SystemMetrics.update_from_collectors
# --------------------------------------------------------------------------
def _make_collector(name, health, processed=0, skipped=0, parse=0, struct=0, io=0):
    cm = CollectorMetrics(collector_name=name)
    cm.health_status = health
    cm.total_artifacts_processed = processed
    cm.total_artifacts_skipped = skipped
    cm.total_parse_errors = parse
    cm.total_structure_errors = struct
    cm.total_io_errors = io
    return cm


def test_system_update_empty():
    sm = SystemMetrics()
    sm.update_from_collectors({})
    assert sm.total_collectors == 0
    assert sm.healthy_collectors == 0
    # total_collectors == 0 and healthy == 0 -> healthy == total -> HEALTHY branch
    assert sm.system_health_status == "HEALTHY"


def test_system_update_all_healthy():
    cols = {
        "a": _make_collector("a", "HEALTHY", processed=10),
        "b": _make_collector("b", "HEALTHY", processed=5),
    }
    sm = SystemMetrics()
    sm.update_from_collectors(cols)
    assert sm.total_collectors == 2
    assert sm.healthy_collectors == 2
    assert sm.system_health_status == "HEALTHY"
    assert sm.overall_error_rate_percent == 0.0


def test_system_update_critical_takes_precedence():
    cols = {
        "a": _make_collector("a", "CRITICAL", processed=10, parse=5),
        "b": _make_collector("b", "DEGRADED", processed=10, io=2),
    }
    sm = SystemMetrics()
    sm.update_from_collectors(cols)
    assert sm.critical_collectors == 1
    assert sm.degraded_collectors == 1
    assert sm.system_health_status == "CRITICAL"
    assert sm.total_validation_failures == 7
    # 7 errors / 20 processed -> 35%
    assert sm.overall_error_rate_percent == pytest.approx(35.0)


def test_system_update_degraded():
    cols = {"a": _make_collector("a", "DEGRADED", processed=10, struct=1)}
    sm = SystemMetrics()
    sm.update_from_collectors(cols)
    assert sm.system_health_status == "DEGRADED"


def test_system_update_nominal_when_mixed_unknown():
    # No critical, no degraded, not all healthy -> NOMINAL
    cols = {
        "a": _make_collector("a", "HEALTHY", processed=10),
        "b": _make_collector("b", "NOMINAL", processed=10),
    }
    sm = SystemMetrics()
    sm.update_from_collectors(cols)
    assert sm.critical_collectors == 0
    assert sm.degraded_collectors == 0
    assert sm.healthy_collectors == 1
    assert sm.system_health_status == "NOMINAL"


def test_system_update_zero_processed_skips_error_rate():
    cols = {"a": _make_collector("a", "HEALTHY", parse=5)}
    sm = SystemMetrics()
    sm.update_from_collectors(cols)
    # total_processed == 0 -> branch skipped, stays default 0.0
    assert sm.overall_error_rate_percent == 0.0
    assert sm.total_validation_failures == 5


# --------------------------------------------------------------------------
# SystemMetrics.to_dict
# --------------------------------------------------------------------------
def test_system_metrics_to_dict():
    cols = {"a": _make_collector("a", "HEALTHY", processed=2)}
    sm = SystemMetrics()
    sm.update_from_collectors(cols)
    d = sm.to_dict()
    assert isinstance(d["timestamp"], str)
    assert d["total_collectors"] == 1
    assert "a" in d["collector_metrics"]
    assert d["collector_metrics"]["a"]["collector_name"] == "a"
    assert d["time_window_minutes"] == 5


def test_system_metrics_default_timestamp_is_set():
    sm = SystemMetrics()
    assert isinstance(sm.timestamp, datetime)


# --------------------------------------------------------------------------
# MetricsCollector
# --------------------------------------------------------------------------
def test_collector_init():
    mc = MetricsCollector()
    assert mc.collector_metrics == {}
    assert isinstance(mc.system_metrics, SystemMetrics)
    assert mc.performance_metrics == []
    assert isinstance(mc._start_time, datetime)


def test_record_collector_run_creates_and_updates():
    mc = MetricsCollector()
    mc.record_collector_run(
        collector_name="c1",
        latency_ms=10.0,
        artifacts_processed=5,
        artifacts_skipped=0,
        parse_errors=0,
        structure_errors=0,
        io_errors=0,
        success=True,
    )
    assert "c1" in mc.collector_metrics
    assert mc.collector_metrics["c1"].total_runs == 1
    assert mc.system_metrics.total_collectors == 1

    # Second run on same collector reuses object
    obj = mc.collector_metrics["c1"]
    mc.record_collector_run("c1", 20.0, 5, 0, 0, 0, 0, True)
    assert mc.collector_metrics["c1"] is obj
    assert obj.total_runs == 2


def test_record_performance_metric_defaults_and_appends():
    mc = MetricsCollector()
    mc.record_performance_metric("m", 1.0, MetricUnit.COUNT)
    assert len(mc.performance_metrics) == 1
    pm = mc.performance_metrics[0]
    assert pm.tags == {}
    assert pm.collector_name is None
    assert isinstance(pm.timestamp, datetime)


def test_record_performance_metric_with_tags():
    mc = MetricsCollector()
    mc.record_performance_metric(
        "m", 2.0, MetricUnit.SECONDS, collector_name="c", artifact_type="t", tags={"a": "b"}
    )
    pm = mc.performance_metrics[0]
    assert pm.tags == {"a": "b"}
    assert pm.collector_name == "c"
    assert pm.artifact_type == "t"


def test_record_performance_metric_trims_to_1000():
    mc = MetricsCollector()
    for i in range(1005):
        mc.record_performance_metric(f"m{i}", float(i), MetricUnit.COUNT)
    assert len(mc.performance_metrics) == 1000
    # oldest dropped: first remaining should be m5
    assert mc.performance_metrics[0].name == "m5"
    assert mc.performance_metrics[-1].name == "m1004"


def test_get_system_metrics_refreshes():
    mc = MetricsCollector()
    mc.record_collector_run("c1", 10.0, 1, 0, 0, 0, 0, True)
    sm = mc.get_system_metrics()
    assert sm is mc.system_metrics
    assert sm.total_collectors == 1


def test_get_collector_metrics_present_and_absent():
    mc = MetricsCollector()
    assert mc.get_collector_metrics("nope") is None
    mc.record_collector_run("c1", 10.0, 1, 0, 0, 0, 0, True)
    got = mc.get_collector_metrics("c1")
    assert got is not None
    assert got.collector_name == "c1"


def test_get_all_collector_metrics_returns_copy():
    mc = MetricsCollector()
    mc.record_collector_run("c1", 10.0, 1, 0, 0, 0, 0, True)
    all_metrics = mc.get_all_collector_metrics()
    assert "c1" in all_metrics
    # mutating the copy doesn't affect internal dict
    all_metrics["c2"] = CollectorMetrics(collector_name="c2")
    assert "c2" not in mc.collector_metrics


def test_get_recent_performance_metrics_empty():
    mc = MetricsCollector()
    assert mc.get_recent_performance_metrics() == []


def test_get_recent_performance_metrics_limit():
    mc = MetricsCollector()
    for i in range(10):
        mc.record_performance_metric(f"m{i}", float(i), MetricUnit.COUNT)
    recent = mc.get_recent_performance_metrics(limit=3)
    assert len(recent) == 3
    assert [m.name for m in recent] == ["m7", "m8", "m9"]


def test_export_snapshot():
    mc = MetricsCollector()
    mc.record_collector_run("c1", 10.0, 5, 0, 1, 0, 0, True)
    mc.record_performance_metric("lat", 10.0, MetricUnit.MILLISECONDS)
    snap = mc.export_snapshot()
    assert set(snap.keys()) == {
        "snapshot_time",
        "system_metrics",
        "collector_metrics",
        "performance_metrics",
    }
    assert isinstance(snap["snapshot_time"], str)
    assert "c1" in snap["collector_metrics"]
    assert snap["system_metrics"]["total_collectors"] == 1
    assert len(snap["performance_metrics"]) == 1
    assert snap["performance_metrics"][0]["name"] == "lat"
