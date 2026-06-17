# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from unittest.mock import MagicMock

from operations_center.spec_author.spec_author_task import (
    INITIAL_STATE,
    LABEL_SOURCE,
    LABEL_TASK_KIND,
    SpecAuthorPayload,
    create_spec_author_task,
    find_in_flight_phase_advance,
    render_task_body,
)


def _make_payload(**kwargs) -> SpecAuthorPayload:
    base = {
        "spec_slug": "my-spec",
        "trigger_source": "spec-director",
        "target_path": "specs/my-spec.md",
    }
    base.update(kwargs)
    return SpecAuthorPayload(**base)


# --- SpecAuthorPayload defaults ---


def test_payload_defaults():
    p = _make_payload()
    assert p.seed_text == ""
    assert p.task_phase == ""
    assert p.recent_git_log_repos == {}
    assert p.existing_specs == []
    assert p.ready_count == 0
    assert p.running_count == 0
    assert p.drained is False


def test_payload_default_collections_are_independent():
    a = _make_payload()
    b = _make_payload()
    a.recent_git_log_repos["x"] = "y"
    a.existing_specs.append("z")
    assert b.recent_git_log_repos == {}
    assert b.existing_specs == []


# --- render_task_body ---


def test_render_body_minimal_uses_empty_placeholders():
    body = render_task_body(_make_payload())
    assert "## Spec Authoring" in body
    assert "task-kind: spec-author" in body
    assert "source: spec-director" in body
    assert "spec_slug: my-spec" in body
    assert "trigger_source: spec-director" in body
    assert "target_path: specs/my-spec.md" in body
    # empty placeholders for the optional blocks
    assert "    {}" in body  # recent_git_log_repos empty
    assert "    []" in body  # existing_specs empty
    assert "seed_text: |\n  ''" in body
    # no task_phase line when unset
    assert "task_phase:" not in body
    assert "ready: 0" in body
    assert "running: 0" in body
    assert "drained: false" in body


def test_render_body_includes_task_phase_line_when_set():
    body = render_task_body(_make_payload(task_phase="design"))
    assert "task_phase: design\n" in body


def test_render_body_single_line_seed_text():
    body = render_task_body(_make_payload(seed_text="hello world"))
    assert "seed_text: |\n  hello world" in body


def test_render_body_multiline_seed_text_is_indented():
    body = render_task_body(_make_payload(seed_text="line1\nline2"))
    # each newline gets two-space continuation indent
    assert "seed_text: |\n  line1\n  line2" in body


def test_render_body_existing_specs_rendered_as_list():
    body = render_task_body(_make_payload(existing_specs=["alpha", "beta"]))
    assert "    - alpha" in body
    assert "    - beta" in body
    assert "    []" not in body


def test_render_body_git_log_single_repo_single_line():
    body = render_task_body(_make_payload(recent_git_log_repos={"repoA": "commit1"}))
    assert "    repoA: |\n      commit1" in body
    # placeholder for empty git block should not appear
    assert "recent_git_log_repos:\n    {}" not in body


def test_render_body_git_log_multiline_is_indented():
    body = render_task_body(_make_payload(recent_git_log_repos={"repoA": "c1\nc2"}))
    assert "    repoA: |\n      c1\n      c2" in body


def test_render_body_git_log_multiple_repos():
    body = render_task_body(_make_payload(recent_git_log_repos={"r1": "a", "r2": "b"}))
    assert "    r1: |\n      a" in body
    assert "    r2: |\n      b" in body


def test_render_body_drained_true_lowercased():
    body = render_task_body(_make_payload(drained=True))
    assert "drained: true" in body


def test_render_body_counts_reflected():
    body = render_task_body(_make_payload(ready_count=3, running_count=5))
    assert "ready: 3" in body
    assert "running: 5" in body


# --- create_spec_author_task ---


def test_create_task_default_title_and_labels():
    client = MagicMock()
    client.create_issue.return_value = {"id": 42}
    result = create_spec_author_task(client, _make_payload())
    assert result == "42"
    client.create_issue.assert_called_once()
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["name"] == "[Spec] my-spec"
    assert kwargs["state"] == INITIAL_STATE
    assert LABEL_TASK_KIND in kwargs["label_names"]
    assert LABEL_SOURCE in kwargs["label_names"]
    assert "trigger: spec-director" in kwargs["label_names"]
    assert "spec-slug: my-spec" in kwargs["label_names"]
    # no task-phase label when unset
    assert all(not lbl.startswith("task-phase:") for lbl in kwargs["label_names"])
    assert "## Spec Authoring" in kwargs["description"]


