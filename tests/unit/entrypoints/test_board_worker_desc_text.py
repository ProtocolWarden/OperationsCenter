# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for _desc_text and the thin-goal guard interaction with description_html."""

from __future__ import annotations


from operations_center.entrypoints.board_worker.main import _desc_text, _extract_goal


# ── _desc_text ────────────────────────────────────────────────────────────────


def test_desc_text_prefers_plain_description() -> None:
    issue = {"description": "Plain text.", "description_html": "<p>HTML.</p>"}
    assert _desc_text(issue) == "Plain text."


def test_desc_text_prefers_description_stripped() -> None:
    issue = {"description": None, "description_stripped": "Stripped text.", "description_html": "<p>HTML.</p>"}
    assert _desc_text(issue) == "Stripped text."


def test_desc_text_falls_back_to_html() -> None:
    issue = {
        "description": None,
        "description_stripped": None,
        "description_html": "<div><p>## Goal<br>Emit JUnit XML from the CI pytest step</p></div>",
    }
    result = _desc_text(issue)
    assert "## Goal" in result
    assert "Emit JUnit XML" in result
    assert "<" not in result


def test_desc_text_html_br_becomes_newline() -> None:
    issue = {
        "description": None,
        "description_stripped": None,
        "description_html": "<p>## Goal<br>Do the thing</p>",
    }
    result = _desc_text(issue)
    assert "\n" in result
    assert "Do the thing" in result


def test_desc_text_empty_issue() -> None:
    assert _desc_text({}) == ""


# ── thin-goal guard interaction ───────────────────────────────────────────────


def test_extract_goal_from_html_description_genuinely_thin() -> None:
    """89191ff5 has a ## Goal section whose text equals the short title (38 chars).
    _desc_text correctly extracts from HTML, but the goal text is still thin.
    The loop-prevention fix is the thin-goal label added by board_worker, not
    making the extracted text artificially longer.
    """
    html = "<div><p>## Goal<br>Emit JUnit XML from the CI pytest step</p><p>## Rationale<br>CI needs XML.</p></div>"
    issue = {"description": None, "description_stripped": None, "description_html": html}
    desc = _desc_text(issue)
    # _desc_text should produce plain text with a ## Goal section
    assert "## Goal" in desc
    assert "Emit JUnit XML" in desc
    assert "<" not in desc
    # The extracted goal text is still the short 38-char value (genuinely thin)
    goal = _extract_goal(desc, "Emit JUnit XML from the CI pytest step").strip()
    assert goal == "Emit JUnit XML from the CI pytest step"
    assert len(goal) < 40  # genuinely thin — thin-goal label prevents loop


def test_extract_goal_html_description_with_long_goal_passes_guard() -> None:
    """A task whose ## Goal section in description_html is substantive (>=40 chars)
    should pass the thin-goal guard once _desc_text extracts it from HTML."""
    html = (
        "<div><p>## Goal<br>Emit JUnit XML from the CI pytest step "
        "so GitHub Actions can display annotated test results inline on PRs</p></div>"
    )
    issue = {"description": None, "description_stripped": None, "description_html": html}
    desc = _desc_text(issue)
    goal = _extract_goal(desc, "Short title").strip()
    assert len(goal) >= 40
