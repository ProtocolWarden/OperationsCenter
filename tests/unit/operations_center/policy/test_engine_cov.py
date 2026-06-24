# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path


from operations_center.contracts.common import (
    BranchPolicy,
    TaskTarget,
    ValidationProfile,
)
from operations_center.contracts.enums import (
    BackendName,
    ExecutionMode,
    LaneName,
    RiskLevel,
    TaskType,
)
from operations_center.contracts.execution import OcExecutionRequest
from operations_center.contracts.proposal import OcPlanningProposal
from operations_center.contracts.routing import OcRoutingDecision
from operations_center.policy.engine import (
    PolicyEngine,
    _build_notes,
    _check_branch_guardrail,
    _check_path_restrictions,
    _check_repo_enabled,
    _check_review_requirements,
    _check_routing_constraints,
    _check_task_type,
    _check_tool_guardrail,
    _check_validation_requirements,
    _determine_status,
    _effective_review_requirement,
    _effective_scope,
    _effective_validation_profile,
    _match_path_rule,
    _validation_req_applies,
)
from operations_center.policy.models import (
    BranchGuardrail,
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    PolicyDecision,
    PolicyStatus,
    PolicyViolation,
    PolicyWarning,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
    ValidationRequirement,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_proposal(
    *,
    repo_key: str = "repo-a",
    base_branch: str = "main",
    allowed_paths: list[str] | None = None,
    task_type: TaskType = TaskType.SIMPLE_EDIT,
    risk_level: RiskLevel = RiskLevel.LOW,
    labels: list[str] | None = None,
    branch_prefix: str = "auto/",
    open_pr: bool = False,
    validation_commands: list[str] | None = None,
) -> OcPlanningProposal:
    return OcPlanningProposal(
        task_id="t-1",
        project_id="p-1",
        task_type=task_type,
        execution_mode=ExecutionMode.GOAL,
        goal_text="do the thing",
        target=TaskTarget(
            repo_key=repo_key,
            clone_url="https://example.invalid/r.git",
            base_branch=base_branch,
            allowed_paths=allowed_paths or [],
        ),
        risk_level=risk_level,
        validation_profile=ValidationProfile(
            profile_name="default",
            commands=validation_commands or [],
        ),
        branch_policy=BranchPolicy(branch_prefix=branch_prefix, open_pr=open_pr),
        labels=labels or [],
    )


def _make_decision(lane: LaneName = LaneName.AIDER_LOCAL) -> OcRoutingDecision:
    backend = BackendName.AIDER_LOCAL if lane == LaneName.AIDER_LOCAL else BackendName.OPENCLAW
    return OcRoutingDecision(
        proposal_id="prop-1",
        selected_lane=lane,
        selected_backend=backend,
    )


def _make_request(
    *,
    allowed_paths: list[str] | None = None,
    validation_commands: list[str] | None = None,
    tmp_path: Path | None = None,
) -> OcExecutionRequest:
    return OcExecutionRequest(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="do the thing",
        repo_key="repo-a",
        clone_url="https://example.invalid/r.git",
        base_branch="main",
        task_branch="auto/x",
        workspace_path=(tmp_path or Path("/tmp")) / "ws",
        allowed_paths=allowed_paths or [],
        validation_commands=validation_commands or [],
    )


def _simple_policy(**kwargs) -> RepoPolicy:
    """A maximally-permissive policy unless overridden."""
    defaults: dict = dict(
        repo_key="repo-a",
        enabled=True,
        path_policy=PathPolicy(rules=[], default_mode="allow"),
        branch_guardrail=BranchGuardrail(allow_direct_commit=True, require_branch=False),
        tool_guardrail=ToolGuardrail(network_mode="allowed", allow_destructive_actions=True),
        validation_requirements=[],
        review_requirement=ReviewRequirement(autonomous_allowed=True),
    )
    defaults.update(kwargs)
    return RepoPolicy(**defaults)


def _engine_with(policy: RepoPolicy) -> PolicyEngine:
    return PolicyEngine.from_config(PolicyConfig(repo_policies=[policy], default_policy=policy))


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def test_from_defaults_builds_engine():
    engine = PolicyEngine.from_defaults()
    assert isinstance(engine, PolicyEngine)


def test_from_config_builds_engine():
    cfg = PolicyConfig(repo_policies=[_simple_policy()])
    engine = PolicyEngine.from_config(cfg)
    assert isinstance(engine, PolicyEngine)


# ---------------------------------------------------------------------------
# Happy path / clean allow
# ---------------------------------------------------------------------------


def test_clean_allow():
    engine = _engine_with(_simple_policy())
    decision = engine.evaluate(_make_proposal(), _make_decision())
    assert decision.status == PolicyStatus.ALLOW
    assert decision.is_allowed
    assert not decision.is_blocked
    assert decision.violations == []
    assert decision.warnings == []
    assert decision.effective_validation_profile == "standard"
    assert decision.effective_review_requirement == "autonomous"
    assert decision.notes == ""


# ---------------------------------------------------------------------------
# 1. Repo enabled (early return on blocking)
# ---------------------------------------------------------------------------


def test_repo_disabled_blocks_and_short_circuits():
    engine = _engine_with(_simple_policy(enabled=False))
    decision = engine.evaluate(_make_proposal(), _make_decision())
    assert decision.is_blocked
    assert any(v.rule_id == "repo.disabled" for v in decision.violations)
    # short-circuit: only the one violation
    assert len(decision.violations) == 1


def test_check_repo_enabled_when_enabled_adds_nothing():
    out: list[PolicyViolation] = []
    _check_repo_enabled(_simple_policy(enabled=True), out)
    assert out == []


# ---------------------------------------------------------------------------
# 2. Task type restrictions
# ---------------------------------------------------------------------------


def test_blocked_task_type():
    pol = _simple_policy(blocked_task_types=["simple_edit"])
    engine = _engine_with(pol)
    decision = engine.evaluate(_make_proposal(task_type=TaskType.SIMPLE_EDIT), _make_decision())
    assert decision.is_blocked
    assert any(v.rule_id == "task_type.blocked" for v in decision.violations)


def test_task_type_not_in_allowlist():
    pol = _simple_policy(allowed_task_types=["bug_fix"])
    engine = _engine_with(pol)
    decision = engine.evaluate(_make_proposal(task_type=TaskType.SIMPLE_EDIT), _make_decision())
    assert decision.is_blocked
    assert any(v.rule_id == "task_type.not_in_allowlist" for v in decision.violations)


def test_task_type_in_allowlist_ok():
    pol = _simple_policy(allowed_task_types=["simple_edit"])
    out: list[PolicyViolation] = []
    _check_task_type(_make_proposal(task_type=TaskType.SIMPLE_EDIT), pol, out)
    assert out == []


# ---------------------------------------------------------------------------
# 3. Routing constraints
# ---------------------------------------------------------------------------


def test_routing_local_only_violated_on_remote_lane():
    out: list[PolicyViolation] = []
    _check_routing_constraints(
        _make_proposal(labels=["local_only"]),
        _make_decision(LaneName.CLAUDE_CLI),
        out,
    )
    assert any(v.rule_id == "routing.local_only_violated" for v in out)
    assert out[0].blocking


def test_routing_local_only_satisfied_on_local_lane():
    out: list[PolicyViolation] = []
    _check_routing_constraints(
        _make_proposal(labels=["no_remote"]),
        _make_decision(LaneName.AIDER_LOCAL),
        out,
    )
    assert out == []


def test_routing_no_local_label_skips():
    out: list[PolicyViolation] = []
    _check_routing_constraints(
        _make_proposal(labels=["whatever"]),
        _make_decision(LaneName.CLAUDE_CLI),
        out,
    )
    assert out == []


# ---------------------------------------------------------------------------
# 4. Path restrictions
# ---------------------------------------------------------------------------


def test_no_paths_default_block():
    pol = _simple_policy(path_policy=PathPolicy(rules=[], default_mode="block"))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=[]), pol, None, v, w)
    assert any(x.rule_id == "path.unrestricted_writes_blocked" for x in v)


