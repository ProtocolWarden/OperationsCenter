# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Controller-tier reviewer drift monitor (EVAL §4.2, D-EVAL-5).

The deterministic blocking gate (`replay`) grades the verdict *code*, which cannot
silently drift. The actual reviewer-quality risk lives one layer down: the model
mis-EXTRACTING checks from a diff (well-formed but wrong). This task closes that
coverage gap — it replays the corpus's ``extraction``-kind cases (real diffs)
through a model and flags, NON-BLOCKING, when the model's majority verdict drifts
from the signed answer.

Two design rules from the spec, enforced here:

* **Different model family than the implementer** — N copies of one family is N=1
  (shared blindspots). The extractor is injected; the caller wires a non-implementer
  backend. There is no clean single-shot model API in the repo today, so the live
  invoker is a deliberate seam: with none configured the task ``skipped`` (no model,
  no false drift). Opt-in via ``OC_EVAL_DRIFT_MONITOR=1`` once an extractor is wired.
* **Non-blocking** — drift becomes a deduplicated operator ticket, never a build
  failure (voting smooths onset-of-regression variance; it must not gate)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from operations_center.eval.corpus import load_ledger
from operations_center.eval.critic import (
    EXTRACTION_KIND,
    CheckExtractor,
    DriftResult,
    run_drift_monitor,
)
from operations_center.maintenance.contracts import MaintenanceResult

if TYPE_CHECKING:
    from operations_center.adapters.plane.client import PlaneClient
    from operations_center.maintenance.contracts import MaintenanceContext

DEFAULT_INTERVAL_SECONDS = 21600  # 6h — drift is slow; model calls are not free
DEFAULT_CORPUS = Path("eval/corpus/ledger.jsonl")
_ENABLE_ENV = "OC_EVAL_DRIFT_MONITOR"
_TICKET_PREFIX = "[eval-drift]"
_TICKET_LABELS = ("kind:improve", "repo:OperationsCenter", "source:eval-drift")
_TERMINAL_STATES = {"done", "cancelled", "canceled"}


class DriftMonitorTask:
    """Replay extraction-kind corpus cases through a model; ticket on drift."""

    name = "drift_monitor"

    def __init__(
        self,
        settings: Any,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
        corpus_path: Path = DEFAULT_CORPUS,
        extractor: CheckExtractor | None = None,
        votes: int = 3,
        plane_client: PlaneClient | None = None,
    ) -> None:
        self._settings = settings
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._corpus_path = corpus_path
        self._extractor = extractor
        self._votes = votes
        self._plane_client = plane_client

    def _make_plane_client(self) -> PlaneClient:
        if self._plane_client is not None:
            return self._plane_client
        from operations_center.adapters.plane.client import PlaneClient

        p = self._settings.plane
        return PlaneClient(
            base_url=p.base_url,
            api_token=self._settings.plane_token(),
            workspace_slug=p.workspace_slug,
            project_id=p.project_id,
        )

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()
        # Opt-in + injected-extractor required. No extractor (no wired model) →
        # skipped: no model means no false drift (§0.1 fail-safe).
        if self._extractor is None or os.environ.get(_ENABLE_ENV) != "1":
            return self._result(
                "skipped", started, {"reason": "drift monitor not enabled / no extractor"}
            )
        try:
            cases = [c for c in load_ledger(self._corpus_path).cases() if c.kind == EXTRACTION_KIND]
        except Exception as exc:  # noqa: BLE001 — a corpus read error must not halt the loop
            return self._result("failed", started, {}, error=f"corpus_load_failed: {exc}")
        if not cases:
            return self._result("skipped", started, {"reason": "no extraction-kind cases"})

        try:
            results = run_drift_monitor(cases, self._extractor, votes=self._votes)
        except Exception as exc:  # noqa: BLE001 — a flaky backend must not halt the loop
            return self._result("failed", started, {}, error=f"drift_run_failed: {exc}")

        drifted = [r for r in results if r.drifted]
        details: dict[str, object] = {"cases": len(cases), "drifted": len(drifted)}
        if drifted:
            details["tickets"] = self._emit_tickets(ctx, drifted)
        return self._result("ok", started, details)

    def _emit_tickets(self, ctx: MaintenanceContext, drifted: list[DriftResult]) -> list[str]:
        client = ctx.resources.get("plane_client") or self._make_plane_client()
        results: list[str] = []
        try:
            open_titles = self._open_ticket_titles(client)
        except Exception as exc:  # noqa: BLE001 — never let ticketing halt the loop
            return [f"list_failed:{exc}"]
        for r in drifted:
            title = f"{_TICKET_PREFIX} {r.case_id}"
            if title in open_titles:
                results.append(f"exists:{r.case_id}")
                continue
            try:
                created = client.create_issue(
                    name=title[:200],
                    description=(
                        f"Reviewer DRIFT on extraction case {r.case_id}: the model's "
                        f"majority verdict ({r.agree_votes}/{r.total_votes}) disagrees "
                        f"with the signed answer.\n\n{r.detail}\n\nNon-blocking signal "
                        "(D-EVAL-5). Adjudicate; if the reviewer model regressed, this "
                        "is the semantic miss the deterministic gate can't see."
                    ),
                    label_names=list(_TICKET_LABELS),
                )
                results.append(f"created:{created.get('id')}")
                open_titles.add(title)
            except Exception as exc:  # noqa: BLE001
                results.append(f"file_failed:{r.case_id}:{exc}")
        return results

    @staticmethod
    def _open_ticket_titles(client: PlaneClient) -> set[str]:
        titles: set[str] = set()
        for issue in client.list_issues():
            name = str(issue.get("name", ""))
            if not name.startswith(_TICKET_PREFIX):
                continue
            state = issue.get("state")
            state_name = state.get("name", "") if isinstance(state, dict) else str(state or "")
            if state_name.strip().lower() not in _TERMINAL_STATES:
                titles.add(name)
        return titles

    def _result(
        self,
        status: Literal["ok", "skipped", "failed"],
        started: float,
        details: dict[str, object],
        *,
        error: str | None = None,
    ) -> MaintenanceResult:
        return MaintenanceResult(
            name=self.name,
            status=status,
            duration_seconds=time.monotonic() - started,
            details=details,
            error=error,
        )
