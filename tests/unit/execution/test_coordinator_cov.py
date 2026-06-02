# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path

from operations_center.config.settings import (
    BackendCapSettings,
    ResourceGateSettings,
)
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import (
    ExecutionResult,
    RuntimeBindingSummary,
)
from operations_center.contracts.routing import LaneDecision
from operations_center.execution import coordinator as coord_mod
from operations_center.execution.coordinator import (
    ExecutionCoordinator,
    _adapter_crash_result,
    _attach_lifecycle_outcome,
    _backend_capped_result,
    _evaluate_runtime_drift,
    _policy_blocked_result,
    _policy_engine_crash_result,
    _recovery_engine_crash_result,
    _resource_gate_blocked_result,
    _runtime_metadata_from_capture,
    _workspace_prep_failed_result,
)
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.execution.models import BudgetDecision
from operations_center.execution.recovery_loop import (
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
)
from operations_center.execution.recovery_loop.models import (
    ExecutionFailureKind,
    RecoveryOutcome,
)
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus


# ---------------------------------------------------------------------------
# Shared test doubles / fixtures
# ---------------------------------------------------------------------------


class _AllowPolicy:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, *_a, **_k) -> PolicyDecision:
        self.calls += 1
        return PolicyDecision(status=PolicyStatus.ALLOW)


class _RecordingAdapter:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0
        self.last_request = None

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        return self.result


class _CrashAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise RuntimeError("adapter exploded")


class _Registry:
    def __init__(self, adapter) -> None:
        self._a = adapter

    def for_backend(self, _backend):
        return self._a


def _bundle() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix lint failures",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
        )
    )
    decision = LaneDecision(
        proposal_id=proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
    )
    return ProposalDecisionBundle(proposal=proposal, decision=decision)


def _runtime(**kw) -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/lint-fix",
        **kw,
    )


def _success(bundle, run_id="run-1") -> ExecutionResult:
    return ExecutionResult(
        run_id=run_id,
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def _backend_failure(bundle, reason: str) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=reason,
    )


def _real_request(bundle) -> object:
    """Build a real ExecutionRequest for use as a recovery next_request."""
    from operations_center.execution.handoff import ExecutionRequestBuilder

    return ExecutionRequestBuilder().build(bundle, _runtime())


class _FakeRequest:
    """Minimal stand-in for ExecutionRequest used by module-level helpers."""

    def __init__(self) -> None:
        self.run_id = "r-1"
        self.proposal_id = "p-1"
        self.decision_id = "d-1"
        self.repo_key = "svc"
        self.runtime_binding = None
        self.lifecycle = None


# ---------------------------------------------------------------------------
# Module-level result builders
# ---------------------------------------------------------------------------


def test_workspace_prep_failed_result() -> None:
    res = _workspace_prep_failed_result(_FakeRequest(), RuntimeError("clone failed"))
    assert res.success is False
    assert res.status == ExecutionStatus.FAILED
    assert res.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "clone failed" in res.failure_reason


def test_adapter_crash_result() -> None:
    res = _adapter_crash_result(_FakeRequest(), ValueError("kaboom"))
    assert res.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "kaboom" in res.failure_reason


def test_recovery_engine_crash_result() -> None:
    res = _recovery_engine_crash_result(_FakeRequest(), RuntimeError("re boom"))
    assert res.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "re boom" in res.failure_reason


def test_policy_engine_crash_result() -> None:
    res = _policy_engine_crash_result(_FakeRequest(), RuntimeError("pe boom"))
    assert res.failure_category == FailureReasonCategory.POLICY_BLOCKED
    assert "pe boom" in res.failure_reason


def test_policy_blocked_result_block_vs_review() -> None:
    blocked = _policy_blocked_result(
        _FakeRequest(), PolicyDecision(status=PolicyStatus.BLOCK, notes="nope")
    )
    assert "execution blocked by policy" in blocked.failure_reason
    review = _policy_blocked_result(
        _FakeRequest(), PolicyDecision(status=PolicyStatus.REQUIRE_REVIEW, notes="check")
    )
    assert "requires review" in review.failure_reason
    assert review.failure_category == FailureReasonCategory.POLICY_BLOCKED


