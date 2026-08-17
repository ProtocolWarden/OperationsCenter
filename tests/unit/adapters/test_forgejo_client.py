# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the Forgejo board adapter.

Two of these exist because the spec identified failure modes that pass a naive
test suite:

* **Pagination** — a single-page fixture would let a page-one-only read ship. The
  fleet reasons about absence (it promotes when a queue looks empty), so a short
  read is not an error, it is a confident wrong decision. Every list test uses
  more than one page.
* **State exclusivity** — Plane enforced one state per issue structurally;
  labels are a set. A multi-state issue must raise rather than resolve, because
  the caller is about to dispatch on the value.

No live server: httpx.MockTransport serves recorded shapes.
"""

from __future__ import annotations

import json

import httpx
import pytest

from operations_center.adapters.forgejo.client import (
    KNOWN_STATES,
    STATE_LABEL_PREFIX,
    ForgejoClient,
    MultipleStatesError,
    UnknownStateError,
)

OWNER, REPO = "protocolwarden", "board"
BASE = "https://forge.local"


def _issue(number: int, *, title: str = "t", body: str = "", labels: list[str] | None = None):
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": n} for n in (labels or [])],
    }


def _client(handler) -> ForgejoClient:
    return ForgejoClient(
        BASE, "tok", OWNER, REPO, transport=httpx.MockTransport(handler)
    )


# ── pagination: the failure that looks like success ──────────────────────────


def test_list_issues_reads_every_page():
    """A page-one-only read returns a plausible, successful, wrong board."""
    total = 120  # > 2 full pages at the 50 page size
    seen_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        limit = int(request.url.params.get("limit", 50))
        seen_pages.append(page)
        start = (page - 1) * limit
        batch = [_issue(n) for n in range(start, min(start + limit, total))]
        return httpx.Response(200, json=batch)

    issues = _client(handler).list_issues()

    assert len(issues) == total, (
        f"read {len(issues)} of {total} issues — a truncated board makes the fleet "
        "promote from queues that only look empty"
    )
    assert seen_pages == [1, 2, 3], f"expected three pages, requested {seen_pages}"


def test_pagination_stops_on_a_short_page():
    """A short page means the end; it must not request another."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(int(request.url.params.get("page", 1)))
        return httpx.Response(200, json=[_issue(1), _issue(2)])  # < page size

    _client(handler).list_issues()
    assert calls == [1], f"kept paging past a short page: {calls}"


def test_comments_and_labels_paginate_too():
    """Comment history and the label set are both unbounded."""
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page == 1:
            return httpx.Response(200, json=[{"id": i, "body": "x"} for i in range(50)])
        return httpx.Response(200, json=[{"id": 999, "body": "last"}])

    c = _client(handler)
    assert len(c.list_comments("7")) == 51
    assert len(c.list_labels()) == 51


# ── state exclusivity ────────────────────────────────────────────────────────


def test_single_state_reads_back():
    c = _client(lambda r: httpx.Response(200, json={}))
    issue = _issue(1, labels=[f"{STATE_LABEL_PREFIX}Ready for AI", "source: autonomy"])
    assert c.state_of(issue) == "Ready for AI"


def test_two_state_labels_raise_rather_than_guess():
    """Corruption must surface at the read that would act on it."""
    c = _client(lambda r: httpx.Response(200, json={}))
    issue = _issue(
        42, labels=[f"{STATE_LABEL_PREFIX}Blocked", f"{STATE_LABEL_PREFIX}Ready for AI"]
    )
    with pytest.raises(MultipleStatesError, match="42"):
        c.state_of(issue)


def test_no_state_label_is_unknown_not_an_error():
    """An unstated issue is a board-hygiene problem, not a crash."""
    c = _client(lambda r: httpx.Response(200, json={}))
    assert c.state_of(_issue(1, labels=["source: autonomy"])) == "Unknown"


