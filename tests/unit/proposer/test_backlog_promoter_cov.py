# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime

from operations_center.proposer.backlog_promoter import (
    BacklogPromoteResult,
    BacklogPromoterService,
    PromotedTask,
    SkippedTask,
    _family_from_labels,
    _issue_label_names,
    _issue_state_name,
    _parse_recorded_tier,
    _parse_source_family,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class FakeClient:
    """Hermetic stand-in for PlaneClientProtocol."""

    def __init__(self, issues=None, list_exc=None, transition_exc=None):
        self._issues = issues or []
        self._list_exc = list_exc
        self._transition_exc = transition_exc
        self.list_calls = 0
        self.transitions: list[tuple[str, str]] = []

    def list_issues(self):
        self.list_calls += 1
        if self._list_exc is not None:
            raise self._list_exc
        return self._issues

    def transition_issue(self, task_id, state):
        if self._transition_exc is not None:
            raise self._transition_exc
        self.transitions.append((task_id, state))


def _make_issue(
    *,
    id="T-1",
    name="A task",
    state="Backlog",
    labels=("source: autonomy",),
    description="source_family: alpha\nautonomy_tier: 1",
    description_stripped=None,
):
    issue = {
        "id": id,
        "name": name,
        "state": state,
        "labels": list(labels),
    }
    if description is not None:
        issue["description"] = description
    if description_stripped is not None:
        issue["description_stripped"] = description_stripped
    return issue


def _tier_lookup(mapping, default=0):
    return lambda family: mapping.get(family, default)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_parse_source_family_found():
    assert _parse_source_family("foo\nsource_family: alpha\nbar") == "alpha"


def test_parse_source_family_strips_trailing():
    # \S+ stops at whitespace; strip() is a no-op safety net.
    assert _parse_source_family("source_family:   beta") == "beta"


def test_parse_source_family_missing():
    assert _parse_source_family("nothing here") is None


def test_family_from_labels_found():
    assert _family_from_labels(["Source-Family: Gamma"]) == "gamma"


def test_family_from_labels_none():
    assert _family_from_labels(["unrelated", "source: autonomy"]) is None


def test_family_from_labels_empty():
    assert _family_from_labels([]) is None


def test_parse_recorded_tier_found():
    assert _parse_recorded_tier("autonomy_tier: 3") == 3


def test_parse_recorded_tier_missing():
    assert _parse_recorded_tier("no tier here") is None


def test_issue_state_name_dict():
    assert _issue_state_name({"state": {"name": "Ready"}}) == "Ready"


def test_issue_state_name_dict_missing_name():
    assert _issue_state_name({"state": {}}) == ""


def test_issue_state_name_string():
    assert _issue_state_name({"state": "Backlog"}) == "Backlog"


def test_issue_state_name_none():
    assert _issue_state_name({}) == ""


def test_issue_label_names_mixed():
    # int 123 is neither dict nor str, so it is dropped entirely.
    issue = {"labels": [{"name": "a"}, "b", {"other": "x"}, 123]}
    assert _issue_label_names(issue) == ["a", "b", ""]


def test_issue_label_names_default_empty():
    assert _issue_label_names({}) == []


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_promote_count_property():
    res = BacklogPromoteResult(generated_at=datetime.now(UTC), dry_run=True)
    assert res.promote_count == 0
    res.promoted.append(
        PromotedTask(task_id="x", title="t", family="f", current_tier=2, recorded_tier=1)
    )
    assert res.promote_count == 1


def test_skipped_task_defaults():
    st = SkippedTask(task_id="x", title="t", reason="r")
    assert st.family is None
    assert st.current_tier is None


# ---------------------------------------------------------------------------
# Service: list_issues error path
# ---------------------------------------------------------------------------


def test_list_issues_failure_records_error():
    client = FakeClient(list_exc=RuntimeError("boom"))
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({}))
    res = svc.promote()
    assert res.promoted == []
    assert len(res.errors) == 1
    assert "Failed to list Plane issues: boom" in res.errors[0]
    assert client.list_calls == 1


def test_issues_passed_in_skips_client_call():
    client = FakeClient(list_exc=RuntimeError("should not be called"))
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    res = svc.promote(issues=[_make_issue()])
    assert client.list_calls == 0
    assert res.promote_count == 1


# ---------------------------------------------------------------------------
# Service: filtering branches (continue without skip record)
# ---------------------------------------------------------------------------


def test_skip_non_backlog_state():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    res = svc.promote(issues=[_make_issue(state="Ready for AI")])
    assert res.promoted == []
    assert res.skipped == []


def test_skip_missing_autonomy_label():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    res = svc.promote(issues=[_make_issue(labels=["something else"])])
    assert res.promoted == []
    assert res.skipped == []


def test_family_filter_excludes_other_family():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    res = svc.promote(issues=[_make_issue()], family_filter="beta")
    assert res.promoted == []
    assert res.skipped == []


