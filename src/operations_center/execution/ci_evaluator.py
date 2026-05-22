# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
ci_evaluator.py — Evaluation runner and guardrail checker for CI attempts.

Runs the resolved evaluation_command in the target repo workspace and produces
an EvaluationScore. Also implements automated guardrail checks for the closed
EnforcedGuardrail enum.

Guardrail implementations:
  REGRESSION_FIXTURES_PASS  — re-run validation_commands from ValidationProfile
  CUSTODIAN_CLEAN           — run custodian-audit on the target repo path
  NO_ARCHITECTURE_VIOLATIONS — run custodian-audit --policy architecture
  NO_RUNTIME_POLICY_WIDENING — static check: no edits to policy/config files
  NO_LOST_ESCALATIONS       — check escalation count via eval output comparison

Design doc: docs/design/continuous-improvement/design.md §7
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from operations_center.contracts.ci import EvaluationScore, EvaluationSpec
from operations_center.contracts.enums import (
    EnforcedGuardrail,
    EvaluationOutcome,
)

logger = logging.getLogger(__name__)

# Files whose presence in an attempt diff triggers NO_RUNTIME_POLICY_WIDENING.
_POLICY_FILE_PATTERNS = (
    ".custodian",
    "custodian",
    "forbidden_paths",
    "policy",
    "allowed_paths",
    ".console/workers.yaml",
)


@dataclass(frozen=True)
class EvaluationContext:
    """Runtime context passed to the evaluator for a single attempt."""

    repo_path: Path
    attempt_number: int
    evaluation_command: str
    validation_commands: list[str]
    changed_file_paths: list[str]
    eval_output_path: Optional[Path] = None
    timeout_seconds: int = 120


