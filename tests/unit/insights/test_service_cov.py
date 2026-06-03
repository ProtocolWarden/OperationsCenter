# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.insights import service as service_mod
from operations_center.insights.models import (
    DerivedInsight,
    InsightRepoRef,
    RepoInsightsArtifact,
)
from operations_center.insights.service import (
    InsightEngineService,
    InsightGenerationContext,
    new_generation_context,
)
from operations_center.observer.models import (
    CheckSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TodoSignal,
)


def _make_snapshot(run_id: str, name: str = "demo", path: str = "/tmp/demo") -> RepoStateSnapshot:
    return RepoStateSnapshot(
        run_id=run_id,
        observed_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        source_command="observe",
        repo=RepoContextSnapshot(
            name=name,
            path=Path(path),
            current_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="passing"),
            dependency_drift=DependencyDriftSignal(status="healthy"),
            todo_signal=TodoSignal(),
        ),
    )


def _make_insight(insight_id: str) -> DerivedInsight:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    return DerivedInsight(
        insight_id=insight_id,
        dedup_key=f"key-{insight_id}",
        kind="test_kind",
        subject="subj",
        status="open",
        first_seen_at=now,
        last_seen_at=now,
    )


def _make_context(
    *,
    repo_filter: str | None = "demo",
    snapshot_run_id: str | None = None,
    history_limit: int = 5,
    run_id: str = "ins_run",
    source_command: str = "insights",
) -> InsightGenerationContext:
    return InsightGenerationContext(
        repo_filter=repo_filter,
        snapshot_run_id=snapshot_run_id,
        history_limit=history_limit,
        run_id=run_id,
        generated_at=datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC),
        source_command=source_command,
    )


def test_default_artifact_writer_is_constructed_when_none() -> None:
    loader = MagicMock()
    svc = InsightEngineService(loader=loader, derivers=[])
    from operations_center.insights.artifact_writer import InsightArtifactWriter

    assert isinstance(svc.artifact_writer, InsightArtifactWriter)
    assert svc.loader is loader
    assert svc.derivers == []


def test_explicit_artifact_writer_is_preserved() -> None:
    loader = MagicMock()
    writer = MagicMock()
    svc = InsightEngineService(loader=loader, derivers=[], artifact_writer=writer)
    assert svc.artifact_writer is writer


def test_generate_with_no_snapshots_returns_empty_artifact() -> None:
    loader = MagicMock()
    loader.load.return_value = []
    writer = MagicMock()
    writer.write.return_value = ["/path/a.json"]
    svc = InsightEngineService(loader=loader, derivers=[MagicMock()], artifact_writer=writer)

    ctx = _make_context(repo_filter="myrepo")
    artifact, written = svc.generate(ctx)

    loader.load.assert_called_once_with(repo="myrepo", snapshot_run_id=None, history_limit=5)
    assert artifact.insights == []
    assert artifact.source_snapshots == []
    assert artifact.repo == InsightRepoRef(name="myrepo", path=Path(""))
    assert artifact.run_id == "ins_run"
    assert written == ["/path/a.json"]
    writer.write.assert_called_once_with(artifact)


def test_generate_with_no_snapshots_uses_unknown_when_repo_filter_none() -> None:
    loader = MagicMock()
    loader.load.return_value = []
    writer = MagicMock()
    writer.write.return_value = []
    svc = InsightEngineService(loader=loader, derivers=[], artifact_writer=writer)

    ctx = _make_context(repo_filter=None, snapshot_run_id="snap1")
    artifact, written = svc.generate(ctx)

    assert artifact.repo.name == "unknown"
    assert artifact.repo.path == Path("")
    assert written == []
    loader.load.assert_called_once_with(repo=None, snapshot_run_id="snap1", history_limit=5)


