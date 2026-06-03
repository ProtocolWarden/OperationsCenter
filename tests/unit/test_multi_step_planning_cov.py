# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import pytest

from operations_center.multi_step_planning import (
    MultiStepPlan,
    _MULTI_STEP_LABEL,
    _MULTI_STEP_TITLE_KEYWORDS,
    _is_multi_step_task,
    _requeue_as_goal,
    _score_proposal_utility,
    build_multi_step_plan,
)


# --------------------------------------------------------------------------
# _is_multi_step_task
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kw", _MULTI_STEP_TITLE_KEYWORDS)
def test_title_keyword_triggers(kw):
    assert _is_multi_step_task(f"Please {kw} the auth layer", None) is True


def test_title_keyword_case_insensitive():
    assert _is_multi_step_task("REFACTOR everything", None) is True


def test_title_no_keyword_returns_false():
    assert _is_multi_step_task("Add a small button", None) is False


def test_empty_title_no_labels_returns_false():
    assert _is_multi_step_task("", None) is False


def test_none_title_no_labels_returns_false():
    assert _is_multi_step_task(None, None) is False


def test_explicit_string_label_triggers():
    assert _is_multi_step_task("trivial", [_MULTI_STEP_LABEL]) is True


def test_explicit_label_with_whitespace_and_case():
    assert _is_multi_step_task("trivial", ["  Plan: Multi-Step  "]) is True


def test_dict_label_triggers():
    assert _is_multi_step_task("trivial", [{"name": "plan: multi-step"}]) is True


def test_dict_label_missing_name_does_not_trigger():
    assert _is_multi_step_task("trivial", [{"id": 5}]) is False


def test_non_str_non_dict_label_is_ignored():
    # An int label normalizes to "" and must not raise.
    assert _is_multi_step_task("trivial", [123]) is False


def test_label_present_short_circuits_before_title_check():
    # Even with a None title, a matching label returns True.
    assert _is_multi_step_task(None, [_MULTI_STEP_LABEL]) is True


def test_labels_without_match_falls_through_to_title():
    assert _is_multi_step_task("redesign module", ["unrelated"]) is True
    assert _is_multi_step_task("plain title", ["unrelated"]) is False


# --------------------------------------------------------------------------
# build_multi_step_plan
# --------------------------------------------------------------------------


def test_build_plan_returns_three_ordered_steps():
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title="Big change",
        parent_goal="do the thing",
        repo_key="myrepo",
    )
    assert isinstance(plan, MultiStepPlan)
    assert plan.parent_id == "P1"
    assert plan.parent_title == "Big change"
    assert len(plan.steps) == 3
    assert [s["step"] for s in plan.steps] == [1, 2, 3]


def test_build_plan_kinds_and_deps():
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title="Big change",
        parent_goal="do the thing",
        repo_key="myrepo",
    )
    assert [s["kind"] for s in plan.steps] == ["goal", "goal", "test"]
    # depends_on is left empty for the caller to wire up.
    assert all(s["depends_on"] == [] for s in plan.steps)


def test_build_plan_titles_include_repo_prefix_and_step_marker():
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title="Big change",
        parent_goal="do the thing",
        repo_key="myrepo",
    )
    assert plan.steps[0]["title"].startswith("[Step 1/3: Analyze] [myrepo] ")
    assert plan.steps[1]["title"].startswith("[Step 2/3: Implement] [myrepo] ")
    assert plan.steps[2]["title"].startswith("[Step 3/3: Verify] [myrepo] ")


def test_build_plan_empty_repo_key_omits_prefix():
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title="Big change",
        parent_goal="do the thing",
        repo_key="",
    )
    assert plan.steps[0]["title"] == "[Step 1/3: Analyze] Big change"
    # Goal still references the (empty) repo key line.
    assert "Repo: \n" in plan.steps[0]["goal"]


def test_build_plan_titles_truncated_to_80_chars():
    long_title = "X" * 200
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title=long_title,
        parent_goal="g",
        repo_key="r",
    )
    for s in plan.steps:
        assert len(s["title"]) <= 80


def test_build_plan_goal_text_contains_parent_goal_and_repo():
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title="T",
        parent_goal="GOALTEXT",
        repo_key="REPO",
    )
    for s in plan.steps:
        assert "GOALTEXT" in s["goal"]
        assert "Repo: REPO" in s["goal"]


def test_build_plan_is_frozen_dataclass():
    plan = build_multi_step_plan(
        parent_id="P1",
        parent_title="T",
        parent_goal="g",
        repo_key="r",
    )
    with pytest.raises(Exception):
        plan.parent_id = "other"  # type: ignore[misc]


# --------------------------------------------------------------------------
# _score_proposal_utility
# --------------------------------------------------------------------------


def test_score_all_max_clamped_to_one_weighted():
    # acc=1, rec=1 (0h), pri=1 (10) -> 0.5 + 0.3 + 0.2 == 1.0
    assert (
        _score_proposal_utility(
            family_acceptance_rate=1.0,
            family_recency_hours=0.0,
            repo_priority=10,
        )
        == 1.0
    )


