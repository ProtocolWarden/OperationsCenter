# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage-complement tests for the artifact_index CLI.

These tests target serialization helpers and command branches that the
existing ``test_cli.py`` does not exercise, using fully mocked collaborators
so they are hermetic (no real index build, filesystem walk, or retrieval).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from operations_center.artifact_index import cli as cli_mod
from operations_center.artifact_index.cli import (
    _path_default,
    _run_summary,
    app,
)
from operations_center.artifact_index.errors import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
)

_runner = CliRunner()


# ---------------------------------------------------------------------------
# Lightweight fakes
# ---------------------------------------------------------------------------


def _enum(value: str) -> SimpleNamespace:
    """Mimic an enum member exposing ``.value``."""
    return SimpleNamespace(value=value)


def _make_run(
    *,
    run_id: str = "run_a",
    repo_id: str = "repo1",
    audit_type: str = "audit_type_1",
    producer: str = "producer1",
    manifest_path: Path | None = None,
    run_status: str | None = "completed",
    manifest_status: str | None = "completed",
    finalized_at: datetime | None = None,
    artifact_count: int = 2,
    is_partial: bool = False,
    load_error: str | None = None,
    index: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        repo_id=repo_id,
        audit_type=audit_type,
        producer=producer,
        manifest_path=manifest_path or Path("/tmp/x/artifact_manifest.json"),
        run_status=_enum(run_status) if run_status else None,
        manifest_status=_enum(manifest_status) if manifest_status else None,
        finalized_at=finalized_at,
        artifact_count=artifact_count,
        is_partial=is_partial,
        load_error=load_error,
        index=index,
    )


def _make_artifact(
    *,
    artifact_id: str = "art1",
    artifact_kind: str = "stage_report",
    location: str = "run_root",
    path_role: str = "primary",
    source_stage: str | None = "StageA",
    status: str = "present",
    path: str = "a/b.json",
    resolved_path: Path | None = None,
    exists_on_disk: bool | None = True,
    is_repo_singleton: bool = False,
    size_bytes: int | None = 10,
    content_type: str = "application/json",
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact_id,
        artifact_kind=artifact_kind,
        location=_enum(location),
        path_role=_enum(path_role),
        source_stage=source_stage,
        status=_enum(status),
        path=path,
        resolved_path=resolved_path,
        exists_on_disk=exists_on_disk,
        is_repo_singleton=is_repo_singleton,
        size_bytes=size_bytes,
        content_type=content_type,
    )


def _patch_index(monkeypatch: pytest.MonkeyPatch, idx: object) -> MagicMock:
    """Make build_multi_run_index return ``idx`` regardless of args."""
    m = MagicMock(return_value=idx)
    monkeypatch.setattr(cli_mod, "build_multi_run_index", m)
    return m


# ---------------------------------------------------------------------------
# _path_default
# ---------------------------------------------------------------------------


class TestPathDefault:
    def test_path_serialized_to_str(self) -> None:
        assert _path_default(Path("/a/b")) == str(Path("/a/b"))

    def test_non_path_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="unserializable"):
            _path_default(object())


# ---------------------------------------------------------------------------
# _run_summary
# ---------------------------------------------------------------------------


class TestRunSummary:
    def test_all_present(self) -> None:
        fin = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        run = _make_run(finalized_at=fin)
        s = _run_summary(run)
        assert s["run_id"] == "run_a"
        assert s["run_status"] == "completed"
        assert s["manifest_status"] == "completed"
        assert s["finalized_at"] == fin.isoformat()
        assert s["manifest_path"] == str(run.manifest_path)
        assert s["artifact_count"] == 2

    def test_none_fields(self) -> None:
        run = _make_run(run_status=None, manifest_status=None, finalized_at=None)
        s = _run_summary(run)
        assert s["run_status"] is None
        assert s["manifest_status"] is None
        assert s["finalized_at"] is None


# ---------------------------------------------------------------------------
# index command
# ---------------------------------------------------------------------------


class TestCmdIndexBranches:
    def test_json_empty_runs_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace(search_root=Path("/root"), runs=[], skipped_paths=[])
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["index", "/root", "--json"])
        assert out.exit_code == 2
        # JSON still printed before the exit.
        data = json.loads(out.output)
        assert data["runs"] == []

    def test_json_with_runs_and_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace(
            search_root=Path("/root"),
            runs=[_make_run()],
            skipped_paths=[(Path("/root/bad"), "too deep")],
        )
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["index", "/root", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["runs"][0]["run_id"] == "run_a"
        assert data["skipped"][0] == ["/root/bad", "too deep"]

    def test_table_with_partial_and_load_error_notes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fin = datetime(2026, 1, 2, tzinfo=timezone.utc)
        runs = [
            _make_run(run_id="r1", is_partial=True, load_error="boom", finalized_at=fin),
            _make_run(
                run_id="",
                repo_id="",
                audit_type="",
                run_status=None,
                manifest_status=None,
                finalized_at=None,
            ),
        ]
        idx = SimpleNamespace(search_root=Path("/root"), runs=runs, skipped_paths=[])
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["index", "/root"])
        assert out.exit_code == 0
        assert "Audit Runs" in out.output

    def test_table_empty_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace(search_root=Path("/root"), runs=[], skipped_paths=[])
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["index", "/root"])
        assert out.exit_code == 2
        assert "No audit runs" in out.output


