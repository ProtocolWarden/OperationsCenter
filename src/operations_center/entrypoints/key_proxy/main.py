# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Localhost cloud-key-injecting reverse proxy (SBX Phase 3, D-OP-1 = HYBRID).

The sandboxed agent points its model base URL at this loopback proxy and carries
**no API key**. The proxy injects the host-held key (``inject_auth``) and streams
the request through to the real endpoint, streaming the response back — so the
cloud key never enters the sandbox env. Plain HTTP on loopback is fine (the hop is
127.0.0.1); the upstream leg is HTTPS via httpx.

Supervised like the egress proxy; the fleet fails open to ollama-local if it's
down (D-OP-1), so this is never a halt.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

import httpx

from operations_center.entrypoints.key_proxy.injector import (
    PROVIDERS,
    inject_auth,
    upstream_base,
)

logger = logging.getLogger("oc_key_proxy")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8890
_HOP = b"\r\n\r\n"


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes] | None:
    """Read an HTTP/1.1 request → (method, path, headers, body) or None."""
    try:
        head = await asyncio.wait_for(reader.readuntil(_HOP), timeout=30)
    except Exception:  # noqa: BLE001 — malformed / closed client: drop
        return None
    try:
        lines = head.split(b"\r\n")
        method, path, _ = lines[0].decode("latin-1").split(" ", 2)
        headers: dict[str, str] = {}
        for raw in lines[1:]:
            if not raw or b":" not in raw:
                continue
            k, _, v = raw.decode("latin-1").partition(":")
            headers[k.strip()] = v.strip()
        body = b""
        clen = int(headers.get("Content-Length", "0") or "0")
        if clen > 0:
            body = await asyncio.wait_for(reader.readexactly(clen), timeout=60)
        return method, path, headers, body
    except Exception:  # noqa: BLE001 — any parse error: drop
        return None


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    provider: str,
    key: str,
    client: httpx.AsyncClient,
) -> None:
    req = await _read_request(reader)
    if req is None:
        _close(writer)
        return
    method, path, headers, body = req
    url = upstream_base(provider) + path
    up_headers = inject_auth(headers, provider=provider, key=key)
    try:
        async with client.stream(method, url, headers=up_headers, content=body) as resp:
            line = f"HTTP/1.1 {resp.status_code} {resp.reason_phrase}\r\n".encode("latin-1")
            writer.write(line)
            for k, v in resp.headers.items():
                if k.lower() in ("transfer-encoding", "connection"):
                    continue
                writer.write(f"{k}: {v}\r\n".encode("latin-1"))
            writer.write(b"\r\n")
            await writer.drain()
            async for chunk in resp.aiter_raw():
                writer.write(chunk)
                await writer.drain()
    except Exception as exc:  # noqa: BLE001 — upstream failure: 502, never crash
        logger.warning("key-proxy upstream-fail provider=%s — %s", provider, exc)
        try:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
    _close(writer)


def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
    except Exception:  # noqa: BLE001 — best-effort teardown
        pass


async def serve(host: str, port: int, *, provider: str, key: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0), http2=False) as client:
        server = await asyncio.start_server(
            lambda r, w: _handle(r, w, provider=provider, key=key, client=client),
            host,
            port,
        )
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        logger.info("oc-key-proxy provider=%s listening on %s", provider, addrs)
        async with server:
            await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="OC localhost cloud-key-injecting proxy")
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    parser.add_argument(
        "--key-env",
        required=True,
        help="env var (on the HOST, not the sandbox) holding the API key",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = os.environ.get(args.key_env)
    if not key:
        raise SystemExit(f"key env {args.key_env!r} is empty — refusing to start keyless")
    asyncio.run(serve(args.host, args.port, provider=args.provider, key=key))


if __name__ == "__main__":
    main()
