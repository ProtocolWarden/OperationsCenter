# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools.loop import controller


def test_claude_session_command() -> None:
    cmd = controller._session_command("claude", "hello world")

    assert Path(cmd[0]).name == "claude"
    assert cmd[1:4] == ["-p", "hello world", "--model"]
    assert "claude-sonnet-4-6" in cmd
    assert "--effort" in cmd
    assert "medium" in cmd
    assert cmd[-2:] == ["--output-format", "text"]


def test_codex_session_command_uses_repo_root() -> None:
    cmd = controller._session_command("codex", "hello world")

    assert [Path(cmd[0]).name, *cmd[1:5]] == [
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


def test_command_available_uses_fallback_candidates(monkeypatch, tmp_path: Path) -> None:
    fallback = tmp_path / "codex"
    fallback.write_text("#!/bin/sh\n")
    fallback.chmod(0o755)

    monkeypatch.setattr(controller.shutil, "which", lambda command: None)
    monkeypatch.setattr(
        controller,
        "_fallback_command_candidates",
        lambda command: [fallback] if command == "codex" else [],
    )

    assert controller._command_available("codex") is True
    assert controller._resolve_command("codex") == str(fallback)


def test_session_env_prepends_backend_bin_dir(monkeypatch) -> None:
    monkeypatch.setattr(
        controller,
        "_resolve_command",
        lambda command: "/tmp/tools/bin/codex" if command == "codex" else None,
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = controller._session_env("codex")

    assert env["PATH"].split(":")[:3] == ["/tmp/tools/bin", "/usr/bin", "/bin"]


def test_select_backend_prefers_codex_when_claude_cooling_down(monkeypatch) -> None:
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
        lambda command: command in {"claude", "codex"},
    )

    # Both claude and opus (same CLI) cooling down → fall through to codex.
    assert controller._select_backend(
        {
            "claude": datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
            "opus": datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
            "codex": None,
        }
    ) == "codex"


def test_select_backend_prefers_opus_when_only_sonnet_cooling_down(monkeypatch) -> None:
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
        lambda command: command in {"claude", "codex"},
    )

    assert controller._select_backend(
        {
            "claude": datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
            "opus": None,
            "codex": None,
        }
    ) == "opus"


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

    assert controller._select_backend({"claude": None, "codex": None}) == "claude"


def test_parse_rate_limit_reset_timezone_message(monkeypatch, tmp_path: Path) -> None:
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

    reset_dt, _ = controller.parse_rate_limit_reset(log_path, "claude")

    assert reset_dt == datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)


def test_parse_rate_limit_reset_timezone_message_without_minutes(
    monkeypatch, tmp_path: Path
) -> None:
    frozen_now = datetime(2026, 5, 26, 22, 45, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)

    log_path = tmp_path / "session.log"
    log_path.write_text("You've hit your weekly limit · resets 9am (America/New_York)\n")

    reset_dt, _ = controller.parse_rate_limit_reset(log_path, "claude")

    assert reset_dt == datetime(2026, 5, 27, 13, 0, tzinfo=timezone.utc)


def test_parse_rate_limit_reset_relative_message(monkeypatch, tmp_path: Path) -> None:
    frozen_now = datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)

    log_path = tmp_path / "session.log"
    log_path.write_text("codex usage limit hit, please try again in 5h 0m\n")

    reset_dt, _ = controller.parse_rate_limit_reset(log_path, "codex")

    assert reset_dt == datetime(2026, 5, 25, 21, 0, tzinfo=timezone.utc)


def test_clear_expired_cooldowns_logs_and_resets(monkeypatch) -> None:
    frozen_now = datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    logged: list[str] = []
    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(controller, "_log", logged.append)

    cooldowns = {
        "claude": datetime(2026, 5, 25, 15, 30, tzinfo=timezone.utc),
        "codex": datetime(2026, 5, 25, 17, 0, tzinfo=timezone.utc),
    }

    controller._clear_expired_cooldowns(cooldowns)

    assert cooldowns["claude"] is None
    assert cooldowns["codex"] == datetime(2026, 5, 25, 17, 0, tzinfo=timezone.utc)
    assert any("Claude cooldown expired" in msg for msg in logged)


def test_opus_session_command() -> None:
    cmd = controller._session_command("opus", "hello world")

    assert Path(cmd[0]).name == "claude"  # opus runs on the claude CLI
    assert cmd[1:4] == ["-p", "hello world", "--model"]
    assert "claude-opus-4-8" in cmd
    assert cmd[-2:] == ["--output-format", "text"]


def test_alternate_backend_follows_priority_order() -> None:
    assert controller._alternate_backend("claude") == "opus"
    assert controller._alternate_backend("opus") == "codex"
    assert controller._alternate_backend("codex") == "claude"


def test_handle_backend_limit_sonnet_model_limit_leaves_opus_runnable(
    monkeypatch, tmp_path: Path
) -> None:
    frozen_now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(controller, "_log", lambda *a, **k: None)

    log_path = tmp_path / "session.log"
    log_path.write_text("rate limit hit; resets 5:15pm (UTC)\n")
    cooldowns: dict = {"claude": None, "opus": None, "codex": None}

    assert controller._handle_backend_limit("claude", log_path, cooldowns) is True
    assert cooldowns["claude"] == datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)
    assert cooldowns["opus"] is None


def test_handle_backend_limit_global_claude_limit_cools_opus_too(
    monkeypatch, tmp_path: Path
) -> None:
    frozen_now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(controller, "_log", lambda *a, **k: None)

    log_path = tmp_path / "session.log"
    log_path.write_text(
        "You've reached your 5-hour session limit; resets 5:15pm (UTC)\n"
    )
    cooldowns: dict = {"claude": None, "opus": None, "codex": None}

    assert controller._handle_backend_limit("claude", log_path, cooldowns) is True
    reset = datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)
    assert cooldowns["claude"] == reset
    assert cooldowns["opus"] == reset
