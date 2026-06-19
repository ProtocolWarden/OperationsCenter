# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.observer import service as service_mod
from operations_center.observer.models import (
    ArchitectureSignal,
    BacklogSignal,
    BenchmarkSignal,
    CheckSignal,
    CIHistorySignal,
    CommitMetadata,
    CoverageSignal,
    FileHotspot,
    DependencyDriftSignal,
    ExecutionHealthSignal,
    LintSignal,
    RepoContextSnapshot,
    SecuritySignal,
    TodoSignal,
    TypeSignal,
    ValidationHistorySignal,
)
from operations_center.observer.service import (
    ObserverContext,
    RepoObserverService,
    new_observer_context,
)


def _make_repo_snapshot() -> RepoContextSnapshot:
    return RepoContextSnapshot(
        name="repo",
        path=Path("/tmp/repo"),
        current_branch="main",
        base_branch="main",
        is_dirty=False,
    )


def _make_context(tmp_path: Path) -> ObserverContext:
    return ObserverContext(
        repo_path=tmp_path / "repo",
        repo_name="repo",
        base_branch="main",
        run_id="obs_run",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        source_command="observe",
        settings=MagicMock(),
        commit_limit=10,
        hotspot_window=20,
        todo_limit=5,
        logs_root=tmp_path / "logs",
    )


def _collector(result: object) -> MagicMock:
    c = MagicMock()
    c.collect.return_value = result
    return c


def _failing_collector(exc: Exception) -> MagicMock:
    c = MagicMock()
    c.collect.side_effect = exc
    return c


def _make_service(**overrides: object) -> RepoObserverService:
    kwargs: dict[str, object] = {
        "repo_collector": _collector(_make_repo_snapshot()),
        "recent_commits_collector": _collector(
            [
                CommitMetadata(
                    sha_short="abc1234",
                    author="dev",
                    timestamp=datetime(2026, 6, 1, tzinfo=UTC),
                    subject="msg",
                )
            ]
        ),
        "file_hotspots_collector": _collector([FileHotspot(path="a.py", touch_count=3)]),
        "test_signal_collector": _collector(CheckSignal(status="passed")),
        "dependency_drift_collector": _collector(DependencyDriftSignal(status="ok")),
        "todo_signal_collector": _collector(TodoSignal()),
    }
    kwargs.update(overrides)
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["a.json", "b.md"]
    kwargs.setdefault("snapshot_builder", builder)
    kwargs.setdefault("artifact_writer", writer)
    return RepoObserverService(**kwargs)  # type: ignore[arg-type]


def test_init_defaults_builder_and_writer() -> None:
    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
    )
    assert svc.snapshot_builder is not None
    assert svc.artifact_writer is not None
    assert svc.execution_health_collector is None
    assert svc.metrics_exporter is None


def test_observe_happy_path_passes_signals_and_returns(tmp_path: Path) -> None:
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    writer = MagicMock()
    writer.write.return_value = ["x.json"]
    svc = _make_service(snapshot_builder=builder, artifact_writer=writer)

    snapshot, artifacts = svc.observe(_make_context(tmp_path))

    assert snapshot == "BUILT"
    assert artifacts == ["x.json"]
    writer.write.assert_called_once_with("BUILT")
    build_kwargs = builder.build.call_args.kwargs
    assert build_kwargs["run_id"] == "obs_run"
    assert build_kwargs["source_command"] == "observe"
    # No errors when all collectors succeed.
    assert build_kwargs["collector_errors"] == {}
    signals = build_kwargs["signals"]
    assert signals.recent_commits[0].sha_short == "abc1234"
    assert signals.file_hotspots[0].path == "a.py"


def test_observe_drives_coverage_trend_recording(tmp_path: Path) -> None:
    # An observation with live coverage now drives CoverageTrendManager: the
    # bridged snapshot is recorded (building the trend history) — the live wire
    # for the previously-unwired trend/alert engines.
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    writer = MagicMock()
    writer.root = tmp_path / "obs"
    writer.write.return_value = ["x.json"]
    svc = _make_service(
        snapshot_builder=builder,
        artifact_writer=writer,
        coverage_signal_collector=_collector(CoverageSignal(status="ok", total_coverage_pct=91.5)),
    )

    svc.observe(_make_context(tmp_path))

    manager = svc._coverage_trend_manager
    assert manager is not None
    assert len(manager.list_snapshots(limit=5)) == 1


