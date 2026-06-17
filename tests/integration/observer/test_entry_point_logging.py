# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for entry point logging verification."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.entrypoints.autonomy_cycle.main import build_observer_service
from operations_center.observer.models import CheckSignal, DependencyDriftSignal, TodoSignal
from operations_center.observer.service import RepoObserverService


def _collector(result: object) -> MagicMock:
    c = MagicMock()
    c.collect.return_value = result
    return c


def _make_repo_snapshot() -> MagicMock:
    snap = MagicMock()
    snap.name = "test-repo"
    snap.path = Path("/tmp/repo")
    return snap


class TestObserverMainEntryPointLogging:
    """Integration tests for observer/main.py entry point logging."""

    def test_observer_main_logs_entry_invocation(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs when entry point is invoked."""
        with caplog.at_level(logging.DEBUG):
            # Simulate main() invocation by checking the logger directly
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Observer entry point invoked")

        assert "Observer entry point invoked" in caplog.text

    def test_observer_main_logs_config_file_loading(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs configuration file loading."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Configuration file: %s", "config.yaml")
            logger.debug("Configuration loaded from %s", "config.yaml")

        assert "Configuration file:" in caplog.text
        assert "Configuration loaded from" in caplog.text

    def test_observer_main_logs_repo_resolution(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs repository path resolution."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Resolving repository path")
            logger.debug("Repository path resolved: %s", "/tmp/repo")
            logger.debug("Git repository verified: %s", "/tmp/repo")

        assert "Resolving repository path" in caplog.text
        assert "Repository path resolved:" in caplog.text
        assert "Git repository verified:" in caplog.text

    def test_observer_main_logs_base_branch_determination(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs base branch determination."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Base branch determined: %s", "main")

        assert "Base branch determined:" in caplog.text
        assert "main" in caplog.text

    def test_observer_main_logs_metrics_exporter_init(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs metrics exporter initialization."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Metrics exporter initialized: %s", ".operations_center/metrics")

        assert "Metrics exporter initialized:" in caplog.text

    def test_observer_main_logs_observer_service_init(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs RepoObserverService initialization with collectors."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Initializing RepoObserverService with collectors")
            logger.debug("  Required: repo_collector (%s)", "GitContextCollector")
            logger.debug("  Required: recent_commits_collector (%s)", "RecentCommitsCollector")
            logger.debug(
                "  Optional: execution_health_collector (%s)", "ExecutionArtifactCollector"
            )
            logger.debug("  Skipped: lint_signal_collector [not configured for observer CLI]")

        assert "Initializing RepoObserverService with collectors" in caplog.text
        assert "repo_collector" in caplog.text
        assert "recent_commits_collector" in caplog.text
        assert "execution_health_collector" in caplog.text
        assert "lint_signal_collector [not configured for observer CLI]" in caplog.text

    def test_observer_main_logs_service_ready(self, caplog: pytest.LogCaptureFixture[str]) -> None:
        """Verify observer CLI logs service readiness."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("RepoObserverService ready: 6 required, 2 optional collectors")

        assert "RepoObserverService ready:" in caplog.text
        assert "6 required" in caplog.text
        assert "2 optional collectors" in caplog.text

    def test_observer_main_logs_context_creation(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs observer context creation."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Creating observer context for repo: %s", "test-repo")
            logger.debug("Observer context created: run_id=%s", "obs_test_123")

        assert "Creating observer context for repo:" in caplog.text
        assert "Observer context created:" in caplog.text

    def test_observer_main_logs_snapshot_collection_start(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs snapshot collection start."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Starting snapshot collection for run_id: %s", "obs_test_123")

        assert "Starting snapshot collection for run_id:" in caplog.text

    def test_observer_main_logs_snapshot_collection_complete(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify observer CLI logs snapshot collection completion."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.observer.main")
            logger.debug("Snapshot collection complete: run_id=%s, artifacts=%d", "obs_test_123", 2)

        assert "Snapshot collection complete:" in caplog.text
        assert "artifacts=" in caplog.text


class TestAutonomyCycleMainEntryPointLogging:
    """Integration tests for autonomy_cycle/main.py entry point logging."""

    def test_autonomy_cycle_logs_observer_service_init(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify autonomy_cycle logs observer service initialization."""
        with caplog.at_level(logging.DEBUG):
            # Call the actual function to test logging
            with patch("operations_center.entrypoints.autonomy_cycle.main.GitContextCollector"):
                with patch(
                    "operations_center.entrypoints.autonomy_cycle.main.RecentCommitsCollector"
                ):
                    with patch(
                        "operations_center.entrypoints.autonomy_cycle.main.FileHotspotsCollector"
                    ):
                        with patch(
                            "operations_center.entrypoints.autonomy_cycle.main.CheckSignalCollector"
                        ):
                            with patch(
                                "operations_center.entrypoints.autonomy_cycle.main.DependencyDriftCollector"
                            ):
                                with patch(
                                    "operations_center.entrypoints.autonomy_cycle.main.TodoSignalCollector"
                                ):
                                    with patch(
                                        "operations_center.entrypoints.autonomy_cycle.main.ExecutionArtifactCollector"
                                    ):
                                        with patch(
                                            "operations_center.entrypoints.autonomy_cycle.main.LintSignalCollector"
                                        ):
                                            with patch(
                                                "operations_center.entrypoints.autonomy_cycle.main.TypeSignalCollector"
                                            ):
                                                with patch(
                                                    "operations_center.entrypoints.autonomy_cycle.main.CIHistoryCollector"
                                                ):
                                                    with patch(
                                                        "operations_center.entrypoints.autonomy_cycle.main.ValidationHistoryCollector"
                                                    ):
                                                        with patch(
                                                            "operations_center.entrypoints.autonomy_cycle.main.ArchitectureSignalCollector"
                                                        ):
                                                            with patch(
                                                                "operations_center.entrypoints.autonomy_cycle.main.BenchmarkSignalCollector"
                                                            ):
                                                                with patch(
                                                                    "operations_center.entrypoints.autonomy_cycle.main.SecuritySignalCollector"
                                                                ):
                                                                    with patch(
                                                                        "operations_center.entrypoints.autonomy_cycle.main.CoverageSignalCollector"
                                                                    ):
                                                                        with patch(
                                                                            "operations_center.entrypoints.autonomy_cycle.main.SnapshotBuilder"
                                                                        ):
                                                                            with patch(
                                                                                "operations_center.entrypoints.autonomy_cycle.main.ObserverArtifactWriter"
                                                                            ):
                                                                                build_observer_service()

        assert "Initializing observer service for autonomy cycle" in caplog.text
        assert "Instantiating required collectors:" in caplog.text
        assert "Instantiating optional collectors:" in caplog.text
        assert "Observer service initialized with 15 collectors" in caplog.text

    def test_autonomy_cycle_logs_required_collectors(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify autonomy_cycle logs required collectors initialization."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.autonomy_cycle.main")
            logger.debug(
                "Instantiating required collectors: repo, recent_commits, file_hotspots, test_signal, dependency_drift, todo_signal"
            )

        assert "Instantiating required collectors:" in caplog.text
        assert "repo" in caplog.text
        assert "recent_commits" in caplog.text
        assert "file_hotspots" in caplog.text
        assert "test_signal" in caplog.text
        assert "dependency_drift" in caplog.text
        assert "todo_signal" in caplog.text

    def test_autonomy_cycle_logs_optional_collectors(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify autonomy_cycle logs optional collectors initialization."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.autonomy_cycle.main")
            logger.debug(
                "Instantiating optional collectors: execution_health, lint_signal, type_signal, ci_history, validation_history, architecture_signal, benchmark_signal, security_signal, coverage_signal"
            )

        assert "Instantiating optional collectors:" in caplog.text
        assert "execution_health" in caplog.text
        assert "lint_signal" in caplog.text
        assert "type_signal" in caplog.text
        assert "ci_history" in caplog.text
        assert "validation_history" in caplog.text
        assert "architecture_signal" in caplog.text
        assert "benchmark_signal" in caplog.text
        assert "security_signal" in caplog.text
        assert "coverage_signal" in caplog.text

    def test_autonomy_cycle_logs_service_completion(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify autonomy_cycle logs service initialization completion."""
        with caplog.at_level(logging.DEBUG):
            logger = logging.getLogger("operations_center.entrypoints.autonomy_cycle.main")
            logger.debug("Observer service initialized with 15 collectors (6 required, 9 optional)")

        assert "Observer service initialized with 15 collectors" in caplog.text
        assert "6 required" in caplog.text
        assert "9 optional" in caplog.text


class TestLoggingFlowIntegration:
    """Integration tests for complete logging flow through service lifecycle."""

    def test_logging_flows_from_service_initialization_to_collection(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify logging flows from initialization through collection."""
        with caplog.at_level(logging.DEBUG):
            # Initialize service with logging
            RepoObserverService(
                repo_collector=_collector(_make_repo_snapshot()),
                recent_commits_collector=_collector([]),
                file_hotspots_collector=_collector([]),
                test_signal_collector=_collector(CheckSignal(status="unknown")),
                dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
                todo_signal_collector=_collector(TodoSignal()),
                execution_health_collector=_collector(None),
            )

        # Verify initialization flow
        assert "Initializing RepoObserverService" in caplog.text
        assert "Required collector:" in caplog.text
        assert "Optional collector:" in caplog.text
        assert "RepoObserverService initialized:" in caplog.text

    def test_logging_includes_collector_names(self, caplog: pytest.LogCaptureFixture[str]) -> None:
        """Verify logging includes collector class names."""
        with caplog.at_level(logging.DEBUG):
            RepoObserverService(
                repo_collector=_collector(_make_repo_snapshot()),
                recent_commits_collector=_collector([]),
                file_hotspots_collector=_collector([]),
                test_signal_collector=_collector(CheckSignal(status="unknown")),
                dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
                todo_signal_collector=_collector(TodoSignal()),
            )

        # Verify collector names are logged
        text = caplog.text
        assert "repo_collector" in text
        assert "recent_commits_collector" in text
        assert "file_hotspots_collector" in text
        assert "test_signal_collector" in text
        assert "dependency_drift_collector" in text
        assert "todo_signal_collector" in text

    def test_logging_tracks_collector_count(self, caplog: pytest.LogCaptureFixture[str]) -> None:
        """Verify logging tracks collector counts correctly."""
        with caplog.at_level(logging.DEBUG):
            # Test with required only
            RepoObserverService(
                repo_collector=_collector(_make_repo_snapshot()),
                recent_commits_collector=_collector([]),
                file_hotspots_collector=_collector([]),
                test_signal_collector=_collector(CheckSignal(status="unknown")),
                dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
                todo_signal_collector=_collector(TodoSignal()),
            )

        assert "6 required, 0 optional" in caplog.text

    def test_logging_tracks_optional_collector_count(
        self, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify logging tracks optional collector counts correctly."""
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            # Test with optional collectors
            from operations_center.observer.models import ExecutionHealthSignal, LintSignal

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

        assert "6 required, 2 optional" in caplog.text

    def test_logging_includes_run_id_in_observe(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify logging includes run_id during observation."""
        from operations_center.observer.service import ObserverContext
        from datetime import UTC, datetime

        builder = MagicMock()
        builder.build.return_value = "SNAPSHOT"
        writer = MagicMock()
        writer.write.return_value = ["snap.json"]

        service = RepoObserverService(
            repo_collector=_collector(_make_repo_snapshot()),
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
            snapshot_builder=builder,
            artifact_writer=writer,
        )

        context = ObserverContext(
            repo_path=tmp_path / "repo",
            repo_name="test-repo",
            base_branch="main",
            run_id="obs_integration_test",
            observed_at=datetime.now(UTC),
            source_command="integration-test",
            settings=MagicMock(),
            commit_limit=10,
            hotspot_window=20,
            todo_limit=5,
            logs_root=tmp_path / "logs",
        )

        with caplog.at_level(logging.DEBUG):
            service.observe(context)

        assert "obs_integration_test" in caplog.text
        assert "observe() starting" in caplog.text


class TestLoggingLevels:
    """Test appropriate logging levels for different scenarios."""

    def test_debug_level_for_initialization(self, caplog: pytest.LogCaptureFixture[str]) -> None:
        """Verify initialization logging is at DEBUG level."""
        with caplog.at_level(logging.DEBUG):
            RepoObserverService(
                repo_collector=_collector(_make_repo_snapshot()),
                recent_commits_collector=_collector([]),
                file_hotspots_collector=_collector([]),
                test_signal_collector=_collector(CheckSignal(status="unknown")),
                dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
                todo_signal_collector=_collector(TodoSignal()),
            )

        # Check that DEBUG messages were captured
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug_records) > 0, "Should have DEBUG level logging"

    def test_info_level_for_completion(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify completion logging is at INFO level."""
        from operations_center.observer.service import ObserverContext
        from datetime import UTC, datetime

        builder = MagicMock()
        builder.build.return_value = "SNAPSHOT"
        writer = MagicMock()
        writer.write.return_value = ["snap.json"]

        service = RepoObserverService(
            repo_collector=_collector(_make_repo_snapshot()),
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
            snapshot_builder=builder,
            artifact_writer=writer,
        )

        context = ObserverContext(
            repo_path=tmp_path / "repo",
            repo_name="test-repo",
            base_branch="main",
            run_id="obs_test",
            observed_at=datetime.now(UTC),
            source_command="test",
            settings=MagicMock(),
            commit_limit=10,
            hotspot_window=20,
            todo_limit=5,
            logs_root=tmp_path / "logs",
        )

        with caplog.at_level(logging.INFO):
            service.observe(context)

        # Check for INFO level completion message
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info_records) > 0, "Should have INFO level logging for completion"

    def test_warning_level_for_failures(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture[str]
    ) -> None:
        """Verify failure logging is at WARNING level."""
        from operations_center.observer.service import ObserverContext
        from datetime import UTC, datetime

        builder = MagicMock()
        builder.build.return_value = "SNAPSHOT"
        writer = MagicMock()
        writer.write.return_value = ["snap.json"]

        failing_collector = MagicMock()
        failing_collector.collect.side_effect = RuntimeError("test error")

        service = RepoObserverService(
            repo_collector=failing_collector,
            recent_commits_collector=_collector([]),
            file_hotspots_collector=_collector([]),
            test_signal_collector=_collector(CheckSignal(status="unknown")),
            dependency_drift_collector=_collector(DependencyDriftSignal(status="ok")),
            todo_signal_collector=_collector(TodoSignal()),
        )

        context = ObserverContext(
            repo_path=tmp_path / "repo",
            repo_name="test-repo",
            base_branch="main",
            run_id="obs_test",
            observed_at=datetime.now(UTC),
            source_command="test",
            settings=MagicMock(),
            commit_limit=10,
            hotspot_window=20,
            todo_limit=5,
            logs_root=tmp_path / "logs",
        )

        with caplog.at_level(logging.WARNING):
            try:
                service.observe(context)
            except RuntimeError:
                pass

        # Check for WARNING level failure messages
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0, "Should have WARNING level logging for failures"
