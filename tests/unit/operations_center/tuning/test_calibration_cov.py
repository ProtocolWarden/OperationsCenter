# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.tuning.calibration import (
    _EXPECTED_RATES,
    _MIN_SAMPLE_SIZE,
    CalibrationRecord,
    ConfidenceCalibrationStore,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "calibration_store.json"


@pytest.fixture
def store(store_path: Path) -> ConfidenceCalibrationStore:
    return ConfidenceCalibrationStore(path=store_path)


def _iso_days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------
# CalibrationRecord
# --------------------------------------------------------------------------
def test_calibration_record_to_dict_roundtrip() -> None:
    rec = CalibrationRecord(
        family="lint_fix",
        confidence="high",
        total=10,
        merged=8,
        escalated=1,
        abandoned=1,
        acceptance_rate=0.8,
        expected_rate=0.8,
        calibration_ratio=1.0,
        repo_key="repoA",
    )
    d = rec.to_dict()
    assert d["family"] == "lint_fix"
    assert d["repo_key"] == "repoA"
    assert d["acceptance_rate"] == 0.8
    # default repo_key is None
    rec2 = CalibrationRecord(
        family="f",
        confidence="low",
        total=1,
        merged=0,
        escalated=0,
        abandoned=1,
        acceptance_rate=0.0,
        expected_rate=0.3,
        calibration_ratio=0.0,
    )
    assert rec2.to_dict()["repo_key"] is None


# --------------------------------------------------------------------------
# record()
# --------------------------------------------------------------------------
def test_record_unknown_confidence_ignored(store: ConfidenceCalibrationStore) -> None:
    store.record("lint_fix", "ultra", "merged")
    assert not store._path.exists()


def test_record_unknown_outcome_ignored(store: ConfidenceCalibrationStore) -> None:
    store.record("lint_fix", "high", "exploded")
    assert not store._path.exists()


def test_record_persists_event_without_repo_key(
    store: ConfidenceCalibrationStore, store_path: Path
) -> None:
    store.record("lint_fix", "high", "merged")
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(data["events"]) == 1
    ev = data["events"][0]
    assert ev["family"] == "lint_fix"
    assert ev["confidence"] == "high"
    assert ev["outcome"] == "merged"
    assert "recorded_at" in ev
    assert "repo_key" not in ev


def test_record_with_repo_key(store: ConfidenceCalibrationStore, store_path: Path) -> None:
    store.record("type_fix", "medium", "escalated", repo_key="repoB")
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["events"][0]["repo_key"] == "repoB"


def test_record_appends_multiple(store: ConfidenceCalibrationStore, store_path: Path) -> None:
    store.record("f", "high", "merged")
    store.record("f", "high", "abandoned")
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(data["events"]) == 2


# --------------------------------------------------------------------------
# calibration_for()
# --------------------------------------------------------------------------
def test_calibration_for_below_min_sample(store: ConfidenceCalibrationStore) -> None:
    for _ in range(_MIN_SAMPLE_SIZE - 1):
        store.record("f", "high", "merged")
    assert store.calibration_for("f", "high") is None


def test_calibration_for_returns_rate(store: ConfidenceCalibrationStore) -> None:
    for _ in range(3):
        store.record("f", "high", "merged")
    for _ in range(2):
        store.record("f", "high", "escalated")
    # 3 merged of 5 total
    assert store.calibration_for("f", "high") == pytest.approx(3 / 5)


def test_calibration_for_filters_by_repo_key(store: ConfidenceCalibrationStore) -> None:
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("f", "high", "merged", repo_key="repoA")
    # different repo present but should not be counted
    store.record("f", "high", "merged", repo_key="repoB")
    assert store.calibration_for("f", "high", repo_key="repoA") == 1.0
    # repoB alone is below min sample
    assert store.calibration_for("f", "high", repo_key="repoB") is None


def test_calibration_for_global_counts_all_repos(store: ConfidenceCalibrationStore) -> None:
    for _ in range(3):
        store.record("f", "high", "merged", repo_key="repoA")
    for _ in range(2):
        store.record("f", "high", "escalated", repo_key="repoB")
    # global (repo_key=None) sees all 5
    assert store.calibration_for("f", "high") == pytest.approx(3 / 5)


def test_calibration_for_window_excludes_stale(
    store: ConfidenceCalibrationStore, store_path: Path
) -> None:
    events = [
        {
            "recorded_at": _iso_days_ago(200),
            "family": "f",
            "confidence": "high",
            "outcome": "merged",
        }
        for _ in range(_MIN_SAMPLE_SIZE)
    ]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({"events": events}), encoding="utf-8")
    # default window 90 days excludes everything
    assert store.calibration_for("f", "high") is None
    # window_days=None disables filtering
    assert store.calibration_for("f", "high", window_days=None) == 1.0


# --------------------------------------------------------------------------
# cleanup_old_events()
# --------------------------------------------------------------------------
def test_cleanup_removes_stale_events(store: ConfidenceCalibrationStore, store_path: Path) -> None:
    events = [
        {
            "recorded_at": _iso_days_ago(200),
            "family": "f",
            "confidence": "high",
            "outcome": "merged",
        },
        {"recorded_at": _iso_days_ago(1), "family": "f", "confidence": "high", "outcome": "merged"},
    ]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({"events": events}), encoding="utf-8")
    removed = store.cleanup_old_events(window_days=90)
    assert removed == 1
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(data["events"]) == 1


