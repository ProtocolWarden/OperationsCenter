# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from operations_center.entrypoints.board_worker import _text


# --------------------------------------------------------------------------
# desc_text
# --------------------------------------------------------------------------
def test_desc_text_prefers_description():
    assert _text.desc_text({"description": "plain", "description_html": "<b>x</b>"}) == "plain"


def test_desc_text_falls_back_to_stripped():
    assert _text.desc_text({"description_stripped": "stripped"}) == "stripped"


def test_desc_text_strips_html_and_unescapes():
    issue = {"description_html": "<p>Hello<br>World &amp; more</p>"}
    assert _text.desc_text(issue) == "Hello\nWorld & more"


def test_desc_text_self_closing_br():
    assert _text.desc_text({"description_html": "a<br/>b"}) == "a\nb"


def test_desc_text_empty_when_nothing_present():
    assert _text.desc_text({}) == ""


def test_desc_text_html_key_present_but_empty():
    # description/stripped empty, html key exists but is falsy -> stays ""
    assert _text.desc_text({"description": "", "description_html": ""}) == ""


# --------------------------------------------------------------------------
# extract_goal
# --------------------------------------------------------------------------
def test_extract_goal_from_section():
    desc = "intro\n## Goal\nDo the thing\nand more\n## Other\nx"
    assert _text.extract_goal(desc, "TITLE") == "Do the thing\nand more"


def test_extract_goal_runs_to_end_of_string():
    desc = "## Goal\nFinal goal text"
    assert _text.extract_goal(desc, "TITLE") == "Final goal text"


def test_extract_goal_case_insensitive():
    desc = "## goal\nlower"
    assert _text.extract_goal(desc, "TITLE") == "lower"


def test_extract_goal_empty_section_falls_back_to_title():
    desc = "## Goal\n   \n## Next\nx"
    assert _text.extract_goal(desc, "MyTitle") == "MyTitle"


def test_extract_goal_no_section_falls_back_to_title():
    assert _text.extract_goal("no goal here", "MyTitle") == "MyTitle"


# --------------------------------------------------------------------------
# task_type_from_kind
# --------------------------------------------------------------------------
def test_task_type_from_kind_known():
    assert _text.task_type_from_kind("goal") == "feature"
    assert _text.task_type_from_kind("test") == "test"
    assert _text.task_type_from_kind("test_campaign") == "test"
    assert _text.task_type_from_kind("improve") == "refactor"
    assert _text.task_type_from_kind("improve_campaign") == "refactor"
    assert _text.task_type_from_kind("spec-author") == "chore"


def test_task_type_from_kind_unknown():
    assert _text.task_type_from_kind("anything-else") == "chore"


# --------------------------------------------------------------------------
# parse_spec_author_payload
# --------------------------------------------------------------------------
def test_parse_spec_author_payload_plain_yaml():
    desc = "intro\n```yaml\nspec_slug: foo\ntask_phase: test\n```\ntail"
    data = _text.parse_spec_author_payload(desc)
    assert data == {"spec_slug": "foo", "task_phase": "test"}


def test_parse_spec_author_payload_strips_html():
    desc = "<p>x</p>```yaml<br>spec_slug: bar &amp; baz<br>```"
    data = _text.parse_spec_author_payload(desc)
    assert data == {"spec_slug": "bar & baz"}


def test_parse_spec_author_payload_no_fence_returns_none():
    assert _text.parse_spec_author_payload("no yaml here") is None


def test_parse_spec_author_payload_invalid_yaml_returns_none():
    # tab+colon style that breaks yaml parsing
    desc = "```yaml\nkey: : : [unbalanced\n```"
    assert _text.parse_spec_author_payload(desc) is None


def test_parse_spec_author_payload_non_dict_returns_none():
    desc = "```yaml\n- just\n- a\n- list\n```"
    assert _text.parse_spec_author_payload(desc) is None


# --------------------------------------------------------------------------
# summarize_prompt_diff_block
# --------------------------------------------------------------------------
def _open():
    return _text._PROMPT_DIFF_OPEN


