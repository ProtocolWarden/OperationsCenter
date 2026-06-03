# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from operations_center.decision.rules.lint_fix import LintFixRule
from operations_center.insights.models import DerivedInsight

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _insight(
    *,
    kind: str = "lint_drift",
    subject: str = "lint_violations",
    status: str = "present",
    dedup_key: str = "lint_drift|lint_violations|present",
    evidence: dict[str, Any] | None = None,
    insight_id: str = "ins-1",
) -> DerivedInsight:
    return DerivedInsight(
        insight_id=insight_id,
        dedup_key=dedup_key,
        kind=kind,
        subject=subject,
        status=status,
        evidence=evidence or {},
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def _hotspot_insight() -> DerivedInsight:
    return _insight(
        kind="cross_signal",
        subject="lint_hotspot_overlap",
        status="present",
        dedup_key="cross_signal|lint_hotspot_overlap|present",
        insight_id="ins-hotspot",
    )


def test_no_insights_returns_empty() -> None:
    assert LintFixRule().evaluate([]) == []


def test_non_lint_drift_insight_skipped() -> None:
    ins = _insight(kind="other_kind")
    assert LintFixRule().evaluate([ins]) == []


def test_present_below_threshold_skipped() -> None:
    ins = _insight(evidence={"violation_count": 4})
    assert LintFixRule(min_violations=5).evaluate([ins]) == []


def test_present_at_threshold_emits_medium() -> None:
    ins = _insight(
        evidence={
            "violation_count": 5,
            "top_codes": ["E501", "F401", "W291", "E302"],
            "distinct_file_count": 3,
        }
    )
    cands = LintFixRule(min_violations=5).evaluate([ins])
    assert len(cands) == 1
    c = cands[0]
    assert c.family == "lint_fix"
    assert c.pattern_key == "violations_present"
    assert c.confidence == "medium"
    assert c.estimated_affected_files == 3
    assert c.risk_class == "style"
    assert c.expires_after_runs == 3
    assert c.priority == (1, 5, "lint_fix|violations_present|5")
    # top_codes truncated to first 3 in the rendered string.
    assert "E501, F401, W291" in c.evidence_lines[0]
    assert "E302" not in c.evidence_lines[0]
    assert c.proposal_outline.source_family == "lint_fix"
    assert c.proposal_outline.labels_hint == ["task-kind: improve", "source: proposer"]
    assert c.matched_rules == [
        "lint_violations_present_min_threshold",
        "candidate_not_seen_in_cooldown_window",
    ]


def test_present_high_count_is_high_confidence() -> None:
    ins = _insight(evidence={"violation_count": 20})
    cands = LintFixRule().evaluate([ins])
    assert cands[0].confidence == "high"


def test_present_just_below_high_count_is_medium() -> None:
    ins = _insight(evidence={"violation_count": 19})
    cands = LintFixRule().evaluate([ins])
    assert cands[0].confidence == "medium"


def test_present_no_top_codes_uses_various() -> None:
    ins = _insight(evidence={"violation_count": 6})
    cands = LintFixRule().evaluate([ins])
    assert "various" in cands[0].evidence_lines[0]
    assert "various" in cands[0].proposal_outline.title_hint


def test_present_missing_violation_count_defaults_to_zero_and_skips() -> None:
    ins = _insight(evidence={})
    # default 0 < min_violations -> skipped
    assert LintFixRule().evaluate([ins]) == []


def test_present_missing_distinct_files_yields_none() -> None:
    ins = _insight(evidence={"violation_count": 7})
    cands = LintFixRule().evaluate([ins])
    assert cands[0].estimated_affected_files is None


def test_hotspot_overlap_forces_high_and_adds_evidence() -> None:
    ins = _insight(evidence={"violation_count": 6, "top_codes": ["E501"]})
    cands = LintFixRule().evaluate([ins, _hotspot_insight()])
    c = cands[0]
    assert c.confidence == "high"
    assert any("cross-signal corroboration" in line for line in c.evidence_lines)
    assert "cross_signal_lint_hotspot_overlap" in c.matched_rules


def test_no_hotspot_overlap_omits_cross_signal_rule() -> None:
    ins = _insight(evidence={"violation_count": 6})
    c = LintFixRule().evaluate([ins])[0]
    assert "cross_signal_lint_hotspot_overlap" not in c.matched_rules
    assert len(c.evidence_lines) == 1


def test_cross_signal_wrong_subject_not_treated_as_overlap() -> None:
    other = _insight(
        kind="cross_signal",
        subject="something_else",
        dedup_key="cross_signal|something_else|present",
    )
    ins = _insight(evidence={"violation_count": 6})
    c = LintFixRule().evaluate([ins, other])[0]
    assert c.confidence == "medium"
    assert "cross_signal_lint_hotspot_overlap" not in c.matched_rules


def test_present_dedup_key_endswith_present_but_status_not_present_skipped() -> None:
    ins = _insight(
        status="resolved",
        evidence={"violation_count": 50},
    )
    # First branch requires status == "present"; dedup ends with present so the
    # worsened elif is not reached either -> no candidate.
    assert LintFixRule().evaluate([ins]) == []


def test_worsened_emits_high_confidence() -> None:
    ins = _insight(
        status="present",
        dedup_key="lint_drift|lint_violations|worsened",
        evidence={"delta": 7, "current_count": 30, "distinct_file_count": 4},
    )
    cands = LintFixRule().evaluate([ins])
    assert len(cands) == 1
    c = cands[0]
    assert c.pattern_key == "violations_worsened"
    assert c.confidence == "high"
    assert c.estimated_affected_files == 4
    assert c.priority == (1, 4, "lint_fix|violations_worsened|7")
    assert "increased by 7" in c.evidence_lines[0]
    assert "now 30 total" in c.evidence_lines[0]
    assert "+7 new ruff violations" in c.proposal_outline.title_hint
    assert c.matched_rules == [
        "lint_violations_count_increased",
        "candidate_not_seen_in_cooldown_window",
    ]


def test_worsened_missing_evidence_defaults_to_zero() -> None:
    ins = _insight(
        dedup_key="lint_drift|lint_violations|worsened",
        evidence={},
    )
    c = LintFixRule().evaluate([ins])[0]
    assert c.priority == (1, 4, "lint_fix|violations_worsened|0")
    assert c.estimated_affected_files is None
    assert "increased by 0" in c.evidence_lines[0]


def test_evidence_copied_not_aliased() -> None:
    ev = {"violation_count": 6, "top_codes": ["E501"]}
    ins = _insight(evidence=ev)
    c = LintFixRule().evaluate([ins])[0]
    c.evidence["mutated"] = True
    assert "mutated" not in ins.evidence


def test_dedup_neither_present_nor_worsened_skipped() -> None:
    ins = _insight(
        dedup_key="lint_drift|lint_violations|improved",
        status="present",
        evidence={"violation_count": 50},
    )
    assert LintFixRule().evaluate([ins]) == []


def test_multiple_insights_produce_multiple_candidates() -> None:
    present = _insight(
        insight_id="a",
        evidence={"violation_count": 10},
    )
    worsened = _insight(
        insight_id="b",
        dedup_key="lint_drift|lint_violations|worsened",
        evidence={"delta": 3, "current_count": 13},
    )
    cands = LintFixRule().evaluate([present, worsened])
    keys = sorted(c.pattern_key for c in cands)
    assert keys == ["violations_present", "violations_worsened"]


def test_non_int_violation_count_string_coerced() -> None:
    ins = _insight(evidence={"violation_count": "8"})
    c = LintFixRule().evaluate([ins])[0]
    assert c.priority == (1, 5, "lint_fix|violations_present|8")


def test_invalid_violation_count_raises_value_error() -> None:
    ins = _insight(evidence={"violation_count": "not-a-number"})
    with pytest.raises(ValueError):
        LintFixRule().evaluate([ins])


def test_custom_min_violations_threshold() -> None:
    ins = _insight(evidence={"violation_count": 8})
    assert LintFixRule(min_violations=10).evaluate([ins]) == []
    assert len(LintFixRule(min_violations=8).evaluate([ins])) == 1
