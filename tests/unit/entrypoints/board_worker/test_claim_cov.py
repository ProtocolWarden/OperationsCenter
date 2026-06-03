# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations_center.entrypoints.board_worker import claim


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_repo_cfg(**kw):
    defaults = dict(
        clone_url="https://github.com/owner/repo.git",
        max_daily_executions=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_settings(repos=None, token="ghp_token"):
    return SimpleNamespace(
        repos=repos if repos is not None else {"repoA": _make_repo_cfg()},
        git_token=lambda: token,
    )


def _label(name):
    return {"name": name}


def _make_issue(
    *,
    task_id="abc123",
    state="Ready for AI",
    labels=None,
    name="A reasonably long task title that exceeds forty characters easily",
    description=None,
    priority=None,
    created_at="2026-01-01T00:00:00Z",
    updated_at=None,
):
    issue = {
        "id": task_id,
        "name": name,
        "labels": labels if labels is not None else [],
        "created_at": created_at,
    }
    if isinstance(state, dict):
        issue["state"] = state
    else:
        issue["state"] = {"name": state}
    if description is not None:
        issue["description"] = description
    if priority is not None:
        issue["priority"] = priority
    if updated_at is not None:
        issue["updated_at"] = updated_at
    return issue


def _client(issues=None):
    c = MagicMock()
    c.list_issues.return_value = issues if issues is not None else []
    return c


# A goal description long enough to pass the thin-goal guard.
_GOOD_DESC = "## Goal\n" + ("Implement the full widget pipeline end to end. " * 3)


# ── claim_next: list_issues failure ─────────────────────────────────────────────


def test_claim_next_list_issues_raises_returns_none():
    c = _client()
    c.list_issues.side_effect = RuntimeError("boom")
    result = claim.claim_next(c, "goal", _make_settings())
    assert result is None
    c.transition_issue.assert_not_called()


# ── claim_next: no candidates ────────────────────────────────────────────────────


def test_claim_next_no_candidates_returns_none():
    issue = _make_issue(state="Running", labels=[_label("task-kind: goal"), _label("repo: repoA")])
    c = _client([issue])
    result = claim.claim_next(c, "goal", _make_settings())
    assert result is None


# ── claim_next: happy path ───────────────────────────────────────────────────────


def test_claim_next_happy_path_claims_issue():
    issue = _make_issue(
        labels=[_label("task-kind: goal"), _label("repo: repoA")],
        description=_GOOD_DESC,
    )
    c = _client([issue])
    settings = _make_settings(token=None)  # token None -> open_pr_gate clear
    result = claim.claim_next(c, "goal", settings)
    assert result is issue
    c.transition_issue.assert_called_once_with("abc123", claim.STATE_RUNNING)


def test_claim_next_transition_raises_returns_none():
    issue = _make_issue(
        labels=[_label("task-kind: goal"), _label("repo: repoA")],
        description=_GOOD_DESC,
    )
    c = _client([issue])
    c.transition_issue.side_effect = RuntimeError("cannot transition")
    settings = _make_settings(token=None)
    result = claim.claim_next(c, "goal", settings)
    assert result is None


# ── claim_next: thin-goal guard ──────────────────────────────────────────────────


def test_claim_next_thin_goal_blocks_and_returns_none():
    issue = _make_issue(
        labels=[_label("task-kind: goal"), _label("repo: repoA")],
        name="short",
        description="## Goal\ntiny",
    )
    c = _client([issue])
    settings = _make_settings(token=None)
    result = claim.claim_next(c, "goal", settings)
    assert result is None
    # _block_thin_task transitions to BLOCKED and comments + labels.
    c.transition_issue.assert_called_once_with("abc123", claim.STATE_BLOCKED)
    c.comment_issue.assert_called_once()
    c.update_issue_labels.assert_called_once()


def test_claim_next_spec_author_skips_thin_guard():
    # spec-author bypasses the thin-goal guard even with a tiny goal.
    issue = _make_issue(
        labels=[_label("task-kind: spec-author")],
        name="x",
        description="",
    )
    c = _client([issue])
    settings = _make_settings(
        repos={"OperationsCenter": _make_repo_cfg()},
        token=None,
    )
    result = claim.claim_next(c, "spec-author", settings)
    assert result is issue
    c.transition_issue.assert_called_once_with("abc123", claim.STATE_RUNNING)


# ── claim_next: open PR gate (goal role only) ────────────────────────────────────


def test_claim_next_goal_blocked_by_open_pr_gate(monkeypatch):
    issue = _make_issue(
        labels=[_label("task-kind: goal"), _label("repo: repoA")],
        description=_GOOD_DESC,
    )
    c = _client([issue])
    settings = _make_settings(token="ghp_real")

    fake_gh = MagicMock()
    fake_gh.list_open_prs.return_value = [
        {"head": {"ref": "feature/x"}, "mergeable": "MERGEABLE", "number": 7}
    ]
    fake_cls = MagicMock(return_value=fake_gh)
    fake_cls.owner_repo_from_clone_url.return_value = ("owner", "repo")
    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", fake_cls, raising=True
    )

    result = claim.claim_next(c, "goal", settings)
    assert result is None
    # OPEN_PR_GATE label added; no claim transition.
    c.update_issue_labels.assert_called_once()
    c.transition_issue.assert_not_called()


