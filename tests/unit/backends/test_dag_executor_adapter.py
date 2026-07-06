# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from cxrp.contracts.runtime_binding import RuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.backends.dag_executor.adapter import DAGExecutorBackendAdapter
from operations_center.config.settings import DAGExecutorSettings
from operations_center.contracts.execution import ExecutionRequest


def _request(*, model: str, config_ref: str | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="run-1",
        proposal_id="proposal-1",
        decision_id="decision-1",
        goal_text="Implement the task",
        repo_key="OperationsCenter",
        clone_url="git@github.com:Velascat/OperationsCenter.git",
        base_branch="main",
        task_branch="task/test",
        workspace_path=Path("/tmp/oc-dag-executor"),
        runtime_binding=RuntimeBinding(
            kind=RuntimeKind.CLI_SUBSCRIPTION,
            selection_mode=SelectionMode.POLICY_SELECTED,
            model=model,
            provider="anthropic",
            config_ref=config_ref,
        ),
    )


def _usage_store(*, remaining: int, max_per_hour: int = 10, max_per_day: int = 50):
    settings = SimpleNamespace(
        max_exec_per_hour=max_per_hour,
        max_exec_per_day=max_per_day,
    )
    return SimpleNamespace(
        settings=settings,
        remaining_exec_capacity=lambda *, now: remaining,
        worker_backend_cooldown_until=lambda worker_backend, *, now: None,
    )


