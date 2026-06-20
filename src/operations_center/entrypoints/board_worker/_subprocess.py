# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Subprocess and environment helpers for board_worker."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .sandbox import maybe_sandbox

# SBX Phase 2: the bwrap sandbox is OFF by default and enabled by setting this
# env var to "1" in the fleet environment. Off-by-default + fail-open means
# merging the sandbox changes nothing in production until it is deliberately
# turned on and observed (the staged rollout the trust-hardening §0.1 requires).
_SANDBOX_ENV_FLAG = "OC_BWRAP_SANDBOX"

logger = logging.getLogger(__name__)

# Keep the persisted diagnostics bounded — enough to diagnose, not unbounded.
_DIAG_MAX_STREAM_CHARS = 200_000
_DIAG_TAIL_CHARS = 1200

_TRANSIENT_CATEGORIES = {"backend_error", "timeout"}
_TRANSIENT_REASON_PATTERNS = (
    "connection refused",
    "connection reset",
    "timed out",
    "timeout",
    "502",
    "503",
    "504",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "remote disconnected",
    "network is unreachable",
    "temporary failure",
)

# Pinned, non-secret base env. Static values, never inherited from the parent.
MINIMAL_ENV_ALLOWLIST = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "CI": "true",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
}

# Agent-backend CLIs (claude_code → `claude`, codex_cli → `codex`, aider_local →
# `aider`) plus the `cl`/`uv` toolchain install into USER-LOCAL bin dirs
# (e.g. ~/.local/bin, a repo-local bin) that the pinned system PATH omits.
# Pinning PATH to the system dirs alone hides them, so dispatch hard-fails with
# "<bin> not found in PATH" — a fleet-halt and a §0.1 self-healing violation.
# We discover each tool's dir from the PARENT PATH (the fleet process resolves
# them fine) and prepend only those specific dirs — not the whole parent PATH —
# preserving the Phase-0 blast-radius cut while keeping the backends runnable.
_EXECUTOR_TOOLS = ("claude", "codex", "aider", "cl", "uv", "node", "npm", "git")


def executor_path() -> str:
    """The PATH for worker subprocesses: pinned system dirs, with the dirs that
    actually hold the agent-backend CLIs prepended (discovered from the parent
    PATH). ``~/.local/bin`` is always prepended when ``HOME`` is set — the
    canonical user-local install dir for these tools (a missing dir on PATH is
    harmless). The parent PATH itself is NOT inherited — only the specific dirs
    that hold the needed tools — so the Phase-0 blast-radius cut is preserved."""
    base = MINIMAL_ENV_ALLOWLIST["PATH"]
    base_dirs = base.split(":")
    extra: list[str] = []

    def _add(directory: str | None) -> None:
        if directory and directory not in base_dirs and directory not in extra:
            extra.append(directory)

    home = os.environ.get("HOME")
    if home:
        _add(os.path.join(home, ".local", "bin"))
    for tool in _EXECUTOR_TOOLS:
        found = shutil.which(tool)
        if found:
            _add(os.path.dirname(found))
    return ":".join([*extra, base]) if extra else base

# Operational + model-access vars forwarded from the parent IF present. These are
# load-bearing: the worker toolchain needs HOME/cache dirs, and it MUST be able to
# reach a model or the fleet cannot run/review/fix — the self-healing invariant
# (HARNESS_TRUST_HARDENING.md §0.1). Model creds + the git token stay in Phase 0;
# full cloud-key containment is Phase 3 (the localhost key-proxy).
_ENV_PASSTHROUGH = (
    # operational
    "HOME",
    "USER",
    "LOGNAME",
    "TERM",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    # model access (local ollama floor + cloud backends)
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OLLAMA_API_BASE",
    "OLLAMA_HOST",
    # ContextLifecycle anchoring. OC's CLAUDE.md ContextGuard REQUIRES CL_ANCHOR:
    # an agent dispatched into the OC clone without it returns a prose refusal
    # ("CL_ANCHOR is not set… run `eval $(cl session start …)`") instead of a JSON
    # plan, so the planner stage fails and the whole run dies. operations-center.sh
    # deliberately sets CL_ANCHOR for the fleet; this forwards it (and the cl
    # session context) to the executor subprocess so the agent stays anchored and
    # cl_dispatch_wrap()'s hydrate/capture is not silently disabled. Dropping it was
    # a Phase-0 over-minimization (regressed the #311 CL_ANCHOR unblock).
    "CL_ANCHOR",
    "CL_HOME",
    "CL_SESSION_ID",
)