def test_no_paths_default_review_required_warns():
    pol = _simple_policy(path_policy=PathPolicy(rules=[], default_mode="review_required"))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=[]), pol, None, v, w)
    assert v == []
    assert any(x.rule_id == "path.unrestricted_writes_review" for x in w)


def test_no_paths_default_allow_silent():
    pol = _simple_policy(path_policy=PathPolicy(rules=[], default_mode="allow"))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=[]), pol, None, v, w)
    assert v == [] and w == []


def test_path_no_rule_match_default_block():
    pol = _simple_policy(path_policy=PathPolicy(rules=[], default_mode="block"))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["src/x.py"]), pol, None, v, w)
    assert any(x.rule_id == "path.default_blocked" for x in v)


def test_path_no_rule_match_default_review_required():
    pol = _simple_policy(path_policy=PathPolicy(rules=[], default_mode="review_required"))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["src/x.py"]), pol, None, v, w)
    rule = next(x for x in v if x.rule_id == "path.default_review_required")
    assert rule.blocking is False
    assert rule.related_path == "src/x.py"


def test_path_no_rule_match_default_allow_silent():
    pol = _simple_policy(path_policy=PathPolicy(rules=[], default_mode="allow"))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["src/x.py"]), pol, None, v, w)
    assert v == [] and w == []


