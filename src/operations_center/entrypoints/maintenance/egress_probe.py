# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Controller-tier egress-proxy health probe (Harness Trust-Hardening, D-OP-2).

The sandboxed executor reaches the network only through the L7/SNI allowlist
egress proxy (``OC_EGRESS_PROXY``). That proxy is the load-bearing network
boundary, so its enforcement must be *actively* asserted rather than assumed.

This maintenance task runs at controller tier (outside the sandbox, where the
proxy config and direct loopback access live) and issues two synthetic probes
each cycle:

* an **allowlisted** destination (``github.com``) — expected to tunnel through;
* a **denied** destination (``example.com``) — expected to be refused (403).

It then classifies the outcome:

* both as expected → ``ok`` (boundary healthy);
* allowlisted destination **refused** → *rot*: the proxy is broken or its
  allowlist regressed, silently strangling the fleet's legitimate egress;
* denied destination **allowed** → *breach*: the allowlist is not being
  enforced, so the containment boundary is open.

Either fault auto-opens a fix task on the board (deduplicated by title so a
persistent fault does not spam the backlog). If the proxy itself is unreachable
the probe returns ``skipped`` — fail-open by design (§0.1 degrade-never-halt):
a restarting/absent proxy is a supervisor concern, not boundary rot.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Literal

from operations_center.maintenance.contracts import MaintenanceResult

if TYPE_CHECKING:
    from operations_center.adapters.plane.client import PlaneClient
    from operations_center.maintenance.contracts import MaintenanceContext

DEFAULT_INTERVAL_SECONDS = 300
_PROXY_ENV = "OC_EGRESS_PROXY"
_ALLOW_HOST = "github.com"
_DENY_HOST = "example.com"
_FIX_TITLE_PREFIX = "[egress-probe] proxy boundary fault"
_FIX_LABELS = ("kind:improve", "repo:OperationsCenter", "source:egress-probe")
_TERMINAL_STATES = {"done", "cancelled", "canceled"}

# Outcome of a single synthetic probe through the proxy.
ProbeOutcome = Literal["allowed", "denied", "proxy_down"]


def _http_probe(proxy: str, host: str, *, timeout: float = 8.0) -> ProbeOutcome:
    """Probe ``host`` through ``proxy``; classify the proxy's verdict.

    ``allowed``    — the proxy tunnelled the CONNECT (any HTTP response came back).
    ``denied``     — the proxy refused the host (403 / ProxyError on CONNECT).
    ``proxy_down`` — the proxy endpoint itself was unreachable (fail-open signal).
    """
    import httpx

    try:
        with httpx.Client(proxy=proxy, timeout=timeout, verify=True) as client:
            client.get(f"https://{host}/")
        return "allowed"
    except httpx.ProxyError:
        # The proxy actively refused the tunnel (allowlist DENY → 403).
        return "denied"
    except httpx.ConnectError as exc:
        # Distinguish "cannot reach the proxy" (down) from a refused upstream.
        text = str(exc).lower()
        if "proxy" in text or "refused" in text or "127.0.0.1" in text:
            return "proxy_down"
        return "denied"
    except httpx.HTTPError:
        # Any other transport-level failure: treat as a denied/unusable path.
        return "denied"


class EgressProbeTask:
    """Active synthetic probe asserting the egress boundary still enforces."""

    name = "egress_probe"

    def __init__(
        self,
        settings: Any,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
        allow_host: str = _ALLOW_HOST,
        deny_host: str = _DENY_HOST,
        probe_fn: Any = None,
        plane_client: PlaneClient | None = None,
    ) -> None:
        self._settings = settings
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._allow_host = allow_host
        self._deny_host = deny_host
        self._probe = probe_fn or _http_probe
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
        proxy = os.environ.get(_PROXY_ENV)
        if not proxy:
            return self._result(
                "skipped", started, {"reason": f"{_PROXY_ENV} not configured"}
            )

        allow = self._probe(proxy, self._allow_host)
        if allow == "proxy_down":
            # Fail-open: the proxy is unreachable (restarting/absent). Not rot —
            # the supervisor owns liveness; do not raise a boundary fault.
            return self._result(
                "skipped", started, {"reason": "egress proxy unreachable", "proxy": proxy}
            )
        deny = self._probe(proxy, self._deny_host)

        rot = allow == "denied"  # an allowlisted destination is being refused
        breach = deny == "allowed"  # a denied destination is being let through
        details: dict[str, object] = {
            "proxy": proxy,
            "allow_host": self._allow_host,
            "allow_result": allow,
            "deny_host": self._deny_host,
            "deny_result": deny,
        }

        if not (rot or breach):
            return self._result("ok", started, details)

        faults = []
        if rot:
            faults.append(
                f"allowlisted host {self._allow_host} was DENIED by the egress proxy"
            )
        if breach:
            faults.append(
                f"denied host {self._deny_host} was ALLOWED through the egress proxy"
            )
        details["faults"] = faults
        details["fix_task"] = self._open_fix_task(ctx, faults)
        return self._result("failed", started, details, error="; ".join(faults))

    def _open_fix_task(self, ctx: MaintenanceContext, faults: list[str]) -> str:
        """Open (or reuse) a board fix task for the boundary fault."""
        client = ctx.resources.get("plane_client") or self._make_plane_client()
        try:
            for issue in client.list_issues():
                name = str(issue.get("name", ""))
                if not name.startswith(_FIX_TITLE_PREFIX):
                    continue
                state = issue.get("state")
                state_name = (
                    state.get("name", "") if isinstance(state, dict) else str(state or "")
                )
                if state_name.strip().lower() not in _TERMINAL_STATES:
                    return f"exists:{issue.get('id')}"

            body = (
                "The controller-tier egress probe detected a fault in the "
                "sandbox network boundary (OC_EGRESS_PROXY):\n\n"
                + "\n".join(f"- {f}" for f in faults)
                + "\n\nThe egress proxy is the load-bearing containment boundary "
                "for the sandboxed executor. Investigate the proxy service "
                "(oc-egress-proxy.service) and its allowlist, then resolve the "
                "regression. Auto-filed by EgressProbeTask (D-OP-2)."
            )
            created = client.create_issue(
                name=f"{_FIX_TITLE_PREFIX}: {faults[0]}"[:200],
                description=body,
                label_names=list(_FIX_LABELS),
            )
            return f"created:{created.get('id')}"
        except Exception as exc:  # noqa: BLE001 — never let task-filing halt the loop
            return f"file_failed:{exc}"

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
