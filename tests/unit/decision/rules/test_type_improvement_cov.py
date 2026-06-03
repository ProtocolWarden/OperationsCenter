# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.decision.rules.type_improvement import TypeImprovementRule
from operations_center.insights.models import DerivedInsight

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _insight(
    *,
    kind: str = "type_health",
    subject: str = "type_errors",
    status: str = "present",
    dedup_key: str = "type_health|type_errors|present",
    evidence: dict[str, Any] | None = None,
) -> DerivedInsight:
    return DerivedInsight(
        insight_id=f"id-{dedup_key}",
        dedup_key=dedup_key,
        kind=kind,
        subject=subject,
        status=status,
        evidence=evidence or {},
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def _hotspot_overlap() -> DerivedInsight:
    return _insight(
        kind="cross_signal",
        subject="type_hotspot_overlap",
        status="present",
        dedup_key="cross_signal|type_hotspot_overlap|present",
    )


def test_no_insights_returns_empty() -> None:
    assert TypeImprovementRule().evaluate([]) == []


def test_non_type_health_insight_ignored() -> None:
    other = _insight(kind="lint_health", dedup_key="lint_health|x|present")
    assert TypeImprovementRule().evaluate([other]) == []


def test_present_below_threshold_skipped() -> None:
    ins = _insight(evidence={"error_count": 2})
    assert TypeImprovementRule(min_errors=3).evaluate([ins]) == []


def test_present_at_threshold_emits_candidate() -> None:
    ins = _insight(
        evidence={
            "error_count": 3,
            "source": "ty",
            "top_codes": ["e1", "e2", "e3", "e4"],
            "distinct_file_count": 5,
        }
    )
    out = TypeImprovementRule(min_errors=3).evaluate([ins])
    assert len(out) == 1
    spec = out[0]
    assert isinstance(spec, CandidateSpec)
    assert spec.family == "type_fix"
    assert spec.pattern_key == "errors_present"
    assert spec.confidence == "medium"
    assert spec.estimated_affected_files == 5
    # Only top 3 codes used.
    assert "e1, e2, e3" in spec.proposal_outline.title_hint
    assert "e4" not in spec.proposal_outline.title_hint
    assert spec.priority == (1, 5, "type_fix|errors_present|3")
    assert spec.matched_rules == [
        "type_errors_present_min_threshold",
        "candidate_not_seen_in_cooldown_window",
    ]
    # Evidence dict is copied, not the same object.
    assert spec.evidence == ins.evidence
    assert spec.evidence is not ins.evidence


def test_present_high_confidence_via_count() -> None:
    ins = _insight(evidence={"error_count": 10})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert spec.confidence == "high"


def test_present_medium_confidence_below_ten_no_overlap() -> None:
    ins = _insight(evidence={"error_count": 9})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert spec.confidence == "medium"


def test_present_hotspot_overlap_high_confidence_and_extra_rule() -> None:
    ins = _insight(evidence={"error_count": 3})
    out = TypeImprovementRule().evaluate([ins, _hotspot_overlap()])
    spec = out[0]
    assert spec.confidence == "high"
    assert "cross_signal_type_hotspot_overlap" in spec.matched_rules
    assert any("cross-signal corroboration" in line for line in spec.evidence_lines)
    assert len(spec.evidence_lines) == 2


def test_present_no_overlap_single_evidence_line() -> None:
    ins = _insight(evidence={"error_count": 4})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert len(spec.evidence_lines) == 1
    assert "cross_signal_type_hotspot_overlap" not in spec.matched_rules


def test_present_defaults_when_evidence_missing_fields() -> None:
    ins = _insight(evidence={"error_count": 5})
    spec = TypeImprovementRule().evaluate([ins])[0]
    # default source and "various" codes
    assert "type checker reports 5 type error(s)" in spec.evidence_lines[0]
    assert "various" in spec.evidence_lines[0]
    assert "various" in spec.proposal_outline.title_hint
    assert spec.estimated_affected_files is None


def test_present_empty_top_codes_uses_various() -> None:
    ins = _insight(evidence={"error_count": 5, "top_codes": []})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert "various" in spec.evidence_lines[0]


def test_present_missing_error_count_defaults_zero_skips() -> None:
    ins = _insight(evidence={})
    # count defaults to 0, below min_errors -> skipped
    assert TypeImprovementRule().evaluate([ins]) == []


def test_present_distinct_files_zero_is_kept() -> None:
    ins = _insight(evidence={"error_count": 5, "distinct_file_count": 0})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert spec.estimated_affected_files == 0


def test_present_proposal_outline_fields() -> None:
    ins = _insight(evidence={"error_count": 7, "source": "mypy", "top_codes": ["x"]})
    outline = TypeImprovementRule().evaluate([ins])[0].proposal_outline
    assert isinstance(outline, ProposalOutline)
    assert outline.source_family == "type_fix"
    assert outline.labels_hint == ["task-kind: improve", "source: proposer"]
    assert "mypy found 7 type error(s)" in outline.summary_hint


def test_present_status_not_present_skips_present_branch() -> None:
    # dedup_key ends with present but status != present -> neither branch fires
    ins = _insight(status="absent", evidence={"error_count": 10})
    assert TypeImprovementRule().evaluate([ins]) == []


def test_worsened_branch_emits_candidate() -> None:
    ins = _insight(
        status="present",
        dedup_key="type_health|type_errors|worsened",
        evidence={"delta": 4, "current_count": 12, "distinct_file_count": 3},
    )
    out = TypeImprovementRule().evaluate([ins])
    assert len(out) == 1
    spec = out[0]
    assert spec.pattern_key == "errors_worsened"
    assert spec.confidence == "high"
    assert spec.estimated_affected_files == 3
    assert spec.priority == (1, 4, "type_fix|errors_worsened|4")
    assert spec.matched_rules == [
        "type_errors_count_increased",
        "candidate_not_seen_in_cooldown_window",
    ]
    assert "increased by 4 (now 12 total)" in spec.evidence_lines[0]
    assert "+4 new type error(s)" in spec.proposal_outline.title_hint
    assert "now 12 total" in spec.proposal_outline.summary_hint


def test_worsened_defaults_when_evidence_missing() -> None:
    ins = _insight(dedup_key="type_health|type_errors|worsened", evidence={})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert spec.priority == (1, 4, "type_fix|errors_worsened|0")
    assert spec.estimated_affected_files is None
    assert "increased by 0 (now 0 total)" in spec.evidence_lines[0]


def test_worsened_distinct_files_zero_kept() -> None:
    ins = _insight(
        dedup_key="type_health|type_errors|worsened",
        evidence={"delta": 1, "current_count": 1, "distinct_file_count": 0},
    )
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert spec.estimated_affected_files == 0


def test_dedup_key_neither_present_nor_worsened_skipped() -> None:
    ins = _insight(dedup_key="type_health|type_errors|improved", status="present")
    assert TypeImprovementRule().evaluate([ins]) == []


def test_multiple_insights_emit_multiple_candidates() -> None:
    present = _insight(evidence={"error_count": 5})
    worsened = _insight(
        dedup_key="type_health|type_errors|worsened",
        evidence={"delta": 2, "current_count": 7},
    )
    out = TypeImprovementRule().evaluate([present, worsened, _hotspot_overlap()])
    keys = {c.pattern_key for c in out}
    assert keys == {"errors_present", "errors_worsened"}
    # hotspot overlap lifts present confidence to high
    present_spec = next(c for c in out if c.pattern_key == "errors_present")
    assert present_spec.confidence == "high"


def test_custom_min_errors_threshold() -> None:
    ins = _insight(evidence={"error_count": 5})
    assert TypeImprovementRule(min_errors=6).evaluate([ins]) == []
    assert len(TypeImprovementRule(min_errors=5).evaluate([ins])) == 1


def test_non_int_error_count_coerced() -> None:
    ins = _insight(evidence={"error_count": "8"})
    spec = TypeImprovementRule().evaluate([ins])[0]
    assert "8 type error(s)" in spec.evidence_lines[0]
    assert spec.confidence == "medium"


def test_invalid_error_count_raises() -> None:
    ins = _insight(evidence={"error_count": "not-a-number"})
    with pytest.raises(ValueError):
        TypeImprovementRule().evaluate([ins])
