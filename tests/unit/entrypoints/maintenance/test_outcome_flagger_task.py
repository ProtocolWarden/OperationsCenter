# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the controller-tier outcome-correlation flagger task (D-EVAL-1)."""

from __future__ import annotations

from datetime import UTC, datetime

from operations_center.entrypoints.maintenance.outcome_flagger_task import (
    OutcomeFlaggerTask,
)
from operations_center.eval.outcome_flagger import ReviewOutcome
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceTask


def _ctx(plane_client=None) -> MaintenanceContext:
    resources = {"plane_client": plane_client} if plane_client is not None else {}
    return MaintenanceContext(
        cycle_id="c", now=datetime(2026, 6, 21, tzinfo=UTC), resources=resources
    )


class _FakePlane:
    def __init__(self, existing: list[dict] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict] = []

    def list_issues(self) -> list[dict]:
        return self.existing

    def create_issue(self, *, name, description, label_names=None):
        issue = {"id": f"new-{len(self.created)}", "name": name}
        self.created.append({"name": name, "labels": label_names})
        return issue


def _source(outcomes):
    return lambda: list(outcomes)


def test_satisfies_protocol_and_defaults():
    task = OutcomeFlaggerTask(settings=None)
    assert isinstance(task, MaintenanceTask)
    assert task.name == "outcome_flagger"
    assert task.interval_seconds > 0
    assert task.enabled is True


def test_skipped_when_no_source_wired(monkeypatch):
    monkeypatch.delenv("OC_EVAL_OUTCOME_SOURCE", raising=False)
    task = OutcomeFlaggerTask(settings=None)
    result = task.run_once(_ctx())
    assert result.status == "skipped"
    assert "no outcome source" in result.details["reason"]


def test_github_source_opt_in_builds_a_source(monkeypatch):
    monkeypatch.setenv("OC_EVAL_OUTCOME_SOURCE", "github")
    monkeypatch.setenv("GITHUB_TOKEN", "x-token")
    from operations_center.eval.outcome_sources import GitHubOutcomeSource

    task = OutcomeFlaggerTask(settings=None)
    assert isinstance(task._resolve_outcome_source(), GitHubOutcomeSource)


def test_github_source_not_built_without_opt_in(monkeypatch):
    monkeypatch.delenv("OC_EVAL_OUTCOME_SOURCE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "x-token")  # token present but not opted in
    task = OutcomeFlaggerTask(settings=None)
    assert task._resolve_outcome_source() is None


def test_skipped_when_source_empty():
    task = OutcomeFlaggerTask(settings=None, outcome_source=_source([]))
    assert task.run_once(_ctx()).status == "skipped"


def test_clean_outcomes_ok_no_tickets():
    plane = _FakePlane()
    task = OutcomeFlaggerTask(
        settings=None,
        outcome_source=_source([ReviewOutcome(1, "LGTM", merged=True, main_regressed=False)]),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["disagreements"] == 0
    assert plane.created == []


def test_disagreements_file_dedup_tickets():
    plane = _FakePlane()
    task = OutcomeFlaggerTask(
        settings=None,
        outcome_source=_source([
            ReviewOutcome(10, "LGTM", merged=True, main_regressed=True, repo="OC"),
            ReviewOutcome(11, "CONCERNS", requeued_to_death=True, repo="OC"),
        ]),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["disagreements"] == 2
    assert result.details["by_attribution"] == {"reviewer": 1, "worker": 1}
    assert len(plane.created) == 2
    assert all(t["name"].startswith("[eval-flag]") for t in plane.created)


def test_existing_open_ticket_not_duplicated():
    plane = _FakePlane(existing=[
        {"id": "x", "name": "[eval-flag] OC#10:lgtm_then_regression",
         "state": {"name": "In Progress"}},
    ])
    task = OutcomeFlaggerTask(
        settings=None,
        outcome_source=_source([
            ReviewOutcome(10, "LGTM", merged=True, main_regressed=True, repo="OC"),
        ]),
    )
    result = task.run_once(_ctx(plane))
    assert plane.created == []
    assert result.details["tickets"] == ["exists:OC#10:lgtm_then_regression"]


def test_terminal_ticket_does_not_suppress_new():
    plane = _FakePlane(existing=[
        {"id": "old", "name": "[eval-flag] OC#10:lgtm_then_regression",
         "state": {"name": "Done"}},
    ])
    task = OutcomeFlaggerTask(
        settings=None,
        outcome_source=_source([
            ReviewOutcome(10, "LGTM", merged=True, main_regressed=True, repo="OC"),
        ]),
    )
    task.run_once(_ctx(plane))
    assert len(plane.created) == 1


def test_source_failure_is_reported_not_raised():
    def _boom():
        raise RuntimeError("instrumentation down")

    task = OutcomeFlaggerTask(settings=None, outcome_source=_boom)
    result = task.run_once(_ctx())
    assert result.status == "failed"
    assert "instrumentation down" in (result.error or "")