def test_path_matched_block_rule():
    pol = _simple_policy(
        path_policy=PathPolicy(
            rules=[PathScopeRule(path_pattern="secrets/*", access_mode="block")],
            default_mode="allow",
        )
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["secrets/key"]), pol, None, v, w)
    assert any(x.rule_id == "path.blocked" and x.blocking for x in v)


def test_path_matched_review_rule():
    pol = _simple_policy(
        path_policy=PathPolicy(
            rules=[PathScopeRule(path_pattern="*.env", access_mode="review_required")],
            default_mode="allow",
        )
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["app.env"]), pol, None, v, w)
    rule = next(x for x in v if x.rule_id == "path.review_required")
    assert rule.blocking is False


def test_path_matched_read_only_rule():
    pol = _simple_policy(
        path_policy=PathPolicy(
            rules=[PathScopeRule(path_pattern="ro/*", access_mode="read_only")],
            default_mode="allow",
        )
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["ro/file"]), pol, None, v, w)
    assert any(x.rule_id == "path.review_required" for x in v)


def test_path_matched_allow_rule_silent():
    pol = _simple_policy(
        path_policy=PathPolicy(
            rules=[PathScopeRule(path_pattern="ok/*", access_mode="allow")],
            default_mode="block",
        )
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_path_restrictions(_make_proposal(allowed_paths=["ok/file"]), pol, None, v, w)
    assert v == [] and w == []


def test_path_request_overrides_proposal(tmp_path):
    pol = _simple_policy(
        path_policy=PathPolicy(
            rules=[PathScopeRule(path_pattern="secrets/*", access_mode="block")],
            default_mode="allow",
        )
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    req = _make_request(allowed_paths=["secrets/key"], tmp_path=tmp_path)
    # proposal has a benign path, request has the sensitive one — request wins
    _check_path_restrictions(_make_proposal(allowed_paths=["ok/file"]), pol, req, v, w)
    assert any(x.rule_id == "path.blocked" for x in v)


# ---------------------------------------------------------------------------
# _match_path_rule
# ---------------------------------------------------------------------------


def test_match_path_rule_first_match_wins():
    pp = PathPolicy(
        rules=[
            PathScopeRule(path_pattern="a/*", access_mode="allow"),
            PathScopeRule(path_pattern="a/b", access_mode="block"),
        ]
    )
    rule = _match_path_rule("a/b", pp, "simple_edit")
    assert rule.access_mode == "allow"


def test_match_path_rule_task_type_filter_skips():
    pp = PathPolicy(
        rules=[
            PathScopeRule(
                path_pattern="a/*", access_mode="block", applies_to_task_types=["feature"]
            )
        ]
    )
    # task type does not match the rule's applies_to_task_types
    assert _match_path_rule("a/x", pp, "simple_edit") is None
    # matching task type uses the rule
    assert _match_path_rule("a/x", pp, "feature").access_mode == "block"


def test_match_path_rule_no_match_returns_none():
    pp = PathPolicy(rules=[PathScopeRule(path_pattern="z/*", access_mode="block")])
    assert _match_path_rule("a/x", pp, "simple_edit") is None


# ---------------------------------------------------------------------------
# 5. Branch guardrail
# ---------------------------------------------------------------------------


def test_branch_no_prefix_warns():
    pol = _simple_policy(branch_guardrail=BranchGuardrail(allow_direct_commit=False))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(branch_prefix=""), pol, v, w)
    assert any(x.rule_id == "branch.no_prefix_set" for x in w)


def test_branch_prefix_present_no_warn():
    pol = _simple_policy(branch_guardrail=BranchGuardrail(allow_direct_commit=False))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(branch_prefix="auto/"), pol, v, w)
    assert not any(x.rule_id == "branch.no_prefix_set" for x in w)


