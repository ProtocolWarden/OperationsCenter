# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from operations_center.execution import usage_store as us_mod
from operations_center.execution.usage_store import (
    UsageStore,
    _check_disk_space,
    _get_lock,
)

NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> UsageStore:
    """Build a UsageStore with a tmp usage path and bounded env caps."""
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", env.get("hour", "10"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_DAY", env.get("day", "50"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", env.get("retries", "3"))
    return UsageStore(path=tmp_path / "usage.json")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_get_lock_returns_same_lock_for_same_path(tmp_path: Path) -> None:
    p = tmp_path / "u.json"
    lock1 = _get_lock(p)
    lock2 = _get_lock(p)
    assert lock1 is lock2


def test_get_lock_distinct_paths_distinct_locks(tmp_path: Path) -> None:
    a = _get_lock(tmp_path / "a.json")
    b = _get_lock(tmp_path / "b.json")
    assert a is not b


def test_check_disk_space_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Usage:
        free = 500 * 1024 * 1024

    monkeypatch.setattr(us_mod.shutil, "disk_usage", lambda _p: _Usage())
    # Ample free space → returns without raising.
    assert _check_disk_space(tmp_path / "f.json") is None


def test_check_disk_space_oserror_on_probe_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_p: object) -> None:
        raise OSError("cannot stat")

    monkeypatch.setattr(us_mod.shutil, "disk_usage", _boom)
    # A failed probe is swallowed — returns silently, does not block the write.
    assert _check_disk_space(tmp_path / "f.json") is None


def test_check_disk_space_critical_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Usage:
        free = 10 * 1024 * 1024  # below 50 MB minimum

    monkeypatch.setattr(us_mod.shutil, "disk_usage", lambda _p: _Usage())
    with pytest.raises(OSError, match="disk_space_critical"):
        _check_disk_space(tmp_path / "f.json")


def test_check_disk_space_low_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Usage:
        free = 100 * 1024 * 1024  # between min(50) and warn(200)

    monkeypatch.setattr(us_mod.shutil, "disk_usage", lambda _p: _Usage())
    with caplog.at_level("WARNING"):
        _check_disk_space(tmp_path / "f.json")
    assert any("disk_space_low" in r.getMessage() for r in caplog.records)


def test_check_disk_space_dir_path_uses_self(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Path] = {}

    class _Usage:
        free = 500 * 1024 * 1024

    def _du(p: Path) -> _Usage:
        captured["p"] = p
        return _Usage()

    monkeypatch.setattr(us_mod.shutil, "disk_usage", _du)
    _check_disk_space(tmp_path)  # tmp_path is a dir
    assert captured["p"] == tmp_path


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def test_load_missing_returns_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    data = store.load()
    assert data["events"] == []
    assert data["updated_at"] is None
    assert data["hourly_exec_count"] == 0


def test_load_existing_reads_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(json.dumps({"events": [], "marker": 7}), encoding="utf-8")
    assert store.load()["marker"] == 7


def test_save_writes_counts_and_updated_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    events = [
        {"kind": "execution", "timestamp": _iso(NOW)},
        {"kind": "skip_budget", "timestamp": _iso(NOW)},
        {"kind": "skip_noop", "timestamp": _iso(NOW)},
        {"kind": "skip_cooldown", "timestamp": _iso(NOW)},
        {"kind": "retry_cap_block", "timestamp": _iso(NOW)},
        {"kind": "proposal_budget_suppressed", "timestamp": _iso(NOW)},
    ]
    store.save({"events": events}, now=NOW)
    saved = store.load()
    assert saved["updated_at"] == _iso(NOW)
    assert saved["hourly_exec_count"] == 1
    assert saved["daily_exec_count"] == 1
    assert saved["skipped_due_to_budget"] == 1
    assert saved["skipped_due_to_noop"] == 1
    assert saved["skipped_due_to_cooldown"] == 1
    assert saved["blocked_due_to_retry_cap"] == 1
    assert saved["suppressed_due_to_proposal_budget"] == 1


def test_save_atomic_no_tmp_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.save({"events": []}, now=NOW)
    assert not store.path.with_name(store.path.name + ".tmp").exists()
    assert store.path.exists()


# ---------------------------------------------------------------------------
# budget_decision (+ circuit breaker)
# ---------------------------------------------------------------------------


def test_budget_decision_allowed_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.budget_decision(now=NOW).allowed is True


def test_budget_decision_hourly_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch, hour="2", day="100")
    for _ in range(2):
        store.record_execution(role="r", task_id="t", signature="s", now=NOW)
    dec = store.budget_decision(now=NOW)
    assert dec.allowed is False
    assert dec.window == "hourly"
    assert dec.reason == "budget_exceeded"


def test_budget_decision_daily_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch, hour="100", day="2")
    # 2 executions across more than an hour apart so hourly doesn't trip
    store.record_execution(role="r", task_id="t1", signature="s", now=NOW - timedelta(hours=3))
    store.record_execution(role="r", task_id="t2", signature="s", now=NOW - timedelta(hours=2))
    dec = store.budget_decision(now=NOW)
    assert dec.allowed is False
    assert dec.window == "daily"


def test_circuit_breaker_opens_on_fresh_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, hour="100", day="100")
    for i in range(4):
        store.record_execution_outcome(
            task_id=f"t{i}", role="r", succeeded=False, now=NOW - timedelta(minutes=i)
        )
    dec = store.budget_decision(now=NOW)
    assert dec.allowed is False
    assert dec.reason == "circuit_breaker_open"


