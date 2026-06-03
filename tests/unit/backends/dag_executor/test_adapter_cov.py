# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

from operations_center.backends.dag_executor import adapter as adapter_mod
from operations_center.backends.dag_executor.adapter import (
    DAGExecutorBackendAdapter,
    _apply_agent_tier_defaults,
    _default_agent_node,
    _dict_to_result,
    _error_result,
    _worker_backend_unavailable_result,
)
from operations_center.config.settings import DAGExecutorSettings
from operations_center.contracts.enums import (
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionRequest


# --------------------------------------------------------------------------
# Helpers (must start with '_' per N2)
# --------------------------------------------------------------------------


def _request(*, workspace: Path) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="run-1",
        proposal_id="proposal-1",
        decision_id="decision-1",
        goal_text="Implement the task",
        repo_key="OperationsCenter",
        clone_url="git@github.com:Velascat/OperationsCenter.git",
        base_branch="main",
        task_branch="task/test",
        workspace_path=workspace,
    )


def _profile() -> dict[str, dict[str, str]]:
    return {
        "claude_code": {"model": "cc-model", "effort": "high"},
        "codex_cli": {"model": "cx-model", "effort": "low"},
    }


class _FakeNodeType:
    """Minimal stand-in for the dag_executor NodeType enum member."""

    AGENT = SimpleNamespace(value="agent")


class _FakeNodeSpec:
    def __init__(
        self,
        *,
        id,
        type,
        model=None,
        effort=None,
        backend_models=None,
        backend_efforts=None,
    ):
        self.id = id
        self.type = type
        self.model = model
        self.effort = effort
        self.backend_models = backend_models if backend_models is not None else {}
        self.backend_efforts = backend_efforts if backend_efforts is not None else {}


class _FakeGraphSpec:
    def __init__(self, *, workflow_id, goal_text, nodes):
        self.workflow_id = workflow_id
        self.goal_text = goal_text
        self.nodes = nodes


def _install_fake_dag_executor(monkeypatch, *, runner_cls, load_graph_file=None):
    """Inject fake dag_executor.* submodules so the lazy import resolves."""
    executor_mod = ModuleType("dag_executor.executor")
    executor_mod.DAGExecutorRunner = runner_cls

    loader_mod = ModuleType("dag_executor.loader")
    loader_mod.load_graph_file = load_graph_file or (
        lambda *a, **k: _FakeGraphSpec(workflow_id="wf", goal_text="g", nodes=[])
    )

    models_mod = ModuleType("dag_executor.models")
    models_mod.GraphSpec = _FakeGraphSpec
    models_mod.NodeSpec = _FakeNodeSpec
    models_mod.NodeType = _FakeNodeType

    pkg = ModuleType("dag_executor")

    monkeypatch.setitem(sys.modules, "dag_executor", pkg)
    monkeypatch.setitem(sys.modules, "dag_executor.executor", executor_mod)
    monkeypatch.setitem(sys.modules, "dag_executor.loader", loader_mod)
    monkeypatch.setitem(sys.modules, "dag_executor.models", models_mod)


def _stub_selectors(monkeypatch, *, execution, runtime=None, invoke_run=False):
    """Patch tiering + worker-backend selector collaborators on the module.

    When ``invoke_run`` is True the round-robin stub calls the supplied
    ``execute_once`` with the preferred backend, exercising ``_run_once`` and
    the underlying runner.
    """
    monkeypatch.setattr(adapter_mod, "select_tier", lambda **kw: "budget")
    monkeypatch.setattr(adapter_mod, "tier_profile", lambda tier: _profile())

    def _round_robin(**kw):
        if invoke_run:
            kw["execute_once"](kw["preferred_backend"])
        return execution

    monkeypatch.setattr(
        adapter_mod,
        "execute_with_worker_backend_round_robin",
        _round_robin,
    )
    monkeypatch.setattr(
        adapter_mod,
        "worker_backend_observed_runtime",
        lambda ex: runtime if runtime is not None else {"obs": True},
    )


def _execution(*, selected_backend, payload, fallback_used=False, reason=None):
    selection = SimpleNamespace(reason=reason)
    return SimpleNamespace(
        selected_backend=selected_backend,
        payload=payload,
        fallback_used=fallback_used,
        selection=selection,
    )


# --------------------------------------------------------------------------
# Pure helper functions
# --------------------------------------------------------------------------