def test_adapter_applies_tier_defaults_to_fallback_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, **kwargs) -> None:
            captured["runner_kwargs"] = kwargs

        def run_graph(self, spec):
            node = spec.nodes[0]
            captured["node_model"] = node.model
            captured["node_effort"] = node.effort
            captured["backend_models"] = dict(node.backend_models)
            captured["backend_efforts"] = dict(node.backend_efforts)
            return {"status": "succeeded"}

    class FakeNodeType:
        AGENT = "agent"

    class FakeNodeSpec:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeGraphSpec:
        def __init__(self, workflow_id, goal_text, nodes) -> None:
            self.workflow_id = workflow_id
            self.goal_text = goal_text
            self.nodes = nodes

    fake_executor = SimpleNamespace(DAGExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(
        GraphSpec=FakeGraphSpec, NodeSpec=FakeNodeSpec, NodeType=FakeNodeType
    )
    fake_loader = SimpleNamespace(load_graph_file=lambda path, goal_text="": None)
    monkeypatch.setitem(sys.modules, "dag_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "dag_executor.models", fake_models)
    monkeypatch.setitem(sys.modules, "dag_executor.loader", fake_loader)

    adapter = DAGExecutorBackendAdapter(
        DAGExecutorSettings(worker_backend="codex_cli"),
        usage_store=_usage_store(remaining=10),
    )

    result = adapter.execute(_request(model="gpt-5.4-mini", config_ref="team_executor:budget"))

    assert result.success is True
    assert captured["node_model"] == "claude-haiku-4-5-20251001"
    assert captured["node_effort"] == "low"
    assert captured["backend_models"] == {"codex_cli": "gpt-5.4-mini"}
    assert captured["backend_efforts"] == {"codex_cli": "low"}


def test_adapter_downgrades_tier_under_budget_pressure(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, **kwargs) -> None:
            pass

        def run_graph(self, spec):
            node = spec.nodes[0]
            captured["node_model"] = node.model
            return {"status": "succeeded"}

    class FakeNodeType:
        AGENT = "agent"

    class FakeNodeSpec:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeGraphSpec:
        def __init__(self, workflow_id, goal_text, nodes) -> None:
            self.workflow_id = workflow_id
            self.goal_text = goal_text
            self.nodes = nodes

    fake_executor = SimpleNamespace(DAGExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(
        GraphSpec=FakeGraphSpec, NodeSpec=FakeNodeSpec, NodeType=FakeNodeType
    )
    fake_loader = SimpleNamespace(load_graph_file=lambda path, goal_text="": None)
    monkeypatch.setitem(sys.modules, "dag_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "dag_executor.models", fake_models)
    monkeypatch.setitem(sys.modules, "dag_executor.loader", fake_loader)

    adapter = DAGExecutorBackendAdapter(
        DAGExecutorSettings(
            tier_name="standard",
            worker_backend="claude_code",
            dynamic_tier_selection=True,
            budget_pressure_threshold=0.75,
        ),
        usage_store=_usage_store(remaining=2),
    )

    adapter.execute(_request(model="opus", config_ref="team_executor:premium"))

    assert captured["node_model"] == "claude-sonnet-5"


def test_adapter_falls_back_to_codex_when_claude_backend_is_cooling_down(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, **kwargs) -> None:
            captured["worker_backend"] = kwargs["worker_backend"]

        def run_graph(self, spec):
            return {"status": "succeeded"}

    class FakeNodeType:
        AGENT = "agent"

    class FakeNodeSpec:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeGraphSpec:
        def __init__(self, workflow_id, goal_text, nodes) -> None:
            self.workflow_id = workflow_id
            self.goal_text = goal_text
            self.nodes = nodes

    fake_executor = SimpleNamespace(DAGExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(
        GraphSpec=FakeGraphSpec, NodeSpec=FakeNodeSpec, NodeType=FakeNodeType
    )
    fake_loader = SimpleNamespace(load_graph_file=lambda path, goal_text="": None)
    monkeypatch.setitem(sys.modules, "dag_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "dag_executor.models", fake_models)
    monkeypatch.setitem(sys.modules, "dag_executor.loader", fake_loader)

    def _cooldown(worker_backend: str, *, now):
        if worker_backend == "claude_code":
            return now + timedelta(hours=1)
        return None

    usage_store = _usage_store(remaining=10)
    usage_store.worker_backend_cooldown_until = _cooldown

    adapter = DAGExecutorBackendAdapter(
        DAGExecutorSettings(worker_backend="claude_code"),
        usage_store=usage_store,
    )

    result = adapter.execute(_request(model="sonnet"))

    assert result.success is True
    assert captured["worker_backend"] == "codex_cli"


def test_adapter_execute_and_capture_reports_fallback_usage(monkeypatch) -> None:
    class FakeRunner:
        def __init__(self, **kwargs) -> None:
            self.worker_backend = kwargs["worker_backend"]

        def run_graph(self, spec):
            return {"status": "succeeded"}

    class FakeNodeType:
        AGENT = "agent"

    class FakeNodeSpec:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeGraphSpec:
        def __init__(self, workflow_id, goal_text, nodes) -> None:
            self.workflow_id = workflow_id
            self.goal_text = goal_text
            self.nodes = nodes

    fake_executor = SimpleNamespace(DAGExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(
        GraphSpec=FakeGraphSpec, NodeSpec=FakeNodeSpec, NodeType=FakeNodeType
    )
    fake_loader = SimpleNamespace(load_graph_file=lambda path, goal_text="": None)
    monkeypatch.setitem(sys.modules, "dag_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "dag_executor.models", fake_models)
    monkeypatch.setitem(sys.modules, "dag_executor.loader", fake_loader)

    def _cooldown(worker_backend: str, *, now):
        if worker_backend == "claude_code":
            return now + timedelta(hours=1)
        return None

    usage_store = _usage_store(remaining=10)
    usage_store.worker_backend_cooldown_until = _cooldown

    adapter = DAGExecutorBackendAdapter(
        DAGExecutorSettings(worker_backend="claude_code"),
        usage_store=usage_store,
    )

    result, capture = adapter.execute_and_capture(_request(model="sonnet"))

    assert result.success is True
    assert capture.observed_runtime["selected_worker_backend"] == "codex_cli"
    assert capture.observed_runtime["fallback_used"] is False
