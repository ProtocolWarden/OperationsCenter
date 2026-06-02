# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The first-pass definition-of-done appended to worker goals."""

from __future__ import annotations

from operations_center.entrypoints.board_worker.dispatch import _append_definition_of_done


def test_definition_of_done_preserves_goal_and_demands_completeness() -> None:
    out = _append_definition_of_done("Implement feature X")

    # Original goal is preserved verbatim at the top.
    assert out.startswith("Implement feature X")
    # The contract demands full implementation + local verification before the PR.
    assert "Definition of done" in out
    lowered = out.lower()
    assert "entirety" in lowered
    assert "test" in lowered
    assert "lint" in lowered or "linter" in lowered
    # No partial / stub hand-offs.
    assert "todo" in lowered and "stub" in lowered
