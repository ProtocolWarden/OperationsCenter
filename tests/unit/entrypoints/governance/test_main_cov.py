# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from typer.testing import CliRunner

from operations_center.audit_governance import (
    AuditGovernanceRequest,
    GovernanceReportError,
)
from operations_center.entrypoints.governance import main as mod

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
def _make_request(
    repo_id: str = "example_repo", audit_type: str = "full"
) -> AuditGovernanceRequest:
    return AuditGovernanceRequest(
        repo_id=repo_id,
        audit_type=audit_type,
        requested_by="alice",
        requested_reason="because audit is needed",
        urgency="normal",
    )


def _make_policy(name: str = "known_repo", status: str = "passed", reason: str = "ok"):
    return SimpleNamespace(policy_name=name, status=status, reason=reason)


def _make_decision(decision: str = "approved", reasons=None, policy_results=None):
    return SimpleNamespace(
        decision=decision,
        reasons=reasons if reasons is not None else ["all clear"],
        policy_results=policy_results if policy_results is not None else [_make_policy()],
    )


def _write_request_json(path: Path, req: AuditGovernanceRequest | None = None) -> Path:
    req = req or _make_request()
    path.write_text(req.model_dump_json(), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# _status_color
# --------------------------------------------------------------------------- #
def test_status_color_known_values():
    assert mod._status_color("approved") == "green"
    assert mod._status_color("approved_and_dispatched") == "green"
    assert mod._status_color("denied") == "red"
    assert mod._status_color("deferred") == "yellow"
    assert mod._status_color("needs_manual_approval") == "cyan"
    assert mod._status_color("dispatch_failed") == "red"


def test_status_color_unknown_default_white():
    assert mod._status_color("something_else") == "white"


# --------------------------------------------------------------------------- #
# request command
# --------------------------------------------------------------------------- #
def test_request_prints_payload_to_stdout():
    result = runner.invoke(
        mod.app,
        [
            "request",
            "--repo",
            "example_repo",
            "--type",
            "full",
            "--reason",
            "needs audit",
            "--requested-by",
            "alice",
        ],
    )
    assert result.exit_code == 0
    assert '"repo_id": "example_repo"' in result.stdout


def test_request_writes_to_output_file(tmp_path):
    out = tmp_path / "req.json"
    result = runner.invoke(
        mod.app,
        [
            "request",
            "--repo",
            "example_repo",
            "--type",
            "full",
            "--reason",
            "needs audit",
            "--requested-by",
            "alice",
            "--suite-report",
            "some/report.json",
            "--urgency",
            "high",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["repo_id"] == "example_repo"
    assert data["urgency"] == "high"
    assert data["related_suite_report_path"] == "some/report.json"
    assert "Request written to" in result.stdout


def test_request_invalid_exits_code_2():
    # empty repo triggers validation error in AuditGovernanceRequest
    result = runner.invoke(
        mod.app,
        [
            "request",
            "--repo",
            "   ",
            "--type",
            "full",
            "--reason",
            "needs audit",
            "--requested-by",
            "alice",
        ],
    )
    assert result.exit_code == 2
    assert "Invalid request" in result.stdout


# --------------------------------------------------------------------------- #
# evaluate command
# --------------------------------------------------------------------------- #
def test_evaluate_not_found_exits_1(tmp_path):
    missing = tmp_path / "nope.json"
    result = runner.invoke(mod.app, ["evaluate", "--request", str(missing)])
    assert result.exit_code == 1
    assert "Not found" in result.stdout


def test_evaluate_bad_json_exits_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    result = runner.invoke(mod.app, ["evaluate", "--request", str(bad)])
    assert result.exit_code == 2
    assert "Cannot load request" in result.stdout


def test_evaluate_approved_success(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    policies = [_make_policy("p1", "passed"), _make_policy("p2", "warning", "warn")]
    eval_mock = MagicMock(return_value=policies)
    dec_mock = MagicMock(return_value=_make_decision("approved", policy_results=policies))
    monkeypatch.setattr(mod, "evaluate_governance_policies", eval_mock)
    monkeypatch.setattr(mod, "make_governance_decision", dec_mock)

    result = runner.invoke(
        mod.app,
        [
            "evaluate",
            "--request",
            str(req_file),
            "--known-repos",
            "example_repo, other",
            "--known-types",
            "full, quick",
        ],
    )
    assert result.exit_code == 0
    assert "APPROVED" in result.stdout
    # known_repos/known_types were parsed and passed through config
    kwargs = eval_mock.call_args.kwargs
    assert kwargs["known_repos"] == ["example_repo", "other"]
    assert kwargs["known_audit_types"] == {"example_repo": ["full", "quick"]}


def test_evaluate_no_known_types_empty_map(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    eval_mock = MagicMock(return_value=[_make_policy()])
    monkeypatch.setattr(mod, "evaluate_governance_policies", eval_mock)
    monkeypatch.setattr(
        mod, "make_governance_decision", MagicMock(return_value=_make_decision("approved"))
    )
    result = runner.invoke(mod.app, ["evaluate", "--request", str(req_file)])
    assert result.exit_code == 0
    assert eval_mock.call_args.kwargs["known_audit_types"] == {}


def test_evaluate_denied_exits_1(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    failed = [_make_policy("p1", "failed", "bad")]
    monkeypatch.setattr(mod, "evaluate_governance_policies", MagicMock(return_value=failed))
    monkeypatch.setattr(
        mod,
        "make_governance_decision",
        MagicMock(return_value=_make_decision("denied", policy_results=failed)),
    )
    result = runner.invoke(mod.app, ["evaluate", "--request", str(req_file)])
    assert result.exit_code == 1
    assert "DENIED" in result.stdout


# --------------------------------------------------------------------------- #
# approve command
# --------------------------------------------------------------------------- #
def _make_decision_json(tmp_path: Path) -> Path:
    from operations_center.audit_governance import AuditGovernanceDecision

    dec = AuditGovernanceDecision(
        request_id="rid",
        repo_id="example_repo",
        audit_type="full",
        decision="needs_manual_approval",
        reasons=["needs sign-off"],
        policy_results=[],
        requires_manual_approval=True,
    )
    p = tmp_path / "decision.json"
    p.write_text(dec.model_dump_json(), encoding="utf-8")
    return p


def test_approve_not_found_exits_1(tmp_path):
    result = runner.invoke(
        mod.app,
        [
            "approve",
            "--decision",
            str(tmp_path / "missing_dec.json"),
            "--request",
            str(tmp_path / "missing_req.json"),
            "--approved-by",
            "boss",
        ],
    )
    assert result.exit_code == 1
    assert "Not found" in result.stdout


def test_approve_bad_files_exits_2(tmp_path):
    req_file = _write_request_json(tmp_path / "req.json")
    bad_dec = tmp_path / "dec.json"
    bad_dec.write_text("{broken", encoding="utf-8")
    result = runner.invoke(
        mod.app,
        [
            "approve",
            "--decision",
            str(bad_dec),
            "--request",
            str(req_file),
            "--approved-by",
            "boss",
        ],
    )
    assert result.exit_code == 2
    assert "Cannot load files" in result.stdout


def test_approve_validation_failure_exits_3(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    dec_file = _make_decision_json(tmp_path)
    monkeypatch.setattr(mod, "make_manual_approval", MagicMock(side_effect=ValueError("mismatch")))
    result = runner.invoke(
        mod.app,
        [
            "approve",
            "--decision",
            str(dec_file),
            "--request",
            str(req_file),
            "--approved-by",
            "boss",
        ],
    )
    assert result.exit_code == 3
    assert "Approval validation failed" in result.stdout


def test_approve_success_stdout(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    dec_file = _make_decision_json(tmp_path)
    approval = MagicMock()
    approval.model_dump_json.return_value = '{"approval_id": "AID"}'
    approval.approval_id = "AID"
    monkeypatch.setattr(mod, "make_manual_approval", MagicMock(return_value=approval))
    ps = MagicMock()
    monkeypatch.setattr(mod, "print_structured", ps)
    result = runner.invoke(
        mod.app,
        [
            "approve",
            "--decision",
            str(dec_file),
            "--request",
            str(req_file),
            "--approved-by",
            "boss",
            "--notes",
            "ok go",
        ],
    )
    assert result.exit_code == 0
    ps.assert_called_once_with(mod.console, approval)


def test_approve_success_to_file(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    dec_file = _make_decision_json(tmp_path)
    out = tmp_path / "approval.json"
    approval = MagicMock()
    approval.model_dump_json.return_value = '{"approval_id": "AID2"}'
    approval.approval_id = "AID2"
    monkeypatch.setattr(mod, "make_manual_approval", MagicMock(return_value=approval))
    result = runner.invoke(
        mod.app,
        [
            "approve",
            "--decision",
            str(dec_file),
            "--request",
            str(req_file),
            "--approved-by",
            "boss",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8") == '{"approval_id": "AID2"}'
    assert "Approval written to" in result.stdout


# --------------------------------------------------------------------------- #
# run command
# --------------------------------------------------------------------------- #
def _make_run_result(
    governance_status: str = "approved_and_dispatched",
    decision: str = "approved",
    report_path: str = "report/path.json",
    dispatch_result=None,
):
    return SimpleNamespace(
        governance_status=governance_status,
        decision=SimpleNamespace(decision=decision),
        report_path=report_path,
        dispatch_result=dispatch_result,
    )


def test_run_not_found_exits_1(tmp_path):
    result = runner.invoke(mod.app, ["run", "--request", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "Not found" in result.stdout


def test_run_bad_request_exits_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    result = runner.invoke(mod.app, ["run", "--request", str(bad)])
    assert result.exit_code == 2
    assert "Cannot load request" in result.stdout


def test_run_bad_approval_exits_2(tmp_path):
    req_file = _write_request_json(tmp_path / "req.json")
    bad_ap = tmp_path / "ap.json"
    bad_ap.write_text("{broken", encoding="utf-8")
    result = runner.invoke(mod.app, ["run", "--request", str(req_file), "--approval", str(bad_ap)])
    assert result.exit_code == 2
    assert "Cannot load approval" in result.stdout


def test_run_approved_and_dispatched_success(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    dr = SimpleNamespace(run_id="RUN1", status=SimpleNamespace(value="completed"), error=None)
    run_mock = MagicMock(return_value=_make_run_result(dispatch_result=dr, report_path="r.json"))
    monkeypatch.setattr(mod, "run_governed_audit", run_mock)
    result = runner.invoke(
        mod.app,
        [
            "run",
            "--request",
            str(req_file),
            "--known-repos",
            "example_repo",
            "--known-types",
            "full",
            "--timeout",
            "30",
        ],
    )
    assert result.exit_code == 0
    assert "APPROVED_AND_DISPATCHED" in result.stdout
    assert "RUN1" in result.stdout
    assert "r.json" in result.stdout
    assert run_mock.call_args.kwargs["dispatch_timeout_seconds"] == 30


def test_run_dispatch_error_shown_and_status_failed(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    dr = SimpleNamespace(run_id="RUN2", status=SimpleNamespace(value="failed"), error="boom")
    monkeypatch.setattr(
        mod,
        "run_governed_audit",
        MagicMock(
            return_value=_make_run_result(
                governance_status="dispatch_failed", decision="approved", dispatch_result=dr
            )
        ),
    )
    result = runner.invoke(mod.app, ["run", "--request", str(req_file)])
    assert result.exit_code == 1
    assert "boom" in result.stdout
    assert "DISPATCH_FAILED" in result.stdout


def test_run_needs_manual_approval_exits_2(tmp_path, monkeypatch):
    req_file = _write_request_json(tmp_path / "req.json")
    monkeypatch.setattr(
        mod,
        "run_governed_audit",
        MagicMock(
            return_value=_make_run_result(
                governance_status="needs_manual_approval",
                decision="needs_manual_approval",
                report_path="",
                dispatch_result=None,
            )
        ),
    )
    result = runner.invoke(mod.app, ["run", "--request", str(req_file)])
    assert result.exit_code == 2
    assert "NEEDS_MANUAL_APPROVAL" in result.stdout


def test_run_with_state_dir_and_approval(tmp_path, monkeypatch):
    from operations_center.audit_governance import AuditManualApproval

    req_file = _write_request_json(tmp_path / "req.json")
    ap = AuditManualApproval(decision_id="d", request_id="r", approved_by="boss")
    ap_file = tmp_path / "ap.json"
    ap_file.write_text(ap.model_dump_json(), encoding="utf-8")
    state_dir = tmp_path / "state"

    run_mock = MagicMock(
        return_value=_make_run_result(
            governance_status="deferred", decision="deferred", report_path=""
        )
    )
    monkeypatch.setattr(mod, "run_governed_audit", run_mock)
    result = runner.invoke(
        mod.app,
        [
            "run",
            "--request",
            str(req_file),
            "--approval",
            str(ap_file),
            "--state-dir",
            str(state_dir),
        ],
    )
    # deferred -> exit code 2
    assert result.exit_code == 2
    cfg = run_mock.call_args.kwargs["governance_config"]
    assert cfg.state_dir == state_dir
    assert run_mock.call_args.kwargs["approval"] is not None


# --------------------------------------------------------------------------- #
# inspect command
# --------------------------------------------------------------------------- #
def _make_report(
    *,
    dispatch=None,
    budget=None,
    cooldown=None,
    reasons=None,
    policy_results=None,
):
    return SimpleNamespace(
        request=SimpleNamespace(
            repo_id="example_repo",
            audit_type="full",
            requested_by="alice",
            requested_reason="needs audit",
            urgency="normal",
        ),
        decision=SimpleNamespace(
            decision="approved",
            reasons=reasons if reasons is not None else ["clear"],
        ),
        policy_results=policy_results
        if policy_results is not None
        else [_make_policy("p1", "passed"), _make_policy("p2", "failed", "bad")],
        dispatch_result_summary=dispatch,
        budget_state_summary=budget,
        cooldown_state_summary=cooldown,
    )


def test_inspect_not_found_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mod, "load_governance_report", MagicMock(side_effect=FileNotFoundError("gone"))
    )
    result = runner.invoke(mod.app, ["inspect", "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 1
    assert "Not found" in result.stdout


def test_inspect_load_error_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_governance_report",
        MagicMock(side_effect=GovernanceReportError("corrupt")),
    )
    result = runner.invoke(mod.app, ["inspect", "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 2
    assert "Load error" in result.stdout


def test_inspect_minimal_report(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "load_governance_report", MagicMock(return_value=_make_report()))
    result = runner.invoke(mod.app, ["inspect", "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 0
    assert "Governance Report" in result.stdout
    assert "example_repo" in result.stdout
    assert "clear" in result.stdout


def test_inspect_full_report_with_summaries(tmp_path, monkeypatch):
    dispatch = SimpleNamespace(run_id="RID", status="completed", error="oops")
    budget = SimpleNamespace(runs_used=2, max_runs=10, runs_remaining=8)
    cooldown = SimpleNamespace(in_cooldown=True, seconds_remaining=120.0)
    report = _make_report(dispatch=dispatch, budget=budget, cooldown=cooldown)
    monkeypatch.setattr(mod, "load_governance_report", MagicMock(return_value=report))
    result = runner.invoke(mod.app, ["inspect", "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 0
    assert "RID" in result.stdout
    assert "oops" in result.stdout
    assert "8 remaining" in result.stdout
    assert "active=YES" in result.stdout


def test_inspect_cooldown_inactive(tmp_path, monkeypatch):
    cooldown = SimpleNamespace(in_cooldown=False, seconds_remaining=0.0)
    report = _make_report(cooldown=cooldown)
    monkeypatch.setattr(mod, "load_governance_report", MagicMock(return_value=report))
    result = runner.invoke(mod.app, ["inspect", "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 0
    assert "active=no" in result.stdout
