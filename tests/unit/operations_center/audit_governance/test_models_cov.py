# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage tests for operations_center.audit_governance.models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from operations_center.audit_dispatch.models import (
    DispatchStatus,
    ManagedAuditDispatchResult,
)
from operations_center.audit_governance import models as m


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _dispatch_result(
    status: DispatchStatus = DispatchStatus.COMPLETED,
) -> ManagedAuditDispatchResult:
    now = datetime.now(UTC)
    return ManagedAuditDispatchResult(
        repo_id="r",
        audit_type="a",
        run_id="run-1",
        status=status,
        started_at=now,
        ended_at=now,
        duration_seconds=1.0,
    )


def _request(**overrides) -> m.AuditGovernanceRequest:
    base = dict(
        repo_id="repo",
        audit_type="lint",
        requested_by="alice",
        requested_reason="because",
    )
    base.update(overrides)
    return m.AuditGovernanceRequest(**base)


def _decision(decision: str = "approved", policy_results=None) -> m.AuditGovernanceDecision:
    return m.AuditGovernanceDecision(
        request_id="req-1",
        repo_id="repo",
        audit_type="lint",
        decision=decision,
        reasons=["ok"],
        policy_results=policy_results or [],
        requires_manual_approval=False,
    )


# ---------------------------------------------------------------------------
# Module-level id helpers
# ---------------------------------------------------------------------------


def test_safe_id_replaces_unsafe_chars():
    assert m._safe_id("a/b c.d") == "a_b_c_d"
    assert m._safe_id("Keep-09_x") == "Keep-09_x"


def test_make_request_id_format(monkeypatch):
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    class _DT:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(m, "datetime", _DT)
    rid = m.make_request_id("my/repo", "type:x")
    assert rid == "my_repo__type_x__20260102_030405"


def test_make_request_id_public_wraps_private():
    rid = m.make_request_id("repo", "lint")
    assert rid.startswith("repo__lint__")
    assert "/" not in rid


# ---------------------------------------------------------------------------
# PolicyResult
# ---------------------------------------------------------------------------


def test_policy_result_defaults_and_frozen():
    pr = m.PolicyResult(policy_name="p", status="passed", reason="ok")
    assert pr.details == ""
    with pytest.raises(ValidationError):
        pr.reason = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AuditGovernanceRequest validators + defaults
# ---------------------------------------------------------------------------


def test_request_defaults():
    r = _request()
    assert r.urgency == "normal"
    assert r.metadata == {}
    assert r.related_recommendation_ids == []
    assert r.allow_if_recent_success is False
    assert r.run_coverage_audit is False
    assert "-" not in r.request_id  # uuid hyphens replaced with underscores
    assert isinstance(r.created_at, datetime)


@pytest.mark.parametrize(
    "field",
    ["repo_id", "audit_type", "requested_reason", "requested_by"],
)
def test_request_empty_field_rejected(field):
    with pytest.raises(ValidationError):
        _request(**{field: "   "})


def test_request_frozen():
    r = _request()
    with pytest.raises(ValidationError):
        r.repo_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AuditGovernanceDecision properties
# ---------------------------------------------------------------------------


def test_decision_is_approved_and_denied():
    assert _decision("approved").is_approved is True
    assert _decision("approved").is_denied is False
    assert _decision("denied").is_denied is True
    assert _decision("denied").is_approved is False


def test_decision_failed_and_warning_policies():
    pr_pass = m.PolicyResult(policy_name="a", status="passed", reason="r")
    pr_fail = m.PolicyResult(policy_name="b", status="failed", reason="r")
    pr_warn = m.PolicyResult(policy_name="c", status="warning", reason="r")
    d = _decision(policy_results=[pr_pass, pr_fail, pr_warn])
    assert d.failed_policies == [pr_fail]
    assert d.warning_policies == [pr_warn]


def test_decision_default_id_present():
    d = _decision()
    assert isinstance(d.decision_id, str) and d.decision_id


# ---------------------------------------------------------------------------
# AuditManualApproval
# ---------------------------------------------------------------------------


def test_manual_approval_defaults():
    ap = m.AuditManualApproval(decision_id="d", request_id="r", approved_by="bob")
    assert ap.approval_notes == ""
    assert isinstance(ap.approved_at, datetime)


def test_manual_approval_empty_approver_rejected():
    with pytest.raises(ValidationError):
        m.AuditManualApproval(decision_id="d", request_id="r", approved_by="  ")


# ---------------------------------------------------------------------------
# AuditBudgetState
# ---------------------------------------------------------------------------


def _budget(max_runs=5, runs_used=0) -> m.AuditBudgetState:
    now = datetime.now(UTC)
    return m.AuditBudgetState(
        repo_id="r",
        audit_type="a",
        period_start=now,
        period_end=now + timedelta(days=7),
        max_runs=max_runs,
        runs_used=runs_used,
    )


def test_budget_runs_remaining_and_exhausted():
    b = _budget(max_runs=3, runs_used=1)
    assert b.runs_remaining == 2
    assert b.is_exhausted is False

    b2 = _budget(max_runs=3, runs_used=3)
    assert b2.runs_remaining == 0
    assert b2.is_exhausted is True

    b3 = _budget(max_runs=3, runs_used=5)
    assert b3.runs_remaining == 0  # clamped at 0
    assert b3.is_exhausted is True


# ---------------------------------------------------------------------------
# AuditCooldownState
# ---------------------------------------------------------------------------


def test_cooldown_no_last_run():
    c = m.AuditCooldownState(repo_id="r", audit_type="a", cooldown_seconds=100.0)
    assert c.is_in_cooldown() is False
    assert c.seconds_remaining() == 0.0


