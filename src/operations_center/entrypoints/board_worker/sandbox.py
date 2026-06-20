# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""SBX Phase 2 — bwrap process containment for the worker/executor subprocess.

Wraps the executor command in a rootless ``bwrap`` (bubblewrap) sandbox that:

- ``--unshare-pid`` + a fresh ``--proc /proc`` so the sandbox cannot read
  ``/proc/<parent>/environ`` (the parent still holds full ``os.environ``) — the
  red-team's "Layer 0 still leaks" defeat (HARNESS_TRUST_HARDENING.md §3.2).
- ``--clearenv`` + an explicit ``--setenv`` allowlist (the already-minimized
  worker env), so nothing leaks through inherited environment either.
- read-only system + a host-pre-built toolchain (claude/cl/uv/git/venv) and the
  Claude auth dir; **never** binds ``~/.ssh ~/.gnupg ~/.aws ~/.config/gh`` — those
  are simply absent inside.
- the task workspace is the only writable real path; ``/tmp`` and ``$HOME`` are
  fresh tmpfs.

SBX Phase 3 — network confinement (D-SBX-2 = `--share-net` + L7/SNI egress
proxy). bwrap keeps the host network namespace (``--share-net``, the default) so
the sandbox can still reach the host ollama floor and the localhost proxies; the
constraint is applied at L7 by pointing the executor's egress at the allowlist
proxy via ``HTTPS_PROXY``. ``--unshare-net`` was rejected (D-SBX-2): an isolated
netns cannot reach a host-loopback proxy / ollama without a forwarder, and an L7
proxy at least logs+constrains the deliberate-exfil case. Wiring is gated on
``OC_EGRESS_PROXY`` and is **fail-open**: if it is unset or the proxy is not
listening, no proxy env is injected and the sandbox keeps direct egress (losing
all HTTPS to a dead proxy would be a §0.1 violation). localhost (ollama floor +
key-proxy) always bypasses the proxy via ``NO_PROXY``.