def test_observe_low_coverage_categorizes_and_routes_alerts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A below-threshold coverage observation drives the full trend+alert wire:
    # slope/volatility/history enrichment plus per-alert categorization and
    # channel routing. The previously-unwired methods (calculate_trend_slope,
    # calculate_volatility_score, get_historical_data, categorize_alert,
    # get_routes_for_alert) all run here.
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    writer = MagicMock()
    writer.root = tmp_path / "obs"
    writer.write.return_value = ["x.json"]
    svc = _make_service(
        snapshot_builder=builder,
        artifact_writer=writer,
        coverage_signal_collector=_collector(CoverageSignal(status="ok", total_coverage_pct=10.0)),
    )

    with caplog.at_level("WARNING", logger=service_mod.logger.name):
        svc.observe(_make_context(tmp_path))

    # A below-threshold alert was generated, categorized, and routed.
    trend_log = [r.getMessage() for r in caplog.records if "Coverage trend:" in r.getMessage()]
    assert trend_log, "expected a coverage-trend warning for the below-threshold run"
    msg = trend_log[0]
    assert "slope=" in msg and "volatility=" in msg
    assert "routed=[" in msg
    # Default routing falls back to the operator channel.
    assert "operator" in msg


def test_observe_no_coverage_does_not_record_trend(tmp_path: Path) -> None:
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    writer = MagicMock()
    writer.root = tmp_path / "obs"
    writer.write.return_value = ["x.json"]
    svc = _make_service(
        snapshot_builder=builder,
        artifact_writer=writer,
        coverage_signal_collector=_collector(CoverageSignal(status="unavailable")),
    )

    svc.observe(_make_context(tmp_path))

    assert svc._coverage_trend_manager is not None
    assert svc._coverage_trend_manager.list_snapshots(limit=5) == []


def test_observe_optional_collectors_present(tmp_path: Path) -> None:
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    svc = _make_service(
        execution_health_collector=_collector(ExecutionHealthSignal()),
        backlog_collector=_collector(BacklogSignal()),
        lint_signal_collector=_collector(LintSignal(status="passed")),
        type_signal_collector=_collector(TypeSignal(status="passed")),
        ci_history_collector=_collector(CIHistorySignal(status="passed")),
        validation_history_collector=_collector(ValidationHistorySignal(status="passed")),
        architecture_signal_collector=_collector(ArchitectureSignal(status="ok")),
        benchmark_signal_collector=_collector(BenchmarkSignal(status="ok")),
        security_signal_collector=_collector(SecuritySignal(status="ok")),
        coverage_signal_collector=_collector(CoverageSignal(status="ok")),
        snapshot_builder=builder,
    )
    svc.observe(_make_context(tmp_path))
    signals = builder.build.call_args.kwargs["signals"]
    assert signals.lint_signal.status == "passed"
    assert signals.type_signal.status == "passed"
    assert signals.ci_history.status == "passed"
    assert signals.validation_history.status == "passed"
    assert signals.architecture_signal.status == "ok"
    assert signals.benchmark_signal.status == "ok"
    assert signals.security_signal.status == "ok"
    assert signals.coverage_signal.status == "ok"


def test_observe_optional_collectors_absent_use_defaults(tmp_path: Path) -> None:
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    svc = _make_service(snapshot_builder=builder)
    svc.observe(_make_context(tmp_path))
    signals = builder.build.call_args.kwargs["signals"]
    assert signals.lint_signal.status == "unavailable"
    assert signals.type_signal.status == "unavailable"
    assert signals.ci_history.status == "unavailable"
    assert signals.validation_history.status == "unavailable"
    assert signals.architecture_signal.status == "unavailable"
    assert signals.benchmark_signal.status == "unavailable"
    assert signals.security_signal.status == "unavailable"
    assert signals.coverage_signal.status == "unavailable"