def test_circuit_breaker_skips_when_under_three_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, hour="100", day="100")
    for i in range(2):
        store.record_execution_outcome(
            task_id=f"t{i}", role="r", succeeded=False, now=NOW - timedelta(minutes=i)
        )
    assert store.budget_decision(now=NOW).allowed is True


def test_circuit_breaker_ignores_stale_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, hour="100", day="100")
    for i in range(4):
        store.record_execution_outcome(
            task_id=f"t{i}", role="r", succeeded=False, now=NOW - timedelta(hours=5, minutes=i)
        )
    # All older than CB_STALENESS_HOURS (4h) -> not counted
    assert store.budget_decision(now=NOW).allowed is True


def test_circuit_breaker_skips_on_version_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, hour="100", day="100")
    for i in range(4):
        store.record_execution_outcome(
            task_id=f"t{i}",
            role="r",
            succeeded=False,
            now=NOW - timedelta(minutes=i),
            backend_version="v1" if i < 2 else "v2",
        )
    # Mixed versions in window -> breaker skipped
    assert store.budget_decision(now=NOW).allowed is True


def test_circuit_breaker_stays_closed_when_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, hour="100", day="100")
    # 1 fail out of 4 -> below 0.8 threshold
    for i in range(4):
        store.record_execution_outcome(
            task_id=f"t{i}", role="r", succeeded=i != 0, now=NOW - timedelta(minutes=i)
        )
    assert store.budget_decision(now=NOW).allowed is True


# ---------------------------------------------------------------------------
# remaining_exec_capacity
# ---------------------------------------------------------------------------


def test_remaining_exec_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch, hour="5", day="50")
    store.record_execution(role="r", task_id="t", signature="s", now=NOW)
    assert store.remaining_exec_capacity(now=NOW) == 4


# ---------------------------------------------------------------------------
# retry_decision
# ---------------------------------------------------------------------------


def test_retry_decision_allowed_under_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch, retries="3")
    dec = store.retry_decision(task_id="t", now=NOW)
    assert dec.allowed is True
    assert dec.limit == 3


def test_retry_decision_blocked_at_cap_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, retries="2")
    for _ in range(2):
        store.record_execution(role="r", task_id="t", signature="s", now=NOW)
    dec = store.retry_decision(task_id="t", now=NOW)
    assert dec.allowed is False
    assert dec.reason == "retry_cap_exceeded"


def test_retry_decision_auto_reset_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, retries="2")
    old = NOW - timedelta(hours=3)
    for _ in range(2):
        store.record_execution(role="r", task_id="t", signature="s", now=old)
    dec = store.retry_decision(task_id="t", now=NOW)
    assert dec.allowed is True
    assert dec.attempts == 0


def test_retry_decision_auto_reset_when_no_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch, retries="2")
    # task_attempts populated but no matching execution event -> last_attempt None
    store.path.write_text(json.dumps({"events": [], "task_attempts": {"t": 5}}), encoding="utf-8")
    dec = store.retry_decision(task_id="t", now=NOW)
    assert dec.allowed is True
    assert dec.attempts == 0


def test_retry_decision_default_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch, retries="3")
    dec = store.retry_decision(task_id="t")
    assert dec.allowed is True


# ---------------------------------------------------------------------------
# _last_attempt_timestamp branches
# ---------------------------------------------------------------------------


def test_last_attempt_timestamp_naive_gets_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    data = {"events": [{"kind": "execution", "task_id": "t", "timestamp": "2026-06-02T10:00:00"}]}
    ts = store._last_attempt_timestamp(data, "t")
    assert ts is not None
    assert ts.tzinfo == timezone.utc


def test_last_attempt_timestamp_events_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store._last_attempt_timestamp({"events": "nope"}, "t") is None


def test_last_attempt_timestamp_bad_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    data = {
        "events": [
            {"kind": "execution", "task_id": "t", "timestamp": "not-a-date"},
            "not-a-dict",
            {"kind": "execution", "task_id": "other", "timestamp": _iso(NOW)},
        ]
    }
    assert store._last_attempt_timestamp(data, "t") is None


def test_last_attempt_timestamp_no_ts_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    data = {"events": [{"kind": "execution", "task_id": "t"}]}
    assert store._last_attempt_timestamp(data, "t") is None


# ---------------------------------------------------------------------------
# noop_decision
# ---------------------------------------------------------------------------


def test_noop_decision_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(role="r", task_id="t", signature="sig", now=NOW)
    dec = store.noop_decision(role="r", task_id="t", signature="sig")
    assert dec.should_skip is True
    assert dec.reason == "no_op"


def test_noop_decision_no_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(role="r", task_id="t", signature="old", now=NOW)
    dec = store.noop_decision(role="r", task_id="t", signature="new")
    assert dec.should_skip is False


# ---------------------------------------------------------------------------
# record_execution / record_execution_outcome metadata
# ---------------------------------------------------------------------------


def test_record_execution_with_repo_and_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(
        role="r", task_id="t", signature="s", now=NOW, repo_key="repo", backend="dag_executor"
    )
    ev = store.load()["events"][0]
    assert ev["repo_key"] == "repo"
    assert ev["backend"] == "dag_executor"
    assert store.load()["task_attempts"]["t"] == 1


def test_record_execution_outcome_with_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_outcome(
        task_id="t",
        role="r",
        succeeded=True,
        now=NOW,
        backend="team_executor",
        backend_version="v9",
    )
    ev = store.load()["events"][0]
    assert ev["backend"] == "team_executor"
    assert ev["backend_version"] == "v9"
    assert ev["succeeded"] is True


