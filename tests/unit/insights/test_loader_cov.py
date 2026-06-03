# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.insights.loader import SnapshotLoader
from operations_center.observer.models import (
    CheckSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TodoSignal,
)


def _make_snapshot(
    *,
    run_id: str,
    repo_name: str = "alpha",
    repo_path: str = "/tmp/alpha",
    observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    return RepoStateSnapshot(
        run_id=run_id,
        observed_at=observed_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_command="observe",
        repo=RepoContextSnapshot(
            name=repo_name,
            path=Path(repo_path),
            current_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="passing"),
            dependency_drift=DependencyDriftSignal(status="healthy"),
            todo_signal=TodoSignal(),
        ),
    )


def _write_snapshot(root: Path, subdir: str, snapshot: RepoStateSnapshot) -> Path:
    d = root / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / "repo_state_snapshot.json"
    p.write_text(snapshot.model_dump_json(), encoding="utf-8")
    return p


def _set_mtime(path: Path, mtime: float) -> None:
    import os

    os.utime(path, (mtime, mtime))


def test_default_root_when_none() -> None:
    loader = SnapshotLoader()
    assert loader.root == Path("tools/report/operations_center/observer")


def test_custom_root_preserved(tmp_path: Path) -> None:
    loader = SnapshotLoader(root=tmp_path)
    assert loader.root == tmp_path


def test_load_no_snapshots_raises(tmp_path: Path) -> None:
    loader = SnapshotLoader(root=tmp_path)
    with pytest.raises(ValueError, match="No observer snapshots found"):
        loader.load(repo=None, snapshot_run_id=None, history_limit=5)


def test_load_all_sorted_newest_first(tmp_path: Path) -> None:
    older = _make_snapshot(run_id="old")
    newer = _make_snapshot(run_id="new")
    p_old = _write_snapshot(tmp_path, "a", older)
    p_new = _write_snapshot(tmp_path, "b", newer)
    _set_mtime(p_old, 1000.0)
    _set_mtime(p_new, 2000.0)
    loader = SnapshotLoader(root=tmp_path)
    result = loader.load(repo=None, snapshot_run_id=None, history_limit=10)
    assert [s.run_id for s in result] == ["new", "old"]


def test_history_limit_truncates(tmp_path: Path) -> None:
    for i in range(5):
        p = _write_snapshot(tmp_path, f"d{i}", _make_snapshot(run_id=f"r{i}"))
        _set_mtime(p, 1000.0 + i)
    loader = SnapshotLoader(root=tmp_path)
    # history_limit + 1 entries returned
    result = loader.load(repo=None, snapshot_run_id=None, history_limit=1)
    assert len(result) == 2


def test_load_repo_filter_by_name(tmp_path: Path) -> None:
    a = _make_snapshot(run_id="ra", repo_name="Alpha", repo_path="/tmp/alpha")
    b = _make_snapshot(run_id="rb", repo_name="Beta", repo_path="/tmp/beta")
    _write_snapshot(tmp_path, "a", a)
    _write_snapshot(tmp_path, "b", b)
    loader = SnapshotLoader(root=tmp_path)
    # case-insensitive + whitespace trimmed
    result = loader.load(repo="  alpha ", snapshot_run_id=None, history_limit=10)
    assert [s.run_id for s in result] == ["ra"]


def test_load_repo_filter_by_path(tmp_path: Path) -> None:
    a = _make_snapshot(run_id="ra", repo_name="Alpha", repo_path="/tmp/alpha")
    b = _make_snapshot(run_id="rb", repo_name="Beta", repo_path="/tmp/beta")
    _write_snapshot(tmp_path, "a", a)
    _write_snapshot(tmp_path, "b", b)
    loader = SnapshotLoader(root=tmp_path)
    result = loader.load(repo="/TMP/BETA", snapshot_run_id=None, history_limit=10)
    assert [s.run_id for s in result] == ["rb"]


def test_load_repo_filter_no_match_raises(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "a", _make_snapshot(run_id="ra", repo_name="Alpha"))
    loader = SnapshotLoader(root=tmp_path)
    with pytest.raises(ValueError, match="No observer snapshots found"):
        loader.load(repo="nonexistent", snapshot_run_id=None, history_limit=10)


def test_load_run_id_not_found_raises(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "a", _make_snapshot(run_id="ra"))
    loader = SnapshotLoader(root=tmp_path)
    with pytest.raises(ValueError, match="Snapshot run id not found: missing"):
        loader.load(repo=None, snapshot_run_id="missing", history_limit=10)


