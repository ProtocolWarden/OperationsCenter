# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import pytest

from operations_center.application.task_parser import TaskParser
from operations_center.domain import ParsedTaskBody


@pytest.fixture
def parser() -> TaskParser:
    return TaskParser()


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #


def test_full_description_with_all_sections(parser: TaskParser) -> None:
    description = (
        "## Goal\n"
        "Do the thing properly.\n\n"
        "## Execution\n"
        "repo: myrepo\n"
        "mode: goal\n"
        "base_branch: develop\n\n"
        "## Constraints\n"
        "Be careful.\n"
    )
    result = parser.parse(description)
    assert isinstance(result, ParsedTaskBody)
    assert result.goal_text == "Do the thing properly."
    assert result.constraints_text == "Be careful."
    assert result.execution_metadata["repo"] == "myrepo"
    assert result.execution_metadata["mode"] == "goal"
    assert result.execution_metadata["base_branch"] == "develop"
    assert result.execution_metadata["open_pr"] is False
    assert result.execution_metadata["allowed_paths"] == []


def test_default_mode_and_base_branch_applied(parser: TaskParser) -> None:
    description = "## Goal\nText\n\n## Execution\nrepo: r1\n"
    result = parser.parse(description)
    assert result.execution_metadata["mode"] == "goal"
    assert result.execution_metadata["base_branch"] == ""


# --------------------------------------------------------------------------- #
# Label-derived repo                                                           #
# --------------------------------------------------------------------------- #


def test_repo_from_labels_when_no_execution_section(parser: TaskParser) -> None:
    description = "## Goal\nJust a goal."
    result = parser.parse(description, labels=["repo: labelrepo"])
    assert result.execution_metadata["repo"] == "labelrepo"
    assert result.execution_metadata["mode"] == "goal"


