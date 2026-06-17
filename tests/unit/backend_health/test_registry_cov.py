# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from operations_center.backend_health import registry as reg
from operations_center.backend_health.models import (
    BackendHealthRecord,
    BackendHealthState,
    RecoveryStrategy,
)
from operations_center.backend_health.registry import (
    BackendHealthRegistry,
    HealthTransition,
    _extract_exit_code,
    _extract_signal,
    _failure_signature,
    _utcnow,
)


# ---------------------------------------------------------------------------
# Lightweight stand-in for ExecutionResult. The registry only ever reads
# failure_reason, failure_category, and status, so a minimal stub keeps the
# tests hermetic and free of pydantic construction overhead.
# ---------------------------------------------------------------------------


@dataclass
class _Enumish:
    value: str


@dataclass
class _Result:
    failure_reason: str | None = None
    failure_category: _Enumish | None = None
    status: _Enumish | None = None


def _fixed_now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _utcnow
# ---------------------------------------------------------------------------


def test_utcnow_is_timezone_aware_utc():
    now = _utcnow()
    assert now.tzinfo is UTC


# ---------------------------------------------------------------------------
# _extract_signal
# ---------------------------------------------------------------------------


def test_extract_signal_sigkill():
    assert _extract_signal("process received SIGKILL") == "SIGKILL"


def test_extract_signal_sigkill_case_insensitive():
    assert _extract_signal("sigkill happened") == "SIGKILL"


def test_extract_signal_signal_equals_9():
    assert _extract_signal("died signal=9 here") == "SIGKILL"


def test_extract_signal_signal_space_9():
    assert _extract_signal("got signal 9 ok") == "SIGKILL"


def test_extract_signal_sigterm():
    assert _extract_signal("terminated by SIGTERM") == "SIGTERM"


def test_extract_signal_none():
    assert _extract_signal("ordinary error message") is None


def test_extract_signal_sigkill_takes_priority_over_sigterm():
    # SIGKILL branch returns before SIGTERM branch is reached.
    assert _extract_signal("SIGKILL and SIGTERM both") == "SIGKILL"


# ---------------------------------------------------------------------------
# _extract_exit_code
# ---------------------------------------------------------------------------


def test_extract_exit_code_exit_code_token():
    assert _extract_exit_code("failed exit_code=137 done") == 137


def test_extract_exit_code_exit_token():
    assert _extract_exit_code("failed exit=2 trailing") == 2


def test_extract_exit_code_negative():
    assert _extract_exit_code("exit_code=-9") == -9


def test_extract_exit_code_no_token():
    assert _extract_exit_code("nothing relevant here") is None


def test_extract_exit_code_token_present_but_no_digits():
    # Token found, but followed by non-digit -> empty digits -> falls through.
    assert _extract_exit_code("exit_code=abc") is None


def test_extract_exit_code_first_token_no_digits_falls_to_second():
    # "exit_code=" has no digits, "exit=" branch picks up 5.
    assert _extract_exit_code("exit_code=x and exit=5") == 5


def test_extract_exit_code_dash_only_after_digits_stops():
    # A '-' after digits already started is not consumed (breaks the loop).
    assert _extract_exit_code("exit=12-34") == 12


def test_extract_exit_code_stops_at_non_digit():
    assert _extract_exit_code("exit=42rest") == 42


# ---------------------------------------------------------------------------
# _failure_signature
# ---------------------------------------------------------------------------


def test_failure_signature_signal():
    res = _Result(failure_reason="oom SIGKILL detected")
    assert _failure_signature(res) == "signal:SIGKILL"


def test_failure_signature_category_when_no_signal():
    res = _Result(
        failure_reason="some validation problem",
        failure_category=_Enumish("validation_failed"),
    )
    assert _failure_signature(res) == "category:validation_failed"


def test_failure_signature_status_when_no_signal_or_category():
    res = _Result(failure_reason="boom", status=_Enumish("failed"))
    assert _failure_signature(res) == "status:failed"


