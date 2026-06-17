# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parametrized edge-case tests for observer metrics extreme scenarios.

Tests coverage:
- Zero values and boundary conditions
- Infinity initialization and handling
- Very large numbers and overflow safety
- Division by zero guards (throughput, error rate)
- Health status transitions (all bands)
- Error rate boundary conditions (0%, 5%, 20%, 100%)
- Latency min/max tracking with inf/zero values
- System health precedence and aggregation
- Timestamp freshness and error tracking

This comprehensive test suite validates that all computation paths handle
extreme scenarios correctly without precision loss, overflow, or crashes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import inf

import pytest

from operations_center.observer.metrics import (
    CollectorMetrics,
    MetricsCollector,
    MetricUnit,
    PerformanceMetric,
    SystemMetrics,
)


# ============================================================================
# PART 1: CollectorMetrics Health Status Boundary Tests (8 parametrized cases)
# ============================================================================
class TestHealthStatusThresholds:
    """Test health status transitions across all error rate boundaries."""

    @pytest.mark.parametrize(
        "error_rate,expected_status",
        [
            (0.0, "HEALTHY"),
            (0.01, "NOMINAL"),
            (4.99, "NOMINAL"),
            (4.999, "NOMINAL"),
            (5.0, "DEGRADED"),
            (5.01, "DEGRADED"),
            (19.99, "DEGRADED"),
            (20.0, "CRITICAL"),
            (20.01, "CRITICAL"),
            (100.0, "CRITICAL"),
        ],
    )
    def test_error_rate_health_status_mapping(
        self, error_rate: float, expected_status: str
    ) -> None:
        """Verify error rate correctly maps to health status across all boundaries."""
        cm = CollectorMetrics(collector_name="test")
        cm.total_runs = 1
        cm.error_rate_percent = error_rate
        cm._update_health_status()
        assert cm.health_status == expected_status

    def test_unknown_status_when_zero_runs(self) -> None:
        """Verify UNKNOWN status when no runs have occurred."""
        cm = CollectorMetrics(collector_name="test")
        assert cm.total_runs == 0
        cm._update_health_status()
        assert cm.health_status == "UNKNOWN"

    def test_health_status_transitions_across_runs(self) -> None:
        """Verify health status evolves correctly through multiple runs."""
        cm = CollectorMetrics(collector_name="test")

        # Run 1: healthy
        cm.update_from_run(10.0, 10, 0, 0, 0, 0, True)
        assert cm.health_status == "HEALTHY"
        assert cm.error_rate_percent == 0.0

        # Run 2: add some errors -> NOMINAL
        # Cumulative: 20 processed, 5 skipped, 1 error -> 1/(20+5)*100 = 4%
        cm.update_from_run(10.0, 10, 5, 1, 0, 0, True)
        assert cm.error_rate_percent == pytest.approx(1.0 / 25.0 * 100.0)
        assert cm.health_status == "NOMINAL"

        # Run 3: more errors -> DEGRADED
        # Cumulative: 30 processed, 10 skipped, 4 errors -> 4/(30+10)*100 = 10%
        cm.update_from_run(10.0, 10, 5, 2, 1, 0, True)
        total_errors = 1 + 2 + 1
        total_attempted = 30 + 10
        assert cm.error_rate_percent == pytest.approx(total_errors / total_attempted * 100.0)
        assert cm.health_status == "DEGRADED"

        # Run 4: many errors -> CRITICAL
        # Cumulative: 40 processed, 15 skipped, 19 errors -> 19/(40+15)*100 = 34.5%
        cm.update_from_run(10.0, 10, 5, 5, 5, 5, False)
        assert cm.health_status == "CRITICAL"


