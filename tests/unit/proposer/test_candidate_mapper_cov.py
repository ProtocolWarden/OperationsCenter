# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from operations_center.autonomy_tiers.config import AutonomyTiersConfig
from operations_center.decision.models import (
    CandidateRationale,
    EvidenceBundle,
    ProposalCandidate,
    ProposalOutline,
)
from operations_center.proposer.candidate_mapper import (
    PlaneTaskDraft,
    ProposalCandidateMapper,
)
from operations_center.proposer.provenance import ProposalProvenance


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def _make_candidate(
    *,
    family: str = "lint_fix",
    risk_class: str = "logic",
    confidence: str = "medium",
    validation_profile: str = "",
    expires_after_runs: int = 5,
    evidence_lines: list[str] | None = None,
    evidence_bundle: EvidenceBundle | None = None,
    labels_hint: list[str] | None = None,
    title_hint: str = "Fix lint",
    summary_hint: str = "Reduce ruff violations",
) -> ProposalCandidate:
    return ProposalCandidate(
        candidate_id="cand-1",
        dedup_key="dk-1",
        family=family,
        subject="subject",
        confidence=confidence,
        evidence_lines=evidence_lines or [],
        risk_class=risk_class,
        expires_after_runs=expires_after_runs,
        validation_profile=validation_profile,
        evidence_bundle=evidence_bundle,
        rationale=CandidateRationale(),
        proposal_outline=ProposalOutline(
            title_hint=title_hint,
            summary_hint=summary_hint,
            labels_hint=labels_hint or [],
        ),
    )


def _make_provenance(*, repo_name: str = "some-repo") -> ProposalProvenance:
    return ProposalProvenance(
        repo_name=repo_name,
        source_family="lint_fix",
        candidate_id="cand-1",
        candidate_dedup_key="dk-1",
        observer_run_ids=["obs-1", "obs-2"],
        insight_run_id="ins-1",
        decision_run_id="dec-1",
        proposer_run_id="prop-1",
    )


def _make_settings(
    *,
    repo_keys: dict[str, str] | None = None,
    self_repo_key: str | None = None,
    include_self_attr: bool = True,
) -> SimpleNamespace:
    """Build a minimal Settings-like object.

    repo_keys maps repo_key -> default_branch.
    """
    if repo_keys is None:
        repo_keys = {"some-repo": "main"}
    repos = {key: SimpleNamespace(default_branch=branch) for key, branch in repo_keys.items()}
    kwargs: dict = {"repos": repos}
    if include_self_attr:
        kwargs["self_repo_key"] = self_repo_key
    return SimpleNamespace(**kwargs)


def _tiers_config(overrides: dict[str, int] | None = None) -> AutonomyTiersConfig:
    return AutonomyTiersConfig(
        updated_at=datetime.now(UTC),
        overrides=overrides or {},
    )


# ---------------------------------------------------------------------------
# map_to_task: state derivation by tier
# ---------------------------------------------------------------------------
def test_tier0_falls_back_to_backlog():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="arch_promotion")  # default tier 0
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert draft.state == "Backlog"
    assert "requires_human_approval: true" in draft.description


def test_tier2_ready_for_ai():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="lint_fix")  # default tier 2
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert draft.state == "Ready for AI"
    assert "requires_human_approval: false" in draft.description


def test_tier1_style_risk_is_ready_for_ai():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="type_fix", risk_class="style")  # default tier 1
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert draft.state == "Ready for AI"


def test_tier1_nonstyle_risk_is_backlog():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="type_fix", risk_class="logic")  # default tier 1
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert draft.state == "Backlog"


def test_overrides_change_tier():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="type_fix", risk_class="logic")
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(overrides={"type_fix": 2}),
    )
    assert draft.state == "Ready for AI"


# ---------------------------------------------------------------------------
# map_to_task: tiers_config None triggers load_tiers_config
# ---------------------------------------------------------------------------
def test_loads_tiers_config_when_none(monkeypatch):
    called = {}

    def fake_load():
        called["loaded"] = True
        return _tiers_config(overrides={"lint_fix": 0})

    monkeypatch.setattr("operations_center.proposer.candidate_mapper.load_tiers_config", fake_load)
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="lint_fix")
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
    )
    assert called.get("loaded") is True
    # override of 0 => Backlog
    assert draft.state == "Backlog"


def test_loads_tiers_config_none_returned(monkeypatch):
    # load_tiers_config returning None should still work (get_family_tier handles None)
    monkeypatch.setattr(
        "operations_center.proposer.candidate_mapper.load_tiers_config", lambda: None
    )
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="lint_fix")  # default tier 2
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
    )
    assert draft.state == "Ready for AI"


