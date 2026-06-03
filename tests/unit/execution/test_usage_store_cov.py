# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.execution import usage_store as us_mod
from operations_center.execution.usage_store import UsageStore, _check_disk_space, _get_lock


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> UsageStore:
    return UsageStore(path=tmp_path / "usage.json")


def _ts(now: datetime, **delta) -> str:
    return (now + timedelta(**delta)).isoformat()


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------
def test_get_lock_caches_same_lock(tmp_path: Path) -> None:
    p = tmp_path / "u.json"
    a = _get_lock(p)
    b = _get_lock(p)
    assert a is b


def test_check_disk_space_ok(tmp_path: Path) -> None:
    # Plenty of free space normally -> no raise, returns None.
    assert _check_disk_space(tmp_path / "u.json") is None


def test_check_disk_space_oserror_swallowed(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def boom(_p):
        calls.append(_p)
        raise OSError("nope")

    monkeypatch.setattr(us_mod.shutil, "disk_usage", boom)
    # Should not raise — can't check, so allow the write (returns None).
    assert _check_disk_space(tmp_path / "u.json") is None
    # The OSError path was actually exercised.
    assert len(calls) == 1


class _Usage:
    def __init__(self, free_bytes: int) -> None:
        self.free = free_bytes


def test_check_disk_space_critical_raises(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(us_mod.shutil, "disk_usage", lambda _p: _Usage(10 * 1024 * 1024))
    with pytest.raises(OSError, match="disk_space_critical"):
        _check_disk_space(tmp_path / "u.json")


def test_check_disk_space_warn_logs(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setattr(us_mod.shutil, "disk_usage", lambda _p: _Usage(100 * 1024 * 1024))
    with caplog.at_level("WARNING"):
        _check_disk_space(tmp_path / "u.json")
    assert any("disk_space_low" in r.getMessage() for r in caplog.records)


def test_check_disk_space_dir_path(monkeypatch, tmp_path: Path) -> None:
    # Cover the path.is_dir() branch (use the dir itself, not parent).
    captured = {}

    def fake(p):
        captured["p"] = p
        return _Usage(500 * 1024 * 1024)

    monkeypatch.setattr(us_mod.shutil, "disk_usage", fake)
    _check_disk_space(tmp_path)
    assert captured["p"] == tmp_path


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------
def test_init_default_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "d.json"))
    s = UsageStore()
    assert s.path == tmp_path / "d.json"


def test_load_missing_returns_default(store: UsageStore) -> None:
    data = store.load()
    assert data["events"] == []
    assert data["hourly_exec_count"] == 0
    assert data["task_attempts"] == {}


def test_load_existing(store: UsageStore) -> None:
    store.path.write_text(json.dumps({"events": [], "marker": 1}), encoding="utf-8")
    assert store.load()["marker"] == 1


def test_save_writes_counts_and_prunes(store: UsageStore) -> None:
    old = (NOW - timedelta(days=10)).isoformat()
    data = {
        "events": [
            {"kind": "execution", "timestamp": NOW.isoformat()},
            {"kind": "skip_budget", "timestamp": NOW.isoformat()},
            {"kind": "skip_noop", "timestamp": NOW.isoformat()},
            {"kind": "skip_cooldown", "timestamp": NOW.isoformat()},
            {"kind": "retry_cap_block", "timestamp": NOW.isoformat()},
            {"kind": "proposal_budget_suppressed", "timestamp": NOW.isoformat()},
            {"kind": "execution", "timestamp": old},  # pruned
        ]
    }
    store.save(data, now=NOW)
    out = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(out["events"]) == 6  # old pruned
    assert out["hourly_exec_count"] == 1
    assert out["daily_exec_count"] == 1
    assert out["skipped_due_to_budget"] == 1
    assert out["skipped_due_to_noop"] == 1
    assert out["skipped_due_to_cooldown"] == 1
    assert out["blocked_due_to_retry_cap"] == 1
    assert out["suppressed_due_to_proposal_budget"] == 1
    assert out["updated_at"] == NOW.isoformat()


# ---------------------------------------------------------------------------
# budget_decision
# ---------------------------------------------------------------------------
def test_budget_decision_allowed_empty(store: UsageStore) -> None:
    assert store.budget_decision(now=NOW).allowed


def test_budget_decision_hourly_exceeded(store: UsageStore, monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", "2")
    s = UsageStore(path=store.path)
    evs = [{"kind": "execution", "timestamp": _ts(NOW, minutes=-i)} for i in range(3)]
    s.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = s.budget_decision(now=NOW)
    assert not d.allowed and d.window == "hourly" and d.reason == "budget_exceeded"


def test_budget_decision_daily_exceeded(store: UsageStore, monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", "100")
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_DAY", "2")
    s = UsageStore(path=store.path)
    evs = [{"kind": "execution", "timestamp": _ts(NOW, hours=-i - 2)} for i in range(3)]
    s.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = s.budget_decision(now=NOW)
    assert not d.allowed and d.window == "daily"


def test_budget_decision_circuit_breaker_open(store: UsageStore) -> None:
    evs = [
        {"kind": "execution_outcome", "succeeded": False, "timestamp": _ts(NOW, minutes=-i)}
        for i in range(4)
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = store.budget_decision(now=NOW)
    assert not d.allowed and d.reason == "circuit_breaker_open"


def test_budget_decision_circuit_breaker_stale_ignored(store: UsageStore) -> None:
    # All failures older than staleness window -> aged out -> allowed.
    evs = [
        {"kind": "execution_outcome", "succeeded": False, "timestamp": _ts(NOW, hours=-5 - i)}
        for i in range(4)
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.budget_decision(now=NOW).allowed


def test_budget_decision_circuit_breaker_mixed_versions_skipped(store: UsageStore) -> None:
    evs = [
        {
            "kind": "execution_outcome",
            "succeeded": False,
            "backend_version": str(v),
            "timestamp": _ts(NOW, minutes=-i),
        }
        for i, v in enumerate([1, 1, 2, 2])
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    # Two versions in window -> skip CB -> allowed.
    assert store.budget_decision(now=NOW).allowed


def test_budget_decision_too_few_outcomes(store: UsageStore) -> None:
    evs = [
        {"kind": "execution_outcome", "succeeded": False, "timestamp": _ts(NOW, minutes=-i)}
        for i in range(2)
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.budget_decision(now=NOW).allowed


# ---------------------------------------------------------------------------
# remaining_exec_capacity
# ---------------------------------------------------------------------------
def test_remaining_exec_capacity(store: UsageStore, monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", "10")
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_DAY", "50")
    s = UsageStore(path=store.path)
    evs = [{"kind": "execution", "timestamp": _ts(NOW, minutes=-1)} for _ in range(3)]
    s.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert s.remaining_exec_capacity(now=NOW) == 7  # 10-3 hourly is the min


# ---------------------------------------------------------------------------
# retry_decision + helpers
# ---------------------------------------------------------------------------
def test_retry_decision_allowed(store: UsageStore) -> None:
    d = store.retry_decision(task_id="t1", now=NOW)
    assert d.allowed and d.attempts == 0


def test_retry_decision_capped_recent(store: UsageStore, monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "2")
    s = UsageStore(path=store.path)
    data = {
        "task_attempts": {"t1": 2},
        "events": [
            {"kind": "execution", "task_id": "t1", "timestamp": _ts(NOW, minutes=-5)},
        ],
    }
    s.path.write_text(json.dumps(data), encoding="utf-8")
    d = s.retry_decision(task_id="t1", now=NOW)
    assert not d.allowed and d.reason == "retry_cap_exceeded"


def test_retry_decision_auto_reset_old(store: UsageStore, monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "2")
    s = UsageStore(path=store.path)
    data = {
        "task_attempts": {"t1": 2},
        "last_task_signatures": {"executor:t1": "sig"},
        "events": [
            {"kind": "execution", "task_id": "t1", "timestamp": _ts(NOW, hours=-2)},
        ],
    }
    s.path.write_text(json.dumps(data), encoding="utf-8")
    d = s.retry_decision(task_id="t1", now=NOW)
    assert d.allowed and d.attempts == 0
    out = json.loads(s.path.read_text(encoding="utf-8"))
    assert "t1" not in out["task_attempts"]
    assert "executor:t1" not in out["last_task_signatures"]


def test_retry_decision_no_last_attempt_resets(store: UsageStore, monkeypatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "2")
    s = UsageStore(path=store.path)
    s.path.write_text(json.dumps({"task_attempts": {"t1": 5}, "events": []}), encoding="utf-8")
    d = s.retry_decision(task_id="t1", now=NOW)
    assert d.allowed and d.attempts == 0


def test_retry_decision_default_now(store: UsageStore) -> None:
    # now=None branch -> uses datetime.now; attempts under cap.
    d = store.retry_decision(task_id="z")
    assert d.allowed


def test_last_attempt_timestamp_non_list(store: UsageStore) -> None:
    assert store._last_attempt_timestamp({"events": "bad"}, "t1") is None


def test_last_attempt_timestamp_naive_tz(store: UsageStore) -> None:
    naive = "2026-06-01T10:00:00"
    data = {"events": [{"kind": "execution", "task_id": "t1", "timestamp": naive}]}
    dt = store._last_attempt_timestamp(data, "t1")
    assert dt is not None and dt.tzinfo is not None


def test_last_attempt_timestamp_bad_value(store: UsageStore) -> None:
    data = {
        "events": [
            "notadict",
            {"kind": "execution", "task_id": "t1", "timestamp": "garbage"},
            {"kind": "execution", "task_id": "t1"},  # no timestamp
        ]
    }
    assert store._last_attempt_timestamp(data, "t1") is None


# ---------------------------------------------------------------------------
# noop_decision
# ---------------------------------------------------------------------------
def test_noop_decision_skip(store: UsageStore) -> None:
    store.path.write_text(
        json.dumps({"last_task_signatures": {"executor:t1": "sig"}}), encoding="utf-8"
    )
    d = store.noop_decision(role="executor", task_id="t1", signature="sig")
    assert d.should_skip and d.reason == "no_op"


def test_noop_decision_no_skip(store: UsageStore) -> None:
    d = store.noop_decision(role="executor", task_id="t1", signature="sig")
    assert not d.should_skip


# ---------------------------------------------------------------------------
# record_execution & outcome
# ---------------------------------------------------------------------------
def test_record_execution(store: UsageStore) -> None:
    store.record_execution(
        role="executor",
        task_id="t1",
        signature="s",
        now=NOW,
        repo_key="r1",
        backend="team_executor",
    )
    out = store.load()
    assert out["task_attempts"]["t1"] == 1
    assert out["last_task_signatures"]["executor:t1"] == "s"
    ev = out["events"][-1]
    assert ev["repo_key"] == "r1" and ev["backend"] == "team_executor"


def test_record_execution_minimal(store: UsageStore) -> None:
    store.record_execution(role="executor", task_id="t1", signature="s", now=NOW)
    ev = store.load()["events"][-1]
    assert "repo_key" not in ev and "backend" not in ev


def test_record_execution_outcome_full(store: UsageStore) -> None:
    store.record_execution_outcome(
        task_id="t1", role="r", succeeded=True, now=NOW, backend="b", backend_version="1.0"
    )
    ev = store.load()["events"][-1]
    assert ev["succeeded"] and ev["backend"] == "b" and ev["backend_version"] == "1.0"


def test_record_execution_outcome_minimal(store: UsageStore) -> None:
    store.record_execution_outcome(task_id="t1", role="r", succeeded=False, now=NOW)
    ev = store.load()["events"][-1]
    assert "backend" not in ev and "backend_version" not in ev


# ---------------------------------------------------------------------------
# misc record_* helpers
# ---------------------------------------------------------------------------
def test_record_quality_warning(store: UsageStore) -> None:
    store.record_quality_warning(
        task_id="t1", repo_key="r", suppression_counts={"noqa": 3}, now=NOW
    )
    assert store.load()["events"][-1]["suppression_counts"] == {"noqa": 3}


def test_record_scope_violation_caps(store: UsageStore) -> None:
    files = [f"f{i}" for i in range(15)]
    store.record_scope_violation(task_id="t1", repo_key="r", violated_files=files, now=NOW)
    assert len(store.load()["events"][-1]["violated_files"]) == 10


def test_record_quota_event(store: UsageStore) -> None:
    store.record_quota_event(task_id="t1", role="r", backend="b", now=NOW)
    assert store.load()["events"][-1]["kind"] == "quota_event"


# ---------------------------------------------------------------------------
# worker backend cooldowns
# ---------------------------------------------------------------------------
def test_record_worker_backend_cooldown_and_until(store: UsageStore) -> None:
    reset = NOW + timedelta(hours=5)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code", reset_at=reset, now=NOW, limit_kind="session_5h"
    )
    got = store.worker_backend_cooldown_until("claude_code", now=NOW)
    assert got == reset


def test_worker_backend_cooldown_until_none(store: UsageStore) -> None:
    assert store.worker_backend_cooldown_until("claude_code", now=NOW) is None


def test_record_cooldown_coalesces(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=1),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    cooldowns = [e for e in store.load()["events"] if e.get("kind") == "worker_backend_cooldown"]
    assert len(cooldowns) == 1
    assert cooldowns[0]["reset_at"] == (NOW + timedelta(hours=2)).isoformat()


def test_worker_backend_cooldown_until_skips_invalid(store: UsageStore) -> None:
    evs = [
        {"kind": "worker_backend_cooldown", "worker_backend": "claude_code", "reset_at": 123},
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": "garbage",
            "timestamp": NOW.isoformat(),
        },
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "other",
            "reset_at": _ts(NOW, hours=5),
            "timestamp": NOW.isoformat(),
        },
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": _ts(NOW, hours=-1),  # past
            "timestamp": NOW.isoformat(),
        },
        {"kind": "execution", "timestamp": NOW.isoformat()},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.worker_backend_cooldown_until("claude_code", now=NOW) is None


def test_worker_backend_cooldown_details(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=3),
        now=NOW,
        limit_kind="model_weekly",
        model="opus",
    )
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=1),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    details = store.worker_backend_cooldown_details("claude_code", now=NOW)
    assert len(details) == 2
    # sorted soonest-first -> sonnet first
    assert details[0]["model"] == "sonnet"
    assert details[0]["seconds_remaining"] > 0


def test_worker_backend_cooldown_details_invalid_reset(store: UsageStore) -> None:
    evs = [
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": 999,
            "timestamp": NOW.isoformat(),
        },
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": "bad",
            "timestamp": NOW.isoformat(),
        },
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": _ts(NOW, hours=-1),
            "timestamp": NOW.isoformat(),
        },
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.worker_backend_cooldown_details("claude_code", now=NOW) == []


def test_worker_backend_blocked_until_account_wide(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=5),
        now=NOW,
        limit_kind="session_5h",
    )
    assert store.worker_backend_blocked_until("claude_code", now=NOW) == NOW + timedelta(hours=5)


