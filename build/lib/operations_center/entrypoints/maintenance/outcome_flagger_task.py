# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Controller-tier outcome-correlation flagger (EVAL Component 2, D-EVAL-1).

Runs the :mod:`operations_center.eval.outcome_flagger` correlation each maintenance
cycle and turns each disagreement into a **deduplicated operator-adjudication
ticket** on the board. It emits tickets, never a metric (the contamination argument
lives in the flagger module).

The outcome data is supplied by an injected :class:`OutcomeSource` seam; with none
wired the task returns ``skipped`` — no data means no tickets, never a false flag
(§0.1 fail-safe). This keeps the controller honest until the reviewer-instrumentation
+ post-merge-regression join is wired as the production source."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Literal

from operations_center.eval.outcome_flagger import (
    Disagreement,
    OutcomeSource,
    flag_disagreements,
)
from operations_center.maintenance.contracts import MaintenanceResult

if TYPE_CHECKING:
    from operations_center.adapters.github_pr import GitHubPRClient
    from operations_center.adapters.plane.client import PlaneClient
    from operations_center.maintenance.contracts import MaintenanceContext

DEFAULT_INTERVAL_SECONDS = 3600
_TICKET_PREFIX = "[eval-flag]"
_TICKET_LABELS = ("kind:improve", "repo:OperationsCenter", "source:eval-flagger")
_TERMINAL_STATES = {"done", "cancelled", "canceled"}
# Opt-in: only build the live GitHub outcome source when explicitly enabled, so the
# task stays skipped (and network-free) by default until an operator turns it on.
_SOURCE_ENV = "OC_EVAL_OUTCOME_SOURCE"


class OutcomeFlaggerTask:
    """Emit operator-adjudication tickets for reviewer/worker outcome disagreements."""

    name = "outcome_flagger"

    def __init__(
        self,
        settings: Any,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
        outcome_source: OutcomeSource | None = None,
        plane_client: PlaneClient | None = None,
    ) -> None:
        self._settings = settings
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._outcome_source = outcome_source
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

    def _make_gh_client(self) -> GitHubPRClient | None:
        from operations_center.adapters.github_pr import GitHubPRClient

        token_env = (
            getattr(getattr(self._settings, "git", None), "token_env", None) or "GITHUB_TOKEN"
        )
        token = os.environ.get(token_env)
        return GitHubPRClient(token=token) if token else None

    def _resolve_outcome_source(self) -> OutcomeSource | None:
        """Injected source wins; otherwise build the live GitHub source only when
        explicitly opted in (``OC_EVAL_OUTCOME_SOURCE=github``) and a token exists.
        Default: ``None`` → skipped (no data, no false flags, no network)."""
        if self._outcome_source is not None:
            return self._outcome_source
        if os.environ.get(_SOURCE_ENV) != "github":
            return None
        gh = self._make_gh_client()
        if gh is None:
            return None
        from operations_center.eval.outcome_sources import make_github_outcome_source

        return make_github_outcome_source(self._settings, gh)

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()
        source = self._resolve_outcome_source()
        if source is None:
            return self._result(
                "skipped", started, {"reason": "no outcome source wired"}
            )
        try:
            outcomes = source()
        except Exception as exc:  # noqa: BLE001 — a flaky source must not halt the loop
            return self._result(
                "failed", started, {}, error=f"outcome_source_failed: {exc}"
            )
        if not outcomes:
            return self._result("skipped", started, {"reason": "no outcomes to correlate"})

        flags = flag_disagreements(outcomes)
        details: dict[str, object] = {
            "outcomes": len(outcomes),
            "disagreements": len(flags),
            "by_attribution": {
                "reviewer": sum(f.attribution == "reviewer" for f in flags),
                "worker": sum(f.attribution == "worker" for f in flags),
            },
        }
        if not flags:
            return self._result("ok", started, details)

        details["tickets"] = self._emit_tickets(ctx, flags)
        return self._result("ok", started, details)

    def _emit_tickets(self, ctx: MaintenanceContext, flags: list[Disagreement]) -> list[str]:
        """Create one deduplicated board ticket per disagreement (best-effort)."""
        client = ctx.resources.get("plane_client") or self._make_plane_client()
        results: list[str] = []
        try:
            open_titles = self._open_ticket_titles(client)
        except Exception as exc:  # noqa: BLE001 — never let ticketing halt the loop
            return [f"list_failed:{exc}"]
        for flag in flags:
            title = f"{_TICKET_PREFIX} {flag.dedup_key}"
            if title in open_titles:
                results.append(f"exists:{flag.dedup_key}")
                continue
            try:
                created = client.create_issue(
                    name=title[:200],
                    description=(
                        f"Outcome-correlation flag ({flag.kind}, attributed to "
                        f"{flag.attribution}) for {flag.repo or 'PR'} #{flag.pr_number}:\n\n"
                        f"{flag.detail}\n\nAuto-filed by OutcomeFlaggerTask (D-EVAL-1). "
                        "This is a FLAGGER, not a metric — adjudicate; if it reveals a "
                        "real reviewer miss, add a signed corpus case."
                    ),
                    label_names=list(_TICKET_LABELS),
                )
                results.append(f"created:{created.get('id')}")
                open_titles.add(title)
            except Exception as exc:  # noqa: BLE001
                results.append(f"file_failed:{flag.dedup_key}:{exc}")
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
