# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# src/operations_center/spec_author/phase_orchestrator.py
"""Phase orchestrator — detection-only (ADR 0007 Phase D).

After Phase D, this module performs only LLM-free Plane state transitions and
emits structured ``PendingPhaseAdvance`` records describing campaigns whose
current phase has reached terminal state and whose next phase should begin.

The caller (``spec_hygiene``) is responsible for creating ``spec-author`` Plane
tasks with ``task_phase`` set for each pending advance; ``board_worker`` then
executes the actual spec rewrite through the normal backend executor pipeline
(no direct Claude subprocess, no ``_claude_cli`` import).

What this module DOES still do synchronously:
    * Promote backlog test/improve tasks to "Ready for AI" when their
      predecessor phase is fully terminal.
    * Close out a campaign (parent → Done, lifecycle: archived label) when
      all child tasks are terminal.

What this module no longer does (removed in Phase D):
    * Call ``_claude_cli.call_claude`` to rewrite spec text between phases.
    * Rewrite blocked-task descriptions via Claude. Blocked-task auto-recovery
      via LLM rewrite is retired with this refactor; if it returns it will be
      built on the same Plane → board_worker → backend pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from operations_center.spec_author.state import CampaignStateManager

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({"done", "cancelled"})


def _status(issue: dict) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).lower()
    return str(state or "").lower()


def _labels(issue: dict) -> list[str]:
    raw = issue.get("labels", [])
    result = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict):
                n = r.get("name")
                if n:
                    result.append(str(n))
            elif r:
                result.append(str(r))
    return result


def _campaign_id_from_issue(issue: dict) -> str | None:
    for lbl in _labels(issue):
        if lbl.lower().startswith("campaign-id:"):
            return lbl.split(":", 1)[1].strip()
    return None


def _task_kind(issue: dict) -> str:
    for lbl in _labels(issue):
        if lbl.strip().lower().startswith("task-kind:"):
            return lbl.split(":", 1)[1].strip().lower()
    return "goal"


@dataclass
class PendingPhaseAdvance:
    """A campaign whose current phase has completed and whose next phase
    should be authored. Emitted by ``PhaseOrchestrator.detect_pending_advances``;
    consumed by ``spec_hygiene`` to create a Plane ``spec-author`` task with
    ``task_phase`` set.

    Carries everything the spec rewrite prompt needs without forcing the caller
    to re-derive it from the issues list.
    """
    campaign_id: str
    spec_slug: str
    spec_file_path: str
    current_phase: str
    next_phase: str
    # Compact snapshot of each child task: (kind, status, title).
    # Goes into the seed_text so the rewrite prompt sees the phase state.
    task_summaries: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class PhaseOrchestrationResult:
    phases_advanced: int = 0
    campaigns_completed: int = 0
    pending_advances: list[PendingPhaseAdvance] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # Back-compat shims for legacy callers; both are always zero post-Phase D.
    # (The spec_director legacy entrypoint that read these was deleted in
    # Phase F; fields kept so spec_hygiene / spec-author can be reverted
    # without a payload schema change.)
    tasks_unblocked: int = 0
    tasks_cancelled: int = 0


# Ordered phase progression. Keys are the task-kind labels present on Plane
# issues; values are the next-phase identifier emitted in the
# ``task_phase`` field of the spec-author payload.
_PHASE_ADVANCE_CHAIN: list[tuple[str, str]] = [
    ("goal",            "test"),
    ("test_campaign",   "improve"),
]


class PhaseOrchestrator:
    """Detection-only orchestrator (ADR 0007 Phase D).

    Per-cycle responsibilities:
      1. Promote backlog tasks in the next phase to "Ready for AI" when the
         current phase is fully terminal. (No LLM.)
      2. Close out campaigns when every child task is terminal. (No LLM.)
      3. Emit ``PendingPhaseAdvance`` records for the spec-rewrite step;
         caller creates Plane tasks for the backend to execute.
    """

    def __init__(
        self,
        client: Any,
        state_manager: CampaignStateManager,
        specs_dir: Path,
        # Kept for back-compat with existing call sites; unused now that the
        # rewrite path is gone. (spec_director entrypoint retired in Phase F;
        # arg retained to avoid churning every existing caller.)
        max_rewrite_attempts: int = 2,  # noqa: ARG002 — kept for signature compat
    ) -> None:
        self._client = client
        self._state = state_manager
        self._specs_dir = specs_dir

    def detect_pending_advances(self, issues: list[dict]) -> list[PendingPhaseAdvance]:
        """Return campaigns where the current phase is complete and the next
        should begin. Caller is responsible for creating Plane tasks via the
        spec-author handler (ADR 0007 Phase D)."""
        result = self.run(issues)
        return result.pending_advances

    def run(self, issues: list[dict]) -> PhaseOrchestrationResult:
        result = PhaseOrchestrationResult()
        active = self._state.load()
        for campaign in active.active_campaigns():
            try:
                self._orchestrate(campaign, issues, result)
            except Exception as exc:
                logger.error(
                    '{"event": "phase_orchestrator_error", "campaign_id": "%s", "error": "%s"}',
                    campaign.campaign_id, str(exc),
                )
                result.errors.append(f"{campaign.campaign_id}: {exc}")
        return result

    def _orchestrate(
        self,
        campaign: Any,
        issues: list[dict],
        result: PhaseOrchestrationResult,
    ) -> None:
        campaign_id = campaign.campaign_id
        by_phase: dict[str, list[dict]] = {
            "goal": [],
            "test_campaign": [],
            "improve_campaign": [],
            "parent": [],
        }
        for issue in issues:
            if _campaign_id_from_issue(issue) != campaign_id:
                continue
            if str(issue.get("name", "")).startswith("[Campaign]"):
                by_phase["parent"].append(issue)
            else:
                kind = _task_kind(issue)
                bucket = kind if kind in by_phase else "goal"
                by_phase[bucket].append(issue)

        # Phase promotion (no LLM): when phase N is terminal, transition
        # backlog tasks in phase N+1 to Ready for AI so the backend picks
        # them up. The spec rewrite itself is emitted as a PendingPhaseAdvance
        # for the caller to enqueue as a spec-author task.
        for current_kind, next_phase_id in _PHASE_ADVANCE_CHAIN:
            current = by_phase[current_kind]
            if not (current and self._all_terminal(current)):
                continue
            # Map next_phase_id ("test" / "improve") to the bucket.
            next_kind = "test_campaign" if next_phase_id == "test" else "improve_campaign"
            backlog_next = [i for i in by_phase[next_kind] if _status(i) == "backlog"]
            for issue in backlog_next:
                self._client.transition_issue(str(issue["id"]), "Ready for AI")
                result.phases_advanced += 1
            if backlog_next:
                self._comment_parent(
                    by_phase["parent"],
                    f"Advancing to {next_phase_id} phase: {len(backlog_next)} tasks promoted.",
                )
                logger.info(
                    '{"event": "phase_advanced", "campaign_id": "%s", "to": "%s", "count": %d}',
                    campaign_id, next_kind, len(backlog_next),
                )

            # Emit a PendingPhaseAdvance describing the spec-rewrite the
            # spec-author handler needs to perform. The caller dedupes
            # against the board (one in-flight phase-advance task per
            # spec+phase) before creating the Plane task.
            result.pending_advances.append(
                PendingPhaseAdvance(
                    campaign_id=campaign_id,
                    spec_slug=campaign.slug,
                    spec_file_path=campaign.spec_file or str(
                        self._specs_dir / f"{campaign.slug}.md"
                    ),
                    current_phase=current_kind,
                    next_phase=next_phase_id,
                    task_summaries=_summarize_phase_tasks(by_phase),
                )
            )

        # Campaign completion: all child tasks terminal.
        all_tasks = by_phase["goal"] + by_phase["test_campaign"] + by_phase["improve_campaign"]
        if all_tasks and self._all_terminal(all_tasks):
            done_n = sum(1 for i in all_tasks if _status(i) == "done")
            cancelled_n = sum(1 for i in all_tasks if _status(i) == "cancelled")
            for parent in by_phase["parent"]:
                parent_id = str(parent["id"])
                self._client.transition_issue(parent_id, "Done")
                self._client.comment_issue(
                    parent_id,
                    f"Campaign complete. {done_n} tasks done, {cancelled_n} cancelled.",
                )
                try:
                    existing = [
                        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
                        for lab in parent.get("labels", [])
                    ]
                    existing = [n for n in existing if n]
                    if "lifecycle: archived" not in existing:
                        self._client.update_issue_labels(
                            parent_id, existing + ["lifecycle: archived"],
                        )
                except Exception as exc:
                    logger.warning(
                        '{"event": "lifecycle_archive_failed", "task_id": "%s", "error": "%s"}',
                        parent_id, str(exc),
                    )
            self._state.mark_complete(campaign_id)
            result.campaigns_completed += 1
            logger.info(
                '{"event": "campaign_complete", "campaign_id": "%s", "done": %d, "cancelled": %d}',
                campaign_id, done_n, cancelled_n,
            )

    def _all_terminal(self, issues: list[dict]) -> bool:
        return bool(issues) and all(_status(i) in _TERMINAL_STATES for i in issues)

    def _comment_parent(self, parents: list[dict], message: str) -> None:
        for parent in parents:
            try:
                self._client.comment_issue(str(parent["id"]), message)
            except Exception as exc:
                logger.debug(
                    '{"event": "comment_parent_failed", "parent_id": "%s", "error": "%s"}',
                    parent.get("id"), exc,
                )


def _summarize_phase_tasks(by_phase: dict[str, list[dict]]) -> list[tuple[str, str, str]]:
    """Compact (kind, status, title) tuples used to populate the rewrite
    prompt's seed_text so the LLM sees current phase state."""
    out: list[tuple[str, str, str]] = []
    for kind in ("goal", "test_campaign", "improve_campaign"):
        for issue in by_phase.get(kind, []):
            out.append((kind, _status(issue), str(issue.get("name", "")).strip()))
    return out