# ---------------------------------------------------------------------------
# record_quality_warning / scope_violation / quota_event
# ---------------------------------------------------------------------------


def test_record_quality_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_quality_warning(
        task_id="t", repo_key="repo", suppression_counts={"noqa": 3}, now=NOW
    )
    ev = store.load()["events"][0]
    assert ev["kind"] == "executor_quality_warning"
    assert ev["suppression_counts"] == {"noqa": 3}


def test_record_scope_violation_caps_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    files = [f"f{i}.py" for i in range(15)]
    store.record_scope_violation(task_id="t", repo_key="repo", violated_files=files, now=NOW)
    ev = store.load()["events"][0]
    assert len(ev["violated_files"]) == 10


def test_record_quota_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_quota_event(task_id="t", role="r", backend="team_executor", now=NOW)
    ev = store.load()["events"][0]
    assert ev["kind"] == "quota_event"
    assert ev["backend"] == "team_executor"


# ---------------------------------------------------------------------------
# worker-backend cooldowns
# ---------------------------------------------------------------------------


def test_record_worker_backend_cooldown_with_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    reset = NOW + timedelta(hours=1)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=reset,
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    ev = store.load()["events"][0]
    assert ev["limit_kind"] == "model_weekly"
    assert ev["model"] == "sonnet"


def test_record_worker_backend_cooldown_coalesces_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(3):
        store.record_worker_backend_cooldown(
            worker_backend="claude_code",
            reset_at=NOW + timedelta(hours=2),
            now=NOW,
            limit_kind="model_weekly",
            model="sonnet",
        )
    cooldowns = [e for e in store.load()["events"] if e["kind"] == "worker_backend_cooldown"]
    assert len(cooldowns) == 1


def test_record_worker_backend_cooldown_no_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code", reset_at=NOW + timedelta(hours=1), now=NOW
    )
    ev = store.load()["events"][0]
    assert "limit_kind" not in ev
    assert "model" not in ev


def test_worker_backend_cooldown_until_returns_latest_future(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code", reset_at=NOW + timedelta(hours=1), now=NOW, model="opus"
    )
    store.record_worker_backend_cooldown(
        worker_backend="claude_code", reset_at=NOW + timedelta(hours=3), now=NOW, model="haiku"
    )
    until = store.worker_backend_cooldown_until("claude_code", now=NOW)
    assert until == NOW + timedelta(hours=3)


def test_worker_backend_cooldown_until_ignores_past_and_other_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    # past reset
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW - timedelta(hours=1),
        now=NOW - timedelta(hours=2),
    )
    # other backend
    store.record_worker_backend_cooldown(
        worker_backend="codex_cli", reset_at=NOW + timedelta(hours=5), now=NOW
    )
    assert store.worker_backend_cooldown_until("claude_code", now=NOW) is None


def test_worker_backend_cooldown_until_bad_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "reset_at": "garbage",
                        "timestamp": _iso(NOW),
                    },
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "reset_at": 12345,
                        "timestamp": _iso(NOW),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.worker_backend_cooldown_until("claude_code", now=NOW) is None


def test_worker_backend_cooldown_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
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
    # sorted soonest first -> sonnet (1h) first
    assert details[0]["model"] == "sonnet"
    assert details[0]["seconds_remaining"] > 0


def test_worker_backend_cooldown_details_dedups_keeps_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    # write two raw events with same key, different reset -> latest wins
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "limit_kind": "model_weekly",
                        "model": "sonnet",
                        "reset_at": _iso(NOW + timedelta(hours=1)),
                        "timestamp": _iso(NOW),
                    },
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "limit_kind": "model_weekly",
                        "model": "sonnet",
                        "reset_at": _iso(NOW + timedelta(hours=5)),
                        "timestamp": _iso(NOW),
                    },
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "reset_at": "bad",
                        "timestamp": _iso(NOW),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    details = store.worker_backend_cooldown_details("claude_code", now=NOW)
    assert len(details) == 1
    assert details[0]["reset_at"] == _iso(NOW + timedelta(hours=5))


def test_worker_backend_blocked_until_model_weekly_partial_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=1),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    # Only one of three models down -> not blocked
    assert store.worker_backend_blocked_until("claude_code", now=NOW) is None


def test_worker_backend_blocked_until_all_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    for model, h in (("sonnet", 1), ("opus", 2), ("haiku", 3)):
        store.record_worker_backend_cooldown(
            worker_backend="claude_code",
            reset_at=NOW + timedelta(hours=h),
            now=NOW,
            limit_kind="model_weekly",
            model=model,
        )
    # all models down -> soonest free-up
    assert store.worker_backend_blocked_until("claude_code", now=NOW) == NOW + timedelta(hours=1)


def test_worker_backend_blocked_until_account_wide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="session_5h",
    )
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=5),
        now=NOW,
        limit_kind="global_weekly",
    )
    # account-wide -> latest reset
    assert store.worker_backend_blocked_until("claude_code", now=NOW) == NOW + timedelta(hours=5)


def test_worker_backend_blocked_until_bad_reset_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "reset_at": "bad",
                        "timestamp": _iso(NOW),
                    },
                    {
                        "kind": "worker_backend_cooldown",
                        "worker_backend": "claude_code",
                        "reset_at": 99,
                        "timestamp": _iso(NOW),
                    },
                    {"kind": "execution", "timestamp": _iso(NOW)},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.worker_backend_blocked_until("claude_code", now=NOW) is None


