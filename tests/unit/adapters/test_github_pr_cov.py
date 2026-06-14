# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from unittest import mock

import httpx
import pytest

from operations_center.adapters import github_pr
from operations_center.adapters.github_pr import GitHubPRClient


def _make_response(
    *,
    status_code: int = 200,
    json_data=None,
    text: str | None = None,
    headers: dict | None = None,
):
    """Build a mock httpx.Response-like object."""
    resp = mock.Mock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_data
    if text is not None:
        resp.text = text

    def _raise():
        if status_code >= 400:
            raise httpx.HTTPStatusError("err", request=mock.Mock(), response=resp)

    resp.raise_for_status.side_effect = _raise
    return resp


@pytest.fixture
def client():
    return GitHubPRClient("tok123")


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
def test_init_sets_headers():
    c = GitHubPRClient("abc")
    assert c._headers["Authorization"] == "Bearer abc"
    assert c._headers["Accept"] == "application/vnd.github+json"
    assert c._headers["X-GitHub-Api-Version"] == "2022-11-28"


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------
def test_request_happy_path_sets_defaults(client):
    resp = _make_response(status_code=200, json_data={})
    with mock.patch.object(github_pr.httpx, "request", return_value=resp) as req:
        out = client._request("GET", "http://x")
    assert out is resp
    _, kwargs = req.call_args
    assert kwargs["timeout"] == 30
    assert kwargs["follow_redirects"] is True
    assert kwargs["headers"] == client._headers


def test_request_respects_caller_overrides(client):
    resp = _make_response(status_code=200)
    with mock.patch.object(github_pr.httpx, "request", return_value=resp) as req:
        client._request("GET", "http://x", timeout=5, follow_redirects=False)
    _, kwargs = req.call_args
    assert kwargs["timeout"] == 5
    assert kwargs["follow_redirects"] is False


def test_request_low_quota_warns(client, caplog):
    resp = _make_response(
        status_code=200,
        headers={"X-RateLimit-Remaining": "3", "X-RateLimit-Reset": "12345"},
    )
    with mock.patch.object(github_pr.httpx, "request", return_value=resp):
        with caplog.at_level("WARNING"):
            client._request("GET", "http://x")
    assert "github_rate_limit_low" in caplog.text


def test_request_high_quota_no_warn(client, caplog):
    resp = _make_response(status_code=200, headers={"X-RateLimit-Remaining": "500"})
    with mock.patch.object(github_pr.httpx, "request", return_value=resp):
        with caplog.at_level("WARNING"):
            client._request("GET", "http://x")
    assert "github_rate_limit_low" not in caplog.text


def test_request_invalid_remaining_header_ignored(client):
    resp = _make_response(status_code=200, headers={"X-RateLimit-Remaining": "not-a-number"})
    with mock.patch.object(github_pr.httpx, "request", return_value=resp):
        out = client._request("GET", "http://x")
    assert out is resp


def test_request_no_remaining_header(client):
    resp = _make_response(status_code=200, headers={})
    with mock.patch.object(github_pr.httpx, "request", return_value=resp):
        out = client._request("GET", "http://x")
    assert out is resp


def test_request_429_retries_then_succeeds(client, caplog):
    r429 = _make_response(status_code=429, headers={"Retry-After": "1"})
    r200 = _make_response(status_code=200, json_data={})
    seq = [r429, r200]
    with (
        mock.patch.object(github_pr.httpx, "request", side_effect=seq),
        mock.patch.object(github_pr.time, "sleep") as sleeper,
    ):
        with caplog.at_level("WARNING"):
            out = client._request("GET", "http://x")
    assert out is r200
    sleeper.assert_called_once_with(1)
    assert "github_rate_limited" in caplog.text


def test_request_429_invalid_retry_after_uses_default(client):
    r429 = _make_response(status_code=429, headers={"Retry-After": "soon"})
    r200 = _make_response(status_code=200)
    with (
        mock.patch.object(github_pr.httpx, "request", side_effect=[r429, r200]),
        mock.patch.object(github_pr.time, "sleep") as sleeper,
    ):
        client._request("GET", "http://x")
    sleeper.assert_called_once_with(github_pr._GH_RATE_LIMIT_DEFAULT_BACKOFF_SECONDS)


