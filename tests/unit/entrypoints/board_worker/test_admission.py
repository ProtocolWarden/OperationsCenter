# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for task-admission author allowlist (Phase B1, determinism surface 5).

The board must not auto-claim a task authored by an un-allowlisted actor when an
allowlist is configured. Disabled (empty allowlist) preserves prior behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from operations_center.config.settings import TaskAdmissionSettings
from operations_center.entrypoints.board_worker import claim


def _repo_cfg():
    return SimpleNamespace(clone_url="https://github.com/owner/repo.git", max_daily_executions=None)


def _settings(allowlist):
    return SimpleNamespace(
        repos={"repoA": _repo_cfg()},
        git_token=lambda: None,  # disables OPEN_PR_GATE network path
        task_admission=TaskAdmissionSettings(author_allowlist=list(allowlist)),
    )


_GOOD_DESC = "## Goal\n" + ("Implement the full widget pipeline end to end. " * 3)


def _issue(*, author, task_id="t1"):
    return {
        "id": task_id,
        "name": "A reasonably long task title that exceeds forty characters",
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: repoA"}],
        "state": {"name": "Ready for AI"},
        "created_at": "2026-01-01T00:00:00Z",
        "description": _GOOD_DESC,
        "created_by": author,
    }


def test_allowed_when_unenforced():
    assert claim._author_allowed(_issue(author="anyone"), _settings([]))


def test_blocks_unlisted_author():
    assert not claim._author_allowed(_issue(author="mallory"), _settings(["alice"]))


def test_allows_listed_author_case_insensitive():
    assert claim._author_allowed(_issue(author="Alice"), _settings(["alice"]))


def test_matches_nested_actor_email():
    issue = _issue(author="ignored")
    issue["created_by"] = {"id": "u1", "email": "alice@corp.test"}
    assert claim._author_allowed(issue, _settings(["alice@corp.test"]))
    assert not claim._author_allowed(issue, _settings(["bob@corp.test"]))


def test_claim_next_skips_unauthorized_and_labels_once():
    issue = _issue(author="mallory")
    client = MagicMock()
    client.list_issues.return_value = [issue]
    settings = _settings(["alice"])

    result = claim.claim_next(client, "goal", settings)

    assert result is None  # not claimed
    client.transition_issue.assert_not_called()  # never moved to Running
    # flagged for operator promotion (add_label → client.update_issue_labels)
    client.update_issue_labels.assert_called_once()
    assert "unauthorized-author" in client.update_issue_labels.call_args.args[1]


def test_claim_next_allows_authorized_author():
    issue = _issue(author="alice")
    client = MagicMock()
    client.list_issues.return_value = [issue]
    settings = _settings(["alice"])

    result = claim.claim_next(client, "goal", settings)

    assert result is not None
    client.transition_issue.assert_called_once()  # claimed → Running