def test_worker_backend_blocked_until_unknown_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="unknown_be",
        reset_at=NOW + timedelta(hours=1),
        now=NOW,
        limit_kind="model_weekly",
        model="sonnet",
    )
    # unknown backend has no WORKER_BACKEND_MODELS entry -> not blocked
    assert store.worker_backend_blocked_until("unknown_be", now=NOW) is None


def test_current_worker_backend_cooldowns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="codex_cli",
        reset_at=NOW + timedelta(hours=2),
        now=NOW,
        limit_kind="model_weekly",
        model="codex",
    )
    snap = store.current_worker_backend_cooldowns(now=NOW)
    assert set(snap) == {"claude_code", "codex_cli"}
    assert snap["codex_cli"]["cooling_down"] is True
    assert snap["codex_cli"]["seconds_remaining"] > 0
    assert snap["claude_code"]["cooling_down"] is False
    assert snap["claude_code"]["reset_at"] is None


def test_clear_worker_backend_cooldown_drops_model_and_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
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
        model="opus",
    )
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=3),
        now=NOW,
        limit_kind="session_5h",
    )
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model="sonnet", now=NOW, include_account_wide=True
    )
    # sonnet model_weekly + account-wide session_5h removed; opus kept
    assert removed == 2
    remaining = [e for e in store.load()["events"] if e["kind"] == "worker_backend_cooldown"]
    assert len(remaining) == 1
    assert remaining[0]["model"] == "opus"
    cleared = [e for e in store.load()["events"] if e["kind"] == "worker_backend_cooldown_cleared"]
    assert cleared and cleared[0]["removed"] == 2


def test_clear_worker_backend_cooldown_keeps_account_when_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=NOW + timedelta(hours=3),
        now=NOW,
        limit_kind="session_5h",
    )
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model="sonnet", now=NOW, include_account_wide=False
    )
    assert removed == 0


def test_clear_worker_backend_cooldown_noop_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(role="r", task_id="t", signature="s", now=NOW)
    removed = store.clear_worker_backend_cooldown(
        worker_backend="claude_code", model="sonnet", now=NOW
    )
    assert removed == 0
    # no cleared event appended
    assert not any(e["kind"] == "worker_backend_cooldown_cleared" for e in store.load()["events"])


def test_is_active_cooldown_for_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = UsageStore._is_active_cooldown_for
    assert f({"kind": "execution"}, "claude_code", now=NOW) is False
    assert (
        f(
            {"kind": "worker_backend_cooldown", "worker_backend": "other"},
            "claude_code",
            now=NOW,
        )
        is False
    )
    assert (
        f(
            {"kind": "worker_backend_cooldown", "worker_backend": "claude_code", "reset_at": 5},
            "claude_code",
            now=NOW,
        )
        is False
    )
    assert (
        f(
            {
                "kind": "worker_backend_cooldown",
                "worker_backend": "claude_code",
                "reset_at": "bad",
            },
            "claude_code",
            now=NOW,
        )
        is False
    )
    assert (
        f(
            {
                "kind": "worker_backend_cooldown",
                "worker_backend": "claude_code",
                "reset_at": _iso(NOW + timedelta(hours=1)),
            },
            "claude_code",
            now=NOW,
        )
        is True
    )


# ---------------------------------------------------------------------------
# budget_decision_for_repo / backend
# ---------------------------------------------------------------------------


def test_budget_decision_for_repo_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for i in range(3):
        store.record_execution(role="r", task_id=f"t{i}", signature="s", now=NOW, repo_key="repo")
    dec = store.budget_decision_for_repo("repo", 2, now=NOW)
    assert dec.allowed is False
    assert dec.reason == "repo_budget_exceeded"


def test_budget_decision_for_repo_allowed_and_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(role="r", task_id="t", signature="s", now=NOW, repo_key="other")
    # event with bad timestamp + non-execution kind ignored
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {"kind": "execution", "repo_key": "repo", "timestamp": "bad"},
                    {"kind": "skip_noop", "repo_key": "repo", "timestamp": _iso(NOW)},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.budget_decision_for_repo("repo", 1, now=NOW).allowed is True


def test_budget_decision_for_backend_no_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.budget_decision_for_backend("", now=NOW).allowed is True


def test_budget_decision_for_backend_no_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.budget_decision_for_backend("be", now=NOW).allowed is True


def test_budget_decision_for_backend_hourly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    for i in range(2):
        store.record_execution(role="r", task_id=f"t{i}", signature="s", now=NOW, backend="dag")
    dec = store.budget_decision_for_backend("dag", max_per_hour=2, now=NOW)
    assert dec.allowed is False
    assert dec.window == "hourly"


def test_budget_decision_for_backend_daily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(
        role="r", task_id="a", signature="s", now=NOW - timedelta(hours=3), backend="dag"
    )
    store.record_execution(
        role="r", task_id="b", signature="s", now=NOW - timedelta(hours=2), backend="dag"
    )
    dec = store.budget_decision_for_backend("dag", max_per_day=2, now=NOW)
    assert dec.allowed is False
    assert dec.window == "daily"


def test_budget_decision_for_backend_skips_bad_ts_and_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {"kind": "execution", "backend": "dag", "timestamp": "bad"},
                    {"kind": "skip_noop", "backend": "dag", "timestamp": _iso(NOW)},
                    {"kind": "execution", "backend": "other", "timestamp": _iso(NOW)},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.budget_decision_for_backend("dag", max_per_hour=1, now=NOW).allowed is True


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_concurrent_runs_for_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_started(task_id="a", backend="dag", now=NOW)
    store.record_execution_started(task_id="b", backend="dag", now=NOW)
    store.record_execution_finished(task_id="a", backend="dag", now=NOW)
    assert store.concurrent_runs_for_backend("dag", now=NOW) == 1


