# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# src/operations_center/entrypoints/spec_hygiene/main.py
"""spec_hygiene watcher — ADR 0007 Phase A.

Hosts the non-LLM hygiene operations extracted from spec_director:
    * spec archival
    * orphan-campaign bootstrap
    * auto-promote backlog → R4AI
    * phase orchestration **detection** (LLM-free portion)
    * campaign recovery (abandonment scan)

Also rebuilds the state/campaigns/active.json projection from Plane at the
top of every cycle so OperatorConsole reads a Plane-derived view.

Phase advance detection only; LLM rewrite still happens via phase_orchestrator
until ADR 0007 Phase D.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.maintenance import (
    MaintenanceContext,
    MaintenanceRegistry,
    MaintenanceResult,
)
from operations_center.entrypoints.maintenance.board_unblock_task import BoardUnblockTask
from operations_center.entrypoints.maintenance.egress_probe import EgressProbeTask
from operations_center.maintenance.ledger_maintain import LedgerMaintainTask
from operations_center.spec_author.campaign_builder import CampaignBuilder
from operations_center.spec_author.models import (
    ActiveCampaigns,
    CampaignRecord,
    SpecFrontMatter,
)
from operations_center.spec_author.phase_orchestrator import (
    PendingPhaseAdvance,
    PhaseOrchestrator,
)
from operations_center.spec_author.recovery import RecoveryService
from operations_center.spec_author.spec_author_task import (
    SpecAuthorPayload,
    create_spec_author_task,
    find_in_flight_phase_advance,
)
from operations_center.spec_author.spec_writer import SpecWriter
from operations_center.spec_author.state import CampaignStateManager

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_SPECS_DIR = Path("docs/specs")

_LIFECYCLE_SKIP_PROMOTE = {
    "lifecycle: expanded",
    "lifecycle: archived",
    "lifecycle: escalated",
}


def _label_names(issue: dict) -> list[str]:
    """Normalize Plane label payloads to a flat list of names."""
    out: list[str] = []
    for label in issue.get("labels", []) or []:
        name = label.get("name", "") if isinstance(label, dict) else str(label)
        if name:
            out.append(name)
    return out


def _has_label(issue: dict, target: str) -> bool:
    target_l = target.strip().lower()
    return any(n.strip().lower() == target_l for n in _label_names(issue))


def _campaign_id_of(issue: dict) -> str | None:
    for n in _label_names(issue):
        if n.lower().startswith("campaign-id:"):
            return n.split(":", 1)[1].strip()
    return None


def _slug_of(issue: dict) -> str:
    """Best-effort slug extraction from a [Campaign] parent issue name."""
    name = str(issue.get("name", "")).strip()
    if name.startswith("[Campaign]"):
        return name[len("[Campaign]") :].strip()
    return ""


def _status_of(issue: dict) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).lower()
    return str(state or "").lower()


def _rebuild_active_projection(
    state_mgr: CampaignStateManager,
    all_issues: list[dict],
) -> None:
    """Rebuild state/campaigns/active.json from Plane.

    Single-writer invariant per ADR 0007. OperatorConsole reads this projection.

    Aggregation rules:
      * Only issues labeled `source: spec-campaign` participate.
      * Group by `campaign-id: <id>` label.
      * Status derivation:
          - 'complete'  → every child issue is in a terminal state (done/cancelled)
                          AND at least one is done
          - 'cancelled' → every child is cancelled
          - 'active'    → at least one child is not terminal
      * slug derived from the parent `[Campaign] <slug>` issue when present;
        otherwise fall back to the existing state record's slug.
    """
    existing = state_mgr.load()
    existing_by_id = {c.campaign_id: c for c in existing.campaigns}

    grouped: dict[str, list[dict]] = {}
    for issue in all_issues:
        if not _has_label(issue, "source: spec-campaign"):
            continue
        cid = _campaign_id_of(issue)
        if not cid:
            continue
        grouped.setdefault(cid, []).append(issue)

    rebuilt: list[CampaignRecord] = []
    for cid, issues in grouped.items():
        statuses = [_status_of(i) for i in issues]
        all_terminal = all(s in {"done", "cancelled"} for s in statuses)
        any_done = any(s == "done" for s in statuses)
        all_cancelled = all(s == "cancelled" for s in statuses)
        if all_terminal and all_cancelled:
            status = "cancelled"
        elif all_terminal and any_done:
            status = "complete"
        else:
            status = "active"

        # active.json is the ACTIVE projection (OperatorConsole's campaign pane
        # reads it). Terminal campaigns (complete/cancelled) are history — their
        # record lives in Plane — so they are not projected here. Without this the
        # projection accumulates every finished campaign indefinitely, cluttering
        # the status pane (observed: 11 records, 10 terminal, only 0 truly active).
        if status != "active":
            continue

        # Pull slug + spec_file + created_at from the parent issue when we can.
        parent = next(
            (i for i in issues if str(i.get("name", "")).startswith("[Campaign]")),
            None,
        )
        prev = existing_by_id.get(cid)
        slug = (parent and _slug_of(parent)) or (prev.slug if prev else cid)
        spec_file = prev.spec_file if prev else str(_SPECS_DIR / f"{slug}.md")
        created_at = (
            (prev.created_at if prev else None)
            or (parent and str(parent.get("created_at") or "") or "")
            or datetime.now(UTC).isoformat()
        )
        rebuilt.append(
            CampaignRecord(
                campaign_id=cid,
                slug=str(slug),
                spec_file=spec_file,
                status=status,  # type: ignore[arg-type]
                created_at=str(created_at),
            )
        )

    state_mgr.save(ActiveCampaigns(campaigns=rebuilt))
    logger.info(
        json.dumps(
            {
                "event": "spec_active_projection_rebuilt",
                "campaign_count": len(rebuilt),
            },
            ensure_ascii=False,
        )
    )


def _bootstrap_orphan_campaigns(
    settings: Any,
    client: PlaneClient,
    all_issues: list[dict],
    state_mgr: CampaignStateManager,
) -> None:
    """Create initial Plane tasks for active campaigns that have none.

    A campaign is "orphaned" if it appears in state/campaigns/active.json but no
    Plane work-item carries its `campaign-id: <id>` label.
    """
    by_campaign: dict[str, int] = {}
    for issue in all_issues:
        for label in issue.get("labels", []) or []:
            name = label.get("name", "") if isinstance(label, dict) else str(label)
            if name.lower().startswith("campaign-id:"):
                cid = name.split(":", 1)[1].strip()
                by_campaign[cid] = by_campaign.get(cid, 0) + 1

    active = state_mgr.load()
    builder = CampaignBuilder(
        client=client,
        project_id=settings.plane.project_id,
        max_tasks=settings.spec_author.max_tasks_per_campaign,
    )
    for campaign in active.active_campaigns():
        if by_campaign.get(campaign.campaign_id, 0) > 0:
            continue
        spec_path = _SPECS_DIR / f"{campaign.slug}.md"
        if not spec_path.exists():
            logger.warning(
                json.dumps(
                    {
                        "event": "orphan_campaign_no_spec",
                        "slug": campaign.slug,
                    },
                    ensure_ascii=False,
                )
            )
            continue
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
            fm = SpecFrontMatter.from_spec_text(spec_text)
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "orphan_campaign_parse_failed",
                        "slug": campaign.slug,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
            continue
        repo_key = fm.repos[0] if fm.repos else ""
        repo_cfg = settings.repos.get(repo_key) if settings.repos else None
        if repo_cfg is None:
            logger.warning(
                json.dumps(
                    {
                        "event": "orphan_campaign_unknown_repo",
                        "slug": campaign.slug,
                        "repo": repo_key,
                    },
                    ensure_ascii=False,
                )
            )
            continue
        try:
            task_ids = builder.build(
                spec_text=spec_text,
                repo_key=repo_key,
                base_branch=repo_cfg.default_branch,
            )
            logger.info(
                json.dumps(
                    {
                        "event": "orphan_campaign_bootstrapped",
                        "campaign_id": campaign.campaign_id,
                        "slug": campaign.slug,
                        "tasks_created": len(task_ids),
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "orphan_campaign_bootstrap_failed",
                        "slug": campaign.slug,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )


def _auto_promote_backlog(client: PlaneClient, issues: list[dict]) -> None:
    """Promote tier-≥2 autonomy tasks from Backlog → Ready for AI each cycle.

    Filters out tasks carrying any lifecycle label meaning "don't touch":
      * expanded   — work already delegated to children
      * archived   — terminal-and-frozen
      * escalated  — out of normal automated flow
    """
    from operations_center.autonomy_tiers.config import (
        get_family_tier,
        load_tiers_config,
    )
    from operations_center.proposer.backlog_promoter import BacklogPromoterService

    def _has_skip_label(issue: dict) -> bool:
        for lab in issue.get("labels", []) or []:
            name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            if name in _LIFECYCLE_SKIP_PROMOTE:
                return True
        return False

    filtered = [i for i in issues if not _has_skip_label(i)]

    tiers_config = load_tiers_config()
    service = BacklogPromoterService(
        plane_client=client,
        get_tier=lambda family: get_family_tier(family, tiers_config),
        dry_run=False,
    )
    try:
        result = service.promote(issues=filtered)
    except Exception as exc:
        logger.error(
            json.dumps({"event": "auto_promote_failed", "error": str(exc)}, ensure_ascii=False)
        )
        return
    if result.promoted:
        logger.info(
            json.dumps(
                {
                    "event": "auto_promote_backlog",
                    "count": len(result.promoted),
                    "families": sorted({t.family for t in result.promoted}),
                },
                ensure_ascii=False,
            )
        )


def _build_phase_advance_seed(advance: PendingPhaseAdvance) -> str:
    """Compose the seed_text the spec-author handler will see for a phase
    advance. Captures the current spec, the phase we're moving from/to, and
    the per-task status snapshot so the rewrite prompt has everything it
    needs without re-reading the board.
    """
    lines: list[str] = []
    lines.append(f"Phase advance: {advance.current_phase} -> {advance.next_phase}")
    lines.append(f"Campaign: {advance.campaign_id}")
    lines.append(f"Spec: {advance.spec_file_path}")
    lines.append("")
    lines.append("Current task state:")
    if advance.task_summaries:
        for kind, status, title in advance.task_summaries:
            lines.append(f"  - [{kind}] [{status}] {title}")
    else:
        lines.append("  (no child tasks recorded)")
    return "\n".join(lines)


def _emit_phase_advance_tasks(
    *,
    client: PlaneClient,
    all_issues: list[dict],
    pending: list[PendingPhaseAdvance],
) -> int:
    """Create a spec-author Plane task with ``task_phase`` set for each
    pending advance. Dedupes against the board: skip if a non-Done
    spec-author task with the same spec_slug + task_phase already exists.
    Returns the count of newly created tasks.
    """
    created = 0
    for advance in pending:
        existing = find_in_flight_phase_advance(
            all_issues,
            advance.spec_slug,
            advance.next_phase,
        )
        if existing is not None:
            logger.info(
                json.dumps(
                    {
                        "event": "spec_phase_advance_skip_dedupe",
                        "spec_slug": advance.spec_slug,
                        "task_phase": advance.next_phase,
                        "existing_issue_id": existing,
                    },
                    ensure_ascii=False,
                )
            )
            continue
        payload = SpecAuthorPayload(
            spec_slug=advance.spec_slug,
            trigger_source="phase_advance",
            target_path=advance.spec_file_path,
            seed_text=_build_phase_advance_seed(advance),
            task_phase=advance.next_phase,
        )
        try:
            issue_id = create_spec_author_task(client, payload)
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "spec_phase_advance_create_failed",
                        "spec_slug": advance.spec_slug,
                        "task_phase": advance.next_phase,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
            continue
        created += 1
        logger.info(
            json.dumps(
                {
                    "event": "spec_phase_advance_task_created",
                    "issue_id": issue_id,
                    "spec_slug": advance.spec_slug,
                    "task_phase": advance.next_phase,
                    "campaign_id": advance.campaign_id,
                },
                ensure_ascii=False,
            )
        )
    return created


def run_once(settings: Any, client: PlaneClient) -> dict[str, Any]:
    """Execute one spec-hygiene cycle. Returns a free-form summary dict
    consumed by SpecHygieneTask to populate MaintenanceResult.details.
    An empty dict means the cycle short-circuited (e.g. disabled or fetch
    failed); the caller can treat that as either ``skipped`` or ``failed``
    based on the value of ``summary.get('status_hint')``.
    """
    summary: dict[str, Any] = {
        "campaigns_projected": 0,
        "phases_advanced": 0,
        "campaigns_completed": 0,
        "phase_advance_tasks_emitted": 0,
        "campaigns_abandoned": 0,
    }
    sd = settings.spec_author
    if not sd.enabled:
        summary["status_hint"] = "skipped"
        summary["reason"] = "spec_author_disabled"
        return summary

    logger.info(json.dumps({"event": "spec_hygiene_cycle_start"}, ensure_ascii=False))

    state_mgr = CampaignStateManager()
    spec_writer = SpecWriter(specs_dir=_SPECS_DIR)

    # Step 0: Spec archival — drop expired specs out of the working tree.
    spec_writer.archive_expired(retention_days=sd.spec_retention_days)

    # Step 1: Fetch all issues once — shared across projection, bootstrap,
    # promotion, orchestration, and recovery.
    try:
        all_issues = client.list_issues()
    except Exception as exc:
        logger.error(
            json.dumps(
                {"event": "spec_hygiene_board_fetch_failed", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        summary["status_hint"] = "failed"
        summary["error"] = f"board_fetch_failed: {exc}"
        return summary

    # Step 1a: Rebuild active.json projection from Plane.
    # Single-writer invariant per ADR 0007. OperatorConsole reads this projection.
    _rebuild_active_projection(state_mgr, all_issues)
    summary["campaigns_projected"] = sum(
        1
        for i in all_issues
        if any(
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).lower()
            == "source: spec-campaign"
            for lab in (i.get("labels") or [])
        )
    )

    # Step 2: Orphan-campaign bootstrap — campaigns registered in state but with
    # zero backing Plane tasks (e.g. autonomously-spawned campaigns whose builder
    # step never ran).
    _bootstrap_orphan_campaigns(settings, client, all_issues, state_mgr)

    # Step 3: Auto-promote Backlog → Ready for AI for tier-≥2 families.
    _auto_promote_backlog(client, all_issues)

    # Step 4: Phase orchestration — detection-only (ADR 0007 Phase D).
    # Runs phase-advance + completion detection synchronously (no LLM) and
    # returns pending advances; we then create spec-author Plane tasks with
    # `task_phase` set so board_worker drives the LLM rewrite through the
    # backend executor pipeline.
    orch = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=_SPECS_DIR,
    )
    orch_result = orch.run(all_issues)
    tasks_emitted = _emit_phase_advance_tasks(
        client=client,
        all_issues=all_issues,
        pending=orch_result.pending_advances,
    )
    summary["phases_advanced"] = int(orch_result.phases_advanced or 0)
    summary["campaigns_completed"] = int(orch_result.campaigns_completed or 0)
    summary["phase_advance_tasks_emitted"] = int(tasks_emitted)
    if any(
        [
            orch_result.phases_advanced,
            orch_result.campaigns_completed,
            tasks_emitted,
        ]
    ):
        logger.info(
            json.dumps(
                {
                    "event": "spec_phase_orchestration",
                    "phases_advanced": orch_result.phases_advanced,
                    "campaigns_completed": orch_result.campaigns_completed,
                    "phase_advance_tasks_emitted": tasks_emitted,
                },
                ensure_ascii=False,
            )
        )

    # Step 5: Recovery scan — abandon stale campaigns past the threshold.
    active = state_mgr.load()
    recovery = RecoveryService(
        client=client,
        state_manager=state_mgr,
        abandon_hours=sd.campaign_abandon_hours,
    )
    abandoned = 0
    for campaign in active.active_campaigns():
        if recovery.should_abandon(campaign):
            recovery.self_cancel(campaign, "abandon_hours_exceeded", _SPECS_DIR)
            abandoned += 1
            logger.info(
                json.dumps(
                    {"event": "spec_campaign_abandoned", "campaign_id": campaign.campaign_id},
                    ensure_ascii=False,
                )
            )
    summary["campaigns_abandoned"] = abandoned
    summary["status_hint"] = "ok"
    return summary


class SpecHygieneTask:
    """``MaintenanceTask`` wrapper around ``run_once`` (ADR 0007 follow-up D).

    Implements the ``operations_center.maintenance.MaintenanceTask`` protocol
    so the spec-hygiene cycle can be driven uniformly by the maintenance
    registry alongside any future maintenance operations. The standalone
    ``main()`` entrypoint also drives this class so there is one source of
    truth for the cycle logic.
    """

    name: str = "spec_hygiene"

    def __init__(
        self,
        settings: Any,
        client: PlaneClient,
        *,
        interval_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        sd = settings.spec_author
        self.interval_seconds = int(
            interval_seconds if interval_seconds is not None else sd.poll_interval_seconds
        )
        self.enabled = bool(enabled if enabled is not None else sd.enabled)

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()
        try:
            summary = run_once(self._settings, self._client)
        except Exception as exc:  # noqa: BLE001 — uniform failure surface
            duration = time.monotonic() - started
            return MaintenanceResult(
                name=self.name,
                status="failed",
                duration_seconds=duration,
                details={"cycle_id": ctx.cycle_id},
                error=str(exc),
            )
        duration = time.monotonic() - started
        hint = summary.pop("status_hint", "ok")
        error = summary.pop("error", None)
        status = hint if hint in {"ok", "skipped", "failed"} else "ok"
        details = dict(summary)
        details["cycle_id"] = ctx.cycle_id
        return MaintenanceResult(
            name=self.name,
            status=status,  # type: ignore[arg-type]
            duration_seconds=duration,
            details=details,
            error=error,
        )


def _write_heartbeat(status_dir: Path | None) -> None:
    if status_dir is None:
        return
    try:
        hb = status_dir / "heartbeat_spec_hygiene.json"
        hb.write_text(
            json.dumps(
                {
                    "role": "spec_hygiene",
                    "at": datetime.now(UTC).isoformat(),
                    "status": "idle",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _log_maintenance_result(result: MaintenanceResult) -> None:
    logger.info(
        json.dumps(
            {
                "event": "maintenance_task_run",
                "name": result.name,
                "status": result.status,
                "duration_seconds": round(result.duration_seconds, 3),
                "details": result.details,
                "error": result.error,
            },
            ensure_ascii=False,
        )
    )


def register_maintenance_tasks(
    registry: MaintenanceRegistry, settings: Any, client: PlaneClient
) -> MaintenanceRegistry:
    """Register every maintenance task the live loop runs.

    Extracted so the wiring is unit-testable (the standalone CLI and the
    watchdog-side loop share it — ADR 0007 follow-up D). Order is informational;
    the registry schedules by per-task interval.
    """
    registry.register(SpecHygieneTask(settings, client))
    # Operator-interventions ledger consolidation loop (observe + self-verifying
    # promote). Runs the controller's half of the ledger so a human only ever
    # encodes a judgment once; recurrences self-verify. Best-effort by design.
    registry.register(LedgerMaintainTask(settings))
    # Autonomous board-unblock engine (Rules 1–10 + PR-merged reconciliation).
    # Previously runnable only as a standalone CLI, so nothing ever investigated
    # stuck/Blocked tasks; registering it here makes the controller self-heal the
    # board every cycle with no human in the loop (HARNESS_TRUST_HARDENING §0.1).
    registry.register(BoardUnblockTask(settings))
    # Controller-tier egress-boundary probe (HARNESS_TRUST_HARDENING D-OP-2).
    # Actively asserts the sandbox's egress proxy still tunnels allowlisted
    # destinations and still refuses denied ones; a regression (rot or breach)
    # auto-opens a fix task. Runs outside the sandbox where the proxy config and
    # loopback live; skipped (fail-open) when no proxy is configured.
    registry.register(EgressProbeTask(settings, plane_client=client))
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(
        description="spec_hygiene — non-LLM spec/campaign hygiene watcher (ADR 0007)",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument(
        "--status-dir",
        type=Path,
        default=None,
        help="Directory for heartbeat_spec_hygiene.json",
    )
    parser.add_argument(
        "--maintenance-state",
        type=Path,
        default=None,
        help="Override path for the maintenance registry's last-run sidecar "
        "(default: .console/maintenance_state.json)",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    sd = settings.spec_author

    # Build a maintenance registry hosting the spec-hygiene task. The
    # standalone CLI and the watchdog-side loop share the same wiring
    # (ADR 0007 follow-up D).
    registry = MaintenanceRegistry(state_path=args.maintenance_state)
    register_maintenance_tasks(registry, settings, client)

    def _build_ctx() -> MaintenanceContext:
        return MaintenanceContext(
            cycle_id=str(uuid.uuid4()),
            now=datetime.now(UTC),
            resources={"plane_client": client, "settings": settings},
        )

    try:
        if args.once:
            # --once bypasses the interval gate so operators can force a
            # single cycle of every registered maintenance task on demand.
            ctx = _build_ctx()
            for registered in registry.list_tasks():
                _log_maintenance_result(registered.run_once(ctx))
            return
        cycle = 0
        while True:
            _write_heartbeat(args.status_dir)
            try:
                results = registry.run_due(_build_ctx())
                for r in results:
                    _log_maintenance_result(r)
            except Exception as exc:
                logger.error(
                    json.dumps(
                        {"event": "spec_hygiene_cycle_error", "cycle": cycle, "error": str(exc)},
                        ensure_ascii=False,
                    )
                )
            cycle += 1
            time.sleep(sd.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()
