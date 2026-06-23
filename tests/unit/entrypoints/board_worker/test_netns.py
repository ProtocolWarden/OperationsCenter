# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Structural egress netns confinement (B1) — argv/fail-open + the kernel-enforcement
integration test (skipped when pasta/iptables/setpriv are unavailable)."""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time

import pytest

from operations_center.entrypoints.board_worker import netns


def test_disabled_returns_cmd_unchanged():
    cmd = ["bwrap", "echo", "hi"]
    assert netns.maybe_netns(cmd, proxy_url="http://127.0.0.1:8889", enabled=False) == cmd


def test_no_proxy_fails_open(monkeypatch):
    monkeypatch.setattr(netns, "pasta_path", lambda: "/usr/bin/pasta")
    cmd = ["bwrap", "x"]
    assert netns.maybe_netns(cmd, proxy_url=None, enabled=True) == cmd


def test_missing_pasta_fails_open(monkeypatch):
    monkeypatch.setattr(netns, "pasta_path", lambda: None)
    cmd = ["bwrap", "x"]
    assert netns.maybe_netns(cmd, proxy_url="http://127.0.0.1:8889", enabled=True) == cmd


def test_required_raises_when_pasta_missing(monkeypatch):
    # B4: OC_EGRESS_REQUIRED flips fail-open into fail-closed.
    monkeypatch.setattr(netns, "pasta_path", lambda: None)
    monkeypatch.setenv("OC_EGRESS_REQUIRED", "1")
    with pytest.raises(netns.EgressContainmentRequiredError):
        netns.maybe_netns(["bwrap", "x"], proxy_url="http://127.0.0.1:8889", enabled=True)


def test_required_raises_when_no_proxy(monkeypatch):
    monkeypatch.setattr(netns, "pasta_path", lambda: "/usr/bin/pasta")
    monkeypatch.setenv("OC_EGRESS_REQUIRED", "1")
    with pytest.raises(netns.EgressContainmentRequiredError):
        netns.maybe_netns(["bwrap", "x"], proxy_url=None, enabled=True)


def test_not_required_still_fails_open(monkeypatch):
    monkeypatch.setattr(netns, "pasta_path", lambda: None)
    monkeypatch.delenv("OC_EGRESS_REQUIRED", raising=False)
    cmd = ["bwrap", "x"]
    assert netns.maybe_netns(cmd, proxy_url="http://127.0.0.1:8889", enabled=True) == cmd


def test_enabled_wraps_with_pasta(monkeypatch):
    monkeypatch.setattr(netns, "pasta_path", lambda: "/usr/bin/pasta")
    monkeypatch.delenv("OC_EGRESS_NETNS_PORTS", raising=False)
    out = netns.maybe_netns(["bwrap", "exec"], proxy_url="http://127.0.0.1:8889", enabled=True)
    assert out[0] == "/usr/bin/pasta"
    assert "--config-net" in out
    assert out[-2:] == ["bwrap", "exec"]  # inner cmd preserved at the tail
    # proxy + ollama host-loopback ports forwarded into the netns
    assert out[out.index("-T") + 1] == "8889"
    assert "11434" in out
    script = out[out.index("-c") + 1]
    assert "OUTPUT -o lo -j ACCEPT" in script  # loopback (forwarded proxy/ollama) allowed
    assert "OUTPUT DROP" in script  # everything else dropped
    assert "setpriv" in script and "bounding-set=-all" in script  # cap-drop


def test_extra_ports_forwarded(monkeypatch):
    monkeypatch.setattr(netns, "pasta_path", lambda: "/usr/bin/pasta")
    monkeypatch.setenv("OC_EGRESS_NETNS_PORTS", "9000, 9001")
    out = netns.maybe_netns(["x"], proxy_url="http://127.0.0.1:8889", enabled=True)
    assert "9000" in out and "9001" in out


def test_enabled_flag(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_NETNS", "1")
    assert netns.netns_enabled() is True
    monkeypatch.delenv("OC_EGRESS_NETNS")
    assert netns.netns_enabled() is False


_HAVE_TOOLS = bool(
    shutil.which("pasta") and shutil.which("iptables") and shutil.which("setpriv")
)

_PROBE = """
import socket, subprocess
r = subprocess.run(["iptables", "-F", "OUTPUT"], capture_output=True)
print("FLUSH", "denied" if r.returncode else "ALLOWED")
def conn(host, port):
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect((host, port))
        payload = s.recv(16).decode() if port != 443 else ""
        print("OK", host, port, payload)
    except OSError as e:
        print("BLOCKED", host, port, e.errno)
    finally:
        s.close()
conn("127.0.0.1", {marker})  # host-loopback proxy via pasta's loopback map
conn("1.1.1.1", 443)         # internet (must be kernel-blocked)
"""


def _pasta_netns_functional() -> bool:
    """True only if pasta can actually create a netns here. CI runners may HAVE the
    binaries but forbid `unshare`/netns creation in the container — in which case
    pasta exits non-zero and this test must skip, not fail."""
    if not _HAVE_TOOLS:
        return False
    try:
        r = subprocess.run(
            ["pasta", "--config-net", "--", "true"], capture_output=True, timeout=15
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(
    not _pasta_netns_functional(), reason="pasta netns not functional here (CI/container)"
)
def test_kernel_enforcement_end_to_end():
    """Decisive: through the real maybe_netns wrapper, the host-loopback proxy is
    reachable, a raw socket to the internet is kernel-blocked, and the agent cannot
    flush the firewall."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    marker = srv.getsockname()[1]
    srv.listen(8)
    stop = threading.Event()

    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.sendall(b"PROXY-OK")
                conn.close()
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.2)
    wrapped = netns.maybe_netns(
        ["python3", "-c", _PROBE.format(marker=marker)],
        proxy_url=f"http://127.0.0.1:{marker}",
        enabled=True,
    )
    assert wrapped[0].endswith("pasta")
    out = subprocess.run(wrapped, capture_output=True, text=True, timeout=60).stdout
    stop.set()

    assert "FLUSH denied" in out, out
    assert f"OK 127.0.0.1 {marker} PROXY-OK" in out, out
    assert "BLOCKED 1.1.1.1 443" in out, out