def test_label_repo_fills_missing_repo_in_execution(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nmode: goal\n"
    result = parser.parse(description, labels=["repo:fromlabel"])
    assert result.execution_metadata["repo"] == "fromlabel"


def test_execution_repo_takes_precedence_over_label(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: execrepo\n"
    result = parser.parse(description, labels=["repo: labelrepo"])
    assert result.execution_metadata["repo"] == "execrepo"


def test_repo_from_labels_case_insensitive_prefix(parser: TaskParser) -> None:
    description = "## Goal\nG"
    result = parser.parse(description, labels=["REPO: caps"])
    assert result.execution_metadata["repo"] == "caps"


def test_repo_from_labels_skips_empty_value(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\n"
    # Empty repo label is ignored; execution repo wins.
    result = parser.parse(description, labels=["repo:   ", "other:x"])
    assert result.execution_metadata["repo"] == "r"


def test_repo_from_labels_returns_none_for_unrelated_labels(parser: TaskParser) -> None:
    description = "## Goal\nG"
    with pytest.raises(ValueError, match="Missing '## Execution' section"):
        parser.parse(description, labels=["priority:high"])


# --------------------------------------------------------------------------- #
# Error paths                                                                  #
# --------------------------------------------------------------------------- #


def test_missing_execution_and_no_label_raises(parser: TaskParser) -> None:
    with pytest.raises(ValueError, match="Missing '## Execution' section"):
        parser.parse("## Goal\nG")


def test_empty_description_raises(parser: TaskParser) -> None:
    with pytest.raises(ValueError, match="Missing '## Execution' section"):
        parser.parse("")


def test_none_description_raises(parser: TaskParser) -> None:
    with pytest.raises(ValueError, match="Missing '## Execution' section"):
        parser.parse(None)  # type: ignore[arg-type]


def test_non_dict_yaml_in_execution_raises(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\n- just\n- a\n- list\n"
    with pytest.raises(ValueError, match="must be valid key/value YAML"):
        parser.parse(description)


def test_execution_yaml_resolving_to_none_uses_label_repo(parser: TaskParser) -> None:
    # A comment-only execution body -> yaml.safe_load returns None -> {} .
    description = "## Goal\nG\n\n## Execution\n# only a comment\n"
    result = parser.parse(description, labels=["repo:lbl"])
    assert result.execution_metadata["repo"] == "lbl"


def test_execution_yaml_none_and_no_label_missing_repo(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\n# only a comment\n"
    with pytest.raises(ValueError, match="Missing execution metadata fields: repo"):
        parser.parse(description)


def test_missing_required_repo_field_raises(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nmode: goal\n"
    with pytest.raises(ValueError, match="Missing execution metadata fields: repo"):
        parser.parse(description)


def test_missing_goal_raises(parser: TaskParser) -> None:
    description = "## Execution\nrepo: r\n"
    with pytest.raises(ValueError, match="Missing goal text"):
        parser.parse(description)


def test_unsupported_mode_raises(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\nmode: bogus\n"
    with pytest.raises(ValueError, match="Unsupported execution mode 'bogus'"):
        parser.parse(description)


# --------------------------------------------------------------------------- #
# Goal fallback logic                                                          #
# --------------------------------------------------------------------------- #


def test_goal_fallback_strips_execution_block_with_trailing_content(
    parser: TaskParser,
) -> None:
    # No ## Goal section; ## Execution present with a following ## section.
    description = "Intro paragraph.\n\n## Execution\nrepo: r\n\n## Notes\ntrailing note\n"
    result = parser.parse(description)
    assert "Intro paragraph." in result.goal_text
    assert "## Notes" in result.goal_text
    assert "trailing note" in result.goal_text
    assert "repo: r" not in result.goal_text


def test_goal_fallback_strips_execution_block_to_end(parser: TaskParser) -> None:
    # No ## Goal, ## Execution is the last section.
    description = "Lead text here.\n\n## Execution\nrepo: r\n"
    result = parser.parse(description)
    assert result.goal_text == "Lead text here."


def test_goal_fallback_when_no_goal_and_no_exec_body_but_label_repo(
    parser: TaskParser,
) -> None:
    # execution_raw empty (label-based repo) and no ## Goal -> fallback is whole desc.
    description = "Plain description with no sections."
    result = parser.parse(description, labels=["repo:lbl"])
    assert result.goal_text == "Plain description with no sections."


def test_goal_fallback_empty_after_strip_raises(parser: TaskParser) -> None:
    # Only an execution section, no goal text -> fallback becomes empty.
    description = "## Execution\nrepo: r\n"
    with pytest.raises(ValueError, match="Missing goal text"):
        parser.parse(description)


def test_goal_section_present_overrides_fallback(parser: TaskParser) -> None:
    description = "## Goal\nReal goal.\n\n## Execution\nrepo: r\n## Notes\nx\n"
    result = parser.parse(description)
    assert result.goal_text == "Real goal."


# --------------------------------------------------------------------------- #
# Section extraction                                                           #
# --------------------------------------------------------------------------- #


def test_extract_sections_no_headers_returns_empty(parser: TaskParser) -> None:
    assert parser._extract_sections("plain text no headers") == {}


def test_extract_sections_titles_lowercased(parser: TaskParser) -> None:
    sections = parser._extract_sections("## GOAL\nbody\n## Execution\nrepo: r")
    assert "goal" in sections
    assert "execution" in sections
    assert sections["goal"] == "body"


# --------------------------------------------------------------------------- #
# Metadata normalization                                                       #
# --------------------------------------------------------------------------- #


def test_mode_alias_test(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\nmode: test\n"
    result = parser.parse(description)
    assert result.execution_metadata["mode"] == "test_campaign"


def test_mode_alias_improve(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\nmode: improve\n"
    result = parser.parse(description)
    assert result.execution_metadata["mode"] == "improve_campaign"


def test_mode_canonical_campaign_modes(parser: TaskParser) -> None:
    for mode in ("test_campaign", "improve_campaign", "fix_pr"):
        description = f"## Goal\nG\n\n## Execution\nrepo: r\nmode: {mode}\n"
        result = parser.parse(description)
        assert result.execution_metadata["mode"] == mode


def test_mode_whitespace_and_uppercase_normalized(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\nmode: '  GOAL  '\n"
    result = parser.parse(description)
    assert result.execution_metadata["mode"] == "goal"


def test_allowed_paths_string_coerced_to_list(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\nallowed_paths: src/foo\n"
    result = parser.parse(description)
    assert result.execution_metadata["allowed_paths"] == ["src/foo"]


def test_allowed_paths_list_trimmed_and_emptied(parser: TaskParser) -> None:
    description = (
        "## Goal\nG\n\n## Execution\nrepo: r\nallowed_paths:\n  - '  a  '\n  - ''\n  - b\n"
    )
    result = parser.parse(description)
    assert result.execution_metadata["allowed_paths"] == ["a", "b"]


def test_open_pr_truthy_values(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\nopen_pr: true\n"
    result = parser.parse(description)
    assert result.execution_metadata["open_pr"] is True


def test_open_pr_default_false_when_absent(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\n"
    result = parser.parse(description)
    assert result.execution_metadata["open_pr"] is False


def test_campaign_passthrough_fields_stringified(parser: TaskParser) -> None:
    description = (
        "## Goal\nG\n\n## Execution\n"
        "repo: r\nmode: test_campaign\n"
        "spec_campaign_id: 12345\n"
        "spec_file: specs/x.md\n"
        "task_phase: '  phase1  '\n"
        "spec_coverage_hint: 80\n"
    )
    result = parser.parse(description)
    md = result.execution_metadata
    assert md["spec_campaign_id"] == "12345"
    assert md["spec_file"] == "specs/x.md"
    assert md["task_phase"] == "phase1"
    assert md["spec_coverage_hint"] == "80"


def test_passthrough_fields_absent_not_added(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\n"
    result = parser.parse(description)
    for field in ("spec_campaign_id", "spec_file", "task_phase", "spec_coverage_hint"):
        assert field not in result.execution_metadata


def test_constraints_blank_becomes_none(parser: TaskParser) -> None:
    description = "## Goal\nG\n\n## Execution\nrepo: r\n\n## Constraints\n   \n"
    result = parser.parse(description)
    assert result.constraints_text is None