def test_claim_next_test_role_skips_open_pr_gate():
    # Non-goal roles do not consult the PR gate at all.
    issue = _make_issue(
        labels=[_label("task-kind: test"), _label("repo: repoA")],
        description=_GOOD_DESC,
    )
    c = _client([issue])
    settings = _make_settings(token="ghp_real")
    result = claim.claim_next(c, "test", settings)
    assert result is issue
    c.transition_issue.assert_called_once_with("abc123", claim.STATE_RUNNING)


# ── _count_daily_executions ──────────────────────────────────────────────────────


def test_count_daily_executions_counts_recent_touched():
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    issues = [
        _make_issue(state="running", labels=[_label("repo: repoA")], updated_at=recent),
        _make_issue(state="done", labels=[_label("repo: repoA")], updated_at=recent),
        _make_issue(state="blocked", labels=[_label("repo: repoB")], updated_at=recent),
    ]
    counts = claim._count_daily_executions(issues)
    assert counts == {"repoA": 2, "repoB": 1}


def test_count_daily_executions_ignores_non_touched_states():
    now = datetime.now(UTC)
    recent = now.isoformat().replace("+00:00", "Z")
    issues = [
        _make_issue(state="Ready for AI", labels=[_label("repo: repoA")], updated_at=recent),
    ]
    assert claim._count_daily_executions(issues) == {}


def test_count_daily_executions_ignores_stale_and_bad_timestamps():
    now = datetime.now(UTC)
    stale = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    issues = [
        _make_issue(state="running", labels=[_label("repo: repoA")], updated_at=stale),
        _make_issue(state="running", labels=[_label("repo: repoB")], updated_at="not-a-date"),
        _make_issue(state="running", labels=[_label("repo: repoC")], updated_at=None),
    ]
    # stale (>24h) skipped, bad timestamp skipped, missing timestamp ("" -> parse fail) skipped.
    assert claim._count_daily_executions(issues) == {}


def test_count_daily_executions_string_state_and_no_repo_label():
    now = datetime.now(UTC)
    recent = now.isoformat().replace("+00:00", "Z")
    # state given as bare string "running"; no repo label -> not counted.
    issues = [
        _make_issue(state="running", labels=[], updated_at=recent),
        {"state": "running", "updated_at": recent, "labels": [_label("repo: repoX")], "id": "1"},
    ]
    assert claim._count_daily_executions(issues) == {"repoX": 1}


def test_count_daily_executions_none_state():
    now = datetime.now(UTC)
    recent = now.isoformat().replace("+00:00", "Z")
    issues = [{"state": None, "updated_at": recent, "labels": [], "id": "1"}]
    assert claim._count_daily_executions(issues) == {}


# ── _build_candidates ────────────────────────────────────────────────────────────


def test_build_candidates_filters_state_kind_and_repo():
    issues = [
        # wrong state
        _make_issue(state="Running", labels=[_label("task-kind: goal"), _label("repo: repoA")]),
        # wrong kind
        _make_issue(labels=[_label("task-kind: test"), _label("repo: repoA")]),
        # unmanaged repo
        _make_issue(labels=[_label("task-kind: goal"), _label("repo: other")]),
        # good
        _make_issue(task_id="good", labels=[_label("task-kind: goal"), _label("repo: repoA")]),
    ]
    settings = _make_settings()
    out = claim._build_candidates(issues, "goal", ["goal"], {"repoA"}, settings, {})
    assert [i["id"] for i in out] == ["good"]


