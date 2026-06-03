# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.decision import service as service_mod
from operations_center.decision.artifact_writer import DecisionArtifactWriter
from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import (
    CandidateRationale,
    ProposalCandidate,
    ProposalCandidatesArtifact,
    ProposalOutline,
)
from operations_center.decision.service import (
    _DEFAULT_ALLOWED_FAMILIES,
    DecisionContext,
    DecisionEngineService,
    _build_rules,
    new_decision_context,
)
from operations_center.tuning.models import TuningConfig

NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class _FakeRepo:
    def __init__(self, name: str = "repo", path: str = "/tmp/repo") -> None:
        self.name = name
        self.path = Path(path)


class _FakeInsightArtifact:
    def __init__(self, run_id: str = "ins_1") -> None:
        self.insights = ["insight-a", "insight-b"]
        self.repo = _FakeRepo()
        self.run_id = run_id


class _FakeLoader:
    """Loader stub returning a fixed insight artifact and prior decisions."""

    def __init__(self, prior_decisions: list | None = None) -> None:
        self.prior_decisions = prior_decisions or []
        self.calls: list[dict] = []

    def load(self, *, repo, insight_run_id, history_limit):
        self.calls.append(
            {"repo": repo, "insight_run_id": insight_run_id, "history_limit": history_limit}
        )
        return _FakeInsightArtifact(), self.prior_decisions


class _Rule:
    """Rule stub yielding pre-built CandidateSpecs regardless of insights."""

    def __init__(self, specs: list[CandidateSpec]) -> None:
        self._specs = specs

    def evaluate(self, insights):
        return list(self._specs)


class _FakeSettings:
    def __init__(self, min_remaining: int = 0) -> None:
        self.min_remaining_exec_for_proposals = min_remaining


class _FakeUsageStore:
    def __init__(self, *, remaining: int = 100, min_remaining: int = 0) -> None:
        self._remaining = remaining
        self.settings = _FakeSettings(min_remaining)
        self.suppression_calls: list[dict] = []

    def remaining_exec_capacity(self, *, now):
        return self._remaining

    def record_proposal_budget_suppression(self, *, reason, now, evidence):
        self.suppression_calls.append({"reason": reason, "now": now, "evidence": evidence})


def _spec(family: str = "lint_fix", subject: str = "pkg/mod.py") -> CandidateSpec:
    return CandidateSpec(
        family=family,
        subject=subject,
        pattern_key="key",
        evidence={"violation_count": 7},
        matched_rules=[f"{family}_rule"],
        proposal_outline=ProposalOutline(title_hint="t", summary_hint="s"),
        confidence="medium",
    )


def _make_service(
    *,
    loader: _FakeLoader,
    usage_store: _FakeUsageStore | None = None,
    rules_specs: list[CandidateSpec] | None = None,
    writer_root: Path | None = None,
) -> DecisionEngineService:
    svc = DecisionEngineService(
        loader=loader,
        usage_store=usage_store or _FakeUsageStore(),
        artifact_writer=DecisionArtifactWriter(root=writer_root),
    )
    if rules_specs is not None:
        svc.rules = [_Rule(rules_specs)]
    return svc


def _ctx(tmp_path: Path, **overrides) -> DecisionContext:
    base = {
        "repo_filter": "repo",
        "insight_run_id": "ins_1",
        "history_limit": 5,
        "max_candidates": 5,
        "cooldown_minutes": 120,
        "run_id": "dec_test",
        "generated_at": NOW,
        "source_command": "oc decide",
        "proposer_root": tmp_path / "proposer",
        "feedback_root": tmp_path / "feedback",
    }
    base.update(overrides)
    return DecisionContext(**base)


# --------------------------------------------------------------------------- #
# _build_rules
# --------------------------------------------------------------------------- #


def test_build_rules_without_tuning_returns_full_set():
    rules = _build_rules(None)
    assert len(rules) == 14