def test_worker_backend_blocked_until_single_model_not_blocked(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=5),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    assert store.worker_backend_blocked_until("claude_code", now=NOW) is None


def test_worker_backend_blocked_until_all_models(store: UsageStore) -> None:
    for m, h in (("sonnet", 1), ("opus", 2), ("haiku", 3)):
        store.record_worker_backend_cooldown(
            worker_backend="claude_code",
            reset_at=NOW + timedelta(hours=h),
            now=NOW,
            limit_kind="model_weekly",
            model=m,
        )
    # all 3 models blocked -> soonest reset
    assert store.worker_backend_blocked_until("claude_code", now=NOW) == NOW + timedelta(hours=1)


def test_worker_backend_blocked_until_invalid_and_unknown(store: UsageStore) -> None:
    evs = [
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": 1,
            "timestamp": NOW.isoformat(),
        },
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": "bad",
            "timestamp": NOW.isoformat(),
        },
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "unknown_be",
            "reset_at": _ts(NOW, hours=5),
            "limit_kind": "model_weekly",
            "model": "x",
            "timestamp": NOW.isoformat(),
        },
        {"kind": "other", "timestamp": NOW.isoformat()},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    # unknown backend not in WORKER_BACKEND_MODELS -> None
    assert store.worker_backend_blocked_until("unknown_be", now=NOW) is None


