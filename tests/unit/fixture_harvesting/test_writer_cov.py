# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from operations_center.fixture_harvesting import writer
from operations_center.fixture_harvesting.errors import (
    FixturePackWriteError,
    UnsafePathError,
)
from operations_center.fixture_harvesting.models import (
    CopyPolicy,
    FixtureSelection,
    HarvestProfile,
    HarvestRequest,
    SelectedArtifact,
)
from operations_center.fixture_harvesting.writer import (
    _assert_safe_destination,
    _build_finding_references,
    _compute_checksum,
    _is_text_content_type,
    _safe_filename,
    _write_fixture_artifact,
    write_fixture_pack,
)

# ---------------------------------------------------------------------------
# Lightweight enum-like value holders (duck-typed .value)
# ---------------------------------------------------------------------------


class _V:
    """A tiny stand-in for an enum member exposing .value."""

    def __init__(self, value: str) -> None:
        self.value = value


def _make_artifact(
    *,
    artifact_id: str = "repo:audit:Stage:kind",
    path: str = "run/output.json",
    resolved_path: Path | None = None,
    exists_on_disk: bool | None = True,
    status: str = "present",
    content_type: str = "application/json",
    checksum: str | None = "sha256:orig",
    size_bytes: int | None = 123,
    limitations: list[Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact_id,
        artifact_kind="stage_report",
        source_stage="Stage",
        location=_V("run_root"),
        path_role=_V("primary"),
        path=path,
        content_type=content_type,
        checksum=checksum,
        size_bytes=size_bytes,
        resolved_path=resolved_path,
        exists_on_disk=exists_on_disk,
        status=_V(status),
        limitations=limitations if limitations is not None else [_V("partial_run")],
    )


def _write_src(tmp_path: Path, name: str = "src.json", data: bytes = b'{"x": 1}') -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------


def test_safe_filename_replaces_unsafe_chars() -> None:
    assert _safe_filename("a/b:c d") == "a_b_c_d"


def test_safe_filename_truncates_to_200() -> None:
    result = _safe_filename("x" * 500)
    assert len(result) == 200


def test_safe_filename_empty_falls_back_to_artifact() -> None:
    # An all-unsafe string of length 0 -> "" -> "artifact"
    assert _safe_filename("") == "artifact"


def test_safe_filename_keeps_allowed_chars() -> None:
    assert _safe_filename("Good-name_1.json") == "Good-name_1.json"


# ---------------------------------------------------------------------------
# _is_text_content_type
# ---------------------------------------------------------------------------


def test_is_text_content_type_known_json() -> None:
    assert _is_text_content_type("application/json") is True


def test_is_text_content_type_text_prefix() -> None:
    assert _is_text_content_type("text/markdown") is True


def test_is_text_content_type_strips_params_and_case() -> None:
    assert _is_text_content_type("Application/JSON; charset=utf-8") is True


def test_is_text_content_type_binary_false() -> None:
    assert _is_text_content_type("application/octet-stream") is False


# ---------------------------------------------------------------------------
# _compute_checksum
# ---------------------------------------------------------------------------


def test_compute_checksum_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    p = _write_src(tmp_path, data=b"hello world")
    expected = "sha256:" + hashlib.sha256(b"hello world").hexdigest()
    assert _compute_checksum(p) == expected


def test_compute_checksum_large_file_chunks(tmp_path: Path) -> None:
    import hashlib

    data = b"a" * (65536 * 2 + 5)
    p = _write_src(tmp_path, data=data)
    expected = "sha256:" + hashlib.sha256(data).hexdigest()
    assert _compute_checksum(p) == expected


# ---------------------------------------------------------------------------
# _assert_safe_destination
# ---------------------------------------------------------------------------


def test_assert_safe_destination_inside_ok(tmp_path: Path) -> None:
    dest = tmp_path / "artifacts" / "a.json"
    # A destination inside the artifacts dir is accepted — returns without raising.
    assert _assert_safe_destination(dest, tmp_path / "artifacts") is None


def test_assert_safe_destination_escape_raises(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    dest = tmp_path / "elsewhere" / "a.json"
    with pytest.raises(UnsafePathError):
        _assert_safe_destination(dest, artifacts_dir)


# ---------------------------------------------------------------------------
# _write_fixture_artifact — guard branches (meta-only)
# ---------------------------------------------------------------------------


def test_write_artifact_unresolved_path(tmp_path: Path) -> None:
    art = _make_artifact(resolved_path=None)
    fa, written = _write_fixture_artifact(art, tmp_path, CopyPolicy(), 0)
    assert written == 0
    assert fa.copied is False
    assert fa.copy_error == "path unresolvable"
    assert fa.checksum == "sha256:orig"
    assert fa.size_bytes == 123
    assert fa.fixture_relative_path is None


def test_write_artifact_missing_excluded_by_policy(tmp_path: Path) -> None:
    art = _make_artifact(resolved_path=tmp_path / "x.json", exists_on_disk=False)
    policy = CopyPolicy(include_missing_files=False)
    fa, written = _write_fixture_artifact(art, tmp_path, policy, 0)
    assert written == 0
    assert fa.copy_error == "missing file excluded by copy policy"


def test_write_artifact_missing_included_records_metadata(tmp_path: Path) -> None:
    art = _make_artifact(resolved_path=tmp_path / "x.json", status="missing")
    policy = CopyPolicy(include_missing_files=True)
    fa, written = _write_fixture_artifact(art, tmp_path, policy, 0)
    assert written == 0
    assert fa.copy_error == "source file does not exist on disk"


def test_write_artifact_resolved_but_not_on_disk(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.json"
    art = _make_artifact(resolved_path=missing, exists_on_disk=True, status="present")
    fa, written = _write_fixture_artifact(art, tmp_path, CopyPolicy(), 0)
    assert written == 0
    assert fa.copy_error == f"source file not found: {missing}"


def test_write_artifact_binary_excluded(tmp_path: Path) -> None:
    src = _write_src(tmp_path)
    art = _make_artifact(resolved_path=src, content_type="application/octet-stream")
    policy = CopyPolicy(include_binary_artifacts=False)
    fa, written = _write_fixture_artifact(art, tmp_path / "artifacts", policy, 0)
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    assert written == 0
    assert "binary content type" in fa.copy_error


def test_write_artifact_content_type_not_allowed(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path)
    art = _make_artifact(resolved_path=src, content_type="application/json")
    policy = CopyPolicy(allowed_content_types=["text/plain"])
    fa, written = _write_fixture_artifact(art, artifacts_dir, policy, 0)
    assert written == 0
    assert "not in allowed list" in fa.copy_error


def test_write_artifact_content_type_in_allowed_list(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path)
    art = _make_artifact(resolved_path=src, content_type="Application/JSON")
    policy = CopyPolicy(allowed_content_types=["application/json"])
    fa, written = _write_fixture_artifact(art, artifacts_dir, policy, 0)
    assert fa.copied is True
    assert written > 0


def test_write_artifact_stat_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path)
    art = _make_artifact(resolved_path=src)

    real_stat = Path.stat

    def _boom(self: Path, *a: Any, **k: Any) -> Any:
        if self == src:
            raise OSError("nope")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _boom)
    fa, written = _write_fixture_artifact(art, artifacts_dir, CopyPolicy(), 0)
    assert written == 0
    assert "cannot stat source file" in fa.copy_error


def test_write_artifact_oversized(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path, data=b"x" * 100)
    art = _make_artifact(resolved_path=src)
    policy = CopyPolicy(max_artifact_bytes=10)
    fa, written = _write_fixture_artifact(art, artifacts_dir, policy, 0)
    assert written == 0
    assert "oversized" in fa.copy_error


def test_write_artifact_total_budget_exceeded(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path, data=b"x" * 100)
    art = _make_artifact(resolved_path=src)
    policy = CopyPolicy(max_total_bytes=50)
    fa, written = _write_fixture_artifact(art, artifacts_dir, policy, 0)
    assert written == 0
    assert fa.copy_error == "max_total_bytes budget exceeded"


def test_write_artifact_copy_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path)
    art = _make_artifact(resolved_path=src)

    def _boom(*a: Any, **k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(writer.shutil, "copy2", _boom)
    fa, written = _write_fixture_artifact(art, artifacts_dir, CopyPolicy(), 0)
    assert written == 0
    assert "copy failed" in fa.copy_error


def test_write_artifact_happy_path_copies(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path, name="thing.json", data=b'{"ok": true}')
    art = _make_artifact(resolved_path=src, path="run/thing.json")
    fa, written = _write_fixture_artifact(art, artifacts_dir, CopyPolicy(), 0)
    assert fa.copied is True
    assert written == len(b'{"ok": true}')
    assert fa.fixture_relative_path is not None
    assert fa.fixture_relative_path.endswith(".json")
    assert fa.checksum.startswith("sha256:")
    assert fa.copy_error == ""
    # file actually copied
    assert (artifacts_dir / fa.fixture_relative_path).read_bytes() == b'{"ok": true}'
    assert fa.limitations == ["partial_run"]


def test_write_artifact_no_suffix_filename(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = _write_src(tmp_path, name="nosuffix", data=b"data")
    art = _make_artifact(resolved_path=src, path="run/nosuffix")
    fa, _ = _write_fixture_artifact(art, artifacts_dir, CopyPolicy(), 0)
    assert fa.copied is True
    assert "." not in fa.fixture_relative_path


# ---------------------------------------------------------------------------
# _build_finding_references
# ---------------------------------------------------------------------------


def test_build_finding_references_none() -> None:
    req = HarvestRequest(index=None, harvest_profile=HarvestProfile.MINIMAL_FAILURE)
    assert _build_finding_references(req) == []


def test_build_finding_references_all() -> None:
    finding = SimpleNamespace(
        finding_id="F1",
        severity=_V("high"),
        category=_V("missing"),
        artifact_ids=["a1", "a2"],
        summary="bad thing",
    )
    req = HarvestRequest(
        index=None,
        harvest_profile=HarvestProfile.MINIMAL_FAILURE,
        findings=[finding],
    )
    refs = _build_finding_references(req)
    assert len(refs) == 1
    assert refs[0].source_finding_id == "F1"
    assert refs[0].severity == "high"
    assert refs[0].category == "missing"
    assert refs[0].artifact_ids == ["a1", "a2"]
    assert refs[0].summary == "bad thing"


def test_build_finding_references_filtered_by_ids() -> None:
    keep = SimpleNamespace(
        finding_id="KEEP",
        severity=_V("low"),
        category=_V("info"),
        artifact_ids=[],
        summary="s",
    )
    drop = SimpleNamespace(
        finding_id="DROP",
        severity=_V("low"),
        category=_V("info"),
        artifact_ids=[],
        summary="s2",
    )
    req = HarvestRequest(
        index=None,
        harvest_profile=HarvestProfile.MINIMAL_FAILURE,
        findings=[keep, drop],
        finding_ids=["KEEP"],
    )
    refs = _build_finding_references(req)
    assert [r.source_finding_id for r in refs] == ["KEEP"]


def test_build_finding_references_string_severity_fallback() -> None:
    # severity/category are plain strings (no .value); finding_id missing
    finding = SimpleNamespace(
        severity="raw_sev",
        category="raw_cat",
        artifact_ids=[],
        summary="x",
    )
    req = HarvestRequest(
        index=None,
        harvest_profile=HarvestProfile.MINIMAL_FAILURE,
        findings=[finding],
    )
    refs = _build_finding_references(req)
    assert refs[0].source_finding_id == ""
    assert refs[0].severity == "raw_sev"
    assert refs[0].category == "raw_cat"


# ---------------------------------------------------------------------------
# write_fixture_pack — integration with mocked _build_index_summary
# ---------------------------------------------------------------------------


def _make_index(tmp_path: Path, manifest_exists: bool = True) -> SimpleNamespace:
    manifest = tmp_path / "manifest.json"
    if manifest_exists:
        manifest.write_text('{"manifest": true}', encoding="utf-8")
    source = SimpleNamespace(
        repo_id="repo1",
        run_id="run1",
        audit_type="audit1",
        manifest_path=str(manifest),
    )
    return SimpleNamespace(
        source=source,
        limitations=[_V("partial_run")],
    )


@pytest.fixture
def _patch_summary(monkeypatch: pytest.MonkeyPatch) -> Any:
    summary = SimpleNamespace(
        model_dump_json=lambda indent=2: '{"summary": true}',
    )
    monkeypatch.setattr(writer, "_build_index_summary", lambda index: summary)
    return summary


def _make_request(**kw: Any) -> HarvestRequest:
    return HarvestRequest(
        index=None,
        harvest_profile=HarvestProfile.MINIMAL_FAILURE,
        **kw,
    )


def test_write_fixture_pack_happy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    # Avoid building a real FixturePack pydantic model (needs real summary):
    captured: dict[str, Any] = {}

    class _FakePack:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

        def model_dump_json(self, indent: int = 2) -> str:
            return '{"pack": true}'

    monkeypatch.setattr(writer, "FixturePack", _FakePack)

    src = _write_src(tmp_path, name="a.json", data=b'{"k":1}')
    art = _make_artifact(resolved_path=src, path="run/a.json")
    selection = FixtureSelection(selected=[SelectedArtifact(artifact=art, rationale="r")])
    index = _make_index(tmp_path)
    req = _make_request(selection_rationale="why", metadata={"m": 1})

    output_dir = tmp_path / "out"
    pack, pack_dir = write_fixture_pack(index, selection, req, output_dir)

    assert isinstance(pack, _FakePack)
    assert pack_dir.parent == output_dir
    assert (pack_dir / "artifacts").is_dir()
    # source manifest copied
    assert (pack_dir / "source_manifest.json").exists()
    # summary written
    assert (pack_dir / "source_index_summary.json").read_text() == '{"summary": true}'
    # fixture_pack.json written
    assert (pack_dir / "fixture_pack.json").read_text() == '{"pack": true}'
    # captured kwargs reflect inputs
    assert captured["source_repo_id"] == "repo1"
    assert captured["selection_rationale"] == "why"
    assert captured["metadata"] == {"m": 1}
    assert captured["limitations"] == ["partial_run"]
    assert len(captured["artifacts"]) == 1
    assert captured["artifacts"][0].copied is True


def test_write_fixture_pack_mkdir_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    def _boom(self: Path, *a: Any, **k: Any) -> Any:
        raise OSError("denied")

    monkeypatch.setattr(Path, "mkdir", _boom)
    index = _make_index(tmp_path)
    selection = FixtureSelection(selected=[])
    req = _make_request()
    with pytest.raises(FixturePackWriteError, match="Cannot create fixture pack directory"):
        write_fixture_pack(index, selection, req, tmp_path / "out")


def test_write_fixture_pack_manifest_missing_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    class _FakePack:
        def __init__(self, **kw: Any) -> None:
            pass

        def model_dump_json(self, indent: int = 2) -> str:
            return "{}"

    monkeypatch.setattr(writer, "FixturePack", _FakePack)
    index = _make_index(tmp_path, manifest_exists=False)
    selection = FixtureSelection(selected=[])
    req = _make_request()
    pack, pack_dir = write_fixture_pack(index, selection, req, tmp_path / "out")
    assert not (pack_dir / "source_manifest.json").exists()


def test_write_fixture_pack_manifest_copy_oserror_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    class _FakePack:
        def __init__(self, **kw: Any) -> None:
            pass

        def model_dump_json(self, indent: int = 2) -> str:
            return "{}"

    monkeypatch.setattr(writer, "FixturePack", _FakePack)

    def _boom(*a: Any, **k: Any) -> Any:
        raise OSError("ro")

    monkeypatch.setattr(writer.shutil, "copy2", _boom)
    index = _make_index(tmp_path)
    selection = FixtureSelection(selected=[])
    req = _make_request()
    # Should not raise despite manifest copy failing
    pack, pack_dir = write_fixture_pack(index, selection, req, tmp_path / "out")
    assert (pack_dir / "fixture_pack.json").exists()


def test_write_fixture_pack_summary_write_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    real_write_text = Path.write_text

    def _selective(self: Path, *a: Any, **k: Any) -> Any:
        if self.name == "source_index_summary.json":
            raise OSError("ro")
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _selective)
    index = _make_index(tmp_path)
    selection = FixtureSelection(selected=[])
    req = _make_request()
    with pytest.raises(FixturePackWriteError, match="source_index_summary.json"):
        write_fixture_pack(index, selection, req, tmp_path / "out")


def test_write_fixture_pack_packjson_write_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    class _FakePack:
        def __init__(self, **kw: Any) -> None:
            pass

        def model_dump_json(self, indent: int = 2) -> str:
            return "{}"

    monkeypatch.setattr(writer, "FixturePack", _FakePack)
    real_write_text = Path.write_text

    def _selective(self: Path, *a: Any, **k: Any) -> Any:
        if self.name == "fixture_pack.json":
            raise OSError("ro")
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _selective)
    index = _make_index(tmp_path)
    selection = FixtureSelection(selected=[])
    req = _make_request()
    with pytest.raises(FixturePackWriteError, match="fixture_pack.json"):
        write_fixture_pack(index, selection, req, tmp_path / "out")


