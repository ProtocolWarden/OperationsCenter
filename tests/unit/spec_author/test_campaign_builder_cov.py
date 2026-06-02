# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from operations_center.spec_author.campaign_builder import (
    CampaignBuilder,
    ChildTaskSpec,
)

SPEC_HEADER = """---
campaign_id: camp-123
slug: my-feature
phases:
  - implement
  - test
  - improve
repos:
  - acme/repo
area_keywords:
  - api
---
"""


def _spec(body: str = "", *, phases_block: str = SPEC_HEADER) -> str:
    return phases_block + body


def _make_client(ids):
    """Return a client whose create_issue yields the given ids in order."""
    client = MagicMock()
    client.create_issue.side_effect = [{"id": i} for i in ids]
    return client


# --------------------------------------------------------------------------
# _extract_goals
# --------------------------------------------------------------------------


def test_extract_goals_parses_numbered_list():
    text = """## Goals
1. First goal
2. Second goal

## Constraints
none
"""
    goals = CampaignBuilder._extract_goals(text)
    assert goals == ["First goal", "Second goal"]


def test_extract_goals_stops_at_next_section():
    text = """## Goals
1. Only goal
## Other
2. Not a goal
"""
    goals = CampaignBuilder._extract_goals(text)
    assert goals == ["Only goal"]


def test_extract_goals_defaults_when_absent():
    goals = CampaignBuilder._extract_goals("no goals here")
    assert goals == ["Implement the spec as described"]


def test_extract_goals_ignores_non_numbered_lines_in_section():
    text = """## Goals
some intro prose
1. Real goal
- bullet not matched
"""
    goals = CampaignBuilder._extract_goals(text)
    assert goals == ["Real goal"]


def test_extract_goals_case_insensitive_header():
    text = """## GOALS
1. Cased goal
"""
    assert CampaignBuilder._extract_goals(text) == ["Cased goal"]


# --------------------------------------------------------------------------
# _extract_section
# --------------------------------------------------------------------------


def test_extract_section_returns_body():
    text = """## Constraints
must be fast
must be safe

## Goals
1. g
"""
    out = CampaignBuilder._extract_section(text, "Constraints")
    assert out == "must be fast\nmust be safe"


def test_extract_section_missing_returns_empty():
    assert CampaignBuilder._extract_section("nothing", "Constraints") == ""


def test_extract_section_case_insensitive():
    text = """## constraints
lower header
"""
    assert CampaignBuilder._extract_section(text, "Constraints") == "lower header"


# --------------------------------------------------------------------------
# _build_parent_body
# --------------------------------------------------------------------------


def test_build_parent_body_includes_fields_and_preview():
    from operations_center.spec_author.models import SpecFrontMatter

    fm = SpecFrontMatter(campaign_id="cid", slug="my-slug")
    spec_text = "x" * 1000
    body = CampaignBuilder._build_parent_body(fm, spec_text)
    assert "campaign_id: cid" in body
    assert "spec_file: docs/specs/my-slug.md" in body
    assert "status: active" in body
    # Preview truncated to 800 chars + "..."
    assert body.count("x") == 800
    assert body.rstrip().endswith("...")


# --------------------------------------------------------------------------
# _create_child_task
# --------------------------------------------------------------------------


def _fm(phases):
    from operations_center.spec_author.models import SpecFrontMatter

    return SpecFrontMatter(campaign_id="cid", slug="slug", phases=phases)


def test_create_child_task_implement_phase():
    client = _make_client(["t1"])
    cb = CampaignBuilder(client, "proj")
    spec = ChildTaskSpec(
        title="T",
        goal_text="  do thing  ",
        constraints_text="  be careful  ",
        phase="implement",
        spec_coverage_hint="Goal 1",
    )
    task_id = cb._create_child_task(
        fm=_fm(["implement"]),
        repo_key="acme/repo",
        base_branch="main",
        spec=spec,
    )
    assert task_id == "t1"
    _, kwargs = client.create_issue.call_args
    assert kwargs["name"] == "T"
    assert kwargs["state"] == "Ready for AI"
    body = kwargs["description"]
    assert "mode: goal" in body
    assert "repo: acme/repo" in body
    assert "base_branch: main" in body
    assert "do thing" in body
    assert "be careful" in body
    assert "task_phase_note" not in body
    assert "task-kind: goal" in kwargs["label_names"]
    assert "campaign-id: cid" in kwargs["label_names"]


def test_create_child_task_test_phase_note():
    client = _make_client(["t2"])
    cb = CampaignBuilder(client, "proj")
    spec = ChildTaskSpec(
        title="T",
        goal_text="g",
        constraints_text="c",
        phase="test_campaign",
        spec_coverage_hint="Goal 1",
    )
    cb._create_child_task(
        fm=_fm(["test_campaign"]),
        repo_key="r",
        base_branch="main",
        spec=spec,
    )
    _, kwargs = client.create_issue.call_args
    assert kwargs["state"] == "Backlog"
    assert "mode: test_campaign" in kwargs["description"]
    assert "Promoted after implement task merges" in kwargs["description"]
    assert "task-kind: test_campaign" in kwargs["label_names"]


