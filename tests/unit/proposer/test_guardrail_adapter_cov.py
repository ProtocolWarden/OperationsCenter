# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.proposer.guardrail_adapter import (
    GuardrailResult,
    ProposerGuardrailAdapter,
)
from operations_center.proposer.result_models import (
    CreatedProposalResult,
    ProposalResultsArtifact,
    ProposerRepoRef,
)

NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
#  Helpers / fakes                                                            #
# --------------------------------------------------------------------------- #


def _make_usage_store(*, remaining: int = 100, min_remaining: int = 10) -> MagicMock:
    store = MagicMock()
    store.remaining_exec_capacity.return_value = remaining
    store.settings.min_remaining_exec_for_proposals = min_remaining
    store.record_proposal_budget_suppression.return_value = None
    return store


def _make_rejection_store(*, rejected: bool = False) -> MagicMock:
    store = MagicMock()
    store.is_rejected.return_value = rejected
    return store


def _make_client(issues: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.list_issues.return_value = issues or []
    return client


def _make_adapter(
    *,
    proposer_root: Path | None = None,
    cooldown_minutes: int = 120,
    recently_done_window_days: int = 7,
    usage_store: MagicMock | None = None,
    rejection_store: MagicMock | None = None,
) -> ProposerGuardrailAdapter:
    return ProposerGuardrailAdapter(
        proposer_root=proposer_root,
        cooldown_minutes=cooldown_minutes,
        recently_done_window_days=recently_done_window_days,
        usage_store=usage_store or _make_usage_store(),
        rejection_store=rejection_store or _make_rejection_store(),
    )


def _write_artifact(
    root: Path,
    run_id: str,
    *,
    generated_at: datetime,
    created: list[CreatedProposalResult],
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact = ProposalResultsArtifact(
        run_id=run_id,
        generated_at=generated_at,
        source_command="proposer",
        repo=ProposerRepoRef(name="repo", path=Path("/tmp/repo")),
        source_decision_run_id="dec-1",
        created=created,
    )
    path = run_dir / "proposal_results.json"
    path.write_text(artifact.model_dump_json(), encoding="utf-8")
    return path


def _created_item(dedup_key: str, status: str = "created") -> CreatedProposalResult:
    return CreatedProposalResult(
        candidate_id="c1",
        dedup_key=dedup_key,
        family="fam",
        plane_title="Title",
        status=status,
    )


# --------------------------------------------------------------------------- #
#  Construction / defaults                                                    #
# --------------------------------------------------------------------------- #


def test_default_proposer_root_and_rejection_store(monkeypatch):
    monkeypatch.delenv("OPERATIONS_CENTER_REJECTION_STORE_PATH", raising=False)
    adapter = ProposerGuardrailAdapter()
    assert adapter.proposer_root == Path("tools/report/operations_center/proposer")
    assert adapter.cooldown_minutes == 120
    assert adapter.recently_done_window_days == 7
    assert adapter._usage_store is None
    # rejection store defaulted to a real ProposalRejectionStore instance
    assert adapter._rejection_store is not None


def test_dataclass_result_defaults():
    res = GuardrailResult(allowed=True)
    assert res.allowed is True
    assert res.reason is None
    assert res.evidence is None


# --------------------------------------------------------------------------- #
#  evaluate: rejection branch (first check)                                   #
# --------------------------------------------------------------------------- #


def test_evaluate_permanently_rejected(tmp_path):
    rej = _make_rejection_store(rejected=True)
    usage = _make_usage_store()
    adapter = _make_adapter(proposer_root=tmp_path, rejection_store=rej, usage_store=usage)
    client = _make_client()
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res.allowed is False
    assert res.reason == "permanently_rejected_by_human"
    assert res.evidence == {"dedup_key": "k1"}
    # rejection short-circuits before usage / client
    usage.remaining_exec_capacity.assert_not_called()
    client.list_issues.assert_not_called()


# --------------------------------------------------------------------------- #
#  evaluate: budget branch                                                    #
# --------------------------------------------------------------------------- #


def test_evaluate_budget_too_low(tmp_path):
    usage = _make_usage_store(remaining=5, min_remaining=10)
    adapter = _make_adapter(proposer_root=tmp_path, usage_store=usage)
    client = _make_client()
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res.allowed is False
    assert res.reason == "proposal_budget_too_low"
    assert res.evidence == {"remaining_exec_capacity": 5, "min_required": 10}
    usage.record_proposal_budget_suppression.assert_called_once()
    kwargs = usage.record_proposal_budget_suppression.call_args.kwargs
    assert kwargs["reason"] == "proposal_budget_too_low"
    assert kwargs["now"] == NOW
    assert kwargs["evidence"] == {"remaining_exec_capacity": 5, "min_required": 10}
    client.list_issues.assert_not_called()


def test_evaluate_budget_equal_to_min_is_allowed(tmp_path):
    # remaining == min_remaining is NOT < min, so passes the budget gate.
    usage = _make_usage_store(remaining=10, min_remaining=10)
    adapter = _make_adapter(proposer_root=tmp_path, usage_store=usage)
    client = _make_client()
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res.allowed is True
    usage.record_proposal_budget_suppression.assert_not_called()


def test_evaluate_uses_internal_usage_store_when_not_injected(tmp_path, monkeypatch):
    # _usage_store is None -> constructs a UsageStore(); patch that symbol.
    import operations_center.proposer.guardrail_adapter as mod

    fake = _make_usage_store(remaining=100, min_remaining=10)
    monkeypatch.setattr(mod, "UsageStore", lambda: fake)
    adapter = ProposerGuardrailAdapter(
        proposer_root=tmp_path,
        usage_store=None,
        rejection_store=_make_rejection_store(),
    )
    client = _make_client()
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res.allowed is True
    fake.remaining_exec_capacity.assert_called_once_with(now=NOW)


# --------------------------------------------------------------------------- #
#  evaluate: open task match                                                  #
# --------------------------------------------------------------------------- #


def test_evaluate_open_task_match_by_title(tmp_path):
    issues = [{"id": "I1", "name": "My Task", "state": {"name": "In Progress"}}]
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client(issues)
    res = adapter.evaluate(client=client, dedup_key="k1", title="  my task  ", now=NOW)
    assert res.allowed is False
    assert res.reason == "existing_open_equivalent_task"
    assert res.evidence == {"plane_issue_id": "I1", "plane_title": "My Task"}


# --------------------------------------------------------------------------- #
#  evaluate: recently-done match                                              #
# --------------------------------------------------------------------------- #


def test_evaluate_recently_done_match(tmp_path):
    issues = [
        {
            "id": "I2",
            "name": "Done Task",
            "state": {"name": "Done"},
            "updated_at": (NOW - timedelta(days=1)).isoformat(),
        }
    ]
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client(issues)
    res = adapter.evaluate(client=client, dedup_key="k1", title="done task", now=NOW)
    assert res.allowed is False
    assert res.reason == "recently_completed_equivalent_task"
    assert res.evidence == {
        "plane_issue_id": "I2",
        "plane_title": "Done Task",
        "recently_done_window_days": 7,
    }


# --------------------------------------------------------------------------- #
#  evaluate: cooldown                                                         #
# --------------------------------------------------------------------------- #


def test_evaluate_cooldown_active(tmp_path):
    last_created = NOW - timedelta(minutes=30)
    _write_artifact(
        tmp_path,
        "run-1",
        generated_at=last_created,
        created=[_created_item("k1")],
    )
    adapter = _make_adapter(proposer_root=tmp_path, cooldown_minutes=120)
    client = _make_client()
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res.allowed is False
    assert res.reason == "cooldown_active"
    assert res.evidence["cooldown_minutes"] == 120
    assert res.evidence["last_created_at"] == last_created.isoformat()


def test_evaluate_cooldown_expired_allows(tmp_path):
    last_created = NOW - timedelta(minutes=200)
    _write_artifact(
        tmp_path,
        "run-1",
        generated_at=last_created,
        created=[_created_item("k1")],
    )
    adapter = _make_adapter(proposer_root=tmp_path, cooldown_minutes=120)
    client = _make_client()
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res.allowed is True


def test_evaluate_all_clear_allows(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client([])
    res = adapter.evaluate(client=client, dedup_key="k1", title="T", now=NOW)
    assert res == GuardrailResult(allowed=True)


# --------------------------------------------------------------------------- #
#  _find_open_task_match                                                       #
# --------------------------------------------------------------------------- #


def test_find_open_skips_done_and_cancelled(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    issues = [
        {"id": "D", "name": "match", "state": {"name": "Done"}},
        {"id": "C", "name": "match", "state": {"name": "Cancelled"}},
    ]
    client = _make_client(issues)
    assert adapter._find_open_task_match(client, dedup_key="k", title="match") is None


def test_find_open_match_by_candidate_dedup_key_line(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    issues = [
        {
            "id": "I9",
            "name": "Different",
            "state": {"name": "Todo"},
            "description": "intro\ncandidate_dedup_key: ABC\nmore",
        }
    ]
    client = _make_client(issues)
    out = adapter._find_open_task_match(client, dedup_key="abc", title="x")
    assert out == ("I9", "Different")


def test_find_open_match_by_proposal_dedup_key_line(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    issues = [
        {
            "id": "I10",
            "name": "Different",
            "state": {"name": "Todo"},
            "description_stripped": "- proposal_dedup_key: xyz",
        }
    ]
    client = _make_client(issues)
    out = adapter._find_open_task_match(client, dedup_key="XYZ", title="x")
    assert out == ("I10", "Different")


def test_find_open_no_match_when_state_not_dict(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    # state is not a dict -> state_name stays "", not skipped, but no match content
    issues = [{"id": "I", "name": "other", "state": "weird", "description": ""}]
    client = _make_client(issues)
    assert adapter._find_open_task_match(client, dedup_key="k", title="t") is None


def test_find_open_no_match_returns_none(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    issues = [
        {
            "id": "I",
            "name": "other",
            "state": {"name": "Todo"},
            "description": "candidate_dedup_key: zzz",
        }
    ]
    client = _make_client(issues)
    assert adapter._find_open_task_match(client, dedup_key="k", title="t") is None


# --------------------------------------------------------------------------- #
#  _find_recently_done_match                                                   #
# --------------------------------------------------------------------------- #


def test_recently_done_window_zero_returns_none(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path, recently_done_window_days=0)
    client = _make_client([{"id": "X", "name": "t", "state": {"name": "Done"}}])
    assert adapter._find_recently_done_match(client, dedup_key="k", title="t", now=NOW) is None


def test_recently_done_skips_non_terminal_state(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client([{"id": "X", "name": "t", "state": {"name": "In Progress"}}])
    assert adapter._find_recently_done_match(client, dedup_key="k", title="t", now=NOW) is None


def test_recently_done_skips_too_old(tmp_path):
    old = NOW - timedelta(days=30)
    adapter = _make_adapter(proposer_root=tmp_path, recently_done_window_days=7)
    client = _make_client(
        [
            {
                "id": "X",
                "name": "t",
                "state": {"name": "Done"},
                "updated_at": old.isoformat(),
            }
        ]
    )
    assert adapter._find_recently_done_match(client, dedup_key="k", title="t", now=NOW) is None


def test_recently_done_uses_completed_at_with_z_suffix(tmp_path):
    recent = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client(
        [
            {
                "id": "X",
                "name": "Match",
                "state": {"name": "Cancelled"},
                "completed_at": recent,
            }
        ]
    )
    out = adapter._find_recently_done_match(client, dedup_key="k", title="match", now=NOW)
    assert out == ("X", "Match")


def test_recently_done_naive_timestamp_treated_as_utc(tmp_path):
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client(
        [
            {
                "id": "N",
                "name": "Match",
                "state": {"name": "Done"},
                "updated_at": naive,
            }
        ]
    )
    out = adapter._find_recently_done_match(client, dedup_key="k", title="match", now=NOW)
    assert out == ("N", "Match")


def test_recently_done_bad_timestamp_falls_through_to_match(tmp_path):
    # ValueError on parse is swallowed; matching by title still works.
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client(
        [
            {
                "id": "B",
                "name": "Match",
                "state": {"name": "Done"},
                "updated_at": "not-a-date",
            }
        ]
    )
    out = adapter._find_recently_done_match(client, dedup_key="k", title="match", now=NOW)
    assert out == ("B", "Match")


def test_recently_done_empty_timestamp_skips_window_check(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client([{"id": "E", "name": "Match", "state": {"name": "Done"}}])
    out = adapter._find_recently_done_match(client, dedup_key="k", title="match", now=NOW)
    assert out == ("E", "Match")


def test_recently_done_match_by_dedup_key_lines(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    recent = (NOW - timedelta(days=1)).isoformat()
    client = _make_client(
        [
            {
                "id": "D1",
                "name": "Other",
                "state": {"name": "Done"},
                "updated_at": recent,
                "description": "- proposal_dedup_key: kk",
            }
        ]
    )
    out = adapter._find_recently_done_match(client, dedup_key="KK", title="x", now=NOW)
    assert out == ("D1", "Other")


def test_recently_done_candidate_dedup_key_line(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    recent = (NOW - timedelta(days=1)).isoformat()
    client = _make_client(
        [
            {
                "id": "D2",
                "name": "Other",
                "state": {"name": "Done"},
                "updated_at": recent,
                "description": "candidate_dedup_key: kk",
            }
        ]
    )
    out = adapter._find_recently_done_match(client, dedup_key="kk", title="x", now=NOW)
    assert out == ("D2", "Other")


def test_recently_done_no_match_returns_none(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    recent = (NOW - timedelta(days=1)).isoformat()
    client = _make_client(
        [
            {
                "id": "D3",
                "name": "Other",
                "state": {"name": "Done"},
                "updated_at": recent,
                "description": "nope",
            }
        ]
    )
    assert adapter._find_recently_done_match(client, dedup_key="kk", title="x", now=NOW) is None


def test_recently_done_state_not_dict_skipped(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client([{"id": "X", "name": "match", "state": None}])
    assert adapter._find_recently_done_match(client, dedup_key="k", title="match", now=NOW) is None


# --------------------------------------------------------------------------- #
#  _last_created_at                                                            #
# --------------------------------------------------------------------------- #


def test_last_created_none_when_no_artifacts(tmp_path):
    adapter = _make_adapter(proposer_root=tmp_path)
    assert adapter._last_created_at("k1") is None


def test_last_created_returns_generated_at_for_matching_key(tmp_path):
    gen = NOW - timedelta(minutes=5)
    _write_artifact(tmp_path, "run-1", generated_at=gen, created=[_created_item("k1", "created")])
    adapter = _make_adapter(proposer_root=tmp_path)
    assert adapter._last_created_at("k1") == gen


def test_last_created_matches_dry_run_status(tmp_path):
    gen = NOW - timedelta(minutes=5)
    _write_artifact(tmp_path, "run-1", generated_at=gen, created=[_created_item("k2", "dry_run")])
    adapter = _make_adapter(proposer_root=tmp_path)
    assert adapter._last_created_at("k2") == gen


def test_last_created_ignores_other_status(tmp_path):
    gen = NOW - timedelta(minutes=5)
    _write_artifact(tmp_path, "run-1", generated_at=gen, created=[_created_item("k3", "failed")])
    adapter = _make_adapter(proposer_root=tmp_path)
    assert adapter._last_created_at("k3") is None


def test_last_created_ignores_other_keys(tmp_path):
    gen = NOW - timedelta(minutes=5)
    _write_artifact(
        tmp_path, "run-1", generated_at=gen, created=[_created_item("other", "created")]
    )
    adapter = _make_adapter(proposer_root=tmp_path)
    assert adapter._last_created_at("k1") is None


def test_last_created_scans_newest_first(tmp_path):
    import os

    older = NOW - timedelta(minutes=100)
    newer = NOW - timedelta(minutes=1)
    p_old = _write_artifact(tmp_path, "run-old", generated_at=older, created=[_created_item("k1")])
    p_new = _write_artifact(tmp_path, "run-new", generated_at=newer, created=[_created_item("k1")])
    # Force mtime ordering: new file newer than old.
    os.utime(p_old, (1000, 1000))
    os.utime(p_new, (2000, 2000))
    adapter = _make_adapter(proposer_root=tmp_path)
    # Newest-mtime artifact is scanned first and returned.
    assert adapter._last_created_at("k1") == newer


def test_evaluate_cooldown_with_last_created_none_allows(tmp_path):
    # No artifacts -> _last_created_at is None -> cooldown skipped -> allowed.
    adapter = _make_adapter(proposer_root=tmp_path)
    client = _make_client([])
    res = adapter.evaluate(client=client, dedup_key="missing", title="T", now=NOW)
    assert res.allowed is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