def test_build_rules_with_tuning_overrides_applied():
    tuning = TuningConfig(
        updated_at=NOW,
        overrides={
            "observation_coverage": {"min_consecutive_runs": 9},
            "test_visibility": {"min_consecutive_runs": 8},
            "dependency_drift": {"min_consecutive_runs": 7},
            "lint_fix": {"min_violations": 11},
            "type_fix": {"min_errors": 12},
        },
    )
    rules = _build_rules(tuning)
    assert len(rules) == 14
    # ObservationCoverageRule is first; confirm override threaded through.
    assert rules[0].min_consecutive_runs == 9


# --------------------------------------------------------------------------- #
# new_decision_context
# --------------------------------------------------------------------------- #


def test_new_decision_context_defaults_allowed_families(monkeypatch):
    monkeypatch.setattr(service_mod, "datetime", _frozen_datetime(NOW), raising=True)
    ctx = new_decision_context(
        repo_filter=None,
        insight_run_id=None,
        history_limit=3,
        max_candidates=2,
        cooldown_minutes=60,
        source_command="cmd",
    )
    assert ctx.allowed_families == _DEFAULT_ALLOWED_FAMILIES
    assert ctx.dry_run is False
    assert ctx.run_id.startswith("dec_")
    assert len(ctx.run_id) <= 31


def test_new_decision_context_explicit_allowed_families(monkeypatch):
    monkeypatch.setattr(service_mod, "datetime", _frozen_datetime(NOW), raising=True)
    custom = frozenset({"lint_fix"})
    ctx = new_decision_context(
        repo_filter="r",
        insight_run_id="i",
        history_limit=1,
        max_candidates=1,
        cooldown_minutes=1,
        source_command="cmd",
        dry_run=True,
        allowed_families=custom,
    )
    assert ctx.allowed_families == custom
    assert ctx.dry_run is True


def _frozen_datetime(value: datetime):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return value

    return _DT


# --------------------------------------------------------------------------- #
# decide() happy path + branches
# --------------------------------------------------------------------------- #


def test_decide_emits_allowed_family_candidate(tmp_path):
    loader = _FakeLoader()
    svc = _make_service(
        loader=loader,
        rules_specs=[_spec(family="lint_fix")],
        writer_root=tmp_path / "out",
    )
    artifact, paths = svc.decide(_ctx(tmp_path))

    assert isinstance(artifact, ProposalCandidatesArtifact)
    assert len(artifact.candidates) == 1
    assert artifact.candidates[0].family == "lint_fix"
    assert artifact.suppressed == []
    # Loader received context-derived params.
    assert loader.calls[0]["repo"] == "repo"
    # Writer produced json + md files that actually exist.
    assert len(paths) == 2
    for p in paths:
        assert Path(p).exists()


def test_decide_suppresses_family_not_allowed(tmp_path):
    loader = _FakeLoader()
    # ci_pattern is in ALL_FAMILIES but not in the default allowed set.
    svc = _make_service(
        loader=loader,
        rules_specs=[_spec(family="ci_pattern", subject="x")],
        writer_root=tmp_path / "out",
    )
    artifact, _ = svc.decide(_ctx(tmp_path))
    assert artifact.candidates == []
    reasons = {s.reason for s in artifact.suppressed}
    assert "family_deferred_initial_gating" in reasons


def test_decide_budget_too_low_suppresses_all(tmp_path):
    loader = _FakeLoader()
    usage = _FakeUsageStore(remaining=1, min_remaining=10)
    svc = _make_service(
        loader=loader,
        usage_store=usage,
        rules_specs=[_spec(family="lint_fix")],
        writer_root=tmp_path / "out",
    )
    artifact, _ = svc.decide(_ctx(tmp_path))
    assert artifact.candidates == []
    assert any(s.reason == "proposal_budget_too_low" for s in artifact.suppressed)
    assert usage.suppression_calls and usage.suppression_calls[0]["reason"] == (
        "proposal_budget_too_low"
    )