def test_create_child_task_improve_phase_note():
    client = _make_client(["t3"])
    cb = CampaignBuilder(client, "proj")
    spec = ChildTaskSpec(
        title="T",
        goal_text="g",
        constraints_text="c",
        phase="improve_campaign",
        spec_coverage_hint="Goal 1",
    )
    cb._create_child_task(
        fm=_fm(["improve_campaign"]),
        repo_key="r",
        base_branch="main",
        spec=spec,
    )
    _, kwargs = client.create_issue.call_args
    assert kwargs["state"] == "Backlog"
    assert "Promoted after test_campaign passes clean" in kwargs["description"]


# --------------------------------------------------------------------------
# build (integration of the above)
# --------------------------------------------------------------------------


def test_build_happy_path_creates_parent_and_children():
    spec_text = _spec("""## Goals
1. Build the API
2. Add docs

## Constraints
keep it simple
""")
    # parent + 2 goals * 3 phases = 7 issues
    client = _make_client([f"id{i}" for i in range(7)])
    cb = CampaignBuilder(client, "proj", max_tasks=10)
    ids = cb.build(spec_text, repo_key="acme/repo", base_branch="main")

    assert len(ids) == 7
    assert ids[0] == "id0"
    # First call is the parent campaign issue.
    first_call = client.create_issue.call_args_list[0]
    assert first_call.kwargs["name"] == "[Campaign] my-feature"
    assert "source: spec-campaign" in first_call.kwargs["label_names"]
    assert "campaign-id: camp-123" in first_call.kwargs["label_names"]

    # Child titles use phase prefixes (phases normalised: test->test_campaign etc.)
    child_names = [c.kwargs["name"] for c in client.create_issue.call_args_list[1:]]
    assert "[Impl] Build the API" in child_names
    assert "[Test] Build the API" in child_names
    assert "[Improve] Build the API" in child_names


def test_build_respects_max_tasks_outer_break():
    spec_text = _spec("""## Goals
1. Goal one
2. Goal two
3. Goal three
""")
    # max_tasks=2 means after 2 children the outer loop breaks on next goal.
    client = _make_client([f"id{i}" for i in range(10)])
    cb = CampaignBuilder(client, "proj", max_tasks=2)
    ids = cb.build(spec_text, repo_key="r", base_branch="main")
    # parent + 2 children
    assert len(ids) == 3


def test_build_respects_max_tasks_inner_break():
    # Single goal, 3 phases, max_tasks=2 -> inner loop break.
    spec_text = _spec("""## Goals
1. Solo goal
""")
    client = _make_client([f"id{i}" for i in range(10)])
    cb = CampaignBuilder(client, "proj", max_tasks=2)
    ids = cb.build(spec_text, repo_key="r", base_branch="main")
    assert len(ids) == 3  # parent + 2 phase children


def test_build_unknown_phase_uses_phase_as_prefix():
    spec_text = """---
campaign_id: c
slug: s
phases:
  - weird
---
## Goals
1. A goal
"""
    client = _make_client(["p", "c1"])
    cb = CampaignBuilder(client, "proj")
    cb.build(spec_text, repo_key="r", base_branch="main")
    child = client.create_issue.call_args_list[1]
    assert child.kwargs["name"] == "[weird] A goal"


def test_build_defaults_goal_when_none_present():
    spec_text = """---
campaign_id: c
slug: s
phases:
  - implement
---
## Constraints
c
"""
    client = _make_client(["p", "c1"])
    cb = CampaignBuilder(client, "proj")
    cb.build(spec_text, repo_key="r", base_branch="main")
    child = client.create_issue.call_args_list[1]
    assert child.kwargs["name"] == "[Impl] Implement the spec as described"


def test_build_truncates_long_goal_title():
    long_goal = "G" * 100
    spec_text = f"""---
campaign_id: c
slug: s
phases:
  - implement
---
## Goals
1. {long_goal}
"""
    client = _make_client(["p", "c1"])
    cb = CampaignBuilder(client, "proj")
    cb.build(spec_text, repo_key="r", base_branch="main")
    child = client.create_issue.call_args_list[1]
    # Title is "[Impl] " + first 60 chars of goal.
    assert child.kwargs["name"] == "[Impl] " + "G" * 60


def test_build_raises_on_missing_front_matter():
    client = MagicMock()
    cb = CampaignBuilder(client, "proj")
    with pytest.raises(ValueError):
        cb.build("no front matter here", repo_key="r", base_branch="main")
    client.create_issue.assert_not_called()


def test_build_logs_limit_warning(caplog):
    import logging

    spec_text = _spec("""## Goals
1. one
2. two
""")
    client = _make_client([f"id{i}" for i in range(10)])
    cb = CampaignBuilder(client, "proj", max_tasks=1)
    with caplog.at_level(logging.WARNING):
        cb.build(spec_text, repo_key="r", base_branch="main")
    assert any("campaign_task_limit_reached" in r.message for r in caplog.records)


def test_build_logs_created_info(caplog):
    import logging

    spec_text = _spec("""## Goals
1. one
""")
    client = _make_client([f"id{i}" for i in range(10)])
    cb = CampaignBuilder(client, "proj")
    with caplog.at_level(logging.INFO):
        cb.build(spec_text, repo_key="r", base_branch="main")
    assert any("campaign_created" in r.message for r in caplog.records)
