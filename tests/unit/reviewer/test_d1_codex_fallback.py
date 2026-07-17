# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for audit D1 — the ordinary reviewer's claude→codex fallback.

These cover the pure selection/dispatch helpers that CI (``pytest tests/unit``)
runs. The full ``_phase1`` dispatch/park/guardrail integration lives in the
root ``tests/test_pr_review_watcher.py`` (which CI does not run — validate
locally).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from operations_center.entrypoints.pr_review_watcher import main as watcher


def test_review_model_for_backend_codex_matches_council_pairing() -> None:
    # The fallback reuses the validated council seat pairing (codex_cli→codex),
    # not a fresh, unvalidated model choice.
    assert watcher._review_model_for_backend("codex_cli") == "codex"


def test_review_model_for_backend_unknown_returns_none() -> None:
    # An unknown/uncovered fallback backend has no known review pairing → None,
    # so the caller PARKS rather than guessing a model.
    assert watcher._review_model_for_backend("mystery_backend") is None


def test_run_direct_review_back_compat_runs_claude_haiku() -> None:
    # Back-compat contract the test suite depends on: the no-arg alias still
    # dispatches to claude_code/haiku via _run_member_review.
    with patch.object(watcher, "_run_member_review", return_value=None) as mock_member:
        watcher._run_direct_review(Path("/nonexistent"), "goal text", "MyRepo-42")

    mock_member.assert_called_once()
    assert mock_member.call_args.kwargs["backend"] == "claude_code"
    assert mock_member.call_args.kwargs["model"] == "haiku"
