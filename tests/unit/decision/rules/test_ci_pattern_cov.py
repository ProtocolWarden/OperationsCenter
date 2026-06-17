# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone


from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.decision.rules.ci_pattern import CIPatternRule
from operations_center.insights.models import DerivedInsight

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _insight(
    *,
    kind: str = "ci_pattern",
    status: str = "failing",
    evidence: dict | None = None,
    subject: str = "ci_checks",
) -> DerivedInsight:
    return DerivedInsight(
        insight_id="i-1",
        dedup_key="d-1",
        kind=kind,
        subject=subject,
        status=status,
        evidence=evidence or {},
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def test_failing_happy_path_full_evidence() -> None:
    rule = CIPatternRule()
    insight = _insight(
        status="failing",
        evidence={
            "failing_checks": ["lint", "types", "unit", "extra"],
            "failure_rate": 0.42,
        },
    )

    candidates = rule.evaluate([insight])

    assert len(candidates) == 1
    spec = candidates[0]
    assert isinstance(spec, CandidateSpec)
    assert spec.family == "ci_pattern"
    assert spec.subject == "ci_checks"
    assert spec.pattern_key == "checks_failing"
    assert spec.confidence == "high"
    assert spec.risk_class == "logic"
    assert spec.expires_after_runs == 4
    # Only the first three checks are rendered.
    assert "lint, types, unit" in spec.evidence_lines[0]
    assert "extra" not in spec.evidence_lines[0]
    # 0.42 -> 42% formatting.
    assert "42%" in spec.evidence_lines[0]
    assert spec.matched_rules == [
        "ci_checks_consistently_failing",
        "candidate_not_seen_in_cooldown_window",
    ]
    # priority embeds full failing count, not the truncated render.
    assert spec.priority == (1, 4, "ci_pattern|failing|4")


def test_failing_evidence_is_copied_not_shared() -> None:
    rule = CIPatternRule()
    original = {"failing_checks": ["a"], "failure_rate": 0.1}
    insight = _insight(status="failing", evidence=original)

    spec = rule.evaluate([insight])[0]
    spec.evidence["mutated"] = True

    assert "mutated" not in original
    assert spec.evidence is not original


def test_failing_outline_fields() -> None:
    rule = CIPatternRule()
    insight = _insight(
        status="failing",
        evidence={"failing_checks": ["build"], "failure_rate": 1.0},
    )

    outline = rule.evaluate([insight])[0].proposal_outline

    assert isinstance(outline, ProposalOutline)
    assert outline.title_hint == "Investigate failing CI checks: build"
    assert "consistently failing" in outline.summary_hint
    assert "100%" in outline.summary_hint
    assert outline.labels_hint == ["task-kind: improve", "source: proposer"]
    assert outline.source_family == "ci_pattern"


def test_failing_with_no_checks_uses_unknown_and_default_rate() -> None:
    rule = CIPatternRule()
    insight = _insight(status="failing", evidence={})

    spec = rule.evaluate([insight])[0]

    assert "unknown" in spec.evidence_lines[0]
    # default failure_rate 0.0 -> 0%
    assert "0%" in spec.evidence_lines[0]
    assert spec.priority == (1, 4, "ci_pattern|failing|0")
    assert "unknown" in spec.proposal_outline.title_hint


def test_failing_empty_list_is_treated_as_unknown() -> None:
    rule = CIPatternRule()
    insight = _insight(status="failing", evidence={"failing_checks": []})

    spec = rule.evaluate([insight])[0]

    assert "unknown" in spec.evidence_lines[0]
    assert spec.priority[2] == "ci_pattern|failing|0"


def test_failing_non_string_checks_are_stringified() -> None:
    rule = CIPatternRule()
    insight = _insight(
        status="failing",
        evidence={"failing_checks": [1, 2, {"x": 1}], "failure_rate": 0.5},
    )

    spec = rule.evaluate([insight])[0]

    assert "1, 2," in spec.evidence_lines[0]
    assert spec.priority[2] == "ci_pattern|failing|3"


def test_flaky_happy_path_full_evidence() -> None:
    rule = CIPatternRule()
    insight = _insight(
        status="flaky",
        evidence={
            "flaky_checks": ["e2e", "integration", "smoke", "extra"],
            "failure_rate": 0.15,
        },
    )

    spec = rule.evaluate([insight])[0]

    assert spec.pattern_key == "checks_flaky"
    assert spec.confidence == "medium"
    assert spec.expires_after_runs == 5
    assert spec.risk_class == "logic"
    assert "e2e, integration, smoke" in spec.evidence_lines[0]
    assert "extra" not in spec.evidence_lines[0]
    assert spec.matched_rules == [
        "ci_checks_intermittently_failing",
        "candidate_not_seen_in_cooldown_window",
    ]
    assert spec.priority == (1, 5, "ci_pattern|flaky|4")


def test_flaky_outline_fields() -> None:
    rule = CIPatternRule()
    insight = _insight(
        status="flaky",
        evidence={"flaky_checks": ["e2e"], "failure_rate": 0.3},
    )

    outline = rule.evaluate([insight])[0].proposal_outline

    assert outline.title_hint == "Stabilize flaky CI checks: e2e"
    assert "intermittent failures" in outline.summary_hint
    assert outline.labels_hint == ["task-kind: improve", "source: proposer"]
    assert outline.source_family == "ci_pattern"


def test_flaky_with_no_checks_uses_unknown() -> None:
    rule = CIPatternRule()
    insight = _insight(status="flaky", evidence={})

    spec = rule.evaluate([insight])[0]

    assert "unknown" in spec.evidence_lines[0]
    assert spec.priority == (1, 5, "ci_pattern|flaky|0")


def test_non_ci_pattern_kind_is_skipped() -> None:
    rule = CIPatternRule()
    insight = _insight(kind="lint_fix", status="failing")

    assert rule.evaluate([insight]) == []


def test_ci_pattern_with_other_status_produces_no_candidate() -> None:
    rule = CIPatternRule()
    insight = _insight(status="passing", evidence={"failing_checks": ["x"]})

    assert rule.evaluate([insight]) == []


def test_empty_insights_returns_empty_list() -> None:
    rule = CIPatternRule()

    result = rule.evaluate([])

    assert result == []


def test_mixed_insights_only_matching_ones_emit() -> None:
    rule = CIPatternRule()
    insights = [
        _insight(kind="other", status="failing"),
        _insight(status="failing", evidence={"failing_checks": ["a"], "failure_rate": 0.9}),
        _insight(status="passing"),
        _insight(status="flaky", evidence={"flaky_checks": ["b"], "failure_rate": 0.2}),
    ]

    candidates = rule.evaluate(insights)

    assert [c.pattern_key for c in candidates] == ["checks_failing", "checks_flaky"]


def test_failure_rate_rounding_behavior() -> None:
    rule = CIPatternRule()
    # 0.005 rounds to 0% (banker-ish .0% formatting), 0.125 -> 12%.
    insight = _insight(
        status="failing",
        evidence={"failing_checks": ["c"], "failure_rate": 0.125},
    )

    spec = rule.evaluate([insight])[0]

    assert "12%" in spec.evidence_lines[0]


def test_returns_new_list_each_call() -> None:
    rule = CIPatternRule()
    insight = _insight(status="failing", evidence={"failing_checks": ["a"]})

    first = rule.evaluate([insight])
    second = rule.evaluate([insight])

    assert first is not second
    assert len(first) == 1 and len(second) == 1