def test_decide_defaults_to_real_usage_store(monkeypatch, tmp_path):
    """When no usage_store is injected, decide() instantiates UsageStore()."""
    loader = _FakeLoader()
    instances = []

    class _Stub:
        def __init__(self):
            instances.append(self)
            self.settings = _FakeSettings(min_remaining=0)

        def remaining_exec_capacity(self, *, now):
            return 50

    monkeypatch.setattr(service_mod, "UsageStore", _Stub)
    svc = DecisionEngineService(
        loader=loader, artifact_writer=DecisionArtifactWriter(root=tmp_path)
    )
    svc.rules = [_Rule([_spec(family="lint_fix")])]
    svc.decide(_ctx(tmp_path))
    assert len(instances) == 1


def test_decide_velocity_cap_suppresses_all(tmp_path):
    loader = _FakeLoader()
    proposer = tmp_path / "proposer"
    run_dir = proposer / "p1"
    run_dir.mkdir(parents=True)
    (run_dir / "proposal_results.json").write_text(
        json.dumps(
            {
                "dry_run": False,
                "generated_at": NOW.isoformat(),
                "created": [{"dedup_key": "a"}, {"dedup_key": "b"}],
            }
        ),
        encoding="utf-8",
    )
    svc = _make_service(
        loader=loader,
        rules_specs=[_spec(family="lint_fix")],
        writer_root=tmp_path / "out",
    )
    ctx = _ctx(tmp_path, proposer_root=proposer, max_proposals_per_24h=2)
    artifact, _ = svc.decide(ctx)
    assert artifact.candidates == []
    assert any(s.reason == "velocity_cap_reached" for s in artifact.suppressed)


def test_decide_stale_open_suppression(tmp_path):
    # Prior decision artifact with a candidate that will expire.
    proposed_at = NOW - timedelta(days=100)
    candidate = _emit_candidate(dedup_key="candidate|lint_fix|pkg/mod.py|key")
    prior = _prior_artifact(generated_at=proposed_at, candidates=[candidate])
    loader = _FakeLoader(prior_decisions=[prior])

    # proposer artifact maps the dedup_key to a plane issue id (so it was created).
    proposer = tmp_path / "proposer"
    rd = proposer / "p1"
    rd.mkdir(parents=True)
    (rd / "proposal_results.json").write_text(
        json.dumps(
            {
                "created": [
                    {"dedup_key": "candidate|lint_fix|pkg/mod.py|key", "plane_issue_id": "ISS-1"}
                ]
            }
        ),
        encoding="utf-8",
    )
    # feedback root has no resolution => still open => stale.
    feedback = tmp_path / "feedback"
    feedback.mkdir()

    svc = _make_service(
        loader=loader,
        rules_specs=[_spec(family="lint_fix")],
        writer_root=tmp_path / "out",
    )
    ctx = _ctx(tmp_path, proposer_root=proposer, feedback_root=feedback)
    artifact, _ = svc.decide(ctx)
    assert any(s.reason == "proposal_stale_open" for s in artifact.suppressed)
    assert artifact.candidates == []


def test_decide_stale_keys_present_but_spec_not_stale(tmp_path):
    """stale_keys non-empty, but the live spec's dedup_key differs -> survives."""
    proposed_at = NOW - timedelta(days=100)
    stale_cand = _emit_candidate(dedup_key="candidate|type_fix|other.py|key")
    prior = _prior_artifact(generated_at=proposed_at, candidates=[stale_cand])
    loader = _FakeLoader(prior_decisions=[prior])

    proposer = tmp_path / "proposer"
    rd = proposer / "p1"
    rd.mkdir(parents=True)
    (rd / "proposal_results.json").write_text(
        json.dumps(
            {"created": [{"dedup_key": "candidate|type_fix|other.py|key", "plane_issue_id": "X"}]}
        ),
        encoding="utf-8",
    )
    feedback = tmp_path / "feedback"
    feedback.mkdir()

    svc = _make_service(
        loader=loader,
        rules_specs=[_spec(family="lint_fix", subject="pkg/mod.py")],
        writer_root=tmp_path / "out",
    )
    ctx = _ctx(tmp_path, proposer_root=proposer, feedback_root=feedback)
    artifact, _ = svc.decide(ctx)
    # The lint_fix candidate survives staleness gate and is emitted.
    assert any(c.family == "lint_fix" for c in artifact.candidates)