def test_concurrent_runs_skips_stale_and_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "execution_started",
                        "task_id": "old",
                        "backend": "dag",
                        "timestamp": _iso(NOW - timedelta(hours=30)),
                    },
                    {
                        "kind": "execution_started",
                        "task_id": "live",
                        "backend": "dag",
                        "timestamp": _iso(NOW),
                    },
                    {
                        "kind": "execution_started",
                        "task_id": 123,
                        "backend": "dag",
                        "timestamp": _iso(NOW),
                    },
                    {
                        "kind": "execution_started",
                        "task_id": "x",
                        "backend": "dag",
                        "timestamp": 999,
                    },
                    {
                        "kind": "execution_started",
                        "task_id": "y",
                        "backend": "dag",
                        "timestamp": "bad",
                    },
                    {
                        "kind": "execution_started",
                        "task_id": "z",
                        "backend": "other",
                        "timestamp": _iso(NOW),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.concurrent_runs_for_backend("dag", now=NOW) == 1


def test_total_concurrent_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_started(task_id="a", backend="dag", now=NOW)
    store.record_execution_started(task_id="a", backend="team", now=NOW)
    store.record_execution_finished(task_id="a", backend="dag", now=NOW)
    # (team, a) still in flight, (dag, a) finished
    assert store.total_concurrent_runs(now=NOW) == 1


def test_total_concurrent_runs_skips_bad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "execution_started",
                        "task_id": "old",
                        "timestamp": _iso(NOW - timedelta(hours=30)),
                    },
                    {"kind": "execution_started", "task_id": 5, "timestamp": _iso(NOW)},
                    {"kind": "execution_started", "task_id": "x", "timestamp": 9},
                    {"kind": "execution_started", "task_id": "ok", "timestamp": _iso(NOW)},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert store.total_concurrent_runs(now=NOW) == 1


def test_global_concurrency_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.global_concurrency_decision(max_concurrent=None, now=NOW).allowed is True
    store.record_execution_started(task_id="a", backend="dag", now=NOW)
    dec = store.global_concurrency_decision(max_concurrent=1, now=NOW)
    assert dec.allowed is False
    assert dec.reason == "global_concurrency_exceeded"
    store.record_execution_finished(task_id="a", backend="dag", now=NOW)
    assert store.global_concurrency_decision(max_concurrent=1, now=NOW).allowed is True


def test_global_rate_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.global_rate_decision(max_per_hour=None, max_per_day=None, now=NOW).allowed is True
    store.record_execution(role="r", task_id="t", signature="s", now=NOW, backend="any")
    dec = store.global_rate_decision(max_per_hour=1, max_per_day=None, now=NOW)
    assert dec.allowed is False
    assert dec.window == "hourly"


def test_global_rate_decision_daily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution(role="r", task_id="a", signature="s", now=NOW - timedelta(hours=3))
    store.record_execution(role="r", task_id="b", signature="s", now=NOW - timedelta(hours=2))
    dec = store.global_rate_decision(max_per_hour=None, max_per_day=2, now=NOW)
    assert dec.allowed is False
    assert dec.window == "daily"


def test_global_rate_decision_bad_ts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps({"events": [{"kind": "execution", "timestamp": "bad"}]}),
        encoding="utf-8",
    )
    assert store.global_rate_decision(max_per_hour=1, max_per_day=1, now=NOW).allowed is True


# ---------------------------------------------------------------------------
# memory decisions
# ---------------------------------------------------------------------------


def test_available_memory_mb_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 100 kB\nMemAvailable: 2048 kB\nSwapFree: 1024 kB\n", encoding="utf-8"
    )
    import builtins

    real_open = builtins.open

    def _fake_open(path: str, *a: object, **k: object):  # type: ignore[no-untyped-def]
        if path == "/proc/meminfo":
            return real_open(meminfo, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", _fake_open)
    # 2048//1024 + 1024//1024 = 2 + 1 = 3
    assert UsageStore.available_memory_mb() == 3


def test_available_memory_mb_bad_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: xx\nSwapFree:\n", encoding="utf-8")
    import builtins

    real_open = builtins.open

    def _fake_open(path: str, *a: object, **k: object):  # type: ignore[no-untyped-def]
        if path == "/proc/meminfo":
            return real_open(meminfo, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", _fake_open)
    assert UsageStore.available_memory_mb() == 0


def test_available_memory_mb_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    def _boom(path: str, *a: object, **k: object):  # type: ignore[no-untyped-def]
        if path == "/proc/meminfo":
            raise OSError("no proc")
        raise AssertionError("unexpected open")

    monkeypatch.setattr(builtins, "open", _boom)
    assert UsageStore.available_memory_mb() == 0


def test_global_memory_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.global_memory_decision(min_available_memory_mb=None).allowed is True
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 0))
    assert store.global_memory_decision(min_available_memory_mb=100).allowed is True
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 50))
    dec = store.global_memory_decision(min_available_memory_mb=100)
    assert dec.allowed is False
    assert dec.reason == "global_memory_insufficient"
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 500))
    assert store.global_memory_decision(min_available_memory_mb=100).allowed is True


