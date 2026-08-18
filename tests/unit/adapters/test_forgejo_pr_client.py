# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the Forgejo PR adapter.

The spec's two decisive findings shape this suite:

* **B1 — no Checks API.** ``get_check_runs`` synthesizes runs from statuses
  under a lossy translation, so the loss tests are the point: ``warning`` must
  be distinguishable from ``success`` (neutral, completed, not failed),
  ``error`` must fail while keeping its own name in the text, ``pending`` must
  read as incomplete, and the no-CI-at-all window must leave completed empty.
* **B2 — the fail-closed gate.** ``get_branch_protection`` must produce
  exactly the two paths ``_branch_protection_ok`` reads, from what the
  instance actually said — ``apply_to_admins`` in, ``enforce_admins.enabled``
  out — and a missing rule must be ``None``, which the gate refuses.

No live server: httpx.MockTransport serves the shapes recorded from the live
Forgejo 13 probe (2026-08-18).
"""

from __future__ import annotations

import base64
import json

import httpx

from operations_center.adapters.forgejo.pr_client import (
    STATUS_TO_CHECK,
    ForgejoPRClient,
)
from operations_center.adapters.pr import PRClient

OWNER, REPO = "protocolwarden", "widget"
BASE = "https://forge.local"


def _client(handler) -> ForgejoPRClient:
    return ForgejoPRClient(BASE, "tok", transport=httpx.MockTransport(handler))


def _status(sid: int, context: str, word: str) -> dict:
    return {"id": sid, "context": context, "status": word, "description": "d"}


def _paged(request: httpx.Request, items: list) -> httpx.Response:
    page = int(request.url.params.get("page", "1"))
    limit = int(request.url.params.get("limit", "50"))
    return httpx.Response(200, json=items[(page - 1) * limit : page * limit])


# ── the seam ─────────────────────────────────────────────────────────────────


def test_satisfies_the_pr_protocol():
    """Every operation the reviewer calls exists; a missing one is an
    AttributeError mid-merge, discovered in production."""
    ops = {n for n in dir(PRClient) if not n.startswith("_")}
    missing = sorted(op for op in ops if not hasattr(ForgejoPRClient, op))
    assert not missing, f"ForgejoPRClient lacks: {missing}"


# ── B1: the status→check translation and its chosen losses ───────────────────


def test_translation_covers_every_forgejo_state():
    assert set(STATUS_TO_CHECK) == {"pending", "success", "failure", "error", "warning"}


def test_warning_is_neutral_not_success_and_not_failed():
    """Forgejo's fifth state. Folding it into success would let "completed
    with warnings" masquerade as "passed"; folding it into failure would block
    merges Forgejo itself would not block."""
    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, [_status(1, "lint", "warning")])
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    runs = c.get_check_runs(OWNER, REPO, "abc")
    assert runs[0]["status"] == "completed"
    assert runs[0]["conclusion"] == "neutral"
    assert c.get_failed_checks(OWNER, REPO, 1) == []
    assert c.get_completed_checks(OWNER, REPO, 1) == ["lint"]


def test_error_fails_but_keeps_its_own_name_in_the_text():
    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, [_status(1, "build", "error")])
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    assert c.get_failed_checks(OWNER, REPO, 1) == ["build: error"]


def test_pending_is_incomplete_not_failed_not_completed():
    """Collapsing pending into either terminal bucket re-creates the #503
    failure mode: merging mid-CI."""
    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, [_status(1, "tests", "pending")])
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    assert c.get_incomplete_checks(OWNER, REPO, 1) == ["tests"]
    assert c.get_failed_checks(OWNER, REPO, 1) == []
    assert c.get_completed_checks(OWNER, REPO, 1) == []


def test_no_statuses_at_all_reads_as_nothing_completed():
    """The no-CI-yet window: failed and incomplete are empty, and completed is
    TOO — the gate's "completed must be non-empty" clause is what stops this
    window from reading as green."""
    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, [])
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    assert c.get_failed_checks(OWNER, REPO, 1) == []
    assert c.get_incomplete_checks(OWNER, REPO, 1) == []
    assert c.get_completed_checks(OWNER, REPO, 1) == []


def test_history_dedupes_to_the_latest_status_per_context():
    """The endpoint returns posting history. A context that failed and was
    re-posted green must count once, as green — the same latest-by-id rule the
    GitHub client applies to re-run check suites."""
    history = [
        _status(1, "tests", "failure"),
        _status(2, "tests", "success"),
        _status(3, "lint", "failure"),
    ]

    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, history)
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    assert c.get_failed_checks(OWNER, REPO, 1) == ["lint: failure"]
    assert sorted(c.get_completed_checks(OWNER, REPO, 1)) == ["lint", "tests"]


def test_ignored_checks_filter_matches_github_semantics():
    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, [_status(1, "Flaky suite", "failure"),
                                    _status(2, "real", "failure")])
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    assert c.get_failed_checks(OWNER, REPO, 1, ignored_checks=["flaky"]) == ["real: failure"]


def test_statuses_paginate_to_exhaustion():
    """120 statuses > 2 pages. A page-one-only read hides 70 of them."""
    history = [_status(i, f"ctx-{i}", "success") for i in range(1, 121)]

    def handler(request):
        if "statuses" in request.url.path:
            return _paged(request, history)
        return httpx.Response(200, json={"head": {"sha": "abc"}})

    c = _client(handler)
    assert len(c.get_completed_checks(OWNER, REPO, 1)) == 120


# ── B2: the gate translation ─────────────────────────────────────────────────


def test_branch_protection_translates_to_what_the_gate_reads():
    """apply_to_admins is enforce_admins under Forgejo's name; the raw rule
    rides along so nothing is asserted the instance did not say."""
    rule = {
        "branch_name": "main",
        "enable_status_check": True,
        "status_check_contexts": ["audit", "reviewer-verdict"],
        "apply_to_admins": True,
    }

    def handler(request):
        assert request.url.path.endswith("/branch_protections/main")
        return httpx.Response(200, json=rule)

    got = _client(handler).get_branch_protection(OWNER, REPO, "main")
    assert got["required_status_checks"]["contexts"] == ["audit", "reviewer-verdict"]
    assert got["enforce_admins"]["enabled"] is True
    assert got["_forgejo"] == rule


def test_branch_protection_without_admin_bit_reports_false_not_true():
    """The honest failure the spec demanded: reporting true because protection
    merely exists would remove the control while the logs say it passed."""
    def handler(request):
        return httpx.Response(200, json={
            "enable_status_check": True,
            "status_check_contexts": ["reviewer-verdict"],
            "apply_to_admins": False,
        })

    got = _client(handler).get_branch_protection(OWNER, REPO, "main")
    assert got["enforce_admins"]["enabled"] is False


def test_branch_protection_with_checks_disabled_reports_no_contexts():
    """Contexts listed but enable_status_check off = not required. Reporting
    them as required would satisfy the gate with a rule Forgejo is not
    enforcing."""
    def handler(request):
        return httpx.Response(200, json={
            "enable_status_check": False,
            "status_check_contexts": ["reviewer-verdict"],
            "apply_to_admins": True,
        })

    got = _client(handler).get_branch_protection(OWNER, REPO, "main")
    assert got["required_status_checks"]["contexts"] == []


def test_missing_protection_is_none_which_the_gate_refuses():
    def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    assert _client(handler).get_branch_protection(OWNER, REPO, "main") is None


# ── pull requests ────────────────────────────────────────────────────────────


def test_merge_pr_sends_the_forgejo_do_verb():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/merge"):
            seen.update(json.loads(request.content))
            return httpx.Response(200)
        return httpx.Response(200, json={})

    out = _client(handler).merge_pr(OWNER, REPO, 7, merge_method="squash")
    assert seen == {"Do": "squash"}
    assert out["merged"] is True


def test_find_pr_by_head_filters_client_side_across_pages():
    prs = [{"number": i, "head": {"ref": f"b-{i}"}} for i in range(1, 91)]

    def handler(request):
        return _paged(request, prs)

    c = _client(handler)
    assert c.find_pr_by_head(OWNER, REPO, "b-88")["number"] == 88
    assert c.find_pr_by_head(OWNER, REPO, "nope") is None


def test_create_and_merge_deletes_the_head_branch():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            return httpx.Response(201, json={"number": 9, "html_url": "http://f/pr/9"})
        if request.url.path.endswith("/merge"):
            return httpx.Response(200)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={})

    url = _client(handler).create_and_merge(
        OWNER, REPO, head="feat", base="main", title="t"
    )
    assert url == "http://f/pr/9"
    assert ("DELETE", f"/api/v1/repos/{OWNER}/{REPO}/branches/feat") in calls


def test_changes_requested_accepts_both_forge_dialects():
    def make(states):
        def handler(request):
            if request.url.path.endswith("/reviews"):
                return _paged(request, [{"id": i, "state": s} for i, s in enumerate(states)])
            return httpx.Response(200, json=[])
        return handler

    assert _client(make(["APPROVED", "REQUEST_CHANGES"])).pr_has_changes_requested(OWNER, REPO, 1)
    assert _client(make(["CHANGES_REQUESTED"])).pr_has_changes_requested(OWNER, REPO, 1)
    assert not _client(make(["APPROVED", "COMMENT"])).pr_has_changes_requested(OWNER, REPO, 1)


# ── files and branches ───────────────────────────────────────────────────────


def test_file_content_round_trip_and_404():
    payload = {"content": base64.b64encode(b"hello").decode(), "sha": "blob1"}

    def handler(request):
        if "missing" in request.url.path:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=payload)

    c = _client(handler)
    assert c.get_file_content(OWNER, REPO, "a.txt", "main") == ("hello", "blob1")
    assert c.get_file_content(OWNER, REPO, "missing.txt", "main") is None


def test_update_file_sends_base64_and_blob_sha():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={})

    ok = _client(handler).update_file(
        OWNER, REPO, "a.txt", new_text="hi", message="m", branch="b", blob_sha="s1"
    )
    assert ok
    assert base64.b64decode(seen["content"]).decode() == "hi"
    assert seen["sha"] == "s1"


def test_branch_head_and_missing_branch():
    def handler(request):
        if "gone" in request.url.path:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"commit": {"id": "abc123"}})

    c = _client(handler)
    assert c.get_branch_head(OWNER, REPO, "main") == "abc123"
    assert c.get_branch_head(OWNER, REPO, "gone") is None


def test_review_comments_flatten_across_reviews():
    def handler(request):
        p = request.url.path
        if p.endswith("/reviews"):
            return _paged(request, [{"id": 1}, {"id": 2}])
        if "/reviews/1/comments" in p:
            return _paged(request, [{"body": "r1c1"}])
        if "/reviews/2/comments" in p:
            return _paged(request, [{"body": "r2c1"}, {"body": "r2c2"}])
        return httpx.Response(200, json=[])

    got = _client(handler).list_pr_review_comments(OWNER, REPO, 5)
    assert [c["body"] for c in got] == ["r1c1", "r2c1", "r2c2"]
