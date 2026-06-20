# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Egress allowlist + TLS SNI extraction (SBX Phase 3, D-OP-2 = B+).

The load-bearing, side-effect-free core of the L7/SNI egress proxy: decide whether
a destination host is permitted, and read the real destination out of a TLS
ClientHello (so a client that CONNECTs to an allowed host but speaks TLS to a
different name is still caught). Kept pure so the policy is unit-testable without
sockets.

The allowlist is intentionally small — the two sanctioned channels (the model
endpoint, github push/clone) plus localhost (the ollama-local floor + the
key-injecting proxy). Per the spec these channels ARE exfil paths; the proxy
*raises cost*, it does not *close* exfil. fail-CLOSED on the data path, but made
non-load-bearing by supervision (Restart=always) + a controller-tier probe.

**Integration Status**: This module is currently used only in the standalone proxy
process (main.py, launched via systemd). Application code integration (HTTPS_PROXY
environment variable setup in the bwrap launcher) is deferred to a follow-on Phase 3 PR.
"""

from __future__ import annotations

# Suffix/exact host rules. A leading "." means "this domain and any subdomain";
# otherwise it is an exact host match. localhost/127.0.0.1 cover the ollama-local
# floor and the localhost cloud-key proxy.
DEFAULT_ALLOWLIST: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "::1",
    # model endpoints
    "api.anthropic.com",
    "api.openai.com",
    ".ollama.ai",
    # github (push / clone / api / large-file)
    "github.com",
    ".github.com",
    "codeload.github.com",
    ".githubusercontent.com",
)


def host_allowed(host: str, allowlist: tuple[str, ...] = DEFAULT_ALLOWLIST) -> bool:
    """True iff ``host`` matches an allowlist rule. Exact match, or suffix match
    for rules beginning with ``.`` (``.github.com`` allows ``api.github.com`` and
    ``github.com`` itself). Case-insensitive; a trailing dot (FQDN root) and any
    ``:port`` are stripped first."""
    if not host:
        return False
    h = host.strip().lower().rstrip(".")
    if "]" in h:  # bracketed IPv6 literal [::1]
        h = h.split("]")[0].lstrip("[")
    elif h.count(":") == 1:  # host:port (not IPv6)
        h = h.split(":", 1)[0]
    for rule in allowlist:
        r = rule.lower()
        if r.startswith("."):
            if h == r[1:] or h.endswith(r):
                return True
        elif h == r:
            return True
    return False


def extract_sni(data: bytes) -> str | None:
    """Extract the ``server_name`` from a TLS ClientHello, or ``None`` if absent /
    unparseable. Pure binary parse — no allocation games, bounds-checked, never
    raises. Used to verify the in-TLS destination matches the CONNECT host."""
    try:
        # TLS record: type(1)=0x16 handshake, version(2), length(2)
        if len(data) < 5 or data[0] != 0x16:
            return None
        # Handshake: type(1)=0x01 ClientHello, length(3)
        pos = 5
        if len(data) < pos + 4 or data[pos] != 0x01:
            return None
        pos += 4
        pos += 2  # client_version
        pos += 32  # random
        if pos >= len(data):
            return None
        sid_len = data[pos]
        pos += 1 + sid_len
        if pos + 2 > len(data):
            return None
        cs_len = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2 + cs_len
        if pos >= len(data):
            return None
        comp_len = data[pos]
        pos += 1 + comp_len
        if pos + 2 > len(data):
            return None  # no extensions
        ext_total = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
        end = min(len(data), pos + ext_total)
        while pos + 4 <= end:
            ext_type = int.from_bytes(data[pos : pos + 2], "big")
            ext_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
            pos += 4
            if ext_type == 0x0000:  # server_name
                # server_name_list length(2), then entries: type(1), len(2), host
                if pos + 5 > len(data):
                    return None
                name_type = data[pos + 2]
                name_len = int.from_bytes(data[pos + 3 : pos + 5], "big")
                if name_type != 0x00 or pos + 5 + name_len > len(data):
                    return None
                return data[pos + 5 : pos + 5 + name_len].decode("ascii", "ignore") or None
            pos += ext_len
        return None
    except Exception:  # noqa: BLE001 — a malformed ClientHello yields no SNI, never a crash
        return None


__all__ = ["DEFAULT_ALLOWLIST", "extract_sni", "host_allowed"]