def test_resource_gate_blocked_result_with_and_without_detail() -> None:
    full = _resource_gate_blocked_result(
        _FakeRequest(),
        BudgetDecision(
            allowed=False,
            reason="global_concurrency_exceeded",
            window="now",
            current=3,
            limit=2,
        ),
    )
    assert full.status == ExecutionStatus.SKIPPED
    assert full.failure_category == FailureReasonCategory.BUDGET_EXHAUSTED
    assert "global_concurrency_exceeded" in full.failure_reason
    assert "window=now" in full.failure_reason
    assert "current=3 limit=2" in full.failure_reason

    minimal = _resource_gate_blocked_result(
        _FakeRequest(),
        BudgetDecision(allowed=False, reason="global_memory_insufficient"),
    )
    assert "window=" not in minimal.failure_reason
    assert "current=" not in minimal.failure_reason


def test_backend_capped_result_with_and_without_detail() -> None:
    full = _backend_capped_result(
        _FakeRequest(),
        BudgetDecision(
            allowed=False,
            reason="backend_budget_exceeded",
            window="day",
            current=5,
            limit=5,
        ),
        "direct_local",
    )
    assert "direct_local" in full.failure_reason
    assert "window=day" in full.failure_reason
    assert "current=5 limit=5" in full.failure_reason

    minimal = _backend_capped_result(
        _FakeRequest(),
        BudgetDecision(allowed=False, reason="x"),
        "direct_local",
    )
    assert "window=" not in minimal.failure_reason


# ---------------------------------------------------------------------------
# _runtime_metadata_from_capture
# ---------------------------------------------------------------------------


def test_runtime_metadata_from_capture_none() -> None:
    assert _runtime_metadata_from_capture(None) == {}


def test_runtime_metadata_from_capture_full() -> None:
    cap = type(
        "Cap",
        (),
        {
            "duration_ms": 99,
            "observed_runtime": {"model": "x"},
            "used_capabilities": ["b", "a"],
        },
    )()
    meta = _runtime_metadata_from_capture(cap)
    assert meta["duration_ms"] == 99
    assert meta["observed_runtime"] == {"model": "x"}
    assert meta["used_capabilities"] == ["a", "b"]


def test_runtime_metadata_from_capture_bad_duration_swallowed() -> None:
    cap = type("Cap", (), {"duration_ms": "not-a-number"})()
    meta = _runtime_metadata_from_capture(cap)
    assert "duration_ms" not in meta


def test_runtime_metadata_from_capture_ignores_wrong_types() -> None:
    cap = type(
        "Cap",
        (),
        {"observed_runtime": "notdict", "used_capabilities": "notseq"},
    )()
    meta = _runtime_metadata_from_capture(cap)
    assert "observed_runtime" not in meta
    assert "used_capabilities" not in meta


# ---------------------------------------------------------------------------
# _evaluate_runtime_drift
# ---------------------------------------------------------------------------


def test_evaluate_runtime_drift_no_binding_returns_none() -> None:
    req = _FakeRequest()
    assert _evaluate_runtime_drift(backend_id="b", request=req, runtime_metadata={}) is None


def test_evaluate_runtime_drift_no_drift_returns_none() -> None:
    req = _FakeRequest()
    req.runtime_binding = RuntimeBindingSummary(
        kind="hosted_api", selection_mode="policy_selected", model="m1", provider="p1"
    )
    out = _evaluate_runtime_drift(
        backend_id="direct_local",
        request=req,
        runtime_metadata={"observed_runtime": {"model": "m1", "provider": "p1"}},
    )
    assert out is None


def test_evaluate_runtime_drift_detected_returns_dict() -> None:
    req = _FakeRequest()
    req.runtime_binding = RuntimeBindingSummary(
        kind="hosted_api", selection_mode="policy_selected", model="m1", provider="p1"
    )
    out = _evaluate_runtime_drift(
        backend_id="direct_local",
        request=req,
        runtime_metadata={"observed_runtime": {"model": "DIFFERENT", "provider": "p1"}},
    )
    assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# _attach_lifecycle_outcome
# ---------------------------------------------------------------------------


def _lifecycle_metadata():
    from operations_center.lifecycle.models import LifecycleMetadata

    return LifecycleMetadata()


def test_attach_lifecycle_outcome_none_metadata_returns_unchanged() -> None:
    bundle = _bundle()
    result = _success(bundle)
    req = _FakeRequest()
    req.lifecycle = None
    out = _attach_lifecycle_outcome(request=req, result=result, repo_graph_context=None)
    assert out is result


