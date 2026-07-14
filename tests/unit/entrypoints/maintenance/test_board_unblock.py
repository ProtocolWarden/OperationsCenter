# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime

from operations_center.entrypoints.maintenance.board_unblock import (
    _allowed_worker_backends,
    _apply_rules,
    _dispatch_cooldown_reason,
)


def _issue(
    task_id: str,
    *,
    state: str,
    labels: list[str],
    updated_at: str = "2026-05-27T15:00:00+00:00",
) -> dict:
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": label} for label in labels],
        "updated_at": updated_at,
    }


_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
_RULES_KWARGS = dict(
    now=_NOW,
    stale_blocked_hours=4,
    stale_running_hours=2,
    clean_blocked_min_minutes=5,
    mem_available_gb=20.0,
)


# --- Rule 8: CLEAN_BLOCKED_RETRY covers spec-author ---


def test_rule8_clean_blocked_retry_spec_author():
    """Rule 8 should re-queue spec-author tasks stuck in Blocked with no executor labels."""
    issue = _issue(
        "t1",
        state="Blocked",
        labels=["task-kind: spec-author", "source: spec-director"],
        updated_at="2026-05-28T10:00:00+00:00",
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    retry_actions = [a for a in actions if a["rule"] == "CLEAN_BLOCKED_RETRY"]
    assert len(retry_actions) == 1
    assert retry_actions[0]["to_state"] == "Backlog"


def test_rule8_skip_spec_author_with_sigkill():
    """Rule 8 must NOT re-queue spec-author tasks with executor-signal label."""
    issue = _issue(
        "t2",
        state="Blocked",
        labels=["task-kind: spec-author", "executor-signal: SIGKILL"],
        updated_at="2026-05-28T10:00:00+00:00",
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    retry_actions = [a for a in actions if a["rule"] == "CLEAN_BLOCKED_RETRY"]
    assert len(retry_actions) == 0


def test_rule8_skip_spec_author_too_young():
    """Rule 8 must NOT re-queue tasks that are too young (< 5 min)."""
    issue = _issue(
        "t3",
        state="Blocked",
        labels=["task-kind: spec-author"],
        updated_at="2026-05-28T11:59:00+00:00",
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    retry_actions = [a for a in actions if a["rule"] == "CLEAN_BLOCKED_RETRY"]
    assert len(retry_actions) == 0


# --- Rule 9: SPEC_AUTHOR_BACKLOG_PROMOTE ---


def test_rule9_spec_author_backlog_promote():
    """Rule 9 should promote spec-author tasks from Backlog to R4AI."""
    issue = _issue(
        "t4",
        state="Backlog",
        labels=["task-kind: spec-author", "source: spec-director"],
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    promote_actions = [a for a in actions if a["rule"] == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(promote_actions) == 1
    assert promote_actions[0]["to_state"] == "Ready for AI"


def test_rule9_skip_non_spec_author():
    """Rule 9 must NOT promote tasks that are not spec-author kind."""
    issue = _issue(
        "t5",
        state="Backlog",
        labels=["task-kind: improve"],
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    promote_actions = [a for a in actions if a["rule"] == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(promote_actions) == 0


def test_rule9_skip_when_low_memory():
    """Rule 9 must NOT promote when memory is below the R4AI threshold."""
    issue = _issue(
        "t6",
        state="Backlog",
        labels=["task-kind: spec-author"],
    )
    low_mem_kwargs = dict(_RULES_KWARGS, mem_available_gb=4.0)
    actions = _apply_rules([issue], **low_mem_kwargs)
    promote_actions = [a for a in actions if a["rule"] == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(promote_actions) == 0


# --- Rule 5: STALE_IN_REVIEW ---


def test_rule5_stale_in_review_fires_without_pr_url():
    """Rule 5 should demote stale In Review tasks with no pr-url label."""
    issue = _issue(
        "t7",
        state="In Review",
        labels=["task-kind: goal"],
        updated_at="2026-05-28T07:00:00+00:00",  # 5h before _NOW
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    stale_actions = [a for a in actions if a["rule"] == "STALE_IN_REVIEW"]
    assert len(stale_actions) == 1
    assert stale_actions[0]["to_state"] == "Backlog"


def test_rule5_stale_in_review_skipped_with_pr_url_label():
    """Rule 5 must NOT demote In Review tasks that carry a pr-url: label (open PR)."""
    issue = _issue(
        "t8",
        state="In Review",
        labels=["task-kind: goal", "pr-url: https://github.com/org/repo/pull/42"],
        updated_at="2026-05-28T07:00:00+00:00",  # 5h before _NOW
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    stale_actions = [a for a in actions if a["rule"] == "STALE_IN_REVIEW"]
    assert len(stale_actions) == 0


def test_rule5_stale_in_review_not_stale():
    """Rule 5 must NOT fire for In Review tasks updated within the staleness window."""
    issue = _issue(
        "t9",
        state="In Review",
        labels=["task-kind: goal"],
        updated_at="2026-05-28T10:00:00+00:00",  # 2h before _NOW — within 4h threshold
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    stale_actions = [a for a in actions if a["rule"] == "STALE_IN_REVIEW"]
    assert len(stale_actions) == 0


def test_rule8_thin_goal_label_prevents_retry():
    """CLEAN_BLOCKED_RETRY must NOT fire for tasks carrying the thin-goal label."""
    issue = _issue(
        "t_thin",
        state="Blocked",
        labels=["task-kind: goal", "thin-goal"],
        updated_at="2026-05-28T10:00:00+00:00",
    )
    actions = _apply_rules([issue], **_RULES_KWARGS)
    retry_actions = [a for a in actions if a["rule"] == "CLEAN_BLOCKED_RETRY"]
    assert len(retry_actions) == 0


def test_open_pr_gate_ready_goal_is_parked_to_backlog():
    issue = _issue(
        "t_gate_ready",
        state="Ready for AI",
        labels=["task-kind: goal", "repo: OperationsCenter"],
    )
    actions = _apply_rules(
        [issue],
        **_RULES_KWARGS,
        open_pr_gate_blocked_repos={"OperationsCenter"},
    )
    parked = [a for a in actions if a["rule"] == "OPEN_PR_GATE_PARK"]
    assert len(parked) == 1
    assert parked[0]["to_state"] == "Backlog"
    assert parked[0]["labels_to_add"] == ["OPEN_PR_GATE"]


def test_open_pr_gate_backlog_goal_requeues_when_gate_clears():
    issue = _issue(
        "t_gate_backlog",
        state="Backlog",
        labels=["task-kind: goal", "repo: OperationsCenter", "OPEN_PR_GATE"],
    )
    actions = _apply_rules([issue], **_RULES_KWARGS, open_pr_gate_blocked_repos=set())
    requeued = [a for a in actions if a["rule"] == "OPEN_PR_GATE_REQUEUE"]
    assert len(requeued) == 1
    assert requeued[0]["to_state"] == "Ready for AI"
    assert requeued[0]["labels_to_remove"] == ["OPEN_PR_GATE"]


def test_open_pr_gate_backlog_goal_stays_parked_while_repo_still_blocked():
    issue = _issue(
        "t_gate_blocked",
        state="Backlog",
        labels=["task-kind: goal", "repo: OperationsCenter", "OPEN_PR_GATE"],
    )
    actions = _apply_rules(
        [issue],
        **_RULES_KWARGS,
        open_pr_gate_blocked_repos={"OperationsCenter"},
    )
    requeued = [a for a in actions if a["rule"] == "OPEN_PR_GATE_REQUEUE"]
    assert requeued == []


# --- Worker-backend cooldown gate (Rules 4, 6, 7, 9 defer R4AI promotion) ---

_COOLDOWN_REASON = (
    "all allowed worker backends cooling down (claude_code until 2026-06-03T13:00:00+00:00)"
)


def test_rule9_promote_deferred_during_cooldown():
    """Rule 9 must NOT promote spec-author Backlog→R4AI while the backend is cooling down."""
    issue = _issue(
        "t_cd9",
        state="Backlog",
        labels=["task-kind: spec-author", "source: spec-director"],
    )
    actions = _apply_rules([issue], **_RULES_KWARGS, cooldown_skip_reason=_COOLDOWN_REASON)
    promote = [a for a in actions if a["rule"] == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(promote) == 1
    assert promote[0].get("skipped") is True
    assert "cooling down" in promote[0]["reason"]


def test_rule7_promote_deferred_during_cooldown():
    """Rule 7 must defer goal Backlog→R4AI promotion while the backend is cooling down."""
    parent = _issue("p_cd7", state="Done", labels=["task-kind: improve"])
    child = _issue(
        "t_cd7",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: autonomy",
            "source: improve-suggestion",
            "original-task-id: p_cd7",
        ],
    )
    actions = _apply_rules([parent, child], **_RULES_KWARGS, cooldown_skip_reason=_COOLDOWN_REASON)
    promote = [a for a in actions if a["rule"] == "GOAL_BACKLOG_PROMOTE"]
    assert len(promote) == 1
    assert promote[0].get("skipped") is True


def test_rule4_self_modify_requeue_deferred_during_cooldown():
    """Rule 4 must defer self-modify:approved Blocked→R4AI while the backend is cooling down."""
    issue = _issue(
        "t_cd4",
        state="Blocked",
        labels=["self-modify: approved"],
    )
    actions = _apply_rules([issue], **_RULES_KWARGS, cooldown_skip_reason=_COOLDOWN_REASON)
    requeue = [a for a in actions if a["rule"] == "SELF_MODIFY_REQUEUE"]
    assert len(requeue) == 1
    assert requeue[0].get("skipped") is True


def test_rule8_park_still_fires_during_cooldown():
    """Rule 8 (Blocked→Backlog parking) is NOT gated — it must still fire during a cooldown."""
    issue = _issue(
        "t_cd8",
        state="Blocked",
        labels=["task-kind: goal"],
        updated_at="2026-05-28T10:00:00+00:00",
    )
    actions = _apply_rules([issue], **_RULES_KWARGS, cooldown_skip_reason=_COOLDOWN_REASON)
    retry = [a for a in actions if a["rule"] == "CLEAN_BLOCKED_RETRY"]
    assert len(retry) == 1
    assert retry[0]["to_state"] == "Backlog"
    assert not retry[0].get("skipped")


def test_open_pr_gate_requeue_deferred_during_cooldown():
    issue = _issue(
        "t_cd_gate",
        state="Backlog",
        labels=["task-kind: goal", "repo: OperationsCenter", "OPEN_PR_GATE"],
    )
    actions = _apply_rules(
        [issue],
        **_RULES_KWARGS,
        cooldown_skip_reason=_COOLDOWN_REASON,
        open_pr_gate_blocked_repos=set(),
    )
    requeue = [a for a in actions if a["rule"] == "OPEN_PR_GATE_REQUEUE"]
    assert len(requeue) == 1
    assert requeue[0].get("skipped") is True


def test_rule7_goal_backlog_promote_skips_open_pr_gate_label():
    parent = _issue("p_gate", state="Done", labels=["task-kind: improve"])
    child = _issue(
        "t_gate",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: autonomy",
            "source: improve-suggestion",
            "original-task-id: p_gate",
            "repo: OperationsCenter",
            "OPEN_PR_GATE",
        ],
    )
    actions = _apply_rules(
        [parent, child],
        **_RULES_KWARGS,
        open_pr_gate_blocked_repos=set(),
    )
    promote = [a for a in actions if a["rule"] == "GOAL_BACKLOG_PROMOTE"]
    assert promote == []
    requeue = [a for a in actions if a["rule"] == "OPEN_PR_GATE_REQUEUE"]
    assert len(requeue) == 1


def test_rule7_goal_backlog_promote_skips_open_pr_gate_blocked_repo():
    parent = _issue("p_gate_repo", state="Done", labels=["task-kind: improve"])
    child = _issue(
        "t_gate_repo",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: autonomy",
            "source: improve-suggestion",
            "original-task-id: p_gate_repo",
            "repo: OperationsCenter",
        ],
    )
    actions = _apply_rules(
        [parent, child],
        **_RULES_KWARGS,
        open_pr_gate_blocked_repos={"OperationsCenter"},
    )
    promote = [a for a in actions if a["rule"] == "GOAL_BACKLOG_PROMOTE"]
    assert promote == []


def test_no_cooldown_reason_promotes_normally():
    """With no cooldown reason, promotion rules apply as before (regression guard)."""
    issue = _issue("t_nocd", state="Backlog", labels=["task-kind: spec-author"])
    actions = _apply_rules([issue], **_RULES_KWARGS)
    promote = [a for a in actions if a["rule"] == "SPEC_AUTHOR_BACKLOG_PROMOTE"]
    assert len(promote) == 1
    assert not promote[0].get("skipped")
    assert promote[0]["to_state"] == "Ready for AI"


def test_allowed_worker_backends_defaults_to_claude_code(monkeypatch):
    monkeypatch.delenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", raising=False)
    assert _allowed_worker_backends() == {"claude_code"}


def test_allowed_worker_backends_maps_providers(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude,codex")
    assert _allowed_worker_backends() == {"claude_code", "codex_cli"}


class _FakeUsageStore:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def current_worker_backend_cooldowns(self, *, now):
        return self._snapshot


def test_dispatch_cooldown_reason_blocks_when_only_backend_cooling(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _FakeUsageStore(
        {
            "claude_code": {"cooling_down": True, "reset_at": "2026-06-03T13:00:00+00:00"},
            "codex_cli": {"cooling_down": False, "reset_at": None},
        }
    )
    reason = _dispatch_cooldown_reason(store, now=_NOW)
    assert reason is not None
    assert "claude_code" in reason


def test_dispatch_cooldown_reason_none_when_alternate_free(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude,codex")
    store = _FakeUsageStore(
        {
            "claude_code": {"cooling_down": True, "reset_at": "2026-06-03T13:00:00+00:00"},
            "codex_cli": {"cooling_down": False, "reset_at": None},
        }
    )
    assert _dispatch_cooldown_reason(store, now=_NOW) is None


def test_dispatch_cooldown_reason_none_when_nothing_cooling(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _FakeUsageStore(
        {
            "claude_code": {"cooling_down": False, "reset_at": None},
            "codex_cli": {"cooling_down": False, "reset_at": None},
        }
    )
    assert _dispatch_cooldown_reason(store, now=_NOW) is None


class _RefreshableFakeUsageStore(_FakeUsageStore):
    """Fake store whose snapshot a refresh callback can flip to runnable."""

    def set_runnable(self, backend: str) -> None:
        self._snapshot[backend] = {"cooling_down": False, "reset_at": None}


def test_dispatch_cooldown_reason_self_heals_via_refresh(monkeypatch):
    # All allowed backends look cooling, but a probe proves one is runnable: the
    # injected refresh clears it, the gate re-reads, and dispatch is no longer
    # deferred. This is the probe-and-clear self-heal at the dispatch boundary.
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _RefreshableFakeUsageStore(
        {"claude_code": {"cooling_down": True, "reset_at": "2026-06-03T13:00:00+00:00"}}
    )
    calls: list[str] = []

    def fake_refresh(s, *, now):
        calls.append("refreshed")
        s.set_runnable("claude_code")

    assert _dispatch_cooldown_reason(store, now=_NOW, refresh=fake_refresh) is None
    assert calls == ["refreshed"]


def test_dispatch_cooldown_reason_still_blocks_when_refresh_finds_nothing(monkeypatch):
    # Refresh runs but the limit is genuinely still active → gate still defers.
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _RefreshableFakeUsageStore(
        {"claude_code": {"cooling_down": True, "reset_at": "2026-06-03T13:00:00+00:00"}}
    )
    reason = _dispatch_cooldown_reason(store, now=_NOW, refresh=lambda s, *, now: None)
    assert reason is not None
    assert "claude_code" in reason


def test_rule7_goal_backlog_promote_skips_thin_goal():
    """GOAL_BACKLOG_PROMOTE must NOT fire for Backlog goal tasks carrying thin-goal."""
    parent = _issue(
        "parent_done",
        state="Done",
        labels=["task-kind: improve"],
    )
    child = _issue(
        "t_thin_backlog",
        state="Backlog",
        labels=[
            "task-kind: goal",
            "source: autonomy",
            "source: improve-suggestion",
            "original-task-id: parent_done",
            "thin-goal",
        ],
    )
    actions = _apply_rules([parent, child], **_RULES_KWARGS)
    promote_actions = [a for a in actions if a["rule"] == "GOAL_BACKLOG_PROMOTE"]
    assert len(promote_actions) == 0
