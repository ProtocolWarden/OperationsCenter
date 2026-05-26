# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from operations_center.backends.worker_backend_selector import (
    execute_with_worker_backend_round_robin,
    parse_worker_backend_reset,
    select_worker_backend,
)


def _usage_store() -> SimpleNamespace:
    return SimpleNamespace(
        worker_backend_cooldown_until=lambda worker_backend, *, now: None,
        record_worker_backend_cooldown=lambda worker_backend, reset_at, now: None,
    )


def test_select_worker_backend_prefers_alternate_when_preferred_cooling_down() -> None:
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)

    def _cooldown(worker_backend: str, *, now):
        if worker_backend == "claude_code":
            return datetime(2026, 5, 25, 17, 0, tzinfo=UTC)
        return None

    usage_store = _usage_store()
    usage_store.worker_backend_cooldown_until = _cooldown

    selection = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=usage_store,
        dynamic_enabled=True,
        now=now,
    )

    assert selection.selected_backend == "codex_cli"


def test_parse_worker_backend_reset_handles_relative_message() -> None:
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)

    reset_at = parse_worker_backend_reset(
        "codex usage limit hit, please try again in 5h 0m",
        "codex_cli",
        now=now,
    )

    assert reset_at == datetime(2026, 5, 25, 21, 0, tzinfo=UTC)


def test_execute_with_worker_backend_round_robin_retries_on_capacity_limit() -> None:
    cooldowns: dict[str, datetime | None] = {"claude_code": None, "codex_cli": None}
    calls: list[str] = []

    def _cooldown_until(worker_backend: str, *, now):
        return cooldowns.get(worker_backend)

    def _record(worker_backend: str, reset_at, now) -> None:
        cooldowns[worker_backend] = reset_at

    usage_store = _usage_store()
    usage_store.worker_backend_cooldown_until = _cooldown_until
    usage_store.record_worker_backend_cooldown = _record

    def _run_once(worker_backend: str):
        calls.append(worker_backend)
        if worker_backend == "claude_code":
            return {"status": "failed", "error_summary": "usage limit hit, please try again in 5h 0m"}
        return {"status": "succeeded", "error_summary": None}

    executed = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=usage_store,
        dynamic_enabled=True,
        execute_once=_run_once,
        failed=lambda payload: payload["status"] != "succeeded",
        failure_text=lambda payload: payload.get("error_summary"),
    )

    assert executed.selected_backend == "codex_cli"
    assert executed.fallback_used is True
    assert calls == ["claude_code", "codex_cli"]
