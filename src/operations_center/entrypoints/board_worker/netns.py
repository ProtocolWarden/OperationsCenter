# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Structural egress confinement via a rootless network namespace (Phase 3, B1).

The bwrap sandbox shares the host network namespace (D-SBX-2), so egress was only
*honor-system* — the proxy is reached via ``HTTPS_PROXY``, which a compromised agent
can ``unset`` or bypass with a raw socket. The audit confirmed this; the audit's
suggested kernel fix (``systemd-run --user -p IPAddressDeny``) was empirically shown
NOT to enforce under a rootless ``--user`` manager.

This closes it structurally and rootless, validated end to end:

1. **pasta** (``passt``) runs the command in a rootless network namespace and
   transparently maps the netns's ``127.0.0.1`` to the **host's** loopback — so the
   host egress proxy (``127.0.0.1:8889``) and ollama (``127.0.0.1:11434``) stay
   reachable at the SAME addresses with **no env change and no forwarder**. The
   command inside runs uid=0 with CAP_NET_ADMIN over *that* netns.
2. An in-netns **iptables OUTPUT DROP** (allow only ``lo`` + established) kernel-
   blocks every non-loopback egress — a raw socket to the internet gets dropped,
   while the proxy/ollama on the mapped loopback still work.
3. **Caps are dropped** (``setpriv --bounding-set=-all``) before exec'ing the
   executor, so the agent cannot flush the firewall. (bwrap's child userns can't
   reach the parent-owned netns either — belt and suspenders.)

Net: an agent that does ``unset HTTPS_PROXY`` + a raw socket is kernel-blocked,
while HTTPS-through-the-proxy keeps working. The honor-system hole is closed.

**Opt-in + fail-open (§0.1 degrade-never-halt).** Gated on ``OC_EGRESS_NETNS=1``.
If pasta is missing, no proxy is configured (a locked netns with no proxy would
have *no* egress at all), or any setup step fails, it degrades to the prior
shared-netns behavior rather than halting the fleet. Enable-and-observe like the
other SBX layers."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_NETNS_FLAG = "OC_EGRESS_NETNS"
_REQUIRED_FLAG = "OC_EGRESS_REQUIRED"
_PASTA_BIN_ENV = "OC_PASTA_BIN"
_EXTRA_PORTS_ENV = "OC_EGRESS_NETNS_PORTS"  # comma-sep extra host-loopback ports
_OLLAMA_PORT = 11434

# In-netns setup: lock egress to loopback (the proxy lives on the host loopback that
# pasta maps in), drop CAP_NET_ADMIN so the agent can't undo it, then exec the cmd.
# Fail-open at every step: a missing/failing iptables or setpriv degrades to running
# the command without that protection rather than halting (§0.1).
_SETUP_SCRIPT = r"""
set -u
if command -v iptables >/dev/null 2>&1; then
  iptables -A OUTPUT -o lo -j ACCEPT 2>/dev/null \
    && iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null \
    && iptables -P OUTPUT DROP 2>/dev/null \
    || echo "oc-netns: egress filter not applied (fail-open)" >&2
fi
if command -v setpriv >/dev/null 2>&1; then
  exec setpriv --inh-caps=-all --bounding-set=-all --ambient-caps=-all "$@"
fi
exec "$@"
"""


def netns_enabled() -> bool:
    return os.environ.get(_NETNS_FLAG) == "1"


class EgressContainmentRequiredError(RuntimeError):
    """Raised when egress confinement is REQUIRED (OC_EGRESS_REQUIRED=1) but
    cannot be established. Default posture is fail-open (§0.1); this is the
    operator opt-in to never run a token-holding backend with unconfined egress.
    """


def egress_required() -> bool:
    return str(os.environ.get(_REQUIRED_FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def pasta_path() -> str | None:
    return shutil.which(os.environ.get(_PASTA_BIN_ENV, "pasta"))


def _forward_ports(proxy_url: str) -> list[int]:
    """Host-loopback ports to expose at the netns ``127.0.0.1`` (pasta ``-T``): the
    egress proxy + ollama + any operator-configured extras. These are the ONLY
    host services the confined executor can reach; everything else is dropped."""
    ports: list[int] = []
    proxy_port = urlparse(proxy_url).port
    if proxy_port:
        ports.append(proxy_port)
    if _OLLAMA_PORT not in ports:
        ports.append(_OLLAMA_PORT)
    for extra in os.environ.get(_EXTRA_PORTS_ENV, "").split(","):
        extra = extra.strip()
        if extra.isdigit() and int(extra) not in ports:
            ports.append(int(extra))
    return ports


def maybe_netns(
    cmd: Sequence[str], *, proxy_url: str | None, enabled: bool
) -> list[str]:
    """Wrap ``cmd`` to run inside a pasta netns whose only egress is the proxy.

    pasta ``-T <port>`` forwards each host-loopback service (proxy, ollama) to the
    netns ``127.0.0.1:<port>`` so the executor's existing env (``HTTPS_PROXY=
    127.0.0.1:8889``) works unchanged; the in-netns iptables drops everything else.

    Fail-open: returns ``cmd`` unchanged when disabled, when pasta is unavailable,
    or when no egress proxy is configured (a locked netns with no proxy would have
    no usable egress — the proxy is the sole channel out)."""
    if not enabled:
        return list(cmd)
    pasta = pasta_path()
    if pasta is None or not proxy_url:
        # Enabled but degraded: make the silent fail-open observable (§0.1 keeps
        # it non-halting, but the audit flagged that absent isolation must be
        # visible). The structured ``event`` key lets the log sweep alert on it.
        reason = "pasta_unavailable" if pasta is None else "no_egress_proxy"
        logger.warning(
            "netns_degraded: egress confinement enabled but running with "
            'shared netns (%s) {"event": "netns_degraded", "reason": "%s"}',
            reason,
            reason,
        )
        if egress_required():
            raise EgressContainmentRequiredError(
                f"OC_EGRESS_REQUIRED set but egress confinement unavailable ({reason})"
            )
        return list(cmd)
    forwards: list[str] = []
    for port in _forward_ports(proxy_url):
        forwards += ["-T", str(port)]
    return [pasta, "--config-net", *forwards, "--", "sh", "-c", _SETUP_SCRIPT, "oc-netns", *cmd]


__all__ = [
    "EgressContainmentRequiredError",
    "egress_required",
    "maybe_netns",
    "netns_enabled",
    "pasta_path",
]
