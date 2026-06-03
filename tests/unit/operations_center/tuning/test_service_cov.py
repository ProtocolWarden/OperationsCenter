# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from operations_center.tuning import service as service_mod
from operations_center.tuning.models import (
    FamilyMetrics,
    SkippedTuningChange,
    TuningRecommendation,
    TuningRunArtifact,
)
from operations_center.tuning.service import (
    TuningContext,
    TuningRegulatorService,
    new_tuning_context,
)


def _rec(family: str, action: str) -> TuningRecommendation:
    return TuningRecommendation(
        family=family,
        action=action,
        rationale="because",
        confidence="high",
    )


def _metrics(family: str) -> FamilyMetrics:
    return FamilyMetrics(family=family, sample_runs=5)


def _patch_aggregate(monkeypatch, family_metrics, sample_runs, start, end):
    def fake_aggregate(*, decision_root, proposer_root, window):
        return family_metrics, sample_runs, start, end

    monkeypatch.setattr(service_mod, "aggregate_family_metrics", fake_aggregate)


# ---------------------------------------------------------------------------
# new_tuning_context
# ---------------------------------------------------------------------------


def test_new_tuning_context_defaults(tmp_path: Path) -> None:
    ctx = new_tuning_context(
        decision_root=tmp_path / "dec",
        proposer_root=tmp_path / "prop",
    )
    assert ctx.auto_apply is False
    assert ctx.window == 20
    assert ctx.source_command == "operations-center tune-autonomy"
    assert ctx.decision_root == tmp_path / "dec"
    assert ctx.proposer_root == tmp_path / "prop"
    assert ctx.dry_run is False
    assert isinstance(ctx.generated_at, datetime)
    assert ctx.generated_at.tzinfo is UTC
    # run_id format: tun_<timestamp>_<microsecond hex>, capped at 31 chars
    assert ctx.run_id.startswith("tun_")
    assert len(ctx.run_id) <= 31
    assert re.match(r"tun_\d{8}T\d{6}Z_[0-9a-f]{6}$", ctx.run_id)


def test_new_tuning_context_overrides(tmp_path: Path) -> None:
    ctx = new_tuning_context(
        decision_root=tmp_path,
        proposer_root=tmp_path,
        auto_apply=True,
        window=7,
        source_command="custom-cmd",
    )
    assert ctx.auto_apply is True
    assert ctx.window == 7
    assert ctx.source_command == "custom-cmd"


def test_new_tuning_context_run_id_unique(tmp_path: Path) -> None:
    a = new_tuning_context(decision_root=tmp_path, proposer_root=tmp_path)
    b = new_tuning_context(decision_root=tmp_path, proposer_root=tmp_path)
    # microsecond component makes these distinct in practice
    assert a.run_id != b.run_id or a.generated_at != b.generated_at


# ---------------------------------------------------------------------------
# TuningRegulatorService.__init__ defaults
# ---------------------------------------------------------------------------


def test_service_init_defaults() -> None:
    svc = TuningRegulatorService()
    assert svc.engine is not None
    assert svc.guardrails is not None
    assert svc.applier is not None
    assert svc.loader is not None
    assert svc.writer is not None


def test_service_init_injection() -> None:
    engine = MagicMock()
    guardrails = MagicMock()
    applier = MagicMock()
    loader = MagicMock()
    writer = MagicMock()
    svc = TuningRegulatorService(
        recommendation_engine=engine,
        guardrails=guardrails,
        applier=applier,
        loader=loader,
        artifact_writer=writer,
    )
    assert svc.engine is engine
    assert svc.guardrails is guardrails
    assert svc.applier is applier
    assert svc.loader is loader
    assert svc.writer is writer


# ---------------------------------------------------------------------------
# run() — dry_run path (no write)
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, **kw) -> TuningContext:
    base = dict(
        run_id="tun_test",
        generated_at=datetime(2026, 6, 2, tzinfo=UTC),
        source_command="cmd",
        decision_root=tmp_path / "dec",
        proposer_root=tmp_path / "prop",
    )
    base.update(kw)
    return TuningContext(**base)


def test_run_dry_run_returns_empty_paths(monkeypatch, tmp_path: Path) -> None:
    fm = [_metrics("observation_coverage")]
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = datetime(2026, 6, 2, tzinfo=UTC)
    _patch_aggregate(monkeypatch, fm, 5, start, end)

    engine = MagicMock()
    engine.evaluate.return_value = [_rec("observation_coverage", "keep")]
    writer = MagicMock()
    svc = TuningRegulatorService(recommendation_engine=engine, artifact_writer=writer)

    ctx = _make_ctx(tmp_path, dry_run=True)
    artifact, paths = svc.run(ctx)

    assert paths == []
    writer.write.assert_not_called()
    engine.evaluate.assert_called_once_with(fm)
    assert isinstance(artifact, TuningRunArtifact)
    assert artifact.run_id == "tun_test"
    assert artifact.dry_run is True
    assert artifact.auto_apply is False
    assert artifact.window_runs == 5
    assert artifact.window_start == start
    assert artifact.window_end == end
    assert artifact.family_metrics == fm
    assert artifact.changes_applied == []
    assert artifact.changes_skipped == []


