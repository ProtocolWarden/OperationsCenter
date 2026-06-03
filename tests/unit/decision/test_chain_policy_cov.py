# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.chain_policy import (
    _FAMILY_PREREQUISITES,
    ChainPolicy,
)
from operations_center.decision.models import (
    CandidateRationale,
    DecisionRepoRef,
    ProposalCandidate,
    ProposalCandidatesArtifact,
    ProposalOutline,
)

_NOW = datetime(2026, 6, 2, 12, 0, 0)


def _outline(family: str = "f") -> ProposalOutline:
    return ProposalOutline(
        title_hint=f"title-{family}",
        summary_hint=f"summary-{family}",
        labels_hint=[],
        source_family=family,
    )


def _spec(family: str, subject: str = "subj", pattern_key: str = "pk") -> CandidateSpec:
    return CandidateSpec(
        family=family,
        subject=subject,
        pattern_key=pattern_key,
        evidence={},
        matched_rules=["r1"],
        proposal_outline=_outline(family),
    )


def _candidate(family: str, *, status: str = "emit", subject: str = "subj") -> ProposalCandidate:
    return ProposalCandidate(
        candidate_id=f"id-{family}",
        dedup_key=f"key-{family}",
        family=family,
        subject=subject,
        status=status,
        rationale=CandidateRationale(matched_rules=[], suppressed_by=[]),
        proposal_outline=_outline(family),
    )


def _artifact(
    *,
    generated_at: datetime,
    candidates: list[ProposalCandidate],
) -> ProposalCandidatesArtifact:
    return ProposalCandidatesArtifact(
        run_id="run-1",
        generated_at=generated_at,
        source_command="cmd",
        repo=DecisionRepoRef(name="repo", path="/tmp/repo"),
        source_insight_run_id="insight-1",
        candidates=candidates,
    )


def test_init_default_cooldown() -> None:
    policy = ChainPolicy()
    assert policy.cooldown_minutes == 120


def test_init_custom_cooldown() -> None:
    policy = ChainPolicy(cooldown_minutes=30)
    assert policy.cooldown_minutes == 30