def test_branch_allow_direct_commit_no_warn():
    pol = _simple_policy(branch_guardrail=BranchGuardrail(allow_direct_commit=True))
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(branch_prefix=""), pol, v, w)
    assert w == []


def test_branch_base_not_allowed_blocks():
    pol = _simple_policy(
        branch_guardrail=BranchGuardrail(allow_direct_commit=True, allowed_base_branches=["main"])
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(base_branch="dev"), pol, v, w)
    assert any(x.rule_id == "branch.base_branch_not_allowed" and x.blocking for x in v)


def test_branch_base_allowed_ok():
    pol = _simple_policy(
        branch_guardrail=BranchGuardrail(allow_direct_commit=True, allowed_base_branches=["main"])
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(base_branch="main"), pol, v, w)
    assert v == []


def test_branch_pr_required_but_missing():
    pol = _simple_policy(
        branch_guardrail=BranchGuardrail(allow_direct_commit=True, require_pr=True)
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(open_pr=False), pol, v, w)
    rule = next(x for x in v if x.rule_id == "branch.pr_required")
    assert rule.blocking is False


def test_branch_pr_required_and_present_ok():
    pol = _simple_policy(
        branch_guardrail=BranchGuardrail(allow_direct_commit=True, require_pr=True)
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_branch_guardrail(_make_proposal(open_pr=True), pol, v, w)
    assert not any(x.rule_id == "branch.pr_required" for x in v)


# ---------------------------------------------------------------------------
# 6. Tool guardrail
# ---------------------------------------------------------------------------


def test_tool_network_local_only_remote_blocks():
    pol = _simple_policy(tool_guardrail=ToolGuardrail(network_mode="local_only"))
    v: list[PolicyViolation] = []
    _check_tool_guardrail(_make_proposal(), _make_decision(LaneName.CLAUDE_CLI), pol, v)
    assert any(x.rule_id == "tool.network_local_only" for x in v)


def test_tool_network_local_only_local_ok():
    pol = _simple_policy(tool_guardrail=ToolGuardrail(network_mode="local_only"))
    v: list[PolicyViolation] = []
    _check_tool_guardrail(_make_proposal(), _make_decision(LaneName.AIDER_LOCAL), pol, v)
    assert v == []


def test_tool_network_blocked():
    pol = _simple_policy(tool_guardrail=ToolGuardrail(network_mode="blocked"))
    v: list[PolicyViolation] = []
    _check_tool_guardrail(_make_proposal(), _make_decision(LaneName.AIDER_LOCAL), pol, v)
    assert any(x.rule_id == "tool.network_blocked" for x in v)


def test_tool_network_allowed_silent():
    pol = _simple_policy(tool_guardrail=ToolGuardrail(network_mode="allowed"))
    v: list[PolicyViolation] = []
    _check_tool_guardrail(_make_proposal(), _make_decision(LaneName.CLAUDE_CLI), pol, v)
    assert v == []


def test_tool_destructive_blocked():
    pol = _simple_policy(
        tool_guardrail=ToolGuardrail(network_mode="allowed", allow_destructive_actions=False)
    )
    v: list[PolicyViolation] = []
    _check_tool_guardrail(
        _make_proposal(labels=["Force_Push"]), _make_decision(LaneName.AIDER_LOCAL), pol, v
    )
    assert any(x.rule_id == "tool.destructive_blocked" for x in v)


def test_tool_destructive_allowed_silent():
    pol = _simple_policy(
        tool_guardrail=ToolGuardrail(network_mode="allowed", allow_destructive_actions=True)
    )
    v: list[PolicyViolation] = []
    _check_tool_guardrail(
        _make_proposal(labels=["force_push"]), _make_decision(LaneName.AIDER_LOCAL), pol, v
    )
    assert v == []


# ---------------------------------------------------------------------------
# 7. Validation requirements
# ---------------------------------------------------------------------------


def test_validation_required_unavailable_blocks():
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(
                applies_to_risk_levels=["high"],
                required_profile="strict",
                block_if_unavailable=True,
            )
        ]
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_validation_requirements(
        _make_proposal(risk_level=RiskLevel.HIGH, validation_commands=[]), pol, None, v, w
    )
    assert any(x.rule_id == "validation.required_unavailable" for x in v)


