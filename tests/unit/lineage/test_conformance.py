# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Rebuild-conformance gate (Phase A4, determinism surface 4 / spec §1.3, A4).

Pins the property the spec demands: a rebuild from source is deterministic, and
an edge whose source has aged past the GC horizon is marked EXPIRED (not silently
dropped) UNLESS the durable tier (A5) vouches for it. This is the gate that "fails
today, passes after A5" — here it passes precisely because A5 is wired.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.lineage import Completeness, build_all, build_chain
from operations_center.lineage.durable import DurableLineageStore, lineage_id_for_task

_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)
_OLD_TASK = "old00000-1111-2222"
_FRESH_TASK = "new00000-3333-4444"


def _write_run(runs: Path, *, run_id: str, task_id: str, written_at: datetime) -> None:
    d = runs / run_id
    d.mkdir(parents=True)
    (d / "proposal.json").write_text(
        json.dumps({"proposal_id": "p", "task_id": task_id, "goal_text": "g", "target": {}})
    )
    (d / "run_metadata.json").write_text(
        json.dumps({"run_id": run_id, "status": "succeeded", "success": True,
                    "written_at": written_at.isoformat()})
    )
    (d / "result.json").write_text(json.dumps({"run_id": run_id, "pull_request_url": None}))


def _corpus(tmp_path: Path) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    state = tmp_path / "state"
    _write_run(runs, run_id="r-old", task_id=_OLD_TASK, written_at=_NOW - timedelta(days=90))
    _write_run(runs, run_id="r-new", task_id=_FRESH_TASK, written_at=_NOW - timedelta(days=2))
    return runs, state


def _run_completeness(chain) -> Completeness:
    return next(n for n in chain.nodes if n.kind == "run").trust.completeness


def test_rebuild_is_deterministic(tmp_path: Path):
    runs, state = _corpus(tmp_path)
    a = build_all(runs_root=runs, state_dir=state, now=_NOW)
    b = build_all(runs_root=runs, state_dir=state, now=_NOW)
    assert {k: v.as_dict() for k, v in a.items()} == {k: v.as_dict() for k, v in b.items()}


def test_aged_source_is_expired_not_dropped(tmp_path: Path):
    runs, state = _corpus(tmp_path)
    chains = build_all(runs_root=runs, state_dir=state, now=_NOW, retention_days=44)
    # the old task still APPEARS (not silently dropped) but is marked expired
    assert _OLD_TASK in chains
    assert _run_completeness(chains[_OLD_TASK]) is Completeness.EXPIRED
    # the fresh one is durable
    assert _run_completeness(chains[_FRESH_TASK]) is Completeness.DURABLE


def test_durable_tier_keeps_aged_lineage_durable(tmp_path: Path):
    runs, state = _corpus(tmp_path)
    # A5: record the OLD task's lineage in the durable tier
    store = DurableLineageStore(tmp_path / "ledger.jsonl")
    store.append(lineage_id_for_task(_OLD_TASK), "goal-lane", {"step": "merged"})

    chain = build_chain(
        _OLD_TASK,
        runs_root=runs,
        state_dir=state,
        now=_NOW,
        retention_days=44,
        durable_lineage_ids=store.durable_lineage_ids(),
    )
    # now the aged source is DURABLE because the durable tier outlives source GC
    assert _run_completeness(chain) is Completeness.DURABLE


def test_durable_tier_does_not_rescue_untracked_lineage(tmp_path: Path):
    runs, state = _corpus(tmp_path)
    store = DurableLineageStore(tmp_path / "ledger.jsonl")
    store.append(lineage_id_for_task(_FRESH_TASK), "goal-lane", {})  # only the fresh one
    chain = build_chain(
        _OLD_TASK, runs_root=runs, state_dir=state, now=_NOW, retention_days=44,
        durable_lineage_ids=store.durable_lineage_ids(),
    )
    assert _run_completeness(chain) is Completeness.EXPIRED


def test_durable_code_computed_edge_becomes_steerable(tmp_path: Path):
    # A1: once an edge is backed by the durable tier it is attested (chained +
    # durable + causal), so a CODE-COMPUTED edge becomes steerable — the model is
    # no longer inert-by-construction.
    runs, state = _corpus(tmp_path)
    store = DurableLineageStore(tmp_path / "ledger.jsonl")
    store.append(lineage_id_for_task(_OLD_TASK), "board_worker", {"step": "done"})
    chain = build_chain(
        _OLD_TASK,
        runs_root=runs,
        state_dir=state,
        now=_NOW,
        durable_lineage_ids=store.durable_lineage_ids(),
    )
    steerable_kinds = {e.kind for e in chain.steerable_edges()}
    assert "executed_as" in steerable_kinds  # task->run, code-computed, now attested


def test_without_durable_backing_nothing_is_steerable(tmp_path: Path):
    runs, state = _corpus(tmp_path)
    chain = build_chain(_FRESH_TASK, runs_root=runs, state_dir=state, now=_NOW)
    assert chain.steerable_edges() == ()
