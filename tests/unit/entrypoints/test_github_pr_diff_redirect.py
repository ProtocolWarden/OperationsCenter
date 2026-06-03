# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Regression test: get_pr_diff must follow HTTP redirects.

GitHub renamed repos cause the API to return 301 redirects.
Without follow_redirects=True, the diff endpoint returns an empty
response body, causing the review watcher to skip all PRs ("empty diff").
"""

from unittest.mock import MagicMock, patch

from operations_center.adapters.github_pr import GitHubPRClient


def _make_client() -> GitHubPRClient:
    return GitHubPRClient(token="test-token")


def test_get_pr_diff_follows_redirect() -> None:
    """get_pr_diff passes follow_redirects=True to httpx.get."""
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.text = "diff --git a/foo.py b/foo.py\n+x = 1\n"
    mock_resp.raise_for_status = MagicMock()

    with patch(
        "operations_center.adapters.github_pr.httpx.get", return_value=mock_resp
    ) as mock_get:
        result = client.get_pr_diff("owner", "repo", 42)

    assert result == mock_resp.text
    _, kwargs = mock_get.call_args
    assert kwargs.get("follow_redirects") is True, (
        "get_pr_diff must pass follow_redirects=True — without it, "
        "renamed-repo 301 redirects cause empty diffs and the review watcher skips all PRs"
    )


def test_get_pr_diff_returns_empty_on_error() -> None:
    """get_pr_diff returns empty string on any exception."""
    client = _make_client()
    with patch(
        "operations_center.adapters.github_pr.httpx.get", side_effect=Exception("network error")
    ):
        result = client.get_pr_diff("owner", "repo", 99)
    assert result == ""


def test_get_pr_diff_uses_diff_accept_header() -> None:
    """get_pr_diff sets Accept: application/vnd.github.v3.diff."""
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.text = "diff content"
    mock_resp.raise_for_status = MagicMock()

    with patch(
        "operations_center.adapters.github_pr.httpx.get", return_value=mock_resp
    ) as mock_get:
        client.get_pr_diff("owner", "repo", 1)

    _, kwargs = mock_get.call_args
    headers = kwargs.get("headers", {})
    assert headers.get("Accept") == "application/vnd.github.v3.diff"
