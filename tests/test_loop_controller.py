# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from operations_center.execution.usage_store import UsageStore
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
    assert (
        controller._select_backend(
            {
                "claude": datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
                "opus": datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
                "codex": None,
            }
        )
        == "codex"
    )


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

    assert (
        controller._select_backend(
            {
                "claude": datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
                "opus": None,
                "codex": None,
            }
        )
        == "opus"
    )


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


def test_parse_rate_limit_reset_model_limit_with_date(monkeypatch, tmp_path: Path) -> None:
    """Real claude CLI per-model limit: 'You've hit your Sonnet limit · resets
    Jun 3, 9am (America/New_York)'. The month+day form must parse."""
    frozen_now = datetime(2026, 5, 30, 16, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)

    log_path = tmp_path / "session.log"
    log_path.write_text("You've hit your Sonnet limit · resets Jun 3, 9am (America/New_York)\n")

    reset_dt, _ = controller.parse_rate_limit_reset(log_path, "claude")

    # Jun 3 09:00 EDT (UTC-4) → 13:00 UTC
    assert reset_dt == datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc)


def test_parse_rate_limit_reset_model_limit_with_date_and_minutes(
    monkeypatch, tmp_path: Path
) -> None:
    frozen_now = datetime(2026, 5, 30, 16, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)

    log_path = tmp_path / "session.log"
    log_path.write_text("You've hit your Opus limit · resets Jun 3, 9:30am (America/New_York)\n")

    reset_dt, _ = controller.parse_rate_limit_reset(log_path, "opus")

    assert reset_dt == datetime(2026, 6, 3, 13, 30, tzinfo=timezone.utc)


def test_handle_backend_limit_real_sonnet_message_cools_claude_leaves_opus(
    monkeypatch, tmp_path: Path
) -> None:
    """The real 'Sonnet limit · resets Jun 3' message must cool claude and leave
    opus runnable so the fallback engages instead of spinning on sonnet."""
    frozen_now = datetime(2026, 5, 30, 16, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(controller, "_log", lambda *a, **k: None)

    log_path = tmp_path / "session.log"
    log_path.write_text("You've hit your Sonnet limit · resets Jun 3, 9am (America/New_York)\n")
    cooldowns: dict = {"claude": None, "opus": None, "codex": None}

    assert controller._handle_backend_limit("claude", log_path, cooldowns) is True
    assert cooldowns["claude"] == datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc)
    assert cooldowns["opus"] is None  # per-model limit → opus still runnable


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


def test_handle_backend_limit_bare_weekly_limit_cools_opus_too(
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
    log_path.write_text("weekly limit hit; resets 5:15pm (UTC)\n")
    cooldowns: dict = {"claude": None, "opus": None, "codex": None}

    assert controller._handle_backend_limit("claude", log_path, cooldowns) is True
    assert cooldowns["claude"] == datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)
    assert cooldowns["opus"] == datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)


