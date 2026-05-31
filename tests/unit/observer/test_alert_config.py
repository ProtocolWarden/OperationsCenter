# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for alert configuration infrastructure."""
import pytest

from operations_center.observer.alert_config import (
    ALERT_ROUTES,
    COLLECTOR_THRESHOLDS,
    AlertContext,
    AlertRoute,
    CollectorThresholds,
    get_alert_route,
    get_collector_thresholds,
    list_collector_names,
    list_condition_names,
)


class TestCollectorThresholds:
    """Test CollectorThresholds dataclass."""

    def test_create_valid_thresholds(self) -> None:
        thresholds = CollectorThresholds(
            collector_name="TestCollector",
            high_water_mark=5,
            error_threshold=10,
            time_window_minutes=5,
            recovery_action="test_action",
        )
        assert thresholds.collector_name == "TestCollector"
        assert thresholds.high_water_mark == 5
        assert thresholds.error_threshold == 10

    def test_high_water_mark_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="high_water_mark must be >= 1"):
            CollectorThresholds(
                collector_name="Test",
                high_water_mark=0,
                error_threshold=10,
            )

    def test_error_threshold_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="error_threshold must be >= 1"):
            CollectorThresholds(
                collector_name="Test",
                high_water_mark=5,
                error_threshold=0,
            )

    def test_error_threshold_must_be_gte_high_water_mark(self) -> None:
        with pytest.raises(
            ValueError,
            match="error_threshold must be >= high_water_mark",
        ):
            CollectorThresholds(
                collector_name="Test",
                high_water_mark=10,
                error_threshold=5,
            )

    def test_time_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="time_window_minutes must be >= 1"):
            CollectorThresholds(
                collector_name="Test",
                high_water_mark=5,
                error_threshold=10,
                time_window_minutes=0,
            )


class TestAlertRoute:
    """Test AlertRoute dataclass."""

    def test_create_valid_route(self) -> None:
        route = AlertRoute(
            condition_name="test_condition",
            channels=["operator_log", "plane"],
        )
        assert route.condition_name == "test_condition"
        assert route.channels == ["operator_log", "plane"]

    def test_invalid_channel_raises_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown channel"):
            AlertRoute(
                condition_name="test",
                channels=["invalid_channel"],
            )

    def test_multiple_channels(self) -> None:
        route = AlertRoute(
            condition_name="test",
            channels=["operator_log", "plane", "slack", "pagerduty"],
        )
        assert len(route.channels) == 4

    def test_route_with_context(self) -> None:
        context = {"task_type": "improve", "priority": "high"}
        route = AlertRoute(
            condition_name="test",
            channels=["plane"],
            context=context,
        )
        assert route.context == context


class TestAlertContext:
    """Test AlertContext dataclass."""

    def test_create_valid_context(self) -> None:
        context = AlertContext(
            condition_name="parse_error_spike",
            collector_name="ExecutionArtifactCollector",
            error_count=15,
            threshold=10,
            severity="HIGH",
        )
        assert context.condition_name == "parse_error_spike"
        assert context.error_count == 15

    def test_to_dict(self) -> None:
        context = AlertContext(
            condition_name="test",
            error_count=5,
            threshold=10,
        )
        context_dict = context.to_dict()
        assert context_dict["condition_name"] == "test"
        assert context_dict["error_count"] == 5
        assert context_dict["threshold"] == 10

    def test_sample_errors(self) -> None:
        errors = ["Error 1", "Error 2", "Error 3"]
        context = AlertContext(
            condition_name="test",
            sample_errors=errors,
        )
        assert context.sample_errors == errors


class TestCollectorThresholdsRegistry:
    """Test COLLECTOR_THRESHOLDS registry."""

    def test_registry_not_empty(self) -> None:
        assert len(COLLECTOR_THRESHOLDS) > 0

    def test_all_thresholds_valid(self) -> None:
        for name, thresholds in COLLECTOR_THRESHOLDS.items():
            assert thresholds.collector_name == name
            assert thresholds.high_water_mark >= 1
            assert thresholds.error_threshold >= thresholds.high_water_mark
            assert thresholds.time_window_minutes >= 1

    def test_execution_artifact_collector_config(self) -> None:
        thresholds = COLLECTOR_THRESHOLDS["ExecutionArtifactCollector"]
        assert thresholds.high_water_mark == 5
        assert thresholds.error_threshold == 10
        assert thresholds.recovery_action == "skip_failed_runs"

    def test_dependency_drift_collector_config(self) -> None:
        thresholds = COLLECTOR_THRESHOLDS["DependencyDriftCollector"]
        assert thresholds.high_water_mark == 3
        assert thresholds.error_threshold == 5
        assert thresholds.recovery_action == "return_not_available"

    def test_default_recovery_action(self) -> None:
        thresholds = COLLECTOR_THRESHOLDS["BenchmarkSignal"]
        assert thresholds.recovery_action == "graceful_degradation"


class TestAlertRoutesRegistry:
    """Test ALERT_ROUTES registry."""

    def test_registry_not_empty(self) -> None:
        assert len(ALERT_ROUTES) > 0

    def test_parse_error_spike_route(self) -> None:
        route = ALERT_ROUTES["parse_error_spike"]
        assert "operator_log" in route.channels
        assert "plane" in route.channels
        assert route.context["priority"] == "high"

    def test_structure_error_surge_route(self) -> None:
        route = ALERT_ROUTES["structure_error_surge"]
        assert "operator_log" in route.channels
        assert "plane" in route.channels

    def test_permission_denied_pattern_route(self) -> None:
        route = ALERT_ROUTES["permission_denied_pattern"]
        assert "operator_log" in route.channels
        assert route.context["priority"] == "medium"

    def test_all_routes_valid(self) -> None:
        for name, route in ALERT_ROUTES.items():
            assert route.condition_name == name
            assert len(route.channels) > 0
            for channel in route.channels:
                assert channel in {
                    "operator_log",
                    "plane",
                    "slack",
                    "pagerduty",
                }


class TestHelperFunctions:
    """Test helper functions."""

    def test_get_collector_thresholds_existing(self) -> None:
        thresholds = get_collector_thresholds("ExecutionArtifactCollector")
        assert thresholds is not None
        assert thresholds.collector_name == "ExecutionArtifactCollector"

    def test_get_collector_thresholds_missing(self) -> None:
        thresholds = get_collector_thresholds("NonExistentCollector")
        assert thresholds is None

    def test_get_alert_route_existing(self) -> None:
        route = get_alert_route("parse_error_spike")
        assert route is not None
        assert route.condition_name == "parse_error_spike"

    def test_get_alert_route_missing(self) -> None:
        route = get_alert_route("nonexistent_condition")
        assert route is None

    def test_list_collector_names(self) -> None:
        names = list_collector_names()
        assert len(names) > 0
        assert "ExecutionArtifactCollector" in names
        assert "DependencyDriftCollector" in names
        assert names == sorted(names)

    def test_list_condition_names(self) -> None:
        names = list_condition_names()
        assert len(names) == 4
        assert "parse_error_spike" in names
        assert "structure_error_surge" in names
        assert "permission_denied_pattern" in names
        assert "collector_health_degradation" in names
        assert names == sorted(names)
