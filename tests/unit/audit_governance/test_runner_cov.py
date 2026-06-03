# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.audit_dispatch.models import (
    DispatchStatus,
    FailureKind,
    ManagedAuditDispatchResult,
)
from operations_center.audit_governance import runner as runner_mod
from operations_center.audit_governance.errors import ManualApprovalError
from operations_center.audit_governance.models import (
    AuditBudgetState,
    AuditCooldownState,
    AuditGovernanceDecision,
    AuditGovernanceRequest,
    AuditManualApproval,
    CoverageAuditSummary,
    GovernanceConfig,
    PolicyResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(**kwargs) -> AuditGovernanceRequest:
    defaults: dict = {
        "repo_id": "example_repo",
        "audit_type": "full",
        "requested_by": "operator",
        "requested_reason": "because",
    }
    defaults.update(kwargs)
    return AuditGovernanceRequest(**defaults)


def _make_decision(
    request: AuditGovernanceRequest,
    *,
    decision: str = "approved",
    requires_manual_approval: bool = False,
    policy_results: list[PolicyResult] | None = None,
) -> AuditGovernanceDecision:
    return AuditGovernanceDecision(
        request_id=request.request_id,
        repo_id=request.repo_id,
        audit_type=request.audit_type,
        decision=decision,
        reasons=["r"],
        policy_results=policy_results or [],
        requires_manual_approval=requires_manual_approval,
    )


def _make_dispatch_result(
    *,
    status: DispatchStatus = DispatchStatus.COMPLETED,
    run_id: str | None = "run-1",
    failure_kind: FailureKind | None = None,
    artifact_manifest_path: str | None = "/tmp/manifest.json",
) -> ManagedAuditDispatchResult:
    now = datetime.now(UTC)
    return ManagedAuditDispatchResult(
        repo_id="example_repo",
        audit_type="full",
        run_id=run_id,
        status=status,
        failure_kind=failure_kind,
        started_at=now,
        ended_at=now,
        duration_seconds=1.5,
        artifact_manifest_path=artifact_manifest_path,
        error=None,
    )


def _make_budget_state() -> AuditBudgetState:
    now = datetime.now(UTC)
    return AuditBudgetState(
        repo_id="example_repo",
        audit_type="full",
        period_start=now,
        period_end=now + timedelta(days=7),
        max_runs=10,
        runs_used=2,
    )


def _make_cooldown_state(*, last_run_at: datetime | None = None) -> AuditCooldownState:
    return AuditCooldownState(
        repo_id="example_repo",
        audit_type="full",
        cooldown_seconds=3600.0,
        last_run_at=last_run_at,
    )


@pytest.fixture
def patched(monkeypatch):
    """Patch all runner collaborators with MagicMocks. Returns a namespace dict."""
    mocks: dict = {}

    budget_state = _make_budget_state()
    cooldown_state = _make_cooldown_state()

    mocks["load_budget_state"] = MagicMock(return_value=budget_state)
    mocks["load_cooldown_state"] = MagicMock(return_value=cooldown_state)
    mocks["evaluate_governance_policies"] = MagicMock(return_value=[])
    mocks["make_governance_decision"] = MagicMock()
    mocks["dispatch_managed_audit"] = MagicMock(return_value=_make_dispatch_result())
    mocks["increment_budget_after_dispatch"] = MagicMock(return_value=budget_state)
    mocks["update_cooldown_after_dispatch"] = MagicMock(return_value=cooldown_state)
    mocks["run_post_dispatch_coverage_audit"] = MagicMock()
    # write_governance_report returns a path
    mocks["write_governance_report"] = MagicMock(return_value=Path("/tmp/report.json"))

    monkeypatch.setattr(runner_mod, "load_budget_state", mocks["load_budget_state"])
    monkeypatch.setattr(runner_mod, "load_cooldown_state", mocks["load_cooldown_state"])
    monkeypatch.setattr(
        runner_mod, "evaluate_governance_policies", mocks["evaluate_governance_policies"]
    )
    monkeypatch.setattr(runner_mod, "make_governance_decision", mocks["make_governance_decision"])
    monkeypatch.setattr(runner_mod, "dispatch_managed_audit", mocks["dispatch_managed_audit"])
    monkeypatch.setattr(
        runner_mod, "increment_budget_after_dispatch", mocks["increment_budget_after_dispatch"]
    )
    monkeypatch.setattr(
        runner_mod, "update_cooldown_after_dispatch", mocks["update_cooldown_after_dispatch"]
    )
    monkeypatch.setattr(
        runner_mod, "run_post_dispatch_coverage_audit", mocks["run_post_dispatch_coverage_audit"]
    )
    monkeypatch.setattr(runner_mod, "write_governance_report", mocks["write_governance_report"])
    return mocks


# ---------------------------------------------------------------------------
# _check_approval_request_id
# ---------------------------------------------------------------------------


def test_check_approval_request_id_match():
    req = _make_request()
    approval = AuditManualApproval(decision_id="d", request_id=req.request_id, approved_by="boss")
    # Should not raise.
    assert runner_mod._check_approval_request_id(approval, req) is None


def test_check_approval_request_id_mismatch_raises():
    req = _make_request()
    approval = AuditManualApproval(decision_id="d", request_id="other_id", approved_by="boss")
    with pytest.raises(ManualApprovalError) as exc:
        runner_mod._check_approval_request_id(approval, req)
    assert "does not match" in str(exc.value)


# ---------------------------------------------------------------------------
# _make_budget_summary / _make_cooldown_summary
# ---------------------------------------------------------------------------


def test_make_budget_summary_none():
    assert runner_mod._make_budget_summary(None) is None


def test_make_budget_summary_populated():
    state = _make_budget_state()
    summary = runner_mod._make_budget_summary(state)
    assert summary is not None
    assert summary.runs_used == 2
    assert summary.max_runs == 10
    assert summary.runs_remaining == 8


def test_make_cooldown_summary_none():
    assert runner_mod._make_cooldown_summary(None) is None


def test_make_cooldown_summary_not_in_cooldown():
    state = _make_cooldown_state(last_run_at=None)
    summary = runner_mod._make_cooldown_summary(state)
    assert summary is not None
    assert summary.in_cooldown is False
    assert summary.seconds_remaining == 0.0
    assert summary.cooldown_seconds == 3600.0


def test_make_cooldown_summary_in_cooldown():
    state = _make_cooldown_state(last_run_at=datetime.now(UTC))
    summary = runner_mod._make_cooldown_summary(state)
    assert summary is not None
    assert summary.in_cooldown is True
    assert summary.seconds_remaining > 0.0


# ---------------------------------------------------------------------------
# run_governed_audit: approved happy path
# ---------------------------------------------------------------------------


def test_approved_dispatches_and_updates_state(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")

    result = runner_mod.run_governed_audit(req)

    assert result.governance_status == "approved_and_dispatched"
    assert result.dispatch_result is not None
    patched["dispatch_managed_audit"].assert_called_once()
    patched["increment_budget_after_dispatch"].assert_called_once()
    patched["update_cooldown_after_dispatch"].assert_called_once()
    patched["write_governance_report"].assert_called_once()
    assert result.report_path == "/tmp/report.json"


def test_approved_default_output_dir(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    runner_mod.run_governed_audit(req)
    # write_governance_report called with default path
    _report, out = patched["write_governance_report"].call_args[0]
    assert out == Path("tools/audit/report/governance")


def test_approved_custom_output_dir_string(patched, tmp_path):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    runner_mod.run_governed_audit(req, output_dir=str(tmp_path / "reports"))
    _report, out = patched["write_governance_report"].call_args[0]
    assert out == tmp_path / "reports"


def test_approved_passes_config_and_log_dirs_to_dispatch(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    runner_mod.run_governed_audit(
        req, config_dir="/cfg", log_dir="/logs", dispatch_timeout_seconds=42.0
    )
    call = patched["dispatch_managed_audit"].call_args
    assert call.kwargs["config_dir"] == "/cfg"
    assert call.kwargs["log_dir"] == "/logs"
    dispatch_req = call.args[0]
    assert dispatch_req.timeout_seconds == 42.0
    assert dispatch_req.correlation_id == req.request_id


# ---------------------------------------------------------------------------
# State loading error handling
# ---------------------------------------------------------------------------


def test_budget_load_failure_is_non_fatal(patched):
    req = _make_request()
    patched["load_budget_state"].side_effect = RuntimeError("boom")
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "approved_and_dispatched"
    # budget_state passed to policies should be None
    assert patched["evaluate_governance_policies"].call_args.kwargs["budget_state"] is None


def test_cooldown_load_failure_is_non_fatal(patched):
    req = _make_request()
    patched["load_cooldown_state"].side_effect = RuntimeError("boom")
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "approved_and_dispatched"
    assert patched["evaluate_governance_policies"].call_args.kwargs["cooldown_state"] is None


# ---------------------------------------------------------------------------
# Manual approval required
# ---------------------------------------------------------------------------


def test_needs_manual_approval_no_approval(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(
        req, decision="needs_manual_approval", requires_manual_approval=True
    )
    result = runner_mod.run_governed_audit(req, approval=None)
    assert result.governance_status == "needs_manual_approval"
    patched["dispatch_managed_audit"].assert_not_called()
    # report written with needs_manual_approval status
    report = patched["write_governance_report"].call_args[0][0]
    assert report.governance_status == "needs_manual_approval"


def test_needs_manual_approval_with_invalid_approval_denied(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(
        req, decision="needs_manual_approval", requires_manual_approval=True
    )
    approval = AuditManualApproval(decision_id="d", request_id="WRONG", approved_by="boss")
    result = runner_mod.run_governed_audit(req, approval=approval)
    assert result.governance_status == "denied"
    assert result.decision.decision == "denied"
    assert any("Manual approval validation failed" in r for r in result.decision.reasons)
    patched["dispatch_managed_audit"].assert_not_called()


def test_needs_manual_approval_with_valid_approval_dispatches(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(
        req, decision="needs_manual_approval", requires_manual_approval=True
    )
    approval = AuditManualApproval(decision_id="d", request_id=req.request_id, approved_by="boss")
    result = runner_mod.run_governed_audit(req, approval=approval)
    assert result.governance_status == "approved_and_dispatched"
    patched["dispatch_managed_audit"].assert_called_once()
    # report carries the approval
    report = patched["write_governance_report"].call_args[0][0]
    assert report.approval is approval


# ---------------------------------------------------------------------------
# Non-approved, non-manual decisions
# ---------------------------------------------------------------------------


def test_denied_decision_no_dispatch(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="denied")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "denied"
    patched["dispatch_managed_audit"].assert_not_called()


def test_deferred_decision_maps_to_deferred(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="deferred")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "deferred"
    patched["dispatch_managed_audit"].assert_not_called()


def test_unknown_decision_value_defaults_to_denied(patched):
    req = _make_request()
    # decision string not in status_map -> default "denied"
    bad = _make_decision(req, decision="approved")
    object.__setattr__(bad, "decision", "weird")
    object.__setattr__(bad, "requires_manual_approval", False)
    patched["make_governance_decision"].return_value = bad
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "denied"
    patched["dispatch_managed_audit"].assert_not_called()


# ---------------------------------------------------------------------------
# Dispatch infrastructure failure
# ---------------------------------------------------------------------------


def test_dispatch_raises_returns_dispatch_failed(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["dispatch_managed_audit"].side_effect = RuntimeError("lock held")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "dispatch_failed"
    report = patched["write_governance_report"].call_args[0][0]
    assert report.dispatch_result_summary is not None
    assert report.dispatch_result_summary.status == "failed"
    assert "lock held" in report.dispatch_result_summary.error
    # state not updated
    patched["increment_budget_after_dispatch"].assert_not_called()


# ---------------------------------------------------------------------------
# Post-dispatch state update failures (non-fatal)
# ---------------------------------------------------------------------------


def test_budget_update_failure_non_fatal(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["increment_budget_after_dispatch"].side_effect = RuntimeError("disk full")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "approved_and_dispatched"


def test_cooldown_update_failure_non_fatal(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["update_cooldown_after_dispatch"].side_effect = RuntimeError("disk full")
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "approved_and_dispatched"


# ---------------------------------------------------------------------------
# Dispatch summary fields
# ---------------------------------------------------------------------------


def test_dispatch_summary_includes_failure_kind(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["dispatch_managed_audit"].return_value = _make_dispatch_result(
        status=DispatchStatus.FAILED,
        failure_kind=FailureKind.PROCESS_TIMEOUT,
        artifact_manifest_path=None,
    )
    runner_mod.run_governed_audit(req)
    report = patched["write_governance_report"].call_args[0][0]
    assert report.dispatch_result_summary.status == "failed"
    assert report.dispatch_result_summary.failure_kind == "process_timeout"


def test_dispatch_summary_no_failure_kind(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["dispatch_managed_audit"].return_value = _make_dispatch_result(failure_kind=None)
    runner_mod.run_governed_audit(req)
    report = patched["write_governance_report"].call_args[0][0]
    assert report.dispatch_result_summary.failure_kind is None


# ---------------------------------------------------------------------------
# Coverage audit branch
# ---------------------------------------------------------------------------


def test_coverage_audit_runs_when_opted_in(patched, monkeypatch):
    req = _make_request(run_coverage_audit=True)
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    cov = CoverageAuditSummary(findings_total=3)
    patched["run_post_dispatch_coverage_audit"].return_value = cov
    resolve = MagicMock(return_value=Path("/repo/root"))
    monkeypatch.setattr(runner_mod, "_resolve_consuming_repo_root", resolve)

    runner_mod.run_governed_audit(req)

    patched["run_post_dispatch_coverage_audit"].assert_called_once()
    resolve.assert_called_once_with(req.repo_id, None)
    report = patched["write_governance_report"].call_args[0][0]
    assert report.coverage_audit_summary is cov


def test_coverage_audit_skipped_when_not_opted_in(patched):
    req = _make_request(run_coverage_audit=False)
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    runner_mod.run_governed_audit(req)
    patched["run_post_dispatch_coverage_audit"].assert_not_called()
    report = patched["write_governance_report"].call_args[0][0]
    assert report.coverage_audit_summary is None


def test_coverage_audit_skipped_when_dispatch_failed_status(patched):
    req = _make_request(run_coverage_audit=True)
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["dispatch_managed_audit"].return_value = _make_dispatch_result(
        status=DispatchStatus.FAILED, artifact_manifest_path="/tmp/m.json"
    )
    runner_mod.run_governed_audit(req)
    patched["run_post_dispatch_coverage_audit"].assert_not_called()


def test_coverage_audit_skipped_when_no_manifest(patched):
    req = _make_request(run_coverage_audit=True)
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    patched["dispatch_managed_audit"].return_value = _make_dispatch_result(
        artifact_manifest_path=None
    )
    runner_mod.run_governed_audit(req)
    patched["run_post_dispatch_coverage_audit"].assert_not_called()


def test_coverage_audit_exception_captured_as_summary(patched, monkeypatch):
    req = _make_request(run_coverage_audit=True)
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    monkeypatch.setattr(
        runner_mod,
        "_resolve_consuming_repo_root",
        MagicMock(side_effect=RuntimeError("no such repo")),
    )
    result = runner_mod.run_governed_audit(req)
    assert result.governance_status == "approved_and_dispatched"
    report = patched["write_governance_report"].call_args[0][0]
    assert report.coverage_audit_summary is not None
    assert "no such repo" in report.coverage_audit_summary.error


# ---------------------------------------------------------------------------
# _write_report_safe
# ---------------------------------------------------------------------------


def test_write_report_safe_delegates(monkeypatch):
    sentinel = Path("/tmp/x.json")
    mock = MagicMock(return_value=sentinel)
    monkeypatch.setattr(runner_mod, "write_governance_report", mock)
    report = MagicMock()
    out = Path("/out")
    assert runner_mod._write_report_safe(report, out) is sentinel
    mock.assert_called_once_with(report, out)


# ---------------------------------------------------------------------------
# _resolve_consuming_repo_root
# ---------------------------------------------------------------------------


def test_resolve_consuming_repo_root(monkeypatch):
    config = MagicMock()
    config.repo_root = "managed/foo"
    loader_mock = MagicMock(return_value=config)
    import operations_center.managed_repos.loader as loader_module

    monkeypatch.setattr(loader_module, "load_managed_repo_config", loader_mock)

    result = runner_mod._resolve_consuming_repo_root("foo_repo", "/cfg")

    loader_mock.assert_called_once_with("foo_repo", config_dir="/cfg")
    assert result == (runner_mod._OC_ROOT / "managed/foo").resolve()


# ---------------------------------------------------------------------------
# GovernanceConfig is wired through
# ---------------------------------------------------------------------------


def test_governance_config_state_dir_used(patched):
    req = _make_request()
    patched["make_governance_decision"].return_value = _make_decision(req, decision="approved")
    cfg = GovernanceConfig(state_dir=Path("/custom/state"))
    runner_mod.run_governed_audit(req, governance_config=cfg)
    # load_budget_state first positional arg is the state_dir
    assert patched["load_budget_state"].call_args.args[0] == Path("/custom/state")
