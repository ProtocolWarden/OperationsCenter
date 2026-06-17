# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage-complement tests for the multi-run artifact index.

These complement ``test_multi_run.py`` by exercising the internal helpers
(``_parse_iso``, ``_coerce_status``, ``_peek_manifest_metadata``,
``_build_one``) and branches not already covered: symlink following,
raced-away manifests, the ManifestInvalid metadata-peek fallback,
finalized_at sorting, and the secondary lookup/iter accessors.
"""

from __future__ import annotations

import datetime as _dt
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.artifact_index import multi_run as mr
from operations_center.artifact_index.errors import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
)
from operations_center.artifact_index.models import IndexedArtifact
from operations_center.artifact_index.multi_run import (
    IndexedRun,
    MultiRunArtifactIndex,
    _build_one,
    _coerce_status,
    _parse_iso,
    _peek_manifest_metadata,
    build_multi_run_index,
    discover_manifest_files,
)
from operations_center.audit_contracts.vocabulary import ManifestStatus, RunStatus

from tests.unit.artifact_index.conftest import _BASE_ENTRY, _make_manifest_payload  # type: ignore


def _write_bucket(root: Path, *, run_id: str, payload_factory=_make_manifest_payload) -> Path:
    bucket = root / "tools" / "audit" / "report" / "audit_type_1" / f"Bucket_{run_id}"
    bucket.mkdir(parents=True, exist_ok=True)
    payload = payload_factory(run_id=run_id, run_root=str(bucket.relative_to(root)))
    (bucket / "artifact_manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return bucket


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    def test_parses_z_suffix(self) -> None:
        got = _parse_iso("2026-04-26T12:00:00Z")
        assert got == datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)

    def test_parses_offset(self) -> None:
        got = _parse_iso("2026-04-26T12:00:00+02:00")
        assert got is not None
        assert got.utcoffset() == _dt.timedelta(hours=2)

    def test_non_string_returns_none(self) -> None:
        assert _parse_iso(None) is None
        assert _parse_iso(12345) is None
        assert _parse_iso(["2026-04-26"]) is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_iso("not-a-date") is None
        assert _parse_iso("") is None


# ---------------------------------------------------------------------------
# _coerce_status
# ---------------------------------------------------------------------------


class TestCoerceStatus:
    def test_valid_run_status(self) -> None:
        assert _coerce_status("running", RunStatus) is RunStatus.RUNNING

    def test_valid_manifest_status(self) -> None:
        assert _coerce_status("partial", ManifestStatus) is ManifestStatus.PARTIAL

    def test_none_returns_none(self) -> None:
        assert _coerce_status(None, RunStatus) is None

    def test_invalid_value_returns_none(self) -> None:
        assert _coerce_status("nonsense", RunStatus) is None

    def test_wrong_type_returns_none(self) -> None:
        # Unhashable / wrong type triggers TypeError inside enum lookup.
        assert _coerce_status(["x"], RunStatus) is None


# ---------------------------------------------------------------------------
# _peek_manifest_metadata
# ---------------------------------------------------------------------------


class TestPeekManifestMetadata:
    def test_reads_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"run_id": "x"}), encoding="utf-8")
        assert _peek_manifest_metadata(p) == {"run_id": "x"}

    def test_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text("{not json", encoding="utf-8")
        assert _peek_manifest_metadata(p) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _peek_manifest_metadata(tmp_path / "missing.json") is None


# ---------------------------------------------------------------------------
# IndexedRun.loaded property
# ---------------------------------------------------------------------------


class TestIndexedRunLoaded:
    def _run(self, *, index, load_error) -> IndexedRun:
        return IndexedRun(
            manifest_path=Path("/tmp/x.json"),
            run_id="r",
            repo_id="repo",
            audit_type="audit_type_1",
            producer="p",
            run_status=None,
            manifest_status=None,
            finalized_at=None,
            artifact_count=0,
            is_partial=False,
            load_error=load_error,
            index=index,
        )

    def test_loaded_true(self) -> None:
        sentinel = object()
        assert self._run(index=sentinel, load_error=None).loaded is True

    def test_loaded_false_when_no_index(self) -> None:
        assert self._run(index=None, load_error=None).loaded is False

    def test_loaded_false_when_error_present(self) -> None:
        sentinel = object()
        assert self._run(index=sentinel, load_error="boom").loaded is False


# ---------------------------------------------------------------------------
# _build_one branches
# ---------------------------------------------------------------------------


class TestBuildOneFailureBranches:
    def test_manifest_not_found_raced_away(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_load(_path):
            raise mr.ManifestNotFoundError("gone")

        monkeypatch.setattr(mr, "load_artifact_manifest", fake_load)
        run = _build_one(Path("/tmp/does-not-matter.json"), repo_root=None)
        assert run.index is None
        assert run.load_error == "gone"
        assert run.run_id == ""
        assert run.repo_id == ""
        assert run.artifact_count == 0
        assert run.is_partial is False
        assert run.loaded is False

    def test_manifest_invalid_peeks_metadata(self, tmp_path: Path, monkeypatch) -> None:
        # Write a file whose JSON is parseable (peek succeeds) but treat it as
        # invalid for full validation by stubbing the loader.
        p = tmp_path / "artifact_manifest.json"
        p.write_text(
            json.dumps(
                {
                    "run_id": "peeked_run",
                    "repo_id": "peeked_repo",
                    "audit_type": "peeked_audit",
                    "producer": "peeked_producer",
                    "run_status": "running",
                    "manifest_status": "partial",
                    "finalized_at": "2026-04-26T12:01:00Z",
                    "artifacts": [{}, {}, {}],
                }
            ),
            encoding="utf-8",
        )

        def fake_load(_path):
            raise mr.ManifestInvalidError("validation failed")

        monkeypatch.setattr(mr, "load_artifact_manifest", fake_load)
        run = _build_one(p, repo_root=None)
        assert run.index is None
        assert run.load_error == "validation failed"
        assert run.run_id == "peeked_run"
        assert run.repo_id == "peeked_repo"
        assert run.audit_type == "peeked_audit"
        assert run.producer == "peeked_producer"
        assert run.run_status is RunStatus.RUNNING
        assert run.manifest_status is ManifestStatus.PARTIAL
        assert run.finalized_at == datetime(2026, 4, 26, 12, 1, 0, tzinfo=timezone.utc)
        assert run.artifact_count == 3
        assert run.is_partial is True

    def test_manifest_invalid_with_unpeekable_metadata(self, tmp_path: Path, monkeypatch) -> None:
        # Corrupt JSON so the peek fallback returns None -> empty defaults.
        p = tmp_path / "artifact_manifest.json"
        p.write_text("{not json", encoding="utf-8")

        def fake_load(_path):
            raise mr.ManifestInvalidError("validation failed")

        monkeypatch.setattr(mr, "load_artifact_manifest", fake_load)
        run = _build_one(p, repo_root=None)
        assert run.run_id == ""
        assert run.repo_id == ""
        assert run.audit_type == ""
        assert run.producer == ""
        assert run.run_status is None
        assert run.manifest_status is None
        assert run.finalized_at is None
        assert run.artifact_count == 0
        assert run.is_partial is False
        assert run.load_error == "validation failed"


# ---------------------------------------------------------------------------
# discover_manifest_files extra branches
# ---------------------------------------------------------------------------


class TestDiscoverExtras:
    def test_root_is_file_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "afile.json"
        f.write_text("{}")
        assert discover_manifest_files(f) == []

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "artifact_manifest.json").write_text("{}")
        assert discover_manifest_files(tmp_path) == []

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        bucket = tmp_path / "b"
        bucket.mkdir()
        m = bucket / "artifact_manifest.json"
        m.write_text("{}")
        assert discover_manifest_files(str(tmp_path)) == [m]

    def test_follow_symlinks_true_traverses_linked_dir(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        (real / "artifact_manifest.json").write_text("{}")
        link_parent = tmp_path / "linkroot"
        link_parent.mkdir()
        link = link_parent / "linked"
        link.symlink_to(real, target_is_directory=True)
        found = discover_manifest_files(link_parent, follow_symlinks=True)
        assert (link / "artifact_manifest.json") in found


# ---------------------------------------------------------------------------
# build_multi_run_index extras: skipped, sorting
# ---------------------------------------------------------------------------


class TestBuildExtras:
    def test_skipped_when_manifest_disappears(self, tmp_path: Path, monkeypatch) -> None:
        bucket = _write_bucket(tmp_path, run_id="r1")
        mp = bucket / "artifact_manifest.json"
        monkeypatch.setattr(mr, "discover_manifest_files", lambda *a, **k: [mp])
        # Delete the file after discovery so is_file() is False.
        mp.unlink()
        idx = build_multi_run_index(tmp_path)
        assert idx.runs == []
        assert len(idx.skipped_paths) == 1
        skipped_path, reason = idx.skipped_paths[0]
        assert skipped_path == mp
        assert "disappeared" in reason

    def test_sorted_most_recent_first(self, tmp_path: Path) -> None:
        def fac_old(*, run_id, run_root):
            return _make_manifest_payload(
                run_id=run_id, run_root=run_root, finalized_at="2026-01-01T00:00:00Z"
            )

        def fac_new(*, run_id, run_root):
            return _make_manifest_payload(
                run_id=run_id, run_root=run_root, finalized_at="2026-12-31T00:00:00Z"
            )

        _write_bucket(tmp_path, run_id="old_run", payload_factory=fac_old)
        _write_bucket(tmp_path, run_id="new_run", payload_factory=fac_new)
        idx = build_multi_run_index(tmp_path)
        assert [r.run_id for r in idx.runs] == ["new_run", "old_run"]

    def test_str_search_root_resolved(self, tmp_path: Path) -> None:
        _write_bucket(tmp_path, run_id="r1")
        idx = build_multi_run_index(str(tmp_path))
        assert idx.search_root == tmp_path.resolve()

    def test_filter_keeps_when_metadata_missing(self, tmp_path: Path) -> None:
        # A failed (raced-away) run with empty repo_id/audit_type must survive
        # filters because the filter only drops runs with truthy mismatching
        # metadata.
        bad = tmp_path / "broken"
        bad.mkdir()
        (bad / "artifact_manifest.json").write_text("{not json", encoding="utf-8")
        idx = build_multi_run_index(tmp_path, repo_filter="something", audit_type_filter="other")
        assert len(idx.runs) == 1
        assert idx.runs[0].repo_id == ""


# ---------------------------------------------------------------------------
# MultiRunArtifactIndex secondary accessors
# ---------------------------------------------------------------------------


class TestAccessors:
    def test_by_repo_and_by_audit_type(self, tmp_path: Path) -> None:
        _write_bucket(tmp_path, run_id="r1")
        idx = build_multi_run_index(tmp_path)
        assert [r.run_id for r in idx.by_repo("example_managed_repo")] == ["r1"]
        assert idx.by_repo("nope") == []
        assert [r.run_id for r in idx.by_audit_type("audit_type_1")] == ["r1"]
        assert idx.by_audit_type("nope") == []

    def test_get_run_missing_returns_none(self, tmp_path: Path) -> None:
        idx = build_multi_run_index(tmp_path)
        assert idx.get_run("nothing") is None

    def test_iter_artifacts_federates(self, tmp_path: Path) -> None:
        _write_bucket(tmp_path, run_id="r1")
        _write_bucket(tmp_path, run_id="r2")
        idx = build_multi_run_index(tmp_path)
        artifacts = list(idx.iter_artifacts())
        assert len(artifacts) == 2
        assert all(isinstance(a, IndexedArtifact) for a in artifacts)

    def test_iter_artifacts_skips_failed_runs(self, tmp_path: Path) -> None:
        _write_bucket(tmp_path, run_id="r1")
        bad = tmp_path / "tools" / "audit" / "report" / "audit_type_1" / "broken"
        bad.mkdir(parents=True)
        (bad / "artifact_manifest.json").write_text("{not json", encoding="utf-8")
        idx = build_multi_run_index(tmp_path)
        # One good, one failed -> only good run's artifacts yielded.
        assert len(list(idx.iter_artifacts())) == 1
        assert len(idx.failed_runs) == 1

    def test_query_returns_empty_when_no_loaded_runs(self, tmp_path: Path) -> None:
        idx = build_multi_run_index(tmp_path)
        assert idx.query() == []


# ---------------------------------------------------------------------------
# resolve: get_run None vs index None
# ---------------------------------------------------------------------------


class TestResolveBranches:
    def test_resolve_unknown_run_message(self, tmp_path: Path) -> None:
        idx = MultiRunArtifactIndex(search_root=tmp_path, runs=[])
        with pytest.raises(ArtifactNotFoundError, match="search_root="):
            idx.resolve("missing", "art")

    def test_resolve_failed_index_none(self, tmp_path: Path) -> None:
        run = IndexedRun(
            manifest_path=tmp_path / "x.json",
            run_id="r1",
            repo_id="repo",
            audit_type="audit_type_1",
            producer="p",
            run_status=None,
            manifest_status=None,
            finalized_at=None,
            artifact_count=0,
            is_partial=False,
            load_error="bad manifest",
            index=None,
        )
        idx = MultiRunArtifactIndex(search_root=tmp_path, runs=[run])
        with pytest.raises(ArtifactPathUnresolvableError, match="failed to load: bad manifest"):
            idx.resolve("r1", "art")

    def test_resolve_recheck_false_no_stat(self, tmp_path: Path) -> None:
        _write_bucket(tmp_path, run_id="r1")
        idx = build_multi_run_index(tmp_path, repo_root=tmp_path)
        # Artifact file not materialized; recheck disabled returns the path.
        path = idx.resolve("r1", _BASE_ENTRY["artifact_id"], recheck_exists=False)
        assert isinstance(path, Path)
        assert not path.exists()
