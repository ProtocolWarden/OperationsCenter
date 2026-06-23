# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the durable lineage tier (Phase A5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.lineage.durable import DurableLineageStore, lineage_id_for_task
from operations_center.lineage.integrity import AuthorshipError


def test_append_persists_and_reloads(tmp_path: Path):
    p = tmp_path / "ledger.jsonl"
    store = DurableLineageStore(p)
    store.append("lin-a", "goal-lane", {"step": "proposed"})
    store.append("lin-a", "goal-lane", {"step": "merged"})

    # a fresh store over the same file sees the persisted entries
    reloaded = DurableLineageStore(p)
    assert reloaded.verify()
    assert len(reloaded.entries) == 2
    assert reloaded.durable_lineage_ids() == {"lin-a"}


def test_authorship_binding_persists_across_reload(tmp_path: Path):
    p = tmp_path / "ledger.jsonl"
    store = DurableLineageStore(p)
    store.append("lin-a", "goal-lane", {"x": 1})
    with pytest.raises(AuthorshipError):
        store.append("lin-a", "attacker", {"x": "forged"})
    # the forged entry was neither chained nor persisted
    reloaded = DurableLineageStore(p)
    assert [e.author for e in reloaded.entries] == ["goal-lane"]


def test_tampered_file_vouches_for_nothing(tmp_path: Path):
    p = tmp_path / "ledger.jsonl"
    store = DurableLineageStore(p)
    store.append("lin-a", "goal-lane", {"step": "one"})
    store.append("lin-a", "goal-lane", {"step": "two"})
    # tamper with a persisted line
    lines = p.read_text().splitlines()
    p.write_text(lines[0].replace("one", "ONE-TAMPERED") + "\n" + lines[1] + "\n")

    reloaded = DurableLineageStore(p)
    # a broken chain must vouch for NOTHING (degrade to source-age semantics)
    assert reloaded.durable_lineage_ids() == set()


def test_missing_file_is_empty(tmp_path: Path):
    store = DurableLineageStore(tmp_path / "nope.jsonl")
    assert store.entries == []
    assert store.durable_lineage_ids() == set()


def test_lineage_id_for_task_matches_fleet_format():
    # mirrors outcomes.py: f"lin-{task_id[:12]}" (first 12 chars, hyphens incl.)
    assert lineage_id_for_task("11111111-2222-3333") == "lin-11111111-222"


# ── R3 concurrency + R4 canonicalization ──────────────────────────────────────


def test_reload_under_lock_prevents_lost_writes(tmp_path: Path):
    # Two stores constructed BEFORE any append both hold stale empty snapshots.
    # Because append reloads under the lock, the second writer sees the first's
    # entry and chains after it — neither write is lost.
    p = tmp_path / "ledger.jsonl"
    a = DurableLineageStore(p)
    b = DurableLineageStore(p)
    a.append("lin-a", "auth", {"x": 1})
    b.append("lin-b", "auth", {"y": 2})
    reloaded = DurableLineageStore(p)
    assert reloaded.verify()
    assert {e.lineage_id for e in reloaded.entries} == {"lin-a", "lin-b"}


def test_same_lineage_concurrent_appenders_chain(tmp_path: Path):
    p = tmp_path / "ledger.jsonl"
    a = DurableLineageStore(p)
    b = DurableLineageStore(p)
    a.append("lin-x", "auth", {"n": 1})
    b.append("lin-x", "auth", {"n": 2})  # reloads, chains off a's tip
    reloaded = DurableLineageStore(p)
    assert reloaded.verify()
    assert len([e for e in reloaded.entries if e.lineage_id == "lin-x"]) == 2


def test_non_json_native_payload_survives_verify(tmp_path: Path):
    # A tuple value + non-str key would hash differently after a JSON reload;
    # canonicalization-before-hashing keeps verify() true.
    p = tmp_path / "ledger.jsonl"
    store = DurableLineageStore(p)
    store.append("lin-a", "auth", {"items": (1, 2), 3: "x"})
    reloaded = DurableLineageStore(p)
    assert reloaded.verify()
    assert reloaded.durable_lineage_ids() == {"lin-a"}


# ── F1 producer ────────────────────────────────────────────────────────────────


def test_record_task_completion_writes_typed_fields(tmp_path: Path):
    from operations_center.lineage.durable import record_task_completion

    record_task_completion(
        tmp_path,
        "task-abc-123456",
        {"run_id": "r1", "status": "succeeded", "pull_request_url": "https://x/pull/9",
         "goal_text": "SECRET"},
    )
    store = DurableLineageStore(tmp_path / "state" / "lineage" / "ledger.jsonl")
    assert store.durable_lineage_ids() == {lineage_id_for_task("task-abc-123456")}
    payload = store.entries[0].payload
    assert payload["status"] == "succeeded" and payload["pr_url"] == "https://x/pull/9"
    assert "goal_text" not in payload  # free text never recorded


def test_record_task_completion_is_best_effort(tmp_path: Path):
    from operations_center.lineage.durable import record_task_completion

    # an unwritable path must not raise (returns None)
    assert record_task_completion(tmp_path / "bad\x00path", "t", {"status": "x"}) is None