def test_attach_lifecycle_outcome_runs_and_sets_outcome() -> None:
    bundle = _bundle()
    result = _success(bundle)
    req = _FakeRequest()
    req.lifecycle = _lifecycle_metadata()
    out = _attach_lifecycle_outcome(request=req, result=result, repo_graph_context=None)
    assert out.lifecycle_outcome is not None


def test_attach_lifecycle_outcome_with_resolving_repo_graph() -> None:
    bundle = _bundle()
    result = _success(bundle)
    req = _FakeRequest()
    req.lifecycle = _lifecycle_metadata()

    class _Node:
        canonical_name = "svc/module"

    class _Graph:
        def resolve(self, _key):
            return _Node()

    out = _attach_lifecycle_outcome(request=req, result=result, repo_graph_context=_Graph())
    assert out.lifecycle_outcome is not None


def test_attach_lifecycle_outcome_runner_raises_returns_original(monkeypatch) -> None:
    bundle = _bundle()
    result = _success(bundle)
    req = _FakeRequest()
    req.lifecycle = _lifecycle_metadata()

    import operations_center.lifecycle as lc

    class _BoomRunner:
        def __init__(self, *_a, **_k) -> None:
            pass

        def run(self, **_k):
            raise RuntimeError("lifecycle boom")

    monkeypatch.setattr(lc, "LifecycleRunner", _BoomRunner)
    out = _attach_lifecycle_outcome(request=req, result=result, repo_graph_context=None)
    assert out is result


# ---------------------------------------------------------------------------
# Coordinator.execute — resource gate
# ---------------------------------------------------------------------------


class _FakeUsageStore:
    """In-memory usage store recording event kinds, with tunable decisions."""

    def __init__(
        self,
        *,
        global_conc=None,
        global_rate=None,
        global_mem=None,
        backend_rate=None,
        backend_conc=None,
        backend_mem=None,
    ) -> None:
        self.events: list[str] = []
        self.quota_events = 0
        self.outcomes: list[bool] = []
        self._global_conc = global_conc
        self._global_rate = global_rate
        self._global_mem = global_mem
        self._backend_rate = backend_rate
        self._backend_conc = backend_conc
        self._backend_mem = backend_mem

    def record_execution_started(self, **_k):
        self.events.append("started")

    def record_execution_finished(self, **_k):
        self.events.append("finished")

    def record_execution(self, **_k):
        self.events.append("execution")

    def record_quota_event(self, **_k):
        self.quota_events += 1

    def record_execution_outcome(self, *, succeeded, **_k):
        self.outcomes.append(succeeded)

    def global_concurrency_decision(self, **_k):
        return self._global_conc

    def global_rate_decision(self, **_k):
        return self._global_rate

    def global_memory_decision(self, **_k):
        return self._global_mem

    def budget_decision_for_backend(self, *_a, **_k):
        return self._backend_rate

    def concurrency_decision_for_backend(self, *_a, **_k):
        return self._backend_conc

    def memory_decision_for_backend(self, *_a, **_k):
        return self._backend_mem


_ALLOW = BudgetDecision(allowed=True)