# ---------------------------------------------------------------------------
# index-show command
# ---------------------------------------------------------------------------


class TestCmdIndexShowBranches:
    def _idx_with_run(self, run: SimpleNamespace) -> SimpleNamespace:
        idx = SimpleNamespace()
        idx.find_run_by_prefix = MagicMock(return_value=run)
        return idx

    def test_ambiguous_prefix_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace()
        idx.find_run_by_prefix = MagicMock(side_effect=ValueError("ambiguous"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["index-show", "/root", "abc"])
        assert out.exit_code == 2
        assert "Ambiguous" in out.output

    def test_not_found_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace()
        idx.find_run_by_prefix = MagicMock(side_effect=ArtifactNotFoundError("nope"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["index-show", "/root", "abc"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_load_error_exits_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _make_run(load_error="bad json", index=None)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(app, ["index-show", "/root", "run_a"])
        assert out.exit_code == 3
        assert "failed to load" in out.output

    def test_index_none_without_load_error_exits_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _make_run(load_error=None, index=None)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(app, ["index-show", "/root", "run_a"])
        assert out.exit_code == 3

    def test_filters_applied_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        arts = [
            _make_artifact(artifact_id="keep", artifact_kind="k1", source_stage="s1"),
            _make_artifact(artifact_id="dropkind", artifact_kind="other", source_stage="s1"),
            _make_artifact(artifact_id="dropstage", artifact_kind="k1", source_stage="other"),
            _make_artifact(
                artifact_id="dropexists",
                artifact_kind="k1",
                source_stage="s1",
                exists_on_disk=False,
            ),
            _make_artifact(
                artifact_id="dropnone",
                artifact_kind="k1",
                source_stage=None,
                exists_on_disk=None,
            ),
        ]
        index = SimpleNamespace(artifacts=arts)
        run = _make_run(index=index)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(
            app,
            [
                "index-show",
                "/root",
                "run_a",
                "--kind",
                "k1",
                "--stage",
                "s1",
                "--location",
                "run_root",
            ],
        )
        assert out.exit_code == 0
        assert "keep" in out.output

    def test_missing_only_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        arts = [
            _make_artifact(artifact_id="present_one", status="present"),
            _make_artifact(artifact_id="missing_one", status="missing"),
        ]
        index = SimpleNamespace(artifacts=arts)
        run = _make_run(index=index)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(app, ["index-show", "/root", "run_a", "--missing-only", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        ids = [a["artifact_id"] for a in data["artifacts"]]
        assert ids == ["missing_one"]

    def test_json_output_with_resolved_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        arts = [_make_artifact(resolved_path=Path("/abs/a/b.json"))]
        index = SimpleNamespace(artifacts=arts)
        run = _make_run(index=index)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(app, ["index-show", "/root", "run_a", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        a = data["artifacts"][0]
        assert a["resolved_path"] == str(Path("/abs/a/b.json"))
        assert a["exists_on_disk"] is True

    def test_json_output_resolved_path_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        arts = [_make_artifact(resolved_path=None)]
        index = SimpleNamespace(artifacts=arts)
        run = _make_run(index=index)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(app, ["index-show", "/root", "run_a", "--json"])
        data = json.loads(out.output)
        assert data["artifacts"][0]["resolved_path"] is None

    def test_table_on_disk_glyphs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        arts = [
            _make_artifact(artifact_id="a_yes", exists_on_disk=True, source_stage="s"),
            _make_artifact(artifact_id="a_no", exists_on_disk=False, source_stage=None),
            _make_artifact(artifact_id="a_unknown", exists_on_disk=None),
        ]
        index = SimpleNamespace(artifacts=arts)
        run = _make_run(index=index)
        _patch_index(monkeypatch, self._idx_with_run(run))
        out = _runner.invoke(app, ["index-show", "/root", "run_a"])
        assert out.exit_code == 0


# ---------------------------------------------------------------------------
# get-artifact command
# ---------------------------------------------------------------------------


class TestCmdGetArtifactBranches:
    def _idx(self, run: SimpleNamespace) -> SimpleNamespace:
        idx = SimpleNamespace()
        idx.find_run_by_prefix = MagicMock(return_value=run)
        return idx

    def test_not_found_run_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace()
        idx.find_run_by_prefix = MagicMock(side_effect=ArtifactNotFoundError("nope"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_ambiguous_prefix_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        idx = SimpleNamespace()
        idx.find_run_by_prefix = MagicMock(side_effect=ValueError("amb"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 2
        assert "Ambiguous" in out.output

    def test_load_error_exits_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _make_run(load_error="boom", index=None)
        _patch_index(monkeypatch, self._idx(run))
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 3

    def test_resolve_artifact_not_found_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = SimpleNamespace(artifacts=[], get_by_id=MagicMock(return_value=None))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(side_effect=ArtifactNotFoundError("missing artifact"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 1
        assert "Artifact not found" in out.output

    def test_resolve_missing_on_disk_exits_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = SimpleNamespace(artifacts=[])
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(
            side_effect=ArtifactPathUnresolvableError("file no longer exists on disk")
        )
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 5
        assert "missing" in out.output.lower()

    def test_resolve_unresolvable_exits_4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = SimpleNamespace(artifacts=[])
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(
            side_effect=ArtifactPathUnresolvableError("cannot resolve relative path")
        )
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 4
        assert "Unresolvable" in out.output

    def test_resolve_prints_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _make_run(index=SimpleNamespace(artifacts=[]))
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.json"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1"])
        assert out.exit_code == 0
        assert "/abs/file.json" in out.output

    def test_no_recheck_flag_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _make_run(index=SimpleNamespace(artifacts=[]))
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.json"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1", "--no-recheck"])
        assert out.exit_code == 0
        # recheck_exists should be False when --no-recheck given.
        _, kwargs = idx.resolve.call_args
        assert kwargs["recheck_exists"] is False

    def test_print_content_disappeared_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = SimpleNamespace(get_by_id=MagicMock(return_value=None))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.json"))
        _patch_index(monkeypatch, idx)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1", "--print-content"])
        assert out.exit_code == 1
        assert "disappeared" in out.output

    def test_print_content_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        indexed = _make_artifact(content_type="application/json")
        index = SimpleNamespace(get_by_id=MagicMock(return_value=indexed))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.json"))
        _patch_index(monkeypatch, idx)
        monkeypatch.setattr(cli_mod, "read_json_artifact", lambda *a, **k: {"hello": "world"})
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1", "--print-content"])
        assert out.exit_code == 0
        assert "hello" in out.output

    def test_print_content_json_read_error_exits_4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        indexed = _make_artifact(content_type="application/json")
        index = SimpleNamespace(get_by_id=MagicMock(return_value=indexed))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.json"))
        _patch_index(monkeypatch, idx)

        def _boom(*a, **k):
            raise RuntimeError("json broke")

        monkeypatch.setattr(cli_mod, "read_json_artifact", _boom)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1", "--print-content"])
        assert out.exit_code == 4
        assert "read_json_artifact failed" in out.output

    def test_print_content_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        indexed = _make_artifact(content_type="text/plain")
        index = SimpleNamespace(get_by_id=MagicMock(return_value=indexed))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.txt"))
        _patch_index(monkeypatch, idx)
        monkeypatch.setattr(cli_mod, "read_text_artifact", lambda *a, **k: "plain body")
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1", "--print-content"])
        assert out.exit_code == 0
        assert "plain body" in out.output

    def test_print_content_text_read_error_exits_4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        indexed = _make_artifact(content_type="text/plain")
        index = SimpleNamespace(get_by_id=MagicMock(return_value=indexed))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.txt"))
        _patch_index(monkeypatch, idx)

        def _boom(*a, **k):
            raise RuntimeError("text broke")

        monkeypatch.setattr(cli_mod, "read_text_artifact", _boom)
        out = _runner.invoke(app, ["get-artifact", "/root", "run_a", "art1", "--print-content"])
        assert out.exit_code == 4
        assert "read_text_artifact failed" in out.output

    def test_print_content_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        indexed = _make_artifact(content_type="text/plain")
        index = SimpleNamespace(get_by_id=MagicMock(return_value=indexed))
        run = _make_run(index=index)
        idx = self._idx(run)
        idx.resolve = MagicMock(return_value=Path("/abs/file.txt"))
        _patch_index(monkeypatch, idx)
        long_text = "X" * 100
        monkeypatch.setattr(cli_mod, "read_text_artifact", lambda *a, **k: long_text)
        out = _runner.invoke(
            app,
            [
                "get-artifact",
                "/root",
                "run_a",
                "art1",
                "--print-content",
                "--max-bytes",
                "10",
            ],
        )
        assert out.exit_code == 0
        assert "truncated" in out.output
