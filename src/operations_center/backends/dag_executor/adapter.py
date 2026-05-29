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

from datetime import UTC, datetime
import logging
from pathlib import Path
from types import SimpleNamespace

from operations_center.backends.tiering import select_tier, tier_profile
from operations_center.backends.worker_backend_selector import (
    execute_with_worker_backend_round_robin,
    worker_backend_observed_runtime,
)
from operations_center.config.settings import DAGExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.execution.usage_store import UsageStore

logger = logging.getLogger(__name__)

_WORKFLOW_FILENAME = ".dag_executor/workflow.yaml"


class DAGExecutorBackendAdapter:
    """Canonical adapter for DAGExecutor backend execution."""

    def __init__(self, settings: DAGExecutorSettings, usage_store: UsageStore | None = None) -> None:
        self._settings = settings
        self._usage_store = usage_store

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        result, _ = self.execute_and_capture(request)
        return result

    def execute_and_capture(
        self, request: ExecutionRequest
    ) -> tuple[ExecutionResult, object | None]:
        try:
            from dag_executor.executor import DAGExecutorRunner  # type: ignore  # noqa: PGH003
            from dag_executor.models import GraphSpec, NodeSpec, NodeType  # type: ignore  # noqa: PGH003
            from dag_executor.loader import load_graph_file  # type: ignore  # noqa: PGH003
        except ImportError as exc:
            return _error_result(request, f"dag_executor not installed: {exc}"), None

        workspace = Path(request.workspace_path)
        workflow_path = workspace / _WORKFLOW_FILENAME
        artifacts_dir = self._settings.artifacts_dir or None
        usage_store = self._usage_store or UsageStore()

        tier = select_tier(
            configured=self._settings.tier_name,
            runtime_binding=request.runtime_binding,
            usage_store=usage_store,
            dynamic_enabled=self._settings.dynamic_tier_selection,
            pressure_threshold=self._settings.budget_pressure_threshold,
        )
        profile = tier_profile(tier)

        logger.info(
            "DAGExecutorAdapter: run=%s workflow=%s backend=%s tier=%s",
            request.run_id,
            workflow_path if workflow_path.exists() else "fallback-single-agent",
            self._settings.worker_backend,
            tier,
        )

        def _run_once(worker_backend: str) -> dict:
            runner = DAGExecutorRunner(
                artifacts_dir=artifacts_dir,
                working_directory=str(workspace),
                timeout_seconds=self._settings.timeout_seconds or None,
                worker_backend=worker_backend,  # type: ignore  # noqa: PGH003
            )
            if workflow_path.exists():
                spec = load_graph_file(str(workflow_path), goal_text=request.goal_text)
                _apply_agent_tier_defaults(spec, profile)
                return runner.run_graph(spec)
            spec = GraphSpec(
                workflow_id=request.run_id,
                goal_text=request.goal_text,
                nodes=[_default_agent_node(NodeSpec, NodeType, profile)],
            )
            return runner.run_graph(spec)

        try:
            executed = execute_with_worker_backend_round_robin(
                preferred_backend=self._settings.worker_backend,
                usage_store=usage_store,
                dynamic_enabled=self._settings.dynamic_worker_backend_selection,
                execute_once=_run_once,
                failed=lambda payload: payload.get("status") != "succeeded",
                failure_text=lambda payload: payload.get("error_summary"),
                logger=lambda msg: logger.info(
                    "DAGExecutorAdapter[%s]: %s", request.run_id, msg
                ),
            )
        except Exception as exc:
            logger.error("DAGExecutorAdapter: run=%s raised %s", request.run_id, exc)
            return _error_result(request, str(exc)), None

        capture = SimpleNamespace(
            observed_runtime=worker_backend_observed_runtime(executed),  # ty: ignore[invalid-argument-type]
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
                "DAGExecutorAdapter: run=%s fallback worker backend=%s",
                request.run_id,
                executed.selected_backend,
            )

        result = _dict_to_result(request, executed.payload)
        if (
            not result.success
            and "limit" in (result.failure_reason or "").lower()
            and hasattr(usage_store, "record_quota_event")
        ):
            usage_store.record_quota_event(
                task_id=request.run_id,
                role="dag_executor",
                backend=executed.selected_backend,
                now=datetime.now(UTC),
            )
        return result, capture


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


def _worker_backend_unavailable_result(
    request: ExecutionRequest,
    reason: str,
) -> ExecutionResult:
    return _error_result(
        request,
        f"worker backend round robin blocked dispatch: {reason}",
    )


def _default_agent_node(NodeSpec, NodeType, profile: dict[str, dict[str, str]]):
    return NodeSpec(
        id="main",
        type=NodeType.AGENT,
        model=profile["claude_code"]["model"],
        effort=profile["claude_code"]["effort"],
        backend_models={"codex_cli": profile["codex_cli"]["model"]},
        backend_efforts={"codex_cli": profile["codex_cli"]["effort"]},
    )


def _apply_agent_tier_defaults(spec, profile: dict[str, dict[str, str]]) -> None:
    for node in spec.nodes:
        node_type = getattr(node, "type", None)
        node_type_value = getattr(node_type, "value", node_type)
        if node_type_value != "agent":
            continue
        if not getattr(node, "model", None):
            node.model = profile["claude_code"]["model"]
        if not getattr(node, "effort", None):
            node.effort = profile["claude_code"]["effort"]
        if "codex_cli" not in node.backend_models:
            node.backend_models["codex_cli"] = profile["codex_cli"]["model"]
        if "codex_cli" not in node.backend_efforts:
            node.backend_efforts["codex_cli"] = profile["codex_cli"]["effort"]