def test_generate_no_snapshots_skips_write_when_writer_falsy() -> None:
    # Exercise the `if self.artifact_writer else []` empty branch by forcing a falsy writer.
    loader = MagicMock()
    loader.load.return_value = []
    svc = InsightEngineService(loader=loader, derivers=[])
    svc.artifact_writer = None  # type: ignore[assignment]

    artifact, written = svc.generate(_make_context())

    assert written == []
    assert isinstance(artifact, RepoInsightsArtifact)


def test_generate_with_snapshots_runs_all_derivers_and_collects_insights() -> None:
    snap_a = _make_snapshot("snap-a", name="repoA", path="/repos/A")
    snap_b = _make_snapshot("snap-b", name="repoB", path="/repos/B")
    loader = MagicMock()
    loader.load.return_value = [snap_a, snap_b]

    d1 = MagicMock()
    d1.derive.return_value = [_make_insight("i1")]
    d2 = MagicMock()
    d2.derive.return_value = [_make_insight("i2"), _make_insight("i3")]

    writer = MagicMock()
    writer.write.return_value = ["/out/repoA.json"]

    svc = InsightEngineService(loader=loader, derivers=[d1, d2], artifact_writer=writer)
    artifact, written = svc.generate(_make_context(repo_filter="repoA"))

    # Repo comes from the first (current) snapshot, not the filter.
    assert artifact.repo.name == "repoA"
    assert artifact.repo.path == Path("/repos/A")
    assert [i.insight_id for i in artifact.insights] == ["i1", "i2", "i3"]
    assert [s.run_id for s in artifact.source_snapshots] == ["snap-a", "snap-b"]
    assert all(s.observed_at == snap_a.observed_at for s in artifact.source_snapshots)
    assert written == ["/out/repoA.json"]

    d1.derive.assert_called_once_with([snap_a, snap_b])
    d2.derive.assert_called_once_with([snap_a, snap_b])
    writer.write.assert_called_once_with(artifact)


def test_generate_with_snapshots_and_no_derivers_yields_no_insights() -> None:
    snap = _make_snapshot("only")
    loader = MagicMock()
    loader.load.return_value = [snap]
    writer = MagicMock()
    writer.write.return_value = []
    svc = InsightEngineService(loader=loader, derivers=[], artifact_writer=writer)

    artifact, written = svc.generate(_make_context())

    assert artifact.insights == []
    assert len(artifact.source_snapshots) == 1
    assert written == []


def test_new_generation_context_builds_expected_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    micros = 0xABCDE  # 703710, valid (<= 999999)
    fixed = datetime(2026, 6, 2, 13, 14, 15, micros, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return fixed

    monkeypatch.setattr(service_mod, "datetime", _FixedDatetime)

    ctx = new_generation_context(
        repo_filter="rf",
        snapshot_run_id="srid",
        history_limit=9,
        source_command="cmd",
    )

    assert ctx.repo_filter == "rf"
    assert ctx.snapshot_run_id == "srid"
    assert ctx.history_limit == 9
    assert ctx.source_command == "cmd"
    assert ctx.generated_at == fixed
    assert ctx.run_id == "ins_20260602T131415Z_0abcde"
    assert ctx.run_id == f"ins_20260602T131415Z_{micros:06x}"
    assert len(ctx.run_id) <= 31


def test_new_generation_context_run_id_slice_and_none_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Max microsecond -> 6 hex digits; the [-31:] slice is applied but the formatted
    # id is 27 chars so it passes through unchanged.
    fixed = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return fixed

    monkeypatch.setattr(service_mod, "datetime", _FixedDatetime)

    ctx = new_generation_context(
        repo_filter=None,
        snapshot_run_id=None,
        history_limit=1,
        source_command="x",
    )

    raw = f"ins_20261231T235959Z_{999999:06x}"
    assert ctx.run_id == raw[-31:]
    assert ctx.run_id == raw
    assert len(ctx.run_id) <= 31
    assert ctx.repo_filter is None
    assert ctx.snapshot_run_id is None


def test_generation_context_is_frozen() -> None:
    ctx = _make_context()
    with pytest.raises(Exception):
        ctx.run_id = "mutated"  # type: ignore[misc]
