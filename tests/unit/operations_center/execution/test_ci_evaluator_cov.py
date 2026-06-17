# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from operations_center.contracts.ci import EvaluationSpec, ScoringMetric
from operations_center.contracts.enums import (
    EnforcedGuardrail,
    EvaluationOutcome,
)
from operations_center.execution import ci_evaluator
from operations_center.execution.ci_evaluator import CiEvaluator, EvaluationContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    metric: str = "false_retry_rate",
    direction: str = "lower_is_better",
    baseline_value=None,
    target_delta=None,
    secondary=None,
    guardrails=None,
):
    return EvaluationSpec(
        baseline_description="baseline",
        primary_scoring=ScoringMetric(
            metric=metric,
            direction=direction,
            baseline_value=baseline_value,
            target_delta=target_delta,
        ),
        secondary_scoring=secondary or [],
        guardrails=guardrails if guardrails is not None else [],
    )


def _make_ctx(tmp_path: Path, **kwargs) -> EvaluationContext:
    defaults = dict(
        repo_path=tmp_path,
        attempt_number=1,
        evaluation_command="echo hi",
        validation_commands=[],
        changed_file_paths=[],
    )
    defaults.update(kwargs)
    return EvaluationContext(**defaults)


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# _run_evaluation_command
# ---------------------------------------------------------------------------


def test_run_eval_empty_command_returns_empty(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, evaluation_command="   ")
    assert ev._run_evaluation_command(ctx) == {}


def test_run_eval_reads_output_file_and_substitutes_n(tmp_path):
    ev = CiEvaluator()
    out_file = tmp_path / "eval.json"
    out_file.write_text(json.dumps({"false_retry_rate": 0.2}), encoding="utf-8")
    ctx = _make_ctx(
        tmp_path,
        evaluation_command="run --attempt {n}",
        attempt_number=7,
        eval_output_path=out_file,
    )
    with patch.object(ci_evaluator.subprocess, "run", return_value=_proc()) as m:
        result = ev._run_evaluation_command(ctx)
    assert result == {"false_retry_rate": 0.2}
    # placeholder substituted
    assert m.call_args.args[0] == "run --attempt 7"


def test_run_eval_output_file_bad_json_logs_and_falls_through(tmp_path):
    ev = CiEvaluator()
    out_file = tmp_path / "eval.json"
    out_file.write_text("not json", encoding="utf-8")
    ctx = _make_ctx(tmp_path, eval_output_path=out_file)
    proc = _proc(returncode=3, stdout="also not json")
    with patch.object(ci_evaluator.subprocess, "run", return_value=proc):
        result = ev._run_evaluation_command(ctx)
    assert result == {"exit_code": 3, "stdout": "also not json"}


def test_run_eval_parses_stdout_json_when_no_file(tmp_path):
    ev = CiEvaluator()
    proc = _proc(stdout='{"metric": 1.0}\n')
    ctx = _make_ctx(tmp_path)
    with patch.object(ci_evaluator.subprocess, "run", return_value=proc):
        result = ev._run_evaluation_command(ctx)
    assert result == {"metric": 1.0}


def test_run_eval_stdout_not_json_returns_exit_envelope(tmp_path):
    ev = CiEvaluator()
    proc = _proc(returncode=5, stdout="plain text")
    ctx = _make_ctx(tmp_path)
    with patch.object(ci_evaluator.subprocess, "run", return_value=proc):
        result = ev._run_evaluation_command(ctx)
    assert result == {"exit_code": 5, "stdout": "plain text"}


def test_run_eval_empty_stdout_returns_exit_envelope(tmp_path):
    ev = CiEvaluator()
    proc = _proc(returncode=0, stdout="")
    ctx = _make_ctx(tmp_path)
    with patch.object(ci_evaluator.subprocess, "run", return_value=proc):
        result = ev._run_evaluation_command(ctx)
    assert result == {"exit_code": 0, "stdout": ""}


def test_run_eval_output_path_set_but_missing_falls_to_stdout(tmp_path):
    ev = CiEvaluator()
    missing = tmp_path / "nope.json"
    proc = _proc(stdout='{"x": 2}')
    ctx = _make_ctx(tmp_path, eval_output_path=missing)
    with patch.object(ci_evaluator.subprocess, "run", return_value=proc):
        result = ev._run_evaluation_command(ctx)
    assert result == {"x": 2}


# ---------------------------------------------------------------------------
# _parse_eval_output
# ---------------------------------------------------------------------------


