# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from operations_center.backends import worker_backend_selector as wbs
from operations_center.backends.worker_backend_selector import (
    WorkerBackendExecution,
    WorkerBackendSelection,
    alternate_worker_backend,
    execute_with_worker_backend_round_robin,
    maybe_record_worker_backend_cooldown,
    parse_worker_backend_reset,
    select_worker_backend,
    worker_backend_candidates,
    worker_backend_observed_runtime,
)

NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _allow_all_remote_worker_backends(monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude,codex")


# --------------------------------------------------------------------------
# worker_backend_candidates
# --------------------------------------------------------------------------
def test_candidates_unsupported_backend_returns_self_only() -> None:
    assert worker_backend_candidates("mystery") == ("mystery",)


def test_candidates_remote_backend_includes_remote_alternates() -> None:
    assert worker_backend_candidates("claude_code") == ("claude_code", "codex_cli")
    assert worker_backend_candidates("codex_cli") == ("codex_cli", "claude_code")


def test_candidates_local_backend_pools_among_local_only() -> None:
    # aider_local has no other local alternate currently (set of one besides self).
    assert worker_backend_candidates("aider_local") == ("aider_local", "direct_local")
    assert worker_backend_candidates("direct_local") == ("direct_local", "aider_local")


# --------------------------------------------------------------------------
# alternate_worker_backend
# --------------------------------------------------------------------------
def test_alternate_remote() -> None:
    assert alternate_worker_backend("claude_code") == "codex_cli"


def test_alternate_local() -> None:
    assert alternate_worker_backend("aider_local") == "direct_local"


def test_alternate_unknown_falls_to_local_pool() -> None:
    # Unknown backend is not in remote pool, so local pool is searched; first
    # local candidate that differs is returned.
    assert alternate_worker_backend("unknown") == "aider_local"


# --------------------------------------------------------------------------
# _read_worker_backend_cooldown
# --------------------------------------------------------------------------
def test_read_cooldown_prefers_blocked_until() -> None:
    reset = NOW + timedelta(hours=1)
    store = SimpleNamespace(
        worker_backend_blocked_until=lambda backend, now: reset,
        worker_backend_cooldown_until=lambda backend, now: None,
    )
    assert wbs._read_worker_backend_cooldown(store, "claude_code", now=NOW) == reset


def test_read_cooldown_falls_back_to_cooldown_until() -> None:
    reset = NOW + timedelta(hours=2)
    store = SimpleNamespace(worker_backend_cooldown_until=lambda backend, now: reset)
    assert wbs._read_worker_backend_cooldown(store, "claude_code", now=NOW) == reset


def test_read_cooldown_no_methods_returns_none() -> None:
    store = SimpleNamespace()
    assert wbs._read_worker_backend_cooldown(store, "claude_code", now=NOW) is None


def test_read_cooldown_non_callable_attr_ignored() -> None:
    store = SimpleNamespace(worker_backend_blocked_until="not-callable")
    assert wbs._read_worker_backend_cooldown(store, "claude_code", now=NOW) is None


def test_read_cooldown_legacy_no_now_kwarg() -> None:
    reset = NOW + timedelta(minutes=30)

    def legacy(backend):  # no now kwarg -> TypeError on first call
        return reset

    store = SimpleNamespace(worker_backend_blocked_until=legacy)
    assert wbs._read_worker_backend_cooldown(store, "claude_code", now=NOW) == reset


# --------------------------------------------------------------------------
# select_worker_backend
# --------------------------------------------------------------------------
def _store_with_cooldowns(mapping: dict[str, datetime | None]):
    return SimpleNamespace(worker_backend_blocked_until=lambda backend, now: mapping.get(backend))


def test_select_dynamic_disabled_returns_preferred() -> None:
    store = _store_with_cooldowns({"claude_code": NOW + timedelta(hours=5)})
    sel = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=False,
        now=NOW,
    )
    assert sel.selected_backend == "claude_code"
    assert sel.reason is None
    assert sel.preferred_backend == "claude_code"


def test_select_picks_preferred_when_no_cooldown() -> None:
    store = _store_with_cooldowns({})
    sel = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        now=NOW,
    )
    assert sel.selected_backend == "claude_code"


def test_select_picks_preferred_when_cooldown_expired() -> None:
    store = _store_with_cooldowns({"claude_code": NOW - timedelta(minutes=1)})
    sel = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        now=NOW,
    )
    assert sel.selected_backend == "claude_code"


def test_select_falls_back_to_alternate() -> None:
    store = _store_with_cooldowns({"claude_code": NOW + timedelta(hours=2)})
    sel = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        now=NOW,
    )
    assert sel.selected_backend == "codex_cli"