# Never forwarded, even if a caller adds them to `passthrough`. The worker provably
# does not need these; dropping them is the Phase-0 blast-radius cut.
_ENV_DENY = frozenset(
    {
        "PLANE_API_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
)


def build_allowlist_env(oc_root: Path, *, passthrough: Sequence[str] = ()) -> dict:
    """Build a minimized environment for worker subprocesses.

    Starts from a pinned non-secret base and forwards only an explicit allowlist of
    operational + model-access vars (`_ENV_PASSTHROUGH`) plus any caller-supplied
    names in ``passthrough`` (e.g. the *active* repo's git-token var, so push still
    works while sibling-repo tokens, the Plane token, and host secrets are dropped).
    Names in ``_ENV_DENY`` are never forwarded.

    The allowlist intentionally preserves model creds and the active git token so a
    minimized worker can still run/review/fix/merge — dropping them would hard-halt
    the fleet, violating the self-healing invariant (HARNESS_TRUST_HARDENING.md
    §0.1). Tighter cloud-key containment is Phase 3.
    """
    env = dict(MINIMAL_ENV_ALLOWLIST)
    # Prepend the agent-tool dirs so claude_code/codex_cli/aider_local can find
    # their CLI binaries (the pinned system PATH alone omits ~/.local/bin etc.).
    env["PATH"] = executor_path()
    env["PYTHONPATH"] = str(oc_root / "src")
    env["GITHUB_ACTIONS"] = os.environ.get("GITHUB_ACTIONS", "false")
    for name in (*_ENV_PASSTHROUGH, *passthrough):
        if name in _ENV_DENY:
            continue
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def git_token_passthrough(settings, repo_cfg) -> tuple[str, ...]:
    """The env-var name holding the ACTIVE repo's git token, if any.

    Forwarded into the minimized worker env (via ``build_allowlist_env``'s
    ``passthrough``) so push still works, while sibling repos' token vars are left
    behind. A repo-specific ``token_env`` overrides the global git token var.
    """
    name = None
    if repo_cfg is not None:
        name = getattr(repo_cfg, "token_env", None)
    if not name:
        git_cfg = getattr(settings, "git", None)
        name = getattr(git_cfg, "token_env", None) if git_cfg is not None else None
    return (name,) if name else ()


def build_env(oc_root: Path) -> dict:
    """Deprecated: use build_allowlist_env() instead."""
    return build_allowlist_env(oc_root)


def venv_python(oc_root: Path) -> str:
    p = oc_root / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


def is_transient_failure(result: dict) -> bool:
    """Return True when an execution failure looks like a transient blip.

    Conservative match: requires category to be backend_error or timeout
    AND the reason text to contain a network-shaped phrase. Avoids
    over-retrying genuine bugs.
    """
    cat = (result.get("failure_category") or "").lower()
    if cat not in _TRANSIENT_CATEGORIES:
        return False
    reason = (result.get("failure_reason") or "").lower()
    return any(p in reason for p in _TRANSIENT_REASON_PATTERNS)


def persist_failure_diagnostics(
    result: dict,
    oc_root: Path,
    role: str,
    short_id: str,
    proc,
    result_text: str = "",
) -> Path | None:
    """Persist the executor subprocess output for a FAILED dispatch and enrich
    ``result['failure_reason']`` with a pointer, so the failure is investigable.

    Why this exists: the executor captures stdout/stderr (``capture_output=True``)
    but on every failure path that output is discarded; the ``team_executor``
    library persists no run artifacts; and the task records only a summary like
    "N of N stages failed". So a recurring execution failure cannot be
    root-caused — the controller can only blind-requeue. This writes the raw
    output to ``logs/local/failures/<role>-<short_id>.log`` (a durable, operator-
    and controller-readable artifact) and appends a ``[diagnostics: <path>]``
    pointer plus a short tail to the failure reason that flows into the task
    comment. Best-effort: never raises (a diagnostics-write failure must not turn
    a recoverable task failure into a crash).
    """
    try:
        out_dir = oc_root / "logs" / "local" / "failures"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{role}-{short_id}.log"
        stdout = (getattr(proc, "stdout", "") or "")[-_DIAG_MAX_STREAM_CHARS:]
        stderr = (getattr(proc, "stderr", "") or "")[-_DIAG_MAX_STREAM_CHARS:]
        rc = getattr(proc, "returncode", "?")
        path.write_text(
            f"# role={role} task={short_id} returncode={rc}\n"
            f"# === stderr ===\n{stderr}\n"
            f"# === stdout ===\n{stdout}\n"
            f"# === result.json ===\n{result_text}\n",
            encoding="utf-8",
        )
        tail = (stderr.strip() or stdout.strip())[-_DIAG_TAIL_CHARS:]
        base = result.get("failure_reason") or result.get("status") or "execution failed"
        result["failure_reason"] = f"{base} [diagnostics: {path}]"
        if tail:
            result["failure_reason"] += f"\n--- executor tail ---\n{tail}"
        logger.warning(
            "board_worker[%s]: task=%s failure diagnostics → %s", role, short_id, path
        )
        return path
    except Exception as exc:  # noqa: BLE001 — diagnostics must never crash dispatch
        logger.warning("board_worker[%s]: failed to persist diagnostics — %s", role, exc)
        return None


def run_executor(
    cmd: Sequence[str],
    *,
    oc_root: Path,
    rw_root: Path,
    workspace: Path,
    env: dict,
) -> subprocess.CompletedProcess:
    """Spawn the executor subprocess, optionally inside the bwrap sandbox.

    Centralizes the SBX Phase 2 wrap so dispatch's spawn sites stay one-liners.
    The sandbox is gated on ``OC_BWRAP_SANDBOX=1`` and is fail-open: when off,
    unavailable, or unconstructable it runs the command exactly as before. The
    outer ``env`` is still passed (harmless: bwrap ``--clearenv`` + ``--setenv``
    re-establishes a controlled env inside; un-sandboxed it is the real env)."""
    enabled = os.environ.get(_SANDBOX_ENV_FLAG) == "1"
    run_cmd = maybe_sandbox(
        cmd, oc_root=oc_root, rw_root=rw_root, env=env, enabled=enabled, chdir=workspace
    )
    return subprocess.run(run_cmd, cwd=oc_root, env=env, capture_output=True, text=True)