def test_resource_gate_concurrency_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(
        global_conc=BudgetDecision(allowed=False, reason="global_concurrency_exceeded")
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        resource_gate=ResourceGateSettings(max_concurrent=1),
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert adapter.calls == 0
    assert out.result.failure_category == FailureReasonCategory.BUDGET_EXHAUSTED
    assert "global_concurrency_exceeded" in out.result.failure_reason


def test_resource_gate_rate_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(
        global_conc=_ALLOW,
        global_rate=BudgetDecision(allowed=False, reason="global_rate_exceeded"),
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        resource_gate=ResourceGateSettings(max_concurrent=1, max_per_hour=2),
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert "global_rate_exceeded" in out.result.failure_reason


def test_resource_gate_memory_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(
        global_conc=_ALLOW,
        global_rate=_ALLOW,
        global_mem=BudgetDecision(allowed=False, reason="global_memory_insufficient"),
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        resource_gate=ResourceGateSettings(
            max_concurrent=1, max_per_hour=2, min_available_memory_mb=4096
        ),
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert "global_memory_insufficient" in out.result.failure_reason


def test_resource_gate_all_pass_dispatches() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(global_conc=_ALLOW, global_rate=_ALLOW, global_mem=_ALLOW)
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        resource_gate=ResourceGateSettings(
            max_concurrent=2, max_per_hour=4, min_available_memory_mb=1
        ),
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True
    assert adapter.calls == 1
    assert "started" in store.events and "finished" in store.events
    assert store.outcomes == [True]


def test_resource_gate_none_when_no_gate_settings() -> None:
    # usage_store present but resource_gate None -> _evaluate_resource_gate None
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
    )
    assert coord._evaluate_resource_gate() is None
    out = coord.execute(bundle, _runtime())
    assert out.executed is True


# ---------------------------------------------------------------------------
# Coordinator.execute — backend caps via fake store
# ---------------------------------------------------------------------------


def test_backend_cap_rate_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(
        backend_rate=BudgetDecision(allowed=False, reason="backend_budget_exceeded")
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        backend_caps={"direct_local": BackendCapSettings(max_per_day=1)},
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert "backend_budget_exceeded" in out.result.failure_reason


def test_backend_cap_concurrency_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(
        backend_rate=_ALLOW,
        backend_conc=BudgetDecision(allowed=False, reason="backend_concurrency_exceeded"),
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        backend_caps={"direct_local": BackendCapSettings(max_per_day=10, max_concurrent=1)},
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert "backend_concurrency_exceeded" in out.result.failure_reason


def test_backend_cap_memory_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore(
        backend_rate=_ALLOW,
        backend_conc=_ALLOW,
        backend_mem=BudgetDecision(allowed=False, reason="backend_memory_insufficient"),
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        backend_caps={
            "direct_local": BackendCapSettings(
                max_per_day=10, max_concurrent=4, min_available_memory_mb=4096
            )
        },
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert "backend_memory_insufficient" in out.result.failure_reason


def test_backend_cap_no_entry_passes() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    store = _FakeUsageStore()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
        backend_caps={"some_other_backend": BackendCapSettings(max_per_day=1)},
    )
    assert coord._evaluate_backend_caps("direct_local") is None
    out = coord.execute(bundle, _runtime())
    assert out.executed is True


def test_evaluate_backend_caps_no_usage_store() -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
    )
    assert coord._evaluate_backend_caps("direct_local") is None


# ---------------------------------------------------------------------------
# Capacity exhaustion -> quota event vs normal outcome
# ---------------------------------------------------------------------------


def test_capacity_exhaustion_records_quota_event() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(
        _backend_failure(bundle, "Backend error: you've hit your limit for today")
    )
    store = _FakeUsageStore()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True
    assert store.quota_events == 1
    assert store.outcomes == []


def test_non_capacity_failure_records_outcome() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "syntax error in patch"))
    store = _FakeUsageStore()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        usage_store=store,
    )
    coord.execute(bundle, _runtime())
    assert store.quota_events == 0
    assert store.outcomes == [False]


# ---------------------------------------------------------------------------
# Workspace prepare / finalize
# ---------------------------------------------------------------------------


class _Workspace:
    def __init__(self, *, prep_raises=False, finalize_raises=False) -> None:
        self.prep_raises = prep_raises
        self.finalize_raises = finalize_raises
        self.prepared = False
        self.finalized = False

    def prepare(self, request):
        self.prepared = True
        if self.prep_raises:
            raise RuntimeError("clone failed")

    def finalize(self, request, result):
        self.finalized = True
        if self.finalize_raises:
            raise RuntimeError("push failed")
        return result.model_copy(update={"failure_reason": "finalized"})


def test_workspace_prep_failure_short_circuits() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    ws = _Workspace(prep_raises=True)
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        workspace_manager=ws,
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is False
    assert adapter.calls == 0
    assert "Workspace preparation failed" in out.result.failure_reason


def test_workspace_finalize_runs_on_success() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    ws = _Workspace()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        workspace_manager=ws,
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True
    assert ws.prepared and ws.finalized
    assert out.result.failure_reason == "finalized"


def test_workspace_finalize_failure_is_non_fatal() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    ws = _Workspace(finalize_raises=True)
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        workspace_manager=ws,
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True
    assert out.result.success is True


def test_workspace_finalize_skipped_on_failure_result() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    ws = _Workspace()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        workspace_manager=ws,
    )
    coord.execute(bundle, _runtime())
    assert ws.prepared is True
    assert ws.finalized is False


