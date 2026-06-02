# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""SpecHygieneTask conforms to MaintenanceTask and returns a structured
result on the happy path (ADR 0007 follow-up D)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from operations_center.entrypoints.spec_hygiene import main as spec_hygiene_main
from operations_center.maintenance import (
    MaintenanceContext,
    MaintenanceTask,
)


def _fake_settings(*, enabled: bool = True, poll: int = 30) -> SimpleNamespace:
    return SimpleNamespace(
        spec_author=SimpleNamespace(
            enabled=enabled,
            poll_interval_seconds=poll,
            spec_retention_days=30,
            campaign_abandon_hours=24,
            max_tasks_per_campaign=10,
        ),
        plane=SimpleNamespace(
            base_url="http://stub",
            workspace_slug="ws",
            project_id="proj",
        ),
        repos={},
        plane_token=lambda: "stub-token",
    )


def test_spec_hygiene_task_is_protocol_compatible():
    settings = _fake_settings()
    task = spec_hygiene_main.SpecHygieneTask(settings, client=object())
    assert isinstance(task, MaintenanceTask)
    assert task.name == "spec_hygiene"
    assert task.interval_seconds == 30
    assert task.enabled is True


def test_spec_hygiene_task_happy_path(monkeypatch):
    """When run_once succeeds, SpecHygieneTask returns status=ok with the
    summary attached as details."""
    fake_summary = {
        "status_hint": "ok",
        "campaigns_projected": 3,
        "phases_advanced": 1,
        "campaigns_completed": 0,
        "phase_advance_tasks_emitted": 1,
        "campaigns_abandoned": 0,
    }

    def _fake_run_once(settings, client):  # noqa: ARG001
        return dict(fake_summary)

    monkeypatch.setattr(spec_hygiene_main, "run_once", _fake_run_once)

    settings = _fake_settings()
    task = spec_hygiene_main.SpecHygieneTask(settings, client=object())
    ctx = MaintenanceContext(
        cycle_id="cycle-abc",
        now=datetime.now(timezone.utc),
    )
    result = task.run_once(ctx)

    assert result.name == "spec_hygiene"
    assert result.status == "ok"
    assert result.error is None
    assert result.duration_seconds >= 0.0
    assert result.details["cycle_id"] == "cycle-abc"
    assert result.details["campaigns_projected"] == 3
    assert result.details["phase_advance_tasks_emitted"] == 1


def test_spec_hygiene_task_disabled_returns_skipped(monkeypatch):
    monkeypatch.setattr(
        spec_hygiene_main,
        "run_once",
        lambda s, c: {"status_hint": "skipped", "reason": "spec_author_disabled"},
    )
    task = spec_hygiene_main.SpecHygieneTask(_fake_settings(enabled=False), client=object())
    result = task.run_once(MaintenanceContext(cycle_id="c", now=datetime.now(timezone.utc)))
    assert result.status == "skipped"
    assert result.details["reason"] == "spec_author_disabled"


def test_spec_hygiene_task_exception_returns_failed(monkeypatch):
    def _boom(settings, client):  # noqa: ARG001
        raise RuntimeError("plane down")

    monkeypatch.setattr(spec_hygiene_main, "run_once", _boom)
    task = spec_hygiene_main.SpecHygieneTask(_fake_settings(), client=object())
    result = task.run_once(MaintenanceContext(cycle_id="c", now=datetime.now(timezone.utc)))
    assert result.status == "failed"
    assert result.error == "plane down"