def test_failure_signature_unknown_fallback():
    res = _Result(failure_reason=None)
    assert _failure_signature(res) == "failure:unknown"


def test_failure_signature_strips_whitespace_reason():
    # Whitespace-only reason -> no signal -> falls to status.
    res = _Result(failure_reason="   ", status=_Enumish("timed_out"))
    assert _failure_signature(res) == "status:timed_out"


# ---------------------------------------------------------------------------
# HealthTransition dataclass defaults
# ---------------------------------------------------------------------------


def test_health_transition_defaults():
    t = HealthTransition(
        backend_id="b1",
        previous=BackendHealthState.UNKNOWN,
        current=BackendHealthState.HEALTHY,
        reason="ok",
    )
    assert t.cooldown_applied is False
    assert t.recovery_strategy is RecoveryStrategy.NONE


# ---------------------------------------------------------------------------
# BackendHealthRegistry.__init__ / get
# ---------------------------------------------------------------------------


def test_init_defaults():
    r = BackendHealthRegistry()
    assert r.cooldown_seconds == 1800
    assert r.unstable_failure_threshold == 2
    assert r.unavailable_failure_threshold == 5
    assert r.records == {}


def test_init_custom_values():
    r = BackendHealthRegistry(
        cooldown_seconds=10,
        unstable_failure_threshold=1,
        unavailable_failure_threshold=3,
    )
    assert r.cooldown_seconds == 10
    assert r.unstable_failure_threshold == 1
    assert r.unavailable_failure_threshold == 3


def test_get_returns_default_record_for_unknown_backend():
    r = BackendHealthRegistry()
    rec = r.get("missing")
    assert isinstance(rec, BackendHealthRecord)
    assert rec.backend_id == "missing"
    assert rec.state is BackendHealthState.UNKNOWN
    # Not persisted by get().
    assert "missing" not in r.records


def test_get_returns_stored_record():
    r = BackendHealthRegistry()
    stored = BackendHealthRecord(backend_id="b", state=BackendHealthState.HEALTHY)
    r.records["b"] = stored
    assert r.get("b") is stored


# ---------------------------------------------------------------------------
# record_success
# ---------------------------------------------------------------------------


def test_record_success_resets_record():
    r = BackendHealthRegistry()
    # Seed a degraded record with cruft to be cleared.
    r.records["b"] = BackendHealthRecord(
        backend_id="b",
        state=BackendHealthState.UNAVAILABLE,
        failure_count=7,
        cooldown_until=_fixed_now(),
        safe_retry_after=_fixed_now(),
        recovery_strategy=RecoveryStrategy.ESCALATE,
        operator_blocked_reason="blocked",
    )
    now = _fixed_now()
    rec, trans = r.record_success("b", now=now)

    assert rec.state is BackendHealthState.HEALTHY
    assert rec.failure_count == 0
    assert rec.last_success_at == now
    assert rec.cooldown_until is None
    assert rec.safe_retry_after is None
    assert rec.recovery_strategy is RecoveryStrategy.NONE
    assert rec.operator_blocked_reason is None
    assert r.records["b"] is rec

    assert trans.previous is BackendHealthState.UNAVAILABLE
    assert trans.current is BackendHealthState.HEALTHY
    assert trans.reason == "execution_success"


def test_record_success_uses_utcnow_when_now_none(monkeypatch):
    r = BackendHealthRegistry()
    sentinel = _fixed_now()
    monkeypatch.setattr(reg, "_utcnow", lambda: sentinel)
    rec, _ = r.record_success("b")
    assert rec.last_success_at == sentinel


# ---------------------------------------------------------------------------
# record_failure
# ---------------------------------------------------------------------------