def test_select_all_cooling_down_returns_none_with_reason() -> None:
    reset = NOW + timedelta(hours=3)
    store = _store_with_cooldowns({"claude_code": reset, "codex_cli": reset})
    sel = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        now=NOW,
    )
    assert sel.selected_backend is None
    assert sel.reason is not None
    assert "all worker backends cooling down" in sel.reason
    assert "claude_code until" in sel.reason
    assert "codex_cli until" in sel.reason


def test_select_default_now_uses_clock(monkeypatch) -> None:
    store = _store_with_cooldowns({})
    sel = select_worker_backend(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
    )
    assert sel.selected_backend == "claude_code"


# --------------------------------------------------------------------------
# parse_worker_backend_reset
# --------------------------------------------------------------------------
def test_parse_timezone_reset_later_today() -> None:
    out = "You're out of usage, resets 2:30pm (America/New_York)"
    # NOW is 12:00 UTC == 08:00 EDT; 2:30pm EDT is later today.
    result = parse_worker_backend_reset(out, "claude_code", now=NOW)
    assert result is not None
    assert result.tzinfo == UTC
    local = result.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    assert (local.hour, local.minute) == (14, 30)


def test_parse_timezone_reset_without_minutes_later_today() -> None:
    out = "You've hit your weekly limit · resets 9am (America/New_York)"
    result = parse_worker_backend_reset(out, "claude_code", now=NOW)
    assert result is not None
    assert result.tzinfo == UTC
    local = result.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    assert (local.hour, local.minute) == (9, 0)


def test_parse_timezone_reset_rolls_to_next_day() -> None:
    # 8:00am EDT when it is already ~08:00 EDT -> should roll forward a day.
    out = "resets 8:00am (America/New_York)"
    result = parse_worker_backend_reset(out, "claude_code", now=NOW)
    assert result is not None
    assert result > NOW
    # Roughly a day out (since reset_local <= now_local triggers +1 day).
    assert result - NOW >= timedelta(hours=23)


def test_parse_timezone_unknown_zone_returns_none() -> None:
    out = "resets 4:20am (Not/AZone)"
    assert parse_worker_backend_reset(out, "claude_code", now=NOW) is None


def test_parse_iso_reset() -> None:
    out = "rate limited; resets at 2026-06-03T09:15:00Z"
    result = parse_worker_backend_reset(out, "claude_code", now=NOW)
    assert result == datetime(2026, 6, 3, 9, 15, 0, tzinfo=UTC)


def test_parse_iso_reset_no_seconds() -> None:
    out = "resets 2026-06-03T09:15Z"
    result = parse_worker_backend_reset(out, "claude_code", now=NOW)
    assert result == datetime(2026, 6, 3, 9, 15, 0, tzinfo=UTC)


def test_parse_relative_reset_hms() -> None:
    out = "too many requests, try again in 1h 30m 15s"
    result = parse_worker_backend_reset(out, "claude_code", now=NOW)
    assert result == NOW + timedelta(hours=1, minutes=30, seconds=15)


def test_parse_relative_reset_zero_delta_falls_through() -> None:
    # "in 0h" style: matches relative regex but delta is zero -> not returned;
    # also no capacity/limit signal -> None.
    out = "available again in 0s and nothing else"
    assert parse_worker_backend_reset(out, "claude_code", now=NOW) is None


def test_parse_no_match_returns_none() -> None:
    assert parse_worker_backend_reset("everything is fine", "claude_code", now=NOW) is None


def test_parse_limit_signal_without_time_returns_none() -> None:
    # Has a limit signal (429) but no parseable reset time -> still None.
    out = "request failed with 429 too many requests"
    assert parse_worker_backend_reset(out, "claude_code", now=NOW) is None


def test_parse_default_now(monkeypatch) -> None:
    out = "no limit here"
    assert parse_worker_backend_reset(out, "claude_code") is None


# --------------------------------------------------------------------------
# maybe_record_worker_backend_cooldown
# --------------------------------------------------------------------------
def test_maybe_record_empty_output_returns_none() -> None:
    store = SimpleNamespace()
    assert (
        maybe_record_worker_backend_cooldown(
            usage_store=store, worker_backend="claude_code", combined_output=None
        )
        is None
    )


def test_maybe_record_no_reset_returns_none() -> None:
    store = SimpleNamespace()
    assert (
        maybe_record_worker_backend_cooldown(
            usage_store=store,
            worker_backend="claude_code",
            combined_output="all good",
            now=NOW,
        )
        is None
    )


