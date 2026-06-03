# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.proposer.rejection_store import ProposalRejectionStore


def _store(tmp_path: Path) -> ProposalRejectionStore:
    return ProposalRejectionStore(path=tmp_path / "rej.json")


# --------------------------------------------------------------------------- #
#  __init__                                                                     #
# --------------------------------------------------------------------------- #


def test_init_explicit_path(tmp_path: Path) -> None:
    p = tmp_path / "custom.json"
    store = ProposalRejectionStore(path=p)
    assert store.path == p


def test_init_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "fromenv.json"
    monkeypatch.setenv("OPERATIONS_CENTER_REJECTION_STORE_PATH", str(target))
    store = ProposalRejectionStore()
    assert store.path == Path(str(target))


def test_init_default_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPERATIONS_CENTER_REJECTION_STORE_PATH", raising=False)
    store = ProposalRejectionStore()
    assert store.path == Path("state/proposal_rejections.json")


# --------------------------------------------------------------------------- #
#  is_rejected                                                                  #
# --------------------------------------------------------------------------- #


def test_is_rejected_empty_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.is_rejected("anything") is False


def test_is_rejected_true(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("Key-A", reason="r", task_id="t1")
    assert store.is_rejected("Key-A") is True


def test_is_rejected_case_and_whitespace_insensitive(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("Key-A", reason="r", task_id="t1")
    assert store.is_rejected("  key-a  ") is True


def test_is_rejected_missing_dedup_key_field(tmp_path: Path) -> None:
    p = tmp_path / "rej.json"
    p.write_text(json.dumps([{"reason": "x"}]), encoding="utf-8")
    store = ProposalRejectionStore(path=p)
    # record without dedup_key falls back to "" -> only matches "" key
    assert store.is_rejected("foo") is False
    assert store.is_rejected("") is True


def test_is_rejected_unmatched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("here", reason="r", task_id="t1")
    assert store.is_rejected("absent") is False


# --------------------------------------------------------------------------- #
#  record_rejection                                                             #
# --------------------------------------------------------------------------- #


def test_record_rejection_writes_record(tmp_path: Path) -> None:
    p = tmp_path / "rej.json"
    store = ProposalRejectionStore(path=p)
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    store.record_rejection(
        "Key-X",
        reason="manual cancel",
        task_id="task-9",
        task_title="Fix things",
        now=fixed,
    )
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data) == 1
    rec = data[0]
    assert rec["dedup_key"] == "Key-X"
    assert rec["reason"] == "manual cancel"
    assert rec["task_id"] == "task-9"
    assert rec["task_title"] == "Fix things"
    assert rec["recorded_at"] == fixed.isoformat()


def test_record_rejection_default_title(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("k", reason="r", task_id="t")
    assert store.all_rejections()[0]["task_title"] == ""


def test_record_rejection_default_now(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = datetime.now(timezone.utc)
    store.record_rejection("k", reason="r", task_id="t")
    recorded = datetime.fromisoformat(store.all_rejections()[0]["recorded_at"])
    after = datetime.now(timezone.utc)
    assert before <= recorded <= after


def test_record_rejection_dedup_no_double(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("Key-A", reason="first", task_id="t1")
    store.record_rejection("  key-a ", reason="second", task_id="t2")
    recs = store.all_rejections()
    assert len(recs) == 1
    assert recs[0]["reason"] == "first"


def test_record_rejection_dedup_skips_save(tmp_path: Path) -> None:
    p = tmp_path / "rej.json"
    store = ProposalRejectionStore(path=p)
    store.record_rejection("dup", reason="r", task_id="t1")
    mtime = p.stat().st_mtime_ns
    # second identical record should early-return without re-saving
    store.record_rejection("dup", reason="r2", task_id="t2")
    assert p.stat().st_mtime_ns == mtime


def test_record_rejection_appends_distinct(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("a", reason="r", task_id="t1")
    store.record_rejection("b", reason="r", task_id="t2")
    keys = {r["dedup_key"] for r in store.all_rejections()}
    assert keys == {"a", "b"}


def test_record_rejection_dedup_against_record_missing_key(tmp_path: Path) -> None:
    p = tmp_path / "rej.json"
    p.write_text(json.dumps([{"reason": "x"}]), encoding="utf-8")
    store = ProposalRejectionStore(path=p)
    # existing record has no dedup_key (-> ""); recording "" should dedup
    store.record_rejection("", reason="r", task_id="t")
    assert len(store.all_rejections()) == 1


# --------------------------------------------------------------------------- #
#  all_rejections                                                               #
# --------------------------------------------------------------------------- #


def test_all_rejections_empty(tmp_path: Path) -> None:
    assert _store(tmp_path).all_rejections() == []


def test_all_rejections_returns_new_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_rejection("k", reason="r", task_id="t")
    a = store.all_rejections()
    a.append({"junk": True})
    # mutation of returned list must not affect store
    assert len(store.all_rejections()) == 1


# --------------------------------------------------------------------------- #
#  _load branches                                                               #
# --------------------------------------------------------------------------- #


def test_load_missing_file(tmp_path: Path) -> None:
    store = ProposalRejectionStore(path=tmp_path / "nope.json")
    assert store.all_rejections() == []


def test_load_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    store = ProposalRejectionStore(path=p)
    assert store.all_rejections() == []


def test_load_non_list_json(tmp_path: Path) -> None:
    p = tmp_path / "obj.json"
    p.write_text(json.dumps({"dedup_key": "x"}), encoding="utf-8")
    store = ProposalRejectionStore(path=p)
    assert store.all_rejections() == []


def test_load_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "rej.json"
    p.write_text(json.dumps([{"dedup_key": "k"}]), encoding="utf-8")
    store = ProposalRejectionStore(path=p)

    def _boom(*_a: object, **_k: object) -> str:
        raise OSError("read failed")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert store.all_rejections() == []


def test_load_valid_list(tmp_path: Path) -> None:
    p = tmp_path / "ok.json"
    p.write_text(json.dumps([{"dedup_key": "k", "reason": "r"}]), encoding="utf-8")
    store = ProposalRejectionStore(path=p)
    recs = store.all_rejections()
    assert recs == [{"dedup_key": "k", "reason": "r"}]


# --------------------------------------------------------------------------- #
#  _save branches                                                               #
# --------------------------------------------------------------------------- #


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "rej.json"
    store = ProposalRejectionStore(path=nested)
    store.record_rejection("k", reason="r", task_id="t")
    assert nested.exists()
    assert json.loads(nested.read_text(encoding="utf-8"))[0]["dedup_key"] == "k"


def test_save_unicode_preserved(tmp_path: Path) -> None:
    p = tmp_path / "u.json"
    store = ProposalRejectionStore(path=p)
    store.record_rejection("café-naïve", reason="señor", task_id="t")
    text = p.read_text(encoding="utf-8")
    assert "café-naïve" in text
    assert "señor" in text


def test_save_no_leftover_tmp(tmp_path: Path) -> None:
    p = tmp_path / "rej.json"
    store = ProposalRejectionStore(path=p)
    store.record_rejection("k", reason="r", task_id="t")
    assert not p.with_suffix(".tmp").exists()


def test_round_trip_persists_across_instances(tmp_path: Path) -> None:
    p = tmp_path / "rej.json"
    ProposalRejectionStore(path=p).record_rejection("k", reason="r", task_id="t")
    assert ProposalRejectionStore(path=p).is_rejected("k") is True