def test_observe_required_collector_failure_raises_and_records(tmp_path: Path) -> None:
    svc = _make_service(repo_collector=_failing_collector(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        svc.observe(_make_context(tmp_path))


def test_observe_optional_collector_failure_uses_default_and_records(tmp_path: Path) -> None:
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    svc = _make_service(
        recent_commits_collector=_failing_collector(ValueError("commits-fail")),
        test_signal_collector=_failing_collector(KeyError("ts")),
        snapshot_builder=builder,
    )
    svc.observe(_make_context(tmp_path))
    kwargs = builder.build.call_args.kwargs
    errors = kwargs["collector_errors"]
    assert "recent_commits" in errors
    assert "commits-fail" in errors["recent_commits"]
    assert "test_signal" in errors
    # Defaults applied.
    assert kwargs["signals"].recent_commits == []
    assert kwargs["signals"].test_signal.status == "unknown"


def test_observe_optional_present_collector_failure_uses_default(tmp_path: Path) -> None:
    builder = MagicMock()
    builder.build.return_value = "BUILT"
    svc = _make_service(
        lint_signal_collector=_failing_collector(RuntimeError("lint-fail")),
        snapshot_builder=builder,
    )
    svc.observe(_make_context(tmp_path))
    kwargs = builder.build.call_args.kwargs
    assert "lint_signal" in kwargs["collector_errors"]
    assert kwargs["signals"].lint_signal.status == "unavailable"


def test_collect_required_propagates_and_records_error(tmp_path: Path) -> None:
    svc = _make_service()
    errors: dict[str, str] = {}
    collector = _failing_collector(RuntimeError("required-down"))
    with pytest.raises(RuntimeError, match="required-down"):
        svc._collect_required(collector, _make_context(tmp_path), "repo_context", errors)
    assert errors["repo_context"] == "required-down"


def test_collect_required_success_returns_result(tmp_path: Path) -> None:
    svc = _make_service()
    snap = _make_repo_snapshot()
    errors: dict[str, str] = {}
    result = svc._collect_required(
        _collector(snap), _make_context(tmp_path), "repo_context", errors
    )
    assert result is snap
    assert errors == {}


def test_collect_optional_returns_default_on_error(tmp_path: Path) -> None:
    svc = _make_service()
    errors: dict[str, str] = {}
    result = svc._collect_optional(
        _failing_collector(RuntimeError("opt-down")),
        _make_context(tmp_path),
        "todo_signal",
        errors,
        default="DEFAULT",
    )
    assert result == "DEFAULT"
    assert errors["todo_signal"] == "opt-down"


def test_collect_optional_success_returns_value(tmp_path: Path) -> None:
    svc = _make_service()
    errors: dict[str, str] = {}
    result = svc._collect_optional(
        _collector("VALUE"),
        _make_context(tmp_path),
        "todo_signal",
        errors,
        default="DEFAULT",
    )
    assert result == "VALUE"
    assert errors == {}


def test_new_observer_context_builds_expected_fields(tmp_path: Path) -> None:
    settings = MagicMock()
    exporter = MagicMock()
    ctx = new_observer_context(
        repo_path=tmp_path / "r",
        repo_name="myrepo",
        base_branch=None,
        settings=settings,
        source_command="cmd",
        commit_limit=3,
        hotspot_window=7,
        todo_limit=2,
        logs_root=tmp_path / "logs",
        metrics_exporter=exporter,
    )
    assert ctx.repo_name == "myrepo"
    assert ctx.base_branch is None
    assert ctx.commit_limit == 3
    assert ctx.hotspot_window == 7
    assert ctx.todo_limit == 2
    assert ctx.metrics_exporter is exporter
    assert ctx.observed_at.tzinfo is UTC
    assert ctx.run_id.startswith("obs_")
    # run_id is truncated to the trailing 31 characters.
    assert len(ctx.run_id) <= 31


def test_new_observer_context_run_id_uses_observed_at(tmp_path: Path, monkeypatch) -> None:
    fixed = datetime(2026, 1, 2, 3, 4, 5, 0xABCDE, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(service_mod, "datetime", _FixedDatetime)
    ctx = new_observer_context(
        repo_path=tmp_path,
        repo_name="r",
        base_branch="dev",
        settings=MagicMock(),
        source_command="cmd",
        commit_limit=1,
        hotspot_window=1,
        todo_limit=1,
        logs_root=tmp_path,
    )
    assert ctx.observed_at == fixed
    assert "20260102T030405Z" in ctx.run_id
    assert ctx.run_id.endswith("0abcde")
    assert ctx.metrics_exporter is None


def test_observer_context_is_frozen(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    with pytest.raises(Exception):
        ctx.repo_name = "other"  # type: ignore[misc]
