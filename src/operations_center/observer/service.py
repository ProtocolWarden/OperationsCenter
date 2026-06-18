# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from operations_center.config import Settings
from operations_center.observer.artifact_writer import ObserverArtifactWriter
from operations_center.observer.exporters import ValidationMetricsExporter
from operations_center.observer.models import (
    ArchitectureSignal,
    BacklogSignal,
    BenchmarkSignal,
    CheckSignal,
    CIHistorySignal,
    CoverageSignal,
    DependencyDriftSignal,
    ExecutionHealthSignal,
    FlakyTestSignal,
    LintSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    TodoSignal,
    TypeSignal,
    ValidationHistorySignal,
)
from operations_center.observer.query import TestSignalQuery
from operations_center.observer.snapshot_builder import SnapshotBuilder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ObserverContext:
    repo_path: Path
    repo_name: str
    base_branch: str | None
    run_id: str
    observed_at: datetime
    source_command: str
    settings: Settings
    commit_limit: int
    hotspot_window: int
    todo_limit: int
    logs_root: Path
    metrics_exporter: ValidationMetricsExporter | None = None


class RepoSignalCollector(Protocol):
    def collect(self, context: ObserverContext) -> Any: ...


class RepoObserverService:
    def __init__(
        self,
        *,
        repo_collector: RepoSignalCollector,
        recent_commits_collector: RepoSignalCollector,
        file_hotspots_collector: RepoSignalCollector,
        test_signal_collector: RepoSignalCollector,
        dependency_drift_collector: RepoSignalCollector,
        todo_signal_collector: RepoSignalCollector,
        execution_health_collector: RepoSignalCollector | None = None,
        backlog_collector: RepoSignalCollector | None = None,
        lint_signal_collector: RepoSignalCollector | None = None,
        type_signal_collector: RepoSignalCollector | None = None,
        ci_history_collector: RepoSignalCollector | None = None,
        validation_history_collector: RepoSignalCollector | None = None,
        architecture_signal_collector: RepoSignalCollector | None = None,
        benchmark_signal_collector: RepoSignalCollector | None = None,
        security_signal_collector: RepoSignalCollector | None = None,
        coverage_signal_collector: RepoSignalCollector | None = None,
        flaky_test_collector: RepoSignalCollector | None = None,
        snapshot_builder: SnapshotBuilder | None = None,
        artifact_writer: ObserverArtifactWriter | None = None,
        metrics_exporter: ValidationMetricsExporter | None = None,
    ) -> None:
        logger.debug("Initializing RepoObserverService")
        self.repo_collector = repo_collector
        logger.debug("  Required collector: repo_collector (%s)", type(repo_collector).__name__)
        self.recent_commits_collector = recent_commits_collector
        logger.debug(
            "  Required collector: recent_commits_collector (%s)",
            type(recent_commits_collector).__name__,
        )
        self.file_hotspots_collector = file_hotspots_collector
        logger.debug(
            "  Required collector: file_hotspots_collector (%s)",
            type(file_hotspots_collector).__name__,
        )
        self.test_signal_collector = test_signal_collector
        logger.debug(
            "  Required collector: test_signal_collector (%s)", type(test_signal_collector).__name__
        )
        self.dependency_drift_collector = dependency_drift_collector
        logger.debug(
            "  Required collector: dependency_drift_collector (%s)",
            type(dependency_drift_collector).__name__,
        )
        self.todo_signal_collector = todo_signal_collector
        logger.debug(
            "  Required collector: todo_signal_collector (%s)", type(todo_signal_collector).__name__
        )
        self.execution_health_collector = execution_health_collector
        if execution_health_collector is not None:
            logger.debug(
                "  Optional collector: execution_health_collector (%s)",
                type(execution_health_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: execution_health_collector [SKIPPED]")
        self.backlog_collector = backlog_collector
        if backlog_collector is not None:
            logger.debug(
                "  Optional collector: backlog_collector (%s)", type(backlog_collector).__name__
            )
        else:
            logger.debug("  Optional collector: backlog_collector [SKIPPED]")
        self.lint_signal_collector = lint_signal_collector
        if lint_signal_collector is not None:
            logger.debug(
                "  Optional collector: lint_signal_collector (%s)",
                type(lint_signal_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: lint_signal_collector [SKIPPED]")
        self.type_signal_collector = type_signal_collector
        if type_signal_collector is not None:
            logger.debug(
                "  Optional collector: type_signal_collector (%s)",
                type(type_signal_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: type_signal_collector [SKIPPED]")
        self.ci_history_collector = ci_history_collector
        if ci_history_collector is not None:
            logger.debug(
                "  Optional collector: ci_history_collector (%s)",
                type(ci_history_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: ci_history_collector [SKIPPED]")
        self.validation_history_collector = validation_history_collector
        if validation_history_collector is not None:
            logger.debug(
                "  Optional collector: validation_history_collector (%s)",
                type(validation_history_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: validation_history_collector [SKIPPED]")
        self.architecture_signal_collector = architecture_signal_collector
        if architecture_signal_collector is not None:
            logger.debug(
                "  Optional collector: architecture_signal_collector (%s)",
                type(architecture_signal_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: architecture_signal_collector [SKIPPED]")
        self.benchmark_signal_collector = benchmark_signal_collector
        if benchmark_signal_collector is not None:
            logger.debug(
                "  Optional collector: benchmark_signal_collector (%s)",
                type(benchmark_signal_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: benchmark_signal_collector [SKIPPED]")
        self.security_signal_collector = security_signal_collector
        if security_signal_collector is not None:
            logger.debug(
                "  Optional collector: security_signal_collector (%s)",
                type(security_signal_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: security_signal_collector [SKIPPED]")
        self.coverage_signal_collector = coverage_signal_collector
        if coverage_signal_collector is not None:
            logger.debug(
                "  Optional collector: coverage_signal_collector (%s)",
                type(coverage_signal_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: coverage_signal_collector [SKIPPED]")
        self.flaky_test_collector = flaky_test_collector
        if flaky_test_collector is not None:
            logger.debug(
                "  Optional collector: flaky_test_collector (%s)",
                type(flaky_test_collector).__name__,
            )
        else:
            logger.debug("  Optional collector: flaky_test_collector [SKIPPED]")
        self.snapshot_builder = snapshot_builder or SnapshotBuilder()
        logger.debug(
            "  Infrastructure: snapshot_builder (%s)", type(self.snapshot_builder).__name__
        )
        self.artifact_writer = artifact_writer or ObserverArtifactWriter()
        logger.debug("  Infrastructure: artifact_writer (%s)", type(self.artifact_writer).__name__)
        # Coverage trend + alert engines (CoverageTrendManager / CoverageAlertManager
        # from #279) live behind this. They were built + tested but never driven;
        # default-construct one rooted under the observer artifact dir so each
        # observation records coverage history and computes trends/regressions/alerts.
        self._coverage_trend_manager = self._build_coverage_trend_manager()
        self.metrics_exporter = metrics_exporter
        if metrics_exporter is not None:
            logger.debug("  Infrastructure: metrics_exporter (%s)", type(metrics_exporter).__name__)
        required_count = 6
        optional_count = sum(
            1
            for c in [
                execution_health_collector,
                backlog_collector,
                lint_signal_collector,
                type_signal_collector,
                ci_history_collector,
                validation_history_collector,
                architecture_signal_collector,
                benchmark_signal_collector,
                security_signal_collector,
                coverage_signal_collector,
                flaky_test_collector,
            ]
            if c is not None
        )
        logger.info(
            "RepoObserverService initialized: %d required, %d optional collectors",
            required_count,
            optional_count,
        )

    def observe(self, context: ObserverContext) -> tuple[RepoStateSnapshot, list[str]]:
        logger.debug(
            "observe() starting for run_id=%s, repo=%s, source=%s",
            context.run_id,
            context.repo_name,
            context.source_command,
        )
        collector_errors: dict[str, str] = {}
        repo_snapshot = self._collect_required(
            self.repo_collector, context, "repo_context", collector_errors
        )
        recent_commits = self._collect_optional(
            self.recent_commits_collector, context, "recent_commits", collector_errors, default=[]
        )
        file_hotspots = self._collect_optional(
            self.file_hotspots_collector, context, "file_hotspots", collector_errors, default=[]
        )
        test_signal = self._collect_optional(
            self.test_signal_collector,
            context,
            "test_signal",
            collector_errors,
            default=CheckSignal(status="unknown"),
        )
        dependency_drift = self._collect_optional(
            self.dependency_drift_collector,
            context,
            "dependency_drift",
            collector_errors,
            default=DependencyDriftSignal(status="not_available"),
        )
        todo_signal = self._collect_optional(
            self.todo_signal_collector,
            context,
            "todo_signal",
            collector_errors,
            default=TodoSignal(),
        )
        execution_health = (
            self._collect_optional(
                self.execution_health_collector,
                context,
                "execution_health",
                collector_errors,
                default=ExecutionHealthSignal(),
            )
            if self.execution_health_collector is not None
            else (
                logger.debug("Skipping execution_health collector (not provided)"),
                ExecutionHealthSignal(),
            )[1]
        )
        backlog = (
            self._collect_optional(
                self.backlog_collector,
                context,
                "backlog",
                collector_errors,
                default=BacklogSignal(),
            )
            if self.backlog_collector is not None
            else (logger.debug("Skipping backlog collector (not provided)"), BacklogSignal())[1]
        )
        lint_signal = (
            self._collect_optional(
                self.lint_signal_collector,
                context,
                "lint_signal",
                collector_errors,
                default=LintSignal(status="unavailable"),
            )
            if self.lint_signal_collector is not None
            else (
                logger.debug("Skipping lint_signal collector (not provided)"),
                LintSignal(status="unavailable"),
            )[1]
        )
        type_signal = (
            self._collect_optional(
                self.type_signal_collector,
                context,
                "type_signal",
                collector_errors,
                default=TypeSignal(status="unavailable"),
            )
            if self.type_signal_collector is not None
            else (
                logger.debug("Skipping type_signal collector (not provided)"),
                TypeSignal(status="unavailable"),
            )[1]
        )
        ci_history = (
            self._collect_optional(
                self.ci_history_collector,
                context,
                "ci_history",
                collector_errors,
                default=CIHistorySignal(status="unavailable"),
            )
            if self.ci_history_collector is not None
            else (
                logger.debug("Skipping ci_history collector (not provided)"),
                CIHistorySignal(status="unavailable"),
            )[1]
        )
        validation_history = (
            self._collect_optional(
                self.validation_history_collector,
                context,
                "validation_history",
                collector_errors,
                default=ValidationHistorySignal(status="unavailable"),
            )
            if self.validation_history_collector is not None
            else (
                logger.debug("Skipping validation_history collector (not provided)"),
                ValidationHistorySignal(status="unavailable"),
            )[1]
        )
        architecture_signal = (
            self._collect_optional(
                self.architecture_signal_collector,
                context,
                "architecture_signal",
                collector_errors,
                default=ArchitectureSignal(status="unavailable"),
            )
            if self.architecture_signal_collector is not None
            else (
                logger.debug("Skipping architecture_signal collector (not provided)"),
                ArchitectureSignal(status="unavailable"),
            )[1]
        )
        benchmark_signal = (
            self._collect_optional(
                self.benchmark_signal_collector,
                context,
                "benchmark_signal",
                collector_errors,
                default=BenchmarkSignal(status="unavailable"),
            )
            if self.benchmark_signal_collector is not None
            else (
                logger.debug("Skipping benchmark_signal collector (not provided)"),
                BenchmarkSignal(status="unavailable"),
            )[1]
        )
        security_signal = (
            self._collect_optional(
                self.security_signal_collector,
                context,
                "security_signal",
                collector_errors,
                default=SecuritySignal(status="unavailable"),
            )
            if self.security_signal_collector is not None
            else (
                logger.debug("Skipping security_signal collector (not provided)"),
                SecuritySignal(status="unavailable"),
            )[1]
        )
        coverage_signal = (
            self._collect_optional(
                self.coverage_signal_collector,
                context,
                "coverage_signal",
                collector_errors,
                default=CoverageSignal(status="unavailable"),
            )
            if self.coverage_signal_collector is not None
            else (
                logger.debug("Skipping coverage_signal collector (not provided)"),
                CoverageSignal(status="unavailable"),
            )[1]
        )
        flaky_test_signal = (
            self._collect_optional(
                self.flaky_test_collector,
                context,
                "flaky_test_signal",
                collector_errors,
                default=FlakyTestSignal(status="unavailable"),
            )
            if self.flaky_test_collector is not None
            else (
                logger.debug("Skipping flaky_test_signal collector (not provided)"),
                FlakyTestSignal(status="unavailable"),
            )[1]
        )

        # Drive the coverage trend + alert engines from this run's live coverage.
        self._record_coverage_trend(coverage_signal, context)

        signals = RepoSignalsSnapshot(
            recent_commits=recent_commits,
            file_hotspots=file_hotspots,
            test_signal=test_signal,
            dependency_drift=dependency_drift,
            todo_signal=todo_signal,
            execution_health=execution_health,
            backlog=backlog,
            lint_signal=lint_signal,
            type_signal=type_signal,
            ci_history=ci_history,
            validation_history=validation_history,
            architecture_signal=architecture_signal,
            benchmark_signal=benchmark_signal,
            security_signal=security_signal,
            coverage_signal=coverage_signal,
            flaky_test_signal=flaky_test_signal,
        )
        snapshot = self.snapshot_builder.build(
            run_id=context.run_id,
            observed_at=context.observed_at,
            source_command=context.source_command,
            repo=repo_snapshot,
            signals=signals,
            collector_errors=collector_errors,
        )
        logger.debug("Aggregating %d signals into snapshot", 16)
        artifacts = self.artifact_writer.write(snapshot)
        logger.info(
            "Snapshot complete: run_id=%s, %d artifacts, %d collector errors",
            context.run_id,
            len(artifacts),
            len(collector_errors),
        )
        return snapshot, artifacts

    def _build_coverage_trend_manager(self) -> Any:
        """Default-construct a CoverageTrendManager rooted under the observer
        artifact dir. Best-effort: returns None if the engine can't be built."""
        try:
            from operations_center.observer.coverage_trend_manager import (
                CoverageTrendManager,
            )

            return CoverageTrendManager.create_local(
                root=self.artifact_writer.root / "coverage-trends"
            )
        except Exception as exc:  # noqa: BLE001 — feature is best-effort
            logger.debug("coverage trend manager unavailable: %s", exc)
            return None

    def _record_coverage_trend(
        self, coverage_signal: CoverageSignal, context: ObserverContext
    ) -> None:
        """Drive the coverage trend + alert engines from this run's coverage.

        Bridges the live CoverageSignal into a CoverageSnapshot, records it to
        CoverageTrendManager (building the history its trend analysis needs),
        computes the trend + a regression check, runs CoverageAlertManager, and
        persists the trend + alerts — logging any regression/alert. This is the
        live integration for the CoverageTrendManager / CoverageAlertManager
        engines (#279), which were built and tested but never driven.

        Best-effort: coverage trend/alerting must never break an observation."""
        manager = self._coverage_trend_manager
        if manager is None or getattr(coverage_signal, "status", "") == "unavailable":
            return
        pct = getattr(coverage_signal, "total_coverage_pct", None)
        if pct is None:
            return
        try:
            from operations_center.observer.coverage_alerting import CoverageAlertManager
            from operations_center.observer.coverage_models import CoverageSnapshot

            snapshot = CoverageSnapshot(
                timestamp=context.observed_at,
                run_id=context.run_id,
                source="coverage.py",
                # The CoverageSignal exposes a single overall (line-based) figure;
                # use it for all three metrics as the best available approximation.
                overall_statement_coverage_pct=float(pct),
                overall_branch_coverage_pct=float(pct),
                overall_line_coverage_pct=float(pct),
            )
            manager.save_snapshot(snapshot)
            trend = manager.compute_trend_analysis(
                metric_type="line", granularity="repository", window_days=7
            )
            manager.save_trend_analysis(trend)
            regressed = manager.detect_regression(snapshot, metric_type="line")
            alerts = CoverageAlertManager().generate_alerts(snapshot, trend_analysis=trend)
            for alert in alerts:
                manager.save_alert(alert)
            if regressed or alerts:
                logger.warning(
                    "Coverage trend: run=%s coverage=%.1f%% regressed=%s alerts=%d",
                    context.run_id,
                    float(pct),
                    regressed,
                    len(alerts),
                )
        except Exception as exc:  # noqa: BLE001 — trend/alerting is best-effort
            logger.debug("coverage trend recording skipped: %s", exc)

    def query(self, root: Path | None = None) -> TestSignalQuery:
        """Create a query API for test signal visibility.

        Returns a TestSignalQuery instance that can be used to:
        - Retrieve individual test signals by run_id or recency
        - Analyze coverage trends over time
        - Summarize failure reasons across snapshots
        - Detect test status stability and regression

        Args:
            root: Override snapshot root directory (default uses artifact_writer root)

        Returns:
            TestSignalQuery instance ready for autonomy consumption
        """
        return TestSignalQuery(root=root or self.artifact_writer.root)

    def _collect_required(
        self,
        collector: RepoSignalCollector,
        context: ObserverContext,
        name: str,
        collector_errors: dict[str, str],
    ) -> RepoContextSnapshot:
        logger.debug("Collecting required signal: %s", name)
        try:
            result = collector.collect(context)
            logger.debug("  ✓ Collected %s", name)
        except Exception as exc:
            logger.warning("Required collector %r failed: %s", name, exc)
            collector_errors[name] = str(exc)
            raise
        return result

    def _collect_optional(
        self,
        collector: RepoSignalCollector,
        context: ObserverContext,
        name: str,
        collector_errors: dict[str, str],
        *,
        default: Any,
    ) -> Any:
        logger.debug("Collecting optional signal: %s", name)
        try:
            result = collector.collect(context)
            logger.debug("  ✓ Collected %s", name)
            return result
        except Exception as exc:
            logger.warning("Optional collector %r failed: %s", name, exc)
            collector_errors[name] = str(exc)
            logger.debug("  ? Using default for %s", name)
            return default


def new_observer_context(
    *,
    repo_path: Path,
    repo_name: str,
    base_branch: str | None,
    settings: Settings,
    source_command: str,
    commit_limit: int,
    hotspot_window: int,
    todo_limit: int,
    logs_root: Path,
    metrics_exporter: ValidationMetricsExporter | None = None,
) -> ObserverContext:
    logger.debug("Creating observer context: repo=%s, branch=%s", repo_name, base_branch)
    observed_at = datetime.now(UTC)
    run_id = f"obs_{observed_at.strftime('%Y%m%dT%H%M%SZ')}_{observed_at.microsecond:06x}"[-31:]
    logger.debug("  Generated run_id: %s", run_id)
    context = ObserverContext(
        repo_path=repo_path,
        repo_name=repo_name,
        base_branch=base_branch,
        run_id=run_id,
        observed_at=observed_at,
        source_command=source_command,
        settings=settings,
        commit_limit=commit_limit,
        hotspot_window=hotspot_window,
        todo_limit=todo_limit,
        logs_root=logs_root,
        metrics_exporter=metrics_exporter,
    )
    logger.info("Observer context created: %s, base_branch=%s", run_id, base_branch)
    return context
