# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from operations_center.tuning.models import FamilyMetrics
from operations_center.tuning.recommendations import RecommendationEngine

engine = RecommendationEngine()


def _m(
    family: str = "fam",
    *,
    sample_runs: int = 10,
    emitted: int = 0,
    suppressed: int = 0,
    created: int = 0,
    suppression_rate: float | None = None,
    create_rate: float | None = None,
    top_suppression_reasons: dict[str, int] | None = None,
    merged: int = 0,
    escalated: int = 0,
    acceptance_rate: float = 0.0,
) -> FamilyMetrics:
    total_seen = emitted + suppressed
    if suppression_rate is None:
        suppression_rate = suppressed / total_seen if total_seen else 0.0
    if create_rate is None:
        create_rate = created / emitted if emitted else 0.0
    return FamilyMetrics(
        family=family,
        sample_runs=sample_runs,
        candidates_emitted=emitted,
        candidates_suppressed=suppressed,
        candidates_created=created,
        suppression_rate=suppression_rate,
        create_rate=create_rate,
        top_suppression_reasons=top_suppression_reasons or {},
        proposals_merged=merged,
        proposals_escalated=escalated,
        acceptance_rate=acceptance_rate,
    )


# --- over-suppressed: top reason resolution ------------------------------


def test_over_suppressed_picks_highest_top_reason() -> None:
    rec = engine._evaluate_one(
        _m(
            sample_runs=12,
            emitted=1,
            suppressed=19,
            suppression_rate=0.95,
            top_suppression_reasons={"min_consecutive_runs": 5, "cooldown": 12},
        )
    )
    assert rec.action == "loosen_threshold"
    assert rec.evidence["top_suppression_reason"] == "cooldown"
    assert "cooldown" in rec.rationale


def test_over_suppressed_unknown_reason_when_empty_map() -> None:
    rec = engine._evaluate_one(_m(sample_runs=12, emitted=1, suppressed=19, suppression_rate=0.95))
    assert rec.action == "loosen_threshold"
    assert rec.evidence["top_suppression_reason"] == "unknown"


def test_over_suppressed_at_exact_threshold() -> None:
    rec = engine._evaluate_one(_m(sample_runs=6, emitted=10, suppressed=90, suppression_rate=0.90))
    assert rec.action == "loosen_threshold"
    # sample_runs 6 < 10 -> medium
    assert rec.confidence == "medium"


# --- noisy/tighten confidence boundaries ---------------------------------


def test_noisy_high_confidence_at_ten_emitted() -> None:
    rec = engine._evaluate_one(
        _m(sample_runs=10, emitted=10, suppressed=0, created=0, create_rate=0.0)
    )
    assert rec.action == "tighten_threshold"
    assert rec.confidence == "high"


def test_noisy_medium_confidence_below_ten_emitted() -> None:
    rec = engine._evaluate_one(
        _m(sample_runs=10, emitted=5, suppressed=0, created=0, create_rate=0.0)
    )
    assert rec.action == "tighten_threshold"
    assert rec.confidence == "medium"


def test_noisy_at_ceiling_create_rate() -> None:
    # create_rate exactly at ceiling (0.10) still noisy (<=)
    rec = engine._evaluate_one(_m(sample_runs=10, emitted=10, created=1, create_rate=0.10))
    assert rec.action == "tighten_threshold"


# --- healthy confidence boundaries ---------------------------------------


def test_healthy_high_confidence_at_five_emitted() -> None:
    rec = engine._evaluate_one(_m(sample_runs=10, emitted=5, created=2, create_rate=0.40))
    assert rec.action == "keep"
    assert rec.confidence == "high"


def test_healthy_medium_confidence_below_five_emitted() -> None:
    rec = engine._evaluate_one(_m(sample_runs=10, emitted=3, created=1, create_rate=0.333))
    assert rec.action == "keep"
    assert rec.confidence == "medium"


def test_healthy_at_floor_create_rate() -> None:
    rec = engine._evaluate_one(_m(sample_runs=10, emitted=4, created=1, create_rate=0.25))
    assert rec.action == "keep"


# --- low acceptance rate -> tighten + tier demotion ----------------------
# To reach the acceptance branches we must avoid the noisy and healthy
# branches: emitted in [HEALTHY_MIN_EMITTED..) but create_rate between
# the noisy ceiling and the healthy floor (e.g. 0.20).


def test_low_acceptance_tightens_and_demotes_tier() -> None:
    rec = engine._evaluate_one(
        _m(
            sample_runs=12,
            emitted=5,
            created=1,
            create_rate=0.20,
            merged=1,
            escalated=9,
            acceptance_rate=0.10,
        )
    )
    assert rec.action == "tighten_threshold"
    assert rec.suggested_change is not None
    assert rec.suggested_change["autonomy_tier"]["direction"] == "decrease"
    assert rec.confidence == "high"  # feedback_total 10 >= 10


def test_low_acceptance_medium_confidence_small_feedback() -> None:
    rec = engine._evaluate_one(
        _m(
            sample_runs=12,
            emitted=5,
            created=1,
            create_rate=0.20,
            merged=1,
            escalated=4,
            acceptance_rate=0.20,
        )
    )
    assert rec.action == "tighten_threshold"
    assert rec.confidence == "medium"  # feedback_total 5 < 10


# --- high acceptance rate -> keep + tier promotion -----------------------


def test_high_acceptance_keeps_and_promotes_tier() -> None:
    rec = engine._evaluate_one(
        _m(
            sample_runs=12,
            emitted=5,
            created=1,
            create_rate=0.20,
            merged=9,
            escalated=1,
            acceptance_rate=0.90,
        )
    )
    assert rec.action == "keep"
    assert rec.suggested_change is not None
    assert rec.suggested_change["autonomy_tier"]["direction"] == "increase"
    assert rec.confidence == "high"


def test_high_acceptance_medium_confidence_small_feedback() -> None:
    rec = engine._evaluate_one(
        _m(
            sample_runs=12,
            emitted=5,
            created=1,
            create_rate=0.20,
            merged=4,
            escalated=1,
            acceptance_rate=0.80,
        )
    )
    assert rec.action == "keep"
    assert rec.confidence == "medium"


# --- final fallback: moderate / not enough signal ------------------------


def test_moderate_review_fallback_no_feedback() -> None:
    # emitted between healthy floor needs, create_rate in dead zone, no feedback
    rec = engine._evaluate_one(_m(sample_runs=10, emitted=5, created=1, create_rate=0.20))
    assert rec.action == "review"
    assert rec.confidence == "low"
    assert "monitor" in rec.rationale.lower()


def test_moderate_review_feedback_in_neutral_band() -> None:
    # acceptance_rate between LOW (0.3) and HIGH (0.8) -> neither branch fires
    rec = engine._evaluate_one(
        _m(
            sample_runs=10,
            emitted=5,
            created=1,
            create_rate=0.20,
            merged=5,
            escalated=5,
            acceptance_rate=0.50,
        )
    )
    assert rec.action == "review"
    assert rec.confidence == "low"


# --- evaluate over a heterogeneous batch ---------------------------------


def test_evaluate_batch_covers_multiple_actions() -> None:
    recs = engine.evaluate(
        [
            _m("a", sample_runs=2),  # no_data
            _m("b", sample_runs=10, emitted=0, suppressed=0),  # silent review
            _m("c", sample_runs=10, emitted=5, created=2, create_rate=0.40),  # keep
        ]
    )
    actions = {r.family: r.action for r in recs}
    assert actions == {"a": "no_data", "b": "review", "c": "keep"}