def test_score_all_zero():
    # acc=0, rec=1.0 at 0h... use stale recency to drive rec to 0
    assert (
        _score_proposal_utility(
            family_acceptance_rate=0.0,
            family_recency_hours=168.0,
            repo_priority=0,
        )
        == 0.0
    )


def test_score_acceptance_above_one_is_clamped():
    s = _score_proposal_utility(
        family_acceptance_rate=5.0,
        family_recency_hours=168.0,
        repo_priority=0,
    )
    assert s == round(1.0 * 0.5, 3)


def test_score_negative_acceptance_clamped_to_zero():
    s = _score_proposal_utility(
        family_acceptance_rate=-3.0,
        family_recency_hours=168.0,
        repo_priority=0,
    )
    assert s == 0.0


def test_score_recency_beyond_week_clamped_to_zero():
    s = _score_proposal_utility(
        family_acceptance_rate=0.0,
        family_recency_hours=10_000.0,
        repo_priority=0,
    )
    assert s == 0.0


def test_score_recency_negative_clamped_to_one():
    # Negative hours -> 1 - (neg/168) > 1 -> clamped to 1 -> rec weight 0.3
    s = _score_proposal_utility(
        family_acceptance_rate=0.0,
        family_recency_hours=-50.0,
        repo_priority=0,
    )
    assert s == 0.3


def test_score_repo_priority_above_ten_clamped():
    s = _score_proposal_utility(
        family_acceptance_rate=0.0,
        family_recency_hours=168.0,
        repo_priority=100,
    )
    assert s == 0.2


def test_score_default_repo_priority_is_zero():
    explicit = _score_proposal_utility(
        family_acceptance_rate=0.4,
        family_recency_hours=84.0,
        repo_priority=0,
    )
    defaulted = _score_proposal_utility(
        family_acceptance_rate=0.4,
        family_recency_hours=84.0,
    )
    assert explicit == defaulted


def test_score_is_rounded_to_three_places():
    s = _score_proposal_utility(
        family_acceptance_rate=0.3333333,
        family_recency_hours=1.0,
        repo_priority=3,
    )
    # The result should be a float with at most 3 decimal places.
    assert s == round(s, 3)


def test_score_partial_recency_midweek():
    # 84h = half a week -> rec = 0.5 -> 0.5 weight 0.3 = 0.15
    s = _score_proposal_utility(
        family_acceptance_rate=0.0,
        family_recency_hours=84.0,
        repo_priority=0,
    )
    assert s == 0.15


# --------------------------------------------------------------------------
# _requeue_as_goal
# --------------------------------------------------------------------------


def test_requeue_basic_fields():
    spec = _requeue_as_goal({"id": 42, "name": "Fix thing"})
    assert spec["name"] == "[goal] Fix thing"
    assert spec["state"] == "Ready for AI"
    assert "requeued-from: 42" in spec["description"]
    assert "step_failed" in spec["description"]


def test_requeue_custom_reason():
    spec = _requeue_as_goal({"id": 7, "name": "T"}, reason="timeout")
    assert "timeout" in spec["description"]
    assert "handoff-reason: timeout" in spec["label_names"]


def test_requeue_missing_name_defaults_untitled():
    spec = _requeue_as_goal({"id": 1})
    assert spec["name"] == "[goal] Untitled"


def test_requeue_missing_id_yields_empty_id():
    spec = _requeue_as_goal({"name": "X"})
    assert "original-task-id: " in spec["label_names"]
    assert "requeued-from: \n" in spec["description"]


def test_requeue_inherits_source_labels_dict():
    spec = _requeue_as_goal(
        {
            "id": 9,
            "name": "T",
            "labels": [
                {"name": "source: upstream_eval"},
                {"name": "source: board_worker"},
                {"name": "other"},
            ],
        }
    )
    assert "source: upstream_eval" in spec["label_names"]
    # board_worker source is filtered out of inheritance...
    # but the canonical source label is always appended once.
    assert spec["label_names"].count("source: board_worker") == 1


def test_requeue_inherits_source_labels_string():
    spec = _requeue_as_goal({"id": 9, "name": "T", "labels": ["Source: Special", "nope"]})
    assert "source: special" in spec["label_names"]


def test_requeue_none_labels_treated_as_empty():
    spec = _requeue_as_goal({"id": 9, "name": "T", "labels": None})
    assert spec["label_names"][0] == "task-kind: goal"
    assert "source: board_worker" in spec["label_names"]


def test_requeue_always_includes_core_labels():
    spec = _requeue_as_goal({"id": 3, "name": "Z"})
    assert "task-kind: goal" in spec["label_names"]
    assert "source: board_worker" in spec["label_names"]
    assert "original-task-id: 3" in spec["label_names"]


def test_requeue_filters_only_board_worker_source():
    spec = _requeue_as_goal({"id": 1, "name": "T", "labels": [{"name": "source: board_worker"}]})
    # Inherited list excludes board_worker, so it appears exactly once
    # from the canonical append.
    assert spec["label_names"].count("source: board_worker") == 1
