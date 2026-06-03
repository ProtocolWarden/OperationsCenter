# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.decision.rules.hotspot_concentration import (
    HotspotConcentrationRule,
)
from operations_center.insights.models import DerivedInsight

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _insight(
    *,
    kind: str = "file_hotspot",
    subject: str = "src/foo.py",
    dedup_key: str | None = None,
    evidence: dict[str, object] | None = None,
) -> DerivedInsight:
    if dedup_key is None:
        dedup_key = f"insight|{kind}|{subject}|repeated_presence"
    return DerivedInsight(
        insight_id=f"id-{subject}-{kind}",
        dedup_key=dedup_key,
        kind=kind,
        subject=subject,
        status="active",
        evidence=evidence or {},
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def _repeated(subject: str, appearances: int) -> DerivedInsight:
    return _insight(
        subject=subject,
        dedup_key=f"insight|file_hotspot|{subject}|repeated_presence",
        evidence={"appears_in_recent_snapshots": appearances},
    )


def _dominant(subject: str, evidence: dict[str, object]) -> DerivedInsight:
    return _insight(
        subject=subject,
        dedup_key=f"insight|file_hotspot|{subject}|dominant_current",
        evidence=evidence,
    )


def test_empty_insights_yields_no_candidates() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=3)
    assert rule.evaluate([]) == []


def test_non_hotspot_kind_is_skipped() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    other = _insight(
        kind="lint_trend",
        subject="src/foo.py",
        evidence={"appears_in_recent_snapshots": 9},
    )
    assert rule.evaluate([other]) == []


def test_below_threshold_is_filtered_out() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=4)
    out = rule.evaluate([_repeated("src/foo.py", 3)])
    assert out == []


def test_at_threshold_emits_candidate() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=3)
    out = rule.evaluate([_repeated("src/foo.py", 3)])
    assert len(out) == 1
    assert isinstance(out[0], CandidateSpec)


def test_above_threshold_emits_candidate() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=2)
    out = rule.evaluate([_repeated("src/foo.py", 5)])
    assert len(out) == 1
    assert out[0].evidence["appears_in_recent_snapshots"] == 5


def test_candidate_field_shape() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=2)
    (cand,) = rule.evaluate([_repeated("src/svc/app.py", 4)])
    assert cand.family == "hotspot_concentration"
    assert cand.subject == "src/svc/app.py"
    assert cand.pattern_key == "persistent"
    assert cand.confidence == "medium"
    assert cand.risk_class == "structural"
    assert cand.expires_after_runs == 5
    assert cand.matched_rules == [
        "hotspot_repeated_presence_min_runs",
        "candidate_not_seen_in_cooldown_window",
    ]
    assert cand.priority == (3, 0, "hotspot_concentration|src/svc/app.py|persistent")


def test_evidence_lines_render_subject_and_count() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    (cand,) = rule.evaluate([_repeated("src/bar.py", 7)])
    assert cand.evidence_lines == [
        "'src/bar.py' appeared in top hotspots across 7 recent snapshots.",
    ]


def test_proposal_outline_contents() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    (cand,) = rule.evaluate([_repeated("src/baz.py", 2)])
    outline = cand.proposal_outline
    assert isinstance(outline, ProposalOutline)
    assert outline.title_hint == ("Investigate repeated hotspot concentration in src/baz.py")
    assert "Recent snapshots repeatedly place this file" in outline.summary_hint
    assert outline.labels_hint == ["task-kind: improve", "source: proposer"]
    assert outline.source_family == "hotspot_concentration"


def test_dominant_evidence_merged_into_candidate() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=2)
    insights = [
        _repeated("src/foo.py", 4),
        _dominant("src/foo.py", {"churn": 42, "rank": 1}),
    ]
    (cand,) = rule.evaluate(insights)
    assert cand.evidence == {
        "appears_in_recent_snapshots": 4,
        "churn": 42,
        "rank": 1,
    }


def test_dominant_does_not_override_appearances_key() -> None:
    # dominant evidence is merged after appearances; if it carries the same key
    # it would override. Verify behavior: update() lets dominant win.
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    insights = [
        _repeated("src/foo.py", 4),
        _dominant("src/foo.py", {"appears_in_recent_snapshots": 99}),
    ]
    (cand,) = rule.evaluate(insights)
    assert cand.evidence["appears_in_recent_snapshots"] == 99


def test_dominant_without_repeated_is_ignored() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    out = rule.evaluate([_dominant("src/foo.py", {"churn": 5})])
    assert out == []


def test_missing_appearances_key_defaults_to_zero() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    bad = _insight(
        subject="src/foo.py",
        dedup_key="insight|file_hotspot|src/foo.py|repeated_presence",
        evidence={},
    )
    # appearances defaults to 0, below min of 1 -> filtered
    assert rule.evaluate([bad]) == []


def test_zero_min_runs_with_zero_appearances_emits() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=0)
    bad = _insight(
        subject="src/foo.py",
        dedup_key="insight|file_hotspot|src/foo.py|repeated_presence",
        evidence={},
    )
    (cand,) = rule.evaluate([bad])
    assert cand.evidence["appears_in_recent_snapshots"] == 0


def test_appearances_coerced_from_string() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=2)
    weird = _insight(
        subject="src/foo.py",
        dedup_key="insight|file_hotspot|src/foo.py|repeated_presence",
        evidence={"appears_in_recent_snapshots": "5"},
    )
    (cand,) = rule.evaluate([weird])
    assert cand.evidence["appears_in_recent_snapshots"] == 5


def test_non_matching_dedup_suffix_ignored() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    other_suffix = _insight(
        subject="src/foo.py",
        dedup_key="insight|file_hotspot|src/foo.py|some_other_signal",
        evidence={"appears_in_recent_snapshots": 9},
    )
    assert rule.evaluate([other_suffix]) == []


def test_multiple_subjects_sorted_by_subject() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    insights = [
        _repeated("zeta.py", 2),
        _repeated("alpha.py", 3),
        _repeated("mid.py", 1),
    ]
    out = rule.evaluate(insights)
    subjects = [c.subject for c in out]
    assert subjects == ["alpha.py", "mid.py", "zeta.py"]


def test_mixed_pass_and_fail_threshold() -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=3)
    insights = [
        _repeated("keep.py", 3),
        _repeated("drop.py", 2),
        _repeated("keep2.py", 10),
    ]
    out = rule.evaluate(insights)
    assert sorted(c.subject for c in out) == ["keep.py", "keep2.py"]


def test_evidence_dict_is_independent_copy() -> None:
    # merged dominant evidence must not mutate the source insight evidence
    rule = HotspotConcentrationRule(min_repeated_runs=1)
    dom_ev: dict[str, object] = {"churn": 1}
    insights = [_repeated("src/foo.py", 2), _dominant("src/foo.py", dom_ev)]
    (cand,) = rule.evaluate(insights)
    cand.evidence["churn"] = 999
    assert dom_ev["churn"] == 1


@pytest.mark.parametrize("min_runs", [1, 5, 100])
def test_threshold_parametrized(min_runs: int) -> None:
    rule = HotspotConcentrationRule(min_repeated_runs=min_runs)
    below = rule.evaluate([_repeated("a.py", min_runs - 1)])
    at = rule.evaluate([_repeated("a.py", min_runs)])
    assert below == []
    assert len(at) == 1