def test_write_fixture_pack_accumulates_total_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_summary: Any
) -> None:
    captured: dict[str, Any] = {}

    class _FakePack:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

        def model_dump_json(self, indent: int = 2) -> str:
            return "{}"

    monkeypatch.setattr(writer, "FixturePack", _FakePack)

    s1 = _write_src(tmp_path, name="one.json", data=b"x" * 40)
    s2 = _write_src(tmp_path, name="two.json", data=b"y" * 40)
    a1 = _make_artifact(artifact_id="repo:a:S:one", resolved_path=s1, path="run/one.json")
    a2 = _make_artifact(artifact_id="repo:a:S:two", resolved_path=s2, path="run/two.json")
    selection = FixtureSelection(
        selected=[
            SelectedArtifact(artifact=a1, rationale="r1"),
            SelectedArtifact(artifact=a2, rationale="r2"),
        ]
    )
    # Budget allows first (40) but not first+second (80) -> second is meta-only
    req = _make_request(copy_policy=CopyPolicy(max_total_bytes=60))
    index = _make_index(tmp_path)
    write_fixture_pack(index, selection, req, tmp_path / "out")

    arts = captured["artifacts"]
    assert arts[0].copied is True
    assert arts[1].copied is False
    assert arts[1].copy_error == "max_total_bytes budget exceeded"
