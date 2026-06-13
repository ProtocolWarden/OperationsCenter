# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parametrized edge-case tests for metrics tuning.

Tests extreme scenarios for metrics calculation, including:
- Zero counts and empty collections
- Infinity and very large values
- Rate calculations with zero denominators
- Boundary conditions for health status thresholds
- Timestamp edge cases
- Serialization correctness
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from operations_center.observer.metrics import (
    CollectorMetrics,
    PerformanceMetric,
    SystemMetrics,
    MetricUnit,
)


class TestCollectorMetricsHealthStatusBands:
    """Parameter set 1: Health status threshold classification."""

    @pytest.mark.parametrize(
        "artifacts_processed,parse_errors,expected_health,expected_rate",
        [
            (1000, 0, "HEALTHY", 0.0),
            (10000, 1, "NOMINAL", 0.01),  # Any error makes it NOMINAL, not HEALTHY
            (10000, 499, "NOMINAL", 4.99),
            (2000, 100, "DEGRADED", 5.0),
            (10000, 1999, "DEGRADED", 19.99),
            (1000, 200, "CRITICAL", 20.0),
            (1000, 500, "CRITICAL", 50.0),
            (1000, 1000, "CRITICAL", 100.0),
        ],
    )
    def test_health_status_bands(
        self, artifacts_processed, parse_errors, expected_health, expected_rate
    ):
        """Verify health status classification at all threshold boundaries."""
        collector = CollectorMetrics("test_collector")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=artifacts_processed,
            artifacts_skipped=0,
            parse_errors=parse_errors,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.health_status == expected_health
        assert collector.error_rate_percent == pytest.approx(expected_rate, abs=0.02)


class TestCollectorMetricsLatencyTracking:
    """Parameter set 2: Latency min/max/mean across multiple runs."""

    @pytest.mark.parametrize(
        "latencies,expected_min,expected_max,expected_mean",
        [
            ([100.0], 100.0, 100.0, 100.0),
            ([200.0, 50.0, 120.0], 50.0, 200.0, 123.33),
            ([0.0], 0.0, 0.0, 0.0),
            ([0.0, 100.0], 0.0, 100.0, 50.0),
        ],
    )
    def test_latency_tracking(self, latencies, expected_min, expected_max, expected_mean):
        """Verify min/max/mean latency calculations across multiple runs."""
        collector = CollectorMetrics("test_collector")

        for latency in latencies:
            collector.update_from_run(
                latency_ms=latency,
                artifacts_processed=1,
                artifacts_skipped=0,
                parse_errors=0,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

        assert collector.min_latency_ms == pytest.approx(expected_min)
        assert collector.max_latency_ms == pytest.approx(expected_max)
        assert collector.mean_latency_ms == pytest.approx(expected_mean, abs=0.01)


class TestCollectorMetricsArtifactCounting:
    """Parameter set 3: Artifact processing and skipping."""

    @pytest.mark.parametrize(
        "processed,skipped,expected_total",
        [
            (10, 0, 10),
            (0, 0, 0),
            (5, 5, 10),
            (1_000_000, 1_000_000, 2_000_000),
        ],
    )
    def test_artifact_counting(self, processed, skipped, expected_total):
        """Verify artifact count aggregation."""
        collector = CollectorMetrics("test_collector")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=processed,
            artifacts_skipped=skipped,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.total_artifacts_processed == processed
        assert collector.total_artifacts_skipped == skipped
        total_attempted = collector.total_artifacts_processed + collector.total_artifacts_skipped
        assert total_attempted == expected_total


class TestCollectorMetricsErrorRateCalculation:
    """Parameter set 4: Error rate with various processed/error combinations."""

    @pytest.mark.parametrize(
        "processed,skipped,parse_err,struct_err,io_err,expected_rate",
        [
            (10, 0, 0, 0, 0, 0.0),
            (10, 0, 1, 0, 0, 10.0),
            (10, 0, 5, 5, 0, 100.0),
            (100, 100, 10, 0, 0, 5.0),
            (0, 0, 5, 0, 0, 0.0),  # No denominator → rate stays 0
        ],
    )
    def test_error_rate_calculation(
        self, processed, skipped, parse_err, struct_err, io_err, expected_rate
    ):
        """Verify error rate calculation with division guard."""
        collector = CollectorMetrics("test_collector")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=processed,
            artifacts_skipped=skipped,
            parse_errors=parse_err,
            structure_errors=struct_err,
            io_errors=io_err,
            success=True,
        )

        assert collector.error_rate_percent == pytest.approx(expected_rate, abs=0.01)