def test_record_failure_first_failure_degraded():
    r = BackendHealthRegistry()
    now = _fixed_now()
    res = _Result(failure_reason="exit_code=1 generic", status=_Enumish("failed"))
    rec, trans = r.record_failure("b", res, now=now)

    assert rec.state is BackendHealthState.DEGRADED
    assert rec.failure_count == 1
    assert rec.recovery_strategy is RecoveryStrategy.RETRY_AFTER_COOLDOWN
    assert rec.cooldown_until is None
    assert rec.last_failure is not None
    assert rec.last_failure.signature == "status:failed"
    assert rec.last_failure.exit_code == 1
    assert rec.last_failure.signal is None
    assert rec.last_failure.timestamp == now
    assert rec.last_failure.reason == "exit_code=1 generic"

    assert trans.previous is BackendHealthState.UNKNOWN
    assert trans.current is BackendHealthState.DEGRADED
    assert trans.cooldown_applied is False
    assert trans.recovery_strategy is RecoveryStrategy.RETRY_AFTER_COOLDOWN
    assert trans.reason == "status:failed"


def test_record_failure_reaches_unstable_threshold():
    r = BackendHealthRegistry(unstable_failure_threshold=2)
    now = _fixed_now()
    res = _Result(failure_reason="boom", status=_Enumish("failed"))
    r.record_failure("b", res, now=now)
    rec, trans = r.record_failure("b", res, now=now)

    assert rec.failure_count == 2
    assert rec.state is BackendHealthState.UNSTABLE
    assert trans.current is BackendHealthState.UNSTABLE


def test_record_failure_reaches_unavailable_threshold():
    r = BackendHealthRegistry(
        unstable_failure_threshold=2,
        unavailable_failure_threshold=3,
    )
    now = _fixed_now()
    res = _Result(failure_reason="boom", status=_Enumish("failed"))
    r.record_failure("b", res, now=now)
    r.record_failure("b", res, now=now)
    rec, trans = r.record_failure("b", res, now=now)

    assert rec.failure_count == 3
    assert rec.state is BackendHealthState.UNAVAILABLE
    assert rec.recovery_strategy is RecoveryStrategy.ESCALATE
    assert trans.recovery_strategy is RecoveryStrategy.ESCALATE


def test_record_failure_sigkill_sets_cooldown_and_unstable():
    r = BackendHealthRegistry(cooldown_seconds=900)
    now = _fixed_now()
    res = _Result(failure_reason="killed by SIGKILL exit_code=-9")
    rec, trans = r.record_failure("b", res, now=now)

    assert rec.state is BackendHealthState.UNSTABLE
    expected_cooldown = now + timedelta(seconds=900)
    assert rec.cooldown_until == expected_cooldown
    assert rec.safe_retry_after == expected_cooldown
    assert rec.recovery_strategy is RecoveryStrategy.REDUCE_PRESSURE
    assert rec.last_failure.signal == "SIGKILL"
    assert rec.last_failure.exit_code == -9
    assert rec.last_failure.signature == "signal:SIGKILL"

    assert trans.cooldown_applied is True
    assert trans.recovery_strategy is RecoveryStrategy.REDUCE_PRESSURE


def test_record_failure_sigkill_overrides_high_failure_count():
    # Even past the unavailable threshold, SIGKILL branch wins (checked first).
    r = BackendHealthRegistry(
        cooldown_seconds=60,
        unstable_failure_threshold=1,
        unavailable_failure_threshold=2,
    )
    now = _fixed_now()
    r.records["b"] = BackendHealthRecord(backend_id="b", failure_count=10)
    res = _Result(failure_reason="SIGKILL boom")
    rec, _ = r.record_failure("b", res, now=now)
    assert rec.failure_count == 11
    assert rec.state is BackendHealthState.UNSTABLE
    assert rec.cooldown_until == now + timedelta(seconds=60)


def test_record_failure_preserves_existing_cooldown_on_non_sigkill():
    r = BackendHealthRegistry()
    now = _fixed_now()
    prior_cooldown = now - timedelta(hours=1)
    prior_safe = now - timedelta(minutes=30)
    r.records["b"] = BackendHealthRecord(
        backend_id="b",
        cooldown_until=prior_cooldown,
        safe_retry_after=prior_safe,
    )
    res = _Result(failure_reason="generic failure", status=_Enumish("failed"))
    rec, _ = r.record_failure("b", res, now=now)
    # Non-SIGKILL path keeps the previous cooldown/safe_retry values.
    assert rec.cooldown_until == prior_cooldown
    assert rec.safe_retry_after == prior_safe


