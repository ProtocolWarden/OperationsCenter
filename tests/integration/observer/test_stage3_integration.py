# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Stage 3 Integration Tests — Verify complete extraction pipeline.

Tests the full flow from pytest execution through artifact generation,
ensuring test names and assertion messages are properly extracted,
stored, processed, and included in final artifacts.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from operations_center.observer.assertion_extractor import extract_assertion_from_excinfo
from operations_center.observer.collectors.flaky_test_collector import FlakyTestCollector
from operations_center.observer.flaky_test_reporter import FlakyTestConfig
from operations_center.observer.models import FlakyTestSignal
from operations_center.observer.pytest_flaky_plugin import FlakyTestDetectionPlugin


class TestStage3FullPipeline:
    """Tests for the complete Stage 3 extraction pipeline."""

    def test_extraction_storage_collection_artifact_flow(self) -> None:
        """Test complete flow: extract → store → collect → artifact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            storage_path = tmp_path / "metrics"
            storage_path.mkdir(parents=True, exist_ok=True)

            # ========== STAGE 1: Extraction in pytest plugin ==========
            plugin = FlakyTestDetectionPlugin(str(tmp_path))
            plugin.pytest_sessionstart(session=SimpleNamespace(name="test-session"))

            def _example():
                pass

            item = SimpleNamespace(
                nodeid="tests/test_example.py::test_example",
                function=type("F", (), {"__name__": "test_example"})(),
            )
            exc_info = SimpleNamespace(value=AssertionError("Expected 42 but got 0"), tb=None)
            call_info = SimpleNamespace(when="call", excinfo=exc_info, duration=0.5)

            plugin.pytest_runtest_makereport(item, call_info)

            # Verify extraction at pytest level
            outcome = plugin.test_outcomes["tests/test_example.py::test_example"]
            assert outcome["test_function"] == "test_example"
            assert outcome["assertion_message"] == "Expected 42 but got 0"
            assert outcome["outcome"] == "failed"

            # ========== STAGE 2: Storage to JSON ==========
            plugin.pytest_sessionfinish(session=SimpleNamespace(name="test-session"), exitstatus=1)

            # Verify JSON report generated
            reports = list((tmp_path / "runs").glob("*/*-session.json"))
            assert len(reports) == 1

            report = json.loads(reports[0].read_text(encoding="utf-8"))
            assert len(report["test_outcomes"]) == 1
            assert report["test_outcomes"][0]["test_function"] == "test_example"
            assert report["test_outcomes"][0]["assertion_message"] == "Expected 42 but got 0"

            # ========== STAGE 3: Collection from JSON ==========
            # Save metrics in JSONL format for collector
            metrics_file = storage_path / "metrics.jsonl"
            metric_data = {
                "nodeid": "tests/test_example.py::test_example",
                "failure_rate": 1.0,
                "run_count": 1,
                "retry_success_count": 0,
                "duration_mean": 0.5,
                "duration_variance": 0.0,
                "pattern_entropy": 0.0,
                "streak_length": 1,
                "recovery_time_days": None,
                "suspected_category": "unknown",
                "flakiness_score": 0.95,
                "confidence": 0.8,
                "markers": [],
                "last_failure_reason": "AssertionError: Expected 42 but got 0",
                "test_name": "test_example",
                "assertion_message": "Expected 42 but got 0",
                "timestamp": datetime.now(UTC).isoformat(),
            }
            with metrics_file.open("w") as f:
                f.write(json.dumps(metric_data) + "\n")

            # Collect metrics
            config = FlakyTestConfig(storage_root=tmp_path)
            collector = FlakyTestCollector(config)
            metrics = collector._load_metrics()

            assert len(metrics) == 1
            metric = metrics[0]
            assert metric.test_name == "test_example"
            assert metric.assertion_message == "Expected 42 but got 0"
            assert metric.nodeid == "tests/test_example.py::test_example"

            # ========== STAGE 4: Artifact Generation ==========
            # Create FlakyTestSignal with most_problematic_tests
            flaky_signal = FlakyTestSignal(
                status="measured",
                flaky_test_count=1,
                unstable_test_count=0,
                affected_modules=["tests"],
                most_problematic_tests=[metric.to_dict()],
                observed_at=datetime.now(UTC),
                summary="1 flaky test detected",
            )

            # Verify artifact data includes extracted fields
            assert flaky_signal.most_problematic_tests
            assert flaky_signal.most_problematic_tests[0]["test_name"] == "test_example"
            assert "Expected 42" in flaky_signal.most_problematic_tests[0]["assertion_message"]

    def test_multiple_failure_types_extraction_pipeline(self) -> None:
        """Test extraction pipeline with multiple exception types."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plugin = FlakyTestDetectionPlugin(str(tmp_path))
            plugin.pytest_sessionstart(session=SimpleNamespace(name="session"))

            # Test 1: AssertionError
            def _assertion():
                pass

            item1 = SimpleNamespace(
                nodeid="tests/test_errors.py::test_assertion",
                function=type("F", (), {"__name__": "test_assertion"})(),
            )
            exc1 = SimpleNamespace(value=AssertionError("assertion failed"), tb=None)
            plugin.pytest_runtest_makereport(
                item1, SimpleNamespace(when="call", excinfo=exc1, duration=0.1)
            )

            # Test 2: TimeoutError
            def _timeout():
                pass

            item2 = SimpleNamespace(
                nodeid="tests/test_errors.py::test_timeout",
                function=type("F", (), {"__name__": "test_timeout"})(),
            )
            exc2 = SimpleNamespace(value=TimeoutError("timeout occurred"), tb=None)
            plugin.pytest_runtest_makereport(
                item2, SimpleNamespace(when="call", excinfo=exc2, duration=30.0)
            )

            # Test 3: ValueError
            def _value():
                pass

            item3 = SimpleNamespace(
                nodeid="tests/test_errors.py::test_value",
                function=type("F", (), {"__name__": "test_value"})(),
            )
            exc3 = SimpleNamespace(value=ValueError("invalid value"), tb=None)
            plugin.pytest_runtest_makereport(
                item3, SimpleNamespace(when="call", excinfo=exc3, duration=0.2)
            )

            # Verify all were extracted
            assert len(plugin.test_outcomes) == 3
            assert plugin.test_outcomes["tests/test_errors.py::test_assertion"]["assertion_message"]
            assert plugin.test_outcomes["tests/test_errors.py::test_timeout"]["assertion_message"]
            assert plugin.test_outcomes["tests/test_errors.py::test_value"]["assertion_message"]

            plugin.pytest_sessionfinish(session=SimpleNamespace(name="session"), exitstatus=1)

            # Verify all stored in JSON
            reports = list((tmp_path / "runs").glob("*/*-session.json"))
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            assert len(report["test_outcomes"]) == 3

            outcomes_by_name = {o["test_function"]: o for o in report["test_outcomes"]}
            assert "test_assertion" in outcomes_by_name
            assert "test_timeout" in outcomes_by_name
            assert "test_value" in outcomes_by_name

    def test_data_preservation_through_json_roundtrip(self) -> None:
        """Test that extracted data survives JSON serialization and deserialization."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plugin = FlakyTestDetectionPlugin(str(tmp_path))
            plugin.pytest_sessionstart(session=SimpleNamespace(name="session"))

            # Test with special characters
            def _unicode():
                pass

            item = SimpleNamespace(
                nodeid="tests/test_unicode.py::test_unicode",
                function=type("F", (), {"__name__": "test_unicode"})(),
            )
            assertion_msg = 'Expected {"key": "value"} but got {"key": "other"}'
            exc = SimpleNamespace(value=AssertionError(assertion_msg), tb=None)
            plugin.pytest_runtest_makereport(
                item, SimpleNamespace(when="call", excinfo=exc, duration=0.3)
            )

            plugin.pytest_sessionfinish(session=SimpleNamespace(name="session"), exitstatus=1)

            # Read and verify JSON
            reports = list((tmp_path / "runs").glob("*/*-session.json"))
            report = json.loads(reports[0].read_text(encoding="utf-8"))

            outcome = report["test_outcomes"][0]
            assert outcome["test_function"] == "test_unicode"
            # Assertion message should be preserved (though possibly cleaned up)
            assert "Expected" in outcome["assertion_message"]
            assert "value" in outcome["assertion_message"]

            # Verify collector can read it back
            storage_path = tmp_path / "metrics"
            storage_path.mkdir(parents=True, exist_ok=True)
            metrics_file = storage_path / "metrics.jsonl"

            metric_dict = {
                "nodeid": outcome.get("test_name", "tests/test_unicode.py::test_unicode"),
                "failure_rate": 1.0,
                "run_count": 1,
                "retry_success_count": 0,
                "duration_mean": 0.3,
                "duration_variance": 0.0,
                "pattern_entropy": 0.0,
                "streak_length": 1,
                "recovery_time_days": None,
                "suspected_category": "unknown",
                "flakiness_score": 0.9,
                "confidence": 0.8,
                "markers": [],
                "last_failure_reason": outcome.get("exception", ""),
                "test_name": outcome.get("test_function", "test_unicode"),
                "assertion_message": outcome.get("assertion_message", ""),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            with metrics_file.open("w") as f:
                f.write(json.dumps(metric_dict) + "\n")

            config = FlakyTestConfig(storage_root=tmp_path)
            collector = FlakyTestCollector(config)
            metrics = collector._load_metrics()

            assert len(metrics) == 1
            assert metrics[0].test_name == "test_unicode"
            # Message should be preserved
            assert len(metrics[0].assertion_message) > 0


class TestStage3ErrorHandling:
    """Tests for error handling in the extraction pipeline."""

    def test_missing_test_name_handled_gracefully(self) -> None:
        """Test that missing test names don't break the pipeline."""
        plugin = FlakyTestDetectionPlugin()

        # Item without function attribute
        item = SimpleNamespace(nodeid="tests/test_no_name.py::test", function=None)
        exc = SimpleNamespace(value=AssertionError("error"), tb=None)
        call = SimpleNamespace(when="call", excinfo=exc, duration=0.1)

        # Should not crash
        plugin.pytest_runtest_makereport(item, call)

        # Should still be recorded
        assert "tests/test_no_name.py::test" in plugin.test_outcomes
        # Test name will be empty
        assert plugin.test_outcomes["tests/test_no_name.py::test"]["test_function"] == ""

    def test_very_long_assertion_message_truncated(self) -> None:
        """Test that excessively long assertion messages are truncated."""
        long_msg = "x" * 500
        exc = SimpleNamespace(value=AssertionError(long_msg), tb=None)
        result = extract_assertion_from_excinfo(exc)

        # Should be truncated to max 200 chars
        assert len(result) <= 200
        if len(long_msg) > 200:
            assert result.endswith("...")

    def test_malformed_excinfo_handled(self) -> None:
        """Test that malformed exception info is handled gracefully."""
        # Missing value attribute
        exc_info = SimpleNamespace(value=None)
        result = extract_assertion_from_excinfo(exc_info)
        assert result == ""

        # None excinfo
        result = extract_assertion_from_excinfo(None)
        assert result == ""
