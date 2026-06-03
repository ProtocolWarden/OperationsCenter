# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.decision.loader import DecisionLoader
from operations_center.decision.models import (
    CandidateRationale,
    DecisionRepoRef,
    ProposalCandidate,
    ProposalCandidatesArtifact,
    ProposalOutline,
)
from operations_center.insights.models import (
    InsightRepoRef,
    RepoInsightsArtifact,
)


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, 0, tzinfo=timezone.utc)


def _insights_artifact(
    *,
    run_id: str,
    repo_name: str = "alpha",
    repo_path: str = "/repos/alpha",
    generated_at: datetime | None = None,
) -> RepoInsightsArtifact:
    return RepoInsightsArtifact(
        run_id=run_id,
        generated_at=generated_at or _dt(1),
        source_command="oc insights",
        repo=InsightRepoRef(name=repo_name, path=Path(repo_path)),
        source_snapshots=[],
        insights=[],
    )


def _decision_artifact(
    *,
    run_id: str,
    source_insight_run_id: str,
    repo_name: str = "alpha",
    repo_path: str = "/repos/alpha",
    generated_at: datetime | None = None,
) -> ProposalCandidatesArtifact:
    return ProposalCandidatesArtifact(
        run_id=run_id,
        generated_at=generated_at or _dt(1),
        source_command="oc decide",
        repo=DecisionRepoRef(name=repo_name, path=Path(repo_path)),
        source_insight_run_id=source_insight_run_id,
        candidates=[
            ProposalCandidate(
                candidate_id="c1",
                dedup_key="d1",
                family="lint_fix",
                subject="s",
                rationale=CandidateRationale(),
                proposal_outline=ProposalOutline(title_hint="t", summary_hint="su"),
            )
        ],
    )


def _write(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(), encoding="utf-8")


def _write_insight(root: Path, model: RepoInsightsArtifact) -> None:
    _write(root / model.run_id / "repo_insights.json", model)


def _write_decision(root: Path, model: ProposalCandidatesArtifact) -> None:
    _write(root / model.run_id / "proposal_candidates.json", model)