def test_current_worker_backend_cooldowns(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="codex_cli",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="model_weekly",
        model="codex",
    )
    snap = store.current_worker_backend_cooldowns(now=NOW)
    assert set(snap) == {"claude_code", "codex_cli"}
    # codex_cli only runs "codex" -> all models blocked
    assert snap["codex_cli"]["cooling_down"] is True
    assert snap["codex_cli"]["seconds_remaining"] > 0
    assert snap["claude_code"]["cooling_down"] is False
    assert snap["claude_code"]["reset_at"] is None


def test_clear_worker_backend_cooldown_model_weekly(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="model_weekly",
        model="opus",
    )
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model="sonnet", now=NOW
    )
    assert removed == 1
    # opus preserved, audit event appended
    kinds = [e["kind"] for e in store.load()["events"]]
    assert "worker_backend_cooldown_cleared" in kinds


def test_clear_worker_backend_cooldown_account_wide(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="session_5h",
    )
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model=None, now=NOW, include_account_wide=True
    )
    assert removed == 1


def test_clear_worker_backend_cooldown_account_wide_preserved(store: UsageStore) -> None:
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="session_5h",
    )
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model=None, now=NOW, include_account_wide=False
    )
    assert removed == 0  # account-wide not dropped


def test_clear_worker_backend_cooldown_nothing(store: UsageStore) -> None:
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model="sonnet", now=NOW
    )
    assert removed == 0
    # save still persisted the (empty) data
    assert store.path.exists()