def test_dict_to_result_success():
    req = _request(workspace=Path("/tmp/x"))
    result = _dict_to_result(req, {"status": "succeeded"})
    assert result.success is True
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.failure_category is None
    assert result.failure_reason is None
    assert result.validation.status is ValidationStatus.SKIPPED
    assert result.branch_name == "task/test"
    assert result.branch_pushed is False


def test_dict_to_result_failure_with_error_summary():
    req = _request(workspace=Path("/tmp/x"))
    result = _dict_to_result(req, {"status": "failed", "error_summary": "boom"})
    assert result.success is False
    assert result.status is ExecutionStatus.FAILED
    assert result.failure_category is FailureReasonCategory.BACKEND_ERROR
    assert result.failure_reason == "boom"


def test_dict_to_result_failure_default_reason():
    req = _request(workspace=Path("/tmp/x"))
    result = _dict_to_result(req, {"status": "failed"})
    assert result.failure_reason == "dag_executor run failed"


def test_error_result_fields():
    req = _request(workspace=Path("/tmp/x"))
    result = _error_result(req, "nope")
    assert result.success is False
    assert result.status is ExecutionStatus.FAILED
    assert result.failure_category is FailureReasonCategory.BACKEND_ERROR
    assert result.failure_reason == "nope"
    assert result.run_id == "run-1"
    assert result.proposal_id == "proposal-1"
    assert result.decision_id == "decision-1"


def test_worker_backend_unavailable_result_wraps_reason():
    req = _request(workspace=Path("/tmp/x"))
    result = _worker_backend_unavailable_result(req, "cooling down")
    assert result.failure_reason == ("worker backend round robin blocked dispatch: cooling down")
    assert result.success is False


def test_default_agent_node_uses_profile():
    node = _default_agent_node(_FakeNodeSpec, _FakeNodeType, _profile())
    assert node.id == "main"
    assert node.type is _FakeNodeType.AGENT
    assert node.model == "cc-model"
    assert node.effort == "high"
    assert node.backend_models == {"codex_cli": "cx-model"}
    assert node.backend_efforts == {"codex_cli": "low"}


def test_apply_agent_tier_defaults_fills_missing():
    node = _FakeNodeSpec(id="a", type=_FakeNodeType.AGENT)
    spec = _FakeGraphSpec(workflow_id="w", goal_text="g", nodes=[node])
    _apply_agent_tier_defaults(spec, _profile())
    assert node.model == "cc-model"
    assert node.effort == "high"
    assert node.backend_models["codex_cli"] == "cx-model"
    assert node.backend_efforts["codex_cli"] == "low"


def test_apply_agent_tier_defaults_keeps_existing():
    node = _FakeNodeSpec(
        id="a",
        type=_FakeNodeType.AGENT,
        model="keep-model",
        effort="keep-effort",
        backend_models={"codex_cli": "keep-cx"},
        backend_efforts={"codex_cli": "keep-cxe"},
    )
    spec = _FakeGraphSpec(workflow_id="w", goal_text="g", nodes=[node])
    _apply_agent_tier_defaults(spec, _profile())
    assert node.model == "keep-model"
    assert node.effort == "keep-effort"
    assert node.backend_models["codex_cli"] == "keep-cx"
    assert node.backend_efforts["codex_cli"] == "keep-cxe"


def test_apply_agent_tier_defaults_skips_non_agent():
    node = _FakeNodeSpec(id="t", type=SimpleNamespace(value="tool"))
    spec = _FakeGraphSpec(workflow_id="w", goal_text="g", nodes=[node])
    _apply_agent_tier_defaults(spec, _profile())
    # Untouched: model/effort stay None and codex_cli not injected.
    assert node.model is None
    assert node.effort is None
    assert "codex_cli" not in node.backend_models


def test_apply_agent_tier_defaults_type_via_plain_string():
    # node_type with no .value attribute -> uses the raw value.
    node = _FakeNodeSpec(id="a", type="agent")
    spec = _FakeGraphSpec(workflow_id="w", goal_text="g", nodes=[node])
    _apply_agent_tier_defaults(spec, _profile())
    assert node.model == "cc-model"


# --------------------------------------------------------------------------
# execute_and_capture: import failure path
# --------------------------------------------------------------------------


