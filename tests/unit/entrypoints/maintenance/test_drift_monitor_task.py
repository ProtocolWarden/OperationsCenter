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


# ── C3 — cross-family panel wiring (COUNCIL_VERDICT.md C3) ────────────────────

_RIGHT = [
    {"check_id": "code_quality", "status": "fail"},
    {"check_id": "no_tooling_artifacts", "status": "pass"},
]  # computes to CONCERNS — matches the seeded x-concern answer
_WRONG = [
    {"check_id": "code_quality", "status": "pass"},
    {"check_id": "no_tooling_artifacts", "status": "pass"},
]  # computes to LGTM — disagrees


def test_panel_empty_by_default_falls_back_to_legacy_skip(monkeypatch, tmp_path):
    """No panel_families/panel_enabled given and no single extractor either —
    empty-panel-config means the feature is OFF, matching the existing inert
    fail-safe (not a new code path)."""
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    task = DriftMonitorTask(settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1)
    result = task.run_once(_ctx())
    assert result.status == "skipped"


def test_panel_disabled_ignores_configured_family_extractors(monkeypatch, tmp_path):
    """panel_enabled=False must not run the panel even if family_extractors are
    injected and non-empty — enabled is a real gate, not just a presence check."""
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        panel_families=["claude_code", "codex_cli"],
        family_extractors={"claude_code": _extractor(_RIGHT), "codex_cli": _extractor(_RIGHT)},
        panel_enabled=False,
    )
    result = task.run_once(_ctx())
    assert result.status == "skipped"


def test_panel_full_quorum_runs_and_catches_family_drift(monkeypatch, tmp_path):
    """Full panel (every configured family has a runnable extractor): runs
    run_panel_drift_monitor and still catches a single family's drift."""
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    plane = _FakePlane()
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        panel_families=["claude_code", "codex_cli"],
        family_extractors={"claude_code": _extractor(_WRONG), "codex_cli": _extractor(_RIGHT)},
        panel_enabled=True,
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["drifted"] == 1
    assert result.details["panel"] == ["claude_code", "codex_cli"]
    assert len(plane.created) == 1


def test_panel_no_drift_when_every_family_agrees(monkeypatch, tmp_path):
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    plane = _FakePlane()
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        panel_families=["claude_code", "codex_cli"],
        family_extractors={"claude_code": _extractor(_RIGHT), "codex_cli": _extractor(_RIGHT)},
        panel_enabled=True,
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["drifted"] == 0
    assert plane.created == []


def test_panel_degraded_missing_family_skips_never_collapses(monkeypatch, tmp_path):
    """The essential degraded-panel rule: a configured family (codex_cli) has
    NO runnable extractor here (e.g. its CLI wasn't resolvable). The task must
    skip loudly — never silently grade with just claude_code (a same-family
    grade is exactly the HIGH finding C3 exists to close)."""
    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    plane = _FakePlane()
    task = DriftMonitorTask(
        settings=None, corpus_path=_extraction_corpus(tmp_path), votes=1,
        panel_families=["claude_code", "codex_cli"],
        family_extractors={"claude_code": _extractor(_WRONG)},  # codex_cli missing
        panel_enabled=True,
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "skipped"
    assert "degraded" in result.details["reason"]
    assert result.details["missing"] == ["codex_cli"]
    assert plane.created == []  # never ran, never ticketed


def test_panel_enabled_via_settings_object(monkeypatch, tmp_path):
    """panel_families/panel_enabled derive from settings.eval_panel when not
    passed explicitly — the spec_hygiene wiring shape (a real Settings)."""
    from types import SimpleNamespace

    monkeypatch.setenv("OC_EVAL_DRIFT_MONITOR", "1")
    settings = SimpleNamespace(
        eval_panel=SimpleNamespace(panel=["claude_code", "codex_cli"], enabled=True, votes=1)
    )
    plane = _FakePlane()
    task = DriftMonitorTask(
        settings=settings, corpus_path=_extraction_corpus(tmp_path),
        family_extractors={"claude_code": _extractor(_WRONG), "codex_cli": _extractor(_RIGHT)},
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["drifted"] == 1
