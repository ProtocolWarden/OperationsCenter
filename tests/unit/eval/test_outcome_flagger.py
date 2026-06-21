# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Component 2 outcome-correlation flagger: tickets, never a metric."""

from __future__ import annotations

from operations_center.eval.outcome_flagger import (
    ReviewOutcome,
    flag_disagreements,
)


def test_lgtm_then_regression_flags_reviewer():
    out = flag_disagreements([
        ReviewOutcome(1, "LGTM", merged=True, main_regressed=True, repo="OC"),
    ])
    assert len(out) == 1
    assert out[0].kind == "lgtm_then_regression"
    assert out[0].attribution == "reviewer"
    assert out[0].dedup_key == "OC#1:lgtm_then_regression"


def test_requeue_to_death_attributes_to_worker_not_reviewer():
    # D-EVAL-4: requeue-to-death is worker non-convergence, NOT reviewer over-flag.
    out = flag_disagreements([
        ReviewOutcome(2, "CONCERNS", requeued_to_death=True, repo="OC"),
    ])
    assert len(out) == 1
    assert out[0].kind == "requeue_to_death"
    assert out[0].attribution == "worker"


def test_clean_lgtm_no_regression_produces_no_flag():
    out = flag_disagreements([
        ReviewOutcome(3, "LGTM", merged=True, main_regressed=False),
    ])
    assert out == []


def test_lgtm_not_merged_does_not_flag():
    out = flag_disagreements([
        ReviewOutcome(4, "LGTM", merged=False, main_regressed=True),
    ])
    assert out == []


def test_concerns_without_requeue_does_not_flag():
    out = flag_disagreements([ReviewOutcome(5, "CONCERNS", merged=False)])
    assert out == []


def test_a_pr_can_raise_both_flags():
    out = flag_disagreements([
        ReviewOutcome(6, "LGTM", merged=True, main_regressed=True, requeued_to_death=True),
    ])
    kinds = {f.kind for f in out}
    assert kinds == {"lgtm_then_regression", "requeue_to_death"}


def test_flagger_returns_no_metric_only_tickets():
    """The flagger must never expose a precision/recall number — just Disagreements."""
    out = flag_disagreements([
        ReviewOutcome(7, "LGTM", merged=True, main_regressed=True),
        ReviewOutcome(8, "CONCERNS", requeued_to_death=True),
    ])
    # Every element is an adjudication ticket; there is no rate/score anywhere.
    assert all(hasattr(f, "dedup_key") and hasattr(f, "attribution") for f in out)
    assert all(not isinstance(f, (int, float)) for f in out)