# ---------------------------------------------------------------------------
# Runtime binding policy
# ---------------------------------------------------------------------------


class _Binding:
    def __init__(self) -> None:
        self.kind = type("K", (), {"value": "hosted_api"})()
        self.selection_mode = type("M", (), {"value": "policy_selected"})()
        self.model = "m1"
        self.provider = "p1"
        self.endpoint = None
        self.config_ref = None


class _BindingPolicy:
    def __init__(self, binding) -> None:
        self._b = binding

    def select(self, proposal, decision):
        return self._b


class _RaisingBindingPolicy:
    def select(self, proposal, decision):
        raise RuntimeError("select boom")


def test_runtime_binding_policy_none_passthrough() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
    )
    rt = _runtime()
    assert coord._apply_runtime_binding_policy(bundle, rt) is rt


def test_runtime_binding_policy_respects_existing_binding() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        runtime_binding_policy=_BindingPolicy(_Binding()),
    )
    rt = _runtime(
        runtime_binding=RuntimeBindingSummary(
            kind="cli_subscription",
            selection_mode="explicit_request",
            provider="anthropic",
            model="caller",
        )
    )
    assert coord._apply_runtime_binding_policy(bundle, rt) is rt


def test_runtime_binding_policy_select_raises_falls_back() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        runtime_binding_policy=_RaisingBindingPolicy(),
    )
    rt = _runtime()
    assert coord._apply_runtime_binding_policy(bundle, rt) is rt


def test_runtime_binding_policy_returns_none_passthrough() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        runtime_binding_policy=_BindingPolicy(None),
    )
    rt = _runtime()
    assert coord._apply_runtime_binding_policy(bundle, rt) is rt


def test_runtime_binding_policy_binds_and_dispatches() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        runtime_binding_policy=_BindingPolicy(_Binding()),
    )
    new_rt = coord._apply_runtime_binding_policy(bundle, _runtime())
    assert new_rt.runtime_binding is not None
    assert new_rt.runtime_binding.model == "m1"
    out = coord.execute(bundle, _runtime())
    assert out.executed is True


# ---------------------------------------------------------------------------
# Run memory indexing
# ---------------------------------------------------------------------------


def test_record_run_memory_none_dir_noop(monkeypatch) -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
    )

    import operations_center.run_memory as rm

    calls: list[object] = []

    def _track(*_a, **_k):
        calls.append(object())

    monkeypatch.setattr(rm, "record_execution_result", _track)
    # No run_memory_index_dir configured -> recorder must not be invoked.
    out = coord._record_run_memory(request=_FakeRequest(), result=_success(bundle), bundle=bundle)
    assert out is None
    assert calls == []


def test_record_run_memory_calls_recorder(monkeypatch, tmp_path: Path) -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    calls = {}

    import operations_center.run_memory as rm

    def _fake_record(result, index_dir, *, repo_id, tags):
        calls["repo_id"] = repo_id
        calls["tags"] = tags

    monkeypatch.setattr(rm, "record_execution_result", _fake_record)
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        run_memory_index_dir=tmp_path / "idx",
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True
    assert calls["repo_id"] == "svc"
    assert calls["tags"][0] == "lint_fix"


def test_record_run_memory_swallows_errors(monkeypatch, tmp_path: Path) -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))

    import operations_center.run_memory as rm

    def _boom(*_a, **_k):
        raise RuntimeError("index boom")

    monkeypatch.setattr(rm, "record_execution_result", _boom)
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        run_memory_index_dir=tmp_path / "idx",
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True  # error swallowed


# ---------------------------------------------------------------------------
# Contract impact hook
# ---------------------------------------------------------------------------


def test_log_contract_impact_no_graph_returns_empty() -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
    )
    assert coord._log_contract_impact(_FakeRequest()) == {}


def test_log_contract_impact_non_repograph_returns_empty() -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
        repo_graph=object(),  # not a RepoGraph
    )
    assert coord._log_contract_impact(_FakeRequest()) == {}


