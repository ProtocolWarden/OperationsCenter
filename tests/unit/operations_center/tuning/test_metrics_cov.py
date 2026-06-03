# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from operations_center.tuning.metrics import (
    _iter_list,
    _sorted_artifact_dirs,
    aggregate_family_metrics,
)


def _write_decision(
    root: Path,
    run_id: str,
    *,
    candidates: list | None = None,
    suppressed: list | None = None,
    dry_run: bool = False,
    generated_at: str | None = "2026-04-04T12:00:00+00:00",
) -> Path:
    d = root / run_id
    d.mkdir(parents=True)
    artifact: dict[str, object] = {
        "run_id": run_id,
        "source_command": "test",
        "dry_run": dry_run,
        "candidates": candidates or [],
        "suppressed": suppressed or [],
    }
    if generated_at is not None:
        artifact["generated_at"] = generated_at
    (d / "proposal_candidates.json").write_text(json.dumps(artifact))
    return d


def _write_proposer(
    root: Path,
    run_id: str,
    decision_run_id: str,
    *,
    created: list | None = None,
    skipped: list | None = None,
    failed: list | None = None,
    dry_run: bool = False,
) -> Path:
    d = root / run_id
    d.mkdir(parents=True)
    artifact = {
        "run_id": run_id,
        "source_decision_run_id": decision_run_id,
        "dry_run": dry_run,
        "created": created or [],
        "skipped": skipped or [],
        "failed": failed or [],
    }
    (d / "proposal_results.json").write_text(json.dumps(artifact))
    return d


# ----- _sorted_artifact_dirs -----


def test_sorted_artifact_dirs_missing_root(tmp_path: Path) -> None:
    assert _sorted_artifact_dirs(tmp_path / "nope") == []


def test_sorted_artifact_dirs_reverse_and_skips_files(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    (tmp_path / "loose.txt").write_text("x")
    dirs = _sorted_artifact_dirs(tmp_path)
    assert [d.name for d in dirs] == ["c", "b", "a"]  # reverse sorted, no file


# ----- _iter_list -----


def test_iter_list_yields_only_dicts() -> None:
    data = {"items": [{"x": 1}, "scalar", 5, {"y": 2}, None]}
    out = list(_iter_list(data, "items"))
    assert out == [{"x": 1}, {"y": 2}]


def test_iter_list_missing_key() -> None:
    assert list(_iter_list({}, "items")) == []


def test_iter_list_non_list_value() -> None:
    assert list(_iter_list({"items": {"not": "a list"}}, "items")) == []


# ----- aggregate: empty / dry-run-only paths -----


def test_returns_empty_when_dirs_exist_but_no_candidate_files(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    (dec / "dec_1").mkdir(parents=True)  # dir without proposal_candidates.json
    metrics, runs, start, end = aggregate_family_metrics(
        decision_root=dec, proposer_root=tmp_path / "p"
    )
    assert metrics == []
    assert runs == 0
    assert start is None and end is None


def test_returns_empty_when_only_dry_run_artifacts(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}], dry_run=True)
    metrics, runs, start, end = aggregate_family_metrics(
        decision_root=dec, proposer_root=tmp_path / "p"
    )
    assert metrics == []
    assert runs == 0


def test_malformed_decision_json_is_skipped(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    bad = dec / "dec_bad"
    bad.mkdir(parents=True)
    (bad / "proposal_candidates.json").write_text("{not valid json")
    _write_decision(dec, "dec_ok", candidates=[{"family": "good"}])
    metrics, runs, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    assert runs == 1
    assert {m.family for m in metrics} == {"good"}


# ----- timestamps / window_start / window_end -----


def test_window_start_end_from_timestamps(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(
        dec, "dec_1", candidates=[{"family": "f"}], generated_at="2026-04-01T00:00:00+00:00"
    )
    _write_decision(
        dec, "dec_2", candidates=[{"family": "f"}], generated_at="2026-04-05T00:00:00+00:00"
    )
    _, _, start, end = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    assert start == datetime.fromisoformat("2026-04-01T00:00:00+00:00")
    assert end == datetime.fromisoformat("2026-04-05T00:00:00+00:00")


def test_invalid_timestamp_ignored(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}], generated_at="not-a-date")
    _, _, start, end = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    assert start is None and end is None


def test_missing_generated_at_yields_no_window(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}], generated_at=None)
    _, _, start, end = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    assert start is None and end is None


# ----- no_creation_rate -----


def test_no_creation_rate(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(
        dec,
        "dec_1",
        candidates=[{"family": "f"}, {"family": "f"}, {"family": "f"}, {"family": "f"}],
    )
    _write_proposer(prop, "prop_1", "dec_1", created=[{"family": "f"}])
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)
    m = next(m for m in metrics if m.family == "f")
    assert m.candidates_emitted == 4
    assert m.candidates_created == 1
    assert m.create_rate == 0.25
    assert m.no_creation_rate == 0.75


# ----- proposer indexing: malformed / dry_run / missing source -----


def test_proposer_dry_run_and_malformed_skipped(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}])
    _write_proposer(prop, "prop_dry", "dec_1", created=[{"family": "f"}], dry_run=True)
    bad = prop / "prop_bad"
    bad.mkdir(parents=True)
    (bad / "proposal_results.json").write_text("<<bad")
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)
    m = next(m for m in metrics if m.family == "f")
    # dry_run proposer excluded from index -> no creations attributed
    assert m.candidates_created == 0


def test_proposer_without_source_decision_id_not_indexed(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}])
    _write_proposer(prop, "prop_1", "", created=[{"family": "f"}])
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)
    m = next(m for m in metrics if m.family == "f")
    assert m.candidates_created == 0