def test_execute_and_capture_import_error(monkeypatch, tmp_path):
    # Ensure dag_executor is not importable.
    for name in list(sys.modules):
        if name == "dag_executor" or name.startswith("dag_executor."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    real_import = __import__

    def _fake_import(name, *a, **k):
        if name.startswith("dag_executor"):
            raise ImportError("missing")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result, capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert capture is None
    assert result.success is False
    assert "dag_executor not installed" in result.failure_reason


# --------------------------------------------------------------------------
# execute_and_capture: happy paths
# --------------------------------------------------------------------------


def test_execute_and_capture_fallback_single_agent(monkeypatch, tmp_path):
    captured_specs = []

    class _Runner:
        def __init__(self, **kw):
            self.kw = kw

        def run_graph(self, spec):
            captured_specs.append(spec)
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(selected_backend="claude_code", payload={"status": "succeeded"})
    _stub_selectors(monkeypatch, execution=execution, invoke_run=True)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    req = _request(workspace=tmp_path)  # no workflow.yaml present
    result, capture = adapter.execute_and_capture(req)

    assert result.success is True
    assert capture.observed_runtime == {"obs": True}
    # Fallback path built a single-agent GraphSpec from goal_text.
    assert len(captured_specs) == 1
    spec = captured_specs[0]
    assert spec.workflow_id == "run-1"
    assert spec.goal_text == "Implement the task"
    assert spec.nodes[0].id == "main"


def test_execute_and_capture_loads_workflow_file(monkeypatch, tmp_path):
    wf = tmp_path / ".dag_executor" / "workflow.yaml"
    wf.parent.mkdir(parents=True)
    wf.write_text("nodes: []\n", encoding="utf-8")

    loaded = []

    def _load(path, goal_text):
        loaded.append((path, goal_text))
        node = _FakeNodeSpec(id="a", type=_FakeNodeType.AGENT)
        return _FakeGraphSpec(workflow_id="wf", goal_text=goal_text, nodes=[node])

    ran = []

    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            ran.append(spec)
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner, load_graph_file=_load)
    execution = _execution(selected_backend="claude_code", payload={"status": "succeeded"})
    _stub_selectors(monkeypatch, execution=execution, invoke_run=True)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))

    assert result.success is True
    assert loaded == [(str(wf), "Implement the task")]
    # tier defaults were applied to the loaded spec's agent node.
    assert ran[0].nodes[0].model == "cc-model"


