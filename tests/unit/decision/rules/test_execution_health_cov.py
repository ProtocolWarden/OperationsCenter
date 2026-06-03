# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from operations_center.decision.rules.execution_health import ExecutionHealthRule
from operations_center.insights.models import DerivedInsight

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _insight(
    kind: str = "execution_health", subject: str = "repo-x", **evidence: object
) -> DerivedInsight:
    return DerivedInsight(
        insight_id="i1",
        dedup_key="d1",
        kind=kind,
        subject=subject,
        status="active",
        evidence=dict(evidence),
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def test_ignores_non_execution_health_insight() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(kind="lint_health", pattern="high_no_op_rate")
    assert rule.evaluate([insight]) == []


def test_unknown_pattern_produces_no_candidate() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(pattern="something_else")
    assert rule.evaluate([insight]) == []


def test_empty_pattern_produces_no_candidate() -> None:
    rule = ExecutionHealthRule()
    # No 'pattern' key at all -> defaults to "" -> no branch matches.
    insight = _insight(repo="r")
    assert rule.evaluate([insight]) == []


def test_empty_insights_returns_empty() -> None:
    rule = ExecutionHealthRule()
    assert rule.evaluate([]) == []


def test_repo_falls_back_to_subject_when_missing() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(
        subject="fallback-repo", pattern="high_no_op_rate", no_op_rate=0.5, total_runs=4
    )
    candidates = rule.evaluate([insight])
    assert len(candidates) == 1
    assert candidates[0].subject == "fallback-repo"


def test_high_no_op_rate_high_confidence() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="repo-a", pattern="high_no_op_rate", no_op_rate=0.9, total_runs=10)
    candidates = rule.evaluate([insight])
    assert len(candidates) == 1
    spec = candidates[0]
    assert spec.family == "execution_health_followup"
    assert spec.pattern_key == "high_no_op_rate"
    assert spec.confidence == "high"
    assert spec.matched_rules == ["execution_health_high_no_op_rate"]
    assert spec.risk_class == "logic"
    assert spec.expires_after_runs == 5
    assert spec.priority == (1, 0, "execution_health|repo-a|high_no_op_rate")
    assert spec.evidence_lines == [
        "90% of last 10 runs for 'repo-a' were no-ops (no material changes)."
    ]
    assert "90%" in spec.proposal_outline.title_hint
    assert "90%" in spec.proposal_outline.summary_hint
    assert spec.proposal_outline.labels_hint == ["task-kind: improve", "source: proposer"]
    assert spec.proposal_outline.source_family == "execution_health_followup"
    # Evidence is copied, not aliased.
    assert spec.evidence == dict(insight.evidence)
    assert spec.evidence is not insight.evidence


def test_high_no_op_rate_boundary_80_is_high() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="high_no_op_rate", no_op_rate=0.8, total_runs=5)
    candidates = rule.evaluate([insight])
    assert candidates[0].confidence == "high"


def test_high_no_op_rate_below_boundary_is_medium() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="high_no_op_rate", no_op_rate=0.79, total_runs=5)
    candidates = rule.evaluate([insight])
    spec = candidates[0]
    assert spec.confidence == "medium"
    assert spec.evidence_lines == ["79% of last 5 runs for 'r' were no-ops (no material changes)."]


def test_high_no_op_rate_defaults_when_evidence_missing() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="high_no_op_rate")
    candidates = rule.evaluate([insight])
    spec = candidates[0]
    # no_op_rate defaults 0 -> 0%, total_runs defaults 0, medium confidence.
    assert spec.confidence == "medium"
    assert spec.evidence_lines == ["0% of last 0 runs for 'r' were no-ops (no material changes)."]


def test_high_no_op_rate_accepts_string_rate() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="high_no_op_rate", no_op_rate="0.85", total_runs=7)
    candidates = rule.evaluate([insight])
    spec = candidates[0]
    assert spec.confidence == "high"
    assert "85%" in spec.proposal_outline.title_hint


def test_persistent_validation_failures() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(
        repo="repo-b", pattern="persistent_validation_failures", validation_failed_count=4
    )
    candidates = rule.evaluate([insight])
    assert len(candidates) == 1
    spec = candidates[0]
    assert spec.pattern_key == "persistent_validation_failures"
    assert spec.confidence == "high"
    assert spec.matched_rules == ["execution_health_persistent_validation_failures"]
    assert spec.expires_after_runs == 3
    assert spec.priority == (0, 0, "execution_health|repo-b|persistent_validation_failures")
    assert spec.evidence_lines == [
        "4 recent runs for 'repo-b' completed but failed post-execution validation."
    ]
    assert "4 recent failures" in spec.proposal_outline.title_hint
    assert "circuit-breaker" in spec.proposal_outline.summary_hint


def test_persistent_validation_failures_default_count() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="persistent_validation_failures")
    spec = rule.evaluate([insight])[0]
    assert spec.evidence_lines == [
        "0 recent runs for 'r' completed but failed post-execution validation."
    ]


def test_repeated_unknown_failures_explicit_total() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(
        repo="repo-c",
        pattern="repeated_unknown_failures",
        unknown_count=2,
        error_count=3,
        unknown_error_total=9,
        total_runs=20,
    )
    spec = rule.evaluate([insight])[0]
    assert spec.pattern_key == "repeated_unknown_failures"
    assert spec.confidence == "high"
    assert spec.matched_rules == ["execution_health_repeated_unknown_failures"]
    assert spec.expires_after_runs == 5
    assert spec.priority == (0, 0, "execution_health|repo-c|repeated_unknown_failures")
    # Explicit unknown_error_total (9) is used rather than derived (2+3=5).
    assert spec.evidence_lines == [
        "9 of the last 20 runs for 'repo-c' ended with unknown or error outcomes "
        "(2 unknown, 3 errors)."
    ]
    assert "9 recent failures" in spec.proposal_outline.title_hint


def test_repeated_unknown_failures_derives_total() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(
        repo="r",
        pattern="repeated_unknown_failures",
        unknown_count=2,
        error_count=3,
        total_runs=10,
    )
    spec = rule.evaluate([insight])[0]
    # No explicit total -> derived 2+3=5.
    assert spec.evidence_lines == [
        "5 of the last 10 runs for 'r' ended with unknown or error outcomes (2 unknown, 3 errors)."
    ]


def test_repeated_unknown_failures_all_defaults() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="repeated_unknown_failures")
    spec = rule.evaluate([insight])[0]
    assert spec.evidence_lines == [
        "0 of the last 0 runs for 'r' ended with unknown or error outcomes (0 unknown, 0 errors)."
    ]


def test_multiple_insights_mixed() -> None:
    rule = ExecutionHealthRule()
    insights = [
        _insight(kind="other", pattern="high_no_op_rate"),
        _insight(repo="r1", pattern="high_no_op_rate", no_op_rate=0.9, total_runs=3),
        _insight(repo="r2", pattern="persistent_validation_failures", validation_failed_count=2),
        _insight(repo="r3", pattern="repeated_unknown_failures", unknown_count=1, error_count=1),
        _insight(repo="r4", pattern="bogus"),
    ]
    candidates = rule.evaluate(insights)
    assert [c.pattern_key for c in candidates] == [
        "high_no_op_rate",
        "persistent_validation_failures",
        "repeated_unknown_failures",
    ]


def test_high_no_op_rate_invalid_rate_raises() -> None:
    rule = ExecutionHealthRule()
    insight = _insight(repo="r", pattern="high_no_op_rate", no_op_rate="not-a-number", total_runs=1)
    with pytest.raises(ValueError):
        rule.evaluate([insight])
