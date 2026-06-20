# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Cloud-key injection policy (SBX Phase 3, D-OP-1 = HYBRID).

Pure, side-effect-free core of the localhost key-injecting proxy: given a provider
and the per-request client headers, produce the upstream headers with the API key
injected — so the key lives ONLY on the host (in the proxy) and is **absent from
the sandbox env**. The sandbox points e.g. ``ANTHROPIC_BASE_URL`` at the loopback
proxy and carries no key; this module decides which auth header to set per
provider and strips any client-supplied auth (the sandbox must not be able to
smuggle its own).

Kept separate so the policy is unit-testable without sockets. The fleet never
halts on the key proxy: the bwrap launcher fails open to the ollama-local floor,
and a dead proxy presents as a worker_backend cooldown (D-OP-1).
"""

from __future__ import annotations

from collections.abc import Mapping

# provider → (upstream base URL, auth header name, value template).
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("https://api.anthropic.com", "x-api-key", "{key}"),
    "openai": ("https://api.openai.com", "authorization", "Bearer {key}"),
}

# Client headers that must never be forwarded as-is — the host re-establishes auth,
# and hop-by-hop headers don't survive a proxy hop.
_STRIP = frozenset(
    {
        "authorization",
        "x-api-key",
        "host",
        "proxy-connection",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    }
)


def upstream_base(provider: str) -> str:
    """The real endpoint for ``provider``. Raises KeyError on unknown provider."""
    return PROVIDERS[provider][0]


def inject_auth(
    client_headers: Mapping[str, str],
    *,
    provider: str,
    key: str,
) -> dict[str, str]:
    """Return upstream headers: the client's headers minus any auth/hop-by-hop
    ones, plus the provider's auth header carrying the host-held ``key``.

    The injected key NEVER came from the client (sandbox) — it is supplied by the
    host proxy. A client that tries to send its own ``authorization``/``x-api-key``
    has it stripped, then overwritten.
    """
    if provider not in PROVIDERS:
        raise KeyError(f"unknown provider {provider!r}")
    _, header_name, template = PROVIDERS[provider]
    out: dict[str, str] = {}
    for name, value in client_headers.items():
        if name.lower() in _STRIP:
            continue
        out[name] = value
    out[header_name] = template.format(key=key)
    return out


__all__ = ["PROVIDERS", "inject_auth", "upstream_base"]