def test_handle_backend_limit_explicit_sonnet_limit_leaves_opus_runnable(
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
    log_path.write_text("Sonnet rate limit hit; resets 5:15pm (UTC)\n")
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
    log_path.write_text("You've reached your 5-hour session limit; resets 5:15pm (UTC)\n")
    cooldowns: dict = {"claude": None, "opus": None, "codex": None}

    assert controller._handle_backend_limit("claude", log_path, cooldowns) is True
    reset = datetime(2026, 5, 25, 17, 15, tzinfo=timezone.utc)
    assert cooldowns["claude"] == reset
    assert cooldowns["opus"] == reset


def test_global_claude_limit_fallback_selects_codex(monkeypatch, tmp_path: Path) -> None:
    frozen_now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(controller, "_log", lambda *a, **k: None)
    monkeypatch.setattr(
        controller,
        "_command_available",
        lambda command: command in {"claude", "codex"},
    )

    log_path = tmp_path / "session.log"
    log_path.write_text("You've reached your 5-hour session limit; resets 5:15pm (UTC)\n")
    cooldowns: dict = {"claude": None, "opus": None, "codex": None}

    assert controller._handle_backend_limit("claude", log_path, cooldowns) is True
    assert controller._fallback_backend_after_limit(cooldowns) == "codex"


def test_seed_cooldowns_from_usage_store_selects_codex(monkeypatch, tmp_path: Path) -> None:
    frozen_now = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(controller, "datetime", FrozenDateTime)
    monkeypatch.setattr(controller, "_log", lambda *a, **k: None)
    monkeypatch.setattr(
        controller,
        "_command_available",
        lambda command: command in {"claude", "codex"},
    )
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))

    store = UsageStore()
    reset_at = frozen_now + timedelta(days=2)
    for model in ("sonnet", "opus"):
        store.record_worker_backend_cooldown(
            worker_backend="claude_code",
            reset_at=reset_at,
            now=frozen_now,
            limit_kind="model_weekly",
            model=model,
        )

    cooldowns: dict = {"claude": None, "opus": None, "codex": None}
    meta: dict = {}
    controller._seed_cooldowns_from_usage_store(cooldowns, meta)

    assert cooldowns["claude"] == reset_at
    assert cooldowns["opus"] == reset_at
    assert meta["claude"]["limit_kind"] == "global_weekly"
    assert meta["claude"]["model"] is None
    assert meta["opus"]["limit_kind"] == "global_weekly"
    assert meta["opus"]["model"] is None
    assert controller._select_backend(cooldowns) == "codex"


def test_classify_limit_kind_model_weekly_for_sonnet() -> None:
    kind, model = controller._classify_limit_kind(
        "claude", "You've hit your Sonnet limit · resets Jun 3, 9am (America/New_York)"
    )
    assert kind == "model_weekly"
    assert model == "sonnet"


def test_classify_limit_kind_session_is_account_wide() -> None:
    kind, model = controller._classify_limit_kind("claude", "5-hour session limit reached")
    assert kind == "session_5h"
    assert model is None


def test_classify_limit_kind_opus_backend_maps_to_opus_model() -> None:
    kind, model = controller._classify_limit_kind("opus", "Opus weekly limit, resets soon")
    assert kind == "model_weekly"
    assert model == "opus"


def test_classify_limit_kind_bare_weekly_is_account_wide() -> None:
    kind, model = controller._classify_limit_kind("claude", "weekly limit, resets soon")
    assert kind == "global_weekly"
    assert model is None


def test_write_runtime_state_emits_limit_kinds(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(controller, "STATE_PATH", state_path)
    reset = datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc)
    cooldowns = {"claude": reset, "opus": None, "codex": None}
    meta = {
        "claude": {
            "limit_kind": "model_weekly",
            "model": "sonnet",
            "reset_at": "2026-06-03T13:00:00Z",
        },
        # Stale entry for a backend no longer cooling — must be dropped.
        "opus": {"limit_kind": "model_weekly", "model": "opus", "reset_at": "x"},
    }
    controller.write_runtime_state(cooldowns, None, limit_meta=meta)

    import json

    state = json.loads(state_path.read_text())
    kinds = state["backend_limit_kinds"]
    assert kinds["claude"]["limit_kind"] == "model_weekly"
    assert kinds["claude"]["model"] == "sonnet"
    assert "opus" not in kinds


def test_write_runtime_state_reports_running_backend_during_sleep(tmp_path, monkeypatch) -> None:
    # When the loop sleeps after running the opus fallback, the state must report
    # the live selection — not null, which previously read as "no runnable backend"
    # even though the loop was healthily running on opus.
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(controller, "STATE_PATH", state_path)
    reset = datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc)
    cooldowns = {"claude": reset, "opus": None, "codex": None}

    controller.write_runtime_state(cooldowns, "opus", sleep_until="2026-05-31T08:19:39Z")

    import json

    state = json.loads(state_path.read_text())
    assert state["runnable_backend"] == "opus"
    assert state["sleeping_until_utc"] == "2026-05-31T08:19:39Z"
