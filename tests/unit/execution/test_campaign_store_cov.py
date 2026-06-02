# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations_center.execution.campaign_store import (
    CampaignRecord,
    CampaignStore,
    _compute_status,
    _refresh_computed,
)


def _fixed(year: int = 2026, month: int = 6, day: int = 2) -> datetime:
    return datetime(year, month, day, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "campaigns.json"


@pytest.fixture
def store(store_path: Path) -> CampaignStore:
    return CampaignStore(path=store_path)


# --------------------------------------------------------------------------
# CampaignRecord
# --------------------------------------------------------------------------
def _record(**kw) -> CampaignRecord:
    base = dict(
        source_task_id="t1",
        title="Title",
        step_task_ids=["a", "b", "c"],
        done_step_ids=[],
        cancelled_step_ids=[],
        created_at="2026-06-02T12:00:00+00:00",
        updated_at="2026-06-02T12:00:00+00:00",
        status="in_progress",
    )
    base.update(kw)
    return CampaignRecord(**base)


def test_record_properties_with_steps():
    rec = _record(done_step_ids=["a", "b"])
    assert rec.total_steps == 3
    assert rec.completed_steps == 2
    assert rec.progress_pct == pytest.approx(66.7)


def test_record_progress_pct_empty_steps():
    rec = _record(step_task_ids=[])
    assert rec.total_steps == 0
    assert rec.completed_steps == 0
    assert rec.progress_pct == 0.0


def test_record_to_dict_includes_computed():
    rec = _record(done_step_ids=["a"])
    d = rec.to_dict()
    assert d["source_task_id"] == "t1"
    assert d["total_steps"] == 3
    assert d["completed_steps"] == 1
    assert d["progress_pct"] == pytest.approx(33.3)
    # base dataclass fields preserved
    assert d["step_task_ids"] == ["a", "b", "c"]
    assert d["status"] == "in_progress"


# --------------------------------------------------------------------------
# CampaignStore.create
# --------------------------------------------------------------------------
def test_create_new_campaign(store: CampaignStore, store_path: Path):
    cid = store.create(
        source_task_id="abc",
        title="Refactor",
        step_task_ids=["s1", "s2"],
        now=_fixed(),
    )
    assert cid == "abc"
    assert store_path.exists()
    rec = store.get("abc")
    assert rec is not None
    assert rec["title"] == "Refactor"
    assert rec["step_task_ids"] == ["s1", "s2"]
    assert rec["done_step_ids"] == []
    assert rec["cancelled_step_ids"] == []
    assert rec["status"] == "in_progress"
    assert rec["created_at"] == _fixed().isoformat()
    assert rec["updated_at"] == _fixed().isoformat()
    assert rec["total_steps"] == 2
    assert rec["completed_steps"] == 0
    assert rec["progress_pct"] == 0.0


def test_create_idempotent_noop_returns_existing(store: CampaignStore):
    store.create(source_task_id="abc", title="First", step_task_ids=["s1"], now=_fixed())
    cid = store.create(source_task_id="abc", title="Second", step_task_ids=["x"], now=_fixed(2027))
    assert cid == "abc"
    rec = store.get("abc")
    # unchanged: still the first campaign
    assert rec["title"] == "First"
    assert rec["step_task_ids"] == ["s1"]


def test_create_uses_now_default(monkeypatch, store: CampaignStore):
    import operations_center.execution.campaign_store as mod

    class _DT:
        @staticmethod
        def now(tz=None):
            return _fixed(2025)

    monkeypatch.setattr(mod, "datetime", _DT)
    store.create(source_task_id="d", title="T", step_task_ids=[])
    rec = store.get("d")
    assert rec["created_at"] == _fixed(2025).isoformat()


def test_create_copies_step_list(store: CampaignStore):
    steps = ["s1", "s2"]
    store.create(source_task_id="c", title="T", step_task_ids=steps, now=_fixed())
    steps.append("mutated")
    rec = store.get("c")
    assert rec["step_task_ids"] == ["s1", "s2"]


# --------------------------------------------------------------------------
# record_step_done
# --------------------------------------------------------------------------
def test_record_step_done_marks_and_completes(store: CampaignStore):
    store.create(source_task_id="c", title="T", step_task_ids=["s1", "s2"], now=_fixed())
    store.record_step_done("c", step_task_id="s1", now=_fixed())
    rec = store.get("c")
    assert rec["done_step_ids"] == ["s1"]
    assert rec["status"] == "partial"
    assert rec["completed_steps"] == 1
    assert rec["progress_pct"] == pytest.approx(50.0)

    store.record_step_done("c", step_task_id="s2", now=_fixed())
    rec = store.get("c")
    assert rec["status"] == "completed"
    assert rec["progress_pct"] == 100.0


def test_record_step_done_unknown_campaign_noop(store: CampaignStore):
    # no campaigns exist; should not raise and not create anything
    store.record_step_done("missing", step_task_id="s1", now=_fixed())
    assert store.get("missing") is None


def test_record_step_done_duplicate_not_appended(store: CampaignStore):
    store.create(source_task_id="c", title="T", step_task_ids=["s1", "s2"], now=_fixed())
    store.record_step_done("c", step_task_id="s1", now=_fixed())
    store.record_step_done("c", step_task_id="s1", now=_fixed())
    rec = store.get("c")
    assert rec["done_step_ids"] == ["s1"]


def test_record_step_done_default_now(monkeypatch, store: CampaignStore):
    import operations_center.execution.campaign_store as mod

    store.create(source_task_id="c", title="T", step_task_ids=["s1"], now=_fixed())

    class _DT:
        @staticmethod
        def now(tz=None):
            return _fixed(2099)

    monkeypatch.setattr(mod, "datetime", _DT)
    store.record_step_done("c", step_task_id="s1")
    rec = store.get("c")
    assert rec["updated_at"] == _fixed(2099).isoformat()


# --------------------------------------------------------------------------
# record_step_cancelled
# --------------------------------------------------------------------------
def test_record_step_cancelled_partial(store: CampaignStore):
    store.create(source_task_id="c", title="T", step_task_ids=["s1", "s2"], now=_fixed())
    store.record_step_cancelled("c", step_task_id="s1", now=_fixed())
    rec = store.get("c")
    assert rec["cancelled_step_ids"] == ["s1"]
    assert rec["status"] == "partial"


def test_record_step_cancelled_all_cancelled(store: CampaignStore):
    store.create(source_task_id="c", title="T", step_task_ids=["s1", "s2"], now=_fixed())
    store.record_step_cancelled("c", step_task_id="s1", now=_fixed())
    store.record_step_cancelled("c", step_task_id="s2", now=_fixed())
    rec = store.get("c")
    assert rec["status"] == "cancelled"


def test_record_step_cancelled_unknown_noop(store: CampaignStore):
    store.record_step_cancelled("nope", step_task_id="s1", now=_fixed())
    assert store.get("nope") is None


def test_record_step_cancelled_duplicate_not_appended(store: CampaignStore):
    store.create(source_task_id="c", title="T", step_task_ids=["s1", "s2"], now=_fixed())
    store.record_step_cancelled("c", step_task_id="s1", now=_fixed())
    store.record_step_cancelled("c", step_task_id="s1", now=_fixed())
    rec = store.get("c")
    assert rec["cancelled_step_ids"] == ["s1"]


def test_record_step_cancelled_default_now(monkeypatch, store: CampaignStore):
    import operations_center.execution.campaign_store as mod

    store.create(source_task_id="c", title="T", step_task_ids=["s1"], now=_fixed())

    class _DT:
        @staticmethod
        def now(tz=None):
            return _fixed(2088)

    monkeypatch.setattr(mod, "datetime", _DT)
    store.record_step_cancelled("c", step_task_id="s1")
    rec = store.get("c")
    assert rec["updated_at"] == _fixed(2088).isoformat()


# --------------------------------------------------------------------------
# get
# --------------------------------------------------------------------------
def test_get_missing_returns_none(store: CampaignStore):
    assert store.get("none") is None


def test_get_returns_copy(store: CampaignStore):
    store.create(source_task_id="c", title="T", step_task_ids=["s1"], now=_fixed())
    rec = store.get("c")
    rec["title"] = "mutated"
    assert store.get("c")["title"] == "T"


# --------------------------------------------------------------------------
# list_campaigns
# --------------------------------------------------------------------------
def test_list_empty(store: CampaignStore):
    assert store.list_campaigns() == []


def test_list_sorted_by_created_desc(store: CampaignStore):
    store.create(source_task_id="old", title="Old", step_task_ids=["s"], now=_fixed(2024))
    store.create(source_task_id="new", title="New", step_task_ids=["s"], now=_fixed(2026))
    rows = store.list_campaigns()
    assert [r["source_task_id"] for r in rows] == ["new", "old"]


def test_list_filter_by_status(store: CampaignStore):
    store.create(source_task_id="a", title="A", step_task_ids=["s1"], now=_fixed())
    store.create(source_task_id="b", title="B", step_task_ids=["s1"], now=_fixed())
    store.record_step_done("a", step_task_id="s1", now=_fixed())  # completed
    completed = store.list_campaigns(status="completed")
    assert [r["source_task_id"] for r in completed] == ["a"]
    in_prog = store.list_campaigns(status="in_progress")
    assert [r["source_task_id"] for r in in_prog] == ["b"]


# --------------------------------------------------------------------------
# _load error handling / persistence
# --------------------------------------------------------------------------
def test_load_missing_file_returns_empty(store: CampaignStore):
    assert store._load() == {}


def test_load_corrupt_json_warns_and_returns_empty(store_path: Path, caplog):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{ not valid json", encoding="utf-8")
    store = CampaignStore(path=store_path)
    with caplog.at_level("WARNING"):
        result = store._load()
    assert result == {}
    assert "campaign_store_load_failed" in caplog.text


def test_save_creates_parent_dirs(tmp_path: Path):
    path = tmp_path / "deep" / "nested" / "campaigns.json"
    store = CampaignStore(path=path)
    store.create(source_task_id="c", title="T", step_task_ids=["s1"], now=_fixed())
    assert path.exists()


def test_persistence_across_instances(store_path: Path):
    s1 = CampaignStore(path=store_path)
    s1.create(source_task_id="c", title="T", step_task_ids=["s1"], now=_fixed())
    s2 = CampaignStore(path=store_path)
    assert s2.get("c")["title"] == "T"


# --------------------------------------------------------------------------
# _compute_status
# --------------------------------------------------------------------------
def test_compute_status_in_progress():
    assert (
        _compute_status(
            {"step_task_ids": ["a", "b"], "done_step_ids": [], "cancelled_step_ids": []}
        )
        == "in_progress"
    )


def test_compute_status_completed():
    assert (
        _compute_status(
            {"step_task_ids": ["a", "b"], "done_step_ids": ["a", "b"], "cancelled_step_ids": []}
        )
        == "completed"
    )


def test_compute_status_cancelled():
    assert (
        _compute_status(
            {"step_task_ids": ["a", "b"], "done_step_ids": [], "cancelled_step_ids": ["a", "b"]}
        )
        == "cancelled"
    )


def test_compute_status_partial():
    assert (
        _compute_status(
            {"step_task_ids": ["a", "b", "c"], "done_step_ids": ["a"], "cancelled_step_ids": ["b"]}
        )
        == "partial"
    )


def test_compute_status_zero_total_in_progress():
    # total == 0 => never completed/cancelled; with no done/cancelled => in_progress
    assert (
        _compute_status({"step_task_ids": [], "done_step_ids": [], "cancelled_step_ids": []})
        == "in_progress"
    )


def test_compute_status_completed_precedence_over_cancelled():
    # done >= total checked first
    assert (
        _compute_status(
            {"step_task_ids": ["a"], "done_step_ids": ["a"], "cancelled_step_ids": ["a"]}
        )
        == "completed"
    )


def test_compute_status_missing_keys_defaults():
    assert _compute_status({}) == "in_progress"


# --------------------------------------------------------------------------
# _refresh_computed
# --------------------------------------------------------------------------
def test_refresh_computed_with_steps():
    rec = {"step_task_ids": ["a", "b", "c"], "done_step_ids": ["a", "b"]}
    _refresh_computed(rec)
    assert rec["total_steps"] == 3
    assert rec["completed_steps"] == 2
    assert rec["progress_pct"] == pytest.approx(66.7)


def test_refresh_computed_zero_total():
    rec = {"step_task_ids": [], "done_step_ids": []}
    _refresh_computed(rec)
    assert rec["total_steps"] == 0
    assert rec["completed_steps"] == 0
    assert rec["progress_pct"] == 0.0