def test_build_candidates_spec_author_repo_override():
    # spec-author kind forces repo_key to SPEC_AUTHOR_REPO_KEY regardless of label.
    issue = _make_issue(labels=[_label("task-kind: spec-author"), _label("repo: ignored")])
    settings = _make_settings(repos={claim.SPEC_AUTHOR_REPO_KEY: _make_repo_cfg()})
    out = claim._build_candidates(
        [issue], "spec-author", ["spec-author"], {claim.SPEC_AUTHOR_REPO_KEY}, settings, {}
    )
    assert len(out) == 1


def test_build_candidates_daily_quota_reached_skips():
    issue = _make_issue(labels=[_label("task-kind: goal"), _label("repo: repoA")])
    settings = _make_settings(repos={"repoA": _make_repo_cfg(max_daily_executions=2)})
    out = claim._build_candidates([issue], "goal", ["goal"], {"repoA"}, settings, {"repoA": 2})
    assert out == []


def test_build_candidates_under_quota_included():
    issue = _make_issue(labels=[_label("task-kind: goal"), _label("repo: repoA")])
    settings = _make_settings(repos={"repoA": _make_repo_cfg(max_daily_executions=5)})
    out = claim._build_candidates([issue], "goal", ["goal"], {"repoA"}, settings, {"repoA": 1})
    assert len(out) == 1


def test_build_candidates_string_state():
    issue = {
        "id": "s1",
        "state": "Ready for AI",
        "labels": [_label("task-kind: goal"), _label("repo: repoA")],
        "created_at": "2026-01-01",
        "name": "n",
    }
    settings = _make_settings()
    out = claim._build_candidates([issue], "goal", ["goal"], {"repoA"}, settings, {})
    assert len(out) == 1


def test_build_candidates_repo_cfg_missing_no_cap():
    # repo is managed but has no cfg object -> cap None -> not skipped.
    issue = _make_issue(labels=[_label("task-kind: goal"), _label("repo: repoA")])
    settings = SimpleNamespace(repos={"repoA": None}, git_token=lambda: None)
    out = claim._build_candidates([issue], "goal", ["goal"], {"repoA"}, settings, {"repoA": 99})
    assert len(out) == 1


# ── _sort_key ─────────────────────────────────────────────────────────────────


def test_sort_key_improve_suggestion_first():
    suggestion = _make_issue(
        labels=[_label("source: improve-suggestion")], priority="low", created_at="2026-01-02"
    )
    normal = _make_issue(labels=[], priority="urgent", created_at="2026-01-01")
    # improve-suggestion ranks before everything despite worse priority/newer.
    assert claim._sort_key(suggestion) < claim._sort_key(normal)


def test_sort_key_priority_order_and_created_at():
    high = _make_issue(priority="high", created_at="2026-01-05")
    medium = _make_issue(priority="medium", created_at="2026-01-01")
    assert claim._sort_key(high) < claim._sort_key(medium)
    # unknown priority falls back to rank 4
    unknown = _make_issue(priority="bogus")
    assert claim._sort_key(unknown)[1] == 4
    # missing priority -> "none" -> 4
    nopri = _make_issue()
    assert claim._sort_key(nopri)[1] == 4


def test_sort_key_string_labels():
    issue = {"labels": ["source: improve-suggestion"], "priority": "high", "created_at": "x"}
    key = claim._sort_key(issue)
    assert key[0] == 0


def test_claim_next_sort_picks_best_candidate():
    older = _make_issue(
        task_id="older",
        labels=[_label("task-kind: goal"), _label("repo: repoA")],
        description=_GOOD_DESC,
        priority="medium",
        created_at="2026-01-01",
    )
    suggestion = _make_issue(
        task_id="sugg",
        labels=[
            _label("task-kind: goal"),
            _label("repo: repoA"),
            _label("source: improve-suggestion"),
        ],
        description=_GOOD_DESC,
        priority="low",
        created_at="2026-02-01",
    )
    c = _client([older, suggestion])
    settings = _make_settings(token=None)
    result = claim.claim_next(c, "goal", settings)
    assert result is suggestion


