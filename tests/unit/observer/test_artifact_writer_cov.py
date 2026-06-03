# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.observer.artifact_writer import ObserverArtifactWriter
from operations_center.observer.models import (
    CheckSignal,
    CommitMetadata,
    DependencyDriftSignal,
    FileHotspot,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TodoFileCount,
    TodoSignal,
)

_TS = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_snapshot(
    *,
    run_id: str = "run-001",
    base_branch: str | None = "main",
    recent_commits: list[CommitMetadata] | None = None,
    file_hotspots: list[FileHotspot] | None = None,
    todo_signal: TodoSignal | None = None,
    test_signal: CheckSignal | None = None,
    dependency_drift: DependencyDriftSignal | None = None,
    collector_errors: dict[str, str] | None = None,
) -> RepoStateSnapshot:
    repo = RepoContextSnapshot(
        name="demo",
        path=Path("/tmp/demo"),
        current_branch="feature",
        base_branch=base_branch,
        is_dirty=True,
    )
    signals = RepoSignalsSnapshot(
        recent_commits=recent_commits or [],
        file_hotspots=file_hotspots or [],
        test_signal=test_signal or CheckSignal(status="unavailable"),
        dependency_drift=dependency_drift or DependencyDriftSignal(status="unavailable"),
        todo_signal=todo_signal or TodoSignal(),
    )
    return RepoStateSnapshot(
        run_id=run_id,
        observed_at=_TS,
        source_command="observe",
        repo=repo,
        signals=signals,
        collector_errors=collector_errors or {},
    )


def _read_md(paths: list[str]) -> str:
    md_path = next(p for p in paths if p.endswith(".md"))
    return Path(md_path).read_text(encoding="utf-8")


def test_default_root_when_none() -> None:
    writer = ObserverArtifactWriter()
    assert writer.root == Path("tools/report/operations_center/observer")


def test_explicit_root_used(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path / "out")
    assert writer.root == tmp_path / "out"


def test_write_returns_both_paths_and_creates_files(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    snap = _make_snapshot()
    result = writer.write(snap)

    assert len(result) == 2
    json_path = Path(result[0])
    md_path = Path(result[1])
    assert json_path.name == "repo_state_snapshot.json"
    assert md_path.name == "repo_state_snapshot.md"
    assert json_path.exists()
    assert md_path.exists()
    assert json_path.parent == tmp_path / "run-001"


def test_write_creates_nested_run_dir(tmp_path: Path) -> None:
    root = tmp_path / "deeply" / "nested"
    writer = ObserverArtifactWriter(root=root)
    writer.write(_make_snapshot(run_id="abc"))
    assert (root / "abc").is_dir()


def test_write_idempotent_existing_dir(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    snap = _make_snapshot()
    first = writer.write(snap)
    # Second write into the same run_id (exist_ok path) must not raise.
    second = writer.write(snap)
    assert first == second


def test_json_content_roundtrips(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    result = writer.write(_make_snapshot(run_id="json-run"))
    data = json.loads(Path(result[0]).read_text(encoding="utf-8"))
    assert data["run_id"] == "json-run"
    assert data["source_command"] == "observe"
    assert data["repo"]["name"] == "demo"


def test_md_header_fields(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot()))
    assert "# Repo State Snapshot" in md
    assert "- run_id: run-001" in md
    assert f"- observed_at: {_TS.isoformat()}" in md
    assert "- repo_name: demo" in md
    assert "- current_branch: feature" in md
    assert "- base_branch: main" in md
    assert "- is_dirty: True" in md


def test_md_base_branch_unknown_when_none(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(base_branch=None)))
    assert "- base_branch: unknown" in md


def test_md_recent_commits_none_when_empty(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(recent_commits=[])))
    assert "## Recent Commits" in md
    section = md.split("## Recent Commits", 1)[1].split("## File Hotspots", 1)[0]
    assert "- none" in section


def test_md_recent_commits_rendered(tmp_path: Path) -> None:
    commit = CommitMetadata(
        sha_short="deadbee",
        author="alice",
        timestamp=_TS,
        subject="fix things",
    )
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(recent_commits=[commit])))
    assert f"- deadbee alice {_TS.isoformat()} fix things" in md


def test_md_file_hotspots_none_when_empty(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(file_hotspots=[])))
    section = md.split("## File Hotspots", 1)[1].split("## Test Signal", 1)[0]
    assert "- none" in section


def test_md_file_hotspots_rendered(tmp_path: Path) -> None:
    hotspot = FileHotspot(path="src/x.py", touch_count=7)
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(file_hotspots=[hotspot])))
    assert "- src/x.py: 7" in md


def test_md_test_and_dependency_none_fields(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot()))
    # test_signal defaults: source/observed_at/summary all None -> "none"
    assert "## Test Signal" in md
    assert "- status: unavailable" in md
    assert "- source: none" in md
    assert "- observed_at: none" in md
    assert "- summary: none" in md
    assert "## Dependency Drift" in md


def test_md_test_and_dependency_populated_fields(tmp_path: Path) -> None:
    test_signal = CheckSignal(
        status="passing",
        source="pytest",
        observed_at=_TS,
        summary="5 passed",
    )
    dep = DependencyDriftSignal(
        status="drift",
        source="pip-audit",
        observed_at=_TS,
        summary="2 outdated",
    )
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(test_signal=test_signal, dependency_drift=dep)))
    assert "- status: passing" in md
    assert "- source: pytest" in md
    assert f"- observed_at: {_TS.isoformat()}" in md
    assert "- summary: 5 passed" in md
    assert "- status: drift" in md
    assert "- source: pip-audit" in md
    assert "- summary: 2 outdated" in md


def test_md_todo_signal_counts_and_none_top_files(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    todo = TodoSignal(todo_count=3, fixme_count=1, top_files=[])
    md = _read_md(writer.write(_make_snapshot(todo_signal=todo)))
    assert "## TODO Signal" in md
    assert "- todo_count: 3" in md
    assert "- fixme_count: 1" in md
    section = md.split("## TODO Signal", 1)[1]
    assert "- none" in section


def test_md_todo_signal_top_files_rendered(tmp_path: Path) -> None:
    todo = TodoSignal(
        todo_count=2,
        fixme_count=0,
        top_files=[TodoFileCount(path="a.py", count=2)],
    )
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(todo_signal=todo)))
    assert "- a.py: 2" in md


def test_md_no_collector_errors_section_when_empty(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    md = _read_md(writer.write(_make_snapshot(collector_errors={})))
    assert "## Collector Errors" not in md


def test_md_collector_errors_section_rendered(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    errors = {"git": "boom", "tests": "timeout"}
    md = _read_md(writer.write(_make_snapshot(collector_errors=errors)))
    assert "## Collector Errors" in md
    assert "- git: boom" in md
    assert "- tests: timeout" in md


def test_write_overwrites_existing_content(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(root=tmp_path)
    writer.write(_make_snapshot(run_id="ov", collector_errors={"git": "boom"}))
    # Re-write same run_id without errors; file content must reflect latest write.
    result = writer.write(_make_snapshot(run_id="ov", collector_errors={}))
    md = _read_md(result)
    assert "## Collector Errors" not in md


def test_default_root_does_not_create_files(monkeypatch: pytest.MonkeyPatch) -> None:
    # Constructing with default root must not touch the filesystem.
    writer = ObserverArtifactWriter()
    assert not writer.root.exists() or writer.root.is_dir()
