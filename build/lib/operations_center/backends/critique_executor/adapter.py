# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/critique_executor/adapter.py — CritiqueExecutorBackendAdapter.

Wraps CritiqueExecutorRunner behind the canonical ExecutionRequest → ExecutionResult
contract. Reads topology, max_rounds, worker_backend from CritiqueExecutorSettings.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from operations_center.backends.tiering import select_tier, tier_profile
from operations_center.backends.worker_backend_selector import (
    execute_with_worker_backend_round_robin,
    worker_backend_observed_runtime,
)
from operations_center.config.settings import CritiqueExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.execution.usage_store import UsageStore

logger = logging.getLogger(__name__)


class CritiqueExecutorBackendAdapter:
    """Canonical adapter for CritiqueExecutor backend execution.

    CritiqueExecutor's config still uses historical `proposer_*` field names.
    In that backend those fields refer to the internal draft agent, not OC's
    separate board-facing proposer subsystem.
    """

    def __init__(
        self, settings: CritiqueExecutorSettings, usage_store: UsageStore | None = None
    ) -> None:
        self._settings = settings
        self._usage_store = usage_store

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        result, _ = self.execute_and_capture(request)
        return result

    def execute_and_capture(
        self, request: ExecutionRequest
    ) -> tuple[ExecutionResult, object | None]:
        try:
            from critique_executor.executor import (  # type: ignore  # noqa: PGH003
                CritiqueExecutorRunner,
            )
            from critique_executor.models import (  # type: ignore  # noqa: PGH003
                CritiqueConfig,
                CritiqueTopology,
            )
        except ImportError as exc:
            return _error_result(request, f"critique_executor not installed: {exc}"), None

        usage_store = self._usage_store or UsageStore()
        working_dir = self._settings.working_dir or str(request.workspace_path)
        tier = select_tier(
            configured=self._settings.tier_name,
            runtime_binding=request.runtime_binding,
            usage_store=usage_store,
            dynamic_enabled=self._settings.dynamic_tier_selection,
            pressure_threshold=self._settings.budget_pressure_threshold,
        )
        profile = tier_profile(tier)
        config = CritiqueConfig(
            topology=CritiqueTopology(self._settings.topology),
            proposer_model=profile["claude_code"]["model"],
            critic_model=profile["claude_code"]["model"],
            proposer_effort=profile["claude_code"]["effort"],
            critic_effort=profile["claude_code"]["effort"],
            proposer_backend_models={"codex_cli": profile["codex_cli"]["model"]},
            critic_backend_models={"codex_cli": profile["codex_cli"]["model"]},
            proposer_backend_efforts={"codex_cli": profile["codex_cli"]["effort"]},
            critic_backend_efforts={"codex_cli": profile["codex_cli"]["effort"]},
            max_rounds=self._settings.max_rounds,
            working_dir=working_dir,
            # S1b: per-task request.timeout_seconds wins; else backend settings.
            # request defaults to None (no override) so live tasks are unchanged.
            timeout_seconds=(
                request.timeout_seconds
                if request.timeout_seconds is not None
                else (self._settings.timeout_seconds or None)
            ),
            worker_backend=self._settings.worker_backend,
        )

        logger.info(
            "CritiqueExecutorAdapter: run=%s topology=%s backend=%s rounds=%d tier=%s",
            request.run_id,
            self._settings.topology,
            self._settings.worker_backend,
            self._settings.max_rounds,
            tier,
        )

        def _run_once(worker_backend: str):
            runner = CritiqueExecutorRunner(
                topology=self._settings.topology,
                config=config,
                worker_backend=worker_backend,  # type: ignore[arg-type]
                working_dir=working_dir,
            )
            return runner.run(
                goal_text=request.goal_text,
                max_rounds=self._settings.max_rounds,
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
                    "CritiqueExecutorAdapter[%s]: %s", request.run_id, msg
                ),
            )
        except Exception as exc:
            logger.error("CritiqueExecutorAdapter: run=%s raised %s", request.run_id, exc)
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
                "CritiqueExecutorAdapter: run=%s fallback worker backend=%s",
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
                role="critique_executor",
                backend="critique_executor",
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
        failure_reason=None
        if success
        else (rxp_result.error_summary or "critique_executor run failed"),
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
