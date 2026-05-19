# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/dag_executor/adapter.py — DAGExecutorBackendAdapter.

Wraps DAGExecutorRunner behind the canonical ExecutionRequest → ExecutionResult contract.

Workflow resolution order:
  1. {workspace_path}/.dag_executor/workflow.yaml — operator-authored workflow
  2. Single-agent fallback GraphSpec built from goal_text
"""
from __future__ import annotations

import logging
from pathlib import Path

from operations_center.config.settings import DAGExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

logger = logging.getLogger(__name__)

_WORKFLOW_FILENAME = ".dag_executor/workflow.yaml"


class DAGExecutorBackendAdapter:
    """Canonical adapter for DAGExecutor backend execution."""

    def __init__(self, settings: DAGExecutorSettings) -> None:
        self._settings = settings

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            from dag_executor.executor import DAGExecutorRunner  # type: ignore[import]
            from dag_executor.models import GraphSpec, NodeSpec, NodeType  # type: ignore[import]
        except ImportError as exc:
            return _error_result(request, f"dag_executor not installed: {exc}")

        workspace = Path(request.workspace_path)
        workflow_path = workspace / _WORKFLOW_FILENAME
        artifacts_dir = self._settings.artifacts_dir or None

        runner = DAGExecutorRunner(
            artifacts_dir=artifacts_dir,
            working_directory=str(workspace),
            timeout_seconds=self._settings.timeout_seconds or None,
            worker_backend=self._settings.worker_backend,
        )

        logger.info(
            "DAGExecutorAdapter: run=%s workflow=%s backend=%s",
            request.run_id,
            workflow_path if workflow_path.exists() else "fallback-single-agent",
            self._settings.worker_backend,
        )

        try:
            if workflow_path.exists():
                result_dict = runner.run_from_yaml(str(workflow_path), goal_text=request.goal_text)
            else:
                spec = GraphSpec(
                    workflow_id=request.run_id,
                    goal_text=request.goal_text,
                    nodes=[NodeSpec(id="main", type=NodeType.AGENT)],
                )
                result_dict = runner.run_graph(spec)
        except Exception as exc:
            logger.error("DAGExecutorAdapter: run=%s raised %s", request.run_id, exc)
            return _error_result(request, str(exc))

        return _dict_to_result(request, result_dict)


def _dict_to_result(request: ExecutionRequest, result_dict: dict) -> ExecutionResult:
    success = result_dict.get("status") == "succeeded"
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
        failure_reason=None if success else (result_dict.get("error_summary") or "dag_executor run failed"),
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
