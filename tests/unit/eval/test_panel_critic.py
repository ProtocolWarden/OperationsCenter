# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""C3 — cross-family EVAL panel aggregation (per-family drift, not pooled)."""

from __future__ import annotations

import pytest

from operations_center.eval.corpus import Case
from operations_center.eval.panel_critic import PanelDriftResult, run_panel_drift_monitor


def _case(cid="c") -> Case:
    return Case(
        case_id=cid,
        kind="extraction",
        input={"diff": "..."},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    )


def _fixed(checks):
    return lambda case, *, vote: checks


_WRONG = [
    {"check_id": "code_quality", "status": "pass"},
    {"check_id": "no_tooling_artifacts", "status": "pass"},
]  # computes to LGTM — disagrees with the CONCERNS answer
_RIGHT = [
    {"check_id": "code_quality", "status": "fail"},
    {"check_id": "no_tooling_artifacts", "status": "pass"},
]  # computes to CONCERNS — matches the answer


def test_claude_family_drift_caught_even_when_codex_agrees():
    """The essential C3 property: a dominant/agreeing family can't mask a
    DIFFERENT family's drift. claude_code alone disagrees with the signed
    answer; codex_cli fully agrees. The panel must still flag drift, and
    must attribute it to claude_code specifically."""
    panel = {"claude_code": _fixed(_WRONG), "codex_cli": _fixed(_RIGHT)}
    results = run_panel_drift_monitor([_case()], panel, votes=3)
    r = results[0]
    assert isinstance(r, PanelDriftResult)
    assert r.drifted is True
    assert r.per_family["claude_code"]["majority"] != r.expected
    assert r.per_family["codex_cli"]["majority"] == r.expected


def test_codex_family_drift_caught_even_when_claude_agrees():
    """Symmetric case: codex_cli disagrees, claude_code agrees — still flagged,
    and still attributed to the actual offending family (codex_cli)."""
    panel = {"claude_code": _fixed(_RIGHT), "codex_cli": _fixed(_WRONG)}
    results = run_panel_drift_monitor([_case()], panel, votes=3)
    r = results[0]
    assert r.drifted is True
    assert r.per_family["codex_cli"]["majority"] != r.expected
    assert r.per_family["claude_code"]["majority"] == r.expected


def test_no_drift_when_every_family_agrees():
    panel = {"claude_code": _fixed(_RIGHT), "codex_cli": _fixed(_RIGHT)}
    results = run_panel_drift_monitor([_case()], panel, votes=3)
    r = results[0]
    assert r.drifted is False
    assert all(fam["majority"] == r.expected for fam in r.per_family.values())


def test_per_family_majority_vote_is_independent_per_family():
    """Each family's majority is computed from ONLY its own votes, never
    pooled with another family's — a flaky claude vote can't be smoothed out
    by codex's votes (or vice versa)."""

    def flaky_claude(case, *, vote):
        # 2 of 3 votes wrong, 1 right — majority is still wrong for this family.
        return _RIGHT if vote == 1 else _WRONG

    panel = {"claude_code": flaky_claude, "codex_cli": _fixed(_RIGHT)}
    results = run_panel_drift_monitor([_case()], panel, votes=3)
    r = results[0]
    assert r.per_family["claude_code"]["agree_votes"] == 2
    assert r.per_family["claude_code"]["total_votes"] == 3
    assert r.per_family["claude_code"]["majority"] != r.expected
    assert r.drifted is True


def test_rejects_zero_votes():
    with pytest.raises(ValueError):
        run_panel_drift_monitor([_case()], {"claude_code": _fixed(_RIGHT)}, votes=0)


def test_rejects_empty_family_extractors():
    """Empty panel has no cross-family control — callers must treat 'no
    panel configured' as feature-OFF before reaching here, not by handing an
    empty map through (see DriftMonitorTask, which does exactly that)."""
    with pytest.raises(ValueError):
        run_panel_drift_monitor([_case()], {}, votes=3)


def test_multiple_cases_each_get_their_own_result():
    panel = {"claude_code": _fixed(_RIGHT), "codex_cli": _fixed(_WRONG)}
    cases = [_case("a"), _case("b")]
    results = run_panel_drift_monitor(cases, panel, votes=1)
    assert [r.case_id for r in results] == ["a", "b"]
    assert all(r.drifted for r in results)
