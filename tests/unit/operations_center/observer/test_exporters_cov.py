# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Complementary coverage tests for observer.exporters.

Targets edge branches not exercised by the sibling suite:
- no-export-dir early returns and the ValueError guard
- IOError handling on write/read
- malformed JSONL lines and bad filename skipping
- auto_rotate disabled and outer rotation exception handler
- aggregate defaults and error-rate computation
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from operations_center.observer.exporters import (
    ValidationFailureMetric,
    ValidationMetricsExporter,
)


def _metric(**overrides: Any) -> ValidationFailureMetric:
    base: dict[str, Any] = {
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "collector_name": "c1",
        "artifact_type": "control_outcome.json",
        "failure_type": "parse_error",
        "severity": "HIGH",
        "error_message": "boom",
        "artifact_path": "/x/y.json",
        "context": {"k": "v"},
        "metrics_snapshot": {"n": 1},
    }
    base.update(overrides)
    return ValidationFailureMetric(**base)


class TestToDict:
    def test_to_dict_envelope_and_fields(self) -> None:
        d = _metric().to_dict()
        env = d["validation_failure_metric"]
        assert env["version"] == "1.0"
        assert env["timestamp"] == "2026-01-01T00:00:00+00:00"
        assert env["collector_name"] == "c1"
        assert env["metrics"] == {"n": 1}
        assert env["context"] == {"k": "v"}


class TestNoExportDir:
    def test_export_failure_noop(self) -> None:
        exp = ValidationMetricsExporter(export_dir=None)
        # Should silently return without raising.
        exp.export_failure(_metric())
        assert exp.export_dir is None

    def test_export_to_file_noop(self) -> None:
        exp = ValidationMetricsExporter(export_dir=None)
        # No export_dir -> early return, returns None and writes nothing.
        assert exp._export_to_file(_metric()) is None

    def test_get_metrics_file_path_raises(self) -> None:
        exp = ValidationMetricsExporter(export_dir=None)
        with pytest.raises(ValueError, match="export_dir is not set"):
            exp._get_metrics_file_path()

    def test_rotate_if_needed_noop(self) -> None:
        exp = ValidationMetricsExporter(export_dir=None)
        # No export_dir -> early return, returns None.
        assert exp._rotate_if_needed() is None

    def test_read_metrics_returns_empty(self) -> None:
        exp = ValidationMetricsExporter(export_dir=None)
        assert exp.read_metrics() == []

    def test_aggregate_empty_when_no_dir(self) -> None:
        exp = ValidationMetricsExporter(export_dir=None)
        agg = exp.aggregate_metrics()
        assert agg["total_errors"] == 0
        assert agg["by_collector"] == {}
        assert "error_rate_per_minute" not in agg


class TestExportErrorHandling:
    def test_export_failure_swallows_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exp = ValidationMetricsExporter(export_dir=tmp_path)

        calls: list[ValidationFailureMetric] = []

        def boom(metric: ValidationFailureMetric) -> None:
            calls.append(metric)
            raise RuntimeError("kaboom")

        monkeypatch.setattr(exp, "_export_to_file", boom)
        # The outer try/except in export_failure must swallow it.
        result = exp.export_failure(_metric())
        assert result is None
        # The failing writer was invoked, yet the exception did not propagate.
        assert len(calls) == 1
        assert list(tmp_path.glob("metrics-*.jsonl")) == []

    def test_export_to_file_handles_ioerror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exp = ValidationMetricsExporter(export_dir=tmp_path)

        import builtins

        real_open = builtins.open

        def fake_open(*args: Any, **kwargs: Any):
            raise OSError("disk full")

        monkeypatch.setattr(builtins, "open", fake_open)
        try:
            # IOError caught inside _export_to_file; no propagation.
            exp._export_to_file(_metric())
        finally:
            monkeypatch.setattr(builtins, "open", real_open)
        # Nothing should have been written.
        assert list(tmp_path.glob("metrics-*.jsonl")) == []


