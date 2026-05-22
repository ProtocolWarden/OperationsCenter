# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/team_executor/adapter.py — TeamExecutorBackendAdapter.

Wraps TeamExecutorRunner.run() behind the canonical ExecutionRequest → ExecutionResult
contract. Reads team_name and worker_backend from TeamExecutorSettings.
"""
from __future__ import annotations

import logging

from operations_center.config.settings import TeamExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

logger = logging.getLogger(__name__)


class TeamExecutorBackendAdapter:
    """Canonical adapter for TeamExecutor backend execution."""

    def __init__(self, settings: TeamExecutorSettings) -> None:
        self._settings = settings

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            from team_executor.executor import TeamExecutorRunner  # type: ignore
        except ImportError as exc:
            return _error_result(request, f"team_executor not installed: {exc}")

        working_dir = str(request.workspace_path)
        logger.info(
            "TeamExecutorAdapter: run=%s team=%s backend=%s dir=%s",
            request.run_id,
            self._settings.team_name,
            self._settings.worker_backend,
            working_dir,
        )

        runner = TeamExecutorRunner(
            team_name=self._settings.team_name,
            working_dir=working_dir,
            worker_backend=self._settings.worker_backend,  # type: ignore  # noqa: PGH003
        )

        try:
            rxp_result = runner.run(
                goal_text=request.goal_text,
                invocation_id=request.run_id,
            )
        except Exception as exc:
            logger.error("TeamExecutorAdapter: run=%s raised %s", request.run_id, exc)
            return _error_result(request, str(exc))

        return _rxp_to_result(request, rxp_result)


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
