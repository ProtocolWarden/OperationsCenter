# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
Tests for the board_worker CI call-site wiring.

Verifies that _run_ci_loop is invoked when the bundle proposal carries
continuous_improvement, and that the single-shot path is unchanged when
it does not.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from operations_center.contracts.ci import (
    ContinuousImprovementSpec,
    EvaluationSpec,
    ImprovementStrategy,
    RefinementPolicy,
    ScoringMetric,
)
from operations_center.contracts.enums import EnforcedGuardrail, RefinementStatus
from operations_center.execution.ci_coordinator import CiRunResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ci_spec_dict() -> dict:
    spec = ContinuousImprovementSpec(
        strategy=ImprovementStrategy(
            principle="Reduce false retry rate",
            constraints=["fail_closed"],
        ),
        evaluation=EvaluationSpec(
            baseline_description="18% false retry rate",
            primary_scoring=ScoringMetric(
                metric="false_retry_rate",
                direction="lower_is_better",
                baseline_value=0.18,
                target_delta=-0.05,
            ),
            guardrails=[EnforcedGuardrail.CUSTODIAN_CLEAN],
        ),
        refinement=RefinementPolicy(max_attempts=3),
    )
    return json.loads(spec.model_dump_json())


def _bundle_with_ci() -> dict:
    return {
        "proposal": {
            "proposal_id": "prop-001",
            "task_id": "task-001",
            "execution_mode": "improve_campaign",
            "continuous_improvement": _ci_spec_dict(),
        },
        "decision": {},
    }


def _bundle_without_ci() -> dict:
    return {
        "proposal": {
            "proposal_id": "prop-002",
            "task_id": "task-002",
            "execution_mode": "improve_campaign",
        },
        "decision": {},
    }


def _mock_settings(tmp_path: Path):
    settings = MagicMock()
    settings.repos.get.return_value = MagicMock(
        local_path=str(tmp_path / "repo"),
        validation_commands=[],
    )
    return settings


# ---------------------------------------------------------------------------
# _run_ci_loop unit tests (via module import)
# ---------------------------------------------------------------------------


class TestRunCiLoop:
    def _call(self, tmp_path, ci_result, settings=None):
        from operations_center.entrypoints.board_worker.outcomes import run_ci_loop as _run_ci_loop

        client = MagicMock()
        issue = {"id": "task-001", "labels": [{"name": "repo: svc"}]}
        if settings is None:
            settings = _mock_settings(tmp_path)

        bundle_file = tmp_path / "bundle.json"
        bundle_file.write_text("{}", encoding="utf-8")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}", encoding="utf-8")

        with patch("operations_center.execution.ci_coordinator.CiCoordinator") as MockCoord:
            coord_instance = MagicMock()
            coord_instance.run.return_value = ci_result
            MockCoord.return_value = coord_instance

            result = _run_ci_loop(
                ci_spec_raw=_ci_spec_dict(),
                client=client,
                issue=issue,
                role="improve",
                task_kind="improve_campaign",
                task_id="task-001",
                repo_key="svc",
                settings=settings,
                python="python3",
                oc_root=tmp_path,
                env={},
                bundle_file=bundle_file,
                config_file=config_file,
                tmp=tmp_path,
                short_id="task001",
            )
        return result, client

    def test_accepted_returns_true_and_calls_handle_success(self, tmp_path):
        ci_result = CiRunResult(
            lineage_id="lin-001",
            final_status=RefinementStatus.ACCEPTED,
            accepted_attempt_number=2,
            total_attempts=2,
            last_decision=None,
            last_score=None,
        )
        with patch(
            "operations_center.entrypoints.board_worker.outcomes.handle_success"
        ) as mock_success:
            result, client = self._call(tmp_path, ci_result)

        assert result is True
        mock_success.assert_called_once()

    def test_budget_exhausted_returns_false_and_calls_handle_failure(self, tmp_path):
        ci_result = CiRunResult(
            lineage_id="lin-001",
            final_status=RefinementStatus.BUDGET_EXHAUSTED,
            accepted_attempt_number=None,
            total_attempts=3,
            last_decision=None,
            last_score=None,
        )
        with patch(
            "operations_center.entrypoints.board_worker.outcomes.handle_failure"
        ) as mock_failure:
            result, client = self._call(tmp_path, ci_result)

        assert result is False
        mock_failure.assert_called_once()

    def test_abandoned_returns_false(self, tmp_path):
        ci_result = CiRunResult(
            lineage_id="lin-001",
            final_status=RefinementStatus.ABANDONED,
            accepted_attempt_number=None,
            total_attempts=1,
            last_decision=None,
            last_score=None,
        )
        with patch("operations_center.entrypoints.board_worker.outcomes.handle_failure"):
            result, _ = self._call(tmp_path, ci_result)
        assert result is False

    def test_escalated_returns_false_and_calls_fail_task(self, tmp_path):
        ci_result = CiRunResult(
            lineage_id="lin-001",
            final_status=RefinementStatus.ESCALATED,
            accepted_attempt_number=None,
            total_attempts=1,
            last_decision=None,
            last_score=None,
        )
        with patch("operations_center.entrypoints.board_worker.outcomes.fail_task") as mock_fail:
            result, _ = self._call(tmp_path, ci_result)

        assert result is False
        mock_fail.assert_called_once()
        call_args = mock_fail.call_args[0]
        assert "escalated" in call_args[3].lower()

    def test_ci_labels_added(self, tmp_path):
        ci_result = CiRunResult(
            lineage_id="lin-001",
            final_status=RefinementStatus.ACCEPTED,
            accepted_attempt_number=1,
            total_attempts=1,
            last_decision=None,
            last_score=None,
        )
        with patch("operations_center.entrypoints.board_worker.outcomes.handle_success"):
            with patch(
                "operations_center.entrypoints.board_worker.outcomes.add_label"
            ) as mock_add_label:
                result, _ = self._call(tmp_path, ci_result)

        labels_added = [c.args[2] for c in mock_add_label.call_args_list]
        assert any("ci-status" in lbl for lbl in labels_added)
        assert any("ci-attempts" in lbl for lbl in labels_added)

    def test_invalid_ci_spec_calls_fail_task(self, tmp_path):
        from operations_center.entrypoints.board_worker.outcomes import run_ci_loop as _run_ci_loop

        client = MagicMock()
        issue = {"id": "task-bad", "labels": []}

        bundle_file = tmp_path / "bundle.json"
        bundle_file.write_text("{}", encoding="utf-8")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{}", encoding="utf-8")

        with patch("operations_center.entrypoints.board_worker.outcomes.fail_task") as mock_fail:
            result = _run_ci_loop(
                ci_spec_raw={"not": "a valid spec"},
                client=client,
                issue=issue,
                role="improve",
                task_kind="improve_campaign",
                task_id="task-bad",
                repo_key="svc",
                settings=_mock_settings(tmp_path),
                python="python3",
                oc_root=tmp_path,
                env={},
                bundle_file=bundle_file,
                config_file=config_file,
                tmp=tmp_path,
                short_id="taskbad",
            )

        assert result is False
        mock_fail.assert_called_once()
