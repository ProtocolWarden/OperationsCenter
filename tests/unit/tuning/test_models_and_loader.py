# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for tuning/models.py and tuning/loader.py.

Covers the TuningConfig.get_int override lookup, model defaults/round-trip,
and TuningArtifactLoader.load_recent (ordering, limit, missing dir, skipping
dirs without artifacts, and tolerating malformed JSON)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.tuning.loader import TuningArtifactLoader
from operations_center.tuning.models import (
    FamilyMetrics,
    TuningConfig,
    TuningRunArtifact,
)

_NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


# ── TuningConfig.get_int ──────────────────────────────────────────────────────


def test_get_int_returns_override_when_int() -> None:
    cfg = TuningConfig(updated_at=_NOW, overrides={"fam": {"min_runs": 1}})
    assert cfg.get_int("fam", "min_runs", default=5) == 1


def test_get_int_returns_default_when_family_absent() -> None:
    cfg = TuningConfig(updated_at=_NOW, overrides={})
    assert cfg.get_int("missing", "min_runs", default=5) == 5


def test_get_int_returns_default_when_key_absent() -> None:
    cfg = TuningConfig(updated_at=_NOW, overrides={"fam": {"other": 2}})
    assert cfg.get_int("fam", "min_runs", default=7) == 7


def test_get_int_returns_default_when_value_not_int() -> None:
    # A non-int override (e.g. a float or string) must fall back to the default.
    cfg = TuningConfig(updated_at=_NOW, overrides={"fam": {"min_runs": "nope"}})
    assert cfg.get_int("fam", "min_runs", default=3) == 3
    cfg2 = TuningConfig(updated_at=_NOW, overrides={"fam": {"min_runs": 1.5}})
    assert cfg2.get_int("fam", "min_runs", default=3) == 3


def test_config_version_defaults_to_1() -> None:
    assert TuningConfig(updated_at=_NOW).version == 1


# ── model defaults / round-trip ───────────────────────────────────────────────


def test_family_metrics_defaults() -> None:
    fm = FamilyMetrics(family="obs", sample_runs=4)
    assert fm.candidates_emitted == 0
    assert fm.suppression_rate == 0.0
    assert fm.top_suppression_reasons == {}


def test_run_artifact_round_trip() -> None:
    art = TuningRunArtifact(
        run_id="r1",
        generated_at=_NOW,
        source_command="tune",
        window_runs=10,
        family_metrics=[FamilyMetrics(family="obs", sample_runs=4)],
    )
    restored = TuningRunArtifact.model_validate(json.loads(art.model_dump_json()))
    assert restored.run_id == "r1"
    assert restored.dry_run is True
    assert restored.family_metrics[0].family == "obs"


# ── TuningArtifactLoader.load_recent ──────────────────────────────────────────


def _write_run(root: Path, name: str, run_id: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    art = TuningRunArtifact(run_id=run_id, generated_at=_NOW, source_command="tune", window_runs=1)
    (d / "tuning_run.json").write_text(art.model_dump_json(), encoding="utf-8")


def test_load_recent_missing_root_returns_empty(tmp_path: Path) -> None:
    loader = TuningArtifactLoader(tuning_root=tmp_path / "nope")
    assert loader.load_recent() == []


def test_load_recent_orders_newest_first_and_respects_limit(tmp_path: Path) -> None:
    # Dir names sort descending → newest first; limit caps the count.
    for name in ("2026-06-01", "2026-06-02", "2026-06-03"):
        _write_run(tmp_path, name, run_id=name)
    loader = TuningArtifactLoader(tuning_root=tmp_path)

    recent = loader.load_recent(limit=2)
    assert [a.run_id for a in recent] == ["2026-06-03", "2026-06-02"]


def test_load_recent_skips_dirs_without_artifact(tmp_path: Path) -> None:
    _write_run(tmp_path, "has-run", run_id="good")
    (tmp_path / "empty-dir").mkdir()
    loader = TuningArtifactLoader(tuning_root=tmp_path)
    assert [a.run_id for a in loader.load_recent()] == ["good"]


def test_load_recent_tolerates_malformed_json(tmp_path: Path) -> None:
    _write_run(tmp_path, "good", run_id="good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "tuning_run.json").write_text("{not json", encoding="utf-8")
    loader = TuningArtifactLoader(tuning_root=tmp_path)
    # Malformed entry is skipped, valid one still returned.
    assert [a.run_id for a in loader.load_recent()] == ["good"]