class CiEvaluator:
    """
    Runs evaluation and guardrail checks for a single CI attempt.

    ``evaluate()`` returns an ``EvaluationScore``; the coordinator uses it
    to make the accept/retry/abandon/escalate decision.
    """

    def evaluate(
        self,
        spec: EvaluationSpec,
        ctx: EvaluationContext,
    ) -> EvaluationScore:
        """Run evaluation command then check all guardrails. Return scored outcome."""
        eval_output = self._run_evaluation_command(ctx)
        primary_value, primary_delta, secondary = self._parse_eval_output(spec, eval_output, ctx)

        passed: list[EnforcedGuardrail] = []
        failed: list[EnforcedGuardrail] = []

        for guardrail in spec.guardrails:
            ok = self._check_guardrail(guardrail, spec, ctx, eval_output)
            (passed if ok else failed).append(guardrail)

        outcome = self._determine_outcome(
            spec=spec,
            primary_delta=primary_delta,
            guardrails_failed=failed,
        )

        return EvaluationScore(
            primary_metric_value=primary_value,
            primary_metric_delta=primary_delta,
            secondary_metrics=secondary,
            guardrails_passed=passed,
            guardrails_failed=failed,
            outcome=outcome,
            evidence_paths=[str(ctx.eval_output_path)] if ctx.eval_output_path else [],
            evaluation_command_used=ctx.evaluation_command,
        )

    # ------------------------------------------------------------------
    # Evaluation command

    def _run_evaluation_command(self, ctx: EvaluationContext) -> dict:
        if not ctx.evaluation_command.strip():
            return {}

        # Substitute attempt number placeholder
        cmd = ctx.evaluation_command.replace("{n}", str(ctx.attempt_number))

        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=ctx.repo_path,
            capture_output=True,
            text=True,
            timeout=ctx.timeout_seconds,
        )

        if ctx.eval_output_path and ctx.eval_output_path.exists():
            try:
                return json.loads(ctx.eval_output_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(
                    '{"event": "ci_eval_parse_failed", "path": "%s", "error": "%s"}',
                    ctx.eval_output_path,
                    exc,
                )

        # Fall back to parsing stdout
        stdout = proc.stdout.strip()
        if stdout:
            try:
                return json.loads(stdout)
            except Exception:
                pass

        return {"exit_code": proc.returncode, "stdout": proc.stdout[:2000]}

    def _parse_eval_output(
        self,
        spec: EvaluationSpec,
        output: dict,
        ctx: EvaluationContext,
    ) -> tuple[Optional[float], Optional[float], dict[str, float]]:
        metric_name = spec.primary_scoring.metric
        primary_value: Optional[float] = None
        primary_delta: Optional[float] = None

        if metric_name in output:
            try:
                primary_value = float(output[metric_name])
                if spec.primary_scoring.baseline_value is not None:
                    primary_delta = primary_value - spec.primary_scoring.baseline_value
            except (TypeError, ValueError):
                pass

        secondary: dict[str, float] = {}
        for sm in spec.secondary_scoring:
            if sm.metric in output:
                try:
                    secondary[sm.metric] = float(output[sm.metric])
                except (TypeError, ValueError):
                    pass

        return primary_value, primary_delta, secondary

    # ------------------------------------------------------------------
    # Guardrail checks

    def _check_guardrail(
        self,
        guardrail: EnforcedGuardrail,
        spec: EvaluationSpec,
        ctx: EvaluationContext,
        eval_output: dict,
    ) -> bool:
        try:
            if guardrail == EnforcedGuardrail.REGRESSION_FIXTURES_PASS:
                return self._check_regression_fixtures(ctx)
            elif guardrail == EnforcedGuardrail.CUSTODIAN_CLEAN:
                return self._check_custodian_clean(ctx)
            elif guardrail == EnforcedGuardrail.NO_ARCHITECTURE_VIOLATIONS:
                return self._check_no_architecture_violations(ctx)
            elif guardrail == EnforcedGuardrail.NO_RUNTIME_POLICY_WIDENING:
                return self._check_no_policy_widening(ctx)
            elif guardrail == EnforcedGuardrail.NO_LOST_ESCALATIONS:
                return self._check_no_lost_escalations(spec, eval_output)
            else:
                logger.warning(
                    '{"event": "ci_guardrail_unknown", "guardrail": "%s"}', guardrail
                )
                return False
        except Exception as exc:
            logger.error(
                '{"event": "ci_guardrail_error", "guardrail": "%s", "error": "%s"}',
                guardrail,
                exc,
            )
            return False

    def _check_regression_fixtures(self, ctx: EvaluationContext) -> bool:
        for cmd in ctx.validation_commands:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=ctx.repo_path,
                capture_output=True,
                text=True,
                timeout=ctx.timeout_seconds,
            )
            if proc.returncode != 0:
                logger.info(
                    '{"event": "ci_guardrail_fixtures_fail", "cmd": "%s", "rc": %d}',
                    cmd,
                    proc.returncode,
                )
                return False
        return True

    def _check_custodian_clean(self, ctx: EvaluationContext) -> bool:
        custodian_bin = shutil.which("custodian-audit")
        if custodian_bin is None:
            logger.warning('{"event": "ci_guardrail_custodian_not_found"}')
            # Fail closed: can't verify = not clean
            return False
        proc = subprocess.run(
            [custodian_bin, "--repo", str(ctx.repo_path), "--json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        try:
            envelope = json.loads(proc.stdout)
            return int(envelope.get("total_findings", 1)) == 0
        except Exception:
            return proc.returncode == 0

    def _check_no_architecture_violations(self, ctx: EvaluationContext) -> bool:
        custodian_bin = shutil.which("custodian-audit")
        if custodian_bin is None:
            logger.warning('{"event": "ci_guardrail_custodian_not_found"}')
            return False
        proc = subprocess.run(
            [custodian_bin, "--repo", str(ctx.repo_path), "--json", "--policy", "architecture"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        try:
            envelope = json.loads(proc.stdout)
            return int(envelope.get("total_findings", 1)) == 0
        except Exception:
            return proc.returncode == 0

    def _check_no_policy_widening(self, ctx: EvaluationContext) -> bool:
        for path in ctx.changed_file_paths:
            lower = path.lower()
            if any(pat in lower for pat in _POLICY_FILE_PATTERNS):
                logger.info(
                    '{"event": "ci_guardrail_policy_widening", "path": "%s"}', path
                )
                return False
        return True

    def _check_no_lost_escalations(
        self, spec: EvaluationSpec, eval_output: dict
    ) -> bool:
        # The evaluation command is expected to emit escalation counts.
        # If the output contains `escalations_before` / `escalations_after` keys,
        # compare them. If not present, pass conservatively (eval command is
        # responsible for surfacing this data; absence means not tracked).
        before = eval_output.get("escalations_before")
        after = eval_output.get("escalations_after")
        if before is None or after is None:
            return True
        return int(after) >= int(before)

    # ------------------------------------------------------------------
    # Outcome determination

    def _determine_outcome(
        self,
        *,
        spec: EvaluationSpec,
        primary_delta: Optional[float],
        guardrails_failed: list[EnforcedGuardrail],
    ) -> EvaluationOutcome:
        if guardrails_failed:
            return EvaluationOutcome.GUARDRAIL_VIOLATED

        if primary_delta is None:
            return EvaluationOutcome.INCONCLUSIVE

        target_delta = spec.primary_scoring.target_delta
        direction = spec.primary_scoring.direction

        # For lower_is_better: negative delta = improvement
        # For higher_is_better: positive delta = improvement
        if direction == "lower_is_better":
            improved = primary_delta < 0
            meets_target = target_delta is None or primary_delta <= target_delta
        else:
            improved = primary_delta > 0
            meets_target = target_delta is None or primary_delta >= target_delta

        if improved and meets_target:
            return EvaluationOutcome.IMPROVED
        if primary_delta == 0 or (not improved and not guardrails_failed):
            return EvaluationOutcome.NEUTRAL
        return EvaluationOutcome.REGRESSED
