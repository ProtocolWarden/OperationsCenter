# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for FlakyTestCollector — Flaky test signal synthesis."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


from operations_center.config import Settings
from operations_center.observer.collectors.flaky_test_collector import FlakyTestCollector
from operations_center.observer.flaky_test_reporter import (
    FlakyTestConfig,
    FlakyTestMetric,
    FlakynessCategory,
)
from operations_center.observer.models import FlakyTestSignal
from operations_center.observer.service import ObserverContext


class TestFlakyTestCollectorInitialization:
    """Tests for FlakyTestCollector initialization."""

    def test_collector_init_with_valid_config(self) -> None:
        config = FlakyTestConfig(storage_root="/tmp/metrics")
        collector = FlakyTestCollector(config)
        assert collector.config == config
        assert collector.config.storage_root == Path("/tmp/metrics")

    def test_collector_init_with_default_thresholds(self) -> None:
        config = FlakyTestConfig(storage_root="/tmp/metrics")
        assert config.flakiness_threshold == 0.10
        assert config.unstable_threshold == 0.05
        assert config.min_run_count == 3


class TestMetricsLoading:
    """Tests for loading metrics from storage."""

    def test_load_metrics_from_jsonl_storage(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_data = [
            FlakyTestMetric(
                nodeid="tests/unit/test_foo.py::test_1",
                failure_rate=0.15,
                run_count=10,
                flakiness_score=0.35,
                confidence=0.8,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_foo.py::test_2",
                failure_rate=0.50,
                run_count=20,
                flakiness_score=0.65,
                confidence=0.95,
            ),
        ]
        with metrics_file.open("w") as f:
            for metric in metrics_data:
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        metrics = collector._load_metrics()

        assert len(metrics) == 2
        assert metrics[0].nodeid == "tests/unit/test_foo.py::test_1"
        assert metrics[1].failure_rate == 0.50

    def test_load_metrics_handles_missing_storage(self, tmp_path: Path) -> None:
        config = FlakyTestConfig(storage_root=tmp_path / "nonexistent")
        collector = FlakyTestCollector(config)
        metrics = collector._load_metrics()
        assert metrics == []

    def test_load_metrics_handles_no_metrics_dir(self, tmp_path: Path) -> None:
        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        metrics = collector._load_metrics()
        assert metrics == []

    def test_load_metrics_handles_corrupted_json(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_file.write_text(
            json.dumps(
                {
                    "nodeid": "tests/unit/test_foo.py::test_1",
                    "failure_rate": 0.15,
                }
            )
            + "\n"
            + "{ invalid json }\n"
        )

        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        metrics = collector._load_metrics()
        assert len(metrics) == 1
        assert metrics[0].nodeid == "tests/unit/test_foo.py::test_1"

    def test_load_metrics_filters_by_historical_window(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        now = datetime.now(UTC)
        metrics_file = metrics_dir / "metrics.jsonl"

        metric_old = FlakyTestMetric(
            nodeid="tests/unit/test_old.py::test_1",
            failure_rate=0.15,
            run_count=10,
        )
        metric_new = FlakyTestMetric(
            nodeid="tests/unit/test_new.py::test_1",
            failure_rate=0.25,
            run_count=10,
        )

        with metrics_file.open("w") as f:
            old_data = metric_old.to_dict()
            old_data["timestamp"] = (now - timedelta(days=60)).isoformat()
            f.write(json.dumps(old_data) + "\n")

            new_data = metric_new.to_dict()
            new_data["timestamp"] = now.isoformat()
            f.write(json.dumps(new_data) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path, historical_window_days=30)
        collector = FlakyTestCollector(config)
        metrics = collector._load_metrics()

        assert len(metrics) == 1
        assert metrics[0].nodeid == "tests/unit/test_new.py::test_1"


class TestSignalComputation:
    """Tests for signal computation from metrics."""

    def test_compute_flaky_test_count(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_data = [
            FlakyTestMetric(
                nodeid="tests/unit/test_1.py::test_1",
                failure_rate=0.15,
                run_count=10,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_2.py::test_2",
                failure_rate=0.50,
                run_count=10,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_3.py::test_3",
                failure_rate=0.05,
                run_count=10,
            ),
        ]
        with metrics_file.open("w") as f:
            for metric in metrics_data:
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path, flakiness_threshold=0.10)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.flaky_test_count == 2

    def test_compute_unstable_test_count(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_data = [
            FlakyTestMetric(
                nodeid="tests/unit/test_1.py::test_1",
                failure_rate=0.07,
                run_count=10,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_2.py::test_2",
                failure_rate=0.05,
                run_count=10,
            ),
        ]
        with metrics_file.open("w") as f:
            for metric in metrics_data:
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(
            storage_root=tmp_path,
            flakiness_threshold=0.10,
            unstable_threshold=0.05,
        )
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.unstable_test_count == 2

    def test_compute_module_affectedness(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_data = [
            FlakyTestMetric(
                nodeid="tests/unit/test_foo.py::test_1",
                failure_rate=0.15,
                run_count=10,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_bar.py::test_2",
                failure_rate=0.50,
                run_count=10,
            ),
            FlakyTestMetric(
                nodeid="tests/integration/test_api.py::test_3",
                failure_rate=0.20,
                run_count=10,
            ),
        ]
        with metrics_file.open("w") as f:
            for metric in metrics_data:
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert "tests/unit" in signal.affected_modules
        assert "tests/integration" in signal.affected_modules

    def test_most_problematic_tests_limited_to_five(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        with metrics_file.open("w") as f:
            for i in range(10):
                metric = FlakyTestMetric(
                    nodeid=f"tests/unit/test_{i}.py::test_{i}",
                    failure_rate=0.1 + (i * 0.05),
                    run_count=10,
                    flakiness_score=0.5 + (i * 0.05),
                )
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert len(signal.most_problematic_tests) <= 5

    def test_category_breakdown_aggregation(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_data = [
            FlakyTestMetric(
                nodeid="tests/unit/test_1.py::test_1",
                failure_rate=0.15,
                run_count=10,
                suspected_category=FlakynessCategory.TRANSIENT,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_2.py::test_2",
                failure_rate=0.50,
                run_count=10,
                suspected_category=FlakynessCategory.STRUCTURAL,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_3.py::test_3",
                failure_rate=0.15,
                run_count=10,
                suspected_category=FlakynessCategory.TRANSIENT,
            ),
        ]
        with metrics_file.open("w") as f:
            for metric in metrics_data:
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path, flakiness_threshold=0.10)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.category_breakdown.get("transient", 0) == 2
        assert signal.category_breakdown.get("structural", 0) == 1


class TestImpactEstimation:
    """Tests for impact estimation."""

    def test_estimate_ci_slowdown_calculation(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metric = FlakyTestMetric(
            nodeid="tests/unit/test_foo.py::test_1",
            failure_rate=0.25,
            run_count=10,
            duration_mean=2.0,
        )
        with metrics_file.open("w") as f:
            f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path, flakiness_threshold=0.10)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.estimated_impact["ci_slowdown_percent"] > 0

    def test_estimate_dev_hours_calculation(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metric = FlakyTestMetric(
            nodeid="tests/unit/test_foo.py::test_1",
            failure_rate=0.30,
            run_count=10,
            duration_mean=3.0,
        )
        with metrics_file.open("w") as f:
            f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path, flakiness_threshold=0.10)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.estimated_impact["dev_hours_per_month"] > 0

    def test_estimate_with_no_flaky_tests(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metric = FlakyTestMetric(
            nodeid="tests/unit/test_foo.py::test_1",
            failure_rate=0.02,
            run_count=10,
        )
        with metrics_file.open("w") as f:
            f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path, flakiness_threshold=0.10)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.estimated_impact["ci_slowdown_percent"] == 0.0
        assert signal.estimated_impact["dev_hours_per_month"] == 0.0


class TestSignalGeneration:
    """Tests for full signal generation workflow."""

    def test_collect_returns_valid_flaky_test_signal(self, tmp_path: Path) -> None:
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        metrics_file = metrics_dir / "metrics.jsonl"
        metrics_data = [
            FlakyTestMetric(
                nodeid="tests/unit/test_foo.py::test_1",
                failure_rate=0.15,
                run_count=10,
            ),
            FlakyTestMetric(
                nodeid="tests/unit/test_bar.py::test_2",
                failure_rate=0.50,
                run_count=10,
            ),
        ]
        with metrics_file.open("w") as f:
            for metric in metrics_data:
                f.write(json.dumps(metric.to_dict()) + "\n")

        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert isinstance(signal, FlakyTestSignal)
        assert signal.status == "measured"
        assert signal.flaky_test_count == 2
        assert signal.source == "flaky-test-reporter"
        assert signal.observed_at is not None
        assert signal.summary is not None
        assert len(signal.summary) > 0

    def test_collect_returns_unavailable_for_empty_storage(self, tmp_path: Path) -> None:
        config = FlakyTestConfig(storage_root=tmp_path)
        collector = FlakyTestCollector(config)
        signal = collector.collect(_make_observer_context())

        assert signal.status == "unavailable"


class TestModuleExtraction:
    """Tests for module path extraction from test node IDs."""

    def test_extract_module_standard_path(self) -> None:
        collector = FlakyTestCollector(FlakyTestConfig(storage_root="/tmp"))
        module = collector._extract_module("tests/unit/test_foo.py::TestClass::test_method")
        assert module == "tests/unit"

    def test_extract_module_integration_path(self) -> None:
        collector = FlakyTestCollector(FlakyTestConfig(storage_root="/tmp"))
        module = collector._extract_module("tests/integration/test_api.py::test_endpoint")
        assert module == "tests/integration"

    def test_extract_module_single_component(self) -> None:
        collector = FlakyTestCollector(FlakyTestConfig(storage_root="/tmp"))
        module = collector._extract_module("test_foo.py::test_method")
        assert module == "test_foo.py"

    def test_extract_module_empty_nodeid(self) -> None:
        collector = FlakyTestCollector(FlakyTestConfig(storage_root="/tmp"))
        module = collector._extract_module("")
        assert module is None


def _make_observer_context(repo_path: Path | None = None) -> ObserverContext:
    """Create a mock ObserverContext for testing."""
    from unittest.mock import MagicMock

    mock_settings = MagicMock(spec=Settings)
    return ObserverContext(
        repo_path=repo_path or Path("/tmp/repo"),
        repo_name="test_repo",
        base_branch="main",
        run_id="test_run_123",
        observed_at=datetime.now(UTC),
        source_command="observer test",
        settings=mock_settings,
        commit_limit=100,
        hotspot_window=7,
        todo_limit=100,
        logs_root=Path("/tmp/logs"),
    )
