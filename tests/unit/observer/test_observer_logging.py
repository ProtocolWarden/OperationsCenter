# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.observer.models import (
    ArchitectureSignal,
    BacklogSignal,
    CheckSignal,
    CIHistorySignal,
    DependencyDriftSignal,
    ExecutionHealthSignal,
    LintSignal,
    RepoContextSnapshot,
    TodoSignal,
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
        repo_name="test-repo",
        base_branch="main",
        run_id="obs_test_run",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        source_command="test-observe",
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


def test_init_logs_required_collectors(caplog: pytest.LogCaptureFixture[str]) -> None:
    with caplog.at_level(logging.DEBUG):
        RepoObserverService(
            repo_collector=_collector(_make_repo_snapshot()),
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
        )

    assert "Initializing RepoObserverService" in caplog.text
    assert "Required collector: repo_collector" in caplog.text
    assert "Required collector: recent_commits_collector" in caplog.text
    assert "Required collector: file_hotspots_collector" in caplog.text
    assert "Required collector: test_signal_collector" in caplog.text
    assert "Required collector: dependency_drift_collector" in caplog.text
    assert "Required collector: todo_signal_collector" in caplog.text
    assert "RepoObserverService initialized: 6 required, 0 optional collectors" in caplog.text


def test_init_logs_optional_collectors_provided(caplog: pytest.LogCaptureFixture[str]) -> None:
    with caplog.at_level(logging.DEBUG):
        RepoObserverService(
            repo_collector=_collector(_make_repo_snapshot()),
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
            execution_health_collector=_collector(ExecutionHealthSignal()),
            lint_signal_collector=_collector(LintSignal(status="passed")),
        )

    assert "Optional collector: execution_health_collector" in caplog.text
    assert "Optional collector: lint_signal_collector" in caplog.text
    assert "RepoObserverService initialized: 6 required, 2 optional collectors" in caplog.text


def test_init_logs_optional_collectors_skipped(caplog: pytest.LogCaptureFixture[str]) -> None:
    with caplog.at_level(logging.DEBUG):
        RepoObserverService(
            repo_collector=_collector(_make_repo_snapshot()),
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
        )

    assert "Optional collector: execution_health_collector [SKIPPED]" in caplog.text
    assert "Optional collector: backlog_collector [SKIPPED]" in caplog.text
    assert "Optional collector: lint_signal_collector [SKIPPED]" in caplog.text