def test_is_active_cooldown_for_branches(store: UsageStore) -> None:
    assert not store._is_active_cooldown_for({"kind": "other"}, "claude_code", now=NOW)
    assert not store._is_active_cooldown_for(
        {"kind": "worker_backend_cooldown", "worker_backend": "x"}, "claude_code", now=NOW
    )
    assert not store._is_active_cooldown_for(
        {"kind": "worker_backend_cooldown", "worker_backend": "claude_code", "reset_at": 1},
        "claude_code",
        now=NOW,
    )
    assert not store._is_active_cooldown_for(
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": "bad",
        },
        "claude_code",
        now=NOW,
    )
    assert store._is_active_cooldown_for(
        {
            "kind": "worker_backend_cooldown",
            "worker_backend": "claude_code",
            "reset_at": _ts(NOW, hours=5),
        },
        "claude_code",
        now=NOW,
    )


# ---------------------------------------------------------------------------
# per-repo / per-backend budgets
# ---------------------------------------------------------------------------
def test_budget_decision_for_repo(store: UsageStore) -> None:
    evs = [
        {"kind": "execution", "repo_key": "r1", "timestamp": _ts(NOW, hours=-1)},
        {"kind": "execution", "repo_key": "r1", "timestamp": _ts(NOW, hours=-2)},
        {"kind": "execution", "repo_key": "r2", "timestamp": _ts(NOW, hours=-1)},
        {"kind": "execution", "repo_key": "r1", "timestamp": "bad"},  # skipped
        {"kind": "skip_budget", "repo_key": "r1", "timestamp": _ts(NOW, hours=-1)},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = store.budget_decision_for_repo("r1", 2, now=NOW)
    assert not d.allowed and d.reason == "repo_budget_exceeded"
    assert store.budget_decision_for_repo("r1", 5, now=NOW).allowed


def test_budget_decision_for_backend_no_backend(store: UsageStore) -> None:
    assert store.budget_decision_for_backend("", now=NOW).allowed


def test_budget_decision_for_backend_no_limits(store: UsageStore) -> None:
    assert store.budget_decision_for_backend("b", now=NOW).allowed


def test_budget_decision_for_backend_hourly(store: UsageStore) -> None:
    evs = [
        {"kind": "execution", "backend": "b", "timestamp": _ts(NOW, minutes=-1)},
        {"kind": "execution", "backend": "b", "timestamp": _ts(NOW, minutes=-2)},
        {"kind": "execution", "backend": "other", "timestamp": _ts(NOW, minutes=-1)},
        {"kind": "execution", "backend": "b", "timestamp": "bad"},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = store.budget_decision_for_backend("b", max_per_hour=2, now=NOW)
    assert not d.allowed and d.window == "hourly"


def test_budget_decision_for_backend_daily(store: UsageStore) -> None:
    evs = [
        {"kind": "execution", "backend": "b", "timestamp": _ts(NOW, hours=-2)},
        {"kind": "execution", "backend": "b", "timestamp": _ts(NOW, hours=-3)},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = store.budget_decision_for_backend("b", max_per_hour=10, max_per_day=2, now=NOW)
    assert not d.allowed and d.window == "daily"


def test_budget_decision_for_backend_allowed(store: UsageStore) -> None:
    assert store.budget_decision_for_backend("b", max_per_hour=10, max_per_day=10, now=NOW).allowed


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------
def test_concurrent_runs_for_backend(store: UsageStore) -> None:
    store.record_execution_started(task_id="t1", backend="b", now=NOW)
    store.record_execution_started(task_id="t2", backend="b", now=NOW)
    store.record_execution_finished(task_id="t1", backend="b", now=NOW)
    assert store.concurrent_runs_for_backend("b", now=NOW) == 1


def test_concurrent_runs_filters(store: UsageStore) -> None:
    evs = [
        {"kind": "execution_started", "task_id": "t1", "backend": "b", "timestamp": "bad"},
        {
            "kind": "execution_started",
            "task_id": "old",
            "backend": "b",
            "timestamp": _ts(NOW, hours=-30),
        },
        {"kind": "execution_started", "backend": "b", "timestamp": NOW.isoformat()},  # no tid
        {
            "kind": "execution_started",
            "task_id": "t9",
            "backend": "other",
            "timestamp": NOW.isoformat(),
        },
        {"kind": "execution_started", "task_id": 123, "backend": "b", "timestamp": NOW.isoformat()},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.concurrent_runs_for_backend("b", now=NOW) == 0


def test_total_concurrent_runs(store: UsageStore) -> None:
    store.record_execution_started(task_id="t1", backend="b1", now=NOW)
    store.record_execution_started(task_id="t1", backend="b2", now=NOW)
    store.record_execution_finished(task_id="t1", backend="b1", now=NOW)
    assert store.total_concurrent_runs(now=NOW) == 1


def test_total_concurrent_runs_filters(store: UsageStore) -> None:
    evs = [
        {"kind": "execution_started", "task_id": "t1", "timestamp": "bad"},
        {"kind": "execution_started", "task_id": "old", "timestamp": _ts(NOW, hours=-30)},
        {"kind": "execution_started", "task_id": 5, "timestamp": NOW.isoformat()},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.total_concurrent_runs(now=NOW) == 0


def test_global_concurrency_decision(store: UsageStore) -> None:
    assert store.global_concurrency_decision(max_concurrent=None, now=NOW).allowed
    store.record_execution_started(task_id="t1", backend="b", now=NOW)
    store.record_execution_started(task_id="t2", backend="b", now=NOW)
    d = store.global_concurrency_decision(max_concurrent=2, now=NOW)
    assert not d.allowed and d.reason == "global_concurrency_exceeded"
    assert store.global_concurrency_decision(max_concurrent=5, now=NOW).allowed


def test_concurrency_decision_for_backend(store: UsageStore) -> None:
    assert store.concurrency_decision_for_backend("", max_concurrent=1, now=NOW).allowed
    assert store.concurrency_decision_for_backend("b", max_concurrent=None, now=NOW).allowed
    store.record_execution_started(task_id="t1", backend="b", now=NOW)
    d = store.concurrency_decision_for_backend("b", max_concurrent=1, now=NOW)
    assert not d.allowed and d.reason == "backend_concurrency_exceeded"
    assert store.concurrency_decision_for_backend("b", max_concurrent=5, now=NOW).allowed


# ---------------------------------------------------------------------------
# rate / memory decisions
# ---------------------------------------------------------------------------
def test_global_rate_decision(store: UsageStore) -> None:
    assert store.global_rate_decision(max_per_hour=None, max_per_day=None, now=NOW).allowed
    evs = [
        {"kind": "execution", "timestamp": _ts(NOW, minutes=-1)},
        {"kind": "execution", "timestamp": _ts(NOW, minutes=-2)},
        {"kind": "execution", "timestamp": "bad"},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = store.global_rate_decision(max_per_hour=2, max_per_day=10, now=NOW)
    assert not d.allowed and d.window == "hourly"


def test_global_rate_decision_daily(store: UsageStore) -> None:
    evs = [
        {"kind": "execution", "timestamp": _ts(NOW, hours=-2)},
        {"kind": "execution", "timestamp": _ts(NOW, hours=-3)},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    d = store.global_rate_decision(max_per_hour=10, max_per_day=2, now=NOW)
    assert not d.allowed and d.window == "daily"
    assert store.global_rate_decision(max_per_hour=10, max_per_day=10, now=NOW).allowed


def test_available_memory_mb_reads(monkeypatch, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       1000 kB\nMemAvailable:  2048000 kB\nSwapFree:      1024000 kB\n"
    )
    real_open = open

    def fake_open(path, *a, **k):
        if path == "/proc/meminfo":
            return real_open(meminfo, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    # 2048000//1024 + 1024000//1024 = 2000 + 1000
    assert UsageStore.available_memory_mb() == 3000


def test_available_memory_mb_bad_values(monkeypatch, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:  notanumber kB\nSwapFree:  alsobad kB\nMemAvailable:\n")
    real_open = open

    def fake_open(path, *a, **k):
        if path == "/proc/meminfo":
            return real_open(meminfo, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert UsageStore.available_memory_mb() == 0


def test_available_memory_mb_oserror(monkeypatch) -> None:
    def boom(*a, **k):
        raise OSError("no /proc")

    monkeypatch.setattr("builtins.open", boom)
    assert UsageStore.available_memory_mb() == 0


def test_global_memory_decision(store: UsageStore, monkeypatch) -> None:
    assert store.global_memory_decision(min_available_memory_mb=None).allowed
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 0))
    assert store.global_memory_decision(min_available_memory_mb=100).allowed
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 50))
    d = store.global_memory_decision(min_available_memory_mb=100)
    assert not d.allowed and d.reason == "global_memory_insufficient"
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 500))
    assert store.global_memory_decision(min_available_memory_mb=100).allowed


def test_memory_decision_for_backend(store: UsageStore, monkeypatch) -> None:
    assert store.memory_decision_for_backend("", min_available_memory_mb=1, now=NOW).allowed
    assert store.memory_decision_for_backend("b", min_available_memory_mb=None, now=NOW).allowed
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 0))
    assert store.memory_decision_for_backend("b", min_available_memory_mb=100, now=NOW).allowed
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 50))
    d = store.memory_decision_for_backend("b", min_available_memory_mb=100, now=NOW)
    assert not d.allowed and d.reason == "backend_memory_insufficient"
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 500))
    assert store.memory_decision_for_backend("b", min_available_memory_mb=100, now=NOW).allowed


