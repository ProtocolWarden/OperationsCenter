# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.proposer.candidate_loader import ProposalCandidateLoader


def _write_decision(
    decision_root: Path,
    *,
    dir_name: str,
    run_id: str,
    source_insight_run_id: str,
    repo_name: str = "acme",
    repo_path: str = "/repos/acme",
    generated_at: str = "2026-06-01T12:00:00+00:00",
) -> Path:
    payload = {
        "run_id": run_id,
        "generated_at": generated_at,
        "source_command": "oc decide",
        "repo": {"name": repo_name, "path": repo_path},
        "source_insight_run_id": source_insight_run_id,
        "candidates": [],
        "suppressed": [],
    }
    import json

    folder = decision_root / dir_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "proposal_candidates.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_insight(
    insights_root: Path,
    *,
    run_id: str,
    repo_name: str = "acme",
    repo_path: str = "/repos/acme",
) -> Path:
    payload = {
        "run_id": run_id,
        "generated_at": "2026-06-01T11:00:00+00:00",
        "source_command": "oc insights",
        "repo": {"name": repo_name, "path": repo_path},
        "source_snapshots": [],
        "insights": [],
    }
    import json

    folder = insights_root / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "repo_insights.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_loader(tmp_path: Path) -> ProposalCandidateLoader:
    return ProposalCandidateLoader(
        decision_root=tmp_path / "decision",
        insights_root=tmp_path / "insights",
    )


def test_default_roots_used_when_not_provided() -> None:
    loader = ProposalCandidateLoader()
    assert loader.decision_root == Path("tools/report/operations_center/decision")
    assert loader.insights_root == Path("tools/report/operations_center/insights")


def test_custom_roots_preserved(tmp_path: Path) -> None:
    loader = ProposalCandidateLoader(
        decision_root=tmp_path / "d",
        insights_root=tmp_path / "i",
    )
    assert loader.decision_root == tmp_path / "d"
    assert loader.insights_root == tmp_path / "i"


def test_load_happy_path_returns_decision_and_insight(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="run1",
        run_id="dec-1",
        source_insight_run_id="ins-1",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    decision, insight = loader.load(repo=None, decision_run_id=None)

    assert decision.run_id == "dec-1"
    assert insight.run_id == "ins-1"


def test_load_picks_most_recent_when_no_run_id(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="old",
        run_id="dec-old",
        source_insight_run_id="ins-1",
        generated_at="2026-05-01T00:00:00+00:00",
    )
    _write_decision(
        tmp_path / "decision",
        dir_name="new",
        run_id="dec-new",
        source_insight_run_id="ins-1",
        generated_at="2026-06-01T00:00:00+00:00",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    decision, _ = loader.load(repo=None, decision_run_id=None)

    assert decision.run_id == "dec-new"


def test_load_specific_decision_run_id(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="a",
        run_id="dec-a",
        source_insight_run_id="ins-1",
    )
    _write_decision(
        tmp_path / "decision",
        dir_name="b",
        run_id="dec-b",
        source_insight_run_id="ins-1",
        generated_at="2026-06-02T00:00:00+00:00",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    decision, _ = loader.load(repo=None, decision_run_id="dec-a")

    assert decision.run_id == "dec-a"


def test_load_unknown_decision_run_id_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="a",
        run_id="dec-a",
        source_insight_run_id="ins-1",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    with pytest.raises(ValueError, match="Decision run id not found: nope"):
        loader.load(repo=None, decision_run_id="nope")


def test_load_no_decisions_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    (tmp_path / "decision").mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="No decision artifacts found"):
        loader.load(repo=None, decision_run_id=None)


def test_load_filter_by_repo_name_case_insensitive(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="acme",
        run_id="dec-acme",
        source_insight_run_id="ins-1",
        repo_name="Acme",
        repo_path="/repos/acme",
    )
    _write_decision(
        tmp_path / "decision",
        dir_name="other",
        run_id="dec-other",
        source_insight_run_id="ins-1",
        repo_name="other",
        repo_path="/repos/other",
        generated_at="2026-07-01T00:00:00+00:00",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    decision, _ = loader.load(repo="  ACME ", decision_run_id=None)

    assert decision.run_id == "dec-acme"


def test_load_filter_by_repo_path(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="acme",
        run_id="dec-acme",
        source_insight_run_id="ins-1",
        repo_name="acme",
        repo_path="/Repos/Acme",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    decision, _ = loader.load(repo="/repos/acme", decision_run_id=None)

    assert decision.run_id == "dec-acme"


def test_load_filter_by_repo_no_match_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="acme",
        run_id="dec-acme",
        source_insight_run_id="ins-1",
        repo_name="acme",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    with pytest.raises(ValueError, match="No decision artifacts found"):
        loader.load(repo="missing", decision_run_id=None)


def test_load_repo_filter_combined_with_run_id_no_match_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="acme",
        run_id="dec-acme",
        source_insight_run_id="ins-1",
        repo_name="acme",
    )
    _write_insight(tmp_path / "insights", run_id="ins-1")

    # run id exists but filtered out by repo mismatch
    with pytest.raises(ValueError, match="Decision run id not found"):
        loader.load(repo="other", decision_run_id="dec-acme")


def test_load_missing_insight_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="a",
        run_id="dec-a",
        source_insight_run_id="ins-missing",
    )
    # No insight artifact written.

    with pytest.raises(ValueError, match="Insight artifact not found.*ins-missing"):
        loader.load(repo=None, decision_run_id=None)


def test_all_decisions_empty_when_root_missing(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    # decision root does not exist at all -> glob yields nothing.
    result = loader._all_decisions()
    assert result == []


def test_all_decisions_sorted_descending(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_decision(
        tmp_path / "decision",
        dir_name="mid",
        run_id="mid",
        source_insight_run_id="ins-1",
        generated_at="2026-03-01T00:00:00+00:00",
    )
    _write_decision(
        tmp_path / "decision",
        dir_name="late",
        run_id="late",
        source_insight_run_id="ins-1",
        generated_at="2026-09-01T00:00:00+00:00",
    )
    _write_decision(
        tmp_path / "decision",
        dir_name="early",
        run_id="early",
        source_insight_run_id="ins-1",
        generated_at="2026-01-01T00:00:00+00:00",
    )

    ids = [a.run_id for a in loader._all_decisions()]
    assert ids == ["late", "mid", "early"]


def test_load_insight_returns_artifact(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    _write_insight(tmp_path / "insights", run_id="ins-x")

    insight = loader._load_insight("ins-x")

    assert insight.run_id == "ins-x"
    assert insight.generated_at == datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc)


def test_load_insight_missing_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    with pytest.raises(ValueError, match="Insight artifact not found"):
        loader._load_insight("nope")