def test_cleanup_no_removal_keeps_file_untouched(store: ConfidenceCalibrationStore) -> None:
    for _ in range(2):
        store.record("f", "high", "merged")
    removed = store.cleanup_old_events(window_days=90)
    assert removed == 0


def test_cleanup_window_none_short_circuits(
    store: ConfidenceCalibrationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # _cutoff(None) -> None, returns 0 without touching store
    monkeypatch.setattr(
        ConfidenceCalibrationStore, "_cutoff", staticmethod(lambda window_days: None)
    )
    assert store.cleanup_old_events(window_days=90) == 0


# --------------------------------------------------------------------------
# _cutoff()
# --------------------------------------------------------------------------
def test_cutoff_none() -> None:
    assert ConfidenceCalibrationStore._cutoff(None) is None


def test_cutoff_returns_iso() -> None:
    cutoff = ConfidenceCalibrationStore._cutoff(90)
    assert cutoff is not None
    parsed = datetime.fromisoformat(cutoff)
    delta = datetime.now(UTC) - parsed
    assert timedelta(days=89) < delta < timedelta(days=91)


# --------------------------------------------------------------------------
# report()
# --------------------------------------------------------------------------
def test_report_skips_below_min_sample(store: ConfidenceCalibrationStore) -> None:
    for _ in range(_MIN_SAMPLE_SIZE - 1):
        store.record("f", "high", "merged")
    assert store.report() == []


def test_report_global_aggregate(store: ConfidenceCalibrationStore) -> None:
    for _ in range(4):
        store.record("lint_fix", "high", "merged", repo_key="repoA")
    store.record("lint_fix", "high", "escalated", repo_key="repoB")
    records = store.report()
    assert len(records) == 1
    rec = records[0]
    assert rec.family == "lint_fix"
    assert rec.confidence == "high"
    assert rec.total == 5
    assert rec.merged == 4
    assert rec.escalated == 1
    assert rec.abandoned == 0
    assert rec.acceptance_rate == pytest.approx(0.8)
    assert rec.expected_rate == _EXPECTED_RATES["high"]
    assert rec.calibration_ratio == pytest.approx(1.0)
    assert rec.repo_key is None  # global aggregate -> rk_val "" -> None


def test_report_per_repo_grouping(store: ConfidenceCalibrationStore) -> None:
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("f", "medium", "merged", repo_key="repoA")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("f", "medium", "abandoned", repo_key="repoB")
    records = store.report(per_repo=True)
    by_repo = {r.repo_key: r for r in records}
    assert set(by_repo) == {"repoA", "repoB"}
    assert by_repo["repoA"].merged == _MIN_SAMPLE_SIZE
    assert by_repo["repoB"].abandoned == _MIN_SAMPLE_SIZE
    assert by_repo["repoB"].acceptance_rate == 0.0
    assert by_repo["repoB"].calibration_ratio == 0.0


def test_report_per_repo_missing_repo_key_groups_empty(
    store: ConfidenceCalibrationStore,
) -> None:
    # records without repo_key -> rk "" in per_repo grouping
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("f", "low", "merged")
    records = store.report(per_repo=True)
    assert len(records) == 1
    assert records[0].repo_key is None  # rk_val "" -> None


def test_report_ignores_invalid_family_or_confidence(
    store: ConfidenceCalibrationStore, store_path: Path
) -> None:
    events = []
    # empty family -> skipped
    for _ in range(_MIN_SAMPLE_SIZE):
        events.append(
            {
                "recorded_at": _iso_days_ago(1),
                "family": "",
                "confidence": "high",
                "outcome": "merged",
            }
        )
    # invalid confidence -> skipped
    for _ in range(_MIN_SAMPLE_SIZE):
        events.append(
            {
                "recorded_at": _iso_days_ago(1),
                "family": "f",
                "confidence": "ultra",
                "outcome": "merged",
            }
        )
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({"events": events}), encoding="utf-8")
    assert store.report() == []
    assert store.report(per_repo=True) == []


def test_report_window_none_uses_all_events(
    store: ConfidenceCalibrationStore, store_path: Path
) -> None:
    events = [
        {
            "recorded_at": _iso_days_ago(500),
            "family": "f",
            "confidence": "high",
            "outcome": "merged",
        }
        for _ in range(_MIN_SAMPLE_SIZE)
    ]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({"events": events}), encoding="utf-8")
    assert store.report(window_days=None)  # not filtered
    assert store.report(window_days=90) == []  # filtered out


# --------------------------------------------------------------------------
# _load() / _save()
# --------------------------------------------------------------------------
def test_load_missing_file_returns_empty(store: ConfidenceCalibrationStore) -> None:
    assert store._load() == {"events": []}


def test_load_corrupt_file_returns_empty(
    store: ConfidenceCalibrationStore, store_path: Path
) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{not valid json", encoding="utf-8")
    assert store._load() == {"events": []}


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "store.json"
    store = ConfidenceCalibrationStore(path=path)
    store._save({"events": [{"x": 1}]})
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"events": [{"x": 1}]}


def test_default_path_constant() -> None:
    s = ConfidenceCalibrationStore()
    assert s._path == Path("state/calibration_store.json")