def test_unlisted_family_passes_through() -> None:
    policy = ChainPolicy()
    specs = [_spec("lint_fix"), _spec("some_other_family")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    assert {s.family for s in live} == {"lint_fix", "some_other_family"}
    assert suppressed == []


def test_downstream_with_no_active_upstream_passes() -> None:
    # type_fix needs lint_fix; lint_fix neither in-cycle nor recently emitted.
    policy = ChainPolicy()
    live, suppressed = policy.apply(
        specs=[_spec("type_fix")], prior_artifacts=[], generated_at=_NOW
    )
    assert [s.family for s in live] == ["type_fix"]
    assert suppressed == []


def test_upstream_in_cycle_suppresses_downstream() -> None:
    policy = ChainPolicy()
    specs = [_spec("lint_fix"), _spec("type_fix")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    assert [s.family for s in live] == ["lint_fix"]
    assert len(suppressed) == 1
    rec = suppressed[0]
    assert rec.family == "type_fix"
    assert rec.reason == "upstream_family_in_cycle"
    assert rec.evidence == {
        "upstream_family": "lint_fix",
        "downstream_family": "type_fix",
    }
    # dedup_key comes from CandidateBuilder.
    assert rec.dedup_key == "candidate|type_fix|subj|pk"
    assert rec.subject == "subj"


def test_upstream_recently_emitted_suppresses_downstream() -> None:
    policy = ChainPolicy(cooldown_minutes=120)
    prior = _artifact(
        generated_at=_NOW - timedelta(minutes=10),
        candidates=[_candidate("lint_fix", status="emit")],
    )
    live, suppressed = policy.apply(
        specs=[_spec("type_fix")], prior_artifacts=[prior], generated_at=_NOW
    )
    assert live == []
    assert len(suppressed) == 1
    assert suppressed[0].reason == "upstream_family_recently_emitted"
    assert suppressed[0].evidence["upstream_family"] == "lint_fix"


def test_in_cycle_takes_precedence_over_recently_emitted() -> None:
    # lint_fix is both in-cycle and recently emitted; in-cycle wins (checked first).
    policy = ChainPolicy()
    prior = _artifact(
        generated_at=_NOW - timedelta(minutes=5),
        candidates=[_candidate("lint_fix")],
    )
    specs = [_spec("lint_fix"), _spec("type_fix")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[prior], generated_at=_NOW)
    assert [s.family for s in live] == ["lint_fix"]
    assert suppressed[0].reason == "upstream_family_in_cycle"


def test_prior_artifact_outside_cooldown_window_ignored() -> None:
    policy = ChainPolicy(cooldown_minutes=120)
    prior = _artifact(
        generated_at=_NOW - timedelta(minutes=121),
        candidates=[_candidate("lint_fix")],
    )
    live, suppressed = policy.apply(
        specs=[_spec("type_fix")], prior_artifacts=[prior], generated_at=_NOW
    )
    assert [s.family for s in live] == ["type_fix"]
    assert suppressed == []


def test_cooldown_boundary_inclusive() -> None:
    # generated_at - cooldown == artifact.generated_at -> >= cutoff is True.
    policy = ChainPolicy(cooldown_minutes=120)
    prior = _artifact(
        generated_at=_NOW - timedelta(minutes=120),
        candidates=[_candidate("lint_fix")],
    )
    live, suppressed = policy.apply(
        specs=[_spec("type_fix")], prior_artifacts=[prior], generated_at=_NOW
    )
    assert live == []
    assert suppressed[0].reason == "upstream_family_recently_emitted"


def test_non_emit_prior_candidate_does_not_block() -> None:
    policy = ChainPolicy()
    prior = _artifact(
        generated_at=_NOW - timedelta(minutes=5),
        candidates=[_candidate("lint_fix", status="suppressed")],
    )
    live, suppressed = policy.apply(
        specs=[_spec("type_fix")], prior_artifacts=[prior], generated_at=_NOW
    )
    assert [s.family for s in live] == ["type_fix"]
    assert suppressed == []


def test_second_chain_execution_health_followup() -> None:
    policy = ChainPolicy()
    specs = [_spec("test_visibility"), _spec("execution_health_followup")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    assert [s.family for s in live] == ["test_visibility"]
    assert len(suppressed) == 1
    assert suppressed[0].family == "execution_health_followup"
    assert suppressed[0].evidence == {
        "upstream_family": "test_visibility",
        "downstream_family": "execution_health_followup",
    }


def test_empty_specs_returns_empty() -> None:
    policy = ChainPolicy()
    live, suppressed = policy.apply(specs=[], prior_artifacts=[], generated_at=_NOW)
    assert live == []
    assert suppressed == []


def test_multiple_prior_artifacts_aggregated() -> None:
    policy = ChainPolicy(cooldown_minutes=120)
    old = _artifact(
        generated_at=_NOW - timedelta(minutes=200),
        candidates=[_candidate("lint_fix")],  # outside window, ignored
    )
    recent = _artifact(
        generated_at=_NOW - timedelta(minutes=30),
        candidates=[_candidate("test_visibility")],
    )
    live, suppressed = policy.apply(
        specs=[_spec("execution_health_followup")],
        prior_artifacts=[old, recent],
        generated_at=_NOW,
    )
    assert live == []
    assert suppressed[0].reason == "upstream_family_recently_emitted"
    assert suppressed[0].evidence["upstream_family"] == "test_visibility"


def test_prerequisites_table_shape() -> None:
    assert _FAMILY_PREREQUISITES["type_fix"] == ["lint_fix"]
    assert _FAMILY_PREREQUISITES["execution_health_followup"] == ["test_visibility"]


def test_unrelated_recent_emission_does_not_suppress() -> None:
    policy = ChainPolicy()
    prior = _artifact(
        generated_at=_NOW - timedelta(minutes=5),
        candidates=[_candidate("unrelated_family")],
    )
    live, suppressed = policy.apply(
        specs=[_spec("type_fix")], prior_artifacts=[prior], generated_at=_NOW
    )
    assert [s.family for s in live] == ["type_fix"]
    assert suppressed == []


def test_subject_and_pattern_key_flow_into_suppressed_record() -> None:
    policy = ChainPolicy()
    specs = [
        _spec("lint_fix"),
        _spec("type_fix", subject="mod.py", pattern_key="px"),
    ]
    _live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    assert suppressed[0].subject == "mod.py"
    assert suppressed[0].dedup_key == "candidate|type_fix|mod.py|px"


def test_raises_on_missing_keyword_arguments() -> None:
    policy = ChainPolicy()
    with pytest.raises(TypeError):
        policy.apply(specs=[], prior_artifacts=[])  # type: ignore[call-arg]