def test_request_429_missing_retry_after_uses_default(client):
    r429 = _make_response(status_code=429, headers={})
    r200 = _make_response(status_code=200)
    with (
        mock.patch.object(github_pr.httpx, "request", side_effect=[r429, r200]),
        mock.patch.object(github_pr.time, "sleep") as sleeper,
    ):
        client._request("GET", "http://x")
    sleeper.assert_called_once_with(github_pr._GH_RATE_LIMIT_DEFAULT_BACKOFF_SECONDS)


def test_request_429_exhausts_retries_returns_last(client):
    r429 = _make_response(status_code=429, headers={"Retry-After": "1"})
    # max retries = 3, so 4 attempts total; all 429
    with (
        mock.patch.object(github_pr.httpx, "request", return_value=r429) as req,
        mock.patch.object(github_pr.time, "sleep") as sleeper,
    ):
        out = client._request("GET", "http://x")
    assert out is r429
    assert req.call_count == github_pr._GH_RATE_LIMIT_MAX_RETRIES + 1
    assert sleeper.call_count == github_pr._GH_RATE_LIMIT_MAX_RETRIES


# ---------------------------------------------------------------------------
# owner_repo_from_clone_url
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("git@github.com:owner/repo", ("owner", "repo")),
    ],
)
def test_owner_repo_from_clone_url_ok(url, expected):
    assert GitHubPRClient.owner_repo_from_clone_url(url) == expected


def test_owner_repo_from_clone_url_invalid():
    with pytest.raises(ValueError, match="Cannot parse owner/repo"):
        GitHubPRClient.owner_repo_from_clone_url("nonsense")


# ---------------------------------------------------------------------------
# create_pr / get_pr / merge_pr / delete_branch
# ---------------------------------------------------------------------------
def test_create_pr(client):
    resp = _make_response(status_code=200, json_data={"number": 7})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        out = client.create_pr("o", "r", head="h", base="b", title="t", body="bod")
    assert out == {"number": 7}
    method, url = req.call_args[0]
    assert method == "POST"
    assert url.endswith("/repos/o/r/pulls")
    assert req.call_args[1]["json"] == {
        "title": "t",
        "head": "h",
        "base": "b",
        "body": "bod",
    }


def test_get_pr(client):
    resp = _make_response(status_code=200, json_data={"id": 1})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        out = client.get_pr("o", "r", 5)
    assert out == {"id": 1}
    assert req.call_args[0][0] == "GET"
    assert req.call_args[0][1].endswith("/repos/o/r/pulls/5")


def test_get_pr_raises_on_error(client):
    resp = _make_response(status_code=404)
    with mock.patch.object(client, "_request", return_value=resp):
        with pytest.raises(httpx.HTTPStatusError):
            client.get_pr("o", "r", 5)


def test_merge_pr_default_method(client):
    resp = _make_response(status_code=200, json_data={"merged": True})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        out = client.merge_pr("o", "r", 9)
    assert out == {"merged": True}
    assert req.call_args[1]["json"] == {"merge_method": "squash"}
    assert req.call_args[0][1].endswith("/repos/o/r/pulls/9/merge")


def test_merge_pr_custom_method(client):
    resp = _make_response(status_code=200, json_data={})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        client.merge_pr("o", "r", 9, merge_method="rebase")
    assert req.call_args[1]["json"] == {"merge_method": "rebase"}


def test_delete_branch(client):
    resp = _make_response(status_code=204)
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.delete_branch("o", "r", "feat") is None
    method, url = req.call_args[0]
    assert method == "DELETE"
    assert url.endswith("/git/refs/heads/feat")


# ---------------------------------------------------------------------------
# simple GET list/dict endpoints
# ---------------------------------------------------------------------------
def test_get_pr_reactions(client):
    resp = _make_response(status_code=200, json_data=[{"content": "+1"}])
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.get_pr_reactions("o", "r", 1) == [{"content": "+1"}]
    assert req.call_args[0][1].endswith("/issues/1/reactions")