# ---------------------------------------------------------------------------
# allowed_paths branches
# ---------------------------------------------------------------------------
def test_allowed_paths_for_operations_center():
    assert ProposalCandidateMapper._allowed_paths("operations-center") == [
        "src/",
        "tests/",
        "docs/",
    ]
    assert ProposalCandidateMapper._allowed_paths("Operations_Center") == [
        "src/",
        "tests/",
        "docs/",
    ]


def test_allowed_paths_for_other_repo_empty():
    assert ProposalCandidateMapper._allowed_paths("some-repo") == []


def test_allowed_paths_appear_in_description():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="lint_fix")
    settings = _make_settings(repo_keys={"operations-center": "trunk"})
    draft = mapper.map_to_task(
        candidate=cand,
        settings=settings,
        provenance=_make_provenance(repo_name="operations-center"),
        tiers_config=_tiers_config(),
    )
    assert "allowed_paths:" in draft.description
    assert "  - src/" in draft.description
    assert "base_branch: trunk" in draft.description


def test_no_allowed_paths_section_for_other_repo():
    mapper = ProposalCandidateMapper()
    cand = _make_candidate(family="lint_fix")
    draft = mapper.map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "allowed_paths:" not in draft.description


# ---------------------------------------------------------------------------
# _repo_key_for_candidate branches
# ---------------------------------------------------------------------------
def test_repo_key_exact_match():
    settings = _make_settings(repo_keys={"some-repo": "main"})
    key = ProposalCandidateMapper._repo_key_for_candidate(
        settings=settings, provenance=_make_provenance(repo_name="some-repo")
    )
    assert key == "some-repo"


def test_repo_key_case_insensitive_match():
    settings = _make_settings(repo_keys={"Some-Repo": "main"})
    key = ProposalCandidateMapper._repo_key_for_candidate(
        settings=settings, provenance=_make_provenance(repo_name="some-repo")
    )
    assert key == "Some-Repo"


def test_repo_key_unknown_raises():
    settings = _make_settings(repo_keys={"a-repo": "main", "b-repo": "dev"})
    with pytest.raises(ValueError) as excinfo:
        ProposalCandidateMapper._repo_key_for_candidate(
            settings=settings, provenance=_make_provenance(repo_name="ghost")
        )
    msg = str(excinfo.value)
    assert "ghost" in msg
    # known repos sorted in message
    assert "a-repo" in msg and "b-repo" in msg


# ---------------------------------------------------------------------------
# _task_kind_for_candidate branches
# ---------------------------------------------------------------------------
def test_task_kind_from_label_hint():
    cand = _make_candidate(labels_hint=["foo", "Task-Kind: Refactor "])
    assert ProposalCandidateMapper._task_kind_for_candidate(cand) == "refactor"


def test_task_kind_goal_for_special_families():
    for fam in ("observation_coverage", "test_visibility"):
        cand = _make_candidate(family=fam)
        assert ProposalCandidateMapper._task_kind_for_candidate(cand) == "goal"


def test_task_kind_default_improve():
    cand = _make_candidate(family="lint_fix")
    assert ProposalCandidateMapper._task_kind_for_candidate(cand) == "improve"


def test_task_kind_label_takes_precedence_over_family():
    cand = _make_candidate(family="observation_coverage", labels_hint=["task-kind:custom"])
    assert ProposalCandidateMapper._task_kind_for_candidate(cand) == "custom"


# ---------------------------------------------------------------------------
# _constraints_for_candidate branches
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "family",
    [
        "test_visibility",
        "dependency_drift_followup",
        "hotspot_concentration",
        "todo_accumulation",
        "observation_coverage",
        "lint_fix",
        "type_fix",
        "ci_pattern",
        "validation_pattern_followup",
    ],
)
def test_constraints_known_families(family):
    cand = _make_candidate(family=family)
    constraints = ProposalCandidateMapper._constraints_for_candidate(cand)
    assert isinstance(constraints, list) and constraints
    assert all(line.startswith("- ") for line in constraints)


def test_constraints_default_family():
    cand = _make_candidate(family="totally_unknown_family")
    constraints = ProposalCandidateMapper._constraints_for_candidate(cand)
    assert constraints == [
        "- Keep the change scoped to the identified issue.",
        "- Do not expand into unrelated refactors.",
    ]


# ---------------------------------------------------------------------------
# evidence_bundle schema version & evidence lines
# ---------------------------------------------------------------------------
def test_evidence_schema_version_from_bundle():
    bundle = EvidenceBundle(schema_version=7, kind="lint_count")
    cand = _make_candidate(family="lint_fix", evidence_bundle=bundle)
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "evidence_schema_version: 7" in draft.description