# ---------------------------------------------------------------------------
# failure rate / durations
# ---------------------------------------------------------------------------
def test_check_failure_rate_degradation_low_samples(store: UsageStore) -> None:
    assert store.check_failure_rate_degradation(now=NOW) is None


def test_check_failure_rate_degradation_degraded(store: UsageStore) -> None:
    evs = [
        {"kind": "execution_outcome", "succeeded": i < 1, "timestamp": _ts(NOW, minutes=-i)}
        for i in range(6)
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    rate = store.check_failure_rate_degradation(now=NOW)
    assert rate is not None and rate < 0.6


def test_check_failure_rate_degradation_healthy(store: UsageStore) -> None:
    evs = [
        {"kind": "execution_outcome", "succeeded": True, "timestamp": _ts(NOW, minutes=-i)}
        for i in range(6)
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.check_failure_rate_degradation(now=NOW) is None


def test_record_and_median_duration(store: UsageStore) -> None:
    for d in (10.0, 30.0, 20.0):
        store.record_execution_duration(task_id="t", role="exec", duration_seconds=d, now=NOW)
    assert store.median_execution_duration("exec", now=NOW) == 20.0


def test_median_duration_even(store: UsageStore) -> None:
    for d in (10.0, 20.0, 30.0, 40.0):
        store.record_execution_duration(task_id="t", role="exec", duration_seconds=d, now=NOW)
    assert store.median_execution_duration("exec", now=NOW) == 25.0


def test_median_duration_too_few(store: UsageStore) -> None:
    store.record_execution_duration(task_id="t", role="exec", duration_seconds=10.0, now=NOW)
    assert store.median_execution_duration("exec", now=NOW) is None


def test_median_duration_default_now(store: UsageStore) -> None:
    now = datetime.now(UTC)
    for d in (10.0, 20.0, 30.0):
        store.record_execution_duration(task_id="t", role="exec", duration_seconds=d, now=now)
    assert store.median_execution_duration("exec") == 20.0


# ---------------------------------------------------------------------------
# audit_export
# ---------------------------------------------------------------------------
def test_audit_export(store: UsageStore) -> None:
    evs = [
        {
            "kind": "execution_duration",
            "task_id": "t1",
            "duration_seconds": 12.0,
            "timestamp": _ts(NOW, minutes=-10),
        },
        {
            "kind": "execution_outcome",
            "task_id": "t1",
            "role": "exec",
            "succeeded": True,
            "timestamp": _ts(NOW, minutes=-9),
        },
        {
            "kind": "execution_outcome",
            "task_id": "t2",
            "succeeded": False,
            "timestamp": _ts(NOW, minutes=-8),
        },
        {"kind": "quota_event", "task_id": "t3", "backend": "b", "timestamp": _ts(NOW, minutes=-7)},
        {
            "kind": "executor_quality_warning",
            "task_id": "t4",
            "repo_key": "r",
            "suppression_counts": {"noqa": 1},
            "timestamp": _ts(NOW, minutes=-6),
        },
        {
            "kind": "scope_violation",
            "task_id": "t5",
            "repo_key": "r",
            "violated_files": ["a"],
            "timestamp": _ts(NOW, minutes=-5),
        },
        {
            "kind": "escalation_sent",
            "classification": "stuck",
            "task_ids": ["t6"],
            "timestamp": _ts(NOW, minutes=-4),
        },
        {"kind": "execution_outcome", "task_id": "old", "timestamp": _ts(NOW, days=-30)},
        {"kind": "execution_outcome", "task_id": "tbad", "timestamp": "bad"},
        {"kind": "noise", "timestamp": _ts(NOW, minutes=-1)},  # unhandled kind
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    rows = store.audit_export(now=NOW)
    outcomes = {r["outcome"] for r in rows}
    assert {
        "succeeded",
        "failed",
        "quota_exhausted",
        "quality_warning",
        "scope_violation",
        "escalated",
    } <= outcomes
    succ = next(r for r in rows if r["outcome"] == "succeeded")
    assert succ["duration_seconds"] == 12.0
    # sorted oldest first
    assert rows == sorted(rows, key=lambda r: r["timestamp"])


def test_audit_export_default_now(store: UsageStore) -> None:
    assert store.audit_export() == []


# ---------------------------------------------------------------------------
# record_skip
# ---------------------------------------------------------------------------
def test_record_skip_noop_stores_signature(store: UsageStore) -> None:
    store.record_skip(
        role="exec",
        task_id="t1",
        signature="sig",
        reason="no_op",
        detail="d",
        now=NOW,
    )
    out = store.load()
    assert out["last_task_signatures"]["exec:t1"] == "sig"
    assert out["events"][-1]["kind"] == "skip_noop"


def test_record_skip_budget(store: UsageStore) -> None:
    store.record_skip(
        role="exec", task_id="t1", signature="s", reason="budget", detail=None, now=NOW
    )
    out = store.load()
    assert "exec:t1" not in out.get("last_task_signatures", {})
    assert out["events"][-1]["kind"] == "skip_budget"


def test_record_skip_cooldown(store: UsageStore) -> None:
    store.record_skip(
        role="exec",
        task_id="t1",
        signature="s",
        reason="cooldown_active",
        detail=None,
        now=NOW,
        evidence={"x": 1},
    )
    ev = store.load()["events"][-1]
    assert ev["kind"] == "skip_cooldown" and ev["evidence"] == {"x": 1}


# ---------------------------------------------------------------------------
# retry cap / proposal cycle / satiation
# ---------------------------------------------------------------------------
def test_record_retry_cap(store: UsageStore) -> None:
    store.record_retry_cap(role="exec", task_id="t1", now=NOW, attempts=3, limit=3)
    ev = store.load()["events"][-1]
    assert ev["kind"] == "retry_cap_block" and ev["attempts"] == 3


def test_proposal_cycle_and_satiation_true(store: UsageStore) -> None:
    for _ in range(5):
        store.record_proposal_cycle(created=0, deduped=9, skipped=1, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is True


def test_satiation_false_too_few(store: UsageStore) -> None:
    store.record_proposal_cycle(created=0, deduped=1, skipped=0, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is False


def test_satiation_false_created(store: UsageStore) -> None:
    for _ in range(5):
        store.record_proposal_cycle(created=2, deduped=1, skipped=0, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is False


def test_satiation_false_zero_total(store: UsageStore) -> None:
    for _ in range(5):
        store.record_proposal_cycle(created=0, deduped=0, skipped=0, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is False


def test_satiation_false_below_ratio(store: UsageStore) -> None:
    for _ in range(5):
        store.record_proposal_cycle(created=0, deduped=1, skipped=0, now=NOW)
    # total_created 0, ratio 1.0 >= 0.9 -> True; force below with high threshold
    assert store.is_proposal_satiated(now=NOW, dedup_ratio_threshold=2.0) is False


def test_reset_satiation_window(store: UsageStore) -> None:
    store.record_proposal_cycle(created=0, deduped=1, skipped=0, now=NOW)
    store.record_execution(role="r", task_id="t", signature="s", now=NOW)
    store.reset_satiation_window(now=NOW)
    kinds = [e["kind"] for e in store.load()["events"]]
    assert "proposal_cycle" not in kinds
    assert "execution" in kinds


# ---------------------------------------------------------------------------
# proposal outcome / validation flaky
# ---------------------------------------------------------------------------
def test_proposal_success_rate(store: UsageStore) -> None:
    for ok in (True, True, False, True):
        store.record_proposal_outcome(category="c", succeeded=ok, now=NOW)
    assert store.proposal_success_rate("c", now=NOW) == 0.75


def test_proposal_success_rate_neutral(store: UsageStore) -> None:
    store.record_proposal_outcome(category="c", succeeded=True, now=NOW)
    assert store.proposal_success_rate("c", now=NOW) == 0.5


def test_proposal_success_rate_default_now(store: UsageStore) -> None:
    now = datetime.now(UTC)
    for ok in (True, True, True):
        store.record_proposal_outcome(category="c", succeeded=ok, now=now)
    assert store.proposal_success_rate("c") == 1.0


def test_is_command_flaky(store: UsageStore) -> None:
    now = datetime.now(UTC)
    for i in range(10):
        store.record_validation_outcome(command="pytest", passed=(i % 2 == 0), now=now)
    assert store.is_command_flaky("pytest", now=now) is True


def test_is_command_flaky_too_few(store: UsageStore) -> None:
    store.record_validation_outcome(command="pytest", passed=False, now=NOW)
    assert store.is_command_flaky("pytest", now=NOW) is False


def test_is_command_flaky_default_now(store: UsageStore) -> None:
    now = datetime.now(UTC)
    for _ in range(10):
        store.record_validation_outcome(command="x", passed=True, now=now)
    assert store.is_command_flaky("x") is False


# ---------------------------------------------------------------------------
# escalation
# ---------------------------------------------------------------------------
def test_should_escalate_fires(store: UsageStore) -> None:
    evs = [
        {
            "kind": "blocked_triage",
            "task_id": f"t{i}",
            "classification": "stuck",
            "timestamp": _ts(NOW, minutes=-i),
        }
        for i in range(3)
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    fire, ids = store.should_escalate(
        classification="stuck", threshold=3, cooldown_seconds=3600, now=NOW
    )
    assert fire and len(ids) == 3


def test_should_escalate_cooldown_blocks(store: UsageStore) -> None:
    evs = [
        {
            "kind": "escalation_sent",
            "classification": "stuck",
            "timestamp": _ts(NOW, minutes=-1),
        },
        {
            "kind": "blocked_triage",
            "task_id": "t1",
            "classification": "stuck",
            "timestamp": _ts(NOW, minutes=-2),
        },
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    fire, ids = store.should_escalate(
        classification="stuck", threshold=1, cooldown_seconds=3600, now=NOW
    )
    assert not fire and ids == []


def test_should_escalate_below_threshold(store: UsageStore) -> None:
    evs = [
        {
            "kind": "blocked_triage",
            "task_id": "t1",
            "classification": "stuck",
            "timestamp": _ts(NOW, minutes=-1),
        },
        {  # empty task_id -> not appended
            "kind": "blocked_triage",
            "task_id": "",
            "classification": "stuck",
            "timestamp": _ts(NOW, minutes=-3),
        },
        {
            "kind": "blocked_triage",
            "task_id": "tbad",
            "classification": "stuck",
            "timestamp": "garbage",
        },
        {
            "kind": "escalation_sent",
            "classification": "stuck",
            "timestamp": "garbage",
        },
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    fire, ids = store.should_escalate(
        classification="stuck", threshold=5, cooldown_seconds=3600, now=NOW
    )
    assert not fire


def test_consecutive_blocks_for_task(store: UsageStore) -> None:
    evs = [
        {
            "kind": "execution_outcome",
            "task_id": "t1",
            "succeeded": True,
            "timestamp": _ts(NOW, minutes=-5),
        },
        {"kind": "blocked_triage", "task_id": "t1", "timestamp": _ts(NOW, minutes=-4)},
        {"kind": "blocked_triage", "task_id": "other", "timestamp": _ts(NOW, minutes=-3)},
        {"kind": "blocked_triage", "task_id": "t1", "timestamp": _ts(NOW, minutes=-2)},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.consecutive_blocks_for_task("t1", now=NOW) == 2


def test_consecutive_blocks_stops_at_success(store: UsageStore) -> None:
    evs = [
        {"kind": "blocked_triage", "task_id": "t1", "timestamp": _ts(NOW, minutes=-5)},
        {
            "kind": "execution_outcome",
            "task_id": "t1",
            "succeeded": True,
            "timestamp": _ts(NOW, minutes=-4),
        },
        {"kind": "blocked_triage", "task_id": "t1", "timestamp": _ts(NOW, minutes=-3)},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    assert store.consecutive_blocks_for_task("t1", now=NOW) == 1


def test_record_blocked_triage(store: UsageStore) -> None:
    store.record_blocked_triage(task_id="t1", classification="stuck", now=NOW)
    assert store.load()["events"][-1]["classification"] == "stuck"


def test_record_escalation(store: UsageStore) -> None:
    store.record_escalation(classification="stuck", task_ids=["t1"], now=NOW)
    assert store.load()["events"][-1]["task_ids"] == ["t1"]


# ---------------------------------------------------------------------------
# cost / spend
# ---------------------------------------------------------------------------
def test_record_cost_and_spend_report(store: UsageStore) -> None:
    store.record_execution_cost(task_id="t1", repo_key="r1", estimated_usd=1.5, now=NOW)
    store.record_execution_cost(task_id="t2", repo_key="r1", estimated_usd=0.5, now=NOW)
    rep = store.get_spend_report(now=NOW)
    assert rep["total_executions"] == 2
    assert rep["total_estimated_usd"] == 2.0
    assert rep["per_repo"]["r1"]["executions"] == 2


def test_spend_report_filters(store: UsageStore) -> None:
    evs = [
        {
            "kind": "execution_cost",
            "repo_key": None,
            "estimated_usd": None,
            "timestamp": _ts(NOW, hours=-1),
        },
        {"kind": "execution_cost", "repo_key": "r", "estimated_usd": 1.0, "timestamp": "bad"},
        {
            "kind": "execution_cost",
            "repo_key": "r",
            "estimated_usd": 1.0,
            "timestamp": _ts(NOW, hours=-30),
        },
        {"kind": "other", "timestamp": _ts(NOW, hours=-1)},
    ]
    store.path.write_text(json.dumps({"events": evs}), encoding="utf-8")
    rep = store.get_spend_report(window_days=1, now=NOW)
    assert rep["total_executions"] == 1
    assert rep["per_repo"]["unknown"]["estimated_usd"] == 0.0


def test_spend_report_default_now(store: UsageStore) -> None:
    rep = store.get_spend_report()
    assert rep["total_executions"] == 0


# ---------------------------------------------------------------------------
# proposal budget suppression / artifacts
# ---------------------------------------------------------------------------
def test_record_proposal_budget_suppression(store: UsageStore) -> None:
    store.record_proposal_budget_suppression(reason="cap", now=NOW, evidence={"k": 1})
    assert store.load()["events"][-1]["kind"] == "proposal_budget_suppressed"


def test_record_and_get_task_artifact(store: UsageStore) -> None:
    store.record_task_artifact(task_id="t1", artifact={"outcome_status": "ok"}, now=NOW)
    art = store.get_task_artifact("t1")
    assert art["outcome_status"] == "ok" and "recorded_at" in art


def test_get_task_artifact_missing(store: UsageStore) -> None:
    assert store.get_task_artifact("nope") is None


def test_get_task_artifact_non_dict(store: UsageStore) -> None:
    store.path.write_text(json.dumps({"task_artifacts": {"t1": "notadict"}}), encoding="utf-8")
    assert store.get_task_artifact("t1") is None


# ---------------------------------------------------------------------------
# static helpers
# ---------------------------------------------------------------------------
def test_issue_signature_dict_state(store: UsageStore) -> None:
    issue = {
        "id": "i1",
        "state": {"name": "Open"},
        "updated_at": "2026-01-01",
        "description_html": "<p>hi</p>",
    }
    sig = UsageStore.issue_signature(issue)
    assert sig == "i1|Open|2026-01-01|<p>hi</p>"


def test_issue_signature_str_state_fallbacks(store: UsageStore) -> None:
    issue = {"id": "i2", "state": "Closed", "updated": "u", "description": "desc"}
    assert UsageStore.issue_signature(issue) == "i2|Closed|u|desc"


def test_issue_signature_description_stripped(store: UsageStore) -> None:
    issue = {"id": "i3", "state": None, "description_stripped": "stripped"}
    assert UsageStore.issue_signature(issue) == "i3|||stripped"


def test_exec_count_bad_timestamp(store: UsageStore) -> None:
    evs = [
        {"kind": "execution", "timestamp": "bad"},
        {"kind": "execution", "timestamp": NOW.isoformat()},
        {"kind": "skip_budget", "timestamp": NOW.isoformat()},
    ]
    assert UsageStore._exec_count(evs, since=NOW - timedelta(hours=1)) == 1


def test_prune_events_caps_and_drops(store: UsageStore) -> None:
    evs = [{"kind": "execution", "timestamp": "bad"}]
    evs += [{"kind": "execution", "timestamp": _ts(NOW, minutes=-i)} for i in range(1100)]
    out = UsageStore._prune_events(evs, now=NOW)
    assert len(out) == 1000  # capped, bad dropped


def test_prune_events_drops_old(store: UsageStore) -> None:
    evs = [
        {"kind": "execution", "timestamp": _ts(NOW, days=-10)},
        {"kind": "execution", "timestamp": NOW.isoformat()},
    ]
    out = UsageStore._prune_events(evs, now=NOW)
    assert len(out) == 1
