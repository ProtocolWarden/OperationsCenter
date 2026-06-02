# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# tests/prompt_diff/test_apply.py
"""Apply-primitive coverage for ``operations_center.prompt_diff``.

ADR 0007 follow-up C — the primitive copied in from
temm1e-labs/promptlabs (MIT). Tests pin the contract the
phase-advance prompt template depends on: anchor uniqueness,
required-field validation, ``apply_edits`` skip semantics.
"""

from __future__ import annotations

import pytest

from operations_center.prompt_diff import (
    ApplyResult,
    Edit,
    EditApplicationError,
    apply_edits,
    apply_one,
)

# ----- apply_one ---------------------------------------------------------


def test_replace_unique_anchor_succeeds():
    src = "alpha beta gamma"
    edit = Edit(op="replace", anchor="beta", new_text="BETA", reason="upcase")
    assert apply_one(src, edit) == "alpha BETA gamma"


def test_replace_ambiguous_anchor_raises():
    src = "foo bar foo"
    edit = Edit(op="replace", anchor="foo", new_text="FOO", reason="upcase")
    with pytest.raises(EditApplicationError, match="ambiguous"):
        apply_one(src, edit)


def test_replace_missing_anchor_raises():
    src = "alpha beta"
    edit = Edit(op="replace", anchor="gamma", new_text="GAMMA", reason="add")
    with pytest.raises(EditApplicationError, match="anchor not found"):
        apply_one(src, edit)


def test_insert_before_succeeds():
    src = "line1\nline2\nline3"
    edit = Edit(op="insert_before", anchor="line2\n", new_text="prelude\n", reason="add header")
    assert apply_one(src, edit) == "line1\nprelude\nline2\nline3"


def test_insert_after_succeeds():
    src = "line1\nline2\nline3"
    edit = Edit(op="insert_after", anchor="line2\n", new_text="extra\n", reason="add body")
    assert apply_one(src, edit) == "line1\nline2\nextra\nline3"


def test_delete_succeeds():
    src = "keep\nremove me\nkeep too"
    edit = Edit(op="delete", anchor="remove me\n", reason="dead text")
    assert apply_one(src, edit) == "keep\nkeep too"


def test_append_no_anchor_needed():
    src = "body"
    edit = Edit(op="append", new_text="\nfooter", reason="add footer")
    assert apply_one(src, edit) == "body\nfooter"


def test_append_requires_new_text():
    edit = Edit(op="append", reason="no-op")
    with pytest.raises(EditApplicationError, match="append requires new_text"):
        apply_one("body", edit)


def test_non_append_requires_anchor():
    edit = Edit(op="replace", new_text="X", reason="missing anchor")
    with pytest.raises(EditApplicationError, match="requires anchor"):
        apply_one("body", edit)


def test_replace_requires_new_text():
    # Anchor present + unique, but no replacement supplied.
    edit = Edit(op="replace", anchor="body", reason="no payload")
    with pytest.raises(EditApplicationError, match="replace requires new_text"):
        apply_one("body", edit)


# ----- apply_edits -------------------------------------------------------


def test_apply_edits_mixed_valid_and_invalid():
    src = "alpha beta gamma"
    edits = [
        Edit(op="replace", anchor="alpha", new_text="ALPHA", reason="ok"),
        Edit(op="replace", anchor="missing", new_text="X", reason="bad anchor"),
        Edit(op="insert_after", anchor="gamma", new_text=" delta", reason="ok"),
    ]
    out, result = apply_edits(src, edits)

    assert isinstance(result, ApplyResult)
    assert out == "ALPHA beta gamma delta"
    assert result.edits_applied == 2
    assert result.edits_skipped == 1
    assert len(result.skip_reasons) == 1
    assert result.skip_reasons[0].startswith("edit[1] replace:")
    assert "anchor not found" in result.skip_reasons[0]


def test_apply_edits_empty_list_is_noop():
    out, result = apply_edits("untouched", [])
    assert out == "untouched"
    assert result.edits_applied == 0
    assert result.edits_skipped == 0
    assert result.skip_reasons == []


def test_apply_edits_sequential_against_partial_state():
    """Edit N runs against the partially-edited output of edit N-1.

    Pins upstream-Optimizer semantics — important because chained edits
    can either enable or invalidate later anchors.
    """
    src = "one two three"
    edits = [
        Edit(op="replace", anchor="two", new_text="TWO", reason="upcase"),
        # This anchor only exists *after* the first edit lands.
        Edit(op="insert_after", anchor="TWO", new_text="!", reason="emphasize"),
    ]
    out, result = apply_edits(src, edits)
    assert out == "one TWO! three"
    assert result.edits_applied == 2
    assert result.edits_skipped == 0