def test_observe_logs_start_and_context(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    assert "observe() starting for run_id=obs_test_run" in caplog.text
    assert "test-repo" in caplog.text
    assert "test-observe" in caplog.text


def test_observe_logs_required_collector_collection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    assert "Collecting required signal: repo_context" in caplog.text
    assert "✓ Collected repo_context" in caplog.text


def test_observe_logs_optional_collector_collection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        execution_health_collector=_collector(ExecutionHealthSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    assert "Collecting optional signal: execution_health" in caplog.text
    assert "✓ Collected execution_health" in caplog.text


def test_observe_logs_skipped_optional_collectors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    assert "Skipping execution_health collector (not provided)" in caplog.text
    assert "Skipping backlog collector (not provided)" in caplog.text
    assert "Skipping lint_signal collector (not provided)" in caplog.text


def test_observe_logs_completion(tmp_path: Path, caplog: pytest.LogCaptureFixture[str]) -> None:
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json", "snap.md"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.INFO):
        svc.observe(_make_context(tmp_path))

    assert "Snapshot complete: run_id=obs_test_run" in caplog.text
    assert "2 artifacts" in caplog.text


def test_observe_logs_optional_collector_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    failing_collector = MagicMock()
    failing_collector.collect.side_effect = RuntimeError("collection failed")

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=failing_collector,
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    assert "Optional collector 'recent_commits' failed" in caplog.text
    assert "collection failed" in caplog.text
    assert "Using default for recent_commits" in caplog.text


def test_observe_logs_required_collector_failure_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    failing_collector = MagicMock()
    failing_collector.collect.side_effect = RuntimeError("required failed")

    svc = RepoObserverService(
        repo_collector=failing_collector,
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            svc.observe(_make_context(tmp_path))

    assert "Required collector 'repo_context' failed" in caplog.text
    assert "required failed" in caplog.text


def test_new_observer_context_logs_creation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    with caplog.at_level(logging.DEBUG):
        new_observer_context(
            repo_path=tmp_path / "repo",
            repo_name="test-repo",
            base_branch="main",
            settings=MagicMock(),
            source_command="test",
            commit_limit=10,
            hotspot_window=20,
            todo_limit=5,
            logs_root=tmp_path / "logs",
        )

    assert "Creating observer context: repo=test-repo, branch=main" in caplog.text
    assert "Generated run_id:" in caplog.text
    assert "Observer context created:" in caplog.text


def test_new_observer_context_generates_run_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    with caplog.at_level(logging.DEBUG):
        context = new_observer_context(
            repo_path=tmp_path / "repo",
            repo_name="test-repo",
            base_branch="main",
            settings=MagicMock(),
            source_command="test",
            commit_limit=10,
            hotspot_window=20,
            todo_limit=5,
            logs_root=tmp_path / "logs",
        )

    assert context.run_id.startswith("obs_")
    assert context.run_id in caplog.text


def test_init_logs_all_optional_collectors_skipped(caplog: pytest.LogCaptureFixture[str]) -> None:
    """Verify all optional collectors log SKIPPED when not provided."""
    with caplog.at_level(logging.DEBUG):
        RepoObserverService(
            repo_collector=_collector(_make_repo_snapshot()),
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
        )

    # Verify all 11 optional collectors show SKIPPED
    assert "[SKIPPED]" in caplog.text
    skipped_count = caplog.text.count("[SKIPPED]")
    assert skipped_count >= 11, (
        f"Expected at least 11 optional collectors marked SKIPPED, got {skipped_count}"
    )


def test_collect_required_signal_logs_success_emoji(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify success emoji is logged when required signal collects successfully."""
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    # Verify checkmark emoji appears in collected messages
    assert "✓" in caplog.text


def test_collect_multiple_required_collectors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify all 6 required collectors are individually logged during collection."""
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    # Verify each required collector is logged
    required_names = [
        "repo_context",
        "recent_commits",
        "file_hotspots",
        "test_signal",
        "dependency_drift",
        "todo_signal",
    ]
    for name in required_names:
        assert name in caplog.text, f"Required collector {name} not found in logs"


def test_collect_multiple_optional_collectors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify multiple optional collectors are logged during collection."""

    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        execution_health_collector=_collector(ExecutionHealthSignal()),
        backlog_collector=_collector(BacklogSignal()),
        architecture_signal_collector=_collector(ArchitectureSignal(status="healthy")),
        ci_history_collector=_collector(CIHistorySignal(status="nominal")),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    # Verify provided optional collectors are logged
    optional_names = [
        "execution_health",
        "backlog",
        "architecture_signal",
        "ci_history",
    ]
    for name in optional_names:
        assert name in caplog.text, f"Optional collector {name} not found in logs"


def test_observe_logs_artifact_count(tmp_path: Path, caplog: pytest.LogCaptureFixture[str]) -> None:
    """Verify artifact count is logged in completion message."""
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    # Multiple artifacts
    writer.write.return_value = ["snap.json", "snap.md", "snap.yaml"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.INFO):
        svc.observe(_make_context(tmp_path))

    # Verify artifact count appears in logs
    assert "3 artifacts" in caplog.text


def test_optional_collector_skipped_not_provided_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify skipped optional collectors log 'not provided' message."""
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    # Verify "not provided" messages appear
    assert "(not provided)" in caplog.text


def test_required_collector_failure_includes_error_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify required collector failure logs include error message."""
    failing_collector = MagicMock()
    error_msg = "database connection failed"
    failing_collector.collect.side_effect = RuntimeError(error_msg)

    svc = RepoObserverService(
        repo_collector=failing_collector,
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            svc.observe(_make_context(tmp_path))

    # Verify error message is included in logs
    assert error_msg in caplog.text


def test_optional_collector_uses_default_on_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify optional collector failure logs default usage."""
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    failing_health_collector = MagicMock()
    failing_health_collector.collect.side_effect = RuntimeError("health check failed")

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        execution_health_collector=failing_health_collector,
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    with caplog.at_level(logging.DEBUG):
        svc.observe(_make_context(tmp_path))

    # Verify default usage is logged
    assert "Using default" in caplog.text


def test_logging_includes_repo_context_details(
    tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
) -> None:
    """Verify logging includes repository context details."""
    builder = MagicMock()
    builder.build.return_value = "SNAPSHOT"
    writer = MagicMock()
    writer.write.return_value = ["snap.json"]

    svc = RepoObserverService(
        repo_collector=_collector(_make_repo_snapshot()),
        recent_commits_collector=_collector([]),
        file_hotspots_collector=_collector([]),
        test_signal_collector=_collector(CheckSignal(status="unknown")),
        dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
        todo_signal_collector=_collector(TodoSignal()),
        snapshot_builder=builder,
        artifact_writer=writer,
    )

    context = _make_context(tmp_path)

    with caplog.at_level(logging.DEBUG):
        svc.observe(context)

    # Verify context details in logs
    assert context.repo_name in caplog.text
    assert context.source_command in caplog.text