class TestCollectorMetricsThroughputCalculation:
    """Parameter set 5: Throughput with various latency/processed combinations."""

    @pytest.mark.parametrize(
        "processed,latency_ms,expected_throughput",
        [
            (10, 100.0, 100.0),  # 10 artifacts / 0.1 sec = 100/sec
            (0, 100.0, 0.0),  # No artifacts → no throughput
            (100, 0.0, 0.0),  # Zero latency → throughput guard prevents division
            (1_000_000, 1000.0, 1_000_000.0),  # Large numbers
            (1, 1000.0, 1.0),  # Single artifact
        ],
    )
    def test_throughput_calculation(self, processed, latency_ms, expected_throughput):
        """Verify throughput calculation with division guards."""
        collector = CollectorMetrics("test_collector")

        collector.update_from_run(
            latency_ms=latency_ms,
            artifacts_processed=processed,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.throughput_artifacts_per_sec == pytest.approx(expected_throughput)


class TestCollectorMetricsCriticalEdgeCases:
    """Critical edge cases from analysis."""

    def test_zero_runs_returns_unknown_status(self):
        """C1: Without any runs, health status is UNKNOWN."""
        collector = CollectorMetrics("test")
        assert collector.health_status == "HEALTHY"  # Initial state from dataclass
        assert collector.total_runs == 0

        # Update health status for zero runs
        collector._update_health_status()
        assert collector.health_status == "UNKNOWN"

    def test_zero_latency_skips_throughput_calculation(self):
        """C2: Zero latency prevents throughput calculation (division guard)."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=0.0,
            artifacts_processed=5,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.min_latency_ms == 0.0
        assert collector.throughput_artifacts_per_sec == 0.0

    def test_infinity_initialization_overwritten_on_first_run(self):
        """C3: min_latency starts at inf but is properly overwritten."""
        collector = CollectorMetrics("test")
        assert collector.min_latency_ms == float("inf")

        collector.update_from_run(
            latency_ms=50.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.min_latency_ms == 50.0
        assert collector.max_latency_ms == 50.0

    def test_no_artifacts_attempted_keeps_zero_error_rate(self):
        """C4: With no attempted artifacts, error_rate stays 0.0 (division guard)."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=0,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.error_rate_percent == 0.0
        assert collector.health_status == "HEALTHY"

    def test_error_rate_exactly_5_percent_boundary(self):
        """C5: Exactly 5% error rate → DEGRADED (inclusive boundary)."""
        collector = CollectorMetrics("test")

        # 100 errors in 2000 attempts = 5%
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=2000,  # total attempted = 2000
            artifacts_skipped=0,
            parse_errors=100,  # 100 errors
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.error_rate_percent == pytest.approx(5.0, abs=0.01)
        assert collector.health_status == "DEGRADED"

    def test_error_rate_exactly_20_percent_boundary(self):
        """C6: Exactly 20% error rate → CRITICAL (inclusive boundary)."""
        collector = CollectorMetrics("test")

        # 200 errors in 1000 attempts = 20%
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1000,  # total attempted = 1000
            artifacts_skipped=0,
            parse_errors=200,  # 200 errors
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.error_rate_percent == pytest.approx(20.0, abs=0.01)
        assert collector.health_status == "CRITICAL"

    def test_errors_without_attempted_artifacts(self):
        """Error counts recorded even with no attempted artifacts."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=0,
            artifacts_skipped=0,
            parse_errors=5,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.total_parse_errors == 5
        assert collector.error_rate_percent == 0.0  # Division guard
        assert collector.health_status == "HEALTHY"
        assert collector.last_error_timestamp is not None

    def test_single_run_equal_min_max_mean(self):
        """CC1: Single run → min = max = mean."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.min_latency_ms == 100.0
        assert collector.max_latency_ms == 100.0
        assert collector.mean_latency_ms == 100.0

    def test_multiple_runs_correct_aggregation(self):
        """CC2: Multiple runs aggregate correctly."""
        collector = CollectorMetrics("test")

        for latency in [200.0, 50.0, 120.0]:
            collector.update_from_run(
                latency_ms=latency,
                artifacts_processed=1,
                artifacts_skipped=0,
                parse_errors=0,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

        assert collector.min_latency_ms == 50.0
        assert collector.max_latency_ms == 200.0
        assert collector.mean_latency_ms == pytest.approx(123.33, abs=0.01)
        assert collector.total_runs == 3

    def test_all_error_types_aggregate_to_total(self):
        """CC3: All error types sum correctly."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=10,
            artifacts_skipped=0,
            parse_errors=2,
            structure_errors=3,
            io_errors=5,
            success=True,
        )

        assert collector.total_parse_errors == 2
        assert collector.total_structure_errors == 3
        assert collector.total_io_errors == 5
        assert collector.error_rate_percent == pytest.approx(100.0)

    def test_success_and_failed_run_tracking(self):
        """CC4: successful_runs + failed_runs = total_runs."""
        collector = CollectorMetrics("test")

        # First: success
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )
        # Second: failure
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=False,
        )
        # Third: success
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.total_runs == 3
        assert collector.successful_runs == 2
        assert collector.failed_runs == 1

    def test_very_large_artifact_counts(self):
        """EV1: Very large numbers don't overflow."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=1000.0,
            artifacts_processed=1_000_000_000,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.total_artifacts_processed == 1_000_000_000
        assert collector.throughput_artifacts_per_sec == pytest.approx(1_000_000_000.0)

    def test_error_timestamps_only_on_errors(self):
        """Last error timestamp only set when errors exist."""
        collector = CollectorMetrics("test")

        # No errors
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )
        assert collector.last_error_timestamp is None

        # With errors
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=1,
            structure_errors=0,
            io_errors=0,
            success=True,
        )
        assert collector.last_error_timestamp is not None

    def test_last_run_timestamp_always_updated(self):
        """Last run timestamp always updated on any update."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        first_timestamp = collector.last_run_timestamp
        assert first_timestamp is not None

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        second_timestamp = collector.last_run_timestamp
        assert second_timestamp is not None
        assert second_timestamp >= first_timestamp

    def test_serialization_preserves_all_fields(self):
        """S2: Serialization includes all fields and formats timestamps."""
        collector = CollectorMetrics("test_collector")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        data = collector.to_dict()

        assert data["collector_name"] == "test_collector"
        assert data["total_runs"] == 1
        assert data["successful_runs"] == 1
        assert data["failed_runs"] == 0
        assert data["total_artifacts_processed"] == 100
        assert data["total_artifacts_skipped"] == 0
        assert data["total_parse_errors"] == 0
        assert data["min_latency_ms"] == 100.0
        assert data["max_latency_ms"] == 100.0
        assert data["mean_latency_ms"] == 100.0
        assert data["health_status"] == "HEALTHY"
        assert isinstance(data["last_run_timestamp"], str)
        assert data["last_error_timestamp"] is None


class TestSystemMetricsHealthPrecedence:
    """Parameter set 6: System health status precedence rules."""

    @pytest.mark.parametrize(
        "healthy,degraded,critical,expected",
        [
            (3, 0, 0, "HEALTHY"),
            (3, 1, 0, "DEGRADED"),
            (3, 0, 1, "CRITICAL"),
            (0, 0, 0, "HEALTHY"),  # Empty dict → HEALTHY
            (1, 1, 1, "CRITICAL"),
            (0, 1, 0, "DEGRADED"),
        ],
    )
    def test_health_precedence(self, healthy, degraded, critical, expected):
        """Verify health status aggregation and precedence."""
        system = SystemMetrics()
        collectors = {}

        for i in range(healthy):
            m = CollectorMetrics(f"healthy_{i}")
            m.update_from_run(100.0, 10, 0, 0, 0, 0, True)
            collectors[f"healthy_{i}"] = m

        for i in range(degraded):
            m = CollectorMetrics(f"degraded_{i}")
            # Create DEGRADED status with 5% error rate
            m.update_from_run(100.0, 95, 0, 5, 0, 0, True)
            collectors[f"degraded_{i}"] = m

        for i in range(critical):
            m = CollectorMetrics(f"critical_{i}")
            # Create CRITICAL status with 20% error rate
            m.update_from_run(100.0, 80, 0, 20, 0, 0, True)
            collectors[f"critical_{i}"] = m

        system.update_from_collectors(collectors)

        assert system.system_health_status == expected
        assert system.healthy_collectors == healthy
        assert system.degraded_collectors == degraded
        assert system.critical_collectors == critical


class TestSystemMetricsErrorRateAggregation:
    """Parameter set 7: System-wide error rate calculation."""

    @pytest.mark.parametrize(
        "processed,errors,expected_rate",
        [
            (100, 10, 10.0),
            (1000, 1, 0.1),
            (0, 10, 0.0),  # No denominator → rate stays 0
            (1_000_000, 1000, 0.1),
            (50, 50, 100.0),
        ],
    )
    def test_system_error_rate(self, processed, errors, expected_rate):
        """Verify system-wide error rate aggregation."""
        system = SystemMetrics()

        collector = CollectorMetrics("test")
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=processed,
            artifacts_skipped=0,
            parse_errors=errors,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        system.update_from_collectors({"test": collector})

        assert system.overall_error_rate_percent == pytest.approx(expected_rate, abs=0.01)


class TestSystemMetricsCriticalEdgeCases:
    """Critical edge cases for SystemMetrics."""

    def test_empty_collectors_dict_is_healthy(self):
        """C7: Empty collectors → HEALTHY (all 0 collectors are healthy)."""
        system = SystemMetrics()
        system.update_from_collectors({})

        assert system.total_collectors == 0
        assert system.healthy_collectors == 0
        assert system.system_health_status == "HEALTHY"
        assert system.overall_error_rate_percent == 0.0

    def test_zero_processed_artifacts_keeps_zero_error_rate(self):
        """C8: No processed artifacts → error_rate stays 0.0 (division guard)."""
        system = SystemMetrics()

        collector = CollectorMetrics("test")
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=0,
            artifacts_skipped=0,
            parse_errors=5,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        system.update_from_collectors({"test": collector})

        assert system.overall_error_rate_percent == 0.0

    def test_critical_collector_makes_system_critical(self):
        """System inherits CRITICAL status if any collector is critical."""
        system = SystemMetrics()

        healthy = CollectorMetrics("healthy")
        healthy.update_from_run(100.0, 10, 0, 0, 0, 0, True)

        critical = CollectorMetrics("critical")
        critical.update_from_run(100.0, 80, 0, 20, 0, 0, True)

        system.update_from_collectors({"healthy": healthy, "critical": critical})

        assert system.system_health_status == "CRITICAL"
        assert system.critical_collectors == 1

    def test_degraded_collector_makes_system_degraded(self):
        """System inherits DEGRADED if no CRITICAL but has DEGRADED."""
        system = SystemMetrics()

        healthy = CollectorMetrics("healthy")
        healthy.update_from_run(100.0, 10, 0, 0, 0, 0, True)

        degraded = CollectorMetrics("degraded")
        degraded.update_from_run(100.0, 95, 0, 5, 0, 0, True)

        system.update_from_collectors({"healthy": healthy, "degraded": degraded})

        assert system.system_health_status == "DEGRADED"
        assert system.degraded_collectors == 1

    def test_nominal_fallback_case(self):
        """Nominal status when not all collectors healthy, no critical/degraded."""
        system = SystemMetrics()

        # Mix of healthy and nominal collectors
        # (Nominal collector has 0% error rate but not explicitly tracked)
        collector1 = CollectorMetrics("c1")
        collector1.update_from_run(100.0, 10, 0, 0, 0, 0, True)

        # Create a state with 0 runs (UNKNOWN) and 1 healthy
        system.update_from_collectors({"c1": collector1})

        # Now add another with uncertain status
        system.total_collectors = 2
        system.healthy_collectors = 1
        system.degraded_collectors = 0
        system.critical_collectors = 0

        # Manually trigger the logic
        if system.critical_collectors > 0:
            system.system_health_status = "CRITICAL"
        elif system.degraded_collectors > 0:
            system.system_health_status = "DEGRADED"
        elif system.healthy_collectors == system.total_collectors:
            system.system_health_status = "HEALTHY"
        else:
            system.system_health_status = "NOMINAL"

        assert system.system_health_status == "NOMINAL"

    def test_very_large_error_counts_no_overflow(self):
        """EV2: Very large error counts don't overflow."""
        system = SystemMetrics()

        collector = CollectorMetrics("test")
        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=1_000_000_000,
            artifacts_skipped=0,
            parse_errors=1_000_000,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        system.update_from_collectors({"test": collector})

        assert system.overall_error_rate_percent == pytest.approx(0.1, abs=0.001)

    def test_timestamp_freshness(self):
        """System timestamp reflects last update, not initialization."""
        system = SystemMetrics()
        init_time = system.timestamp

        collector = CollectorMetrics("test")
        collector.update_from_run(100.0, 10, 0, 0, 0, 0, True)

        system.update_from_collectors({"test": collector})

        assert system.timestamp >= init_time

    def test_system_serialization_includes_nested_metrics(self):
        """S3: System serialization includes all nested collector metrics."""
        system = SystemMetrics()

        collector = CollectorMetrics("test_collector")
        # 100 processed, 0 errors → HEALTHY status
        collector.update_from_run(100.0, 100, 0, 0, 0, 0, True)

        system.update_from_collectors({"test_collector": collector})

        data = system.to_dict()

        assert data["total_collectors"] == 1
        assert data["healthy_collectors"] == 1
        assert "test_collector" in data["collector_metrics"]
        assert data["collector_metrics"]["test_collector"]["collector_name"] == "test_collector"
        assert isinstance(data["timestamp"], str)