def test_evidence_schema_version_default_when_no_bundle():
    cand = _make_candidate(family="lint_fix", evidence_bundle=None)
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "evidence_schema_version: 1" in draft.description


def test_evidence_lines_rendered():
    cand = _make_candidate(family="lint_fix", evidence_lines=["line one", "line two"])
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "## Evidence" in draft.description
    assert "- line one" in draft.description
    assert "- line two" in draft.description


def test_no_evidence_section_when_empty():
    cand = _make_candidate(family="lint_fix", evidence_lines=[])
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "## Evidence" not in draft.description


# ---------------------------------------------------------------------------
# expires_at computation
# ---------------------------------------------------------------------------
def test_expires_at_uses_expires_after_runs(monkeypatch):
    fixed = datetime(2026, 1, 1, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr("operations_center.proposer.candidate_mapper.datetime", FixedDateTime)
    cand = _make_candidate(family="lint_fix", expires_after_runs=3)
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=_make_settings(),
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    expected = (fixed + timedelta(days=6)).strftime("%Y-%m-%d")
    assert f"expires_at: {expected}" in draft.description


# ---------------------------------------------------------------------------
# self-modify label branches
# ---------------------------------------------------------------------------
def test_self_modify_label_added_when_matches():
    cand = _make_candidate(family="lint_fix")
    settings = _make_settings(
        repo_keys={"operations-center": "main"}, self_repo_key="Operations-Center"
    )
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=settings,
        provenance=_make_provenance(repo_name="operations-center"),
        tiers_config=_tiers_config(),
    )
    assert "self-modify: approved" in draft.label_names


def test_self_modify_label_absent_when_no_self_repo_key():
    cand = _make_candidate(family="lint_fix")
    settings = _make_settings(repo_keys={"some-repo": "main"}, self_repo_key=None)
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=settings,
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "self-modify: approved" not in draft.label_names


def test_self_modify_label_absent_when_attr_missing():
    # getattr fallback path: settings without self_repo_key attribute at all
    cand = _make_candidate(family="lint_fix")
    settings = _make_settings(repo_keys={"some-repo": "main"}, include_self_attr=False)
    assert not hasattr(settings, "self_repo_key")
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=settings,
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "self-modify: approved" not in draft.label_names


def test_self_modify_label_absent_when_different_repo():
    cand = _make_candidate(family="lint_fix")
    settings = _make_settings(repo_keys={"some-repo": "main"}, self_repo_key="other-repo")
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=settings,
        provenance=_make_provenance(),
        tiers_config=_tiers_config(),
    )
    assert "self-modify: approved" not in draft.label_names


# ---------------------------------------------------------------------------
# Full draft shape / labels / provenance block
# ---------------------------------------------------------------------------
def test_full_draft_structure():
    cand = _make_candidate(
        family="lint_fix",
        confidence="high",
        validation_profile="fast",
        title_hint="My Title",
        summary_hint="My Summary",
    )
    prov = _make_provenance(repo_name="some-repo")
    draft = ProposalCandidateMapper().map_to_task(
        candidate=cand,
        settings=_make_settings(repo_keys={"some-repo": "main"}),
        provenance=prov,
        tiers_config=_tiers_config(),
    )
    assert isinstance(draft, PlaneTaskDraft)
    assert draft.name == "My Title"
    assert draft.task_kind == "improve"
    # labels
    assert "task-kind: improve" in draft.label_names
    assert "repo: some-repo" in draft.label_names
    assert "source: autonomy" in draft.label_names
    assert "source: propose" in draft.label_names
    assert "source-family: lint_fix" in draft.label_names
    # description sections & provenance fields
    desc = draft.description
    assert desc.startswith("## Execution")
    assert "## Goal" in desc
    assert "My Summary" in desc
    assert "## Constraints" in desc
    assert "## Provenance" in desc
    assert "source: autonomy-proposer" in desc
    assert "candidate_id: cand-1" in desc
    assert "candidate_dedup_key: dk-1" in desc
    assert "confidence: high" in desc
    assert "risk_class: logic" in desc
    assert "autonomy_tier: 2" in desc
    assert "validation_profile: fast" in desc
    assert "insight_run_id: ins-1" in desc
    assert "decision_run_id: dec-1" in desc
    assert "proposer_run_id: prop-1" in desc
    # observer run ids
    assert "  - obs-1" in desc
    assert "  - obs-2" in desc
    # description is stripped
    assert desc == desc.strip()
