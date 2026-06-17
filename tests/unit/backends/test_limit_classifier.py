# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import pytest

from operations_center.backends.limit_classifier import (
    GLOBAL_WEEKLY,
    MODEL_WEEKLY,
    SESSION_5H,
    classify_limit,
    models_affected,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "You've hit your Sonnet limit · resets Jun 3, 9am (America/New_York)",
            (MODEL_WEEKLY, "sonnet"),
        ),
        ("Opus weekly limit reached", (MODEL_WEEKLY, "opus")),
        ("You have reached your 5-hour session limit", (SESSION_5H, None)),
        ("session usage exhausted", (SESSION_5H, None)),
        ("account limit reached for this organization", (GLOBAL_WEEKLY, None)),
        ("weekly limit hit", (GLOBAL_WEEKLY, None)),
    ],
)
def test_classify_limit_kinds(text: str, expected: tuple[str, str | None]) -> None:
    assert classify_limit(text) == expected


def test_session_beats_named_model() -> None:
    # A 5-hour/session window is account-wide even if a model is mentioned.
    assert classify_limit("Sonnet 5-hour session limit") == (SESSION_5H, None)


def test_no_signal_returns_none_kind() -> None:
    assert classify_limit("some unrelated build error") == (None, None)
    assert classify_limit(None) == (None, None)


def test_models_affected_model_weekly_scopes_to_named_model() -> None:
    assert models_affected("claude_code", MODEL_WEEKLY, "sonnet") == ("sonnet",)


def test_models_affected_global_kinds_hit_all_models() -> None:
    assert set(models_affected("claude_code", SESSION_5H, None)) == {
        "sonnet",
        "opus",
        "haiku",
    }
    assert set(models_affected("claude_code", GLOBAL_WEEKLY, None)) == {
        "sonnet",
        "opus",
        "haiku",
    }
