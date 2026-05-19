# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/critique_executor/adapter.py — CritiqueExecutorBackendAdapter.

Wraps CritiqueExecutorRunner behind the canonical ExecutionRequest → ExecutionResult
contract. Reads topology, max_rounds, worker_backend from CritiqueExecutorSettings.
"""
from __future__ import annotations

import logging
from pathlib import Path

from operations_center.config.settings import CritiqueExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

logger = logging.getLogger(__name__)


class CritiqueExecutorBackendAdapter:
    """Canonical adapter for CritiqueExecutor backend execution."""

    def __init__(self, settings: CritiqueExecutorSettings) -> None:
        self._settings = settings

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            from critique_executor.executor import CritiqueExecutorRunner  # type: ignore[import]
        except ImportError as exc:
            return _error_result(request, f"critique_executor not installed: {exc}")

        working_dir = self._settings.working_dir or str(request.workspace_path)

        logger.info(
            "CritiqueExecutorAdapter: run=%s topology=%s backend=%s rounds=%d",
            request.run_id,
            self._settings.topology,
            self._settings.worker_backend,
            self._settings.max_rounds,
        )

        runner = CritiqueExecutorRunner(
            topology=self._settings.topology,
            worker_backend=self._settings.worker_backend,  # type: ignore[arg-type]
            working_dir=working_dir,
        )

        try:
            rxp_result = runner.run(
                goal_text=request.goal_text,
                max_rounds=self._settings.max_rounds,
            )
        except Exception as exc:
            logger.error("CritiqueExecutorAdapter: run=%s raised %s", request.run_id, exc)
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
        failure_reason=None if success else (rxp_result.error_summary or "critique_executor run failed"),
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
