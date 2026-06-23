# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/projection.py — build the lineage read-model from on-disk signals.

Joins three families of artifacts the fleet already writes, on stable keys:

  * run artifacts   <runs_root>/<run_id>/{proposal,run_metadata,result}.json
                    → task_id, proposal_id, run_id, status, success, PR url
  * pr_reviews      <state_dir>/pr_reviews/<repo>-<n>.json
                    → plane_task_id, pr_number, head_sha, phase, verdict
  * ci_lineage      <state_dir>/ci_lineage.json   (keyed lin-<task_id[:12]>)

Nothing here writes; it only reads and joins. Every node/edge is trust-labeled:
edges whose lineage the durable tier vouches for are ``attested`` (steerable iff
code-computed), all others get the not-yet-attested default (never steerable).
So an edge becomes steerable ONLY once it is recorded in the durable/integrity
tier — the safe default for everything else.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import (
    Completeness,
    LineageChain,
    LineageEdge,
    LineageNode,
    Provenance,
    TrustFlags,
    attested_trust,
    default_trust,
)

_DEFAULT_RETENTION_DAYS = 44  # matches session/lineage source GC (see spec §1.3)
_PR_NUM_RE = re.compile(r"/pull/(\d+)")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _completeness(
    ts: datetime | None,
    *,
    now: datetime,
    retention_days: int,
    durable: bool = False,
) -> Completeness:
    """An edge older than the source retention window is not reproducible on a
    rebuild from source, so it is EXPIRED even if the file is still present —
    UNLESS the durable tier (Phase A5) vouches for its lineage, in which case it
    stays DURABLE because that tier outlives the source GC."""

    if durable:
        return Completeness.DURABLE
    if ts is None:
        return Completeness.EXPIRED
    if now - ts > timedelta(days=retention_days):
        return Completeness.EXPIRED
    return Completeness.DURABLE


def _pr_number_from_url(url: Any) -> int | None:
    if not isinstance(url, str):
        return None
    m = _PR_NUM_RE.search(url)
    return int(m.group(1)) if m else None


class _RunRecord:
    __slots__ = ("run_id", "task_id", "proposal_id", "meta", "result", "proposal")

    def __init__(self, run_dir: Path) -> None:
        self.proposal = _read_json(run_dir / "proposal.json") or {}
        self.meta = _read_json(run_dir / "run_metadata.json") or {}
        self.result = _read_json(run_dir / "result.json") or {}
        self.run_id = self.meta.get("run_id") or self.result.get("run_id") or run_dir.name
        self.task_id = self.proposal.get("task_id")
        self.proposal_id = self.proposal.get("proposal_id") or self.meta.get("proposal_id")


def _iter_runs(runs_root: Path) -> list[_RunRecord]:
    if not runs_root.is_dir():
        return []
    out: list[_RunRecord] = []
    for run_dir in sorted(runs_root.iterdir()):
        if run_dir.is_dir():
            out.append(_RunRecord(run_dir))
    return out


def _load_pr_reviews(state_dir: Path) -> list[dict[str, Any]]:
    pr_dir = state_dir / "pr_reviews"
    if not pr_dir.is_dir():
        return []
    states: list[dict[str, Any]] = []
    for path in sorted(pr_dir.glob("*.json")):
        data = _read_json(path)
        if data:
            states.append(data)
    return states


