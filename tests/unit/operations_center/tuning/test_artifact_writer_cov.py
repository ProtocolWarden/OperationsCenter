# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from operations_center.tuning.artifact_writer import (
    _DEFAULT_TUNING_ROOT,
    TuningArtifactWriter,
    _json_dumps,
)
from operations_center.tuning.models import (
    FamilyMetrics,
    SkippedTuningChange,
    TuningChange,
    TuningRecommendation,
    TuningRunArtifact,
)


def _ts() -> datetime:
    return datetime(2026, 6, 2, 12, 30, 0, tzinfo=timezone.utc)


def _full_artifact(run_id: str = "run-001") -> TuningRunArtifact:
    return TuningRunArtifact(
        run_id=run_id,
        generated_at=_ts(),
        source_command="oc tune",
        dry_run=False,
        auto_apply=True,
        window_runs=10,
        window_start=_ts(),
        window_end=_ts(),
        family_metrics=[
            FamilyMetrics(family="observation_coverage", sample_runs=5, candidates_emitted=3)
        ],
        recommendations=[
            TuningRecommendation(
                family="observation_coverage",
                action="loosen_threshold",
                rationale="too strict",
                confidence="high",
                suggested_change={"min_consecutive_runs": {"from": 2, "to": 1}},
            )
        ],
        changes_applied=[
            TuningChange(
                family="observation_coverage",
                key="min_consecutive_runs",
                before=2,
                after=1,
                reason="loosen",
                applied_at=_ts(),
            )
        ],
        changes_skipped=[
            SkippedTuningChange(
                family="other",
                intended_action="tighten_threshold",
                reason="cooldown_active",
            )
        ],
    )


def _empty_artifact() -> TuningRunArtifact:
    # Exercises the None branches for window_start / window_end and empty lists.
    return TuningRunArtifact(
        run_id="run-empty",
        generated_at=_ts(),
        source_command="oc tune",
        window_runs=0,
    )


def test_default_root_when_none() -> None:
    writer = TuningArtifactWriter()
    assert writer.tuning_root == _DEFAULT_TUNING_ROOT


def test_explicit_root(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path / "custom")
    assert writer.tuning_root == tmp_path / "custom"


def test_write_returns_four_paths_under_run_dir(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    paths = writer.write(_full_artifact())

    assert len(paths) == 4
    run_dir = tmp_path / "run-001"
    assert paths == [
        str(run_dir / "tuning_run.json"),
        str(run_dir / "family_tuning_summary.json"),
        str(run_dir / "tuning_recommendations.json"),
        str(run_dir / "tuning_changes.json"),
    ]
    for p in paths:
        assert Path(p).is_file()


def test_write_creates_nested_dirs(tmp_path: Path) -> None:
    root = tmp_path / "a" / "b" / "c"
    writer = TuningArtifactWriter(tuning_root=root)
    writer.write(_full_artifact())
    assert (root / "run-001").is_dir()


def test_run_json_matches_model_dump(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    artifact = _full_artifact()
    paths = writer.write(artifact)
    run_data = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    assert run_data == json.loads(artifact.model_dump_json(indent=2))


def test_summary_json_content(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    artifact = _full_artifact()
    paths = writer.write(artifact)
    summary = json.loads(Path(paths[1]).read_text(encoding="utf-8"))

    assert summary["run_id"] == "run-001"
    assert summary["generated_at"] == _ts().isoformat()
    assert summary["window_runs"] == 10
    assert summary["window_start"] == _ts().isoformat()
    assert summary["window_end"] == _ts().isoformat()
    assert summary["family_metrics"][0]["family"] == "observation_coverage"


def test_recommendations_json_content(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    paths = writer.write(_full_artifact())
    rec = json.loads(Path(paths[2]).read_text(encoding="utf-8"))
    assert rec["run_id"] == "run-001"
    assert rec["recommendations"][0]["action"] == "loosen_threshold"


def test_changes_json_content(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    paths = writer.write(_full_artifact())
    changes = json.loads(Path(paths[3]).read_text(encoding="utf-8"))
    assert changes["auto_apply"] is True
    assert changes["changes_applied"][0]["key"] == "min_consecutive_runs"
    assert changes["changes_skipped"][0]["reason"] == "cooldown_active"


def test_write_with_none_windows_and_empty_lists(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    paths = writer.write(_empty_artifact())

    summary = json.loads(Path(paths[1]).read_text(encoding="utf-8"))
    assert summary["window_start"] is None
    assert summary["window_end"] is None
    assert summary["family_metrics"] == []

    changes = json.loads(Path(paths[3]).read_text(encoding="utf-8"))
    assert changes["auto_apply"] is False
    assert changes["changes_applied"] == []
    assert changes["changes_skipped"] == []


def test_write_is_idempotent_existing_dir(tmp_path: Path) -> None:
    # exist_ok=True: writing twice into the same run dir must not raise.
    writer = TuningArtifactWriter(tuning_root=tmp_path)
    writer.write(_full_artifact())
    paths = writer.write(_full_artifact())
    assert all(Path(p).is_file() for p in paths)


def test_json_dumps_datetime_default_str() -> None:
    out = _json_dumps({"ts": _ts()})
    # default=str serializes via str(datetime), not isoformat().
    assert str(_ts()) in out
    assert json.loads(out)["ts"] == str(_ts())


def test_json_dumps_non_ascii_preserved() -> None:
    out = _json_dumps({"name": "café"})
    assert "café" in out
    assert "\\u" not in out


def test_json_dumps_is_indented() -> None:
    out = _json_dumps({"a": 1})
    assert "\n" in out
    assert "  " in out
