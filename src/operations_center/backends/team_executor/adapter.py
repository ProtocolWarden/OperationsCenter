# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/team_executor/adapter.py — TeamExecutorBackendAdapter.

Wraps TeamExecutorRunner.run() behind the canonical ExecutionRequest → ExecutionResult
contract. Reads team_name and worker_backend from TeamExecutorSettings.
"""
from __future__ import annotations

from datetime import UTC, datetime
import logging
from types import SimpleNamespace

from operations_center.config.settings import TeamExecutorSettings
from operations_center.backends.tiering import select_tier
from operations_center.backends.worker_backend_selector import (
    execute_with_worker_backend_round_robin,
    worker_backend_observed_runtime,
)
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.execution.usage_store import UsageStore

logger = logging.getLogger(__name__)


class TeamExecutorBackendAdapter:
    """Canonical adapter for TeamExecutor backend execution."""

    def __init__(self, settings: TeamExecutorSettings, usage_store: UsageStore | None = None) -> None:
        self._settings = settings
        self._usage_store = usage_store

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        result, _ = self.execute_and_capture(request)
        return result

    def execute_and_capture(
        self, request: ExecutionRequest
    ) -> tuple[ExecutionResult, object | None]:
        try:
            from team_executor.executor import TeamExecutorRunner  # ty: ignore[unresolved-import]  # noqa: PGH003
        except ImportError as exc:
            return _error_result(request, f"team_executor not installed: {exc}"), None

        usage_store = self._usage_store or UsageStore()
        working_dir = str(request.workspace_path)
        team_name = _select_team_name(
            self._settings,
            request,
            usage_store=usage_store,
        )
        logger.info(
            "TeamExecutorAdapter: run=%s team=%s preferred_backend=%s dir=%s",
            request.run_id,
            team_name,
            self._settings.worker_backend,
            working_dir,
        )

        def _run_once(worker_backend: str):
            runner = TeamExecutorRunner(
                team_name=team_name,
                working_dir=working_dir,
                worker_backend=worker_backend,  # ty: ignore[invalid-argument-type]  # noqa: PGH003
            )
            return runner.run(
                goal_text=request.goal_text,
                invocation_id=request.run_id,
            )

        try:
            executed = execute_with_worker_backend_round_robin(
                preferred_backend=self._settings.worker_backend,
                usage_store=usage_store,
                dynamic_enabled=self._settings.dynamic_worker_backend_selection,
                execute_once=_run_once,
                failed=lambda payload: payload.status != "succeeded",
                failure_text=lambda payload: payload.error_summary,
                logger=lambda msg: logger.info(
                    "TeamExecutorAdapter[%s]: %s", request.run_id, msg
                ),
            )
        except Exception as exc:
            logger.error("TeamExecutorAdapter: run=%s raised %s", request.run_id, exc)
            return _error_result(request, str(exc)), None

        capture = SimpleNamespace(
            observed_runtime=worker_backend_observed_runtime(executed),
        )

        if executed.selected_backend is None or executed.payload is None:
            return (
                _worker_backend_unavailable_result(
                    request,
                    executed.selection.reason or "no worker backend available",
                ),
                capture,
            )

        if executed.fallback_used:
            logger.info(
                "TeamExecutorAdapter: run=%s fallback worker backend=%s",
                request.run_id,
                executed.selected_backend,
            )

        result = _rxp_to_result(request, executed.payload)
        if (
            not result.success
            and "limit" in (result.failure_reason or "").lower()
            and hasattr(usage_store, "record_quota_event")
        ):
            usage_store.record_quota_event(
                task_id=request.run_id,
                role="team_executor",
                backend="team_executor",
                now=datetime.now(UTC),
            )
        return result, capture


def _rxp_to_result(request: ExecutionRequest, rxp_result) -> ExecutionResult:
    success = rxp_result.status == "succeeded"
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SUCCEEDED if success else ExecutionStatus.FAILED,
        success=success,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        branch_pushed=False,
        branch_name=request.task_branch,
        failure_category=None if success else FailureReasonCategory.BACKEND_ERROR,
        failure_reason=None if success else (rxp_result.error_summary or "team_executor run failed"),
    )


def _select_team_name(
    settings: TeamExecutorSettings,
    request: ExecutionRequest,
    *,
    usage_store: UsageStore,
) -> str:
    return select_tier(
        configured=settings.team_name,
        runtime_binding=request.runtime_binding,
        usage_store=usage_store,
        dynamic_enabled=settings.dynamic_team_selection,
        pressure_threshold=settings.budget_pressure_threshold,
    )


def _error_result(request: ExecutionRequest, reason: str) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        branch_pushed=False,
        branch_name=request.task_branch,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=reason,
    )


def _worker_backend_unavailable_result(
    request: ExecutionRequest,
    reason: str,
) -> ExecutionResult:
    return _error_result(
        request,
        f"worker backend round robin blocked dispatch: {reason}",
    )