def test_run_passes_context_to_aggregate(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_aggregate(*, decision_root, proposer_root, window):
        captured["decision_root"] = decision_root
        captured["proposer_root"] = proposer_root
        captured["window"] = window
        return [], 0, None, None

    monkeypatch.setattr(service_mod, "aggregate_family_metrics", fake_aggregate)
    engine = MagicMock()
    engine.evaluate.return_value = []
    svc = TuningRegulatorService(recommendation_engine=engine)

    ctx = _make_ctx(tmp_path, dry_run=True, window=13)
    svc.run(ctx)

    assert captured["decision_root"] == tmp_path / "dec"
    assert captured["proposer_root"] == tmp_path / "prop"
    assert captured["window"] == 13


# ---------------------------------------------------------------------------
# run() — write path
# ---------------------------------------------------------------------------


def test_run_writes_artifact_when_not_dry_run(monkeypatch, tmp_path: Path) -> None:
    _patch_aggregate(monkeypatch, [], 3, None, None)
    engine = MagicMock()
    engine.evaluate.return_value = []
    writer = MagicMock()
    writer.write.return_value = ["/some/path.json"]
    svc = TuningRegulatorService(recommendation_engine=engine, artifact_writer=writer)

    ctx = _make_ctx(tmp_path, dry_run=False)
    artifact, paths = svc.run(ctx)

    writer.write.assert_called_once_with(artifact)
    assert paths == ["/some/path.json"]


# ---------------------------------------------------------------------------
# run() — auto_apply -> skipped changes
# ---------------------------------------------------------------------------


def test_run_auto_apply_records_skipped_for_threshold_actions(monkeypatch, tmp_path: Path) -> None:
    _patch_aggregate(monkeypatch, [], 9, None, None)
    recs = [
        _rec("fam_a", "loosen_threshold"),
        _rec("fam_b", "tighten_threshold"),
        _rec("fam_c", "keep"),
        _rec("fam_d", "review"),
    ]
    engine = MagicMock()
    engine.evaluate.return_value = recs
    svc = TuningRegulatorService(recommendation_engine=engine)

    ctx = _make_ctx(tmp_path, dry_run=True, auto_apply=True)
    artifact, _ = svc.run(ctx)

    # Only the two threshold actions become skipped changes
    assert len(artifact.changes_skipped) == 2
    families = {c.family for c in artifact.changes_skipped}
    assert families == {"fam_a", "fam_b"}
    for c in artifact.changes_skipped:
        assert isinstance(c, SkippedTuningChange)
        assert c.reason == "review_only_runtime"
        assert c.evidence == {"requested_auto_apply": True, "sample_runs": 9}
    # Intended action preserved
    by_family = {c.family: c.intended_action for c in artifact.changes_skipped}
    assert by_family["fam_a"] == "loosen_threshold"
    assert by_family["fam_b"] == "tighten_threshold"
    # artifact always reports auto_apply False (runtime is recommendation-only)
    assert artifact.auto_apply is False
    assert artifact.changes_applied == []


def test_run_auto_apply_no_threshold_actions(monkeypatch, tmp_path: Path) -> None:
    _patch_aggregate(monkeypatch, [], 4, None, None)
    engine = MagicMock()
    engine.evaluate.return_value = [_rec("fam", "keep"), _rec("fam2", "no_data")]
    svc = TuningRegulatorService(recommendation_engine=engine)

    ctx = _make_ctx(tmp_path, dry_run=True, auto_apply=True)
    artifact, _ = svc.run(ctx)

    assert artifact.changes_skipped == []


def test_run_no_auto_apply_skips_no_changes(monkeypatch, tmp_path: Path) -> None:
    _patch_aggregate(monkeypatch, [], 4, None, None)
    engine = MagicMock()
    # Even with threshold actions, no skipped changes when auto_apply is False
    engine.evaluate.return_value = [_rec("fam", "loosen_threshold")]
    svc = TuningRegulatorService(recommendation_engine=engine)

    ctx = _make_ctx(tmp_path, dry_run=True, auto_apply=False)
    artifact, _ = svc.run(ctx)

    assert artifact.changes_skipped == []


def test_run_recommendations_attached_to_artifact(monkeypatch, tmp_path: Path) -> None:
    _patch_aggregate(monkeypatch, [], 2, None, None)
    recs = [_rec("fam", "review")]
    engine = MagicMock()
    engine.evaluate.return_value = recs
    svc = TuningRegulatorService(recommendation_engine=engine)

    ctx = _make_ctx(tmp_path, dry_run=True)
    artifact, _ = svc.run(ctx)
    assert artifact.recommendations == recs
