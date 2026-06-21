# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""GitHubOutcomeSource: merged-then-regressed PRs become LGTM-miss outcomes."""

from __future__ import annotations

from operations_center.eval.outcome_sources import GitHubOutcomeSource
from operations_center.post_merge_regression import RegressionSignal


def _sig(pr_number):
    return RegressionSignal(
        pr_number=pr_number,
        merge_commit_sha="abc",
        head_sha="def",
        failed_checks=("Test (pytest)",),
        merged_at="2026-06-21T00:00:00+00:00",
        base_branch="main",
    )


def _detector(signals):
    def _fn(gh, owner, repo, *, base_branch="main", lookback_hours=24):
        return list(signals)
    return _fn


def test_regressed_merged_pr_becomes_lgtm_miss():
    src = GitHubOutcomeSource(gh_client=object(), detector=_detector([_sig(42)]))
    outcomes = src()
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.pr_number == 42
    assert o.decision == "LGTM"  # merge required reviewer-verdict=success
    assert o.merged and o.main_regressed
    assert o.repo == "OperationsCenter"


def test_signal_without_pr_number_is_skipped():
    src = GitHubOutcomeSource(gh_client=object(), detector=_detector([_sig(None)]))
    assert src() == []


def test_no_regressions_yields_no_outcomes():
    src = GitHubOutcomeSource(gh_client=object(), detector=_detector([]))
    assert src() == []


def test_detector_exception_does_not_break_the_join():
    def _boom(*a, **k):
        raise RuntimeError("github down")

    src = GitHubOutcomeSource(gh_client=object(), detector=_boom)
    assert src() == []  # best-effort; a flaky source must not raise


def test_multiple_targets_are_scanned():
    src = GitHubOutcomeSource(
        gh_client=object(),
        targets=(("o", "r1"), ("o", "r2")),
        detector=_detector([_sig(7)]),
    )
    outcomes = src()
    assert {o.repo for o in outcomes} == {"r1", "r2"}
    assert all(o.pr_number == 7 for o in outcomes)
