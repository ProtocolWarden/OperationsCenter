# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the localhost cloud-key-injecting proxy (SBX Phase 3, D-OP-1)."""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from operations_center.entrypoints.key_proxy import main as kp
from operations_center.entrypoints.key_proxy.injector import inject_auth, upstream_base


class TestInjectAuth:
    def test_anthropic_uses_x_api_key(self):
        out = inject_auth({"content-type": "application/json"}, provider="anthropic", key="sk-ant")
        assert out["x-api-key"] == "sk-ant"
        assert out["content-type"] == "application/json"
        assert "authorization" not in {k.lower() for k in out}

    def test_openai_uses_bearer(self):
        out = inject_auth({}, provider="openai", key="sk-oai")
        assert out["authorization"] == "Bearer sk-oai"

    def test_strips_client_supplied_auth(self):
        # the sandbox must not smuggle its own key; it is stripped then overwritten
        out = inject_auth(
            {"authorization": "Bearer ATTACKER", "x-api-key": "ATTACKER", "host": "evil"},
            provider="anthropic",
            key="real-key",
        )
        assert out["x-api-key"] == "real-key"
        assert "ATTACKER" not in str(out)
        assert "host" not in {k.lower() for k in out}

    def test_unknown_provider_raises(self):
        with pytest.raises(KeyError):
            inject_auth({}, provider="nope", key="k")

    def test_upstream_base(self):
        assert upstream_base("anthropic") == "https://api.anthropic.com"
        assert upstream_base("openai") == "https://api.openai.com"


def test_read_request_parses_method_path_headers_body():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"POST /v1/messages HTTP/1.1\r\nContent-Length: 5\r\nX-Foo: bar\r\n\r\nhello"
        )
        reader.feed_eof()
        return await kp._read_request(reader)

    method, path, headers, body = asyncio.run(run())
    assert method == "POST"
    assert path == "/v1/messages"
    assert headers["X-Foo"] == "bar"
    assert body == b"hello"


def test_proxy_injects_key_and_forwards_to_upstream():
    """End-to-end: the proxy forwards to a mock upstream WITH the injected key,
    and the sandbox-side request carried none."""

    async def scenario():
        seen = {}

        async def upstream(reader, writer):
            head = await reader.readuntil(b"\r\n\r\n")
            for ln in head.split(b"\r\n"):
                if b":" in ln:
                    k, _, v = ln.decode().partition(":")
                    seen[k.strip().lower()] = v.strip()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
            await writer.drain()
            writer.close()

        up_srv = await asyncio.start_server(upstream, "127.0.0.1", 0)
        uport = up_srv.sockets[0].getsockname()[1]

        with mock.patch.object(kp, "upstream_base", lambda p: f"http://127.0.0.1:{uport}"):
            import httpx

            async with httpx.AsyncClient() as client:
                proxy = await asyncio.start_server(
                    lambda r, w: kp._handle(
                        r, w, provider="anthropic", key="HOST-ONLY-KEY", client=client
                    ),
                    "127.0.0.1",
                    0,
                )
                pport = proxy.sockets[0].getsockname()[1]
                async with up_srv, proxy:
                    r, w = await asyncio.open_connection("127.0.0.1", pport)
                    # sandbox-side request carries NO key
                    w.write(b"POST /v1/messages HTTP/1.1\r\nContent-Length: 2\r\n\r\nyo")
                    await w.drain()
                    resp = await asyncio.wait_for(r.read(256), timeout=5)
                    w.close()
        return seen, resp

    seen, resp = asyncio.run(scenario())
    # the injected key reached upstream...
    assert seen.get("x-api-key") == "HOST-ONLY-KEY"
    # ...and the response streamed back
    assert b"200 OK" in resp and b"hi" in resp
