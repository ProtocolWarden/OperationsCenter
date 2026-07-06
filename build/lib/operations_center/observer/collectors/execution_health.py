# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
import logging
from pathlib import Path

from operations_center.observer.models import ExecutionHealthSignal, ExecutionRunRecord
from operations_center.observer.service import ObserverContext
from operations_center.observer.validation import (
    ArtifactValidator,
    ExecutionOutcomeValidator,
    RequestValidator,
    ValidationHistoryValidator,
)

logger = logging.getLogger(__name__)

_ARTIFACT_SCAN_LIMIT = 60
_RECENT_RUNS_IN_SIGNAL = 10


class ExecutionArtifactCollector:
    """Reads retained executor_plane execution artifacts for a specific repo and
    surfaces execution health metrics (no_op rate, validation failure rate)
    that feed the downstream insight → decision → propose pipeline."""

    def __init__(self, *, artifact_scan_limit: int = _ARTIFACT_SCAN_LIMIT) -> None:
        self.artifact_scan_limit = artifact_scan_limit

    def collect(self, context: ObserverContext) -> ExecutionHealthSignal:
        report_root = Path(context.settings.report_root)
        if not report_root.exists():
            return ExecutionHealthSignal()

        run_dirs = sorted(
            [d for d in report_root.iterdir() if d.is_dir()],
            reverse=True,
        )[: self.artifact_scan_limit]

        total = 0
        executed = 0
        no_op = 0
        unknown = 0
        error = 0
        validation_failed = 0
        recent_runs: list[ExecutionRunRecord] = []

        for run_dir in run_dirs:
            outcome_file = run_dir / "control_outcome.json"
            request_file = run_dir / "request.json"
            if not outcome_file.exists() or not request_file.exists():
                continue

            try:
                outcome_text = outcome_file.read_text(encoding="utf-8")
                outcome = json.loads(outcome_text)
            except (OSError, UnicodeDecodeError) as e:
                ArtifactValidator.log_io_error(
                    outcome_file,
                    e,
                    context={"collector": "ExecutionArtifactCollector"},
                    metrics_exporter=context.metrics_exporter,
                )
                continue
            except json.JSONDecodeError as e:
                ArtifactValidator.log_parse_error(
                    outcome_file,
                    e,
                    context={"collector": "ExecutionArtifactCollector"},
                    metrics_exporter=context.metrics_exporter,
                )
                continue

            is_valid, error_msg = ExecutionOutcomeValidator.validate(outcome)
            if not is_valid:
                ArtifactValidator.log_structure_error(
                    outcome_file,
                    error_msg,
                    expected_schema="control_outcome.json",
                    context={"collector": "ExecutionArtifactCollector"},
                    metrics_exporter=context.metrics_exporter,
                )
                continue

            try:
                request_text = request_file.read_text(encoding="utf-8")
                request = json.loads(request_text)
            except (OSError, UnicodeDecodeError) as e:
                ArtifactValidator.log_io_error(
                    request_file,
                    e,
                    context={"collector": "ExecutionArtifactCollector"},
                    metrics_exporter=context.metrics_exporter,
                )
                continue
            except json.JSONDecodeError as e:
                ArtifactValidator.log_parse_error(
                    request_file,
                    e,
                    context={"collector": "ExecutionArtifactCollector"},
                    metrics_exporter=context.metrics_exporter,
                )
                continue

            is_valid, error_msg = RequestValidator.validate(request)
            if not is_valid:
                ArtifactValidator.log_structure_error(
                    request_file,
                    error_msg,
                    expected_schema="request.json",
                    context={"collector": "ExecutionArtifactCollector"},
                    metrics_exporter=context.metrics_exporter,
                )
                continue

            task = request.get("task", {})
            repo_key = task.get("repo_key", "")
            if repo_key.lower() != context.repo_name.lower():
                continue

            validation_passed: bool | None = None
            validation_file = run_dir / "validation.json"
            if validation_file.exists():
                try:
                    v_text = validation_file.read_text(encoding="utf-8")
                    v = json.loads(v_text)
                except (OSError, UnicodeDecodeError) as e:
                    ArtifactValidator.log_io_error(
                        validation_file,
                        e,
                        context={"collector": "ExecutionArtifactCollector"},
                        metrics_exporter=context.metrics_exporter,
                    )
                except json.JSONDecodeError as e:
                    ArtifactValidator.log_parse_error(
                        validation_file,
                        e,
                        context={"collector": "ExecutionArtifactCollector"},
                        metrics_exporter=context.metrics_exporter,
                    )
                else:
                    is_valid, error_msg = ValidationHistoryValidator.validate(v)
                    if is_valid:
                        raw = v.get("passed")
                        if raw is not None:
                            validation_passed = bool(raw)
                    else:
                        ArtifactValidator.log_structure_error(
                            validation_file,
                            error_msg,
                            expected_schema="validation.json",
                            context={"collector": "ExecutionArtifactCollector"},
                            metrics_exporter=context.metrics_exporter,
                        )

            outcome_status = str(outcome.get("status", "unknown"))
            outcome_reason = outcome.get("reason")
            if outcome_reason is not None:
                outcome_reason = str(outcome_reason)
            task_id = str(outcome.get("task_id", ""))
            worker_role = str(outcome.get("worker_role", "unknown"))
            run_id = str(request.get("run_id", run_dir.name))

            total += 1
            if outcome_status == "executed":
                executed += 1
                if validation_passed is False:
                    validation_failed += 1
            elif outcome_status == "no_op":
                no_op += 1
            elif outcome_status == "unknown":
                unknown += 1
            elif outcome_status == "error":
                error += 1

            recent_runs.append(
                ExecutionRunRecord(
                    run_id=run_id,
                    task_id=task_id,
                    worker_role=worker_role,
                    outcome_status=outcome_status,
                    outcome_reason=outcome_reason,
                    validation_passed=validation_passed,
                )
            )

        return ExecutionHealthSignal(
            total_runs=total,
            executed_count=executed,
            no_op_count=no_op,
            unknown_count=unknown,
            error_count=error,
            validation_failed_count=validation_failed,
            recent_runs=recent_runs[:_RECENT_RUNS_IN_SIGNAL],
        )
