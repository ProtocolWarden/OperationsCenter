# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

from cxrp.contracts.runtime_binding import RuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.backends.critique_executor.adapter import CritiqueExecutorBackendAdapter
from operations_center.config.settings import CritiqueExecutorSettings
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
        workspace_path=Path("/tmp/oc-critique-executor"),
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


def test_adapter_builds_budget_profile_for_codex(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, topology: str, config, worker_backend: str, working_dir: str) -> None:
            captured["topology"] = topology
            captured["config"] = config
            captured["worker_backend"] = worker_backend
            captured["working_dir"] = working_dir

        def run(self, goal_text: str, max_rounds: int | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    class FakeCritiqueTopology:
        def __call__(self, value: str):
            return value

    class FakeCritiqueConfig:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_executor = SimpleNamespace(CritiqueExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(CritiqueConfig=FakeCritiqueConfig, CritiqueTopology=FakeCritiqueTopology())
    monkeypatch.setitem(sys.modules, "critique_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "critique_executor.models", fake_models)

    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="codex_cli", topology="reflexion"),
        usage_store=_usage_store(remaining=10),
    )

    result = adapter.execute(_request(model="gpt-5.4-mini", config_ref="team_executor:budget"))

    assert result.success is True
    config = captured["config"]
    assert config.proposer_model == "claude-haiku-4-5-20251001"
    assert config.critic_model == "claude-haiku-4-5-20251001"
    assert config.proposer_backend_models["codex_cli"] == "gpt-5.4-mini"
    assert config.proposer_backend_efforts["codex_cli"] == "low"
    assert captured["worker_backend"] == "codex_cli"


def test_adapter_downgrades_premium_to_standard_under_pressure(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, topology: str, config, worker_backend: str, working_dir: str) -> None:
            captured["config"] = config

        def run(self, goal_text: str, max_rounds: int | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    class FakeCritiqueTopology:
        def __call__(self, value: str):
            return value

    class FakeCritiqueConfig:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_executor = SimpleNamespace(CritiqueExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(CritiqueConfig=FakeCritiqueConfig, CritiqueTopology=FakeCritiqueTopology())
    monkeypatch.setitem(sys.modules, "critique_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "critique_executor.models", fake_models)

    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(
            tier_name="standard",
            worker_backend="claude_code",
            topology="adversarial",
            dynamic_tier_selection=True,
        ),
        usage_store=_usage_store(remaining=2),
    )

    adapter.execute(_request(model="opus", config_ref="team_executor:premium"))

    assert captured["config"].proposer_model == "claude-sonnet-4-6"


def test_adapter_falls_back_to_codex_when_claude_backend_is_cooling_down(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, topology: str, config, worker_backend: str, working_dir: str) -> None:
            captured["worker_backend"] = worker_backend

        def run(self, goal_text: str, max_rounds: int | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    class FakeCritiqueTopology:
        def __call__(self, value: str):
            return value

    class FakeCritiqueConfig:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_executor = SimpleNamespace(CritiqueExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(CritiqueConfig=FakeCritiqueConfig, CritiqueTopology=FakeCritiqueTopology())
    monkeypatch.setitem(sys.modules, "critique_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "critique_executor.models", fake_models)

    def _cooldown(worker_backend: str, *, now):
        if worker_backend == "claude_code":
            return now + timedelta(hours=1)
        return None

    usage_store = _usage_store(remaining=10)
    usage_store.worker_backend_cooldown_until = _cooldown

    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=usage_store,
    )

    result = adapter.execute(_request(model="sonnet"))

    assert result.success is True
    assert captured["worker_backend"] == "codex_cli"


def test_adapter_execute_and_capture_reports_selected_worker_backend(monkeypatch) -> None:
    class FakeRunner:
        def __init__(self, topology: str, config, worker_backend: str, working_dir: str) -> None:
            self.worker_backend = worker_backend

        def run(self, goal_text: str, max_rounds: int | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    class FakeCritiqueTopology:
        def __call__(self, value: str):
            return value

    class FakeCritiqueConfig:
        def __init__(self, **kwargs) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_executor = SimpleNamespace(CritiqueExecutorRunner=FakeRunner)
    fake_models = SimpleNamespace(CritiqueConfig=FakeCritiqueConfig, CritiqueTopology=FakeCritiqueTopology())
    monkeypatch.setitem(sys.modules, "critique_executor.executor", fake_executor)
    monkeypatch.setitem(sys.modules, "critique_executor.models", fake_models)

    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="codex_cli", topology="reflexion"),
        usage_store=_usage_store(remaining=10),
    )

    result, capture = adapter.execute_and_capture(
        _request(model="gpt-5.4-mini", config_ref="team_executor:budget")
    )

    assert result.success is True
    assert capture.observed_runtime["preferred_worker_backend"] == "codex_cli"
    assert capture.observed_runtime["selected_worker_backend"] == "codex_cli"