def test_proposer_missing_results_file_skipped(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}])
    (prop / "prop_empty").mkdir(parents=True)  # dir but no results file
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)
    assert any(m.family == "f" for m in metrics)


# ----- feedback: merged / escalated / acceptance_rate -----


def _write_feedback(root: Path, name: str, record: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(record))


def test_feedback_merged_and_escalated_acceptance_rate(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    fb = tmp_path / "feedback"
    _write_decision(dec, "dec_1", candidates=[{"family": "obs"}])
    # proposer 'created' builds issue_to_family map (task_id -> family)
    _write_proposer(
        prop,
        "prop_1",
        "dec_1",
        created=[{"plane_issue_id": "ISSUE-1", "family": "obs"}],
    )
    _write_feedback(fb, "f1.json", {"task_id": "ISSUE-1", "outcome": "merged"})
    _write_feedback(fb, "f2.json", {"task_id": "ISSUE-1", "outcome": "merged"})
    _write_feedback(fb, "f3.json", {"task_id": "ISSUE-1", "outcome": "escalated"})
    metrics, _, _, _ = aggregate_family_metrics(
        decision_root=dec, proposer_root=prop, feedback_root=fb
    )
    m = next(m for m in metrics if m.family == "obs")
    assert m.proposals_merged == 2
    assert m.proposals_escalated == 1
    assert m.acceptance_rate == round(2 / 3, 3)


def test_feedback_via_plane_issue_id_fallback(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    fb = tmp_path / "feedback"
    _write_decision(dec, "dec_1", candidates=[{"family": "obs"}])
    _write_proposer(
        prop, "prop_1", "dec_1", created=[{"plane_issue_id": "ISSUE-9", "family": "obs"}]
    )
    # No task_id; use plane_issue_id fallback branch
    _write_feedback(fb, "f1.json", {"plane_issue_id": "ISSUE-9", "outcome": "merged"})
    metrics, _, _, _ = aggregate_family_metrics(
        decision_root=dec, proposer_root=prop, feedback_root=fb
    )
    m = next(m for m in metrics if m.family == "obs")
    assert m.proposals_merged == 1
    assert m.acceptance_rate == 1.0


def test_feedback_unmapped_and_malformed_skipped(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    fb = tmp_path / "feedback"
    _write_decision(dec, "dec_1", candidates=[{"family": "obs"}])
    _write_proposer(
        prop, "prop_1", "dec_1", created=[{"plane_issue_id": "ISSUE-1", "family": "obs"}]
    )
    # unknown task -> no family -> skipped
    _write_feedback(fb, "f1.json", {"task_id": "UNKNOWN", "outcome": "merged"})
    # malformed json -> skipped
    (fb / "bad.json").write_text("{broken")
    metrics, _, _, _ = aggregate_family_metrics(
        decision_root=dec, proposer_root=prop, feedback_root=fb
    )
    m = next(m for m in metrics if m.family == "obs")
    assert m.proposals_merged == 0
    assert m.acceptance_rate == 0.0


def test_default_feedback_root_used_when_none(tmp_path: Path, monkeypatch) -> None:
    # feedback_root None -> defaults to Path("state/proposal_feedback") relative to cwd
    monkeypatch.chdir(tmp_path)
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(dec, "dec_1", candidates=[{"family": "obs"}])
    _write_proposer(
        prop, "prop_1", "dec_1", created=[{"plane_issue_id": "ISSUE-1", "family": "obs"}]
    )
    fb = tmp_path / "state" / "proposal_feedback"
    _write_feedback(fb, "f1.json", {"task_id": "ISSUE-1", "outcome": "merged"})
    metrics, _, _, _ = aggregate_family_metrics(
        decision_root=dec, proposer_root=prop, feedback_root=None
    )
    m = next(m for m in metrics if m.family == "obs")
    assert m.proposals_merged == 1


# ----- top_suppression_reasons truncated to 5 -----


def test_top_suppression_reasons_truncated_to_five(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    suppressed = []
    for i, reason in enumerate(["r1", "r2", "r3", "r4", "r5", "r6"]):
        # give decreasing counts so ordering is deterministic
        for _ in range(6 - i):
            suppressed.append({"family": "obs", "reason": reason})
    _write_decision(dec, "dec_1", candidates=[], suppressed=suppressed)
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    m = next(m for m in metrics if m.family == "obs")
    assert len(m.top_suppression_reasons) == 5
    assert "r6" not in m.top_suppression_reasons


def test_suppression_default_reason_unknown(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[], suppressed=[{"family": "obs"}])
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    m = next(m for m in metrics if m.family == "obs")
    assert m.top_suppression_reasons == {"unknown": 1}


# ----- candidates/suppressed/items without family are ignored -----


def test_entries_without_family_ignored(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(
        dec,
        "dec_1",
        candidates=[{"status": "emit"}, {"family": "obs"}],
        suppressed=[{"reason": "x"}],
    )
    _write_proposer(
        prop,
        "prop_1",
        "dec_1",
        created=[{}],
        skipped=[{}],
        failed=[{}],
    )
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)
    families = {m.family for m in metrics}
    assert families == {"obs"}
    m = next(m for m in metrics if m.family == "obs")
    assert m.candidates_emitted == 1
    assert m.candidates_suppressed == 0


# ----- families sorted in output -----


def test_families_sorted(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(
        dec,
        "dec_1",
        candidates=[{"family": "zeta"}, {"family": "alpha"}, {"family": "mid"}],
    )
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    assert [m.family for m in metrics] == ["alpha", "mid", "zeta"]


def test_sample_runs_reflects_real_artifact_count(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[{"family": "f"}])
    _write_decision(dec, "dec_2", candidates=[{"family": "f"}], dry_run=True)
    metrics, runs, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    assert runs == 1
    assert all(m.sample_runs == 1 for m in metrics)