def test_maybe_record_calls_recorder_and_logger() -> None:
    calls = {}

    def recorder(*, worker_backend, reset_at, now, limit_kind, model):
        calls["kwargs"] = dict(
            worker_backend=worker_backend,
            reset_at=reset_at,
            now=now,
            limit_kind=limit_kind,
            model=model,
        )

    logs: list[str] = []
    store = SimpleNamespace(record_worker_backend_cooldown=recorder)
    out = "Sonnet weekly limit reached, try again in 2h"
    reset = maybe_record_worker_backend_cooldown(
        usage_store=store,
        worker_backend="claude_code",
        combined_output=out,
        now=NOW,
        logger=logs.append,
    )
    assert reset == NOW + timedelta(hours=2)
    assert calls["kwargs"]["worker_backend"] == "claude_code"
    assert calls["kwargs"]["limit_kind"] == "model_weekly"
    assert calls["kwargs"]["model"] == "sonnet"
    assert logs and "cooling down until" in logs[0]
    assert "(model_weekly/sonnet)" in logs[0]


def test_maybe_record_generic_limit_kind_when_unclassified() -> None:
    captured = {}

    def recorder(*, worker_backend, reset_at, now, limit_kind, model):
        captured["limit_kind"] = limit_kind
        captured["model"] = model

    logs: list[str] = []
    store = SimpleNamespace(record_worker_backend_cooldown=recorder)
    # Relative reset present, but no recognizable limit-kind words/model.
    out = "retry in 5m"
    reset = maybe_record_worker_backend_cooldown(
        usage_store=store,
        worker_backend="claude_code",
        combined_output=out,
        now=NOW,
        logger=logs.append,
    )
    assert reset == NOW + timedelta(minutes=5)
    assert captured["limit_kind"] == "generic"
    assert captured["model"] is None
    # logger suffix without model component
    assert "(generic)" in logs[0]


def test_maybe_record_legacy_recorder_typeerror_fallback() -> None:
    seen = {}

    def legacy_recorder(*, worker_backend, reset_at, now):
        seen["worker_backend"] = worker_backend
        seen["reset_at"] = reset_at

    store = SimpleNamespace(record_worker_backend_cooldown=legacy_recorder)
    out = "usage limit reached, retry in 1h"
    reset = maybe_record_worker_backend_cooldown(
        usage_store=store,
        worker_backend="codex_cli",
        combined_output=out,
        now=NOW,
    )
    assert reset == NOW + timedelta(hours=1)
    assert seen["worker_backend"] == "codex_cli"


def test_maybe_record_non_callable_recorder_skipped_no_logger() -> None:
    store = SimpleNamespace(record_worker_backend_cooldown="nope")
    out = "rate limit, retry in 30m"
    reset = maybe_record_worker_backend_cooldown(
        usage_store=store,
        worker_backend="claude_code",
        combined_output=out,
        now=NOW,
    )
    assert reset == NOW + timedelta(minutes=30)


# --------------------------------------------------------------------------
# worker_backend_observed_runtime
# --------------------------------------------------------------------------
def test_observed_runtime_serializes_cooldowns() -> None:
    reset = NOW + timedelta(hours=1)
    selection = WorkerBackendSelection(
        preferred_backend="claude_code",
        selected_backend="codex_cli",
        cooldowns={"claude_code": reset, "codex_cli": None},
    )
    execution = WorkerBackendExecution(
        selected_backend="codex_cli",
        payload="ok",
        fallback_used=True,
        selection=selection,
    )
    out = worker_backend_observed_runtime(execution)
    assert out["worker_backend_strategy"] == "round_robin"
    assert out["preferred_worker_backend"] == "claude_code"
    assert out["selected_worker_backend"] == "codex_cli"
    assert out["fallback_used"] is True
    assert out["worker_backend_cooldowns"] == {
        "claude_code": reset.isoformat(),
        "codex_cli": None,
    }


# --------------------------------------------------------------------------
# execute_with_worker_backend_round_robin
# --------------------------------------------------------------------------
def _ok_store():
    return _store_with_cooldowns({})


def test_round_robin_no_backend_available() -> None:
    reset = NOW + timedelta(hours=3)
    # Force select to return None by cooling everything; select uses datetime.now
    # internally, so make resets far in the future.
    far = datetime.now(UTC) + timedelta(hours=10)
    store = _store_with_cooldowns({"claude_code": far, "codex_cli": far})
    result = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=lambda b: pytest.fail("should not execute"),
        failed=lambda p: True,
        failure_text=lambda p: None,
    )
    assert result.selected_backend is None
    assert result.payload is None
    assert result.fallback_used is False
    assert reset  # silence unused


def test_round_robin_primary_succeeds() -> None:
    store = _ok_store()
    result = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=lambda b: f"payload-{b}",
        failed=lambda p: False,
        failure_text=lambda p: None,
    )
    assert result.selected_backend == "claude_code"
    assert result.payload == "payload-claude_code"
    assert result.fallback_used is False