# --------------------------------------------------------------------------- #
# _count_proposals_last_24h
# --------------------------------------------------------------------------- #


def test_count_proposals_no_root(tmp_path):
    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=tmp_path / "missing")
    assert svc._count_proposals_last_24h(ctx) == 0


def test_count_proposals_skips_bad_and_dry_and_old(tmp_path):
    proposer = tmp_path / "proposer"
    proposer.mkdir()

    def _wr(name, payload):
        d = proposer / name
        d.mkdir()
        (d / "proposal_results.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )

    _wr("bad", "{ not json")  # ValueError -> skipped
    _wr("dry", {"dry_run": True, "generated_at": NOW.isoformat(), "created": [{}]})
    _wr("badts", {"generated_at": "not-a-date", "created": [{}]})
    _wr(
        "old",
        {"generated_at": (NOW - timedelta(hours=48)).isoformat(), "created": [{}, {}]},
    )
    _wr(
        "good",
        {"generated_at": NOW.isoformat(), "created": [{}, {}, {}]},
    )
    _wr("non_list_created", {"generated_at": NOW.isoformat(), "created": "nope"})

    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=proposer)
    assert svc._count_proposals_last_24h(ctx) == 3


# --------------------------------------------------------------------------- #
# _stale_open_dedup_keys
# --------------------------------------------------------------------------- #


def test_stale_keys_empty_when_no_prior(tmp_path):
    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path)
    assert svc._stale_open_dedup_keys(ctx, []) == set()


def test_stale_keys_not_expired_returns_empty(tmp_path):
    cand = _emit_candidate(dedup_key="dk", expires_after_runs=5)
    prior = _prior_artifact(generated_at=NOW, candidates=[cand])
    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path)
    # generated_at == proposed_at => expiry in future => not stale.
    assert svc._stale_open_dedup_keys(ctx, [prior]) == set()


def test_stale_keys_expired_but_no_issue_id(tmp_path):
    cand = _emit_candidate(dedup_key="dk", expires_after_runs=1)
    prior = _prior_artifact(generated_at=NOW - timedelta(days=100), candidates=[cand])
    # proposer root absent => no issue mapping => skipped (never created in Plane).
    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=tmp_path / "missing", feedback_root=tmp_path / "fbmissing")
    assert svc._stale_open_dedup_keys(ctx, [prior]) == set()


def test_stale_keys_resolved_issue_excluded(tmp_path):
    cand = _emit_candidate(dedup_key="dk", expires_after_runs=1)
    prior = _prior_artifact(generated_at=NOW - timedelta(days=100), candidates=[cand])

    proposer = tmp_path / "proposer"
    rd = proposer / "p1"
    rd.mkdir(parents=True)
    (rd / "proposal_results.json").write_text(
        json.dumps({"created": [{"dedup_key": "dk", "plane_issue_id": "ISS-9"}]}),
        encoding="utf-8",
    )
    feedback = tmp_path / "feedback"
    feedback.mkdir()
    (feedback / "fb.json").write_text(
        json.dumps({"task_id": "ISS-9", "outcome": "merged"}), encoding="utf-8"
    )

    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=proposer, feedback_root=feedback)
    # Resolved => excluded from stale.
    assert svc._stale_open_dedup_keys(ctx, [prior]) == set()


