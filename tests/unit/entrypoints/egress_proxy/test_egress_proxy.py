# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the L7/SNI egress allowlist proxy (SBX Phase 3).

Tests the proxy in isolation (allowlist rules, SNI extraction, proxy handler logic).
Application integration (HTTPS_PROXY environment variable setup in bwrap launcher)
is deferred to follow-on Phase 3 PRs; these tests verify the proxy itself is correct.
"""

from __future__ import annotations

import asyncio

from operations_center.entrypoints.egress_proxy.allowlist import (
    DEFAULT_ALLOWLIST,
    extract_sni,
    host_allowed,
)


class TestHostAllowed:
    def test_exact_and_subdomain(self):
        assert host_allowed("github.com")
        assert host_allowed("api.github.com")  # via .github.com
        assert host_allowed("api.anthropic.com")
        assert host_allowed("localhost")

    def test_denied_hosts(self):
        assert not host_allowed("evil.com")
        assert not host_allowed("api.anthropic.com.evil.com")
        assert not host_allowed("notgithub.com")
        assert not host_allowed("")

    def test_strips_port_and_trailing_dot(self):
        assert host_allowed("github.com:443")
        assert host_allowed("github.com.")

    def test_ipv6_localhost(self):
        assert host_allowed("[::1]:443")
        assert host_allowed("127.0.0.1")

    def test_suffix_rule_matches_bare_domain(self):
        assert host_allowed("github.com", (".github.com",))


def _client_hello(server_name: str) -> bytes:
    """Build a minimal valid TLS ClientHello carrying an SNI for round-trip
    testing of the parser (follows RFC 8446 record/handshake/extension framing)."""
    sn = server_name.encode()
    sni_list = b"\x00" + len(sn).to_bytes(2, "big") + sn  # type host_name + len + name
    sni_ext_body = len(sni_list).to_bytes(2, "big") + sni_list
    sni_ext = b"\x00\x00" + len(sni_ext_body).to_bytes(2, "big") + sni_ext_body
    exts = len(sni_ext).to_bytes(2, "big") + sni_ext
    body = (
        b"\x03\x03"  # client_version TLS1.2
        + b"\x00" * 32  # random
        + b"\x00"  # session_id len 0
        + b"\x00\x02\x13\x01"  # cipher_suites: len 2 + one suite
        + b"\x01\x00"  # compression: len 1 + null
        + exts
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    record = b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake
    return record


class TestExtractSni:
    def test_round_trip(self):
        assert extract_sni(_client_hello("api.anthropic.com")) == "api.anthropic.com"
        assert extract_sni(_client_hello("evil.example")) == "evil.example"

    def test_non_tls_returns_none(self):
        assert extract_sni(b"GET / HTTP/1.1\r\n\r\n") is None
        assert extract_sni(b"") is None
        assert extract_sni(b"\x16\x03\x01\x00\x05") is None  # truncated

    def test_malformed_never_raises(self):
        assert extract_sni(b"\x16" + b"\xff" * 50) is None

    def test_truncated_at_every_stage_returns_none(self):
        full = _client_hello("api.anthropic.com")
        # truncating the valid ClientHello at many points exercises each
        # bounds-check return-None branch without ever raising.
        for n in range(5, len(full)):
            assert extract_sni(full[:n]) in (None, "api.anthropic.com")



def test_proxy_denies_then_tunnels():
    """Integration: a denied CONNECT host gets 403; an allowed one tunnels."""
    from operations_center.entrypoints.egress_proxy.main import _handle

    async def scenario():
        # echo upstream
        async def echo(r, w):
            while (data := await r.read(1024)):
                w.write(data)
                await w.drain()
            w.close()
        echo_srv = await asyncio.start_server(echo, "127.0.0.1", 0)
        eport = echo_srv.sockets[0].getsockname()[1]
        # proxy
        proxy = await asyncio.start_server(
            lambda r, w: _handle(r, w, DEFAULT_ALLOWLIST), "127.0.0.1", 0
        )
        pport = proxy.sockets[0].getsockname()[1]
        async with echo_srv, proxy:
            # 1) DENY non-allowlisted host -> 403
            r, w = await asyncio.open_connection("127.0.0.1", pport)
            w.write(b"CONNECT evil.com:443 HTTP/1.1\r\n\r\n")
            await w.drain()
            resp = await asyncio.wait_for(r.read(64), timeout=5)
            assert b"403" in resp
            w.close()
            # 2) ALLOW 127.0.0.1 + a valid allowlisted ClientHello -> tunnel through
            #    echo. (A real HTTPS client sends the ClientHello first; the proxy
            #    now fail-closes on a missing SNI, so plaintext-first no longer works.)
            r2, w2 = await asyncio.open_connection("127.0.0.1", pport)
            w2.write(f"CONNECT 127.0.0.1:{eport} HTTP/1.1\r\n\r\n".encode())
            await w2.drain()
            est = await asyncio.wait_for(r2.read(64), timeout=5)
            assert b"200" in est
            w2.write(_client_hello("github.com"))  # allowlisted SNI -> passes fail-closed
            await w2.drain()
            w2.write(b"ping-through-proxy")
            await w2.drain()
            buf = b""
            while b"ping-through-proxy" not in buf:
                chunk = await asyncio.wait_for(r2.read(4096), timeout=5)
                if not chunk:
                    break
                buf += chunk
            assert b"ping-through-proxy" in buf  # bytes tunnelled through to echo
            w2.close()

    asyncio.run(scenario())


def test_proxy_405_on_non_connect_and_sni_deny_and_upstream_fail():
    """Cover the 405 / SNI-deny / upstream-fail branches of _handle."""
    from operations_center.entrypoints.egress_proxy.main import _handle

    async def scenario():
        proxy = await asyncio.start_server(
            lambda r, w: _handle(r, w, DEFAULT_ALLOWLIST), "127.0.0.1", 0
        )
        pport = proxy.sockets[0].getsockname()[1]
        async with proxy:
            # (a) non-CONNECT -> 405
            r, w = await asyncio.open_connection("127.0.0.1", pport)
            w.write(b"GET / HTTP/1.1\r\n\r\n")
            await w.drain()
            assert b"405" in await asyncio.wait_for(r.read(64), timeout=5)
            w.close()
            # (b) allowed CONNECT, but ClientHello SNI is denied -> dropped (no tunnel)
            r2, w2 = await asyncio.open_connection("127.0.0.1", pport)
            w2.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
            await w2.drain()
            assert b"200" in await asyncio.wait_for(r2.read(64), timeout=5)
            w2.write(_client_hello("evil.example.com"))
            await w2.drain()
            assert await asyncio.wait_for(r2.read(64), timeout=5) == b""  # closed
            w2.close()
            # (c) allowed host but dead upstream port -> connection dropped
            r3, w3 = await asyncio.open_connection("127.0.0.1", pport)
            w3.write(b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n")
            await w3.drain()
            assert b"200" in await asyncio.wait_for(r3.read(64), timeout=5)
            w3.write(_client_hello("github.com"))  # allowed sni
            await w3.drain()
            assert await asyncio.wait_for(r3.read(64), timeout=5) == b""  # upstream fail -> drop
            w3.close()

    asyncio.run(scenario())


def test_missing_sni_fails_closed():
    """A ClientHello with no extractable SNI (ECH / no-SNI) is now REFUSED — it used
    to pass, letting an attacker tunnel anywhere through an allowlisted CONNECT host."""
    from operations_center.entrypoints.egress_proxy.main import _handle

    async def scenario():
        proxy = await asyncio.start_server(
            lambda r, w: _handle(r, w, DEFAULT_ALLOWLIST), "127.0.0.1", 0
        )
        pport = proxy.sockets[0].getsockname()[1]
        async with proxy:
            r, w = await asyncio.open_connection("127.0.0.1", pport)
            w.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")  # allowed CONNECT host
            await w.drain()
            assert b"200" in await asyncio.wait_for(r.read(64), timeout=5)
            w.write(b"GET / HTTP/1.1\r\n\r\n")  # not a TLS ClientHello -> SNI None
            await w.drain()
            assert await asyncio.wait_for(r.read(64), timeout=5) == b""  # fail-closed: dropped
            w.close()

    asyncio.run(scenario())


def test_strict_sni_pin_denies_allowlisted_mismatch(monkeypatch):
    """OC_EGRESS_SNI_STRICT=1 pins SNI == CONNECT host: an allowlisted-but-different
    SNI (in-allowlist domain-fronting) is refused."""
    monkeypatch.setenv("OC_EGRESS_SNI_STRICT", "1")
    from operations_center.entrypoints.egress_proxy.main import _handle

    async def scenario():
        proxy = await asyncio.start_server(
            lambda r, w: _handle(r, w, DEFAULT_ALLOWLIST), "127.0.0.1", 0
        )
        pport = proxy.sockets[0].getsockname()[1]
        async with proxy:
            r, w = await asyncio.open_connection("127.0.0.1", pport)
            w.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")  # allowed CONNECT host
            await w.drain()
            assert b"200" in await asyncio.wait_for(r.read(64), timeout=5)
            w.write(_client_hello("github.com"))  # allowlisted, but != CONNECT host
            await w.drain()
            assert await asyncio.wait_for(r.read(64), timeout=5) == b""  # strict: dropped
            w.close()

    asyncio.run(scenario())