def test_list_pr_comments(client):
    resp = _make_response(status_code=200, json_data=[{"body": "hi"}])
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.list_pr_comments("o", "r", 1) == [{"body": "hi"}]
    assert req.call_args[0][1].endswith("/issues/1/comments")


def test_list_pr_review_comments(client):
    resp = _make_response(status_code=200, json_data=[{"body": "x"}])
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.list_pr_review_comments("o", "r", 1) == [{"body": "x"}]
    assert req.call_args[0][1].endswith("/pulls/1/comments")


def test_get_comment_reactions(client):
    resp = _make_response(status_code=200, json_data=[{"content": "heart"}])
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.get_comment_reactions("o", "r", 42) == [{"content": "heart"}]
    assert req.call_args[0][1].endswith("/issues/comments/42/reactions")


def test_update_pr_description(client):
    resp = _make_response(status_code=200, json_data={"body": "new"})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        out = client.update_pr_description("o", "r", 3, "new")
    assert out == {"body": "new"}
    method, url = req.call_args[0]
    assert method == "PATCH"
    assert url.endswith("/pulls/3")
    assert req.call_args[1]["json"] == {"body": "new"}


def test_post_comment(client):
    resp = _make_response(status_code=200, json_data={"id": 11})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        out = client.post_comment("o", "r", 3, "hello")
    assert out == {"id": 11}
    method, url = req.call_args[0]
    assert method == "POST"
    assert url.endswith("/issues/3/comments")
    assert req.call_args[1]["json"] == {"body": "hello"}


# ---------------------------------------------------------------------------
# get_check_runs
# ---------------------------------------------------------------------------
def test_get_check_runs_returns_list(client):
    resp = _make_response(status_code=200, json_data={"check_runs": [{"name": "ci"}]})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.get_check_runs("o", "r", "sha") == [{"name": "ci"}]
    assert req.call_args[1]["params"] == {"per_page": 100}
    assert req.call_args[0][1].endswith("/commits/sha/check-runs")


def test_get_check_runs_missing_key(client):
    resp = _make_response(status_code=200, json_data={})
    with mock.patch.object(client, "_request", return_value=resp):
        assert client.get_check_runs("o", "r", "sha") == []


# ---------------------------------------------------------------------------
# get_failed_checks
# ---------------------------------------------------------------------------
def test_get_failed_checks_fetches_pr_when_none(client):
    pr = {"head": {"sha": "abc"}}
    with (
        mock.patch.object(client, "get_pr", return_value=pr) as getpr,
        mock.patch.object(
            client,
            "get_check_runs",
            return_value=[
                {"id": 1, "name": "lint", "conclusion": "failure"},
            ],
        ),
    ):
        out = client.get_failed_checks("o", "r", 1)
    getpr.assert_called_once_with("o", "r", 1)
    assert out == ["lint: failure"]


def test_get_failed_checks_no_head_sha(client):
    out = client.get_failed_checks("o", "r", 1, pr_data={"head": {}})
    assert out == []


def test_get_failed_checks_head_none(client):
    out = client.get_failed_checks("o", "r", 1, pr_data={"head": None})
    assert out == []


def test_get_failed_checks_check_runs_exception(client):
    with mock.patch.object(client, "get_check_runs", side_effect=RuntimeError("boom")):
        out = client.get_failed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == []


# ---------------------------------------------------------------------------
# get_incomplete_checks
# ---------------------------------------------------------------------------
def test_get_incomplete_checks_flags_running_runs(client):
    runs = [
        {"id": 1, "name": "lint", "status": "completed", "conclusion": "success"},
        {"id": 2, "name": "Test (pytest)", "status": "in_progress", "conclusion": None},
        {"id": 3, "name": "Snapshot", "status": "queued", "conclusion": None},
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_incomplete_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert sorted(out) == ["Snapshot", "Test (pytest)"]


def test_get_incomplete_checks_empty_when_all_completed(client):
    runs = [
        {"id": 1, "name": "lint", "status": "completed", "conclusion": "success"},
        {"id": 2, "name": "test", "status": "completed", "conclusion": "failure"},
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_incomplete_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == []


def test_get_incomplete_checks_honors_ignored_and_latest_run(client):
    # A stale in_progress run superseded by a newer completed run for the same
    # name must not count as pending; ignored names are excluded.
    runs = [
        {"id": 1, "name": "flaky", "status": "in_progress", "conclusion": None},
        {"id": 5, "name": "flaky", "status": "completed", "conclusion": "success"},
        {"id": 6, "name": "Snapshot validation", "status": "queued", "conclusion": None},
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_incomplete_checks(
            "o", "r", 1, pr_data={"head": {"sha": "x"}}, ignored_checks=["snapshot"]
        )
    assert out == []


def test_get_completed_checks_lists_terminal_runs(client):
    runs = [
        {"id": 1, "name": "lint", "status": "completed", "conclusion": "success"},
        {"id": 2, "name": "Test (pytest)", "status": "in_progress", "conclusion": None},
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_completed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == ["lint"]


def test_get_completed_checks_empty_when_no_runs(client):
    # The Guard C window: head pushed/rebased, no check runs registered yet.
    with mock.patch.object(client, "get_check_runs", return_value=[]):
        out = client.get_completed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == []


def test_get_completed_checks_honors_ignored(client):
    runs = [
        {"id": 1, "name": "Snapshot validation", "status": "completed", "conclusion": "success"}
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_completed_checks(
            "o", "r", 1, pr_data={"head": {"sha": "x"}}, ignored_checks=["snapshot"]
        )
    assert out == []


def test_get_incomplete_checks_no_head_sha(client):
    assert client.get_incomplete_checks("o", "r", 1, pr_data={"head": {}}) == []


def test_get_incomplete_checks_check_runs_exception(client):
    with mock.patch.object(client, "get_check_runs", side_effect=RuntimeError("boom")):
        out = client.get_incomplete_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == []


def test_get_failed_checks_uses_output_title(client):
    runs = [
        {
            "id": 1,
            "name": "build",
            "conclusion": "failure",
            "output": {"title": "compile error"},
        }
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_failed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == ["build: compile error"]


def test_get_failed_checks_output_none_falls_back_to_conclusion(client):
    runs = [{"id": 1, "name": "test", "conclusion": "timed_out", "output": None}]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_failed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == ["test: timed_out"]


def test_get_failed_checks_dedup_keeps_latest(client):
    runs = [
        {"id": 1, "name": "ci", "conclusion": "failure"},
        {"id": 5, "name": "ci", "conclusion": "success"},
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_failed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    # latest (id=5) is success -> no failures
    assert out == []


def test_get_failed_checks_ignored(client):
    runs = [
        {"id": 1, "name": "flaky-base-check", "conclusion": "failure"},
        {"id": 2, "name": "real", "conclusion": "cancelled"},
    ]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_failed_checks(
            "o",
            "r",
            1,
            pr_data={"head": {"sha": "x"}},
            ignored_checks=["flaky"],
        )
    assert out == ["real: cancelled"]


def test_get_failed_checks_success_not_failed(client):
    runs = [{"id": 1, "name": "ci", "conclusion": "success"}]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_failed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == []


def test_get_failed_checks_unnamed_run(client):
    runs = [{"id": 1, "conclusion": "failure"}]
    with mock.patch.object(client, "get_check_runs", return_value=runs):
        out = client.get_failed_checks("o", "r", 1, pr_data={"head": {"sha": "x"}})
    assert out == ["unknown: failure"]


# ---------------------------------------------------------------------------
# list_open_prs
# ---------------------------------------------------------------------------
def test_list_open_prs(client):
    resp = _make_response(status_code=200, json_data=[{"number": 1}])
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.list_open_prs("o", "r") == [{"number": 1}]
    assert req.call_args[1]["params"] == {"state": "open", "per_page": 100}


# ---------------------------------------------------------------------------
# list_pr_files
# ---------------------------------------------------------------------------
def test_list_pr_files_ok(client):
    resp = _make_response(
        status_code=200,
        json_data=[
            {"filename": "a.py"},
            {"filename": "b.py"},
            {"no_filename": True},
            "not-a-dict",
        ],
    )
    with mock.patch.object(client, "_request", return_value=resp):
        assert client.list_pr_files("o", "r", 1) == ["a.py", "b.py"]


def test_list_pr_files_error_returns_empty(client):
    with mock.patch.object(client, "_request", side_effect=RuntimeError):
        assert client.list_pr_files("o", "r", 1) == []


def test_list_pr_files_raise_for_status_error(client):
    resp = _make_response(status_code=500)
    with mock.patch.object(client, "_request", return_value=resp):
        assert client.list_pr_files("o", "r", 1) == []


# ---------------------------------------------------------------------------
# get_pr_diff / _pr_diff_too_large_summary
# ---------------------------------------------------------------------------
def test_get_pr_diff_ok(client):
    resp = _make_response(status_code=200, text="diff --git")
    with mock.patch.object(github_pr.httpx, "get", return_value=resp) as g:
        assert client.get_pr_diff("o", "r", 1) == "diff --git"
    _, kwargs = g.call_args
    assert kwargs["headers"]["Accept"] == "application/vnd.github.v3.diff"


def test_get_pr_diff_406_falls_back(client):
    resp = _make_response(status_code=406)
    with (
        mock.patch.object(github_pr.httpx, "get", return_value=resp),
        mock.patch.object(client, "_pr_diff_too_large_summary", return_value="SUMMARY") as summ,
    ):
        assert client.get_pr_diff("o", "r", 1) == "SUMMARY"
    summ.assert_called_once_with("o", "r", 1)


def test_get_pr_diff_http_error_returns_empty(client):
    resp = _make_response(status_code=500)
    with mock.patch.object(github_pr.httpx, "get", return_value=resp):
        assert client.get_pr_diff("o", "r", 1) == ""


def test_get_pr_diff_exception_returns_empty(client):
    with mock.patch.object(github_pr.httpx, "get", side_effect=httpx.ConnectError("x")):
        assert client.get_pr_diff("o", "r", 1) == ""


def test_pr_diff_too_large_summary_ok(client):
    files = [
        {
            "status": "added",
            "filename": "a.py",
            "additions": 5,
            "deletions": 0,
        },
        {"filename": "b.py"},  # defaults
        "not-a-dict",
    ]
    resp = _make_response(status_code=200, json_data=files)
    with mock.patch.object(client, "_request", return_value=resp):
        out = client._pr_diff_too_large_summary("o", "r", 1)
    assert "DIFF_TOO_LARGE" in out
    assert "showing 3 changed files" in out
    assert "added    a.py (+5/-0)" in out
    assert "modified b.py (+0/-0)" in out


def test_pr_diff_too_large_summary_error_returns_empty(client):
    with mock.patch.object(client, "_request", side_effect=RuntimeError):
        assert client._pr_diff_too_large_summary("o", "r", 1) == ""


def test_pr_diff_too_large_summary_raise_for_status_error(client):
    resp = _make_response(status_code=500)
    with mock.patch.object(client, "_request", return_value=resp):
        assert client._pr_diff_too_large_summary("o", "r", 1) == ""


# ---------------------------------------------------------------------------
# get_mergeable
# ---------------------------------------------------------------------------
def test_get_mergeable_true(client):
    with mock.patch.object(client, "get_pr", return_value={"mergeable": True}):
        assert client.get_mergeable("o", "r", 1) is True


def test_get_mergeable_false(client):
    with mock.patch.object(client, "get_pr", return_value={"mergeable": False}):
        assert client.get_mergeable("o", "r", 1) is False


def test_get_mergeable_none_while_computing(client):
    with mock.patch.object(client, "get_pr", return_value={"mergeable": None}):
        assert client.get_mergeable("o", "r", 1) is None


def test_get_mergeable_exception(client):
    with mock.patch.object(client, "get_pr", side_effect=RuntimeError):
        assert client.get_mergeable("o", "r", 1) is None


# ---------------------------------------------------------------------------
# close_pr
# ---------------------------------------------------------------------------
def test_close_pr(client):
    resp = _make_response(status_code=200, json_data={"state": "closed"})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.close_pr("o", "r", 1) == {"state": "closed"}
    method, url = req.call_args[0]
    assert method == "PATCH"
    assert req.call_args[1]["json"] == {"state": "closed"}


# ---------------------------------------------------------------------------
# list_pr_reviews / pr_has_changes_requested
# ---------------------------------------------------------------------------
def test_list_pr_reviews(client):
    resp = _make_response(status_code=200, json_data=[{"state": "APPROVED"}])
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.list_pr_reviews("o", "r", 1) == [{"state": "APPROVED"}]
    assert req.call_args[1]["params"] == {"per_page": 100}


def test_pr_has_changes_requested_true(client):
    reviews = [{"state": "APPROVED"}, {"state": "CHANGES_REQUESTED"}]
    with mock.patch.object(client, "list_pr_reviews", return_value=reviews):
        assert client.pr_has_changes_requested("o", "r", 1) is True


def test_pr_has_changes_requested_false(client):
    reviews = [{"state": "APPROVED"}]
    with mock.patch.object(client, "list_pr_reviews", return_value=reviews):
        assert client.pr_has_changes_requested("o", "r", 1) is False


def test_pr_has_changes_requested_exception(client):
    with mock.patch.object(client, "list_pr_reviews", side_effect=RuntimeError):
        assert client.pr_has_changes_requested("o", "r", 1) is False


# ---------------------------------------------------------------------------
# get_branch_head
# ---------------------------------------------------------------------------
def test_get_branch_head_ok(client):
    resp = _make_response(status_code=200, json_data={"commit": {"sha": "deadbeef"}})
    with mock.patch.object(client, "_request", return_value=resp) as req:
        assert client.get_branch_head("o", "r", "main") == "deadbeef"
    assert req.call_args[0][1].endswith("/branches/main")


def test_get_branch_head_empty_sha_returns_none(client):
    resp = _make_response(status_code=200, json_data={"commit": {"sha": ""}})
    with mock.patch.object(client, "_request", return_value=resp):
        assert client.get_branch_head("o", "r", "main") is None


def test_get_branch_head_no_commit_returns_none(client):
    resp = _make_response(status_code=200, json_data={"commit": None})
    with mock.patch.object(client, "_request", return_value=resp):
        assert client.get_branch_head("o", "r", "main") is None


def test_get_branch_head_exception(client):
    with mock.patch.object(client, "_request", side_effect=RuntimeError):
        assert client.get_branch_head("o", "r", "main") is None


def test_get_branch_head_raise_for_status_error(client):
    resp = _make_response(status_code=404)
    with mock.patch.object(client, "_request", return_value=resp):
        assert client.get_branch_head("o", "r", "main") is None


# ---------------------------------------------------------------------------
# has_thumbs_up
# ---------------------------------------------------------------------------
def test_has_thumbs_up_true():
    assert GitHubPRClient.has_thumbs_up([{"content": "-1"}, {"content": "+1"}])


def test_has_thumbs_up_false():
    assert not GitHubPRClient.has_thumbs_up([{"content": "heart"}])


def test_has_thumbs_up_empty():
    assert not GitHubPRClient.has_thumbs_up([])


# ---------------------------------------------------------------------------
# create_and_merge
# ---------------------------------------------------------------------------
def test_create_and_merge(client):
    pr = {"number": 12, "html_url": "https://gh/pr/12"}
    with (
        mock.patch.object(client, "create_pr", return_value=pr) as create,
        mock.patch.object(client, "merge_pr") as merge,
        mock.patch.object(client, "delete_branch") as delete,
    ):
        url = client.create_and_merge("o", "r", head="h", base="b", title="t", body="bod")
    assert url == "https://gh/pr/12"
    create.assert_called_once_with("o", "r", head="h", base="b", title="t", body="bod")
    merge.assert_called_once_with("o", "r", 12, merge_method="squash")
    delete.assert_called_once_with("o", "r", "h")


def test_create_and_merge_custom_method(client):
    pr = {"number": 1, "html_url": "u"}
    with (
        mock.patch.object(client, "create_pr", return_value=pr),
        mock.patch.object(client, "merge_pr") as merge,
        mock.patch.object(client, "delete_branch"),
    ):
        client.create_and_merge("o", "r", head="h", base="b", title="t", merge_method="merge")
    merge.assert_called_once_with("o", "r", 1, merge_method="merge")
