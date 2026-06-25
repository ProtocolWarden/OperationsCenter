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


def test_drop_caps_false_omits_capdrop_but_keeps_firewall(monkeypatch):
    # Sandboxed payload (bwrap is the containment): the cap-drop MUST be omitted — a
    # `setpriv --bounding-set=-all` in front of bwrap empties the bounding set, which
    # persists into bwrap's child userns and masks the CAP_SYS_ADMIN it needs to build
    # its namespaces (the two SBX layers would compose fail-CLOSED). The egress
    # firewall must STILL be applied.
    monkeypatch.setattr(netns, "pasta_path", lambda: "/usr/bin/pasta")
    out = netns.maybe_netns(
        ["bwrap", "exec"], proxy_url="http://127.0.0.1:8889", enabled=True, drop_caps=False
    )
    script = out[out.index("-c") + 1]
    assert "setpriv" not in script  # no cap-drop in front of bwrap
    assert "OUTPUT DROP" in script  # firewall still confines egress
    assert out[-2:] == ["bwrap", "exec"]  # inner cmd preserved at the tail


def test_drop_caps_true_includes_capdrop(monkeypatch):
    # Non-sandboxed payload running DIRECTLY in the netns: the cap-drop is what stops
    # the payload flushing the firewall, so it MUST be present (and is the default).
    monkeypatch.setattr(netns, "pasta_path", lambda: "/usr/bin/pasta")
    out = netns.maybe_netns(
        ["python3", "x"], proxy_url="http://127.0.0.1:8889", enabled=True, drop_caps=True
    )
    script = out[out.index("-c") + 1]
    assert "setpriv" in script and "bounding-set=-all" in script


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


def test_run_executor_skips_capdrop_only_for_bwrap_payload(monkeypatch, tmp_path):
    # Integration of the fix: run_executor tells maybe_netns to KEEP caps
    # (drop_caps=False) only when the wrapped payload is ACTUALLY bwrap, and to drop
    # them otherwise — including the sandbox fail-open path where maybe_sandbox returns
    # the bare executor (which DOES need the cap-drop). Keyed off the resolved argv,
    # not the OC_BWRAP_SANDBOX flag.
    from operations_center.entrypoints.board_worker import _subprocess

    captured = {}

    def fake_netns(cmd, *, proxy_url, enabled, drop_caps=True):
        captured["drop_caps"] = drop_caps
        return list(cmd)

    monkeypatch.setattr(_subprocess, "maybe_netns", fake_netns)
    monkeypatch.setattr(_subprocess, "netns_enabled", lambda: True)
    monkeypatch.setattr(
        _subprocess, "_resolve_egress_proxy", lambda env: "http://127.0.0.1:8889"
    )
    monkeypatch.setattr(_subprocess.subprocess, "run", lambda *a, **k: "RAN")
    monkeypatch.delenv("OC_SANDBOX_RLIMITS", raising=False)

    # payload IS bwrap -> keep caps so bwrap can build its namespaces
    monkeypatch.setattr(_subprocess, "maybe_sandbox", lambda cmd, **kw: ["bwrap", *cmd])
    _subprocess.run_executor(
        ["echo", "hi"], oc_root=tmp_path, rw_root=tmp_path, workspace=tmp_path, env={}
    )
    assert captured["drop_caps"] is False

    # sandbox fail-open returns the bare executor -> drop caps (it runs in the netns)
    monkeypatch.setattr(_subprocess, "maybe_sandbox", lambda cmd, **kw: list(cmd))
    _subprocess.run_executor(
        ["python3", "x"], oc_root=tmp_path, rw_root=tmp_path, workspace=tmp_path, env={}
    )
    assert captured["drop_caps"] is True


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


@pytest.mark.skipif(
    not (_pasta_netns_functional() and shutil.which("bwrap")),
    reason="pasta netns + bwrap not both functional here (CI/container)",
)
def test_bwrap_inside_netns_runs_when_caps_kept():
    """Decisive regression for the fail-CLOSED SBX composition: a bwrap payload wrapped
    by the netns with ``drop_caps=False`` can build its pid/uts/ipc namespaces and run.
    With the cap-drop in front of it (``drop_caps=True``) bwrap instead aborts with
    "Creating new namespace failed: Operation not permitted" — the emptied bounding set
    persists into bwrap's child userns and masks CAP_SYS_ADMIN."""
    bwrap_cmd = [
        "bwrap", "--unshare-pid", "--unshare-uts", "--unshare-ipc",
        "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
        "--", "/bin/echo", "NS-OK",
    ]
    wrapped = netns.maybe_netns(
        list(bwrap_cmd), proxy_url="http://127.0.0.1:18889", enabled=True, drop_caps=False
    )
    assert wrapped[0].endswith("pasta")
    r = subprocess.run(wrapped, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "NS-OK" in r.stdout, (r.returncode, r.stderr)
