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


def build_env(oc_root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(oc_root / "src")
    return env


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
