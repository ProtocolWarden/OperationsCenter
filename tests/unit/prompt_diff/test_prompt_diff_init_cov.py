# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import pytest
from pydantic import ValidationError

from operations_center.prompt_diff import (
    ApplyResult,
    Edit,
    EditApplicationError,
    apply_edits,
    apply_one,
)


def _edit(op, *, anchor=None, new_text=None, reason="r", targets_criterion=None):
    return Edit(
        op=op,
        anchor=anchor,
        new_text=new_text,
        reason=reason,
        targets_criterion=targets_criterion,
    )


# ---------------------------------------------------------------------------
# Edit model
# ---------------------------------------------------------------------------


def test_edit_defaults():
    e = Edit(op="append", new_text="x", reason="add")
    assert e.anchor is None
    assert e.new_text == "x"
    assert e.targets_criterion is None
    assert e.reason == "add"


def test_edit_full_fields():
    e = Edit(
        op="replace",
        anchor="a",
        new_text="b",
        reason="why",
        targets_criterion="crit1",
    )
    assert e.op == "replace"
    assert e.anchor == "a"
    assert e.targets_criterion == "crit1"


def test_edit_requires_reason():
    with pytest.raises(ValidationError):
        Edit(op="append", new_text="x")  # type: ignore[call-arg]


def test_edit_rejects_unknown_op_at_construction():
    with pytest.raises(ValidationError):
        Edit(op="frobnicate", reason="r")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# EditApplicationError
# ---------------------------------------------------------------------------


def test_edit_application_error_is_valueerror():
    assert issubclass(EditApplicationError, ValueError)


# ---------------------------------------------------------------------------
# ApplyResult
# ---------------------------------------------------------------------------


def test_apply_result_defaults():
    r = ApplyResult()
    assert r.edits_applied == 0
    assert r.edits_skipped == 0
    assert r.skip_reasons == []


def test_apply_result_skip_reasons_independent_instances():
    a = ApplyResult()
    b = ApplyResult()
    a.skip_reasons.append("x")
    assert b.skip_reasons == []


# ---------------------------------------------------------------------------
# apply_one — append
# ---------------------------------------------------------------------------


def test_apply_one_append():
    assert apply_one("hello", _edit("append", new_text=" world")) == "hello world"


def test_apply_one_append_empty_string():
    assert apply_one("hello", _edit("append", new_text="")) == "hello"


def test_apply_one_append_missing_new_text_raises():
    with pytest.raises(EditApplicationError, match="append requires new_text"):
        apply_one("hello", _edit("append"))


# ---------------------------------------------------------------------------
# apply_one — anchor validation (shared by non-append ops)
# ---------------------------------------------------------------------------


def test_apply_one_missing_anchor_raises():
    with pytest.raises(EditApplicationError, match="replace requires anchor"):
        apply_one("hello", _edit("replace", new_text="x"))


def test_apply_one_anchor_not_found_raises():
    with pytest.raises(EditApplicationError, match="anchor not found"):
        apply_one("hello", _edit("replace", anchor="zzz", new_text="x"))


def test_apply_one_ambiguous_anchor_raises():
    with pytest.raises(EditApplicationError, match=r"anchor ambiguous \(3 matches\)"):
        apply_one("a a a", _edit("delete", anchor="a"))


# ---------------------------------------------------------------------------
# apply_one — replace
# ---------------------------------------------------------------------------


def test_apply_one_replace():
    assert (
        apply_one("hello world", _edit("replace", anchor="world", new_text="there"))
        == "hello there"
    )


def test_apply_one_replace_only_first_when_unique():
    # anchor is unique so replace count=1 path is exercised
    assert apply_one("xAy", _edit("replace", anchor="A", new_text="B")) == "xBy"


def test_apply_one_replace_missing_new_text_raises():
    with pytest.raises(EditApplicationError, match="replace requires new_text"):
        apply_one("hello", _edit("replace", anchor="hello"))


# ---------------------------------------------------------------------------
# apply_one — delete
# ---------------------------------------------------------------------------


def test_apply_one_delete():
    assert apply_one("hello world", _edit("delete", anchor=" world")) == "hello"


def test_apply_one_delete_ignores_new_text():
    assert apply_one("abc", _edit("delete", anchor="b", new_text="ignored")) == "ac"


# ---------------------------------------------------------------------------
# apply_one — insert_before / insert_after
# ---------------------------------------------------------------------------


def test_apply_one_insert_before():
    assert (
        apply_one("world", _edit("insert_before", anchor="world", new_text="hello "))
        == "hello world"
    )


def test_apply_one_insert_before_missing_new_text_raises():
    with pytest.raises(EditApplicationError, match="insert_before requires new_text"):
        apply_one("world", _edit("insert_before", anchor="world"))


def test_apply_one_insert_after():
    assert (
        apply_one("hello", _edit("insert_after", anchor="hello", new_text=" world"))
        == "hello world"
    )


def test_apply_one_insert_after_missing_new_text_raises():
    with pytest.raises(EditApplicationError, match="insert_after requires new_text"):
        apply_one("hello", _edit("insert_after", anchor="hello"))


# ---------------------------------------------------------------------------
# apply_one — unknown op (defeat pydantic via model_construct)
# ---------------------------------------------------------------------------


def test_apply_one_unknown_op_raises():
    bogus = Edit.model_construct(
        op="bogus", anchor="hello", new_text="x", reason="r", targets_criterion=None
    )
    with pytest.raises(EditApplicationError, match="unknown op: bogus"):
        apply_one("hello", bogus)


# ---------------------------------------------------------------------------
# apply_edits
# ---------------------------------------------------------------------------


def test_apply_edits_empty_list():
    text, result = apply_edits("base", [])
    assert text == "base"
    assert result.edits_applied == 0
    assert result.edits_skipped == 0
    assert result.skip_reasons == []


def test_apply_edits_all_succeed_sequential():
    edits = [
        _edit("replace", anchor="foo", new_text="bar"),
        _edit("append", new_text="!"),
    ]
    text, result = apply_edits("foo", edits)
    assert text == "bar!"
    assert result.edits_applied == 2
    assert result.edits_skipped == 0


def test_apply_edits_skips_failures_and_records_reason():
    edits = [
        _edit("replace", anchor="missing", new_text="x"),
        _edit("append", new_text="ok"),
    ]
    text, result = apply_edits("base", edits)
    assert text == "baseok"
    assert result.edits_applied == 1
    assert result.edits_skipped == 1
    assert result.skip_reasons == ["edit[0] replace: anchor not found"]


def test_apply_edits_continues_against_partially_edited_text():
    # First edit creates the anchor the second edit needs.
    edits = [
        _edit("append", new_text=" INSERTED"),
        _edit("replace", anchor="INSERTED", new_text="DONE"),
    ]
    text, result = apply_edits("start", edits)
    assert text == "start DONE"
    assert result.edits_applied == 2
    assert result.edits_skipped == 0


def test_apply_edits_skip_reason_index_matches_position():
    edits = [
        _edit("append", new_text="A"),
        _edit("delete", anchor="zzz"),
        _edit("insert_before", anchor="nope", new_text="x"),
    ]
    _text, result = apply_edits("base", edits)
    assert result.edits_applied == 1
    assert result.edits_skipped == 2
    assert result.skip_reasons[0].startswith("edit[1] delete:")
    assert result.skip_reasons[1].startswith("edit[2] insert_before:")