def test_load_run_id_reorders_current_first_same_repo_path(tmp_path: Path) -> None:
    # Three snapshots: two share repo path "/tmp/alpha", one is a different repo.
    s1 = _make_snapshot(run_id="r1", repo_name="alpha", repo_path="/tmp/alpha")
    s2 = _make_snapshot(run_id="r2", repo_name="alpha", repo_path="/tmp/alpha")
    s3 = _make_snapshot(run_id="r3", repo_name="beta", repo_path="/tmp/beta")
    p1 = _write_snapshot(tmp_path, "a", s1)
    p2 = _write_snapshot(tmp_path, "b", s2)
    p3 = _write_snapshot(tmp_path, "c", s3)
    _set_mtime(p1, 3000.0)  # newest
    _set_mtime(p2, 2000.0)
    _set_mtime(p3, 1000.0)
    loader = SnapshotLoader(root=tmp_path)
    # Select r2 (the older alpha snapshot); it must be pulled to front and the
    # beta snapshot must be filtered out since it has a different repo path.
    result = loader.load(repo=None, snapshot_run_id="r2", history_limit=10)
    assert result[0].run_id == "r2"
    run_ids = {s.run_id for s in result}
    assert run_ids == {"r1", "r2"}
    assert "r3" not in run_ids


def test_latest_snapshot_age_none_when_empty(tmp_path: Path) -> None:
    loader = SnapshotLoader(root=tmp_path)
    assert loader.latest_snapshot_age_hours() is None


def test_latest_snapshot_age_overall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_snapshot(tmp_path, "a", _make_snapshot(run_id="ra"))
    # mtime exactly 2 hours before "now"
    now_ts = 100000.0
    _set_mtime(p, now_ts - 2 * 3600)

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):  # noqa: ARG004
            from datetime import datetime as _dt

            return _dt.fromtimestamp(now_ts, tz=timezone.utc)

    monkeypatch.setattr("operations_center.insights.loader.datetime", _FakeDateTime)
    loader = SnapshotLoader(root=tmp_path)
    assert loader.latest_snapshot_age_hours() == pytest.approx(2.0)


def test_latest_snapshot_age_filtered_by_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pa = _write_snapshot(tmp_path, "a", _make_snapshot(run_id="ra", repo_name="Alpha"))
    pb = _write_snapshot(tmp_path, "b", _make_snapshot(run_id="rb", repo_name="Beta"))
    now_ts = 100000.0
    _set_mtime(pa, now_ts - 5 * 3600)
    _set_mtime(pb, now_ts - 1 * 3600)

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):  # noqa: ARG004
            from datetime import datetime as _dt

            return _dt.fromtimestamp(now_ts, tz=timezone.utc)

    monkeypatch.setattr("operations_center.insights.loader.datetime", _FakeDateTime)
    loader = SnapshotLoader(root=tmp_path)
    # Filter for Alpha: should pick pa (5h) not the newer pb.
    assert loader.latest_snapshot_age_hours(repo="alpha") == pytest.approx(5.0)


def test_latest_snapshot_age_repo_no_match_returns_none(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "a", _make_snapshot(run_id="ra", repo_name="Alpha"))
    loader = SnapshotLoader(root=tmp_path)
    assert loader.latest_snapshot_age_hours(repo="zeta") is None


def test_latest_snapshot_age_repo_skips_unparseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First (newest) file is corrupt -> exception path -> continue; second matches.
    good = _write_snapshot(tmp_path, "good", _make_snapshot(run_id="rg", repo_name="Alpha"))
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = bad_dir / "repo_state_snapshot.json"
    bad.write_text("not valid json", encoding="utf-8")
    now_ts = 100000.0
    _set_mtime(bad, now_ts - 1 * 3600)  # newest -> tried first, fails
    _set_mtime(good, now_ts - 4 * 3600)

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):  # noqa: ARG004
            from datetime import datetime as _dt

            return _dt.fromtimestamp(now_ts, tz=timezone.utc)

    monkeypatch.setattr("operations_center.insights.loader.datetime", _FakeDateTime)
    loader = SnapshotLoader(root=tmp_path)
    assert loader.latest_snapshot_age_hours(repo="alpha") == pytest.approx(4.0)


def test_all_snapshots_parses_each(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "a", _make_snapshot(run_id="ra"))
    _write_snapshot(tmp_path, "b", _make_snapshot(run_id="rb"))
    loader = SnapshotLoader(root=tmp_path)
    snaps = loader._all_snapshots()
    assert {s.run_id for s in snaps} == {"ra", "rb"}
    assert all(isinstance(s, RepoStateSnapshot) for s in snaps)