def test_log_contract_impact_with_impact(monkeypatch) -> None:
    bundle = _bundle()
    from platform_manifest import RepoGraph

    graph = RepoGraph()

    class _Node:
        def __init__(self, name, repo="r"):
            self.canonical_name = name
            self.repo_id = repo

    class _Summary:
        def __init__(self) -> None:
            self.target = _Node("svc/api")
            self.affected = [_Node("svc/a"), _Node("svc/b")]
            self.public_affected = [_Node("svc/a")]
            self.private_affected = [_Node("svc/b")]

        def has_impact(self):
            return True

    import operations_center.impact_analysis as ia

    monkeypatch.setattr(ia, "compute_contract_impact", lambda g, key: _Summary())
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
        repo_graph=graph,
    )
    meta = coord._log_contract_impact(_FakeRequest())
    assert meta["contract_impact"]["affected_count"] == 2
    assert meta["contract_impact"]["public_affected"] == ["svc/a"]


def test_log_contract_impact_no_impact_returns_empty(monkeypatch) -> None:
    bundle = _bundle()
    from platform_manifest import RepoGraph

    class _Summary:
        def has_impact(self):
            return False

    import operations_center.impact_analysis as ia

    monkeypatch.setattr(ia, "compute_contract_impact", lambda g, key: _Summary())
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
        repo_graph=RepoGraph(),
    )
    assert coord._log_contract_impact(_FakeRequest()) == {}


def test_log_contract_impact_compute_raises_returns_empty(monkeypatch) -> None:
    bundle = _bundle()
    from platform_manifest import RepoGraph

    import operations_center.impact_analysis as ia

    def _boom(*_a, **_k):
        raise RuntimeError("impact boom")

    monkeypatch.setattr(ia, "compute_contract_impact", _boom)
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
        repo_graph=RepoGraph(),
    )
    assert coord._log_contract_impact(_FakeRequest()) == {}


# ---------------------------------------------------------------------------
# Recovery loop branches via stub engine
# ---------------------------------------------------------------------------


def _action(decision, attempt=1, delay=None):
    return RecoveryAction(
        attempt=attempt,
        failure_kind=ExecutionFailureKind.UNKNOWN,
        decision=decision,
        reason="r",
        delay_seconds=delay,
    )


class _StubEngine:
    """Yields a queue of RecoveryOutcomes; raises if exhausted."""

    def __init__(self, outcomes) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def evaluate(self, result, ctx):
        self.calls += 1
        return self._outcomes.pop(0)


class _RaisingEngine:
    def evaluate(self, result, ctx):
        raise RuntimeError("engine boom")


def test_recovery_accept_first_attempt() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success(bundle))
    engine = _StubEngine(
        [RecoveryOutcome(decision=RecoveryDecision.ACCEPT, action=_action(RecoveryDecision.ACCEPT))]
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        recovery_engine=engine,
    )
    out = coord.execute(bundle, _runtime())
    assert out.executed is True
    assert adapter.calls == 1
    # recovery action attached
    assert out.result.recovery is not None


def test_recovery_reject_no_next_request_stops() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    engine = _StubEngine(
        [
            RecoveryOutcome(
                decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                action=_action(RecoveryDecision.REJECT_UNRECOVERABLE),
                next_request=None,
            )
        ]
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        recovery_engine=engine,
    )
    out = coord.execute(bundle, _runtime())
    assert adapter.calls == 1
    assert out.result.success is False


def test_recovery_engine_raises_synthesizes_crash_result() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        recovery_engine=_RaisingEngine(),
    )
    out = coord.execute(bundle, _runtime())
    assert "RecoveryEngine raised" in out.result.failure_reason


def test_recovery_retry_same_request_then_accept() -> None:
    bundle = _bundle()
    # Adapter returns a failure both times; engine retries once, then accepts.
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    req_sentinel = object()
    engine = _StubEngine(
        [
            RecoveryOutcome(
                decision=RecoveryDecision.RETRY_SAME_REQUEST,
                action=_action(RecoveryDecision.RETRY_SAME_REQUEST, delay=None),
                next_request=req_sentinel,  # different object -> request_changed True
                requires_policy_revalidation=False,
            ),
            RecoveryOutcome(
                decision=RecoveryDecision.ACCEPT,
                action=_action(RecoveryDecision.ACCEPT, attempt=2),
            ),
        ]
    )
    # request_changed True triggers policy re-eval; keep policy allowing.
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        recovery_engine=engine,
        recovery_policy=RecoveryPolicy(max_attempts=3),
    )
    coord.execute(bundle, _runtime())
    assert adapter.calls == 2
    assert engine.calls == 2