def test_family_filter_includes_matching_family():
    client = FakeClient()
    svc = BacklogPromoterService(
        plane_client=client, get_tier=_tier_lookup({"alpha": 2}), dry_run=False
    )
    res = svc.promote(issues=[_make_issue()], family_filter="alpha")
    assert res.promote_count == 1
    assert client.transitions == [("T-1", "Ready for AI")]


# ---------------------------------------------------------------------------
# Service: skip records
# ---------------------------------------------------------------------------


def test_skip_no_source_family():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({}))
    issue = _make_issue(description="no family marker", labels=["source: autonomy"])
    res = svc.promote(issues=[issue])
    assert res.promoted == []
    assert len(res.skipped) == 1
    assert res.skipped[0].reason == "no_source_family_in_provenance"
    assert res.skipped[0].family is None


def test_skip_tier_below_2():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 1}))
    res = svc.promote(issues=[_make_issue()])
    assert res.promoted == []
    assert len(res.skipped) == 1
    assert res.skipped[0].reason == "tier_below_2"
    assert res.skipped[0].family == "alpha"
    assert res.skipped[0].current_tier == 1


def test_family_resolved_from_labels_when_absent_in_description():
    client = FakeClient()
    svc = BacklogPromoterService(
        plane_client=client, get_tier=_tier_lookup({"delta": 2}), dry_run=False
    )
    issue = _make_issue(
        description="no marker here",
        labels=["source: autonomy", "source-family: delta"],
    )
    res = svc.promote(issues=[issue])
    assert res.promote_count == 1
    assert res.promoted[0].family == "delta"


def test_description_stripped_fallback():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    issue = _make_issue(
        description=None,
        description_stripped="source_family: alpha\nautonomy_tier: 2",
    )
    res = svc.promote(issues=[issue])
    assert res.promote_count == 1
    assert res.promoted[0].recorded_tier == 2


# ---------------------------------------------------------------------------
# Service: promotion happy paths
# ---------------------------------------------------------------------------


def test_dry_run_does_not_transition():
    client = FakeClient()
    svc = BacklogPromoterService(
        plane_client=client, get_tier=_tier_lookup({"alpha": 2}), dry_run=True
    )
    res = svc.promote(issues=[_make_issue()])
    assert res.promote_count == 1
    assert res.dry_run is True
    assert client.transitions == []
    p = res.promoted[0]
    assert p.task_id == "T-1"
    assert p.title == "A task"
    assert p.family == "alpha"
    assert p.current_tier == 2
    assert p.recorded_tier == 1


def test_live_run_transitions():
    client = FakeClient()
    svc = BacklogPromoterService(
        plane_client=client, get_tier=_tier_lookup({"alpha": 5}), dry_run=False
    )
    res = svc.promote(issues=[_make_issue()])
    assert res.promote_count == 1
    assert client.transitions == [("T-1", "Ready for AI")]


def test_transition_failure_records_error_and_skips_promote():
    client = FakeClient(transition_exc=ValueError("nope"))
    svc = BacklogPromoterService(
        plane_client=client, get_tier=_tier_lookup({"alpha": 2}), dry_run=False
    )
    res = svc.promote(issues=[_make_issue()])
    assert res.promoted == []
    assert len(res.errors) == 1
    assert "Failed to promote T-1 (A task): nope" in res.errors[0]


def test_state_dict_form_and_label_dict_form():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    issue = {
        "id": "T-9",
        "name": "Dicty",
        "state": {"name": "  BACKLOG  "},
        "labels": [{"name": "Source: Autonomy"}],
        "description": "source_family: alpha",
    }
    res = svc.promote(issues=[issue])
    assert res.promote_count == 1
    assert res.promoted[0].recorded_tier is None


def test_missing_id_and_name_defaults():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({"alpha": 2}))
    issue = {
        "state": "Backlog",
        "labels": ["source: autonomy"],
        "description": "source_family: alpha",
    }
    res = svc.promote(issues=[issue])
    assert res.promote_count == 1
    assert res.promoted[0].task_id == ""
    assert res.promoted[0].title == "Untitled"


def test_multiple_issues_mixed_outcomes():
    issues = [
        _make_issue(id="ok", description="source_family: alpha\nautonomy_tier: 2"),
        _make_issue(id="low", description="source_family: beta", labels=["source: autonomy"]),
        _make_issue(id="nofam", description="nothing"),
        _make_issue(id="notbacklog", state="Done"),
    ]
    client = FakeClient()
    svc = BacklogPromoterService(
        plane_client=client,
        get_tier=_tier_lookup({"alpha": 2, "beta": 1}),
        dry_run=True,
    )
    res = svc.promote(issues=issues)
    assert {p.task_id for p in res.promoted} == {"ok"}
    reasons = {s.task_id: s.reason for s in res.skipped}
    assert reasons == {"low": "tier_below_2", "nofam": "no_source_family_in_provenance"}


def test_generated_at_is_tz_aware():
    client = FakeClient()
    svc = BacklogPromoterService(plane_client=client, get_tier=_tier_lookup({}))
    res = svc.promote(issues=[])
    assert res.generated_at.tzinfo is not None