def test_memory_decision_for_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert (
        store.memory_decision_for_backend("", min_available_memory_mb=100, now=NOW).allowed is True
    )
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 0))
    assert (
        store.memory_decision_for_backend("dag", min_available_memory_mb=100, now=NOW).allowed
        is True
    )
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 50))
    dec = store.memory_decision_for_backend("dag", min_available_memory_mb=100, now=NOW)
    assert dec.allowed is False
    assert dec.reason == "backend_memory_insufficient"
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 500))
    assert (
        store.memory_decision_for_backend("dag", min_available_memory_mb=100, now=NOW).allowed
        is True
    )


def test_concurrency_decision_for_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.concurrency_decision_for_backend("", max_concurrent=1, now=NOW).allowed is True
    assert (
        store.concurrency_decision_for_backend("dag", max_concurrent=None, now=NOW).allowed is True
    )
    store.record_execution_started(task_id="a", backend="dag", now=NOW)
    dec = store.concurrency_decision_for_backend("dag", max_concurrent=1, now=NOW)
    assert dec.allowed is False
    assert dec.reason == "backend_concurrency_exceeded"


# ---------------------------------------------------------------------------
# failure rate degradation
# ---------------------------------------------------------------------------


def test_check_failure_rate_degradation_low_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_outcome(task_id="t", role="r", succeeded=False, now=NOW)
    assert store.check_failure_rate_degradation(now=NOW) is None


def test_check_failure_rate_degradation_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    for i in range(6):
        store.record_execution_outcome(
            task_id=f"t{i}", role="r", succeeded=i < 2, now=NOW - timedelta(minutes=i)
        )
    rate = store.check_failure_rate_degradation(now=NOW, warn_threshold=0.6)
    assert rate is not None
    assert rate < 0.6


def test_check_failure_rate_degradation_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    for i in range(6):
        store.record_execution_outcome(
            task_id=f"t{i}", role="r", succeeded=True, now=NOW - timedelta(minutes=i)
        )
    assert store.check_failure_rate_degradation(now=NOW) is None


# ---------------------------------------------------------------------------
# durations
# ---------------------------------------------------------------------------


def test_record_and_median_duration_odd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for d in (10.0, 30.0, 20.0):
        store.record_execution_duration(task_id="t", role="exec", duration_seconds=d, now=NOW)
    assert store.median_execution_duration("exec", now=NOW) == 20.0


