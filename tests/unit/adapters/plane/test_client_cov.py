# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from operations_center.adapters.plane.client import PlaneClient


class FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(
        self,
        *,
        json_data: Any = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}
        self._raise_error = raise_error
        self.raise_called = False

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        self.raise_called = True
        if self._raise_error:
            raise httpx.HTTPStatusError("boom", request=MagicMock(), response=MagicMock())


@pytest.fixture
def client() -> PlaneClient:
    """Construct a PlaneClient with its httpx client replaced by a mock."""
    c = PlaneClient(
        base_url="https://plane.example.com/",
        api_token="tok",
        workspace_slug="ws",
        project_id="proj",
    )
    # Replace the real httpx client so no network or sleeps occur.
    c._client = MagicMock()
    return c


def _make_request_returning(client: PlaneClient, response: FakeResponse) -> list[dict[str, Any]]:
    """Patch ``_request`` to return ``response`` and record calls."""
    calls: list[dict[str, Any]] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return response

    client._request = fake_request  # type: ignore[method-assign]
    return calls


# --------------------------------------------------------------------------
# __init__ / close
# --------------------------------------------------------------------------


def test_init_strips_trailing_slash_and_sets_fields() -> None:
    c = PlaneClient(
        base_url="https://x.test/",
        api_token="tok",
        workspace_slug="ws",
        project_id="proj",
    )
    assert c.base_url == "https://x.test"
    assert c.workspace_slug == "ws"
    assert c.project_id == "proj"
    assert c._states_cache is None
    assert c._labels_cache is None


def test_close_delegates_to_client(client: PlaneClient) -> None:
    result = client.close()
    assert result is None
    assert client._client.close.call_count == 1
    client._client.close.assert_called_once_with()


# --------------------------------------------------------------------------
# fetch_issue / fetch_project
# --------------------------------------------------------------------------


def test_fetch_issue_hydrates_dict(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"id": "1", "labels": [{"name": "a"}]})
    calls = _make_request_returning(client, resp)
    out = client.fetch_issue("T1")
    assert out["id"] == "1"
    assert calls[0]["method"] == "GET"
    assert "work-items/T1/" in calls[0]["url"]
    assert calls[0]["kwargs"]["params"] == {"expand": "state"}
    assert resp.raise_called


def test_fetch_issue_non_dict_payload_returned_as_is(client: PlaneClient) -> None:
    resp = FakeResponse(json_data=["not-a-dict"])
    _make_request_returning(client, resp)
    assert client.fetch_issue("T1") == ["not-a-dict"]


def test_fetch_project(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"name": "p"})
    calls = _make_request_returning(client, resp)
    assert client.fetch_project() == {"name": "p"}
    assert calls[0]["url"].endswith("projects/proj/")


# --------------------------------------------------------------------------
# list_issues
# --------------------------------------------------------------------------


def test_list_issues_list_payload(client: PlaneClient) -> None:
    resp = FakeResponse(json_data=[{"id": "1"}, "skip", {"id": "2"}])
    _make_request_returning(client, resp)
    out = client.list_issues()
    assert [i["id"] for i in out] == ["1", "2"]


def test_list_issues_paginated_dict_payload(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": [{"id": "9"}, 5]})
    _make_request_returning(client, resp)
    out = client.list_issues()
    assert [i["id"] for i in out] == ["9"]


def test_list_issues_unexpected_payload_returns_empty(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"no_results": True})
    _make_request_returning(client, resp)
    assert client.list_issues() == []


