# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage-focused unit tests for slice_replay.checks.

Hermetic: models are constructed directly (no harvest pipeline, no network,
no git). All filesystem access is confined to pytest tmp_path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from operations_center.behavior_calibration.models import ArtifactIndexSummary
from operations_center.fixture_harvesting.models import FixtureArtifact, FixturePack
from operations_center.slice_replay import checks
from operations_center.slice_replay.checks import (
    CHECK_REGISTRY,
    _is_json_type,
    _is_text_type,
)
from operations_center.slice_replay.models import (
    SliceReplayCheck,
    SliceReplayProfile,
    SliceReplayRequest,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _summary() -> ArtifactIndexSummary:
    return ArtifactIndexSummary(
        total_artifacts=1,
        by_kind={},
        by_location={},
        by_status={},
        singleton_count=0,
        partial_count=0,
        excluded_path_count=0,
        unresolved_path_count=0,
        missing_file_count=0,
        machine_readable_count=0,
        warnings_count=0,
        errors_count=0,
        manifest_limitations=[],
    )


def _check(check_type: str = "x", **kw) -> SliceReplayCheck:
    base = {
        "check_type": check_type,
        "description": "desc",
        "fixture_artifact_ids": ["aid-1"],
    }
    base.update(kw)
    return SliceReplayCheck(**base)


def _artifact(**kw) -> FixtureArtifact:
    base = {
        "source_artifact_id": "src:aid",
        "artifact_kind": "stage_report",
        "source_stage": "TopicSelectionStage",
        "location": "run_root",
        "path_role": "primary",
        "source_path": "run/topic.json",
        "fixture_relative_path": "topic.json",
        "content_type": "application/json",
        "copied": True,
    }
    base.update(kw)
    return FixtureArtifact(**base)


def _pack(**kw) -> FixturePack:
    base = {
        "fixture_pack_id": "repo__run__profile__ts",
        "source_repo_id": "repo",
        "source_run_id": "run",
        "source_audit_type": "audit_type_1",
        "source_manifest_path": "/abs/manifest.json",
        "source_index_summary": _summary(),
        "harvest_profile": "full_manifest_snapshot",
    }
    base.update(kw)
    return FixturePack(**base)


def _request(**kw) -> SliceReplayRequest:
    base = {
        "fixture_pack_path": Path("/tmp/fp.json"),
        "replay_profile": SliceReplayProfile.FIXTURE_INTEGRITY,
    }
    base.update(kw)
    return SliceReplayRequest(**base)


def _write_artifact(pack_dir: Path, rel: str, data: bytes) -> Path:
    f = pack_dir / "artifacts" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


# ---------------------------------------------------------------------------
# helper predicates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ct,expected",
    [
        ("application/json", True),
        ("application/x-ndjson", True),
        ("application/json; charset=utf-8", True),
        ("APPLICATION/JSON", True),
        ("text/plain", False),
        ("application/octet-stream", False),
    ],
)
def test_is_json_type(ct, expected):
    assert _is_json_type(ct) is expected


@pytest.mark.parametrize(
    "ct,expected",
    [
        ("application/json", True),
        ("text/plain", True),
        ("text/markdown", True),  # startswith text/
        ("text/csv; charset=utf-8", True),
        ("application/yaml", True),
        ("application/octet-stream", False),
        ("image/png", False),
    ],
)
def test_is_text_type(ct, expected):
    assert _is_text_type(ct) is expected


# ---------------------------------------------------------------------------
# check_fixture_pack_loads
# ---------------------------------------------------------------------------


def test_fixture_pack_loads_passes(tmp_path):
    res = checks.check_fixture_pack_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "passed"
    assert res.check_id == _check().check_id or res.check_id  # has an id
    assert res.fixture_artifact_ids == ["aid-1"]


# ---------------------------------------------------------------------------
# check_copied_file_exists
# ---------------------------------------------------------------------------