def test_validation_recommended_unavailable_warns():
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(
                applies_to_risk_levels=["medium"],
                required_profile="standard",
                block_if_unavailable=False,
            )
        ]
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_validation_requirements(
        _make_proposal(risk_level=RiskLevel.MEDIUM, validation_commands=[]), pol, None, v, w
    )
    assert any(x.rule_id == "validation.recommended_unavailable" for x in w)


def test_validation_available_via_proposal_silent():
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(applies_to_risk_levels=["high"], block_if_unavailable=True)
        ]
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_validation_requirements(
        _make_proposal(risk_level=RiskLevel.HIGH, validation_commands=["pytest"]), pol, None, v, w
    )
    assert v == [] and w == []


def test_validation_available_via_request_silent(tmp_path):
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(applies_to_risk_levels=["high"], block_if_unavailable=True)
        ]
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    req = _make_request(validation_commands=["pytest"], tmp_path=tmp_path)
    _check_validation_requirements(
        _make_proposal(risk_level=RiskLevel.HIGH, validation_commands=[]), pol, req, v, w
    )
    assert v == [] and w == []


def test_validation_no_applicable_requirement():
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(applies_to_risk_levels=["high"], block_if_unavailable=True)
        ]
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_validation_requirements(
        _make_proposal(risk_level=RiskLevel.LOW, validation_commands=[]), pol, None, v, w
    )
    assert v == [] and w == []


def test_validation_only_first_match_used():
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(applies_to_risk_levels=["high"], block_if_unavailable=True),
            ValidationRequirement(applies_to_risk_levels=["high"], block_if_unavailable=True),
        ]
    )
    v: list[PolicyViolation] = []
    w: list[PolicyWarning] = []
    _check_validation_requirements(
        _make_proposal(risk_level=RiskLevel.HIGH, validation_commands=[]), pol, None, v, w
    )
    assert len(v) == 1


# ---------------------------------------------------------------------------
# _validation_req_applies
# ---------------------------------------------------------------------------


