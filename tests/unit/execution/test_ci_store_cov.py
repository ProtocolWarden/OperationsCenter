# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.contracts.ci import (
    ClpBinding,
    ContinuousImprovementSpec,
    EvaluationSpec,
    ImprovementLineage,
    ImprovementStrategy,
    LineageAttempt,
    OcContinuousImprovementState,
    OcLineageIndexEntry,
    ScoringMetric,
)
from operations_center.contracts.enums import (
    EnforcedGuardrail,
    LineageBranchReason,
    RefinementStatus,
)
from operations_center.execution.ci_store import CiStore


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _ts(day: int = 1) -> datetime:
    return datetime(2026, 6, day, 12, 0, 0, tzinfo=timezone.utc)


def _index_entry(
    *,
    lineage_id: str = "lin-1",
    proposal_id: str = "prop-1",
    status: RefinementStatus = RefinementStatus.IN_PROGRESS,
    last_updated_at: datetime | None = None,
) -> OcLineageIndexEntry:
    return OcLineageIndexEntry(
        lineage_id=lineage_id,
        proposal_id=proposal_id,
        lineage_artifact_path=f"active/{lineage_id}/lineage.json",
        status=status,
        current_attempt_number=1,
        last_updated_at=last_updated_at or _ts(),
    )


def _strategy() -> ImprovementStrategy:
    return ImprovementStrategy(
        principle="Reduce false retries",
        constraints=["fail_closed"],
    )


def _eval_spec() -> EvaluationSpec:
    return EvaluationSpec(
        baseline_description="baseline",
        primary_scoring=ScoringMetric(metric="false_retry_rate"),
        guardrails=[EnforcedGuardrail.CUSTODIAN_CLEAN],
    )


def _ci_spec() -> ContinuousImprovementSpec:
    return ContinuousImprovementSpec(
        strategy=_strategy(),
        evaluation=_eval_spec(),
    )


def _ci_state(
    *,
    proposal_id: str = "prop-1",
    lineage_id: str = "lin-1",
) -> OcContinuousImprovementState:
    return OcContinuousImprovementState(
        proposal_id=proposal_id,
        lineage_index=_index_entry(lineage_id=lineage_id, proposal_id=proposal_id),
        spec_snapshot=_ci_spec(),
        clp_binding=ClpBinding(),
        last_updated_at=_ts(),
    )


def _lineage(*, lineage_id: str = "lin-1") -> ImprovementLineage:
    attempt = LineageAttempt(
        attempt_number=1,
        run_id="run-1",
        branch_reason=LineageBranchReason.INITIAL,
        strategy_used=_strategy(),
        started_at=_ts(),
    )
    return ImprovementLineage(
        lineage_id=lineage_id,
        status=RefinementStatus.IN_PROGRESS,
        current_attempt_number=1,
        attempts=[attempt],
    )


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "ci_lineage.json"


@pytest.fixture
def store(store_path: Path) -> CiStore:
    return CiStore(path=store_path)


# ---------------------------------------------------------------------------
# Index entry round-trips
# ---------------------------------------------------------------------------


def test_upsert_then_get_index_entry(store: CiStore, store_path: Path) -> None:
    entry = _index_entry()
    store.upsert_index_entry(entry)
    assert store_path.exists()
    got = store.get_index_entry("lin-1")
    assert got is not None
    assert got.lineage_id == "lin-1"
    assert got.status == RefinementStatus.IN_PROGRESS


def test_upsert_index_entry_overwrites(store: CiStore) -> None:
    store.upsert_index_entry(_index_entry(status=RefinementStatus.IN_PROGRESS))
    store.upsert_index_entry(_index_entry(status=RefinementStatus.ACCEPTED))
    got = store.get_index_entry("lin-1")
    assert got is not None
    assert got.status == RefinementStatus.ACCEPTED


def test_get_index_entry_missing_returns_none(store: CiStore) -> None:
    assert store.get_index_entry("nope") is None


def test_upsert_persists_to_disk_layout(store: CiStore, store_path: Path) -> None:
    store.upsert_index_entry(_index_entry())
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert "lineage_index" in raw
    assert "ci_state" in raw
    assert "lin-1" in raw["lineage_index"]


# ---------------------------------------------------------------------------
# list_index_entries — filters and sorting
# ---------------------------------------------------------------------------


def test_list_index_entries_empty(store: CiStore) -> None:
    assert store.list_index_entries() == []


def test_list_index_entries_sorted_desc_by_last_updated(store: CiStore) -> None:
    store.upsert_index_entry(_index_entry(lineage_id="a", last_updated_at=_ts(1)))
    store.upsert_index_entry(_index_entry(lineage_id="b", last_updated_at=_ts(3)))
    store.upsert_index_entry(_index_entry(lineage_id="c", last_updated_at=_ts(2)))
    ids = [e.lineage_id for e in store.list_index_entries()]
    assert ids == ["b", "c", "a"]


def test_list_index_entries_filter_by_proposal_id(store: CiStore) -> None:
    store.upsert_index_entry(_index_entry(lineage_id="a", proposal_id="p1"))
    store.upsert_index_entry(_index_entry(lineage_id="b", proposal_id="p2"))
    out = store.list_index_entries(proposal_id="p2")
    assert [e.lineage_id for e in out] == ["b"]