def build_chain(
    task_id: str,
    *,
    runs_root: Path,
    state_dir: Path,
    now: datetime | None = None,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    durable_lineage_ids: set[str] | None = None,
    _all_runs: list[_RunRecord] | None = None,
) -> LineageChain:
    """Assemble the lineage chain for one task_id from on-disk signals.

    ``durable_lineage_ids`` (Phase A5) is the set of lineage_ids the durable tier
    vouches for; an edge whose lineage is durable is ``attested`` — integrity
    CHAINED, completeness DURABLE, order CAUSAL — so a code-computed durable edge
    becomes steerable. ``_all_runs`` lets ``build_all`` scan the run dirs ONCE and
    share the parsed records (R5), instead of re-scanning per task.
    """

    now = now or datetime.now(timezone.utc)
    durable = (durable_lineage_ids is not None) and (f"lin-{task_id[:12]}" in durable_lineage_ids)
    all_runs = _all_runs if _all_runs is not None else _iter_runs(runs_root)
    runs = [r for r in all_runs if r.task_id == task_id]
    pr_states = _load_pr_reviews(state_dir)

    def _trust(provenance: Provenance, complete: Completeness) -> TrustFlags:
        # A durable-backed edge is attested (3 dims green); otherwise the
        # not-yet-attested default (integrity unverified, order host-relative).
        if durable:
            return attested_trust(provenance)
        return default_trust(provenance=provenance, completeness=complete)

    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    task_node_id = f"task:{task_id}"

    # ── Task node — defining content (goal) originates from an attacker-
    # controllable issue body, so the node is TEXT_DERIVED: never steerable.
    task_ts = None
    goal = None
    repo = None
    for r in runs:
        task_ts = task_ts or _parse_ts(r.meta.get("written_at"))
        goal = goal or r.proposal.get("goal_text")
        target = r.proposal.get("target") or {}
        repo = repo or (target.get("repo_key") if isinstance(target, dict) else None)
    task_complete = _completeness(task_ts, now=now, retention_days=retention_days, durable=durable)
    nodes.append(
        LineageNode(
            node_id=task_node_id,
            kind="task",
            trust=_trust(Provenance.TEXT_DERIVED, task_complete),
            attributes={"task_id": task_id, "repo_key": repo, "goal_text": goal},
        )
    )

    pr_numbers: set[int] = set()
    for r in runs:
        run_node_id = f"run:{r.run_id}"
        run_ts = _parse_ts(r.meta.get("written_at"))
        run_complete = _completeness(run_ts, now=now, retention_days=retention_days, durable=durable)
        # Run/proposal facts are machine-generated → code-computed.
        nodes.append(
            LineageNode(
                node_id=run_node_id,
                kind="run",
                trust=_trust(Provenance.CODE_COMPUTED, run_complete),
                attributes={
                    "run_id": r.run_id,
                    "proposal_id": r.proposal_id,
                    "status": r.meta.get("status"),
                    "success": r.meta.get("success"),
                    "selected_lane": r.meta.get("selected_lane"),
                    "failure_category": r.meta.get("failure_category"),
                },
            )
        )
        edges.append(
            LineageEdge(
                src=task_node_id,
                dst=run_node_id,
                kind="executed_as",
                trust=_trust(Provenance.CODE_COMPUTED, run_complete),
            )
        )
        pr_num = _pr_number_from_url(r.result.get("pull_request_url"))
        if pr_num is not None:
            pr_numbers.add(pr_num)
            edges.append(
                LineageEdge(
                    src=run_node_id,
                    dst=f"pr:{pr_num}",
                    kind="produced_pr",
                    trust=_trust(Provenance.CODE_COMPUTED, run_complete),
                )
            )

    # ── PR + verdict nodes — matched by direct plane_task_id link OR by a PR
    # number this task's runs produced.
    for state in pr_states:
        pr_num = state.get("pr_number")
        linked = state.get("plane_task_id") == task_id or pr_num in pr_numbers
        if not linked or pr_num is None:
            continue
        pr_node_id = f"pr:{pr_num}"
        pr_ts = _parse_ts(state.get("updated_at")) or _parse_ts(state.get("created_at"))
        pr_complete = _completeness(pr_ts, now=now, retention_days=retention_days, durable=durable)
        nodes.append(
            LineageNode(
                node_id=pr_node_id,
                kind="pr",
                trust=_trust(Provenance.CODE_COMPUTED, pr_complete),
                attributes={
                    "pr_number": pr_num,
                    "repo_key": state.get("repo_key"),
                    "phase": state.get("phase"),
                    "head_sha": state.get("head_sha") or state.get("escalated_head_sha"),
                },
            )
        )
        # If the task->pr link came only via plane_task_id (not a produced_pr
        # edge), connect it so the chain is traversable.
        if pr_num not in pr_numbers:
            edges.append(
                LineageEdge(
                    src=task_node_id,
                    dst=pr_node_id,
                    kind="produced_pr",
                    trust=_trust(Provenance.CODE_COMPUTED, pr_complete),
                )
            )
        verdict = state.get("verdict")
        if isinstance(verdict, dict) and verdict.get("result"):
            verdict_node_id = f"verdict:{pr_num}"
            # compute_verdict() output is code-computed — the one genuinely
            # code-computed steering candidate in the whole chain.
            nodes.append(
                LineageNode(
                    node_id=verdict_node_id,
                    kind="verdict",
                    trust=_trust(Provenance.CODE_COMPUTED, pr_complete),
                    attributes={
                        "result": verdict.get("result"),
                        "failing_checks": verdict.get("failing_checks"),
                    },
                )
            )
            edges.append(
                LineageEdge(
                    src=pr_node_id,
                    dst=verdict_node_id,
                    kind="reviewed_as",
                    trust=_trust(Provenance.CODE_COMPUTED, pr_complete),
                )
            )

    return LineageChain(task_id=task_id, nodes=tuple(nodes), edges=tuple(edges))


def build_all(
    *,
    runs_root: Path,
    state_dir: Path,
    now: datetime | None = None,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    durable_lineage_ids: set[str] | None = None,
) -> dict[str, LineageChain]:
    """Build chains for every task_id discoverable in the run artifacts."""

    now = now or datetime.now(timezone.utc)
    # R5: scan + parse the run dirs ONCE and share the records across every chain,
    # instead of re-scanning runs_root per task (which was O(tasks × runs)).
    all_runs = _iter_runs(runs_root)
    task_ids = {r.task_id for r in all_runs if r.task_id}
    # Tasks that only show up in pr_reviews (via plane_task_id) also count.
    for state in _load_pr_reviews(state_dir):
        tid = state.get("plane_task_id")
        if tid:
            task_ids.add(tid)
    return {
        tid: build_chain(
            tid,
            runs_root=runs_root,
            state_dir=state_dir,
            now=now,
            retention_days=retention_days,
            durable_lineage_ids=durable_lineage_ids,
            _all_runs=all_runs,
        )
        for tid in sorted(task_ids)
    }
