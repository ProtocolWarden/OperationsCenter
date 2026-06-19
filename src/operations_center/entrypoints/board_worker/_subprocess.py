# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Subprocess and environment helpers for board_worker."""

from __future__ import annotations

import os
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

# Minimal environment allowlist: only includes vars worker processes genuinely need.
# Principle: Least privilege. No inherited secrets (GITHUB_TOKEN, AWS_*, PLANE_API_KEY, etc.).
# Static keys are defined here; GITHUB_ACTIONS is read at runtime in build_allowlist_env.
MINIMAL_ENV_ALLOWLIST = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "CI": "true",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
}


def build_allowlist_env(oc_root: Path) -> dict:
    """Build a minimalist environment for worker subprocesses.

    Returns only whitelisted variables to prevent exposure of host secrets
    (GitHub tokens, API keys, AWS credentials, etc.) to untrusted worker code.
    """
    env = dict(MINIMAL_ENV_ALLOWLIST)
    env["PYTHONPATH"] = str(oc_root / "src")
    env["GITHUB_ACTIONS"] = os.environ.get("GITHUB_ACTIONS", "false")
    return env


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