def test_cooldown_active_and_expired():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    c = m.AuditCooldownState(
        repo_id="r",
        audit_type="a",
        cooldown_seconds=100.0,
        last_run_at=now,
    )
    # 40s elapsed -> still in cooldown, 60s remaining
    t_active = now + timedelta(seconds=40)
    assert c.is_in_cooldown(now=t_active) is True
    assert c.seconds_remaining(now=t_active) == pytest.approx(60.0)

    # 150s elapsed -> expired, 0 remaining
    t_expired = now + timedelta(seconds=150)
    assert c.is_in_cooldown(now=t_expired) is False
    assert c.seconds_remaining(now=t_expired) == 0.0


def test_cooldown_defaults_now(monkeypatch):
    # last_run far in the past relative to real now -> not in cooldown
    past = datetime.now(UTC) - timedelta(seconds=10_000)
    c = m.AuditCooldownState(repo_id="r", audit_type="a", cooldown_seconds=1.0, last_run_at=past)
    assert c.is_in_cooldown() is False
    assert c.seconds_remaining() == 0.0


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


def test_budget_and_cooldown_config_defaults():
    assert m.BudgetConfig().max_runs == 10
    assert m.BudgetConfig().period_days == 7
    assert m.CooldownConfig().cooldown_seconds == 3600.0


def test_governance_config_defaults():
    g = m.GovernanceConfig()
    assert g.known_repos == []
    assert g.known_audit_types == {}
    assert isinstance(g.state_dir, Path)
    assert g.require_mini_regression_for_urgency == ["low", "normal"]


def test_governance_config_get_budget_default_and_specific():
    custom = m.BudgetConfig(max_runs=99, period_days=1)
    g = m.GovernanceConfig(budget_config={"repo": {"lint": custom}})
    assert g.get_budget_config("repo", "lint") is custom
    # missing repo -> default
    assert g.get_budget_config("other", "lint") == m.BudgetConfig()
    # missing audit_type within known repo -> default
    assert g.get_budget_config("repo", "other") == m.BudgetConfig()


def test_governance_config_get_cooldown_default_and_specific():
    custom = m.CooldownConfig(cooldown_seconds=42.0)
    g = m.GovernanceConfig(cooldown_config={"repo": {"lint": custom}})
    assert g.get_cooldown_config("repo", "lint") is custom
    assert g.get_cooldown_config("other", "lint") == m.CooldownConfig()
    assert g.get_cooldown_config("repo", "other") == m.CooldownConfig()


# ---------------------------------------------------------------------------
# AuditGovernedRunResult
# ---------------------------------------------------------------------------


def test_governed_run_result_not_dispatched():
    res = m.AuditGovernedRunResult(
        request=_request(),
        decision=_decision("denied"),
        governance_status="denied",
    )
    assert res.was_dispatched is False
    assert res.succeeded is False


def test_governed_run_result_dispatched_success():
    res = m.AuditGovernedRunResult(
        request=_request(),
        decision=_decision("approved"),
        governance_status="approved_and_dispatched",
        dispatch_result=_dispatch_result(DispatchStatus.COMPLETED),
    )
    assert res.was_dispatched is True
    assert res.succeeded is True


def test_governed_run_result_dispatched_but_failed_dispatch():
    res = m.AuditGovernedRunResult(
        request=_request(),
        decision=_decision("approved"),
        governance_status="approved_and_dispatched",
        dispatch_result=_dispatch_result(DispatchStatus.FAILED),
    )
    assert res.was_dispatched is True
    assert res.succeeded is False  # dispatch did not succeed


def test_governed_run_result_status_mismatch():
    # dispatch succeeded but governance_status not approved_and_dispatched
    res = m.AuditGovernedRunResult(
        request=_request(),
        decision=_decision("approved"),
        governance_status="dispatch_failed",
        dispatch_result=_dispatch_result(DispatchStatus.COMPLETED),
    )
    assert res.succeeded is False


# ---------------------------------------------------------------------------
# Report summaries
# ---------------------------------------------------------------------------


def test_summary_models_construct():
    ds = m.DispatchResultSummary(run_id="r1", status="completed")
    assert ds.failure_kind is None
    bs = m.BudgetStateSummary(runs_used=1, max_runs=5, runs_remaining=4)
    assert bs.period_start is None
    cs = m.CooldownStateSummary(in_cooldown=False, cooldown_seconds=10.0, seconds_remaining=0.0)
    assert cs.last_run_at is None
    cv = m.CoverageAuditSummary()
    assert cv.findings_total == 0
    assert cv.sample_findings == []


# ---------------------------------------------------------------------------
# AuditGovernanceReport
# ---------------------------------------------------------------------------


def test_report_defaults_and_dispatched_run_id_none():
    rep = m.AuditGovernanceReport(
        request=_request(),
        decision=_decision("denied"),
        policy_results=[],
    )
    assert rep.schema_version == "1.2"
    assert rep.governance_status == "denied"
    assert rep.approval is None
    assert rep.dispatched_run_id is None


def test_report_dispatched_run_id_present():
    rep = m.AuditGovernanceReport(
        request=_request(),
        decision=_decision("approved"),
        policy_results=[],
        dispatch_result_summary=m.DispatchResultSummary(run_id="abc", status="completed"),
    )
    assert rep.dispatched_run_id == "abc"


def test_report_serializes_roundtrip():
    rep = m.AuditGovernanceReport(
        request=_request(),
        decision=_decision("approved"),
        policy_results=[m.PolicyResult(policy_name="p", status="passed", reason="r")],
    )
    dumped = rep.model_dump()
    rebuilt = m.AuditGovernanceReport.model_validate(dumped)
    assert rebuilt.request.repo_id == "repo"
    assert rebuilt.policy_results[0].policy_name == "p"