def test_list_index_entries_filter_by_status(store: CiStore) -> None:
    store.upsert_index_entry(_index_entry(lineage_id="a", status=RefinementStatus.ACCEPTED))
    store.upsert_index_entry(_index_entry(lineage_id="b", status=RefinementStatus.ABANDONED))
    out = store.list_index_entries(status=RefinementStatus.ABANDONED)
    assert [e.lineage_id for e in out] == ["b"]


def test_list_index_entries_combined_filters(store: CiStore) -> None:
    store.upsert_index_entry(
        _index_entry(lineage_id="a", proposal_id="p1", status=RefinementStatus.ACCEPTED)
    )
    store.upsert_index_entry(
        _index_entry(lineage_id="b", proposal_id="p1", status=RefinementStatus.ABANDONED)
    )
    store.upsert_index_entry(
        _index_entry(lineage_id="c", proposal_id="p2", status=RefinementStatus.ACCEPTED)
    )
    out = store.list_index_entries(proposal_id="p1", status=RefinementStatus.ACCEPTED)
    assert [e.lineage_id for e in out] == ["a"]


# ---------------------------------------------------------------------------
# CI state round-trips
# ---------------------------------------------------------------------------


def test_upsert_then_get_state(store: CiStore) -> None:
    store.upsert_state(_ci_state())
    got = store.get_state("prop-1")
    assert got is not None
    assert got.proposal_id == "prop-1"
    assert got.lineage_index.lineage_id == "lin-1"


def test_get_state_missing_returns_none(store: CiStore) -> None:
    assert store.get_state("absent") is None


def test_state_and_index_share_store(store: CiStore, store_path: Path) -> None:
    store.upsert_index_entry(_index_entry())
    store.upsert_state(_ci_state())
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert "lin-1" in raw["lineage_index"]
    assert "prop-1" in raw["ci_state"]
    # Both retrievable after interleaved writes.
    assert store.get_index_entry("lin-1") is not None
    assert store.get_state("prop-1") is not None


# ---------------------------------------------------------------------------
# load_lineage / save_lineage static helpers
# ---------------------------------------------------------------------------


def test_save_then_load_lineage(tmp_path: Path) -> None:
    art = tmp_path / "active" / "lin-1" / "lineage.json"
    lineage = _lineage()
    CiStore.save_lineage(lineage, art)
    assert art.exists()
    loaded = CiStore.load_lineage(art)
    assert loaded is not None
    assert loaded.lineage_id == "lin-1"
    assert len(loaded.attempts) == 1


def test_save_lineage_creates_parent_dirs(tmp_path: Path) -> None:
    art = tmp_path / "deep" / "nested" / "lineage.json"
    assert not art.parent.exists()
    CiStore.save_lineage(_lineage(), art)
    assert art.parent.is_dir()
    assert art.exists()


def test_load_lineage_missing_returns_none(tmp_path: Path) -> None:
    assert CiStore.load_lineage(tmp_path / "nope.json") is None


def test_load_lineage_invalid_json_returns_none(tmp_path: Path, caplog) -> None:
    art = tmp_path / "lineage.json"
    art.write_text("{ not valid json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        result = CiStore.load_lineage(art)
    assert result is None
    assert "ci_store_load_lineage_failed" in caplog.text


def test_load_lineage_valid_json_wrong_schema_returns_none(tmp_path: Path) -> None:
    art = tmp_path / "lineage.json"
    # Valid JSON but missing required lineage_id field.
    art.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    assert CiStore.load_lineage(art) is None


# ---------------------------------------------------------------------------
# Internal _load / _save behaviour
# ---------------------------------------------------------------------------


def test_load_returns_defaults_when_file_absent(store: CiStore) -> None:
    data = store._load()
    assert data == {"lineage_index": {}, "ci_state": {}}


def test_load_backfills_missing_keys(store: CiStore, store_path: Path) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    # File present but missing both top-level keys.
    store_path.write_text(json.dumps({"other": 1}), encoding="utf-8")
    data = store._load()
    assert data["lineage_index"] == {}
    assert data["ci_state"] == {}
    assert data["other"] == 1


def test_load_corrupt_file_returns_defaults_and_logs(
    store: CiStore, store_path: Path, caplog
) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("not json at all", encoding="utf-8")
    with caplog.at_level("WARNING"):
        data = store._load()
    assert data == {"lineage_index": {}, "ci_state": {}}
    assert "ci_store_load_failed" in caplog.text


def test_save_creates_parent_dirs(store: CiStore, store_path: Path) -> None:
    assert not store_path.parent.exists()
    store._save({"lineage_index": {}, "ci_state": {}})
    assert store_path.exists()
    assert store_path.parent.is_dir()


def test_save_writes_indented_unicode(store: CiStore, store_path: Path) -> None:
    store._save({"lineage_index": {"k": "válue-✓"}, "ci_state": {}})
    text = store_path.read_text(encoding="utf-8")
    # ensure_ascii=False keeps the non-ASCII chars literal.
    assert "válue-✓" in text
    # indent=2 produces newlines.
    assert "\n" in text


def test_default_path_is_state_ci_lineage() -> None:
    s = CiStore()
    assert s._path == Path("state/ci_lineage.json")


def test_load_after_save_roundtrips_internal(store: CiStore) -> None:
    payload = {"lineage_index": {"x": {"a": 1}}, "ci_state": {"y": {"b": 2}}}
    store._save(payload)
    assert store._load() == payload