SELF-HEALING INVARIANT (§0.1): this is **fail-open**. If bwrap is missing, the
config flag is off, or any bind path is absent, ``maybe_sandbox`` returns the
command UNCHANGED — the fleet runs un-sandboxed rather than halting. A wrong
sandbox must never deadlock the fleet (the bootstrap-deadlock lesson from the
PATH/CL_ANCHOR regressions). Turning it on is an explicit, observed step.
"""

from __future__ import annotations

import os
import shutil
import socket
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

# HOME subdirectories that hold host credentials — NEVER bound into the sandbox.
_SECRET_HOME_DIRS = (".ssh", ".gnupg", ".aws", ".config/gh", ".config/gcloud")

# System directories the toolchain needs at runtime (read-only).
_RO_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

_SANDBOX_HOME = "/sandbox-home"

# SBX Phase 3 (D-SBX-2): when set to the egress proxy URL in the fleet env, the
# sandbox routes HTTPS egress through the L7/SNI allowlist proxy. Unset => no
# proxy wiring (fail-open: direct net, as Phase 2).
_EGRESS_PROXY_ENV_FLAG = "OC_EGRESS_PROXY"
# localhost destinations that MUST bypass the egress proxy: the host ollama floor
# and the localhost cloud-key proxy (D-OP-1) live on loopback and are not
# "egress" — routing them through the CONNECT proxy would break the local floor.
_PROXY_BYPASS = "127.0.0.1,localhost,::1"


def bwrap_available() -> bool:
    """Check if bwrap (bubblewrap) is available in the PATH."""
    return shutil.which("bwrap") is not None


def _proxy_reachable(url: str, *, timeout: float = 0.5) -> bool:
    """True if the egress proxy host:port accepts a TCP connection. This is the
    gate that keeps proxy wiring FAIL-OPEN (§0.1): if the proxy is down we inject
    nothing and the sandbox keeps direct egress rather than losing all HTTPS."""
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8889
    except ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_egress_proxy(env: dict) -> str | None:
    """The egress proxy URL to wire into the sandbox, or ``None``. Reads
    ``OC_EGRESS_PROXY`` from the controlled env (falling back to the parent
    process env, where the fleet actually sets it) and returns it only when the
    proxy is reachable — so a dead/missing proxy degrades to direct egress
    instead of breaking every HTTPS call (fail-open, §0.1)."""
    url = env.get(_EGRESS_PROXY_ENV_FLAG) or os.environ.get(_EGRESS_PROXY_ENV_FLAG)
    if not url or not _proxy_reachable(url):
        return None
    return url


def _proxy_env(url: str, *, existing_no_proxy: str = "") -> dict[str, str]:
    """PURE: the proxy env vars that route the executor's egress through ``url``.
    Sets both upper- and lower-case forms (clients differ) and a ``NO_PROXY`` that
    always exempts localhost (ollama floor + key-proxy), preserving any caller
    NO_PROXY entries."""
    no_proxy = ",".join(
        dict.fromkeys(filter(None, (*_PROXY_BYPASS.split(","), *existing_no_proxy.split(","))))
    )
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def _real(p: str | None) -> str | None:
    if not p:
        return None
    try:
        rp = os.path.realpath(p)
        return rp if os.path.exists(rp) else None
    except OSError:
        return None


def _toolchain_ro_binds(oc_root: Path, env: dict) -> list[str]:
    """Read-only host paths the executor toolchain needs. Derived from the
    resolved binaries + env so it tracks where things actually live."""
    binds: list[str] = []

    def add(p: str | None) -> None:
        rp = _real(p)
        if rp and rp not in binds:
            binds.append(rp)

    # The OC source tree (PYTHONPATH) + its venv (python/pytest/ruff).
    add(str(oc_root / "src"))
    add(str(oc_root / ".venv"))
    # Agent CLIs + their install dirs.
    for tool in ("claude", "cl", "uv", "aider", "node", "npm"):
        found = shutil.which(tool)
        if found:
            add(os.path.dirname(_real(found) or found))
    # ContextLifecycle home (cl) + the anchor manifest (.context tree).
    add(env.get("CL_HOME"))
    add(env.get("CL_ANCHOR"))
    # Claude subscription auth — required or the agent refuses.
    home = env.get("HOME") or os.path.expanduser("~")
    add(os.path.join(home, ".claude"))
    return binds


def build_sandbox_argv(
    inner_cmd: Sequence[str],
    *,
    oc_root: Path,
    rw_root: Path,
    env: dict,
    chdir: Path | None = None,
    extra_ro_binds: Sequence[str] = (),
) -> list[str]:
    """Construct the bwrap argv wrapping ``inner_cmd``. Pure/deterministic given
    its inputs (so it is unit-testable offline). Does NOT check availability —
    callers gate via ``maybe_sandbox``."""
    home = _SANDBOX_HOME
    argv: list[str] = [
        "bwrap",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        home,
    ]
    for d in _RO_SYSTEM_DIRS:
        if os.path.isdir(d):
            argv += ["--ro-bind", d, d]
    real_home = os.path.realpath(env.get("HOME") or os.path.expanduser("~"))
    secret_paths = {os.path.join(real_home, d) for d in _SECRET_HOME_DIRS}
    for src in (*_toolchain_ro_binds(oc_root, env), *extra_ro_binds):
        # Defense-in-depth: never bind a credential dir even if a toolchain path
        # somehow resolved to one (e.g. a misconfigured CL_HOME under ~/.ssh).
        if any(src == s or src.startswith(s + os.sep) for s in secret_paths):
            continue
        argv += ["--ro-bind", src, src]
    # Re-seed the Claude auth dir INTO the tmpfs home (where the agent looks).
    claude_auth = _real(os.path.join(env.get("HOME") or os.path.expanduser("~"), ".claude"))
    if claude_auth:
        argv += ["--ro-bind", claude_auth, f"{home}/.claude"]
    # The one writable real path: the per-task ephemeral dir (workspace, the
    # plan bundle, the config copy, the result file — all under it). oc_root
    # itself is NOT bound, so .env secrets and the .git token stay outside.
    argv += ["--bind", str(rw_root), str(rw_root)]
    # Controlled env (already minimized by build_allowlist_env), HOME re-pointed
    # at the sandbox tmpfs home so secrets under the real HOME are unreachable.
    for k, v in env.items():
        if v is None:
            continue
        argv += ["--setenv", k, str(v)]
    argv += ["--setenv", "HOME", home]
    argv += ["--chdir", str(chdir if chdir is not None else rw_root)]
    return [*argv, *inner_cmd]


def maybe_sandbox(
    inner_cmd: Sequence[str],
    *,
    oc_root: Path,
    rw_root: Path,
    env: dict,
    enabled: bool,
    chdir: Path | None = None,
) -> list[str]:
    """Return the bwrap-wrapped command when sandboxing is enabled AND available
    AND the workspace exists; otherwise return ``inner_cmd`` unchanged (fail-open).

    This is the single decision point: a wrong/unavailable sandbox degrades to
    the prior un-sandboxed behavior, never a halt (§0.1).
    """
    if not enabled:
        return list(inner_cmd)
    if not bwrap_available():
        return list(inner_cmd)
    try:
        if not Path(rw_root).is_dir():
            return list(inner_cmd)
        # Phase 3 (D-SBX-2): route egress through the L7/SNI allowlist proxy when
        # OC_EGRESS_PROXY is set AND reachable. The reachability check is the
        # fail-open gate (a dead proxy => no proxy env => direct egress, never a
        # halt). Computed here (the impure decision point) so build_sandbox_argv
        # stays a pure, offline-testable env→argv function.
        sandbox_env = dict(env)
        proxy_url = _resolve_egress_proxy(env)
        if proxy_url:
            sandbox_env.update(
                _proxy_env(
                    proxy_url,
                    existing_no_proxy=env.get("NO_PROXY") or env.get("no_proxy") or "",
                )
            )
        return build_sandbox_argv(
            inner_cmd, oc_root=oc_root, rw_root=rw_root, env=sandbox_env, chdir=chdir
        )
    except Exception:  # noqa: BLE001 — sandbox construction must never break dispatch
        return list(inner_cmd)


__all__ = [
    "build_sandbox_argv",
    "bwrap_available",
    "maybe_sandbox",
]
