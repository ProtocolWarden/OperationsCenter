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
        # `--effort low` keeps reviews cheap+fast.
        #
        # `--permission-mode acceptEdits` is REQUIRED, not a convenience. The
        # member's one required action is writing verdict.json to its cwd, and
        # under the default permission mode a non-interactive `-p` run cannot
        # write at all: the CLI denies the write, the model reports success in
        # prose, and the process still exits rc=0. The reviewer then finds no
        # verdict.json, records "no verdict", and the fail-safe scores the PR
        # CONCERNS — publishing a FAILING reviewer-verdict status and burning a
        # fix-ladder attempt on a PR nobody actually reviewed. Diagnosed live
        # 2026-08-13 after a fresh CLI install; the previous host evidently
        # carried a permissive user-level settings file that masked this.
        #
        # Deliberately NOT --dangerously-skip-permissions. A council member
        # reads attacker-influenceable text (the PR diff), so the injection
        # threat in COUNCIL_VERDICT.md is live: bypassPermissions would hand an
        # injected instruction full Bash. acceptEdits grants file writes only —
        # verified on this host that a Bash escape attempt under acceptEdits is
        # refused and writes stay confined to the member's temp cwd.
        return [
            "claude",
            "--model",
            model,
            "-p",
            "--effort",
            "low",
            "--permission-mode",
            "acceptEdits",
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
