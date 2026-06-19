# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Subprocess and environment helpers for board_worker."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

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