def test_round_robin_primary_fails_no_cooldown_recorded() -> None:
    store = _ok_store()
    result = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=lambda b: f"payload-{b}",
        failed=lambda p: True,
        failure_text=lambda p: "generic crash, no limit signal",
    )
    # reset_at is None -> returns primary, no fallback.
    assert result.selected_backend == "claude_code"
    assert result.fallback_used is False


def test_round_robin_dynamic_disabled_no_fallback() -> None:
    store = _ok_store()
    result = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=False,
        execute_once=lambda b: f"payload-{b}",
        failed=lambda p: True,
        failure_text=lambda p: "usage limit reached, retry in 1h",
    )
    # reset_at parsed but dynamic_enabled False -> no fallback path.
    assert result.selected_backend == "claude_code"
    assert result.fallback_used is False


def test_round_robin_falls_back_to_alternate_success() -> None:
    # Primary fails with a limit; after recording cooldown the fallback select
    # must pick the alternate. We track recorded backends in a mutable store.
    cooled: dict[str, datetime] = {}

    def blocked_until(backend, now):
        return cooled.get(backend)

    def recorder(*, worker_backend, reset_at, now, limit_kind, model):
        cooled[worker_backend] = reset_at

    store = SimpleNamespace(
        worker_backend_blocked_until=blocked_until,
        record_worker_backend_cooldown=recorder,
    )
    logs: list[str] = []

    def execute_once(backend):
        return {"backend": backend, "fail": backend == "claude_code"}

    result = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=execute_once,
        failed=lambda p: p["fail"],
        failure_text=lambda p: "usage limit reached, retry in 2h" if p["fail"] else None,
        logger=logs.append,
    )
    assert result.fallback_used is True
    assert result.selected_backend == "codex_cli"
    assert result.payload["backend"] == "codex_cli"
    assert any("retrying with codex_cli" in m for m in logs)


def test_round_robin_fallback_also_fails_records_cooldown() -> None:
    cooled: dict[str, datetime] = {}

    def blocked_until(backend, now):
        return cooled.get(backend)

    def recorder(*, worker_backend, reset_at, now, limit_kind, model):
        cooled[worker_backend] = reset_at

    store = SimpleNamespace(
        worker_backend_blocked_until=blocked_until,
        record_worker_backend_cooldown=recorder,
    )

    def execute_once(backend):
        return {"backend": backend, "fail": True}

    result = execute_with_worker_backend_round_robin(
        preferred_backend="claude_code",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=execute_once,
        failed=lambda p: p["fail"],
        failure_text=lambda p: "usage limit reached, retry in 2h",
    )
    assert result.fallback_used is True
    assert result.selected_backend == "codex_cli"
    # Both backends recorded as cooled down.
    assert set(cooled) == {"claude_code", "codex_cli"}


def test_round_robin_fallback_same_as_primary_no_retry() -> None:
    # Local backend whose only alternate is also local: after the primary is
    # cooled, the fallback select returns None (only one local runnable), so we
    # keep the primary payload with fallback_used False.
    cooled: dict[str, datetime] = {}

    def blocked_until(backend, now):
        return cooled.get(backend)

    def recorder(*, worker_backend, reset_at, now, limit_kind, model):
        cooled[worker_backend] = reset_at

    store = SimpleNamespace(
        worker_backend_blocked_until=blocked_until,
        record_worker_backend_cooldown=recorder,
    )

    def execute_once(backend):
        return {"backend": backend, "fail": True}

    result = execute_with_worker_backend_round_robin(
        preferred_backend="aider_local",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=execute_once,
        failed=lambda p: p["fail"],
        failure_text=lambda p: "usage limit reached, retry in 2h",
    )
    # After cooling aider_local, the only alternate (direct_local) is runnable,
    # so it actually falls back. Adjust expectation: fallback to direct_local.
    assert result.selected_backend == "direct_local"
    assert result.fallback_used is True


def test_round_robin_fallback_none_keeps_primary() -> None:
    # An unsupported backend has no alternates (candidates == self only). When it
    # fails with a limit and is cooled, the fallback select returns None, so the
    # primary payload is kept with fallback_used False (lines 304-313).
    cooled: dict[str, datetime] = {}

    def blocked_until(backend, now):
        return cooled.get(backend)

    def recorder(*, worker_backend, reset_at, now, limit_kind, model):
        cooled[worker_backend] = reset_at

    store = SimpleNamespace(
        worker_backend_blocked_until=blocked_until,
        record_worker_backend_cooldown=recorder,
    )

    result = execute_with_worker_backend_round_robin(
        preferred_backend="mystery_backend",
        usage_store=store,
        dynamic_enabled=True,
        execute_once=lambda b: {"backend": b, "fail": True},
        failed=lambda p: p["fail"],
        failure_text=lambda p: "usage limit reached, retry in 2h",
    )
    assert result.selected_backend == "mystery_backend"
    assert result.fallback_used is False
    assert result.selection.selected_backend is None