def test_recovery_retry_with_delay_records_actual(monkeypatch) -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    # Avoid real sleeping: patch bounded_sleep in coordinator module.
    monkeypatch.setattr(coord_mod, "bounded_sleep", lambda d, m: 0.0)
    engine = _StubEngine(
        [
            RecoveryOutcome(
                decision=RecoveryDecision.RETRY_SAME_REQUEST,
                action=_action(RecoveryDecision.RETRY_SAME_REQUEST, delay=5.0),
                next_request=object(),
                delay_seconds=5.0,
            ),
            RecoveryOutcome(
                decision=RecoveryDecision.ACCEPT,
                action=_action(RecoveryDecision.ACCEPT, attempt=2),
            ),
        ]
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
        recovery_engine=engine,
        recovery_policy=RecoveryPolicy(max_attempts=2),
    )
    out = coord.execute(bundle, _runtime())
    assert adapter.calls == 2
    assert out.result.recovery is not None


def test_recovery_retry_policy_revalidation_blocks() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    engine = _StubEngine(
        [
            RecoveryOutcome(
                decision=RecoveryDecision.RETRY_MODIFIED_REQUEST,
                action=_action(RecoveryDecision.RETRY_MODIFIED_REQUEST),
                next_request=_real_request(bundle),
                requires_policy_revalidation=True,
            )
        ]
    )
    # Policy allows first eval (initial), then BLOCK on revalidation.
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_TwoPhasePolicy(),
        recovery_engine=engine,
        recovery_policy=RecoveryPolicy(max_attempts=2),
    )
    out = coord.execute(bundle, _runtime())
    assert out.result.failure_category == FailureReasonCategory.POLICY_BLOCKED


class _TwoPhasePolicy:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, *_a, **_k):
        self.calls += 1
        if self.calls <= 1:
            return PolicyDecision(status=PolicyStatus.ALLOW)
        return PolicyDecision(status=PolicyStatus.BLOCK, notes="blocked on retry")


def test_recovery_retry_policy_engine_raises_during_revalidation() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_backend_failure(bundle, "boom"))
    engine = _StubEngine(
        [
            RecoveryOutcome(
                decision=RecoveryDecision.RETRY_MODIFIED_REQUEST,
                action=_action(RecoveryDecision.RETRY_MODIFIED_REQUEST),
                next_request=_real_request(bundle),
                requires_policy_revalidation=True,
            )
        ]
    )
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_RaisingPolicyTwoPhase(),
        recovery_engine=engine,
        recovery_policy=RecoveryPolicy(max_attempts=2),
    )
    out = coord.execute(bundle, _runtime())
    assert "PolicyEngine.evaluate raised mid-loop" in out.result.failure_reason


class _RaisingPolicyTwoPhase:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, *_a, **_k):
        self.calls += 1
        # first call (pre-dispatch) allows; revalidation raises
        if self.calls <= 1:
            return PolicyDecision(status=PolicyStatus.ALLOW)
        raise RuntimeError("revalidation boom")


def test_adapter_crash_through_recovery_loop_real_engine() -> None:
    # Uses the default real engine; crashing adapter yields a crash result.
    bundle = _bundle()
    adapter = _CrashAdapter()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_AllowPolicy(),
    )
    out = coord.execute(bundle, _runtime())
    assert out.result.success is False
    assert "Adapter raised unexpected exception" in out.result.failure_reason


# ---------------------------------------------------------------------------
# _build_detail_refs error path
# ---------------------------------------------------------------------------


def test_build_detail_refs_none_capture() -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
    )
    assert coord._build_detail_refs(object(), _FakeRequest(), None) == []


def test_build_detail_refs_builder_raises_returns_empty() -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
    )

    class _Builder:
        def build_backend_detail_refs(self, request, capture):
            raise RuntimeError("ref boom")

    assert coord._build_detail_refs(_Builder(), _FakeRequest(), {"x": 1}) == []


def test_build_detail_refs_non_builder_adapter_returns_empty() -> None:
    bundle = _bundle()
    coord = ExecutionCoordinator(
        adapter_registry=_Registry(_RecordingAdapter(_success(bundle))),
        policy_engine=_AllowPolicy(),
    )
    assert coord._build_detail_refs(object(), _FakeRequest(), {"x": 1}) == []