def test_stale_keys_handles_corrupt_files(tmp_path):
    cand = _emit_candidate(dedup_key="dk", expires_after_runs=1)
    prior = _prior_artifact(generated_at=NOW - timedelta(days=100), candidates=[cand])

    proposer = tmp_path / "proposer"
    rd = proposer / "p1"
    rd.mkdir(parents=True)
    # bad json in proposer
    (rd / "proposal_results.json").write_text("{bad", encoding="utf-8")
    rd2 = proposer / "p2"
    rd2.mkdir()
    # created not a list -> skipped
    (rd2 / "proposal_results.json").write_text(json.dumps({"created": "no"}), encoding="utf-8")
    rd3 = proposer / "p3"
    rd3.mkdir()
    # created item not a dict, plus missing fields
    (rd3 / "proposal_results.json").write_text(
        json.dumps({"created": ["str", {"dedup_key": "", "plane_issue_id": ""}]}),
        encoding="utf-8",
    )

    feedback = tmp_path / "feedback"
    feedback.mkdir()
    (feedback / "bad.json").write_text("{bad", encoding="utf-8")  # corrupt -> skipped
    (feedback / "incomplete.json").write_text(
        json.dumps({"task_id": "", "outcome": "merged"}), encoding="utf-8"
    )

    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=proposer, feedback_root=feedback)
    # No usable issue mapping => not stale despite expiry.
    assert svc._stale_open_dedup_keys(ctx, [prior]) == set()


def test_stale_keys_actually_stale(tmp_path):
    cand = _emit_candidate(dedup_key="dk", expires_after_runs=1)
    prior = _prior_artifact(generated_at=NOW - timedelta(days=100), candidates=[cand])

    proposer = tmp_path / "proposer"
    rd = proposer / "p1"
    rd.mkdir(parents=True)
    (rd / "proposal_results.json").write_text(
        json.dumps({"created": [{"dedup_key": "dk", "plane_issue_id": "ISS-9"}]}),
        encoding="utf-8",
    )
    feedback = tmp_path / "feedback"
    feedback.mkdir()
    # unresolved outcome
    (feedback / "fb.json").write_text(
        json.dumps({"task_id": "ISS-9", "outcome": "in_review"}), encoding="utf-8"
    )

    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=proposer, feedback_root=feedback)
    assert svc._stale_open_dedup_keys(ctx, [prior]) == {"dk"}


def test_stale_keys_dedup_first_artifact_wins(tmp_path):
    """A dedup_key seen twice across artifacts keeps the first artifact's data."""
    c1 = _emit_candidate(dedup_key="dk", expires_after_runs=1)
    c2 = _emit_candidate(dedup_key="dk", expires_after_runs=99)
    a1 = _prior_artifact(generated_at=NOW - timedelta(days=100), candidates=[c1])
    a2 = _prior_artifact(generated_at=NOW, candidates=[c2])
    svc = _make_service(loader=_FakeLoader(), rules_specs=[])
    ctx = _ctx(tmp_path, proposer_root=tmp_path / "missing")
    # First artifact (expired, no issue mapping) -> not stale (no issue), but the
    # key is only registered once; assert no crash and empty result.
    assert svc._stale_open_dedup_keys(ctx, [a1, a2]) == set()


# --------------------------------------------------------------------------- #
# helpers building real pydantic models
# --------------------------------------------------------------------------- #


def _emit_candidate(*, dedup_key: str, expires_after_runs: int = 5) -> ProposalCandidate:
    return ProposalCandidate(
        candidate_id="c1",
        dedup_key=dedup_key,
        family="lint_fix",
        subject="pkg/mod.py",
        status="emit",
        expires_after_runs=expires_after_runs,
        rationale=CandidateRationale(),
        proposal_outline=ProposalOutline(title_hint="t", summary_hint="s"),
    )


def _prior_artifact(*, generated_at: datetime, candidates: list) -> ProposalCandidatesArtifact:
    return ProposalCandidatesArtifact(
        run_id="prev",
        generated_at=generated_at,
        source_command="oc decide",
        repo={"name": "repo", "path": "/tmp/repo"},
        source_insight_run_id="ins_0",
        candidates=candidates,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
