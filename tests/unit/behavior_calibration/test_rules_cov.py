# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from operations_center.audit_contracts.vocabulary import (
    ArtifactStatus,
    Limitation,
    Location,
    RunStatus,
)
from operations_center.behavior_calibration import rules
from operations_center.behavior_calibration.models import (
    FindingCategory,
    FindingSeverity,
)


# ---------------------------------------------------------------------------
# Fakes — rules only read attributes, so lightweight namespaces suffice.
# ---------------------------------------------------------------------------


def _artifact(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "artifact_id": "a:b:c:primary",
        "artifact_kind": "stage_report",
        "status": ArtifactStatus.PRESENT,
        "is_partial": False,
        "resolved_path": "/tmp/x.json",
        "location": Location.RUN_ROOT,
        "exists_on_disk": True,
        "limitations": [],
        "content_type": "application/json",
        "description": "desc",
        "consumer_types": ["human_review"],
        "valid_for": ["current_run_only"],
        "is_machine_readable": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _index(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "run_status": RunStatus.COMPLETED,
        "manifest_status": SimpleNamespace(value="completed"),
        "errors": [],
        "warnings": [],
        "artifacts": [_artifact()],
        "singleton_artifacts": [],
        "excluded_paths": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _sources(findings: list) -> set[str]:
    return {f.source for f in findings}


def _categories(findings: list) -> set[FindingCategory]:
    return {f.category for f in findings}


# ---------------------------------------------------------------------------
# check_run_status
# ---------------------------------------------------------------------------


def test_run_status_completed_clean_yields_no_findings():
    findings = rules.check_run_status(_index())
    assert findings == []


def test_run_status_failed_emits_error_finding():
    idx = _index(run_status=RunStatus.FAILED)
    findings = rules.check_run_status(idx)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == FindingSeverity.ERROR
    assert f.category == FindingCategory.FAILED_RUN
    assert "failed" in f.summary
    assert "completed" in f.detail  # manifest_status interpolated


def test_run_status_interrupted_emits_partial_warning():
    idx = _index(run_status=RunStatus.INTERRUPTED)
    findings = rules.check_run_status(idx)
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.WARNING
    assert findings[0].category == FindingCategory.PARTIAL_RUN


def test_run_status_unexpected_value_emits_unknown_warning():
    idx = _index(run_status=RunStatus.PENDING)
    findings = rules.check_run_status(idx)
    assert len(findings) == 1
    assert findings[0].category == FindingCategory.UNKNOWN
    assert "pending" in findings[0].summary


def test_run_status_errors_truncated_to_five():
    errs = [f"err{i}" for i in range(8)]
    idx = _index(errors=errs)
    findings = rules.check_run_status(idx)
    err_findings = [f for f in findings if f.category == FindingCategory.RUNTIME_FAILURE]
    assert len(err_findings) == 1
    assert "8 error" in err_findings[0].summary
    # detail joins only the first five entries
    assert err_findings[0].detail.count("\n") == 4
    assert "err5" not in err_findings[0].detail


def test_run_status_warnings_emit_warning_finding():
    idx = _index(warnings=["w1", "w2"])
    findings = rules.check_run_status(idx)
    warn = [f for f in findings if f.category == FindingCategory.UNKNOWN]
    assert len(warn) == 1
    assert warn[0].severity == FindingSeverity.WARNING
    assert "2 warning" in warn[0].summary


def test_run_status_combines_failed_errors_and_warnings():
    idx = _index(run_status=RunStatus.FAILED, errors=["e"], warnings=["w"])
    findings = rules.check_run_status(idx)
    cats = _categories(findings)
    assert cats == {
        FindingCategory.FAILED_RUN,
        FindingCategory.RUNTIME_FAILURE,
        FindingCategory.UNKNOWN,
    }


# ---------------------------------------------------------------------------
# check_partial_artifacts
# ---------------------------------------------------------------------------


def test_partial_artifacts_none_when_all_present_and_complete():
    findings = rules.check_partial_artifacts(_index())
    assert findings == []


def test_partial_artifacts_reports_missing_status():
    miss = _artifact(artifact_id="m1", status=ArtifactStatus.MISSING)
    idx = _index(artifacts=[miss, _artifact()])
    findings = rules.check_partial_artifacts(idx)
    missing = [f for f in findings if f.category == FindingCategory.MISSING_ARTIFACT]
    assert len(missing) == 1
    assert missing[0].artifact_ids == ["m1"]
    assert missing[0].severity == FindingSeverity.WARNING


def test_partial_artifacts_reports_partial_limitation():
    part = _artifact(artifact_id="p1", is_partial=True)
    idx = _index(artifacts=[part])
    findings = rules.check_partial_artifacts(idx)
    partial = [f for f in findings if f.category == FindingCategory.PARTIAL_RUN]
    assert len(partial) == 1
    assert partial[0].artifact_ids == ["p1"]


def test_partial_artifacts_reports_both():
    idx = _index(
        artifacts=[
            _artifact(artifact_id="m", status=ArtifactStatus.MISSING),
            _artifact(artifact_id="p", is_partial=True),
        ]
    )
    findings = rules.check_partial_artifacts(idx)
    assert _categories(findings) == {
        FindingCategory.MISSING_ARTIFACT,
        FindingCategory.PARTIAL_RUN,
    }


# ---------------------------------------------------------------------------
# check_unresolved_paths
# ---------------------------------------------------------------------------


def test_unresolved_paths_none_when_all_resolved():
    assert rules.check_unresolved_paths(_index()) == []


def test_unresolved_paths_non_external_warning():
    a = _artifact(artifact_id="u", resolved_path=None, location=Location.RUN_ROOT)
    idx = _index(artifacts=[a])
    findings = rules.check_unresolved_paths(idx)
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.WARNING
    assert findings[0].category == FindingCategory.UNRESOLVED_PATH
    assert findings[0].artifact_ids == ["u"]


def test_unresolved_paths_external_info():
    a = _artifact(
        artifact_id="x",
        resolved_path=None,
        location=Location.EXTERNAL_OR_UNKNOWN,
    )
    idx = _index(artifacts=[a])
    findings = rules.check_unresolved_paths(idx)
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.INFO
    assert findings[0].artifact_ids == ["x"]


def test_unresolved_paths_both_external_and_non_external():
    idx = _index(
        artifacts=[
            _artifact(artifact_id="n", resolved_path=None, location=Location.RUN_ROOT),
            _artifact(
                artifact_id="e",
                resolved_path=None,
                location=Location.EXTERNAL_OR_UNKNOWN,
            ),
        ]
    )
    findings = rules.check_unresolved_paths(idx)
    sev = {f.severity for f in findings}
    assert sev == {FindingSeverity.WARNING, FindingSeverity.INFO}


# ---------------------------------------------------------------------------
# check_missing_files
# ---------------------------------------------------------------------------


def test_missing_files_none_when_present():
    assert rules.check_missing_files(_index()) == []


def test_missing_files_reports_resolved_but_absent():
    a = _artifact(artifact_id="g", exists_on_disk=False, resolved_path="/tmp/gone.json")
    idx = _index(artifacts=[a])
    findings = rules.check_missing_files(idx)
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.ERROR
    assert findings[0].category == FindingCategory.MISSING_FILE
    assert findings[0].artifact_ids == ["g"]


def test_missing_files_ignores_unresolved_absent():
    # exists_on_disk False but resolved_path None -> not a missing-file case
    a = _artifact(exists_on_disk=False, resolved_path=None)
    idx = _index(artifacts=[a])
    assert rules.check_missing_files(idx) == []


# ---------------------------------------------------------------------------
# check_singleton_limitations
# ---------------------------------------------------------------------------


def test_singleton_limitations_none_when_no_singletons():
    assert rules.check_singleton_limitations(_index(singleton_artifacts=[])) == []


def test_singleton_limitations_present_without_overwrite_flag():
    s = _artifact(artifact_id="s", location=Location.REPO_SINGLETON, limitations=[])
    idx = _index(singleton_artifacts=[s])
    assert rules.check_singleton_limitations(idx) == []


def test_singleton_limitations_overwritten_emits_info():
    s = _artifact(
        artifact_id="s",
        location=Location.REPO_SINGLETON,
        limitations=[Limitation.REPO_SINGLETON_OVERWRITTEN],
    )
    idx = _index(singleton_artifacts=[s])
    findings = rules.check_singleton_limitations(idx)
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.INFO
    assert findings[0].category == FindingCategory.REPO_SINGLETON_WARNING
    assert findings[0].artifact_ids == ["s"]


# ---------------------------------------------------------------------------
# check_excluded_paths
# ---------------------------------------------------------------------------


def test_excluded_paths_none_when_empty():
    assert rules.check_excluded_paths(_index(excluded_paths=[])) == []


def test_excluded_paths_reports_count_and_metadata():
    idx = _index(excluded_paths=["coverage.ini", ".coverage.abc"])
    findings = rules.check_excluded_paths(idx)
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.INFO
    assert findings[0].category == FindingCategory.NOISE_EXCLUSION
    assert findings[0].metadata == {"excluded_path_count": 2}


# ---------------------------------------------------------------------------
# check_producer_compliance
# ---------------------------------------------------------------------------


def test_producer_compliance_clean_artifact_yields_no_findings():
    assert rules.check_producer_compliance(_index()) == []


@pytest.mark.parametrize("bad_ct", ["unknown", ""])
def test_producer_compliance_unknown_content_type(bad_ct):
    a = _artifact(artifact_id="c", content_type=bad_ct)
    findings = rules.check_producer_compliance(_index(artifacts=[a]))
    ct = [f for f in findings if "content_type" in f.summary]
    assert len(ct) == 1
    assert ct[0].severity == FindingSeverity.WARNING
    assert ct[0].category == FindingCategory.PRODUCER_CONTRACT_GAP


def test_producer_compliance_no_description():
    a = _artifact(artifact_id="d", description="")
    findings = rules.check_producer_compliance(_index(artifacts=[a]))
    desc = [f for f in findings if "no description" in f.summary]
    assert len(desc) == 1
    assert desc[0].severity == FindingSeverity.INFO


def test_producer_compliance_no_consumer_types():
    a = _artifact(artifact_id="cc", consumer_types=[])
    findings = rules.check_producer_compliance(_index(artifacts=[a]))
    cons = [f for f in findings if "consumer_types" in f.summary]
    assert len(cons) == 1
    assert cons[0].artifact_ids == ["cc"]


def test_producer_compliance_no_valid_for():
    a = _artifact(artifact_id="vf", valid_for=[])
    findings = rules.check_producer_compliance(_index(artifacts=[a]))
    vf = [f for f in findings if "valid_for" in f.summary]
    assert len(vf) == 1
    assert vf[0].artifact_ids == ["vf"]


def test_producer_compliance_all_gaps_at_once():
    a = _artifact(
        artifact_id="bad",
        content_type="unknown",
        description="",
        consumer_types=[],
        valid_for=[],
    )
    findings = rules.check_producer_compliance(_index(artifacts=[a]))
    assert len(findings) == 4
    severities = [f.severity for f in findings]
    assert severities.count(FindingSeverity.WARNING) == 1
    assert severities.count(FindingSeverity.INFO) == 3


# ---------------------------------------------------------------------------
# check_coverage_gaps
# ---------------------------------------------------------------------------


def test_coverage_gaps_empty_manifest_errors():
    findings = rules.check_coverage_gaps(_index(artifacts=[]))
    assert len(findings) == 1
    assert findings[0].severity == FindingSeverity.ERROR
    assert findings[0].category == FindingCategory.COVERAGE_GAP
    assert "No artifacts" in findings[0].summary


def test_coverage_gaps_all_present_no_findings():
    idx = _index(
        artifacts=[
            _artifact(artifact_id="a1", status=ArtifactStatus.PRESENT),
            _artifact(artifact_id="a2", status=ArtifactStatus.PRESENT),
        ]
    )
    assert rules.check_coverage_gaps(idx) == []


def test_coverage_gaps_high_missing_ratio_warns():
    # 2 of 2 missing -> ratio 1.0, len present >= 2; also both same kind -> all-missing kind finding
    idx = _index(
        artifacts=[
            _artifact(artifact_id="m1", status=ArtifactStatus.MISSING, artifact_kind="k"),
            _artifact(artifact_id="m2", status=ArtifactStatus.MISSING, artifact_kind="k"),
        ]
    )
    findings = rules.check_coverage_gaps(idx)
    summaries = [f.summary for f in findings]
    assert any("coverage gap" in s for s in summaries)
    assert any("kind 'k'" in s for s in summaries)
    assert all(f.category == FindingCategory.COVERAGE_GAP for f in findings)


def test_coverage_gaps_single_missing_below_threshold():
    # 1 missing of 3 -> ratio < 0.5 and present count < 2: no ratio finding
    idx = _index(
        artifacts=[
            _artifact(artifact_id="m", status=ArtifactStatus.MISSING, artifact_kind="ka"),
            _artifact(artifact_id="p1", status=ArtifactStatus.PRESENT, artifact_kind="kb"),
            _artifact(artifact_id="p2", status=ArtifactStatus.PRESENT, artifact_kind="kb"),
        ]
    )
    findings = rules.check_coverage_gaps(idx)
    # No overall ratio finding; one all-missing-kind finding for 'ka'
    assert not any("coverage gap" in f.summary for f in findings)
    kind_findings = [f for f in findings if "kind 'ka'" in f.summary]
    assert len(kind_findings) == 1


def test_coverage_gaps_kind_with_some_present_not_flagged():
    idx = _index(
        artifacts=[
            _artifact(artifact_id="m", status=ArtifactStatus.MISSING, artifact_kind="k"),
            _artifact(artifact_id="p", status=ArtifactStatus.PRESENT, artifact_kind="k"),
        ]
    )
    findings = rules.check_coverage_gaps(idx)
    assert not any("kind 'k'" in f.summary for f in findings)


# ---------------------------------------------------------------------------
# check_content
# ---------------------------------------------------------------------------


def _patch_retrieval(monkeypatch, *, json_fn=None, text_fn=None):
    import operations_center.artifact_index.retrieval as retr

    if json_fn is not None:
        monkeypatch.setattr(retr, "read_json_artifact", json_fn)
    if text_fn is not None:
        monkeypatch.setattr(retr, "read_text_artifact", text_fn)


def test_content_skips_unreadable_artifacts(monkeypatch):
    called = {"json": 0, "text": 0}

    def _json(*a, **k):
        called["json"] += 1

    def _text(*a, **k):
        called["text"] += 1

    _patch_retrieval(monkeypatch, json_fn=_json, text_fn=_text)
    # not on disk -> skipped entirely
    a = _artifact(exists_on_disk=False)
    findings = rules.check_content(_index(artifacts=[a]), None, 1000)
    assert findings == []
    assert called == {"json": 0, "text": 0}


def test_content_selected_ids_filters(monkeypatch):
    seen_ids = []

    def _json(index, aid, *, max_bytes):
        seen_ids.append(aid)

    _patch_retrieval(monkeypatch, json_fn=_json)
    idx = _index(
        artifacts=[
            _artifact(artifact_id="keep", content_type="application/json"),
            _artifact(artifact_id="drop", content_type="application/json"),
        ]
    )
    findings = rules.check_content(idx, ["keep"], 500)
    assert findings == []
    assert seen_ids == ["keep"]


def test_content_json_success_no_findings(monkeypatch):
    captured = {}

    def _json(index, aid, *, max_bytes):
        captured["max_bytes"] = max_bytes
        return {"ok": True}

    _patch_retrieval(monkeypatch, json_fn=_json)
    a = _artifact(artifact_id="j", content_type="application/json", is_machine_readable=True)
    findings = rules.check_content(_index(artifacts=[a]), None, 4242)
    assert findings == []
    assert captured["max_bytes"] == 4242


def test_content_json_parse_failure_emits_error(monkeypatch):
    def _json(index, aid, *, max_bytes):
        raise ValueError("bad json")

    _patch_retrieval(monkeypatch, json_fn=_json)
    a = _artifact(artifact_id="j", content_type="application/json", is_machine_readable=True)
    findings = rules.check_content(_index(artifacts=[a]), None, 100)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == FindingSeverity.ERROR
    assert f.category == FindingCategory.INVALID_JSON
    assert f.confidence == "high"
    assert "bad json" in f.detail
    assert f.artifact_ids == ["j"]


def test_content_json_skipped_when_not_machine_readable(monkeypatch):
    called = {"n": 0}

    def _json(*a, **k):
        called["n"] += 1

    _patch_retrieval(monkeypatch, json_fn=_json)
    # json content_type but is_machine_readable False -> branch not taken,
    # and does not start with text/ -> nothing read
    a = _artifact(content_type="application/json", is_machine_readable=False)
    findings = rules.check_content(_index(artifacts=[a]), None, 100)
    assert findings == []
    assert called["n"] == 0


def test_content_text_success_no_findings(monkeypatch):
    def _text(index, aid, *, max_bytes):
        return "hello"

    _patch_retrieval(monkeypatch, text_fn=_text)
    a = _artifact(artifact_id="t", content_type="text/plain")
    findings = rules.check_content(_index(artifacts=[a]), None, 100)
    assert findings == []


def test_content_text_unresolvable_swallowed(monkeypatch):
    from operations_center.artifact_index.errors import ArtifactPathUnresolvableError

    def _text(index, aid, *, max_bytes):
        raise ArtifactPathUnresolvableError("no root")

    _patch_retrieval(monkeypatch, text_fn=_text)
    a = _artifact(artifact_id="t", content_type="text/markdown")
    findings = rules.check_content(_index(artifacts=[a]), None, 100)
    assert findings == []


def test_content_text_other_error_emits_warning(monkeypatch):
    def _text(index, aid, *, max_bytes):
        raise OSError("disk gone")

    _patch_retrieval(monkeypatch, text_fn=_text)
    a = _artifact(artifact_id="t", content_type="text/plain")
    findings = rules.check_content(_index(artifacts=[a]), None, 100)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == FindingSeverity.WARNING
    assert f.category == FindingCategory.MISSING_FILE
    assert f.confidence == "medium"
    assert "disk gone" in f.detail


def test_content_non_text_non_json_ignored(monkeypatch):
    called = {"json": 0, "text": 0}

    def _json(*a, **k):
        called["json"] += 1

    def _text(*a, **k):
        called["text"] += 1

    _patch_retrieval(monkeypatch, json_fn=_json, text_fn=_text)
    a = _artifact(content_type="application/octet-stream", is_machine_readable=False)
    findings = rules.check_content(_index(artifacts=[a]), None, 100)
    assert findings == []
    assert called == {"json": 0, "text": 0}


def test_module_exports_all_checks():
    expected = {
        "check_content",
        "check_coverage_gaps",
        "check_excluded_paths",
        "check_missing_files",
        "check_partial_artifacts",
        "check_producer_compliance",
        "check_run_status",
        "check_singleton_limitations",
        "check_unresolved_paths",
    }
    assert set(rules.__all__) == expected
