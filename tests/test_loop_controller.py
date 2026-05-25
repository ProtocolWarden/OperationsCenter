# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools.loop import controller


def test_claude_session_command() -> None:
    cmd = controller._session_command("claude", "hello world")

    assert cmd[:4] == ["claude", "-p", "hello world", "--model"]
    assert "claude-sonnet-4-6" in cmd
    assert "--effort" in cmd
    assert "medium" in cmd
    assert cmd[-2:] == ["--output-format", "text"]


def test_codex_session_command_uses_repo_root() -> None:
    cmd = controller._session_command("codex", "hello world")

    assert cmd[:5] == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        str(controller.REPO_ROOT),
    ]
    assert cmd[5:9] == [
        "--model",
        "gpt-5.4",
        "-c",
        'model_reasoning_effort="medium"',
    ]
    assert cmd[-1] == "hello world"


def test_select_backend_prefers_codex_during_fallback_window(monkeypatch) -> None:
    frozen_now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        controller,
        "_command_available",
        lambda command: command == "codex",
    )

    assert controller._select_backend(datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc)) == "codex"


def test_select_backend_uses_claude_when_not_fallback(monkeypatch) -> None:
    frozen_now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        controller,
        "_command_available",
        lambda command: command == "claude",
    )

    assert controller._select_backend(None) == "claude"


def test_parse_rate_limit_reset(monkeypatch, tmp_path: Path) -> None:
    frozen_now = datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)

    log_path = tmp_path / "session.log"
    log_path.write_text("rate limit hit; resets 5:15pm (UTC)\n")

    reset_dt = controller.parse_rate_limit_reset(log_path)

    assert reset_dt == datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)