def test_validation_req_applies_matrix():
    vr_all = ValidationRequirement()
    assert _validation_req_applies(vr_all, "low", "feature") is True

    vr_risk = ValidationRequirement(applies_to_risk_levels=["high"])
    assert _validation_req_applies(vr_risk, "high", "x") is True
    assert _validation_req_applies(vr_risk, "low", "x") is False

    vr_type = ValidationRequirement(applies_to_task_types=["feature"])
    assert _validation_req_applies(vr_type, "low", "feature") is True
    assert _validation_req_applies(vr_type, "low", "bug_fix") is False


# ---------------------------------------------------------------------------
# 8. Review requirements
# ---------------------------------------------------------------------------


def test_review_blocked_without_human():
    pol = _simple_policy(review_requirement=ReviewRequirement(blocked_without_human=True))
    v: list[PolicyViolation] = []
    _check_review_requirements(_make_proposal(), pol, v)
    assert len(v) == 1
    assert v[0].rule_id == "review.blocked_without_human" and v[0].blocking


def test_review_required_when_not_autonomous():
    pol = _simple_policy(review_requirement=ReviewRequirement(autonomous_allowed=False))
    v: list[PolicyViolation] = []
    _check_review_requirements(_make_proposal(), pol, v)
    assert any(x.rule_id == "review.required" and not x.blocking for x in v)


def test_review_required_label():
    pol = _simple_policy(review_requirement=ReviewRequirement(autonomous_allowed=True))
    v: list[PolicyViolation] = []
    _check_review_requirements(_make_proposal(labels=["review_required"]), pol, v)
    assert any(x.rule_id == "review.required" for x in v)


def test_review_required_by_risk():
    pol = _simple_policy(
        review_requirement=ReviewRequirement(require_review_for_risk_levels=["high"])
    )
    v: list[PolicyViolation] = []
    _check_review_requirements(_make_proposal(risk_level=RiskLevel.HIGH), pol, v)
    assert any(x.rule_id == "review.required" for x in v)


def test_review_required_by_task_type():
    pol = _simple_policy(
        review_requirement=ReviewRequirement(require_review_for_task_types=["feature"])
    )
    v: list[PolicyViolation] = []
    _check_review_requirements(_make_proposal(task_type=TaskType.FEATURE), pol, v)
    assert any(x.rule_id == "review.required" for x in v)


def test_review_trusted_source_bypasses_risk_gate():
    pol = _simple_policy(
        review_requirement=ReviewRequirement(require_review_for_risk_levels=["high"])
    )
    v: list[PolicyViolation] = []
    _check_review_requirements(
        _make_proposal(risk_level=RiskLevel.HIGH, labels=["source: autonomy"]), pol, v
    )
    assert v == []


def test_review_trusted_source_still_honors_review_required_label():
    pol = _simple_policy(
        review_requirement=ReviewRequirement(require_review_for_risk_levels=["high"])
    )
    v: list[PolicyViolation] = []
    _check_review_requirements(
        _make_proposal(risk_level=RiskLevel.HIGH, labels=["source: autonomy", "review_required"]),
        pol,
        v,
    )
    assert any(x.rule_id == "review.required" for x in v)


def test_review_clean_no_violation():
    pol = _simple_policy(review_requirement=ReviewRequirement(autonomous_allowed=True))
    v: list[PolicyViolation] = []
    _check_review_requirements(_make_proposal(risk_level=RiskLevel.LOW), pol, v)
    assert v == []


# ---------------------------------------------------------------------------
# Decision builder helpers
# ---------------------------------------------------------------------------


def test_determine_status_block():
    assert _determine_status(
        [PolicyViolation(rule_id="r", category="c", blocking=True, message="m")], []
    ) == (PolicyStatus.BLOCK)


def test_determine_status_require_review():
    assert (
        _determine_status(
            [PolicyViolation(rule_id="r", category="c", blocking=False, message="m")], []
        )
        == PolicyStatus.REQUIRE_REVIEW
    )


def test_determine_status_allow_with_warnings():
    assert _determine_status([], [PolicyWarning(rule_id="r", category="c", message="m")]) == (
        PolicyStatus.ALLOW_WITH_WARNINGS
    )