def test_median_duration_even(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for d in (10.0, 20.0, 30.0, 40.0):
        store.record_execution_duration(task_id="t", role="exec", duration_seconds=d, now=NOW)
    assert store.median_execution_duration("exec", now=NOW) == 25.0


def test_median_duration_too_few(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_duration(task_id="t", role="exec", duration_seconds=5.0, now=NOW)
    assert store.median_execution_duration("exec", now=NOW) is None


def test_median_duration_default_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    for d in (1.0, 2.0, 3.0):
        store.record_execution_duration(task_id="t", role="exec", duration_seconds=d, now=now)
    assert store.median_execution_duration("exec") == 2.0


# ---------------------------------------------------------------------------
# audit_export
# ---------------------------------------------------------------------------


def test_audit_export_all_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_duration(task_id="t1", role="exec", duration_seconds=12.0, now=NOW)
    store.record_execution_outcome(
        task_id="t1", role="exec", succeeded=True, now=NOW, backend="dag", backend_version="v1"
    )
    store.record_execution_outcome(task_id="t2", role="exec", succeeded=False, now=NOW)
    store.record_quota_event(task_id="t3", role="exec", backend="team", now=NOW)
    store.record_quality_warning(
        task_id="t4", repo_key="repo", suppression_counts={"noqa": 1}, now=NOW
    )
    store.record_scope_violation(task_id="t5", repo_key="repo", violated_files=["a.py"], now=NOW)
    store.record_escalation(classification="flaky", task_ids=["t6"], now=NOW)
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
    # duration linked for t1
    succ = next(r for r in rows if r["task_id"] == "t1" and r["outcome"] == "succeeded")
    assert succ["duration_seconds"] == 12.0
    assert succ["backend_version"] == "v1"


def test_audit_export_cutoff_and_bad_ts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "execution_outcome",
                        "task_id": "old",
                        "succeeded": True,
                        "timestamp": _iso(NOW - timedelta(days=10)),
                    },
                    {
                        "kind": "execution_outcome",
                        "task_id": "bad",
                        "succeeded": True,
                        "timestamp": "garbage",
                    },
                    {
                        "kind": "execution_outcome",
                        "task_id": "ok",
                        "succeeded": True,
                        "timestamp": _iso(NOW),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    rows = store.audit_export(window_days=7, now=NOW)
    # old is pruned by _prune_events (7d) anyway; bad ts skipped; only ok remains
    assert [r["task_id"] for r in rows] == ["ok"]


def test_audit_export_default_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_outcome(task_id="t", role="r", succeeded=False, now=datetime.now(UTC))
    rows = store.audit_export()
    assert rows and rows[0]["outcome"] == "failed"


# ---------------------------------------------------------------------------
# record_skip
# ---------------------------------------------------------------------------


def test_record_skip_noop_persists_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_skip(role="r", task_id="t", signature="sig", reason="no_op", detail="d", now=NOW)
    data = store.load()
    assert data["last_task_signatures"]["r:t"] == "sig"
    assert data["events"][0]["kind"] == "skip_noop"


def test_record_skip_budget_no_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_skip(role="r", task_id="t", signature="sig", reason="budget", detail=None, now=NOW)
    data = store.load()
    assert "r:t" not in data.get("last_task_signatures", {})
    assert data["events"][0]["kind"] == "skip_budget"


def test_record_skip_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_skip(
        role="r",
        task_id="t",
        signature="sig",
        reason="cooldown_active",
        detail=None,
        now=NOW,
        evidence={"k": "v"},
    )
    ev = store.load()["events"][0]
    assert ev["kind"] == "skip_cooldown"
    assert ev["evidence"] == {"k": "v"}


# ---------------------------------------------------------------------------
# record_retry_cap
# ---------------------------------------------------------------------------


def test_record_retry_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_retry_cap(role="r", task_id="t", now=NOW, attempts=3, limit=3)
    ev = store.load()["events"][0]
    assert ev["kind"] == "retry_cap_block"
    assert ev["attempts"] == 3


# ---------------------------------------------------------------------------
# proposal cycle / satiation
# ---------------------------------------------------------------------------


def test_proposal_cycle_satiated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(5):
        store.record_proposal_cycle(created=0, deduped=9, skipped=1, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is True


def test_proposal_not_satiated_too_few(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_proposal_cycle(created=0, deduped=10, skipped=0, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is False


def test_proposal_not_satiated_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(5):
        store.record_proposal_cycle(created=1, deduped=9, skipped=0, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is False


def test_proposal_not_satiated_zero_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(5):
        store.record_proposal_cycle(created=0, deduped=0, skipped=0, now=NOW)
    assert store.is_proposal_satiated(now=NOW) is False


def test_proposal_not_satiated_below_ratio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(5):
        store.record_proposal_cycle(created=0, deduped=1, skipped=0, now=NOW)
    # ratio = 1.0 actually; use a low-ratio composition instead
    assert store.is_proposal_satiated(now=NOW, dedup_ratio_threshold=2.0) is False


def test_reset_satiation_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_proposal_cycle(created=0, deduped=1, skipped=0, now=NOW)
    store.record_execution(role="r", task_id="t", signature="s", now=NOW)
    store.reset_satiation_window(now=NOW)
    kinds = {e["kind"] for e in store.load()["events"]}
    assert "proposal_cycle" not in kinds
    assert "execution" in kinds


# ---------------------------------------------------------------------------
# proposal outcomes / success rate
# ---------------------------------------------------------------------------


def test_proposal_success_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for s in (True, True, False, True):
        store.record_proposal_outcome(category="cat", succeeded=s, now=NOW)
    assert store.proposal_success_rate("cat", now=NOW) == 0.75


def test_proposal_success_rate_neutral(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_proposal_outcome(category="cat", succeeded=True, now=NOW)
    assert store.proposal_success_rate("cat", now=NOW) == 0.5


def test_proposal_success_rate_default_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(3):
        store.record_proposal_outcome(category="cat", succeeded=True, now=datetime.now(UTC))
    assert store.proposal_success_rate("cat") == 1.0


# ---------------------------------------------------------------------------
# validation outcomes / flaky
# ---------------------------------------------------------------------------


def test_is_command_flaky_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for i in range(10):
        store.record_validation_outcome(command="pytest", passed=i >= 5, now=NOW)
    assert store.is_command_flaky("pytest", now=NOW) is True


def test_is_command_flaky_too_few(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_validation_outcome(command="pytest", passed=False, now=NOW)
    assert store.is_command_flaky("pytest", now=NOW) is False


def test_is_command_flaky_default_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for _ in range(10):
        store.record_validation_outcome(command="pytest", passed=True, now=datetime.now(UTC))
    assert store.is_command_flaky("pytest") is False


# ---------------------------------------------------------------------------
# escalation
# ---------------------------------------------------------------------------


def test_should_escalate_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for i in range(3):
        store.record_blocked_triage(task_id=f"t{i}", classification="ci", now=NOW)
    fire, ids = store.should_escalate(
        classification="ci", threshold=3, cooldown_seconds=3600, now=NOW
    )
    assert fire is True
    assert set(ids) == {"t0", "t1", "t2"}


def test_should_escalate_below_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_blocked_triage(task_id="t0", classification="ci", now=NOW)
    fire, ids = store.should_escalate(
        classification="ci", threshold=3, cooldown_seconds=3600, now=NOW
    )
    assert fire is False
    assert ids == []


def test_should_escalate_cooldown_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_escalation(classification="ci", task_ids=["x"], now=NOW)
    for i in range(3):
        store.record_blocked_triage(task_id=f"t{i}", classification="ci", now=NOW)
    fire, ids = store.should_escalate(
        classification="ci", threshold=3, cooldown_seconds=3600, now=NOW
    )
    assert fire is False


def test_should_escalate_bad_and_empty_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    aware = _iso(NOW)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "blocked_triage",
                        "task_id": "t0",
                        "classification": "ci",
                        "timestamp": aware,
                    },
                    {
                        "kind": "blocked_triage",
                        "task_id": "t1",
                        "classification": "ci",
                        "timestamp": aware,
                    },
                    {
                        "kind": "blocked_triage",
                        "task_id": "",
                        "classification": "ci",
                        "timestamp": aware,
                    },
                    {
                        "kind": "blocked_triage",
                        "task_id": "t2",
                        "classification": "other",
                        "timestamp": aware,
                    },
                    {"kind": "escalation_sent", "classification": "other", "timestamp": aware},
                ]
            }
        ),
        encoding="utf-8",
    )
    fire, ids = store.should_escalate(
        classification="ci", threshold=2, cooldown_seconds=3600, now=NOW
    )
    assert fire is True
    assert set(ids) == {"t0", "t1"}


# ---------------------------------------------------------------------------
# consecutive blocks
# ---------------------------------------------------------------------------


def test_consecutive_blocks_for_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_outcome(task_id="t", role="r", succeeded=True, now=NOW)
    store.record_blocked_triage(task_id="t", classification="ci", now=NOW)
    store.record_blocked_triage(task_id="t", classification="ci", now=NOW)
    # other task interleaved should be skipped
    store.record_blocked_triage(task_id="other", classification="ci", now=NOW)
    assert store.consecutive_blocks_for_task("t", now=NOW) == 2


def test_consecutive_blocks_stops_at_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_blocked_triage(task_id="t", classification="ci", now=NOW)
    store.record_execution_outcome(task_id="t", role="r", succeeded=True, now=NOW)
    store.record_blocked_triage(task_id="t", classification="ci", now=NOW)
    # walking backwards: 1 block, then success -> stop
    assert store.consecutive_blocks_for_task("t", now=NOW) == 1


# ---------------------------------------------------------------------------
# cost / spend
# ---------------------------------------------------------------------------


def test_spend_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_cost(task_id="t1", repo_key="repo", estimated_usd=1.5, now=NOW)
    store.record_execution_cost(task_id="t2", repo_key="repo", estimated_usd=2.5, now=NOW)
    report = store.get_spend_report(now=NOW)
    assert report["total_executions"] == 2
    assert report["total_estimated_usd"] == 4.0
    assert report["per_repo"]["repo"]["executions"] == 2


def test_spend_report_unknown_repo_and_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps(
            {
                "events": [
                    {"kind": "execution_cost", "estimated_usd": 3.0, "timestamp": _iso(NOW)},
                    {
                        "kind": "execution_cost",
                        "repo_key": "r",
                        "estimated_usd": 9.0,
                        "timestamp": _iso(NOW - timedelta(days=5)),
                    },
                    {
                        "kind": "execution_cost",
                        "repo_key": "r",
                        "estimated_usd": 1.0,
                        "timestamp": "bad",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    report = store.get_spend_report(window_days=1, now=NOW)
    assert report["per_repo"]["unknown"]["estimated_usd"] == 3.0
    assert "r" not in report["per_repo"]


def test_spend_report_default_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_execution_cost(
        task_id="t", repo_key="repo", estimated_usd=1.0, now=datetime.now(UTC)
    )
    report = store.get_spend_report()
    assert report["total_executions"] == 1


# ---------------------------------------------------------------------------
# proposal budget suppression / artifacts / signatures
# ---------------------------------------------------------------------------


def test_record_proposal_budget_suppression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_proposal_budget_suppression(reason="cap", now=NOW, evidence={"x": 1})
    ev = store.load()["events"][0]
    assert ev["kind"] == "proposal_budget_suppressed"
    assert ev["evidence"] == {"x": 1}


def test_task_artifact_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.record_task_artifact(task_id="t", artifact={"outcome_status": "done"}, now=NOW)
    art = store.get_task_artifact("t")
    assert art is not None
    assert art["outcome_status"] == "done"
    assert art["recorded_at"] == _iso(NOW)


def test_get_task_artifact_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert store.get_task_artifact("nope") is None


def test_get_task_artifact_non_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps({"events": [], "task_artifacts": {"t": "not-a-dict"}}), encoding="utf-8"
    )
    assert store.get_task_artifact("t") is None


# ---------------------------------------------------------------------------
# issue_signature
# ---------------------------------------------------------------------------


def test_issue_signature_state_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = UsageStore.issue_signature(
        {"id": "1", "state": {"name": "Open"}, "updated_at": "2026-01-01", "description": "hi"}
    )
    assert sig == "1|Open|2026-01-01|hi"


def test_issue_signature_state_str_and_html_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sig = UsageStore.issue_signature(
        {"id": "2", "state": "Closed", "updated": "2026-02-02", "description_html": "<p>x</p>"}
    )
    assert sig == "2|Closed|2026-02-02|<p>x</p>"


def test_issue_signature_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = UsageStore.issue_signature({})
    assert sig == "|||"


# ---------------------------------------------------------------------------
# _exec_count / _prune_events edge cases
# ---------------------------------------------------------------------------


def test_exec_count_skips_non_execution_and_bad_ts() -> None:
    events = [
        {"kind": "skip_noop", "timestamp": _iso(NOW)},
        {"kind": "execution", "timestamp": "bad"},
        {"kind": "execution", "timestamp": _iso(NOW)},
    ]
    assert UsageStore._exec_count(events, since=NOW - timedelta(hours=1)) == 1


def test_prune_events_drops_old_and_bad() -> None:
    events = [
        {"kind": "execution", "timestamp": _iso(NOW - timedelta(days=10))},
        {"kind": "execution", "timestamp": "bad"},
        {"kind": "execution", "timestamp": _iso(NOW)},
    ]
    retained = UsageStore._prune_events(events, now=NOW)
    assert len(retained) == 1
    assert retained[0]["timestamp"] == _iso(NOW)


def test_prune_events_caps_at_1000() -> None:
    events = [{"kind": "execution", "timestamp": _iso(NOW)} for _ in range(1200)]
    retained = UsageStore._prune_events(events, now=NOW)
    assert len(retained) == 1000


def test_worker_backend_cooldown_until_with_naive_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # reset_at with timezone vs now; ensures comparison path executes
    store = _store(tmp_path, monkeypatch)
    store.record_worker_backend_cooldown(
        worker_backend="codex_cli", reset_at=NOW + timedelta(hours=1), now=NOW, model="codex"
    )
    until = store.worker_backend_cooldown_until("codex_cli", now=NOW)
    assert until == NOW + timedelta(hours=1)