def test_list_issues_results_not_a_list(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": "nope"})
    _make_request_returning(client, resp)
    assert client.list_issues() == []


# --------------------------------------------------------------------------
# list_states (with caching)
# --------------------------------------------------------------------------


def test_list_states_list_payload_and_cache(client: PlaneClient) -> None:
    resp = FakeResponse(json_data=[{"id": "s1", "name": "Todo"}, 7])
    calls = _make_request_returning(client, resp)
    out = client.list_states()
    assert out == [{"id": "s1", "name": "Todo"}]
    # Second call uses cache, no new request.
    out2 = client.list_states()
    assert out2 == out
    assert out2 is not out  # returns a copy
    assert len(calls) == 1


def test_list_states_paginated(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": [{"id": "s2"}, "x"]})
    _make_request_returning(client, resp)
    assert client.list_states() == [{"id": "s2"}]


def test_list_states_unexpected_returns_empty_and_no_cache(client: PlaneClient) -> None:
    resp = FakeResponse(json_data="weird")
    _make_request_returning(client, resp)
    assert client.list_states() == []
    assert client._states_cache is None


def test_list_states_dict_without_list_results(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": 123})
    _make_request_returning(client, resp)
    assert client.list_states() == []


# --------------------------------------------------------------------------
# list_labels (caching + force_refresh)
# --------------------------------------------------------------------------


def test_list_labels_list_and_cache_and_force_refresh(client: PlaneClient) -> None:
    resp = FakeResponse(json_data=[{"id": "l1", "name": "bug"}])
    calls = _make_request_returning(client, resp)
    assert client.list_labels() == [{"id": "l1", "name": "bug"}]
    # Cached.
    client.list_labels()
    assert len(calls) == 1
    # force_refresh bypasses cache.
    client.list_labels(force_refresh=True)
    assert len(calls) == 2


def test_list_labels_paginated(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": [{"id": "l2"}, None]})
    _make_request_returning(client, resp)
    assert client.list_labels() == [{"id": "l2"}]


def test_list_labels_unexpected_returns_empty(client: PlaneClient) -> None:
    resp = FakeResponse(json_data=42)
    _make_request_returning(client, resp)
    assert client.list_labels() == []


def test_list_labels_dict_results_not_list(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": {}})
    _make_request_returning(client, resp)
    assert client.list_labels() == []


# --------------------------------------------------------------------------
# list_comments
# --------------------------------------------------------------------------


def test_list_comments_list(client: PlaneClient) -> None:
    resp = FakeResponse(json_data=[{"c": 1}, "x"])
    _make_request_returning(client, resp)
    assert client.list_comments("T1") == [{"c": 1}]


def test_list_comments_paginated(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": [{"c": 2}, 0]})
    _make_request_returning(client, resp)
    assert client.list_comments("T1") == [{"c": 2}]


def test_list_comments_unexpected(client: PlaneClient) -> None:
    resp = FakeResponse(json_data="nope")
    _make_request_returning(client, resp)
    assert client.list_comments("T1") == []


def test_list_comments_dict_results_not_list(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"results": 1})
    _make_request_returning(client, resp)
    assert client.list_comments("T1") == []


# --------------------------------------------------------------------------
# to_board_task
# --------------------------------------------------------------------------


def _exec_description(extra: str = "") -> str:
    return (
        "## Goal\nDo the thing.\n\n## Execution\nrepo: myrepo\nbase_branch: main\n" + extra + "\n"
    )


def test_to_board_task_full(client: PlaneClient) -> None:
    issue = {
        "id": "ID1",
        "project_id": "P9",
        "name": "My Task",
        "description": _exec_description("mode: goal\nopen_pr: true\n"),
        "labels": [{"name": "x"}, {"name": "y"}, "bad"],
        "state": {"name": "Running"},
    }
    bt = client.to_board_task(issue)
    assert bt.task_id == "ID1"
    assert bt.project_id == "P9"
    assert bt.title == "My Task"
    assert bt.status == "Running"
    assert bt.labels == ["x", "y"]
    assert bt.repo_key == "myrepo"
    assert bt.base_branch == "main"
    assert bt.open_pr is True
    assert bt.goal_text == "Do the thing."


def test_to_board_task_defaults_and_state_string(client: PlaneClient) -> None:
    issue = {
        "id": 5,
        "description": _exec_description(),
        "state": "CustomState",
    }
    bt = client.to_board_task(issue)
    assert bt.task_id == "5"
    assert bt.project_id == "proj"  # falls back to client.project_id
    assert bt.title == "Untitled"
    assert bt.status == "CustomState"
    assert bt.allowed_paths == []
    assert bt.validation_profile is None
    assert bt.open_pr is False


def test_to_board_task_state_none_unknown(client: PlaneClient) -> None:
    issue = {"id": "z", "description": _exec_description(), "state": None}
    bt = client.to_board_task(issue)
    assert bt.status == "Unknown"


def test_to_board_task_with_validation_profile_and_paths(client: PlaneClient) -> None:
    desc = _exec_description("validation_profile: strict\nallowed_paths:\n  - a/b\n  - c/d\n")
    issue = {"id": "1", "description": desc, "state": {"name": "Todo"}}
    bt = client.to_board_task(issue)
    assert bt.validation_profile == "strict"
    assert bt.allowed_paths == ["a/b", "c/d"]


# --------------------------------------------------------------------------
# transition_issue
# --------------------------------------------------------------------------


def test_transition_issue_running_sets_start_date(client: PlaneClient) -> None:
    client._states_cache = [{"id": "SID", "name": "Running"}]
    resp = FakeResponse(json_data={})
    calls = _make_request_returning(client, resp)
    client.transition_issue("T1", "Running")
    body = calls[0]["kwargs"]["json"]
    assert body["state"] == "SID"
    assert "start_date" in body
    assert "target_date" not in body
    assert calls[0]["method"] == "PATCH"


@pytest.mark.parametrize("state", ["Done", "Review", "In Review", "Blocked"])
def test_transition_issue_terminal_sets_target_date(client: PlaneClient, state: str) -> None:
    client._states_cache = []  # no resolution -> keeps raw name
    resp = FakeResponse(json_data={})
    calls = _make_request_returning(client, resp)
    client.transition_issue("T1", state)
    body = calls[0]["kwargs"]["json"]
    assert body["state"] == state
    assert "target_date" in body
    assert "start_date" not in body


def test_transition_issue_other_state_no_dates(client: PlaneClient) -> None:
    client._states_cache = []
    resp = FakeResponse(json_data={})
    calls = _make_request_returning(client, resp)
    client.transition_issue("T1", "Todo")
    body = calls[0]["kwargs"]["json"]
    assert body == {"state": "Todo"}


# --------------------------------------------------------------------------
# create_issue
# --------------------------------------------------------------------------


def test_create_issue_minimal(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"id": "new"})
    calls = _make_request_returning(client, resp)
    out = client.create_issue(name="N", description="hello world")
    assert out == {"id": "new"}
    body = calls[0]["kwargs"]["json"]
    assert body["name"] == "N"
    assert body["description_stripped"] == "hello world"
    assert body["description_html"] == "<p>hello world</p>"
    assert "state" not in body
    assert "labels" not in body


def test_create_issue_with_state_and_labels(client: PlaneClient) -> None:
    client._states_cache = [{"id": "SID", "name": "Todo"}]
    client._labels_cache = [{"id": "LID", "name": "bug"}]
    resp = FakeResponse(json_data={"id": "new"})
    calls = _make_request_returning(client, resp)
    client.create_issue(name="N", description="d", state="Todo", label_names=["bug"])
    body = calls[0]["kwargs"]["json"]
    assert body["state"] == "SID"
    assert body["labels"] == ["LID"]


# --------------------------------------------------------------------------
# update_issue_description / update_issue_labels
# --------------------------------------------------------------------------


def test_update_issue_description(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={})
    calls = _make_request_returning(client, resp)
    client.update_issue_description("T1", "para one\n\npara two")
    body = calls[0]["kwargs"]["json"]
    assert body["description_stripped"] == "para one\n\npara two"
    assert body["description_html"] == "<p>para one</p><p>para two</p>"
    assert calls[0]["method"] == "PATCH"


def test_update_issue_labels(client: PlaneClient) -> None:
    client._labels_cache = [{"id": "LID", "name": "bug"}]
    resp = FakeResponse(json_data={})
    calls = _make_request_returning(client, resp)
    client.update_issue_labels("T1", ["bug"])
    assert calls[0]["kwargs"]["json"] == {"labels": ["LID"]}


# --------------------------------------------------------------------------
# comment_issue
# --------------------------------------------------------------------------


def test_comment_issue(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={})
    calls = _make_request_returning(client, resp)
    client.comment_issue("T1", "Summary line\n- item")
    body = calls[0]["kwargs"]["json"]
    assert body["comment_html"] == "<p>Summary line</p><ul><li>item</li></ul>"
    assert calls[0]["method"] == "POST"


# --------------------------------------------------------------------------
# _resolve_state_value
# --------------------------------------------------------------------------


def test_resolve_state_value_match_case_insensitive(client: PlaneClient) -> None:
    client._states_cache = [{"id": "SID", "name": "In Progress"}]
    assert client._resolve_state_value("  in progress ") == "SID"


def test_resolve_state_value_no_match_returns_original(client: PlaneClient) -> None:
    client._states_cache = [{"id": "SID", "name": "Todo"}]
    assert client._resolve_state_value("Nonexistent") == "Nonexistent"


# --------------------------------------------------------------------------
# _ensure_label_ids / _create_label
# --------------------------------------------------------------------------


def test_ensure_label_ids_existing_only(client: PlaneClient) -> None:
    client._labels_cache = [
        {"id": "L1", "name": "Bug"},
        {"id": "L2", "name": "feature"},
        {"id": None, "name": "ignored"},  # filtered out (no id)
        {"id": "L3", "name": ""},  # filtered out (no name)
    ]
    ids = client._ensure_label_ids(["bug", "FEATURE", "  ", ""])
    assert ids == ["L1", "L2"]


def test_ensure_label_ids_creates_missing(client: PlaneClient) -> None:
    client._labels_cache = []
    resp = FakeResponse(json_data={"id": "NEW", "name": "shiny"})
    calls = _make_request_returning(client, resp)
    ids = client._ensure_label_ids(["shiny", "shiny"])
    # Created once, reused on the second occurrence.
    assert ids == ["NEW", "NEW"]
    create_calls = [c for c in calls if c["method"] == "POST"]
    assert len(create_calls) == 1
    assert create_calls[0]["kwargs"]["json"] == {"name": "shiny"}
    # Cache was appended to.
    assert {"id": "NEW", "name": "shiny"} in client._labels_cache


def test_create_label_no_cache_append_when_cache_none(client: PlaneClient) -> None:
    client._labels_cache = None
    resp = FakeResponse(json_data={"id": "NEW", "name": "x"})
    _make_request_returning(client, resp)
    out = client._create_label("x")
    assert out == {"id": "NEW", "name": "x"}
    assert client._labels_cache is None


def test_create_label_non_dict_response_not_appended(client: PlaneClient) -> None:
    client._labels_cache = []
    resp = FakeResponse(json_data="not-a-dict")
    _make_request_returning(client, resp)
    out = client._create_label("x")
    assert out == "not-a-dict"
    assert client._labels_cache == []


# --------------------------------------------------------------------------
# _hydrate_issue_labels
# --------------------------------------------------------------------------


def test_hydrate_labels_empty_or_missing(client: PlaneClient) -> None:
    assert client._hydrate_issue_labels({"labels": []}) == {"labels": []}
    assert client._hydrate_issue_labels({"labels": "x"}) == {"labels": "x"}
    assert client._hydrate_issue_labels({}) == {}


def test_hydrate_labels_already_dicts(client: PlaneClient) -> None:
    issue = {"labels": [{"id": "1"}, {"id": "2"}]}
    assert client._hydrate_issue_labels(issue) is issue


def test_hydrate_labels_resolves_ids_from_cache(client: PlaneClient) -> None:
    client._labels_cache = [{"id": "L1", "name": "bug"}, {"id": "L2", "name": "feat"}]
    issue = {"labels": ["L1", "L2"]}
    out = client._hydrate_issue_labels(issue)
    assert out["labels"] == [
        {"id": "L1", "name": "bug"},
        {"id": "L2", "name": "feat"},
    ]


def test_hydrate_labels_force_refresh_for_unresolved(client: PlaneClient) -> None:
    # First label_map call (cache) misses L9 -> triggers force_refresh fetch.
    client._labels_cache = [{"id": "L1", "name": "bug"}]
    resp = FakeResponse(json_data=[{"id": "L1", "name": "bug"}, {"id": "L9", "name": "new"}])
    _make_request_returning(client, resp)
    issue = {"labels": ["L1", "L9", "missing"]}
    out = client._hydrate_issue_labels(issue)
    assert out["labels"][0] == {"id": "L1", "name": "bug"}
    assert out["labels"][1] == {"id": "L9", "name": "new"}
    # Still unresolved -> raw kept.
    assert out["labels"][2] == "missing"


def test_hydrate_labels_mixed_dict_and_id(client: PlaneClient) -> None:
    client._labels_cache = [{"id": "L1", "name": "bug"}]
    issue = {"labels": [{"id": "X", "name": "inline"}, "L1"]}
    out = client._hydrate_issue_labels(issue)
    assert out["labels"][0] == {"id": "X", "name": "inline"}
    assert out["labels"][1] == {"id": "L1", "name": "bug"}


# --------------------------------------------------------------------------
# _request — retry logic
# --------------------------------------------------------------------------


def _real_request_client() -> PlaneClient:
    c = PlaneClient(
        base_url="https://plane.example.com",
        api_token="tok",
        workspace_slug="ws",
        project_id="proj",
    )
    c._client = MagicMock()
    return c


def test_request_success_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _real_request_client()
    ok = MagicMock(status_code=200)
    c._client.request.return_value = ok
    slept: list[float] = []
    monkeypatch.setattr(
        "operations_center.adapters.plane.client.time.sleep", lambda s: slept.append(s)
    )
    assert c._request("GET", "/x") is ok
    assert slept == []
    c._client.request.assert_called_once()


def test_request_retries_on_connect_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _real_request_client()
    ok = MagicMock(status_code=200)
    c._client.request.side_effect = [httpx.ConnectError("nope"), ok]
    slept: list[float] = []
    monkeypatch.setattr(
        "operations_center.adapters.plane.client.time.sleep", lambda s: slept.append(s)
    )
    assert c._request("GET", "/x") is ok
    assert slept == [2]  # attempt 1 backoff


def test_request_connect_error_exhausts_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _real_request_client()
    c._client.request.side_effect = httpx.TimeoutException("timeout")
    monkeypatch.setattr("operations_center.adapters.plane.client.time.sleep", lambda s: None)
    with pytest.raises(httpx.TimeoutException):
        c._request("GET", "/x")
    assert c._client.request.call_count == 4


def test_request_429_with_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _real_request_client()
    r429 = MagicMock(status_code=429, headers={"Retry-After": "7"})
    ok = MagicMock(status_code=200)
    c._client.request.side_effect = [r429, ok]
    slept: list[float] = []
    monkeypatch.setattr(
        "operations_center.adapters.plane.client.time.sleep", lambda s: slept.append(s)
    )
    assert c._request("GET", "/x") is ok
    assert slept == [7]


def test_request_429_non_numeric_retry_after_uses_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _real_request_client()
    r429 = MagicMock(status_code=429, headers={"Retry-After": "soon"})
    ok = MagicMock(status_code=200)
    c._client.request.side_effect = [r429, ok]
    slept: list[float] = []
    monkeypatch.setattr(
        "operations_center.adapters.plane.client.time.sleep", lambda s: slept.append(s)
    )
    assert c._request("GET", "/x") is ok
    assert slept == [2]  # attempt 1 * 2


def test_request_429_exhausts_returns_last_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _real_request_client()
    r429 = MagicMock(status_code=429, headers={})
    c._client.request.return_value = r429
    monkeypatch.setattr("operations_center.adapters.plane.client.time.sleep", lambda s: None)
    assert c._request("GET", "/x") is r429
    assert c._client.request.call_count == 4


def test_request_5xx_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _real_request_client()
    r503 = MagicMock(status_code=503, headers={})
    ok = MagicMock(status_code=200)
    c._client.request.side_effect = [r503, ok]
    slept: list[float] = []
    monkeypatch.setattr(
        "operations_center.adapters.plane.client.time.sleep", lambda s: slept.append(s)
    )
    assert c._request("GET", "/x") is ok
    assert slept == [2]


def test_request_5xx_exhausts_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _real_request_client()
    r502 = MagicMock(status_code=502, headers={})
    c._client.request.return_value = r502
    monkeypatch.setattr("operations_center.adapters.plane.client.time.sleep", lambda s: None)
    # On the final attempt, the 5xx branch is skipped and the response returns.
    assert c._request("GET", "/x") is r502
    assert c._client.request.call_count == 4


def test_request_non_retryable_4xx_returned_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _real_request_client()
    r404 = MagicMock(status_code=404, headers={})
    c._client.request.return_value = r404
    monkeypatch.setattr("operations_center.adapters.plane.client.time.sleep", lambda s: None)
    assert c._request("GET", "/x") is r404
    c._client.request.assert_called_once()


# --------------------------------------------------------------------------
# _render_comment_html
# --------------------------------------------------------------------------


def test_render_comment_html_empty() -> None:
    assert PlaneClient._render_comment_html("   \n  ") == "<p>(no summary)</p>"


def test_render_comment_html_header_only() -> None:
    assert PlaneClient._render_comment_html("Just a header") == "<p>Just a header</p>"


def test_render_comment_html_with_items_and_escaping() -> None:
    out = PlaneClient._render_comment_html("Head <x>\n- a & b\n  ignored\n- c")
    assert out == "<p>Head &lt;x&gt;</p><ul><li>a &amp; b</li><li>c</li></ul>"


# --------------------------------------------------------------------------
# _render_text_html
# --------------------------------------------------------------------------


def test_render_text_html_empty() -> None:
    assert PlaneClient._render_text_html("   ") == "<p></p>"


def test_render_text_html_multiline_blocks() -> None:
    out = PlaneClient._render_text_html("line1\nline<2>\n\nblock2")
    assert out == "<p>line1<br/>line&lt;2&gt;</p><p>block2</p>"


# --------------------------------------------------------------------------
# _issue_description_text
# --------------------------------------------------------------------------


def test_issue_description_text_prefers_description() -> None:
    assert PlaneClient._issue_description_text({"description": "raw"}) == "raw"


def test_issue_description_text_uses_stripped() -> None:
    assert PlaneClient._issue_description_text({"description_stripped": "stripped"}) == "stripped"


def test_issue_description_text_falls_back_to_html() -> None:
    out = PlaneClient._issue_description_text(
        {"description": "  ", "description_html": "<p>hi</p>"}
    )
    assert out == "hi"


def test_issue_description_text_empty() -> None:
    assert PlaneClient._issue_description_text({}) == ""
    assert PlaneClient._issue_description_text({"description_html": "   "}) == ""


# --------------------------------------------------------------------------
# _html_to_task_text
# --------------------------------------------------------------------------


def test_html_to_task_text_headings_lists_breaks() -> None:
    html_body = (
        "<h2>Title</h2><ul><li>one</li><li>two</li></ul><p>first<br/>second</p><div>boxed</div>"
    )
    out = PlaneClient._html_to_task_text(html_body)
    assert "## Title" in out
    assert "- one" in out
    assert "- two" in out
    assert "first" in out
    assert "second" in out
    assert "boxed" in out
    assert "<" not in out  # all tags stripped


def test_html_to_task_text_unescapes_entities() -> None:
    out = PlaneClient._html_to_task_text("<p>a &amp; b</p>")
    assert out == "a & b"


def test_html_to_task_text_collapses_blank_lines() -> None:
    out = PlaneClient._html_to_task_text("<p>a</p><p></p><p></p><p>b</p>")
    assert "\n\n\n" not in out


# --------------------------------------------------------------------------
# raise_for_status propagation
# --------------------------------------------------------------------------


def test_fetch_issue_raises_on_http_error(client: PlaneClient) -> None:
    resp = FakeResponse(json_data={"id": "1"}, raise_error=True)
    _make_request_returning(client, resp)
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch_issue("T1")
