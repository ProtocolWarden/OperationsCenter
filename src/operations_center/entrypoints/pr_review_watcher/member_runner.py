# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Shared CLI-argv builder for one review-panel member (C1/C3).

Extracted from ``pr_review_watcher/main.py`` (a pure move, no logic change) so
that code outside the reviewer's merge-critical module — namely the EVAL
cross-family panel invoker (``eval/panel_invoker.py``, C3) — can build the
exact same backend/model CLI invocation the live council uses, without
importing ``main.py`` itself (which pulls in the full reviewer state
machine). ``main.py`` keeps a thin alias so its own callers/tests are
unaffected.
"""

from __future__ import annotations


def build_member_argv(backend: str, model: str, prompt: str) -> list[str] | None:
    """Build the CLI argv for one review-panel member.

    Mirrors :func:`worker_backend_probe._probe_command` — the same binary/flag
    shape the controller and the cooldown-probe already use — so the reviewer's
    own invocation matches the rest of the fleet instead of a bespoke one-off.
    Returns ``None`` for an unsupported ``(backend, model)`` pair.
    """
    if backend == "claude_code":
        # Preserve the live single-review invocation exactly (only the model
        # varies per council seat): `--effort low` keeps reviews cheap+fast, and
        # NOT passing --dangerously-skip-permissions matches the path that has
        # run in production — a reviewer in an empty tmpdir needs neither.
        return [
            "claude",
            "--model",
            model,
            "-p",
            "--effort",
            "low",
            prompt,
        ]
    if backend == "codex_cli":
        return [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            prompt,
        ]
    return None


__all__ = ["build_member_argv"]