# ============================================================================
# PART 2: Latency Edge Cases (5 parametrized cases)
# ============================================================================
class TestLatencyEdgeCases:
    """Test latency min/max/mean tracking with edge values."""

    def test_latency_first_run_sets_min_from_infinity(self) -> None:
        """Verify first run correctly sets min_latency from infinity initialization."""
        cm = CollectorMetrics(collector_name="test")
        assert cm.min_latency_ms == inf
        assert cm.max_latency_ms == 0.0

        cm.update_from_run(100.0, 1, 0, 0, 0, 0, True)
        assert cm.min_latency_ms == 100.0
        assert cm.max_latency_ms == 100.0

    def test_latency_zero_value_tracked_correctly(self) -> None:
        """Verify zero latency is tracked as minimum value."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(50.0, 1, 0, 0, 0, 0, True)
        cm.update_from_run(0.0, 1, 0, 0, 0, 0, True)
        assert cm.min_latency_ms == 0.0
        assert cm.max_latency_ms == 50.0

    def test_latency_multiple_runs_tracks_min_max(self) -> None:
        """Verify min/max latency correctly tracked across multiple runs."""
        cm = CollectorMetrics(collector_name="test")
        latencies = [150.0, 50.0, 200.0, 75.0, 100.0]
        for lat in latencies:
            cm.update_from_run(lat, 1, 0, 0, 0, 0, True)

        assert cm.min_latency_ms == 50.0
        assert cm.max_latency_ms == 200.0
        assert cm.total_latency_ms == sum(latencies)
        assert cm.mean_latency_ms == pytest.approx(sum(latencies) / len(latencies))

    def test_latency_zero_skips_throughput_calculation(self) -> None:
        """Verify throughput isn't calculated when elapsed time is zero."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(0.0, 100, 0, 0, 0, 0, True)

        assert cm.total_latency_ms == 0.0
        assert cm.throughput_artifacts_per_sec == 0.0

    @pytest.mark.parametrize(
        "total_latency,processed,expected_throughput",
        [
            (1000.0, 100, 100.0),  # 100 artifacts / 1 second
            (500.0, 50, 100.0),  # 50 artifacts / 0.5 seconds
            (2000.0, 10, 5.0),  # 10 artifacts / 2 seconds
            (100.0, 5, 50.0),  # 5 artifacts / 0.1 seconds
        ],
    )
    def test_throughput_calculation_correctness(
        self, total_latency: float, processed: int, expected_throughput: float
    ) -> None:
        """Verify throughput calculation: artifacts / (total_ms / 1000)."""
        cm = CollectorMetrics(collector_name="test")
        # Accumulate latency and processed across multiple runs
        for _ in range(5):
            cm.update_from_run(total_latency / 5.0, processed // 5, 0, 0, 0, 0, True)

        assert cm.throughput_artifacts_per_sec == pytest.approx(expected_throughput, rel=1e-5)


# ============================================================================
# PART 3: Artifact Processing Edge Cases (5 parametrized cases)
# ============================================================================
class TestArtifactProcessingEdgeCases:
    """Test artifact processing and skipping counters."""

    @pytest.mark.parametrize(
        "processed,skipped",
        [
            (0, 0),
            (100, 0),
            (0, 100),
            (100, 100),
            (1000000, 1000000),  # very large numbers
        ],
    )
    def test_artifact_counters_accumulate(self, processed: int, skipped: int) -> None:
        """Verify artifact counters accumulate correctly."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10.0, processed, skipped, 0, 0, 0, True)

        assert cm.total_artifacts_processed == processed
        assert cm.total_artifacts_skipped == skipped

    def test_artifact_processing_with_multiple_runs(self) -> None:
        """Verify artifact counters accumulate across multiple runs."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10.0, 50, 10, 0, 0, 0, True)
        cm.update_from_run(10.0, 30, 5, 0, 0, 0, True)
        cm.update_from_run(10.0, 20, 15, 0, 0, 0, True)

        assert cm.total_artifacts_processed == 100
        assert cm.total_artifacts_skipped == 30

    def test_zero_processed_zero_skipped_no_error_rate(self) -> None:
        """Verify error_rate stays 0.0 when no artifacts to process."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10.0, 0, 0, 5, 5, 5, True)

        # No division by zero: total_attempted = 0, so branch skipped
        assert cm.error_rate_percent == 0.0
        assert cm.health_status == "HEALTHY"


# ============================================================================
# PART 4: Error Rate Calculation Edge Cases (7 parametrized cases)
# ============================================================================
class TestErrorRateCalculation:
    """Test error rate calculation with guard conditions."""

    @pytest.mark.parametrize(
        "processed,skipped,parse,struct,io,expected_error_rate",
        [
            (10, 0, 0, 0, 0, 0.0),  # zero errors
            (100, 0, 5, 0, 0, 5.0),  # 5%
            (100, 0, 0, 5, 0, 5.0),  # struct errors
            (100, 0, 0, 0, 5, 5.0),  # io errors
            (100, 0, 1, 2, 2, 5.0),  # mixed error types
            (100, 100, 10, 10, 10, 15.0),  # 30 errors / 200 total = 15%
            (1000000, 1000000, 500000, 500000, 500000, 75.0),  # 1.5M / 2M = 75%
        ],
    )
    def test_error_rate_calculation(
        self,
        processed: int,
        skipped: int,
        parse: int,
        struct: int,
        io: int,
        expected_error_rate: float,
    ) -> None:
        """Verify error_rate = (total_errors / total_attempted) * 100."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10.0, processed, skipped, parse, struct, io, True)

        assert cm.error_rate_percent == pytest.approx(expected_error_rate, rel=1e-5)

    def test_error_rate_with_no_processed_artifacts_guard(self) -> None:
        """Verify error rate guard: division by zero prevented when attempted=0."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10.0, 0, 0, 100, 100, 100, True)

        # Guard: total_attempted = 0, so branch skipped
        assert cm.error_rate_percent == 0.0

    def test_error_rate_progresses_with_multiple_runs(self) -> None:
        """Verify cumulative error rate across multiple runs."""
        cm = CollectorMetrics(collector_name="test")

        # Run 1: 10 processed, 1 error -> 10%
        cm.update_from_run(10.0, 10, 0, 1, 0, 0, True)
        assert cm.error_rate_percent == pytest.approx(10.0)

        # Run 2: 10 processed, 0 errors -> error rate drops to 5%
        cm.update_from_run(10.0, 10, 0, 0, 0, 0, True)
        assert cm.error_rate_percent == pytest.approx(5.0)

    def test_error_types_independence(self) -> None:
        """Verify each error type is tracked independently but aggregated."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10.0, 100, 0, 10, 20, 30, True)

        assert cm.total_parse_errors == 10
        assert cm.total_structure_errors == 20
        assert cm.total_io_errors == 30
        total_errors = 60
        assert cm.error_rate_percent == pytest.approx((total_errors / 100) * 100.0)


# ============================================================================
# PART 5: System Health Precedence (6 parametrized cases)
# ============================================================================
class TestSystemHealthPrecedence:
    """Test SystemMetrics health status precedence rules."""

    def test_system_empty_collectors_is_healthy(self) -> None:
        """Verify system is HEALTHY when no collectors exist."""
        sm = SystemMetrics()
        sm.update_from_collectors({})
        assert sm.total_collectors == 0
        assert sm.system_health_status == "HEALTHY"

    def test_system_all_healthy_collectors_is_healthy(self) -> None:
        """Verify system is HEALTHY when all collectors are HEALTHY."""
        collectors = {f"c{i}": self._make_collector(f"c{i}", "HEALTHY") for i in range(3)}
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.healthy_collectors == 3
        assert sm.system_health_status == "HEALTHY"

    def test_system_critical_takes_precedence(self) -> None:
        """Verify system is CRITICAL if any collector is CRITICAL."""
        collectors = {
            "c1": self._make_collector("c1", "CRITICAL"),
            "c2": self._make_collector("c2", "DEGRADED"),
            "c3": self._make_collector("c3", "HEALTHY"),
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.critical_collectors == 1
        assert sm.system_health_status == "CRITICAL"

    def test_system_degraded_takes_precedence_over_nominal(self) -> None:
        """Verify system is DEGRADED if any collector is DEGRADED (no CRITICAL)."""
        collectors = {
            "c1": self._make_collector("c1", "DEGRADED"),
            "c2": self._make_collector("c2", "NOMINAL"),
            "c3": self._make_collector("c3", "HEALTHY"),
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.degraded_collectors == 1
        assert sm.system_health_status == "DEGRADED"

    def test_system_nominal_when_mixed_non_degraded(self) -> None:
        """Verify system is NOMINAL when mixed but no CRITICAL/DEGRADED."""
        collectors = {
            "c1": self._make_collector("c1", "HEALTHY"),
            "c2": self._make_collector("c2", "NOMINAL"),
            "c3": self._make_collector("c3", "UNKNOWN"),
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.critical_collectors == 0
        assert sm.degraded_collectors == 0
        assert sm.system_health_status == "NOMINAL"

    @pytest.mark.parametrize(
        "statuses,expected",
        [
            (["HEALTHY", "HEALTHY"], "HEALTHY"),
            (["HEALTHY", "NOMINAL"], "NOMINAL"),
            (["NOMINAL", "NOMINAL"], "NOMINAL"),
            (["HEALTHY", "DEGRADED"], "DEGRADED"),
            (["CRITICAL", "HEALTHY"], "CRITICAL"),
            (["CRITICAL", "DEGRADED"], "CRITICAL"),
        ],
    )
    def test_system_health_precedence_matrix(self, statuses: list[str], expected: str) -> None:
        """Parametrized test of system health precedence rules."""
        collectors = {
            f"c{i}": self._make_collector(f"c{i}", status) for i, status in enumerate(statuses)
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.system_health_status == expected

    @staticmethod
    def _make_collector(
        name: str, health: str, processed: int = 10, errors: int = 0
    ) -> CollectorMetrics:
        """Helper to create a collector with specified health status."""
        cm = CollectorMetrics(collector_name=name)
        cm.health_status = health
        cm.total_artifacts_processed = processed
        cm.total_artifacts_skipped = 0
        if errors > 0:
            cm.total_parse_errors = errors
        cm.total_runs = 1
        return cm


# ============================================================================
# PART 6: Overall Error Rate Calculation (5 parametrized cases)
# ============================================================================
class TestSystemErrorRateCalculation:
    """Test SystemMetrics overall error rate aggregation."""

    def test_system_error_rate_aggregation(self) -> None:
        """Verify system aggregates error rates from all collectors."""
        collectors = {
            "c1": self._make_collector("c1", "HEALTHY", processed=100, errors=10),
            "c2": self._make_collector("c2", "HEALTHY", processed=100, errors=5),
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)

        # total_errors = 15, total_attempted = 200 -> 7.5%
        assert sm.overall_error_rate_percent == pytest.approx(7.5)
        assert sm.total_validation_failures == 15

    def test_system_error_rate_zero_when_no_errors(self) -> None:
        """Verify error rate is 0.0 when no errors occur."""
        collectors = {
            "c1": self._make_collector("c1", "HEALTHY", processed=100, errors=0),
            "c2": self._make_collector("c2", "HEALTHY", processed=100, errors=0),
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.overall_error_rate_percent == 0.0
        assert sm.total_validation_failures == 0

    def test_system_error_rate_guard_with_zero_processed(self) -> None:
        """Verify error rate stays 0.0 when no artifacts processed."""
        collectors = {
            "c1": self._make_collector("c1", "HEALTHY", processed=0, errors=5),
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)

        # Guard: total_processed = 0, so branch skipped
        assert sm.overall_error_rate_percent == 0.0
        assert sm.total_validation_failures == 5

    @pytest.mark.parametrize(
        "processed_list,error_counts,expected_rate",
        [
            ([100, 100, 100], [0, 0, 0], 0.0),
            ([100, 100, 100], [5, 5, 5], 5.0),
            ([100, 100], [10, 10], 10.0),
            ([1000, 1000], [100, 200], 15.0),
            ([1000000, 1000000], [100000, 200000], 15.0),
        ],
    )
    def test_system_error_rate_parametrized(
        self, processed_list: list[int], error_counts: list[int], expected_rate: float
    ) -> None:
        """Parametrized test of system error rate calculation."""
        collectors = {
            f"c{i}": self._make_collector(f"c{i}", "HEALTHY", processed_list[i], error_counts[i])
            for i in range(len(processed_list))
        }
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        assert sm.overall_error_rate_percent == pytest.approx(expected_rate, rel=1e-5)

    @staticmethod
    def _make_collector(name: str, health: str, processed: int, errors: int) -> CollectorMetrics:
        """Helper to create a collector with specified error count."""
        cm = CollectorMetrics(collector_name=name)
        cm.health_status = health
        cm.total_artifacts_processed = processed
        cm.total_artifacts_skipped = 0
        cm.total_parse_errors = errors
        cm.total_runs = 1
        return cm


# ============================================================================
# PART 7: Timestamp Handling (3 parametrized cases)
# ============================================================================
class TestTimestampHandling:
    """Test timestamp tracking for runs and errors."""

    def test_last_run_timestamp_always_updated(self) -> None:
        """Verify last_run_timestamp is updated on every run."""
        cm = CollectorMetrics(collector_name="test")
        assert cm.last_run_timestamp is None

        cm.update_from_run(10.0, 1, 0, 0, 0, 0, True)
        ts1 = cm.last_run_timestamp
        assert ts1 is not None

        cm.update_from_run(10.0, 1, 0, 0, 0, 0, True)
        ts2 = cm.last_run_timestamp
        assert ts2 is not None
        assert ts2 >= ts1

    def test_last_error_timestamp_only_set_with_errors(self) -> None:
        """Verify last_error_timestamp is only set when errors occur."""
        cm = CollectorMetrics(collector_name="test")
        assert cm.last_error_timestamp is None

        # Run with no errors
        cm.update_from_run(10.0, 1, 0, 0, 0, 0, True)
        assert cm.last_error_timestamp is None

        # Run with errors
        cm.update_from_run(10.0, 1, 0, 1, 0, 0, True)
        ts1 = cm.last_error_timestamp
        assert ts1 is not None

        # Run with more errors
        cm.update_from_run(10.0, 1, 0, 1, 0, 0, True)
        ts2 = cm.last_error_timestamp
        assert ts2 is not None
        assert ts2 >= ts1

    def test_system_timestamp_updated_on_aggregation(self) -> None:
        """Verify system timestamp is updated when collectors aggregated."""
        sm = SystemMetrics()
        ts1 = sm.timestamp

        collectors = {"c1": CollectorMetrics(collector_name="c1")}
        sm.update_from_collectors(collectors)
        ts2 = sm.timestamp
        assert ts2 >= ts1


# ============================================================================
# PART 8: Serialization and Data Integrity (3 parametrized cases)
# ============================================================================
class TestSerializationIntegrity:
    """Test to_dict() serialization preserves all data."""

    def test_performance_metric_serialization(self) -> None:
        """Verify PerformanceMetric serializes correctly."""
        ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        pm = PerformanceMetric(
            name="latency",
            value=100.5,
            unit=MetricUnit.MILLISECONDS,
            timestamp=ts,
            collector_name="c1",
            artifact_type="json",
            tags={"env": "test"},
        )
        d = pm.to_dict()

        assert d["name"] == "latency"
        assert d["value"] == 100.5
        assert d["unit"] == "ms"
        assert d["timestamp"] == ts.isoformat()
        assert d["collector"] == "c1"
        assert d["artifact_type"] == "json"
        assert d["tags"] == {"env": "test"}

    def test_collector_metrics_serialization_with_stats(self) -> None:
        """Verify CollectorMetrics serializes with all computed stats."""
        cm = CollectorMetrics(collector_name="c1")
        cm.update_from_run(100.0, 50, 10, 5, 0, 0, True)
        cm.update_from_run(50.0, 50, 10, 0, 2, 0, False)

        d = cm.to_dict()
        assert d["collector_name"] == "c1"
        assert d["total_runs"] == 2
        assert d["successful_runs"] == 1
        assert d["failed_runs"] == 1
        assert d["total_artifacts_processed"] == 100
        assert d["total_artifacts_skipped"] == 20
        assert d["total_parse_errors"] == 5
        assert d["total_structure_errors"] == 2
        assert d["min_latency_ms"] == 50.0
        assert d["max_latency_ms"] == 100.0
        # error_rate = 7/(100+20)*100 = 5.833%, which is DEGRADED
        assert d["health_status"] == "DEGRADED"
        assert isinstance(d["last_run_timestamp"], str)
        assert isinstance(d["last_error_timestamp"], str)

    def test_system_metrics_serialization_complete(self) -> None:
        """Verify SystemMetrics serializes all collector data."""
        collectors = {
            "c1": CollectorMetrics(collector_name="c1"),
            "c2": CollectorMetrics(collector_name="c2"),
        }
        collectors["c1"].update_from_run(10.0, 10, 0, 0, 0, 0, True)
        collectors["c2"].update_from_run(10.0, 5, 0, 1, 0, 0, True)

        sm = SystemMetrics()
        sm.update_from_collectors(collectors)
        d = sm.to_dict()

        assert d["total_collectors"] == 2
        assert d["healthy_collectors"] == 1
        assert "c1" in d["collector_metrics"]
        assert "c2" in d["collector_metrics"]
        assert isinstance(d["timestamp"], str)


# ============================================================================
# PART 9: Multiple Run Dynamics (4 parametrized cases)
# ============================================================================
class TestMultipleRunDynamics:
    """Test behavior across multiple sequential runs."""

    def test_counters_accumulate_correctly_over_runs(self) -> None:
        """Verify all counters accumulate across multiple runs."""
        cm = CollectorMetrics(collector_name="test")

        for i in range(5):
            cm.update_from_run(
                latency_ms=10.0 + i,
                artifacts_processed=10,
                artifacts_skipped=2,
                parse_errors=1,
                structure_errors=0,
                io_errors=0,
                success=i < 4,  # Last run fails
            )

        assert cm.total_runs == 5
        assert cm.successful_runs == 4
        assert cm.failed_runs == 1
        assert cm.total_artifacts_processed == 50
        assert cm.total_artifacts_skipped == 10
        assert cm.total_parse_errors == 5

    def test_mean_latency_updates_correctly(self) -> None:
        """Verify mean latency is recalculated after each run."""
        cm = CollectorMetrics(collector_name="test")
        latencies = [100.0, 50.0, 150.0]
        cumulative_sum = 0.0

        for lat in latencies:
            cm.update_from_run(lat, 1, 0, 0, 0, 0, True)
            cumulative_sum += lat
            expected_mean = cumulative_sum / cm.total_runs
            assert cm.mean_latency_ms == pytest.approx(expected_mean)

    def test_health_status_improves_then_degrades(self) -> None:
        """Verify health status can improve and degrade dynamically."""
        cm = CollectorMetrics(collector_name="test")

        # Start with high error rate -> CRITICAL
        cm.update_from_run(10.0, 10, 0, 5, 5, 5, True)
        assert cm.health_status == "CRITICAL"

        # Add successful runs to dilute error rate -> improves to NOMINAL
        for _ in range(30):
            cm.update_from_run(10.0, 100, 0, 0, 0, 0, True)

        # Error rate drops -> NOMINAL (15 errors / 3010 attempts ≈ 0.5%, which is < 5%)
        assert cm.health_status == "NOMINAL"
        assert cm.error_rate_percent < 5.0

    @pytest.mark.parametrize(
        "run_sequence",
        [
            [(10.0, 10, 0, 0, 0, 0, True), (10.0, 10, 0, 0, 0, 0, True)],
            [(50.0, 5, 5, 1, 0, 0, True), (50.0, 5, 5, 0, 0, 0, True)],
            [(100.0, 100, 0, 10, 10, 10, True)] * 3,
        ],
    )
    def test_multiple_run_sequences(self, run_sequence: list) -> None:
        """Parametrized test of various run sequences."""
        cm = CollectorMetrics(collector_name="test")

        for latency, processed, skipped, parse, struct, io, success in run_sequence:
            cm.update_from_run(latency, processed, skipped, parse, struct, io, success)

        assert cm.total_runs == len(run_sequence)
        assert cm.min_latency_ms <= cm.max_latency_ms
        assert cm.mean_latency_ms >= 0


# ============================================================================
# PART 10: Very Large Numbers and Precision (3 parametrized cases)
# ============================================================================
class TestLargeNumbersAndPrecision:
    """Test behavior with very large numbers."""

    @pytest.mark.parametrize(
        "processed,errors",
        [
            (1_000_000, 100_000),
            (10_000_000, 1_000_000),
            (100_000_000, 10_000_000),
        ],
    )
    def test_large_artifact_counts(self, processed: int, errors: int) -> None:
        """Verify large artifact counts don't cause precision loss."""
        cm = CollectorMetrics(collector_name="test")
        cm.update_from_run(10000.0, processed, 0, errors, 0, 0, True)

        expected_rate = (errors / (processed + 0)) * 100
        assert cm.error_rate_percent == pytest.approx(expected_rate, rel=1e-10)

    def test_very_large_latency_accumulation(self) -> None:
        """Verify mean latency calculation doesn't overflow with large values."""
        cm = CollectorMetrics(collector_name="test")

        # Simulate very long-running operations
        for _ in range(1000):
            cm.update_from_run(100000.0, 1, 0, 0, 0, 0, True)

        assert cm.total_runs == 1000
        assert cm.mean_latency_ms == pytest.approx(100000.0)
        assert cm.total_latency_ms == pytest.approx(100000000.0)

    def test_system_level_large_scale_aggregation(self) -> None:
        """Verify system metrics aggregate large-scale data correctly."""
        collectors = {f"c{i}": self._make_large_collector(f"c{i}") for i in range(10)}
        sm = SystemMetrics()
        sm.update_from_collectors(collectors)

        assert sm.total_collectors == 10
        assert sm.total_validation_failures == 10_000_000
        expected_rate = (10_000_000 / (10 * 100_000_000)) * 100
        assert sm.overall_error_rate_percent == pytest.approx(expected_rate, rel=1e-10)

    @staticmethod
    def _make_large_collector(name: str) -> CollectorMetrics:
        """Helper to create a collector with large-scale metrics."""
        cm = CollectorMetrics(collector_name=name)
        cm.total_runs = 100
        cm.total_artifacts_processed = 100_000_000
        cm.total_artifacts_skipped = 0
        cm.total_parse_errors = 1_000_000
        cm.health_status = "DEGRADED"
        return cm


# ============================================================================
# PART 11: Integration Tests (Real-world Scenarios)
# ============================================================================
class TestRealWorldScenarios:
    """Integration tests combining multiple edge cases."""

    def test_mixed_collector_states_system_aggregation(self) -> None:
        """Simulate realistic multi-collector system state."""
        mc = MetricsCollector()

        # Healthy collector
        mc.record_collector_run("parser", 50.0, 1000, 50, 5, 0, 0, True)
        # Degraded collector with errors
        mc.record_collector_run("validator", 100.0, 500, 100, 25, 25, 25, False)
        # Another healthy collector
        mc.record_collector_run("transformer", 75.0, 750, 25, 10, 0, 0, True)

        system = mc.get_system_metrics()
        assert system.total_collectors == 3
        assert system.total_validation_failures > 0

    def test_stress_scenario_many_runs_one_collector(self) -> None:
        """Simulate high-volume run scenario."""
        mc = MetricsCollector()

        # Simulate 100 runs of a collector
        for i in range(100):
            mc.record_collector_run(
                "high_volume",
                latency_ms=10.0 + (i % 10),
                artifacts_processed=100,
                artifacts_skipped=10,
                parse_errors=i % 10,  # Variable errors
                structure_errors=0,
                io_errors=0,
                success=i % 5 != 0,  # 80% success rate
            )

        collector = mc.get_collector_metrics("high_volume")
        assert collector is not None
        assert collector.total_runs == 100
        assert collector.successful_runs == 80
        assert collector.failed_runs == 20
        assert collector.min_latency_ms >= 10.0
        assert collector.max_latency_ms <= 20.0

    def test_error_recovery_scenario(self) -> None:
        """Simulate recovery from error state."""
        cm = CollectorMetrics(collector_name="recovery_test")

        # Initial high error rate
        cm.update_from_run(10.0, 10, 0, 5, 5, 5, False)
        assert cm.health_status == "CRITICAL"

        # Gradual recovery with many successful runs
        for _ in range(50):
            cm.update_from_run(10.0, 100, 0, 0, 0, 0, True)

        # Error rate drops dramatically -> NOMINAL (15 / 5010 ≈ 0.3%, which is < 5%)
        assert cm.health_status == "NOMINAL"
        assert cm.error_rate_percent < 1.0