def _close():
    return _text._PROMPT_DIFF_CLOSE


def test_summarize_read_failure(tmp_path):
    count, note = _text.summarize_prompt_diff_block(
        workspace=tmp_path, target_path="does_not_exist.md"
    )
    assert count is None
    assert note.startswith("read failed:")


def test_summarize_absent_fence(tmp_path):
    (tmp_path / "spec.md").write_text("no fence here", encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count is None
    assert note == "absent"


def test_summarize_only_open_marker_is_absent(tmp_path):
    (tmp_path / "spec.md").write_text(_open() + "\nedits:\n", encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count is None
    assert note == "absent"


def test_summarize_parsed(tmp_path):
    body = (
        "edits:\n"
        "  - op: replace\n"
        '    anchor: "a"\n'
        '    new_text: "b"\n'
        '    reason: "r"\n'
        "  - op: append\n"
        '    new_text: "x"\n'
        '    reason: "r2"\n'
    )
    content = f"spec body\n{_open()}\n{body}{_close()}\ntrailer"
    (tmp_path / "spec.md").write_text(content, encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count == 2
    assert note == "parsed"


def test_summarize_edits_key_missing(tmp_path):
    body = "other: value\n"
    content = f"{_open()}\n{body}{_close()}"
    (tmp_path / "spec.md").write_text(content, encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count is None
    assert note == "edits key missing or not a list"


def test_summarize_empty_body_yaml_none(tmp_path):
    # empty body -> yaml.safe_load returns None -> doc becomes {} -> edits missing
    content = f"{_open()}\n{_close()}"
    (tmp_path / "spec.md").write_text(content, encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count is None
    assert note == "edits key missing or not a list"


def test_summarize_parse_failure_invalid_edit(tmp_path):
    # edits is a list but entries fail Edit.model_validate (missing required reason)
    body = "edits:\n  - op: replace\n    anchor: a\n    new_text: b\n"
    content = f"{_open()}\n{body}{_close()}"
    (tmp_path / "spec.md").write_text(content, encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count is None
    assert note.startswith("parse failed:")


def test_summarize_malformed_yaml_in_body(tmp_path):
    body = "edits: : : [\n"
    content = f"{_open()}\n{body}{_close()}"
    (tmp_path / "spec.md").write_text(content, encoding="utf-8")
    count, note = _text.summarize_prompt_diff_block(workspace=tmp_path, target_path="spec.md")
    assert count is None
    assert note.startswith("parse failed:")


# --------------------------------------------------------------------------
# build_phase_advance_goal_text
# --------------------------------------------------------------------------
def test_build_phase_advance_basic():
    out = _text.build_phase_advance_goal_text(
        spec_slug="myslug",
        target_path="specs/foo.md",
        task_phase="test",
        seed_text="",
        ctx={},
        run_id_placeholder="RUN123",
    )
    assert "Spec phase advance — myslug -> test" in out
    assert "specs/foo.md" in out
    assert "RUN123" in out
    assert _open() in out
    assert _close() in out
    # no seed -> no phase state section
    assert "Phase state (from spec_hygiene)" not in out
    # no git repos
    assert "Recent Git Activity" not in out


def test_build_phase_advance_with_seed_and_repos():
    ctx = {"recent_git_log_repos": {"repoA": "log lines", "repoB": ""}}
    out = _text.build_phase_advance_goal_text(
        spec_slug="s",
        target_path="t.md",
        task_phase="improve",
        seed_text="SEED",
        ctx=ctx,
        run_id_placeholder="R",
    )
    assert "Phase state (from spec_hygiene)" in out
    assert "SEED" in out
    assert "Recent Git Activity (repoA)" in out
    # empty log skipped
    assert "Recent Git Activity (repoB)" not in out


def test_build_phase_advance_repos_not_dict():
    out = _text.build_phase_advance_goal_text(
        spec_slug="s",
        target_path="t.md",
        task_phase="p",
        seed_text="",
        ctx={"recent_git_log_repos": "not a dict"},
        run_id_placeholder="R",
    )
    assert "Recent Git Activity" not in out


# --------------------------------------------------------------------------
# build_spec_author_goal_text
# --------------------------------------------------------------------------
def test_build_spec_author_delegates_when_phase_present(monkeypatch):
    called = {}

    def fake(**kwargs):
        called.update(kwargs)
        return "DELEGATED"

    monkeypatch.setattr(_text, "build_phase_advance_goal_text", fake)
    payload = {
        "spec_slug": "sl",
        "target_path": "tp.md",
        "task_phase": "test",
        "seed_text": "sd",
        "context_bundle": {"k": "v"},
    }
    out = _text.build_spec_author_goal_text(payload, "RUNID")
    assert out == "DELEGATED"
    assert called["spec_slug"] == "sl"
    assert called["task_phase"] == "test"
    assert called["ctx"] == {"k": "v"}
    assert called["run_id_placeholder"] == "RUNID"


def test_build_spec_author_delegates_ctx_not_dict(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        _text,
        "build_phase_advance_goal_text",
        lambda **kw: captured.update(kw) or "X",
    )
    payload = {"task_phase": "p", "context_bundle": ["notdict"]}
    _text.build_spec_author_goal_text(payload, "R")
    assert captured["ctx"] == {}


def test_build_spec_author_full_authoring():
    payload = {
        "spec_slug": "newslug",
        "target_path": "specs/new.md",
        "trigger_source": "operator",
        "seed_text": "do better",
        "context_bundle": {
            "recent_git_log_repos": {"repoX": "commit1", "repoY": ""},
            "existing_specs": ["specs/old.md"],
            "board_snapshot": {"ready": 3, "running": 1, "drained": 0},
        },
    }
    out = _text.build_spec_author_goal_text(payload, "RUN9")
    assert "Spec: newslug" in out
    assert "specs/new.md" in out
    assert "RUN9" in out
    assert "slug: newslug" in out
    assert "## Available Repos" in out
    assert "- repoX" in out
    assert "## Operator Direction\ndo better" in out
    assert "Recent Git Activity (repoX)" in out
    assert "Recent Git Activity (repoY)" not in out
    assert "Existing Specs (do not duplicate)" in out
    assert "- specs/old.md" in out
    assert "## Board Summary" in out
    assert "ready: 3" in out
    assert "trigger_source: operator" in out


def test_build_spec_author_minimal_no_optionals():
    payload = {"spec_slug": "s", "target_path": "t.md"}
    out = _text.build_spec_author_goal_text(payload, "R")
    assert "Spec: s" in out
    assert "## Available Repos" not in out
    assert "## Operator Direction" not in out
    assert "Existing Specs" not in out
    assert "## Board Summary" not in out
    assert "## Boundaries" in out


def test_build_spec_author_repos_present_but_empty_dict():
    payload = {
        "spec_slug": "s",
        "target_path": "t.md",
        "context_bundle": {"recent_git_log_repos": {}},
    }
    out = _text.build_spec_author_goal_text(payload, "R")
    assert "## Available Repos" not in out


def test_build_spec_author_board_snapshot_not_dict():
    payload = {
        "spec_slug": "s",
        "target_path": "t.md",
        "context_bundle": {"board_snapshot": "nope"},
    }
    out = _text.build_spec_author_goal_text(payload, "R")
    assert "## Board Summary" not in out


def test_build_spec_author_board_snapshot_missing_keys():
    payload = {
        "spec_slug": "s",
        "target_path": "t.md",
        "context_bundle": {"board_snapshot": {"ready": 5}},
    }
    out = _text.build_spec_author_goal_text(payload, "R")
    assert "ready: 5" in out
    assert "running: ?" in out
    assert "drained: ?" in out


def test_build_spec_author_context_bundle_missing_uses_empty():
    payload = {"spec_slug": "s", "target_path": "t.md", "context_bundle": None}
    out = _text.build_spec_author_goal_text(payload, "R")
    assert "Spec: s" in out
