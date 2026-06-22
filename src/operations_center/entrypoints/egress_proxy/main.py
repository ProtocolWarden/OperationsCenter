# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""L7/SNI egress allowlist proxy (SBX Phase 3, D-OP-2 = B+).

A minimal HTTPS-CONNECT proxy: the sandboxed executor is given ``HTTPS_PROXY``
pointing here (the bwrap launcher injects it via ``board_worker/sandbox.py`` when
``OC_EGRESS_PROXY`` is set and reachable, gated on the proxy's liveness — fail-open).
Per D-SBX-2 the sandbox keeps ``--share-net`` (an isolated netns could not reach this
host-loopback proxy / the ollama floor without a forwarder), so the constraint is
applied at L7 here, not by netns isolation. For each ``CONNECT host:port`` it
(1) checks the CONNECT host against the allowlist, then (2) peeks the TLS ClientHello
and verifies the real SNI is also allowlisted — catching a client that CONNECTs to an
allowed host but speaks TLS to another. Only then does it tunnel. Everything else is
refused and logged.

Run as ``oc-egress-proxy.service`` (``systemd --user``, ``Restart=always``, linger,
ordered before ``oc-fleet.service``): "proxy down" self-recovers in seconds and
the bwrap launcher fails open to the ollama-local floor, so this is fail-CLOSED on
the data path yet never a fleet halt (§0.1 / D-OP-2).

**Integration Status**: STANDALONE SERVICE, now WIRED into the sandbox. The main()
function is invoked by systemd (see deploy/systemd/oc-egress-proxy.service). The
bwrap launcher injects ``HTTPS_PROXY`` pointing here when ``OC_EGRESS_PROXY`` is set
and this proxy is reachable (``board_worker/sandbox.py``). Controller-tier synthetic
health probes (DENY-on-allowlisted = rot → auto-fix task) remain a follow-on. The
proxy is intentionally a separate process, not directly instantiated from app code.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from operations_center.entrypoints.egress_proxy.allowlist import (
    DEFAULT_ALLOWLIST,
    extract_sni,
    host_allowed,
)

logger = logging.getLogger("oc_egress_proxy")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8889
_PEEK_BYTES = 4096
_BUF = 65536


def _parse_connect(line: bytes) -> tuple[str, int] | None:
    """Parse ``CONNECT host:port HTTP/1.1`` → (host, port) or None."""
    try:
        parts = line.decode("latin-1").split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            return None
        hostport = parts[1]
        if hostport.startswith("["):  # [ipv6]:port
            host, _, port = hostport[1:].partition("]:")
            return host, int(port or 443)
        host, _, port = hostport.rpartition(":")
        return (host or hostport), int(port or 443)
    except Exception:  # noqa: BLE001 — never raise on a malformed CONNECT line
        return None


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(_BUF)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:  # noqa: BLE001  # pragma: no cover — a proxy must not crash on any socket error
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001  # pragma: no cover — best-effort close
            pass


async def _handle(
    creader: asyncio.StreamReader,
    cwriter: asyncio.StreamWriter,
    allowlist: tuple[str, ...],
) -> None:
    peer = cwriter.get_extra_info("peername")
    try:
        # Read the CONNECT request head (up to blank line).
        head = await asyncio.wait_for(creader.readuntil(b"\r\n\r\n"), timeout=10)
    except Exception:  # noqa: BLE001 — drop the connection on any read/parse error
        cwriter.close()
        return
    target = _parse_connect(head.split(b"\r\n", 1)[0])
    if target is None:
        cwriter.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
        await _safe_drain_close(cwriter)
        return
    host, port = target
    if not host_allowed(host, allowlist):
        logger.warning("egress DENY connect host=%s:%s peer=%s", host, port, peer)
        cwriter.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await _safe_drain_close(cwriter)
        return

    cwriter.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await cwriter.drain()

    # Peek the ClientHello and verify the in-TLS SNI is also allowlisted.
    try:
        hello = await asyncio.wait_for(creader.read(_PEEK_BYTES), timeout=10)
    except Exception:  # noqa: BLE001 — drop the connection on any read/parse error
        cwriter.close()
        return
    sni = extract_sni(hello)
    # Fail-CLOSED on the in-TLS SNI. Previously a MISSING sni (ECH / no-SNI
    # ClientHello) passed — letting an attacker tunnel anywhere through an
    # allowlisted CONNECT host. Now an absent or non-allowlisted SNI is refused.
    # OC_EGRESS_SNI_STRICT additionally pins sni == CONNECT host (blocks
    # in-allowlist domain-fronting, e.g. CONNECT github.com / SNI gist.github…);
    # opt-in so connection-coalescing clients aren't broken by default.
    sni_ok = sni is not None and host_allowed(sni, allowlist)
    if sni_ok and os.environ.get("OC_EGRESS_SNI_STRICT") == "1":
        sni_ok = sni.lower() == host.lower()
    if not sni_ok:
        logger.warning("egress DENY sni=%s (connect host=%s) peer=%s", sni, host, peer)
        cwriter.close()
        return

    try:
        ureader, uwriter = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
    except Exception as exc:  # noqa: BLE001 — any upstream-connect failure -> refuse
        logger.warning("egress upstream-fail host=%s:%s — %s", host, port, exc)
        cwriter.close()
        return

    logger.info("egress ALLOW host=%s:%s sni=%s", host, port, sni)
    if hello:
        uwriter.write(hello)
        await uwriter.drain()
    await asyncio.gather(_pipe(creader, uwriter), _pipe(ureader, cwriter))


async def _safe_drain_close(writer: asyncio.StreamWriter) -> None:
    try:
        await writer.drain()
    except Exception:  # noqa: BLE001  # pragma: no cover — a proxy must not crash on any socket error
        pass
    try:
        writer.close()
    except Exception:  # noqa: BLE001  # pragma: no cover — a proxy must not crash on any socket error
        pass


async def serve(host: str, port: int, allowlist: tuple[str, ...]) -> None:  # pragma: no cover
    server = await asyncio.start_server(lambda r, w: _handle(r, w, allowlist), host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    logger.info("oc-egress-proxy listening on %s; allowlist=%s", addrs, list(allowlist))
    async with server:
        await server.serve_forever()


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="OC L7/SNI egress allowlist proxy")
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="extra allowlist rule (exact host or .suffix); repeatable",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    allowlist = (*DEFAULT_ALLOWLIST, *args.allow)
    try:
        asyncio.run(serve(args.host, args.port, allowlist))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
