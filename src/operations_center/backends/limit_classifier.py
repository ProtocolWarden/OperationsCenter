# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Classify Claude/Codex CLI limit messages into a kind + model.

A worker backend (``claude_code``) runs several models (sonnet, opus, haiku),
each with an independent weekly quota, plus account-wide limits that apply to
every model at once (the rolling 5-hour session window and the org/account
weekly cap). The plain "is the backend cooling down?" signal collapses all of
these together and reads as inaccurate — a burnt Sonnet weekly does NOT stop
haiku or opus.

This module extracts two orthogonal facts from a limit message:

* ``limit_kind`` — *why* the backend is limited:
    - ``model_weekly``  : one model's weekly quota (others still runnable)
    - ``session_5h``    : the rolling 5-hour session window (all models)
    - ``global_weekly`` : account/org weekly cap (all models)
    - ``generic``       : a limit was detected but couldn't be classified
* ``model`` — which model the message named (``sonnet`` | ``opus`` | ``haiku``
    | ``codex``), or ``None`` when the limit is account-wide / unattributed.

Both the executor worker-backend round-robin and the watchdog controller feed
this into the usage store so the status surfaces can show per-model state.
"""

from __future__ import annotations

import re

# Public API of this module — declared explicitly (consumed library; some
# functions are tested as the boundary but not all internally wired).
__all__ = [
    "detect_model",
    "classify_limit",
    "models_affected",
]

# limit_kind values
MODEL_WEEKLY = "model_weekly"
SESSION_5H = "session_5h"
GLOBAL_WEEKLY = "global_weekly"
GENERIC = "generic"

# Models a worker backend may run, keyed by canonical worker_backend name.
WORKER_BACKEND_MODELS: dict[str, tuple[str, ...]] = {
    "claude_code": ("sonnet", "opus", "haiku"),
    "codex_cli": ("codex",),
}

_MODEL_RE = re.compile(r"\b(sonnet|opus|haiku|codex)\b", re.IGNORECASE)

# Account/session limits apply to every model under the backend.
_SESSION_RE = re.compile(r"5.?hour|five.?hour|session\s+(?:limit|usage)", re.IGNORECASE)
_GLOBAL_RE = re.compile(
    r"account\s+limit|organi[sz]ation\s+limit|org\s+limit|all\s+models", re.IGNORECASE
)
_WEEKLY_RE = re.compile(r"weekly\s+limit|per.?week|this\s+week", re.IGNORECASE)
# Any signal that a limit/quota was hit at all.
_LIMIT_SIGNAL_RE = re.compile(
    r"rate\s*limit|usage\s*limit|weekly\s*limit|quota|too\s+many\s+requests|429"
    r"|hit\s+your[^\n]{0,40}limit|sonnet\s+limit|opus\s+limit|haiku\s+limit"
    r"|claude\s+limit|session\s+limit",
    re.IGNORECASE,
)


def detect_model(text: str | None, *, default: str | None = None) -> str | None:
    """Return the first model named in ``text`` (lower-cased), else ``default``."""
    if not text:
        return default
    match = _MODEL_RE.search(text)
    return match.group(1).lower() if match else default


def classify_limit(
    text: str | None, *, default_model: str | None = None
) -> tuple[str | None, str | None]:
    """Return ``(limit_kind, model)`` for a CLI limit message.

    ``limit_kind`` is ``None`` only when ``text`` carries no limit signal at all
    (callers that already know a limit fired should treat ``None`` as
    ``generic``). ``model`` is ``None`` for account-wide limits.
    """
    if not text:
        return (None, default_model)

    model = detect_model(text, default=default_model)

    # Order matters: a 5-hour/session window is global even if a model is named.
    if _SESSION_RE.search(text):
        return (SESSION_5H, None)
    if _GLOBAL_RE.search(text):
        return (GLOBAL_WEEKLY, None)
    if _WEEKLY_RE.search(text):
        # "Sonnet weekly limit" → model-specific; bare "weekly limit" → global.
        return (MODEL_WEEKLY, model) if model else (GLOBAL_WEEKLY, None)
    if model:
        # e.g. "You've hit your Sonnet limit" with no explicit "weekly" word.
        return (MODEL_WEEKLY, model)
    if _LIMIT_SIGNAL_RE.search(text):
        return (GENERIC, model)
    return (None, model)


def models_affected(
    worker_backend: str, limit_kind: str | None, model: str | None
) -> tuple[str, ...]:
    """Which of ``worker_backend``'s models a limit takes down.

    A ``model_weekly`` limit affects only the named model; account-wide kinds
    (``session_5h``/``global_weekly``) affect every model; an unattributed
    ``generic`` limit conservatively affects all models.
    """
    known = WORKER_BACKEND_MODELS.get(worker_backend, ())
    if limit_kind == MODEL_WEEKLY and model:
        return (model,) if model in known else (model,)
    return known
