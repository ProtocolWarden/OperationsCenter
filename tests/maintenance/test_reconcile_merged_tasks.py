# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the merged-PR → Plane-task reconciler."""

from __future__ import annotations

from datetime import UTC, datetime

from operations_center.entrypoints.maintenance import reconcile_merged_tasks as mod
from operations_center.entrypoints.maintenance.reconcile_merged_tasks import (
    TaskClosure,
    _merged_prs_recent,
    apply_closures,
    find_closures,
    main,
)

_UUID = "abcdef12-3456-7890-abcd-ef1234567890"


def _issue(tid, state, *, repo="OperationsCenter", name="t", desc=""):
    return {
        "id": tid,
        "name": name,
        "state": {"name": state},
        "labels": [{"name": f"repo: {repo}"}],
        "description": desc,
    }


def _pr(number, *, title="", body="", merged_at="2026-06-17T00:00:00Z"):
    return {"number": number, "title": title, "body": body, "merged_at": merged_at}


# ── find_closures: the pure matcher ──────────────────────────────────────────


def test_explicit_closes_ref_matches_uuid():
    issues = [_issue(_UUID, "Backlog")]
    prs = [_pr(42, body=f"Implements the thing.\n\nCloses {_UUID}")]
    cl = find_closures(issues, prs, "OperationsCenter")
    assert len(cl) == 1
    assert isinstance(cl[0], TaskClosure)
    assert cl[0].task_id == _UUID and cl[0].pr_number == 42 and cl[0].via == "closes-ref"


def test_close_keyword_variants_and_case_insensitive():
    for kw in ("Fixes", "resolved", "RESOLVES task", "Promotes"):
        cl = find_closures([_issue(_UUID, "Todo")], [_pr(7, title=f"{kw} {_UUID}")], "OperationsCenter")
        assert len(cl) == 1, kw


def test_in_review_task_with_merged_pr_number_in_desc():
    issues = [_issue(_UUID, "In Review", desc="Tracking work in PR #99.")]
    cl = find_closures(issues, [_pr(99)], "OperationsCenter")
    assert len(cl) == 1
    assert cl[0].via == "in-review-pr-merged" and cl[0].pr_number == 99


def test_backlog_task_with_pr_number_is_NOT_closed_by_convention():
    # The description convention only applies to In Review (where the watcher
    # actively tracks the PR). A Backlog task merely mentioning #99 is not closed.
    issues = [_issue(_UUID, "Backlog", desc="see PR #99")]
    assert find_closures(issues, [_pr(99)], "OperationsCenter") == []


def test_terminal_tasks_are_skipped():
    for st in ("Done", "Cancelled"):
        issues = [_issue(_UUID, st, desc="PR #99")]
        prs = [_pr(99, body=f"Closes {_UUID}")]
        assert find_closures(issues, prs, "OperationsCenter") == []


def test_wrong_repo_label_skipped():
    issues = [_issue(_UUID, "In Review", repo="OtherRepo", desc="PR #99")]
    assert find_closures(issues, [_pr(99)], "OperationsCenter") == []


def test_in_review_without_matching_merged_pr_not_closed():
    issues = [_issue(_UUID, "In Review", desc="PR #5")]
    assert find_closures(issues, [_pr(99)], "OperationsCenter") == []  # #5 not merged


def test_explicit_ref_wins_over_in_review_convention():
    issues = [_issue(_UUID, "In Review", desc="PR #99")]
    prs = [_pr(99), _pr(7, body=f"Closes {_UUID}")]
    cl = find_closures(issues, prs, "OperationsCenter")
    assert len(cl) == 1 and cl[0].via == "closes-ref" and cl[0].pr_number == 7


def test_each_task_closed_at_most_once():
    issues = [_issue(_UUID, "In Review", desc="PR #99 and #100")]
    cl = find_closures(issues, [_pr(99), _pr(100)], "OperationsCenter")
    assert len(cl) == 1


# ── _merged_prs_recent: filtering ────────────────────────────────────────────


class _FakeGitHub:
    def __init__(self, prs):
        self._prs = prs

    def list_closed_prs(self, owner, repo):
        return self._prs


def test_merged_prs_recent_filters_unmerged_and_old():
    now = datetime(2026, 6, 17, tzinfo=UTC)
    prs = [
        _pr(1, merged_at="2026-06-16T00:00:00Z"),  # recent merged → kept
        _pr(2, merged_at=None),  # closed-unmerged → dropped
        _pr(3, merged_at="2026-05-01T00:00:00Z"),  # old → dropped (7d window)
    ]
    gh = _FakeGitHub(prs)
    # owner_repo_from_clone_url is a staticmethod on the real class; patch via clone_url
    kept = _merged_prs_recent(gh, "https://github.com/ProtocolWarden/OperationsCenter.git", days=7, now=now)
    assert [p["number"] for p in kept] == [1]


# ── apply_closures: I/O via injected fake plane ──────────────────────────────


class _FakePlane:
    def __init__(self):
        self.transitions = []
        self.comments = []
        self.closed = False

    def transition_issue(self, tid, state):
        self.transitions.append((tid, state))

    def comment_issue(self, tid, md):
        self.comments.append((tid, md))

    def close(self):
        self.closed = True


def test_apply_closures_transitions_and_comments(monkeypatch):
    fake = _FakePlane()
    monkeypatch.setattr(mod, "_plane_client", lambda settings: fake)
    n = apply_closures(object(), [TaskClosure("R", _UUID, "t", 42, "closes-ref")])
    assert n == 1
    assert fake.transitions == [(_UUID, "Done")]
    assert "PR #42" in fake.comments[0][1]
    assert fake.closed is True


def test_apply_closures_one_failure_does_not_abort(monkeypatch):
    class _PartialPlane(_FakePlane):
        def transition_issue(self, tid, state):
            if tid == "bad":
                raise RuntimeError("boom")
            super().transition_issue(tid, state)

    fake = _PartialPlane()
    monkeypatch.setattr(mod, "_plane_client", lambda settings: fake)
    n = apply_closures(
        object(),
        [TaskClosure("R", "bad", "x", 1, "closes-ref"), TaskClosure("R", _UUID, "y", 2, "closes-ref")],
    )
    assert n == 1  # the good one still applied
    assert fake.closed is True


def test_apply_closures_empty_is_noop(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(mod, "_plane_client", lambda settings: called.__setitem__("n", called["n"] + 1))
    assert apply_closures(object(), []) == 0
    assert called["n"] == 0  # no client constructed when nothing to do


# ── main: report-only vs apply ───────────────────────────────────────────────


def test_main_report_only(monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: object())
    monkeypatch.setattr(mod, "scan", lambda *a, **k: [TaskClosure("R", _UUID, "task name", 42, "closes-ref")])
    applied = {"called": False}
    monkeypatch.setattr(mod, "apply_closures", lambda *a, **k: applied.__setitem__("called", True) or 1)
    rc = main(["--config", "x.yaml"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WOULD CLOSE" in out and "--apply" in out
    assert applied["called"] is False  # report-only must not apply


def test_main_apply(monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: object())
    monkeypatch.setattr(mod, "scan", lambda *a, **k: [TaskClosure("R", _UUID, "t", 42, "closes-ref")])
    monkeypatch.setattr(mod, "apply_closures", lambda *a, **k: 1)
    rc = main(["--config", "x.yaml", "--apply"])
    assert rc == 0
    assert "CLOSED" in capsys.readouterr().out
