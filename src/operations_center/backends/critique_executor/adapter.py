# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/critique_executor/adapter.py — CritiqueExecutorBackendAdapter.

Wraps CritiqueExecutorRunner behind the canonical ExecutionRequest → ExecutionResult
contract. Reads topology, max_rounds, worker_backend from CritiqueExecutorSettings.
"""
from __future__ import annotations

import logging

from operations_center.backends.tiering import select_tier, tier_profile
from operations_center.config.settings import CritiqueExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.execution.usage_store import UsageStore

logger = logging.getLogger(__name__)


class CritiqueExecutorBackendAdapter:
    """Canonical adapter for CritiqueExecutor backend execution.

    CritiqueExecutor's config still uses historical `proposer_*` field names.
    In that backend those fields refer to the internal draft agent, not OC's
    separate board-facing proposer subsystem.
    """

    def __init__(self, settings: CritiqueExecutorSettings, usage_store: UsageStore | None = None) -> None:
        self._settings = settings
        self._usage_store = usage_store

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            from critique_executor.executor import CritiqueExecutorRunner  # type: ignore  # noqa: PGH003
            from critique_executor.models import CritiqueConfig, CritiqueTopology  # type: ignore  # noqa: PGH003
        except ImportError as exc:
            return _error_result(request, f"critique_executor not installed: {exc}")

        working_dir = self._settings.working_dir or str(request.workspace_path)
        tier = select_tier(
            configured="default",
            runtime_binding=request.runtime_binding,
            usage_store=self._usage_store or UsageStore(),
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
            timeout_seconds=self._settings.timeout_seconds,
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

        runner = CritiqueExecutorRunner(
            topology=self._settings.topology,
            config=config,
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