def test_record_failure_none_reason_unknown_signature():
    r = BackendHealthRegistry()
    now = _fixed_now()
    res = _Result(failure_reason=None)
    rec, trans = r.record_failure("b", res, now=now)
    assert rec.last_failure.signature == "failure:unknown"
    assert rec.last_failure.exit_code is None
    assert rec.last_failure.signal is None
    assert rec.last_failure.reason is None
    assert trans.reason == "failure:unknown"


def test_record_failure_uses_utcnow_when_now_none(monkeypatch):
    r = BackendHealthRegistry()
    sentinel = _fixed_now()
    monkeypatch.setattr(reg, "_utcnow", lambda: sentinel)
    res = _Result(failure_reason="boom", status=_Enumish("failed"))
    rec, _ = r.record_failure("b", res)
    assert rec.last_failure.timestamp == sentinel


# ---------------------------------------------------------------------------
# start_recovery
# ---------------------------------------------------------------------------


def test_start_recovery_increments_attempt_and_sets_state():
    r = BackendHealthRegistry()
    r.records["b"] = BackendHealthRecord(
        backend_id="b",
        state=BackendHealthState.UNAVAILABLE,
        recovery_attempt_count=2,
    )
    rec, trans = r.start_recovery("b", strategy=RecoveryStrategy.RESTART_BACKEND)

    assert rec.state is BackendHealthState.RECOVERING
    assert rec.recovery_attempt_count == 3
    assert rec.recovery_strategy is RecoveryStrategy.RESTART_BACKEND
    assert r.records["b"] is rec

    assert trans.previous is BackendHealthState.UNAVAILABLE
    assert trans.current is BackendHealthState.RECOVERING
    assert trans.reason == "recovery_attempt_started"
    assert trans.recovery_strategy is RecoveryStrategy.RESTART_BACKEND


def test_start_recovery_on_unknown_backend():
    r = BackendHealthRegistry()
    rec, trans = r.start_recovery("new", strategy=RecoveryStrategy.RESTART_WATCHER)
    assert rec.recovery_attempt_count == 1
    assert trans.previous is BackendHealthState.UNKNOWN


# ---------------------------------------------------------------------------
# mark_operator_blocked
# ---------------------------------------------------------------------------


def test_mark_operator_blocked_sets_state_and_reason():
    r = BackendHealthRegistry()
    r.records["b"] = BackendHealthRecord(backend_id="b", state=BackendHealthState.UNSTABLE)
    rec, trans = r.mark_operator_blocked("b", "manual hold")

    assert rec.state is BackendHealthState.OPERATOR_BLOCKED
    assert rec.recovery_strategy is RecoveryStrategy.ESCALATE
    assert rec.operator_blocked_reason == "manual hold"
    assert r.records["b"] is rec

    assert trans.previous is BackendHealthState.UNSTABLE
    assert trans.current is BackendHealthState.OPERATOR_BLOCKED
    assert trans.reason == "manual hold"
    assert trans.recovery_strategy is RecoveryStrategy.ESCALATE


# ---------------------------------------------------------------------------
# Sequencing / integration across methods
# ---------------------------------------------------------------------------


def test_failure_then_success_resets_counter():
    r = BackendHealthRegistry(unstable_failure_threshold=2)
    now = _fixed_now()
    res = _Result(failure_reason="boom", status=_Enumish("failed"))
    r.record_failure("b", res, now=now)
    r.record_failure("b", res, now=now)
    assert r.get("b").state is BackendHealthState.UNSTABLE

    rec, trans = r.record_success("b", now=now)
    assert rec.failure_count == 0
    assert rec.state is BackendHealthState.HEALTHY
    assert trans.previous is BackendHealthState.UNSTABLE