class TestRotate:
    def test_auto_rotate_disabled_keeps_old(self, tmp_path: Path) -> None:
        old = tmp_path / "metrics-2000-01-01.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path, retention_days=1, auto_rotate=False)
        exp._rotate_if_needed()
        assert old.exists()

    def test_rotate_removes_old_keeps_new(self, tmp_path: Path) -> None:
        old = tmp_path / "metrics-2000-01-01.jsonl"
        recent = tmp_path / f"metrics-{datetime.now(UTC):%Y-%m-%d}.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        recent.write_text("{}\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path, retention_days=30)
        exp._rotate_if_needed()
        assert not old.exists()
        assert recent.exists()

    def test_rotate_skips_malformed_filename(self, tmp_path: Path) -> None:
        bad = tmp_path / "metrics-not-a-date.jsonl"
        bad.write_text("{}\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path, retention_days=1)
        exp._rotate_if_needed()
        # ValueError on strptime is caught; file survives.
        assert bad.exists()

    def test_rotate_outer_exception_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exp = ValidationMetricsExporter(export_dir=tmp_path, retention_days=1)

        glob_calls: list[str] = []

        class BoomDir:
            def glob(self, pattern: str):
                glob_calls.append(pattern)
                raise RuntimeError("glob exploded")

        monkeypatch.setattr(exp, "export_dir", BoomDir())
        # Outer try/except in _rotate_if_needed must swallow.
        result = exp._rotate_if_needed()
        assert result is None
        # The exploding glob was reached, yet the exception was swallowed.
        assert glob_calls == ["metrics-*.jsonl"]


class TestReadMetrics:
    def test_skips_empty_and_bad_lines(self, tmp_path: Path) -> None:
        f = tmp_path / f"metrics-{datetime.now(UTC):%Y-%m-%d}.jsonl"
        good = json.dumps(_metric().to_dict())
        f.write_text(f"\n{good}\n   \nnot-json\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path)
        out = exp.read_metrics()
        assert len(out) == 1
        assert out[0]["validation_failure_metric"]["collector_name"] == "c1"

    def test_skips_bad_filename(self, tmp_path: Path) -> None:
        bad = tmp_path / "metrics-zzzz.jsonl"
        bad.write_text(json.dumps(_metric().to_dict()) + "\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path)
        # ValueError on filename date -> continue -> nothing read.
        assert exp.read_metrics() == []

    def test_date_filter_excludes_out_of_range(self, tmp_path: Path) -> None:
        early = tmp_path / "metrics-2020-01-01.jsonl"
        late = tmp_path / "metrics-2030-01-01.jsonl"
        early.write_text(json.dumps(_metric().to_dict()) + "\n", encoding="utf-8")
        late.write_text(json.dumps(_metric().to_dict()) + "\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path)
        out = exp.read_metrics(
            from_date=datetime(2025, 1, 1),
            to_date=datetime(2026, 1, 1),
        )
        # Both files fall outside the window.
        assert out == []

    def test_read_handles_ioerror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / f"metrics-{datetime.now(UTC):%Y-%m-%d}.jsonl"
        f.write_text(json.dumps(_metric().to_dict()) + "\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path)

        import builtins

        real_open = builtins.open

        def fake_open(*args: Any, **kwargs: Any):
            raise OSError("cannot read")

        monkeypatch.setattr(builtins, "open", fake_open)
        try:
            out = exp.read_metrics()
        finally:
            monkeypatch.setattr(builtins, "open", real_open)
        assert out == []

    def test_read_outer_exception_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exp = ValidationMetricsExporter(export_dir=tmp_path)

        class BoomDir:
            def glob(self, _pattern: str):
                raise RuntimeError("glob exploded")

        monkeypatch.setattr(exp, "export_dir", BoomDir())
        assert exp.read_metrics() == []


class TestAggregate:
    def test_defaults_for_missing_fields(self, tmp_path: Path) -> None:
        f = tmp_path / f"metrics-{datetime.now(UTC):%Y-%m-%d}.jsonl"
        # Envelope present but inner fields missing -> "unknown" defaults.
        line = json.dumps({"validation_failure_metric": {}})
        f.write_text(line + "\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path)
        agg = exp.aggregate_metrics()
        assert agg["total_errors"] == 0  # unknown failure_type counts nowhere
        assert agg["by_collector"] == {"unknown": 1}
        assert agg["by_artifact_type"] == {"unknown": 1}
        assert agg["by_severity"] == {"unknown": 1}
        assert agg["error_rate_per_minute"] == 0.0

    def test_counts_all_failure_types_and_rate(self, tmp_path: Path) -> None:
        f = tmp_path / f"metrics-{datetime.now(UTC):%Y-%m-%d}.jsonl"
        rows = [
            _metric(failure_type="parse_error").to_dict(),
            _metric(failure_type="structure_error", collector_name="c2").to_dict(),
            _metric(failure_type="io_error", severity="LOW").to_dict(),
        ]
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        exp = ValidationMetricsExporter(export_dir=tmp_path)
        agg = exp.aggregate_metrics()
        assert agg["total_errors"] == 3
        assert agg["parse_errors"] == 1
        assert agg["structure_errors"] == 1
        assert agg["io_errors"] == 1
        assert agg["by_collector"] == {"c1": 2, "c2": 1}
        assert agg["by_severity"] == {"HIGH": 2, "LOW": 1}
        assert agg["error_rate_per_minute"] == pytest.approx(3 / 24 / 60)


class TestFactory:
    def test_create_metric_defaults(self) -> None:
        before = datetime.now(UTC)
        m = ValidationMetricsExporter.create_metric_from_error(
            collector_name="col",
            artifact_type="art",
            failure_type="io_error",
            severity="MEDIUM",
            error_message="msg",
            artifact_path=Path("/tmp/a.json"),
        )
        assert m.context == {}
        assert m.metrics_snapshot == {}
        assert m.artifact_path == str(Path("/tmp/a.json"))
        assert m.timestamp >= before

    def test_create_metric_with_context_and_snapshot(self) -> None:
        m = ValidationMetricsExporter.create_metric_from_error(
            collector_name="col",
            artifact_type="art",
            failure_type="parse_error",
            severity="HIGH",
            error_message="msg",
            artifact_path="/tmp/b.json",
            context={"a": 1},
            metrics_snapshot={"b": 2},
        )
        assert m.context == {"a": 1}
        assert m.metrics_snapshot == {"b": 2}


class TestRoundTrip:
    def test_export_then_read(self, tmp_path: Path) -> None:
        exp = ValidationMetricsExporter(export_dir=tmp_path)
        exp.export_failure(_metric())
        out = exp.read_metrics()
        assert len(out) == 1
        env = out[0]["validation_failure_metric"]
        assert env["error_message"] == "boom"
