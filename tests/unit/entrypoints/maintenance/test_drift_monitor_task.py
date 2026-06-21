# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the controller-tier reviewer drift monitor task (D-EVAL-5)."""

from __future__ import annotations

from datetime import UTC, datetime

from operations_center.entrypoints.maintenance.drift_monitor_task import DriftMonitorTask
from operations_center.eval.corpus import Case, append_case
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceTask


def _ctx(plane_client=None) -> MaintenanceContext:
    resources = {"plane_client": plane_client} if plane_client is not None else {}
    return MaintenanceContext(
        cycle_id="c", now=datetime(2026, 6, 21, tzinfo=UTC), resources=resources
    )


class _FakePlane:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.created = []

    def list_issues(self):
        return self.existing

    def create_issue(self, *, name, description, label_names=None):
        self.created.append({"name": name})
        return {"id": f"new-{len(self.created)}", "name": name}


def _extraction_corpus(tmp_path):
    p = tmp_path / "ledger.jsonl"
    append_case(p, Case(
        case_id="x-concern", kind="extraction", input={"diff": "buggy"},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    ))
    append_case(p, Case(  # a verdict-kind case must be IGNORED by the drift monitor
        case_id="v-1", kind="verdict",
        input={"checks": [{"check_id": "code_quality", "status": "pass"}]},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    ))
    return p


def _extractor(checks):
    return lambda case, *, vote: checks


def test_satisfies_protocol():
    task = DriftMonitorTask(settings=None)
    assert isinstance(task, MaintenanceTask)
    assert task.name == "drift_monitor"
    assert task.interval_seconds > 0


def test_skipped_without_extractor(monkeypatch):
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    assert DriftMonitorTask(settings=None).run_once(_ctx()).status == "skipped"


def test_skipped_when_not_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv("OC_EVAL_DRIFT_MONITOR", raising=False)
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path),
        extractor=_extractor([{"check_id": "code_quality", "status": "fail"}]),
    )
    assert task.run_once(_ctx()).status == "skipped"


def test_no_drift_when_model_matches_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    plane = _FakePlane()
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        extractor=_extractor([
            {"check_id": "code_quality", "status": "fail"},
            {"check_id": "no_tooling_artifacts", "status": "pass"},
        ]),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["cases"] == 1  # only the extraction case, not the verdict one
    assert result.details["drifted"] == 0
    assert plane.created == []


def test_drift_files_ticket(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    plane = _FakePlane()
    # Model says everything passes → LGTM, but the answer is CONCERNS → drift.
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        extractor=_extractor([
            {"check_id": "code_quality", "status": "pass"},
            {"check_id": "no_tooling_artifacts", "status": "pass"},
        ]),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["drifted"] == 1
    assert len(plane.created) == 1
    assert plane.created[0]["name"] == "[eval-drift] x-concern"


def test_drift_ticket_dedup(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    plane = _FakePlane(existing=[
        {"id": "e", "name": "[eval-drift] x-concern", "state": {"name": "In Progress"}},
    ])
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        extractor=_extractor([{"check_id": "code_quality", "status": "pass"},
                              {"check_id": "no_tooling_artifacts", "status": "pass"}]),
    )
    task.run_once(_ctx(plane))
    assert plane.created == []
