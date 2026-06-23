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