def test_copied_file_exists_no_artifact(tmp_path):
    res = checks.check_copied_file_exists(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_copied_file_exists_metadata_only(tmp_path):
    art = _artifact(copied=False, copy_error="missing file")
    res = checks.check_copied_file_exists(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"
    assert "metadata-only" in res.summary


def test_copied_file_exists_empty_relative_path(tmp_path):
    art = _artifact(copied=True, fixture_relative_path="")
    res = checks.check_copied_file_exists(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "failed"


def test_copied_file_exists_present(tmp_path):
    _write_artifact(tmp_path, "topic.json", b"{}")
    res = checks.check_copied_file_exists(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "passed"


def test_copied_file_exists_missing(tmp_path):
    res = checks.check_copied_file_exists(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "failed"
    assert "missing" in res.summary.lower()


# ---------------------------------------------------------------------------
# check_metadata_only_reason_present
# ---------------------------------------------------------------------------


def test_metadata_only_reason_no_artifact(tmp_path):
    res = checks.check_metadata_only_reason_present(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_metadata_only_reason_copied(tmp_path):
    res = checks.check_metadata_only_reason_present(
        _check(), _pack(), tmp_path, _artifact(copied=True), _request()
    )
    assert res.status == "skipped"


def test_metadata_only_reason_has_copy_error(tmp_path):
    art = _artifact(copied=False, copy_error="oversized")
    res = checks.check_metadata_only_reason_present(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "passed"
    assert "oversized" in res.summary


def test_metadata_only_reason_has_limitations(tmp_path):
    art = _artifact(copied=False, copy_error="", limitations=["partial_run"])
    res = checks.check_metadata_only_reason_present(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "passed"
    assert "limitations" in res.summary


def test_metadata_only_reason_missing(tmp_path):
    art = _artifact(copied=False, copy_error="", limitations=[])
    res = checks.check_metadata_only_reason_present(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "failed"


# ---------------------------------------------------------------------------
# check_source_manifest_loads
# ---------------------------------------------------------------------------


def test_source_manifest_not_found(tmp_path):
    res = checks.check_source_manifest_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "failed"
    assert "not found" in res.summary


def test_source_manifest_invalid_json(tmp_path):
    (tmp_path / "source_manifest.json").write_text("not json", encoding="utf-8")
    res = checks.check_source_manifest_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "failed"
    assert "not valid JSON" in res.summary


def test_source_manifest_os_error(tmp_path, monkeypatch):
    (tmp_path / "source_manifest.json").write_text("{}", encoding="utf-8")

    def boom(self, *a, **k):
        raise OSError("io fail")

    monkeypatch.setattr(Path, "read_text", boom)
    res = checks.check_source_manifest_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "error"
    assert "io fail" in res.error


def test_source_manifest_missing_fields(tmp_path):
    (tmp_path / "source_manifest.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    res = checks.check_source_manifest_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "failed"
    assert "missing expected fields" in res.summary


def test_source_manifest_valid(tmp_path):
    (tmp_path / "source_manifest.json").write_text(
        json.dumps({"repo_id": "r", "artifacts": []}), encoding="utf-8"
    )
    res = checks.check_source_manifest_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "passed"


# ---------------------------------------------------------------------------
# check_source_index_summary_loads
# ---------------------------------------------------------------------------


def test_source_index_summary_not_found(tmp_path):
    res = checks.check_source_index_summary_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "failed"


def test_source_index_summary_invalid_json(tmp_path):
    (tmp_path / "source_index_summary.json").write_text("{bad", encoding="utf-8")
    res = checks.check_source_index_summary_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "failed"
    assert "not valid JSON" in res.summary


def test_source_index_summary_os_error(tmp_path, monkeypatch):
    (tmp_path / "source_index_summary.json").write_text("{}", encoding="utf-8")

    def boom(self, *a, **k):
        raise OSError("io fail")

    monkeypatch.setattr(Path, "read_text", boom)
    res = checks.check_source_index_summary_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "error"


def test_source_index_summary_missing_field(tmp_path):
    (tmp_path / "source_index_summary.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    res = checks.check_source_index_summary_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "failed"
    assert "total_artifacts" in res.summary


def test_source_index_summary_valid(tmp_path):
    (tmp_path / "source_index_summary.json").write_text(
        json.dumps({"total_artifacts": 5}), encoding="utf-8"
    )
    res = checks.check_source_index_summary_loads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "passed"
    assert "total_artifacts=5" in res.summary


# ---------------------------------------------------------------------------
# check_json_artifact_reads
# ---------------------------------------------------------------------------


def test_json_artifact_no_artifact(tmp_path):
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_json_artifact_not_copied(tmp_path):
    art = _artifact(copied=False, fixture_relative_path=None)
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"


def test_json_artifact_not_json_type(tmp_path):
    art = _artifact(content_type="text/plain")
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"
    assert "Not a JSON" in res.summary


def test_json_artifact_file_missing(tmp_path):
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "failed"


def test_json_artifact_os_error(tmp_path, monkeypatch):
    _write_artifact(tmp_path, "topic.json", b"{}")

    def boom(self, *a, **k):
        raise OSError("read fail")

    monkeypatch.setattr(Path, "read_bytes", boom)
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "error"


def test_json_artifact_invalid_json(tmp_path):
    _write_artifact(tmp_path, "topic.json", b"not json")
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "failed"
    assert "not valid JSON" in res.summary


def test_json_artifact_valid(tmp_path):
    _write_artifact(tmp_path, "topic.json", json.dumps({"a": 1}).encode())
    res = checks.check_json_artifact_reads(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "passed"


def test_json_artifact_truncation_branch(tmp_path):
    # Valid JSON larger than the limit gets truncated -> becomes invalid JSON.
    payload = json.dumps({"key": "x" * 100}).encode()
    _write_artifact(tmp_path, "topic.json", payload)
    res = checks.check_json_artifact_reads(
        _check(), _pack(), tmp_path, _artifact(), _request(max_artifact_bytes=5)
    )
    assert res.status == "failed"


# ---------------------------------------------------------------------------
# check_text_artifact_reads
# ---------------------------------------------------------------------------


def test_text_artifact_no_artifact(tmp_path):
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_text_artifact_not_copied(tmp_path):
    art = _artifact(copied=False, fixture_relative_path=None, content_type="text/plain")
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"


def test_text_artifact_not_text_type(tmp_path):
    art = _artifact(content_type="application/octet-stream")
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"
    assert "Not a text" in res.summary


def test_text_artifact_file_missing(tmp_path):
    art = _artifact(content_type="text/plain")
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "failed"


def test_text_artifact_os_error(tmp_path, monkeypatch):
    art = _artifact(content_type="text/plain")
    _write_artifact(tmp_path, "topic.json", b"hello")

    def boom(self, *a, **k):
        raise OSError("read fail")

    monkeypatch.setattr(Path, "read_bytes", boom)
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "error"


def test_text_artifact_invalid_utf8(tmp_path):
    art = _artifact(content_type="text/plain")
    _write_artifact(tmp_path, "topic.json", b"\xff\xfe\xff")
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "failed"
    assert "UTF-8" in res.summary


def test_text_artifact_valid(tmp_path):
    art = _artifact(content_type="text/plain")
    _write_artifact(tmp_path, "topic.json", b"hello world")
    res = checks.check_text_artifact_reads(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "passed"


def test_text_artifact_truncation_valid(tmp_path):
    # Truncating multibyte sequence still valid here because ASCII payload.
    art = _artifact(content_type="text/plain")
    _write_artifact(tmp_path, "topic.json", b"abcdefghij")
    res = checks.check_text_artifact_reads(
        _check(), _pack(), tmp_path, art, _request(max_artifact_bytes=3)
    )
    assert res.status == "passed"


# ---------------------------------------------------------------------------
# check_failure_limitation_present
# ---------------------------------------------------------------------------


def test_failure_limitation_artifact_has_failure_lims(tmp_path):
    art = _artifact(limitations=["partial_run", "other"])
    res = checks.check_failure_limitation_present(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "passed"
    assert "partial_run" in res.summary


def test_failure_limitation_artifact_not_copied(tmp_path):
    art = _artifact(copied=False, copy_error="missing", limitations=[])
    res = checks.check_failure_limitation_present(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "passed"
    assert "not copied" in res.summary


def test_failure_limitation_pack_level(tmp_path):
    # Artifact copied with no failure lims -> falls through to pack-level.
    art = _artifact(copied=True, limitations=[])
    pack = _pack(limitations=["failed_run"])
    res = checks.check_failure_limitation_present(_check(), pack, tmp_path, art, _request())
    assert res.status == "passed"
    assert "failed_run" in res.summary


def test_failure_limitation_none(tmp_path):
    art = _artifact(copied=True, limitations=[])
    pack = _pack(limitations=[])
    res = checks.check_failure_limitation_present(_check(), pack, tmp_path, art, _request())
    assert res.status == "failed"


def test_failure_limitation_no_artifact_pack_has(tmp_path):
    pack = _pack(limitations=["missing_downstream_artifacts"])
    res = checks.check_failure_limitation_present(_check(), pack, tmp_path, None, _request())
    assert res.status == "passed"


# ---------------------------------------------------------------------------
# check_checksum_matches_if_available
# ---------------------------------------------------------------------------


def test_checksum_no_artifact(tmp_path):
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_checksum_not_copied(tmp_path):
    art = _artifact(copied=False, fixture_relative_path=None)
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"


def test_checksum_none_recorded(tmp_path):
    art = _artifact(checksum=None)
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "skipped"
    assert "No checksum" in res.summary


def test_checksum_file_missing(tmp_path):
    art = _artifact(checksum="sha256:deadbeef")
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "failed"
    assert "not found" in res.summary


def test_checksum_os_error(tmp_path, monkeypatch):
    data = b"payload"
    _write_artifact(tmp_path, "topic.json", data)
    art = _artifact(checksum="sha256:whatever")

    def boom(self, *a, **k):
        raise OSError("open fail")

    monkeypatch.setattr(Path, "open", boom)
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "error"


def test_checksum_match(tmp_path):
    data = b"payload-bytes"
    _write_artifact(tmp_path, "topic.json", data)
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    art = _artifact(checksum=digest)
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "passed"


def test_checksum_mismatch(tmp_path):
    _write_artifact(tmp_path, "topic.json", b"payload")
    art = _artifact(checksum="sha256:0000")
    res = checks.check_checksum_matches_if_available(_check(), _pack(), tmp_path, art, _request())
    assert res.status == "failed"
    assert "mismatch" in res.summary.lower()


# ---------------------------------------------------------------------------
# check_artifact_kind_matches
# ---------------------------------------------------------------------------


def test_artifact_kind_no_artifact(tmp_path):
    res = checks.check_artifact_kind_matches(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_artifact_kind_no_filter(tmp_path):
    res = checks.check_artifact_kind_matches(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "skipped"


def test_artifact_kind_match(tmp_path):
    res = checks.check_artifact_kind_matches(
        _check(), _pack(), tmp_path, _artifact(), _request(artifact_kind="stage_report")
    )
    assert res.status == "passed"


def test_artifact_kind_mismatch(tmp_path):
    res = checks.check_artifact_kind_matches(
        _check(), _pack(), tmp_path, _artifact(), _request(artifact_kind="other")
    )
    assert res.status == "failed"
    assert "mismatch" in res.summary


# ---------------------------------------------------------------------------
# check_source_stage_matches
# ---------------------------------------------------------------------------


def test_source_stage_no_artifact(tmp_path):
    res = checks.check_source_stage_matches(_check(), _pack(), tmp_path, None, _request())
    assert res.status == "skipped"


def test_source_stage_no_filter(tmp_path):
    res = checks.check_source_stage_matches(_check(), _pack(), tmp_path, _artifact(), _request())
    assert res.status == "skipped"


def test_source_stage_match(tmp_path):
    res = checks.check_source_stage_matches(
        _check(),
        _pack(),
        tmp_path,
        _artifact(),
        _request(source_stage="TopicSelectionStage"),
    )
    assert res.status == "passed"


def test_source_stage_mismatch(tmp_path):
    res = checks.check_source_stage_matches(
        _check(), _pack(), tmp_path, _artifact(), _request(source_stage="OtherStage")
    )
    assert res.status == "failed"
    assert "mismatch" in res.summary


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_maps_to_callables():
    assert set(CHECK_REGISTRY) == {
        "fixture_pack_loads",
        "copied_file_exists",
        "metadata_only_reason_present",
        "source_manifest_loads",
        "source_index_summary_loads",
        "json_artifact_reads",
        "text_artifact_reads",
        "failure_limitation_present",
        "checksum_matches_if_available",
        "artifact_kind_matches",
        "source_stage_matches",
    }
    for fn in CHECK_REGISTRY.values():
        assert callable(fn)


def test_registry_entries_point_to_module_functions():
    assert CHECK_REGISTRY["fixture_pack_loads"] is checks.check_fixture_pack_loads
    assert CHECK_REGISTRY["source_stage_matches"] is checks.check_source_stage_matches