# ── _block_thin_task error path ──────────────────────────────────────────────────


def test_block_thin_task_swallows_exceptions():
    issue = _make_issue(labels=[])
    c = MagicMock()
    c.transition_issue.side_effect = RuntimeError("transition failed")
    # Should not raise even though transition fails.
    claim._block_thin_task(c, issue, "goal", 5)
    c.transition_issue.assert_called_once()
    # comment/label not reached because transition raised first.
    c.comment_issue.assert_not_called()


# ── _open_pr_gate_clear ──────────────────────────────────────────────────────────


def test_open_pr_gate_clear_no_token():
    issue = _make_issue(labels=[_label("repo: repoA")])
    settings = _make_settings(token=None)
    c = MagicMock()
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is True


def test_open_pr_gate_clear_no_clone_url():
    issue = _make_issue(labels=[_label("repo: repoA")])
    settings = _make_settings(repos={"repoA": _make_repo_cfg(clone_url=None)}, token="ghp")
    c = MagicMock()
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is True


def test_open_pr_gate_clear_no_repo_label():
    issue = _make_issue(labels=[])
    settings = _make_settings(token="ghp")
    c = MagicMock()
    # gate_repo_key "" -> cfg None -> clone_url None -> returns True
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is True


def test_open_pr_gate_clear_no_blocking_prs(monkeypatch):
    issue = _make_issue(labels=[_label("repo: repoA")])
    settings = _make_settings(token="ghp")
    c = MagicMock()

    fake_gh = MagicMock()
    fake_gh.list_open_prs.return_value = [
        {"head": {"ref": "spec-author/foo"}, "mergeable": "MERGEABLE", "number": 1},
        {"head": {"ref": "feature/y"}, "mergeable": "UNKNOWN", "number": 2},
    ]
    fake_cls = MagicMock(return_value=fake_gh)
    fake_cls.owner_repo_from_clone_url.return_value = ("owner", "repo")
    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", fake_cls, raising=True
    )
    # spec-author and UNKNOWN PRs are both non-blocking -> gate clear.
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is True
    c.update_issue_labels.assert_not_called()


def test_open_pr_gate_clear_blocking_pr_missing_head(monkeypatch):
    issue = _make_issue(labels=[_label("repo: repoA")])
    settings = _make_settings(token="ghp")
    c = MagicMock()

    fake_gh = MagicMock()
    # head missing -> (None or {}).get -> "" -> not spec-author -> blocking
    fake_gh.list_open_prs.return_value = [{"mergeable": "MERGEABLE", "number": 9}]
    fake_cls = MagicMock(return_value=fake_gh)
    fake_cls.owner_repo_from_clone_url.return_value = ("owner", "repo")
    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", fake_cls, raising=True
    )
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is False
    c.update_issue_labels.assert_called_once()


def test_open_pr_gate_clear_exception_proceeds(monkeypatch):
    issue = _make_issue(labels=[_label("repo: repoA")])
    settings = _make_settings(token="ghp")
    c = MagicMock()

    fake_cls = MagicMock()
    fake_cls.side_effect = RuntimeError("github down")
    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", fake_cls, raising=True
    )
    # On any error the gate proceeds (returns True) rather than blocking.
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is True


def test_open_pr_gate_clear_many_blocking_truncates(monkeypatch):
    issue = _make_issue(labels=[_label("repo: repoA")])
    settings = _make_settings(token="ghp")
    c = MagicMock()

    prs = [{"head": {"ref": f"feat/{i}"}, "mergeable": "MERGEABLE", "number": i} for i in range(8)]
    fake_gh = MagicMock()
    fake_gh.list_open_prs.return_value = prs
    fake_cls = MagicMock(return_value=fake_gh)
    fake_cls.owner_repo_from_clone_url.return_value = ("owner", "repo")
    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", fake_cls, raising=True
    )
    assert claim._open_pr_gate_clear(c, issue, settings, "abc123") is False


def test_role_kinds_unknown_role_keyerror():
    # claim_next indexes ROLE_KINDS[role]; an unknown role raises KeyError.
    with pytest.raises(KeyError):
        claim.claim_next(_client([]), "nonexistent-role", _make_settings())