def test_transition_adds_before_removing():
    """An interrupted transition must leave two states, never zero.

    Two states is loud and recoverable; zero silently drops the task off every
    queue the fleet scans.
    """
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/issues/5"):
            return httpx.Response(200, json=_issue(
                5, labels=[f"{STATE_LABEL_PREFIX}Backlog", "source: autonomy"]
            ))
        if request.method == "GET" and request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": f"{STATE_LABEL_PREFIX}Ready for AI"},
                                             {"name": "source: autonomy"}])
        if request.method == "PUT":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    _client(handler).transition_issue("5", "Ready for AI")

    assert len(sent) == 1, "expected a single label write"
    labels = sent[0]["labels"]
    assert f"{STATE_LABEL_PREFIX}Ready for AI" in labels
    assert f"{STATE_LABEL_PREFIX}Backlog" not in labels, "old state survived"
    assert "source: autonomy" in labels, "fleet labels were clobbered by the transition"


def test_unknown_state_is_refused():
    """Typos must not silently create a state nothing dispatches on."""
    c = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(UnknownStateError):
        c.transition_issue("1", "ready for ai")  # wrong case
    with pytest.raises(UnknownStateError):
        c.create_issue(name="x", description="y", state="Nonsense")


def test_the_state_vocabulary_matches_the_fleet():
    """These six names are what board_unblock and the workers dispatch on."""
    assert KNOWN_STATES == {
        "Backlog", "Ready for AI", "Blocked", "Awaiting Input", "Done", "Cancelled",
    }


# ── label namespace hygiene ──────────────────────────────────────────────────


def test_state_labels_are_hidden_from_the_fleet():
    """Rules parse labels; `state: ` is adapter plumbing and must not leak."""
    c = _client(lambda r: httpx.Response(200, json={}))
    # A real task body: TaskParser requires an Execution section (or a `repo:`
    # label) and will refuse anything else.
    body = (
        "Do the thing.\n"
        "\n"
        "## Execution\n"
        "\n"
        "repo: OperationsCenter\n"
        "base_branch: main\n"
    )
    issue = _issue(
        9,
        body=body,
        labels=[f"{STATE_LABEL_PREFIX}Blocked", "source: autonomy", "task_phase: implement"],
    )
    task = c.to_board_task(issue)
    assert task.status == "Blocked"
    assert "source: autonomy" in task.labels
    assert not [n for n in task.labels if n.startswith(STATE_LABEL_PREFIX)], (
        "state label leaked into the fleet's label vocabulary"
    )


def test_updating_labels_preserves_the_state():
    """Callers pass fleet vocabulary and know nothing about `state: `."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/issues/3"):
            return httpx.Response(200, json=_issue(
                3, labels=[f"{STATE_LABEL_PREFIX}Blocked", "source: autonomy"]
            ))
        if request.method == "GET" and request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": "retry-count: 2"},
                                             {"name": f"{STATE_LABEL_PREFIX}Blocked"}])
        if request.method == "PUT":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    _client(handler).update_issue_labels("3", ["retry-count: 2"])

    labels = sent[0]["labels"]
    assert "retry-count: 2" in labels
    assert f"{STATE_LABEL_PREFIX}Blocked" in labels, (
        "replacing labels unstated the task — it would fall off every queue"
    )


# ── protocol conformance and auth ────────────────────────────────────────────


def test_satisfies_the_board_client_protocol():
    from tests.unit.adapters.test_board_seam import BOARD_OPERATIONS

    missing = sorted(op for op in BOARD_OPERATIONS if not hasattr(ForgejoClient, op))
    assert not missing, f"ForgejoClient does not provide: {missing}"


def test_uses_forgejo_auth_not_planes():
    """Plane's X-API-Key authenticates as nobody here; every call would 401."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=[])

    _client(handler).list_issues()
    assert seen.get("authorization") == "token tok"
    assert "x-api-key" not in seen


def test_retries_a_transient_5xx(monkeypatch):
    """A 503 is retried, not surfaced.

    The backoff is stubbed out: sleeping for real would make this the slowest
    test in the suite to assert something that has nothing to do with timing.
    """
    slept: list[float] = []
    monkeypatch.setattr(
        "operations_center.adapters.forgejo.client.time.sleep", slept.append
    )
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=[])

    _client(handler).list_issues()
    assert attempts["n"] == 2, "did not retry a 503"
    assert slept, "retried without backing off — that is a hot loop against a struggling server"