def test_default_roots_used_when_none(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    loader = DecisionLoader()
    assert loader.insights_root == Path("tools/report/operations_center/insights")
    assert loader.decision_root == Path("tools/report/operations_center/decision")


def test_explicit_roots_override(tmp_path) -> None:
    loader = DecisionLoader(insights_root=tmp_path / "i", decision_root=tmp_path / "d")
    assert loader.insights_root == tmp_path / "i"
    assert loader.decision_root == tmp_path / "d"


def test_load_no_filter_picks_latest_insight(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(iroot, _insights_artifact(run_id="old", generated_at=_dt(1)))
    _write_insight(iroot, _insights_artifact(run_id="new", generated_at=_dt(5)))
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    current, prior = loader.load(repo=None, insight_run_id=None, history_limit=10)

    assert current.run_id == "new"
    assert prior == []


def test_load_filter_by_repo_name(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(
        iroot,
        _insights_artifact(run_id="a", repo_name="Alpha", repo_path="/repos/alpha"),
    )
    _write_insight(
        iroot,
        _insights_artifact(run_id="b", repo_name="beta", repo_path="/repos/beta"),
    )
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    # mixed case + whitespace exercises the normalization branch
    current, _ = loader.load(repo="  alpha  ", insight_run_id=None, history_limit=10)

    assert current.run_id == "a"


def test_load_filter_by_repo_path(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(
        iroot,
        _insights_artifact(run_id="a", repo_name="alpha", repo_path="/repos/alpha"),
    )
    _write_insight(
        iroot,
        _insights_artifact(run_id="b", repo_name="beta", repo_path="/repos/beta"),
    )
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    current, _ = loader.load(repo="/REPOS/BETA", insight_run_id=None, history_limit=10)

    assert current.run_id == "b"


def test_load_explicit_run_id_match(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(iroot, _insights_artifact(run_id="r1", generated_at=_dt(5)))
    _write_insight(iroot, _insights_artifact(run_id="r2", generated_at=_dt(1)))
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    current, _ = loader.load(repo=None, insight_run_id="r2", history_limit=10)

    assert current.run_id == "r2"


def test_load_explicit_run_id_not_found_raises(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(iroot, _insights_artifact(run_id="r1"))
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    with pytest.raises(ValueError, match="Insight run id not found: nope"):
        loader.load(repo=None, insight_run_id="nope", history_limit=10)


def test_load_no_insights_raises(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    iroot.mkdir()
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    with pytest.raises(ValueError, match="No insight artifacts found"):
        loader.load(repo=None, insight_run_id=None, history_limit=10)


def test_load_repo_filter_excludes_everything_raises(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(iroot, _insights_artifact(run_id="a", repo_name="alpha"))
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    with pytest.raises(ValueError, match="No insight artifacts found"):
        loader.load(repo="missing", insight_run_id=None, history_limit=10)


def test_load_prior_decisions_filtered_and_limited(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(
        iroot,
        _insights_artifact(run_id="current", repo_path="/repos/alpha", generated_at=_dt(9)),
    )

    # Same repo, different source insight -> included (newest first)
    _write_decision(
        droot,
        _decision_artifact(
            run_id="dA",
            source_insight_run_id="prev1",
            repo_path="/repos/alpha",
            generated_at=_dt(8),
        ),
    )
    _write_decision(
        droot,
        _decision_artifact(
            run_id="dB",
            source_insight_run_id="prev2",
            repo_path="/repos/alpha",
            generated_at=_dt(7),
        ),
    )
    # Same repo but source == current.run_id -> excluded
    _write_decision(
        droot,
        _decision_artifact(
            run_id="dSelf",
            source_insight_run_id="current",
            repo_path="/repos/alpha",
            generated_at=_dt(6),
        ),
    )
    # Different repo path -> excluded
    _write_decision(
        droot,
        _decision_artifact(
            run_id="dOther",
            source_insight_run_id="prevX",
            repo_path="/repos/beta",
            generated_at=_dt(5),
        ),
    )
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    current, prior = loader.load(repo=None, insight_run_id=None, history_limit=1)

    assert current.run_id == "current"
    assert len(prior) == 1
    assert prior[0].run_id == "dA"


def test_load_prior_decisions_history_limit_zero(tmp_path) -> None:
    iroot = tmp_path / "insights"
    droot = tmp_path / "decisions"
    _write_insight(iroot, _insights_artifact(run_id="current", repo_path="/repos/alpha"))
    _write_decision(
        droot,
        _decision_artifact(
            run_id="dA",
            source_insight_run_id="prev1",
            repo_path="/repos/alpha",
        ),
    )
    loader = DecisionLoader(insights_root=iroot, decision_root=droot)

    _, prior = loader.load(repo=None, insight_run_id=None, history_limit=0)

    assert prior == []


def test_all_insights_sorted_descending_by_generated_at(tmp_path) -> None:
    iroot = tmp_path / "insights"
    _write_insight(iroot, _insights_artifact(run_id="mid", generated_at=_dt(3)))
    _write_insight(iroot, _insights_artifact(run_id="new", generated_at=_dt(9)))
    _write_insight(iroot, _insights_artifact(run_id="old", generated_at=_dt(1)))
    loader = DecisionLoader(insights_root=iroot, decision_root=tmp_path / "d")

    result = loader._all_insights()

    assert [a.run_id for a in result] == ["new", "mid", "old"]


def test_all_insights_empty_when_no_files(tmp_path) -> None:
    iroot = tmp_path / "insights"
    iroot.mkdir()
    loader = DecisionLoader(insights_root=iroot, decision_root=tmp_path / "d")

    assert loader._all_insights() == []


def test_all_decisions_sorted_descending(tmp_path) -> None:
    droot = tmp_path / "decisions"
    _write_decision(
        droot,
        _decision_artifact(run_id="d1", source_insight_run_id="i1", generated_at=_dt(2)),
    )
    _write_decision(
        droot,
        _decision_artifact(run_id="d2", source_insight_run_id="i2", generated_at=_dt(8)),
    )
    loader = DecisionLoader(insights_root=tmp_path / "i", decision_root=droot)

    result = loader._all_decisions()

    assert [a.run_id for a in result] == ["d2", "d1"]


def test_all_decisions_empty_when_dir_missing(tmp_path) -> None:
    # glob on a non-existent dir yields nothing rather than raising
    loader = DecisionLoader(
        insights_root=tmp_path / "i",
        decision_root=tmp_path / "does_not_exist",
    )

    assert loader._all_decisions() == []