def test_create_task_phase_title_and_label():
    client = MagicMock()
    client.create_issue.return_value = {"id": "abc-123"}
    result = create_spec_author_task(client, _make_payload(task_phase="design"))
    assert result == "abc-123"
    kwargs = client.create_issue.call_args.kwargs
    assert kwargs["name"] == "[Spec:design] my-spec"
    assert "task-phase: design" in kwargs["label_names"]


def test_create_task_returns_id_as_string():
    client = MagicMock()
    client.create_issue.return_value = {"id": 7}
    result = create_spec_author_task(client, _make_payload())
    assert isinstance(result, str)
    assert result == "7"


# --- find_in_flight_phase_advance ---


def _match_issue(slug="my-spec", phase="design", state="In Progress", issue_id="i1"):
    return {
        "id": issue_id,
        "state": {"name": state},
        "labels": [
            {"name": LABEL_SOURCE},
            {"name": LABEL_TASK_KIND},
            {"name": f"spec-slug: {slug}"},
            {"name": f"task-phase: {phase}"},
        ],
    }


def test_find_returns_matching_issue_id():
    issues = [_match_issue(issue_id="match-1")]
    assert find_in_flight_phase_advance(issues, "my-spec", "design") == "match-1"


def test_find_skips_done_state():
    issues = [_match_issue(state="Done")]
    assert find_in_flight_phase_advance(issues, "my-spec", "design") is None


def test_find_skips_done_case_insensitive():
    issues = [_match_issue(state="DONE")]
    assert find_in_flight_phase_advance(issues, "my-spec", "design") is None


def test_find_no_match_when_slug_differs():
    issues = [_match_issue(slug="other-spec")]
    assert find_in_flight_phase_advance(issues, "my-spec", "design") is None


def test_find_no_match_when_phase_differs():
    issues = [_match_issue(phase="impl")]
    assert find_in_flight_phase_advance(issues, "my-spec", "design") is None


def test_find_empty_issues_returns_none():
    assert find_in_flight_phase_advance([], "my-spec", "design") is None


def test_find_handles_string_labels():
    issue = {
        "id": "str-1",
        "state": {"name": "Backlog"},
        "labels": [
            LABEL_SOURCE,
            LABEL_TASK_KIND,
            "spec-slug: my-spec",
            "task-phase: design",
        ],
    }
    assert find_in_flight_phase_advance([issue], "my-spec", "design") == "str-1"


def test_find_handles_missing_state_key():
    issue = _match_issue()
    del issue["state"]
    assert find_in_flight_phase_advance([issue], "my-spec", "design") == "i1"


def test_find_handles_none_state():
    issue = _match_issue()
    issue["state"] = None
    assert find_in_flight_phase_advance([issue], "my-spec", "design") == "i1"


def test_find_handles_none_labels():
    issue = {"id": "x", "state": {"name": "Open"}, "labels": None}
    assert find_in_flight_phase_advance([issue], "my-spec", "design") is None


def test_find_handles_missing_labels_key():
    issue = {"id": "x", "state": {"name": "Open"}}
    assert find_in_flight_phase_advance([issue], "my-spec", "design") is None


def test_find_missing_id_returns_empty_string():
    issue = _match_issue()
    del issue["id"]
    assert find_in_flight_phase_advance([issue], "my-spec", "design") == ""


def test_find_partial_label_set_no_match():
    # missing the kind label
    issue = {
        "id": "p1",
        "state": {"name": "Open"},
        "labels": [
            {"name": LABEL_SOURCE},
            {"name": "spec-slug: my-spec"},
            {"name": "task-phase: design"},
        ],
    }
    assert find_in_flight_phase_advance([issue], "my-spec", "design") is None


def test_find_first_match_among_multiple():
    issues = [
        _match_issue(state="Done", issue_id="done-one"),
        _match_issue(issue_id="live-one"),
    ]
    assert find_in_flight_phase_advance(issues, "my-spec", "design") == "live-one"


def test_find_label_dict_missing_name_key():
    # label dict without "name" -> treated as empty, no match
    issue = {
        "id": "n1",
        "state": {"name": "Open"},
        "labels": [{}, {}, {}, {}],
    }
    assert find_in_flight_phase_advance([issue], "my-spec", "design") is None