def test_execute_passthrough_returns_only_result(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(selected_backend="claude_code", payload={"status": "succeeded"})
    _stub_selectors(monkeypatch, execution=execution)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result = adapter.execute(_request(workspace=tmp_path))
    assert isinstance(result, type(_error_result(_request(workspace=tmp_path), "x")))
    assert result.success is True


# --------------------------------------------------------------------------
# execute_and_capture: runner settings & artifacts_dir wiring
# --------------------------------------------------------------------------


def test_runner_receives_settings(monkeypatch, tmp_path):
    seen = {}

    class _Runner:
        def __init__(self, **kw):
            seen.update(kw)

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(selected_backend="claude_code", payload={"status": "succeeded"})
    _stub_selectors(monkeypatch, execution=execution, invoke_run=True)

    settings = DAGExecutorSettings(
        artifacts_dir="/art", timeout_seconds=120, worker_backend="claude_code"
    )
    adapter = DAGExecutorBackendAdapter(settings)
    adapter.execute_and_capture(_request(workspace=tmp_path))

    assert seen["artifacts_dir"] == "/art"
    assert seen["timeout_seconds"] == 120
    assert seen["working_directory"] == str(tmp_path)
    assert seen["worker_backend"] == "claude_code"


def test_runner_artifacts_and_timeout_falsy_become_none(monkeypatch, tmp_path):
    seen = {}

    class _Runner:
        def __init__(self, **kw):
            seen.update(kw)

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(selected_backend="claude_code", payload={"status": "succeeded"})
    _stub_selectors(monkeypatch, execution=execution, invoke_run=True)

    settings = DAGExecutorSettings(artifacts_dir="", timeout_seconds=0)
    adapter = DAGExecutorBackendAdapter(settings)
    adapter.execute_and_capture(_request(workspace=tmp_path))

    assert seen["artifacts_dir"] is None
    assert seen["timeout_seconds"] is None


# --------------------------------------------------------------------------
# execute_and_capture: error / unavailable / fallback branches
# --------------------------------------------------------------------------


def test_round_robin_raises_returns_error_result(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    monkeypatch.setattr(adapter_mod, "select_tier", lambda **kw: "budget")
    monkeypatch.setattr(adapter_mod, "tier_profile", lambda tier: _profile())

    def _boom(**kw):
        raise RuntimeError("explode")

    monkeypatch.setattr(adapter_mod, "execute_with_worker_backend_round_robin", _boom)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result, capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert capture is None
    assert result.success is False
    assert result.failure_reason == "explode"


def test_no_backend_available_returns_unavailable(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(selected_backend=None, payload=None, reason="all cooling down")
    _stub_selectors(monkeypatch, execution=execution)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result, capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert capture is not None  # capture built before the unavailable check
    assert result.success is False
    assert "worker backend round robin blocked dispatch: all cooling down" == (
        result.failure_reason
    )


def test_no_backend_available_default_reason(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    # reason is None -> falls back to default string.
    execution = _execution(selected_backend=None, payload=None, reason=None)
    _stub_selectors(monkeypatch, execution=execution)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert (
        result.failure_reason
        == "worker backend round robin blocked dispatch: no worker backend available"
    )


def test_payload_none_with_backend_still_unavailable(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    # selected_backend set but payload None -> still unavailable branch.
    execution = _execution(selected_backend="claude_code", payload=None, reason="x")
    _stub_selectors(monkeypatch, execution=execution)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert "worker backend round robin blocked dispatch: x" == result.failure_reason


def test_fallback_used_logs_and_succeeds(monkeypatch, tmp_path, caplog):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(
        selected_backend="codex_cli",
        payload={"status": "succeeded"},
        fallback_used=True,
    )
    _stub_selectors(monkeypatch, execution=execution)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings())
    with caplog.at_level("INFO"):
        result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert result.success is True
    assert any("fallback worker backend" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# execute_and_capture: quota-event recording on limit failures
# --------------------------------------------------------------------------


def test_limit_failure_records_quota_event(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "failed"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    payload = {"status": "failed", "error_summary": "usage LIMIT reached"}
    execution = _execution(selected_backend="claude_code", payload=payload)
    _stub_selectors(monkeypatch, execution=execution)

    usage_store = mock.Mock()
    usage_store.record_quota_event = mock.Mock()
    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings(), usage_store=usage_store)
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))

    assert result.success is False
    usage_store.record_quota_event.assert_called_once()
    kwargs = usage_store.record_quota_event.call_args.kwargs
    assert kwargs["task_id"] == "run-1"
    assert kwargs["role"] == "dag_executor"
    assert kwargs["backend"] == "dag_executor"


def test_non_limit_failure_does_not_record(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "failed"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    payload = {"status": "failed", "error_summary": "syntax error"}
    execution = _execution(selected_backend="claude_code", payload=payload)
    _stub_selectors(monkeypatch, execution=execution)

    usage_store = mock.Mock()
    usage_store.record_quota_event = mock.Mock()
    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings(), usage_store=usage_store)
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))

    assert result.success is False
    usage_store.record_quota_event.assert_not_called()


def test_limit_failure_without_record_method_is_safe(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "failed"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    payload = {"status": "failed", "error_summary": "limit hit"}
    execution = _execution(selected_backend="claude_code", payload=payload)
    _stub_selectors(monkeypatch, execution=execution)

    # usage_store lacking record_quota_event -> hasattr() guard skips recording.
    class _Bare:
        pass

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings(), usage_store=_Bare())
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert result.success is False
    assert "limit" in result.failure_reason.lower()


def test_default_usage_store_constructed_when_none(monkeypatch, tmp_path):
    class _Runner:
        def __init__(self, **kw):
            pass

        def run_graph(self, spec):
            return {"status": "succeeded"}

    _install_fake_dag_executor(monkeypatch, runner_cls=_Runner)
    execution = _execution(selected_backend="claude_code", payload={"status": "succeeded"})
    _stub_selectors(monkeypatch, execution=execution)

    sentinel = object()
    constructed = mock.Mock(return_value=sentinel)
    monkeypatch.setattr(adapter_mod, "UsageStore", constructed)

    adapter = DAGExecutorBackendAdapter(DAGExecutorSettings(), usage_store=None)
    result, _capture = adapter.execute_and_capture(_request(workspace=tmp_path))
    assert result.success is True
    constructed.assert_called_once_with()