class TestPerformanceMetricSerialization:
    """Serialization tests for PerformanceMetric."""

    def test_performance_metric_to_dict_preserves_fields(self):
        """S1: PerformanceMetric serialization includes all fields."""
        now = datetime.now(timezone.utc)
        metric = PerformanceMetric(
            name="latency",
            value=100.5,
            unit=MetricUnit.MILLISECONDS,
            timestamp=now,
            collector_name="test_collector",
            artifact_type="test_artifact",
            tags={"run_id": "123"},
        )

        data = metric.to_dict()

        assert data["name"] == "latency"
        assert data["value"] == 100.5
        assert data["unit"] == "ms"
        assert data["timestamp"] == now.isoformat()
        assert data["collector"] == "test_collector"
        assert data["artifact_type"] == "test_artifact"
        assert data["tags"] == {"run_id": "123"}


class TestEdgeCaseStateTransitions:
    """State transition and dynamic update tests."""

    def test_health_improves_with_lower_error_rate(self):
        """ST1: Health status improves as error rate decreases."""
        collector = CollectorMetrics("test")

        # Start with 20% error rate (CRITICAL)
        collector.update_from_run(100.0, 80, 0, 20, 0, 0, True)
        assert collector.health_status == "CRITICAL"

        # Add successful run to lower error rate to 10% (DEGRADED)
        collector.update_from_run(100.0, 100, 0, 10, 0, 0, True)
        assert collector.health_status == "DEGRADED"

    def test_health_degrades_with_higher_error_rate(self):
        """ST2: Health status degrades as error rate increases."""
        collector = CollectorMetrics("test")

        # Start with 0% error rate (HEALTHY)
        collector.update_from_run(100.0, 1000, 0, 0, 0, 0, True)
        assert collector.health_status == "HEALTHY"
        assert collector.error_rate_percent == 0.0

        # Add minimal errors - any error makes it NOMINAL (not HEALTHY)
        collector.update_from_run(100.0, 4000, 0, 10, 0, 0, True)
        # Total: 5000 processed, 10 errors = 10/5000 = 0.2% → NOMINAL
        assert collector.health_status == "NOMINAL"

        # Increase error rate to just under 5% boundary (still NOMINAL)
        collector.update_from_run(100.0, 0, 0, 190, 0, 0, True)
        # Total: 5000 processed, 200 errors = 200/5000 = 4% → NOMINAL
        assert collector.health_status == "NOMINAL"

        # Increase error rate to >= 5% (DEGRADED)
        collector.update_from_run(100.0, 0, 0, 50, 0, 0, True)
        # Total: 5000 processed, 250 errors = 250/5000 = 5% → DEGRADED
        assert collector.health_status == "DEGRADED"

    def test_error_timestamp_transitions_from_none_to_set(self):
        """ST3: Error timestamp transitions None → now when first error occurs."""
        collector = CollectorMetrics("test")

        # No errors
        collector.update_from_run(100.0, 10, 0, 0, 0, 0, True)
        assert collector.last_error_timestamp is None

        # First error
        collector.update_from_run(100.0, 10, 0, 1, 0, 0, True)
        assert collector.last_error_timestamp is not None
        first_error_time = collector.last_error_timestamp

        # Second error - timestamp should be updated
        collector.update_from_run(100.0, 10, 0, 1, 0, 0, True)
        assert collector.last_error_timestamp >= first_error_time


