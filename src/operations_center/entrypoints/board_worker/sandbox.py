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

SELF-HEALING INVARIANT (§0.1): this is **fail-open**. If bwrap is missing, the
config flag is off, or any bind path is absent, ``maybe_sandbox`` returns the
command UNCHANGED — the fleet runs un-sandboxed rather than halting. A wrong
sandbox must never deadlock the fleet (the bootstrap-deadlock lesson from the
PATH/CL_ANCHOR regressions). Turning it on is an explicit, observed step.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

# HOME subdirectories that hold host credentials — NEVER bound into the sandbox.
_SECRET_HOME_DIRS = (".ssh", ".gnupg", ".aws", ".config/gh", ".config/gcloud")

# System directories the toolchain needs at runtime (read-only).
_RO_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

_SANDBOX_HOME = "/sandbox-home"


def bwrap_available() -> bool:
    """Check if bwrap (bubblewrap) is available in the PATH."""
    return shutil.which("bwrap") is not None


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
    for src in (*_toolchain_ro_binds(oc_root, env), *extra_ro_binds):
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
        return build_sandbox_argv(inner_cmd, oc_root=oc_root, rw_root=rw_root, env=env, chdir=chdir)
    except Exception:  # noqa: BLE001 — sandbox construction must never break dispatch
        return list(inner_cmd)


__all__ = [
    "build_sandbox_argv",
    "bwrap_available",
    "maybe_sandbox",
]
