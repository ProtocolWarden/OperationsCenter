# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

from cxrp.contracts.runtime_binding import RuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.backends.team_executor.adapter import (
    TeamExecutorBackendAdapter,
    _select_team_name,
)
from operations_center.config.settings import TeamExecutorSettings
from operations_center.contracts.execution import ExecutionRequest


def _request(*, model: str | None = None) -> ExecutionRequest:
    runtime_binding = None
    if model is not None:
        runtime_binding = RuntimeBinding(
            kind=RuntimeKind.CLI_SUBSCRIPTION,
            selection_mode=SelectionMode.POLICY_SELECTED,
            model=model,
            provider="anthropic",
        )
    return ExecutionRequest(
        run_id="run-1",
        proposal_id="proposal-1",
        decision_id="decision-1",
        goal_text="Implement the task",
        repo_key="OperationsCenter",
        clone_url="git@github.com:Velascat/OperationsCenter.git",
        base_branch="main",
        task_branch="task/test",
        workspace_path=Path("/tmp/oc-team-executor"),
        runtime_binding=runtime_binding,
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


def test_select_team_name_uses_runtime_binding_tier_without_pressure() -> None:
    settings = TeamExecutorSettings(team_name="standard", dynamic_team_selection=True)

    assert _select_team_name(settings, _request(model="opus"), usage_store=_usage_store(remaining=10)) == "premium"
    assert _select_team_name(settings, _request(model="sonnet"), usage_store=_usage_store(remaining=10)) == "standard"
    assert _select_team_name(settings, _request(model="haiku"), usage_store=_usage_store(remaining=10)) == "budget"
    assert _select_team_name(settings, _request(model="gpt-5.4-mini"), usage_store=_usage_store(remaining=10)) == "budget"


def test_select_team_name_prefers_config_ref_tier_hint() -> None:
    settings = TeamExecutorSettings(team_name="standard", dynamic_team_selection=True)
    request = _request(model="gpt-5.4")
    request = request.model_copy(
        update={
            "runtime_binding": RuntimeBinding(
                kind=RuntimeKind.CLI_SUBSCRIPTION,
                selection_mode=SelectionMode.POLICY_SELECTED,
                model="gpt-5.4",
                provider="openai",
                config_ref="team_executor:premium",
            )
        }
    )

    assert _select_team_name(settings, request, usage_store=_usage_store(remaining=10)) == "premium"


def test_select_team_name_downgrades_one_tier_under_budget_pressure() -> None:
    settings = TeamExecutorSettings(
        team_name="standard",
        dynamic_team_selection=True,
        budget_pressure_threshold=0.75,
    )
    pressured = _usage_store(remaining=2)

    assert _select_team_name(settings, _request(model="opus"), usage_store=pressured) == "standard"
    assert _select_team_name(settings, _request(model="sonnet"), usage_store=pressured) == "budget"
    assert _select_team_name(settings, _request(model="haiku"), usage_store=pressured) == "budget"


def test_select_team_name_respects_static_mode() -> None:
    settings = TeamExecutorSettings(
        team_name="premium",
        dynamic_team_selection=False,
        budget_pressure_threshold=0.75,
    )

    selected = _select_team_name(
        settings,
        _request(model="haiku"),
        usage_store=_usage_store(remaining=1),
    )

    assert selected == "premium"


def test_adapter_execute_passes_selected_team_to_runner(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, team_name: str, working_dir: str, worker_backend: str) -> None:
            captured["team_name"] = team_name
            captured["working_dir"] = working_dir
            captured["worker_backend"] = worker_backend

        def run(self, goal_text: str, invocation_id: str | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    fake_module = SimpleNamespace(TeamExecutorRunner=FakeRunner)
    monkeypatch.setitem(sys.modules, "team_executor.executor", fake_module)

    adapter = TeamExecutorBackendAdapter(
        TeamExecutorSettings(
            team_name="standard",
            worker_backend="codex_cli",
            dynamic_team_selection=True,
            budget_pressure_threshold=0.75,
        ),
        usage_store=_usage_store(remaining=2),
    )

    result = adapter.execute(_request(model="opus"))

    assert result.success is True
    assert captured["team_name"] == "standard"
    assert captured["worker_backend"] == "codex_cli"


def test_adapter_falls_back_to_codex_when_claude_backend_is_cooling_down(monkeypatch) -> None:
    captured: list[str] = []

    class FakeRunner:
        def __init__(self, team_name: str, working_dir: str, worker_backend: str) -> None:
            captured.append(worker_backend)

        def run(self, goal_text: str, invocation_id: str | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    fake_module = SimpleNamespace(TeamExecutorRunner=FakeRunner)
    monkeypatch.setitem(sys.modules, "team_executor.executor", fake_module)

    def _cooldown(worker_backend: str, *, now):
        if worker_backend == "claude_code":
            return now + timedelta(hours=1)
        return None

    usage_store = _usage_store(remaining=10)
    usage_store.worker_backend_cooldown_until = _cooldown

    adapter = TeamExecutorBackendAdapter(
        TeamExecutorSettings(worker_backend="claude_code"),
        usage_store=usage_store,
    )

    result = adapter.execute(_request(model="sonnet"))

    assert result.success is True
    assert captured == ["codex_cli"]


def test_adapter_execute_and_capture_reports_selected_worker_backend(monkeypatch) -> None:
    class FakeRunner:
        def __init__(self, team_name: str, working_dir: str, worker_backend: str) -> None:
            self.worker_backend = worker_backend

        def run(self, goal_text: str, invocation_id: str | None = None):
            return SimpleNamespace(status="succeeded", error_summary=None)

    fake_module = SimpleNamespace(TeamExecutorRunner=FakeRunner)
    monkeypatch.setitem(sys.modules, "team_executor.executor", fake_module)

    adapter = TeamExecutorBackendAdapter(
        TeamExecutorSettings(worker_backend="claude_code"),
        usage_store=_usage_store(remaining=10),
    )

    result, capture = adapter.execute_and_capture(_request(model="sonnet"))

    assert result.success is True
    assert capture.observed_runtime["preferred_worker_backend"] == "claude_code"
    assert capture.observed_runtime["selected_worker_backend"] == "claude_code"
    assert capture.observed_runtime["fallback_used"] is False