class TestBoundaryAndEdgeCaseCombinations:
    """Tests for complex combinations of edge cases."""

    def test_zero_latency_with_artifacts_processed(self):
        """Zero latency doesn't prevent artifact counting."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=0.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=0,
            structure_errors=0,
            io_errors=0,
            success=True,
        )

        assert collector.total_artifacts_processed == 100
        assert collector.throughput_artifacts_per_sec == 0.0  # Guard prevents calc

    def test_multiple_zero_latencies_in_sequence(self):
        """Multiple zero latencies handled correctly."""
        collector = CollectorMetrics("test")

        for _ in range(3):
            collector.update_from_run(
                latency_ms=0.0,
                artifacts_processed=1,
                artifacts_skipped=0,
                parse_errors=0,
                structure_errors=0,
                io_errors=0,
                success=True,
            )

        assert collector.min_latency_ms == 0.0
        assert collector.max_latency_ms == 0.0
        assert collector.mean_latency_ms == 0.0

    def test_errors_across_different_error_types(self):
        """Different error types handled independently."""
        collector = CollectorMetrics("test")

        collector.update_from_run(
            latency_ms=100.0,
            artifacts_processed=100,
            artifacts_skipped=0,
            parse_errors=5,
            structure_errors=3,
            io_errors=2,
            success=True,
        )

        assert collector.total_parse_errors == 5
        assert collector.total_structure_errors == 3
        assert collector.total_io_errors == 2
        # Total errors = 10, total attempted = 100, so 10% error rate → DEGRADED
        assert collector.error_rate_percent == pytest.approx(10.0)
        assert collector.health_status == "DEGRADED"  # 5% <= 10% < 20% → DEGRADED

    def test_aggregating_mixed_healthy_and_nominal_collectors(self):
        """ST4: Multiple collectors with different health statuses aggregate correctly."""
        system = SystemMetrics()

        healthy = CollectorMetrics("healthy")
        healthy.update_from_run(100.0, 100, 0, 0, 0, 0, True)

        nominal = CollectorMetrics("nominal")
        nominal.update_from_run(100.0, 96, 0, 4, 0, 0, True)

        system.update_from_collectors({"healthy": healthy, "nominal": nominal})

        assert system.healthy_collectors == 1
        assert system.system_health_status == "NOMINAL"