def test_determine_status_allow():
    assert _determine_status([], []) == PolicyStatus.ALLOW


def test_effective_validation_profile_match():
    pol = _simple_policy(
        validation_requirements=[
            ValidationRequirement(applies_to_risk_levels=["high"], required_profile="strict")
        ]
    )
    assert _effective_validation_profile(_make_proposal(risk_level=RiskLevel.HIGH), pol, None) == (
        "strict"
    )


def test_effective_validation_profile_default():
    pol = _simple_policy(validation_requirements=[])
    assert _effective_validation_profile(_make_proposal(), pol, None) == "standard"


def test_effective_review_requirement_from_violation():
    pol = _simple_policy()
    v = [PolicyViolation(rule_id="review.required", category="review", blocking=False, message="m")]
    assert _effective_review_requirement(_make_proposal(), pol, v) == "required"


def test_effective_review_requirement_from_policy_flag():
    pol = _simple_policy(review_requirement=ReviewRequirement(autonomous_allowed=False))
    assert _effective_review_requirement(_make_proposal(), pol, []) == "required"


def test_effective_review_requirement_autonomous():
    pol = _simple_policy(review_requirement=ReviewRequirement(autonomous_allowed=True))
    assert _effective_review_requirement(_make_proposal(), pol, []) == "autonomous"


def test_effective_scope_request_priority(tmp_path):
    req = _make_request(allowed_paths=["a/*"], tmp_path=tmp_path)
    assert _effective_scope(_make_proposal(allowed_paths=["b/*"]), req) == ["a/*"]


def test_effective_scope_proposal_fallback():
    assert _effective_scope(_make_proposal(allowed_paths=["b/*"]), None) == ["b/*"]


def test_build_notes_violations_and_warnings():
    notes = _build_notes(
        [PolicyViolation(rule_id="v1", category="c", blocking=True, message="m")],
        [PolicyWarning(rule_id="w1", category="c", message="m")],
    )
    assert "violations: v1" in notes and "warnings: w1" in notes


def test_build_notes_empty():
    assert _build_notes([], []) == ""


# ---------------------------------------------------------------------------
# End-to-end evaluate combinations
# ---------------------------------------------------------------------------


def test_evaluate_require_review_status():
    pol = _simple_policy(review_requirement=ReviewRequirement(autonomous_allowed=False))
    engine = _engine_with(pol)
    decision = engine.evaluate(_make_proposal(), _make_decision())
    assert decision.requires_review
    assert decision.effective_review_requirement == "required"


def test_evaluate_allow_with_warnings_status():
    pol = _simple_policy(branch_guardrail=BranchGuardrail(allow_direct_commit=False))
    engine = _engine_with(pol)
    decision = engine.evaluate(_make_proposal(branch_prefix=""), _make_decision())
    assert decision.status == PolicyStatus.ALLOW_WITH_WARNINGS
    assert isinstance(decision, PolicyDecision)


def test_evaluate_block_status_full_pipeline():
    pol = _simple_policy(tool_guardrail=ToolGuardrail(network_mode="blocked"))
    engine = _engine_with(pol)
    decision = engine.evaluate(_make_proposal(), _make_decision())
    assert decision.is_blocked
    assert "tool.network_blocked" in decision.notes


def test_evaluate_with_request(tmp_path):
    pol = _simple_policy()
    engine = _engine_with(pol)
    req = _make_request(allowed_paths=["src/*"], tmp_path=tmp_path)
    decision = engine.evaluate(_make_proposal(), _make_decision(), req)
    assert decision.effective_scope == ["src/*"]


def test_evaluate_uses_default_policy_for_unknown_repo():
    engine = PolicyEngine.from_defaults()
    decision = engine.evaluate(
        _make_proposal(repo_key="never-configured", risk_level=RiskLevel.LOW), _make_decision()
    )
    assert isinstance(decision, PolicyDecision)