def test_parse_eval_primary_with_baseline_delta(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec(metric="m", baseline_value=10.0)
    val, delta, sec = ev._parse_eval_output(spec, {"m": 4}, _make_ctx(tmp_path))
    assert val == 4.0
    assert delta == -6.0
    assert sec == {}


def test_parse_eval_primary_without_baseline_no_delta(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec(metric="m", baseline_value=None)
    val, delta, _ = ev._parse_eval_output(spec, {"m": 4}, _make_ctx(tmp_path))
    assert val == 4.0
    assert delta is None


def test_parse_eval_primary_not_in_output(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec(metric="m", baseline_value=1.0)
    val, delta, _ = ev._parse_eval_output(spec, {"other": 1}, _make_ctx(tmp_path))
    assert val is None
    assert delta is None


def test_parse_eval_primary_unconvertible(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec(metric="m", baseline_value=1.0)
    val, delta, _ = ev._parse_eval_output(spec, {"m": "abc"}, _make_ctx(tmp_path))
    assert val is None
    assert delta is None


def test_parse_eval_secondary_metrics_mixed(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec(
        metric="m",
        secondary=[
            ScoringMetric(metric="good"),
            ScoringMetric(metric="bad"),
            ScoringMetric(metric="absent"),
        ],
    )
    output = {"m": 1, "good": 2.5, "bad": "nan-ish-string"}
    _, _, sec = ev._parse_eval_output(spec, output, _make_ctx(tmp_path))
    assert sec == {"good": 2.5}


# ---------------------------------------------------------------------------
# _check_guardrail dispatch + error handling
# ---------------------------------------------------------------------------


def test_check_guardrail_unknown_returns_false(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec()
    fake = MagicMock()
    fake.__eq__ = lambda self, other: False  # never matches any branch
    result = ev._check_guardrail(fake, spec, _make_ctx(tmp_path), {})
    assert result is False


def test_check_guardrail_catches_exception(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec()
    ctx = _make_ctx(tmp_path)
    with patch.object(ev, "_check_regression_fixtures", side_effect=RuntimeError("boom")):
        result = ev._check_guardrail(EnforcedGuardrail.REGRESSION_FIXTURES_PASS, spec, ctx, {})
    assert result is False


@pytest.mark.parametrize(
    "guardrail,method",
    [
        (EnforcedGuardrail.REGRESSION_FIXTURES_PASS, "_check_regression_fixtures"),
        (EnforcedGuardrail.CUSTODIAN_CLEAN, "_check_custodian_clean"),
        (EnforcedGuardrail.NO_ARCHITECTURE_VIOLATIONS, "_check_no_architecture_violations"),
        (EnforcedGuardrail.NO_RUNTIME_POLICY_WIDENING, "_check_no_policy_widening"),
    ],
)
def test_check_guardrail_dispatch(tmp_path, guardrail, method):
    ev = CiEvaluator()
    spec = _make_spec()
    ctx = _make_ctx(tmp_path)
    with patch.object(ev, method, return_value=True) as m:
        assert ev._check_guardrail(guardrail, spec, ctx, {}) is True
    m.assert_called_once()


def test_check_guardrail_dispatch_lost_escalations(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec()
    ctx = _make_ctx(tmp_path)
    with patch.object(ev, "_check_no_lost_escalations", return_value=True) as m:
        assert (
            ev._check_guardrail(EnforcedGuardrail.NO_LOST_ESCALATIONS, spec, ctx, {"a": 1}) is True
        )
    m.assert_called_once_with(spec, {"a": 1})


# ---------------------------------------------------------------------------
# _check_regression_fixtures
# ---------------------------------------------------------------------------


def test_regression_fixtures_all_pass(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, validation_commands=["a", "b"])
    with patch.object(ci_evaluator.subprocess, "run", return_value=_proc(returncode=0)):
        assert ev._check_regression_fixtures(ctx) is True


def test_regression_fixtures_one_fails(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, validation_commands=["a", "b"])
    procs = [_proc(returncode=0), _proc(returncode=1)]
    with patch.object(ci_evaluator.subprocess, "run", side_effect=procs):
        assert ev._check_regression_fixtures(ctx) is False


def test_regression_fixtures_empty_passes(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, validation_commands=[])
    assert ev._check_regression_fixtures(ctx) is True


# ---------------------------------------------------------------------------
# _check_custodian_clean
# ---------------------------------------------------------------------------


def test_custodian_clean_not_found_fails_closed(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    with patch.object(ci_evaluator.shutil, "which", return_value=None):
        assert ev._check_custodian_clean(ctx) is False


def test_custodian_clean_zero_findings(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(stdout=json.dumps({"total_findings": 0}))
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc),
    ):
        assert ev._check_custodian_clean(ctx) is True


def test_custodian_clean_nonzero_findings(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(stdout=json.dumps({"total_findings": 3}))
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc),
    ):
        assert ev._check_custodian_clean(ctx) is False


def test_custodian_clean_bad_json_falls_back_to_returncode(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(returncode=0, stdout="garbage")
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc),
    ):
        assert ev._check_custodian_clean(ctx) is True


def test_custodian_clean_bad_json_nonzero_rc(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(returncode=1, stdout="garbage")
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc),
    ):
        assert ev._check_custodian_clean(ctx) is False


# ---------------------------------------------------------------------------
# _check_no_architecture_violations
# ---------------------------------------------------------------------------


def test_arch_violations_not_found_fails_closed(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    with patch.object(ci_evaluator.shutil, "which", return_value=None):
        assert ev._check_no_architecture_violations(ctx) is False


def test_arch_violations_zero_findings_with_policy_flag(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(stdout=json.dumps({"total_findings": 0}))
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc) as m,
    ):
        assert ev._check_no_architecture_violations(ctx) is True
    assert "architecture" in m.call_args.args[0]
    assert "--policy" in m.call_args.args[0]


def test_arch_violations_nonzero(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(stdout=json.dumps({"total_findings": 2}))
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc),
    ):
        assert ev._check_no_architecture_violations(ctx) is False


def test_arch_violations_bad_json_falls_back(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path)
    proc = _proc(returncode=0, stdout="no json here")
    with (
        patch.object(ci_evaluator.shutil, "which", return_value="/bin/custodian-audit"),
        patch.object(ci_evaluator.subprocess, "run", return_value=proc),
    ):
        assert ev._check_no_architecture_violations(ctx) is True


# ---------------------------------------------------------------------------
# _check_no_policy_widening
# ---------------------------------------------------------------------------


def test_policy_widening_clean(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, changed_file_paths=["src/foo.py", "README.md"])
    assert ev._check_no_policy_widening(ctx) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/.custodian.yaml",
        "CUSTODIAN.md",
        "config/forbidden_paths.txt",
        "policy/rules.yaml",
        "allowed_paths.json",
        ".console/workers.yaml",
    ],
)
def test_policy_widening_detects(tmp_path, path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, changed_file_paths=["safe.py", path])
    assert ev._check_no_policy_widening(ctx) is False


def test_policy_widening_empty_changes(tmp_path):
    ev = CiEvaluator()
    ctx = _make_ctx(tmp_path, changed_file_paths=[])
    assert ev._check_no_policy_widening(ctx) is True


# ---------------------------------------------------------------------------
# _check_no_lost_escalations
# ---------------------------------------------------------------------------


def test_lost_escalations_missing_keys_passes():
    ev = CiEvaluator()
    spec = _make_spec()
    assert ev._check_no_lost_escalations(spec, {}) is True
    assert ev._check_no_lost_escalations(spec, {"escalations_before": 1}) is True
    assert ev._check_no_lost_escalations(spec, {"escalations_after": 1}) is True


def test_lost_escalations_preserved():
    ev = CiEvaluator()
    spec = _make_spec()
    out = {"escalations_before": 2, "escalations_after": 2}
    assert ev._check_no_lost_escalations(spec, out) is True


def test_lost_escalations_increased():
    ev = CiEvaluator()
    spec = _make_spec()
    out = {"escalations_before": 2, "escalations_after": 3}
    assert ev._check_no_lost_escalations(spec, out) is True


def test_lost_escalations_dropped_fails():
    ev = CiEvaluator()
    spec = _make_spec()
    out = {"escalations_before": 3, "escalations_after": 1}
    assert ev._check_no_lost_escalations(spec, out) is False


# ---------------------------------------------------------------------------
# _determine_outcome
# ---------------------------------------------------------------------------


def test_outcome_guardrail_violated():
    ev = CiEvaluator()
    spec = _make_spec()
    out = ev._determine_outcome(
        spec=spec,
        primary_delta=-5.0,
        guardrails_failed=[EnforcedGuardrail.CUSTODIAN_CLEAN],
    )
    assert out == EvaluationOutcome.GUARDRAIL_VIOLATED


def test_outcome_inconclusive_when_delta_none():
    ev = CiEvaluator()
    spec = _make_spec()
    out = ev._determine_outcome(spec=spec, primary_delta=None, guardrails_failed=[])
    assert out == EvaluationOutcome.INCONCLUSIVE


def test_outcome_improved_lower_is_better_meets_target():
    ev = CiEvaluator()
    spec = _make_spec(direction="lower_is_better", target_delta=-2.0)
    out = ev._determine_outcome(spec=spec, primary_delta=-5.0, guardrails_failed=[])
    assert out == EvaluationOutcome.IMPROVED


def test_outcome_improved_lower_is_better_no_target():
    ev = CiEvaluator()
    spec = _make_spec(direction="lower_is_better", target_delta=None)
    out = ev._determine_outcome(spec=spec, primary_delta=-0.1, guardrails_failed=[])
    assert out == EvaluationOutcome.IMPROVED


def test_outcome_improved_higher_is_better_meets_target():
    ev = CiEvaluator()
    spec = _make_spec(direction="higher_is_better", target_delta=2.0)
    out = ev._determine_outcome(spec=spec, primary_delta=5.0, guardrails_failed=[])
    assert out == EvaluationOutcome.IMPROVED


def test_outcome_neutral_zero_delta():
    ev = CiEvaluator()
    spec = _make_spec(direction="lower_is_better")
    out = ev._determine_outcome(spec=spec, primary_delta=0.0, guardrails_failed=[])
    assert out == EvaluationOutcome.NEUTRAL


def test_outcome_neutral_not_improved():
    ev = CiEvaluator()
    # lower_is_better but delta is positive (worse) → not improved → NEUTRAL branch
    spec = _make_spec(direction="lower_is_better")
    out = ev._determine_outcome(spec=spec, primary_delta=3.0, guardrails_failed=[])
    assert out == EvaluationOutcome.NEUTRAL


def test_outcome_regressed_improved_but_below_target():
    ev = CiEvaluator()
    # lower_is_better: delta negative = improved, but does not meet target (-10)
    spec = _make_spec(direction="lower_is_better", target_delta=-10.0)
    out = ev._determine_outcome(spec=spec, primary_delta=-1.0, guardrails_failed=[])
    # improved True but meets_target False -> falls past IMPROVED;
    # delta != 0 and improved is True so NEUTRAL branch is False -> REGRESSED
    assert out == EvaluationOutcome.REGRESSED


def test_outcome_higher_is_better_improved_below_target_regressed():
    ev = CiEvaluator()
    spec = _make_spec(direction="higher_is_better", target_delta=10.0)
    out = ev._determine_outcome(spec=spec, primary_delta=1.0, guardrails_failed=[])
    assert out == EvaluationOutcome.REGRESSED


# ---------------------------------------------------------------------------
# evaluate() integration
# ---------------------------------------------------------------------------


def test_evaluate_full_improved_no_guardrails(tmp_path):
    ev = CiEvaluator()
    out_file = tmp_path / "eval.json"
    out_file.write_text(json.dumps({"m": 5.0, "lat": 2.0}), encoding="utf-8")
    spec = _make_spec(
        metric="m",
        direction="higher_is_better",
        baseline_value=1.0,
        target_delta=1.0,
        secondary=[ScoringMetric(metric="lat")],
        guardrails=[],
    )
    ctx = _make_ctx(tmp_path, eval_output_path=out_file)
    with patch.object(ci_evaluator.subprocess, "run", return_value=_proc()):
        score = ev.evaluate(spec, ctx)
    assert score.primary_metric_value == 5.0
    assert score.primary_metric_delta == 4.0
    assert score.secondary_metrics == {"lat": 2.0}
    assert score.outcome == EvaluationOutcome.IMPROVED
    assert score.evidence_paths == [str(out_file)]
    assert score.evaluation_command_used == ctx.evaluation_command


def test_evaluate_with_passing_and_failing_guardrails(tmp_path):
    ev = CiEvaluator()
    out_file = tmp_path / "eval.json"
    out_file.write_text(json.dumps({"m": 5.0}), encoding="utf-8")
    spec = _make_spec(
        metric="m",
        direction="higher_is_better",
        baseline_value=1.0,
        guardrails=[
            EnforcedGuardrail.NO_RUNTIME_POLICY_WIDENING,
            EnforcedGuardrail.REGRESSION_FIXTURES_PASS,
        ],
    )
    # policy widening fails (policy file changed), regression passes (no cmds)
    ctx = _make_ctx(
        tmp_path,
        eval_output_path=out_file,
        changed_file_paths=["policy/x.yaml"],
        validation_commands=[],
    )
    with patch.object(ci_evaluator.subprocess, "run", return_value=_proc()):
        score = ev.evaluate(spec, ctx)
    assert EnforcedGuardrail.NO_RUNTIME_POLICY_WIDENING in score.guardrails_failed
    assert EnforcedGuardrail.REGRESSION_FIXTURES_PASS in score.guardrails_passed
    assert score.outcome == EvaluationOutcome.GUARDRAIL_VIOLATED


def test_evaluate_no_eval_output_path_empty_evidence(tmp_path):
    ev = CiEvaluator()
    spec = _make_spec(metric="m", baseline_value=None, guardrails=[])
    ctx = _make_ctx(tmp_path, eval_output_path=None)
    with patch.object(ci_evaluator.subprocess, "run", return_value=_proc(stdout='{"m": 2}')):
        score = ev.evaluate(spec, ctx)
    assert score.evidence_paths == []
    assert score.primary_metric_value == 2.0
    # no baseline -> delta None -> INCONCLUSIVE
    assert score.outcome == EvaluationOutcome.INCONCLUSIVE
