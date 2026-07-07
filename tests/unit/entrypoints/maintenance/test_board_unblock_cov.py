# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest

import operations_center.entrypoints.maintenance.board_unblock as bu

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
_FRESH = "2026-05-28T11:59:00+00:00"  # 1 minute ago relative to _NOW
_STALE = "2026-05-28T05:00:00+00:00"  # 7 hours ago


def _issue(
    task_id: str,
    *,
    state: str,
    labels: list[str] | None = None,
    name: str | None = None,
    updated_at: str | None = _STALE,
) -> dict:
    issue: dict = {
        "id": task_id,
        "name": name if name is not None else f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": label} for label in (labels or [])],
    }
    if updated_at is not None:
        issue["updated_at"] = updated_at
    return issue


def _run(issues, **kwargs):
    params = dict(
        now=_NOW,
        stale_blocked_hours=4,
        stale_running_hours=2,
        clean_blocked_min_minutes=5,
        mem_available_gb=16.0,
    )
    params.update(kwargs)
    return bu._apply_rules(issues, **params)


def _by_rule(actions, rule):
    return [a for a in actions if a["rule"] == rule]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_labels_handles_dicts_strings_and_blanks():
    issue = {"labels": [{"name": " a "}, "b", {"name": ""}, {"nope": 1}, None]}
    assert bu._labels(issue) == ["a", "b"]


def test_labels_none_labels():
    assert bu._labels({"labels": None}) == []
    assert bu._labels({}) == []


def test_state_name_dict_and_string_and_none():
    assert bu._state_name({"state": {"name": " Blocked "}}) == "Blocked"
    assert bu._state_name({"state": "Running"}) == "Running"
    assert bu._state_name({"state": None}) == ""
    assert bu._state_name({}) == ""


def test_label_value_case_insensitive_prefix():
    labels = ["Retry-Count: 5", "other"]
    assert bu._label_value(labels, "retry-count:") == "5"
    assert bu._label_value(labels, "missing:") is None


def test_has_label_and_prefix():
    labels = ["Task-Kind: goal", "self-modify: approved"]
    assert bu._has_label(labels, "task-kind: goal")
    assert not bu._has_label(labels, "task-kind: improve")
    assert bu._has_label_prefix(labels, "self-modify:")
    assert not bu._has_label_prefix(labels, "blocked-by:")


def test_parse_updated_at_variants():
    assert bu._parse_updated_at({"updated_at": "2026-05-28T05:00:00Z"}) == datetime(
        2026, 5, 28, 5, 0, 0, tzinfo=UTC
    )
    # naive datetime gets UTC attached
    assert bu._parse_updated_at({"updated_at": "2026-05-28T05:00:00"}).tzinfo == UTC
    # falls back to created_at
    assert bu._parse_updated_at({"created_at": "2026-05-28T05:00:00Z"}) is not None
    # missing
    assert bu._parse_updated_at({}) is None
    # invalid
    assert bu._parse_updated_at({"updated_at": "not-a-date"}) is None


def test_retry_count():
    assert bu._retry_count(["retry-count: 3"]) == 3
    assert bu._retry_count([]) == 0
    assert bu._retry_count(["retry-count: abc"]) == 0


def test_is_terminal():
    assert bu._is_terminal("Done")
    assert bu._is_terminal("CANCELLED")
    assert not bu._is_terminal("Blocked")


def test_blocker_task_id():
    assert bu._blocker_task_id(["blocked-by: T-1"]) == "T-1"
    assert bu._blocker_task_id([]) is None


def test_build_id_state_map():
    issues = [_issue("1", state="Done"), _issue("2", state="Blocked")]
    assert bu._build_id_state_map(issues) == {"1": "Done", "2": "Blocked"}


# ---------------------------------------------------------------------------
# _mem_available_gb
# ---------------------------------------------------------------------------


def test_mem_available_gb_reads_proc(monkeypatch, tmp_path):
    fake = tmp_path / "meminfo"
    fake.write_text("MemTotal: 100\nMemAvailable: 2097152 kB\n", encoding="utf-8")
    monkeypatch.setattr(bu, "Path", lambda p: fake)
    assert bu._mem_available_gb() == pytest.approx(2.0)


def test_mem_available_gb_no_match(monkeypatch, tmp_path):
    fake = tmp_path / "meminfo"
    fake.write_text("MemTotal: 100\n", encoding="utf-8")
    monkeypatch.setattr(bu, "Path", lambda p: fake)
    assert bu._mem_available_gb() == float("inf")


def test_mem_available_gb_read_error(monkeypatch):
    def boom(_):
        raise OSError("no /proc")

    monkeypatch.setattr(bu, "Path", boom)
    assert bu._mem_available_gb() == float("inf")


# ---------------------------------------------------------------------------
# _allowed_worker_backends
# ---------------------------------------------------------------------------


def test_allowed_backends_default(monkeypatch):
    monkeypatch.delenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", raising=False)
    assert bu._allowed_worker_backends() == {"claude_code"}


def test_allowed_backends_mapping(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude, codex")
    assert bu._allowed_worker_backends() == {"claude_code", "codex_cli"}


def test_allowed_backends_unknown_falls_back(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "weird, ")
    assert bu._allowed_worker_backends() == {"claude_code"}


# ---------------------------------------------------------------------------
# _dispatch_cooldown_reason
# ---------------------------------------------------------------------------


def _store(snapshot):
    store = mock.Mock()
    store.current_worker_backend_cooldowns.return_value = snapshot
    return store


def test_cooldown_reason_none_when_backend_free(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _store({"claude_code": {"cooling_down": False}})
    assert bu._dispatch_cooldown_reason(store, now=_NOW) is None


def test_cooldown_reason_returns_message(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _store({"claude_code": {"cooling_down": True, "reset_at": "2026-05-28T13:00:00+00:00"}})
    reason = bu._dispatch_cooldown_reason(store, now=_NOW)
    assert reason is not None
    assert "claude_code until 2026-05-28T13:00:00+00:00" in reason
    assert "promotion deferred" in reason


def test_cooldown_reason_without_reset_at(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _store({"claude_code": {"cooling_down": True}})
    reason = bu._dispatch_cooldown_reason(store, now=_NOW)
    assert "claude_code);" in reason or "claude_code)" in reason


def test_cooldown_reason_partial_free(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude, codex")
    store = _store(
        {
            "claude_code": {"cooling_down": True},
            "codex_cli": {"cooling_down": False},
        }
    )
    assert bu._dispatch_cooldown_reason(store, now=_NOW) is None


def test_cooldown_reason_exception_returns_none():
    store = mock.Mock()
    store.current_worker_backend_cooldowns.side_effect = RuntimeError("boom")
    assert bu._dispatch_cooldown_reason(store, now=_NOW) is None


def test_cooldown_reason_refresh_clears_stale(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = mock.Mock()
    # first call: cooling; after refresh: free
    store.current_worker_backend_cooldowns.side_effect = [
        {"claude_code": {"cooling_down": True}},
        {"claude_code": {"cooling_down": False}},
    ]
    refresh = mock.Mock()
    assert bu._dispatch_cooldown_reason(store, now=_NOW, refresh=refresh) is None
    refresh.assert_called_once()


def test_cooldown_reason_refresh_still_cooling(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = mock.Mock()
    store.current_worker_backend_cooldowns.side_effect = [
        {"claude_code": {"cooling_down": True}},
        {"claude_code": {"cooling_down": True, "reset_at": "later"}},
    ]
    refresh = mock.Mock()
    reason = bu._dispatch_cooldown_reason(store, now=_NOW, refresh=refresh)
    assert reason is not None
    refresh.assert_called_once()


def test_cooldown_reason_refresh_raises(monkeypatch):
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = mock.Mock()
    # both snapshot reads return cooling; refresh raises but is swallowed
    store.current_worker_backend_cooldowns.return_value = {"claude_code": {"cooling_down": True}}
    refresh = mock.Mock(side_effect=RuntimeError("probe failed"))
    reason = bu._dispatch_cooldown_reason(store, now=_NOW, refresh=refresh)
    assert reason is not None


def test_cooldown_reason_empty_cooling_returns_none(monkeypatch):
    # No allowed backend present in snapshot at all -> status empty -> not cooling -> None
    monkeypatch.setenv("OPERATIONS_CENTER_ALLOWED_PROVIDERS", "claude")
    store = _store({})
    assert bu._dispatch_cooldown_reason(store, now=_NOW) is None


# ---------------------------------------------------------------------------
# Rule 1 — DEAD_REMEDIATION_CANCEL
# ---------------------------------------------------------------------------


def test_rule1_dead_remediation_label():
    actions = _run([_issue("1", state="Blocked", labels=["dead-remediation"])])
    a = _by_rule(actions, "DEAD_REMEDIATION_CANCEL")
    assert a and a[0]["to_state"] == "Cancelled"
    assert a[0]["reason"] == "labelled dead-remediation"


def test_rule1_sigkill_exhausted():
    actions = _run(
        [
            _issue(
                "1",
                state="Running",
                labels=["executor-signal: SIGKILL", "retry-count: 3"],
            )
        ]
    )
    a = _by_rule(actions, "DEAD_REMEDIATION_CANCEL")
    assert a and "≥3 SIGKILL" in a[0]["reason"]


def test_rule1_sigkill_under_threshold_not_cancelled():
    actions = _run(
        [
            _issue(
                "1",
                state="Running",
                labels=["executor-signal: SIGKILL", "retry-count: 2"],
                updated_at=_FRESH,
            )
        ]
    )
    assert not _by_rule(actions, "DEAD_REMEDIATION_CANCEL")


def test_rule1_skips_terminal_state():
    actions = _run([_issue("1", state="Done", labels=["dead-remediation"])])
    assert not _by_rule(actions, "DEAD_REMEDIATION_CANCEL")


# Rule 1 — CODE_FAILURE_RETRY_CAP cancel
def test_rule1_code_fail_exhausted_cancels():
    actions = _run(
        [_issue("1", state="Blocked", labels=["task-kind: goal", "code-fail-count: 3"])],
        code_failure_retry_cap=3,
    )
    a = _by_rule(actions, "DEAD_REMEDIATION_CANCEL")
    assert a and "clean code failures" in a[0]["reason"]
    assert a[0]["to_state"] == "Cancelled"


def test_rule1_code_fail_under_cap_not_cancelled():
    actions = _run(
        [_issue("1", state="Blocked", labels=["code-fail-count: 2"], updated_at=_FRESH)],
        code_failure_retry_cap=3,
    )
    assert not _by_rule(actions, "DEAD_REMEDIATION_CANCEL")


def test_rule1_code_fail_cap_disabled_by_default():
    # default cap is 0 (disabled) → even an exhausted counter is not cancelled
    actions = _run(
        [_issue("1", state="Blocked", labels=["code-fail-count: 9"], updated_at=_FRESH)]
    )
    assert not _by_rule(actions, "DEAD_REMEDIATION_CANCEL")


# ---------------------------------------------------------------------------
# Rule 2 — INVESTIGATE_DEPRIORITISE
# ---------------------------------------------------------------------------


def test_rule2_investigate_demoted():
    actions = _run([_issue("1", state="Ready for AI", labels=["task-kind: investigate"])])
    a = _by_rule(actions, "INVESTIGATE_DEPRIORITISE")
    assert a and a[0]["to_state"] == "Backlog"


def test_rule2_not_in_r4ai_ignored():
    actions = _run([_issue("1", state="Backlog", labels=["task-kind: investigate"])])
    assert not _by_rule(actions, "INVESTIGATE_DEPRIORITISE")


# ---------------------------------------------------------------------------
# Rule 3 — IMPROVE_UNBLOCK
# ---------------------------------------------------------------------------


def test_rule3_blocker_terminal():
    issues = [
        _issue("dep", state="Done"),
        _issue(
            "1",
            state="Blocked",
            labels=["task-kind: improve", "blocked-by: dep"],
            updated_at=_FRESH,
        ),
    ]
    actions = _run(issues)
    a = _by_rule(actions, "IMPROVE_UNBLOCK")
    assert a and a[0]["to_state"] == "Backlog"
    assert "is now Done" in a[0]["reason"]


def test_rule3_stale_blocked_no_blocker():
    actions = _run([_issue("1", state="Blocked", labels=["task-kind: goal"], updated_at=_STALE)])
    a = _by_rule(actions, "IMPROVE_UNBLOCK")
    assert a and "stale in Blocked" in a[0]["reason"]


def test_rule3_blocker_not_terminal_and_fresh_no_action():
    issues = [
        _issue("dep", state="Running"),
        _issue(
            "1",
            state="Blocked",
            labels=["task-kind: improve", "blocked-by: dep"],
            updated_at=_FRESH,
        ),
    ]
    assert not _by_rule(_run(issues), "IMPROVE_UNBLOCK")


def test_rule3_self_modify_excluded():
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["task-kind: improve", "self-modify: approved"],
                updated_at=_STALE,
            )
        ]
    )
    assert not _by_rule(actions, "IMPROVE_UNBLOCK")


# ---------------------------------------------------------------------------
# Rule 4 — SELF_MODIFY_REQUEUE
# ---------------------------------------------------------------------------


def test_rule4_requeue_no_blocker():
    actions = _run([_issue("1", state="Blocked", labels=["self-modify: approved"])])
    a = _by_rule(actions, "SELF_MODIFY_REQUEUE")
    assert a and a[0]["to_state"] == "Ready for AI"
    assert "no blocking dependency" in a[0]["reason"]
    assert "skipped" not in a[0]


def test_rule4_requeue_blocker_terminal():
    issues = [
        _issue("dep", state="Cancelled"),
        _issue("1", state="Blocked", labels=["self-modify: approved", "blocked-by: dep"]),
    ]
    a = _by_rule(_run(issues), "SELF_MODIFY_REQUEUE")
    assert a and "is now Cancelled" in a[0]["reason"]


def test_rule4_blocker_active_no_action():
    issues = [
        _issue("dep", state="Running"),
        _issue("1", state="Blocked", labels=["self-modify: approved", "blocked-by: dep"]),
    ]
    assert not _by_rule(_run(issues), "SELF_MODIFY_REQUEUE")


def test_rule4_sigkill_skipped():
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["self-modify: approved", "executor-signal: SIGKILL", "retry-count: 1"],
            )
        ]
    )
    a = _by_rule(actions, "SELF_MODIFY_REQUEUE")
    assert a and a[0]["skipped"] is True
    assert "SIGKILL" in a[0]["reason"]


def test_rule4_blocked_reason_policy_skipped():
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["self-modify: approved", "blocked-reason: policy"],
            )
        ]
    )
    a = _by_rule(actions, "SELF_MODIFY_REQUEUE")
    assert a and a[0]["skipped"] is True
    assert "blocked-reason:policy" in a[0]["reason"]


def test_rule4_exit_code_zero_no_signal_skipped():
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["self-modify: approved", "executor-exit-code: 0"],
            )
        ]
    )
    a = _by_rule(actions, "SELF_MODIFY_REQUEUE")
    assert a and a[0]["skipped"] is True
    assert "empty result.json" in a[0]["reason"]


def test_rule4_low_mem_skipped():
    actions = _run(
        [_issue("1", state="Blocked", labels=["self-modify: approved"])],
        mem_available_gb=1.0,
    )
    a = _by_rule(actions, "SELF_MODIFY_REQUEUE")
    assert a and a[0]["skipped"] is True
    assert "GB threshold" in a[0]["reason"]


def test_rule4_cooldown_skipped():
    actions = _run(
        [_issue("1", state="Blocked", labels=["self-modify: approved"])],
        cooldown_skip_reason="cooling",
    )
    a = _by_rule(actions, "SELF_MODIFY_REQUEUE")
    assert a and a[0]["skipped"] is True
    assert "SKIPPED — cooling" in a[0]["reason"]


# ---------------------------------------------------------------------------
# Rule 5 — STALE_IN_REVIEW
# ---------------------------------------------------------------------------


def test_rule5_stale_in_review():
    actions = _run([_issue("1", state="In Review", updated_at=_STALE)])
    a = _by_rule(actions, "STALE_IN_REVIEW")
    assert a and a[0]["to_state"] == "Backlog"


def test_rule5_pr_url_skipped():
    actions = _run([_issue("1", state="In Review", labels=["pr-url: http://x"], updated_at=_STALE)])
    assert not _by_rule(actions, "STALE_IN_REVIEW")


def test_rule5_fresh_no_action():
    actions = _run([_issue("1", state="In Review", updated_at=_FRESH)])
    assert not _by_rule(actions, "STALE_IN_REVIEW")


def test_rule5_no_updated_at_no_action():
    actions = _run([_issue("1", state="In Review", updated_at=None)])
    assert not _by_rule(actions, "STALE_IN_REVIEW")


# ---------------------------------------------------------------------------
# Rule 6 — STALE_RUNNING_REQUEUE
# ---------------------------------------------------------------------------


def test_rule6_stale_running():
    actions = _run([_issue("1", state="Running", updated_at=_STALE)])
    a = _by_rule(actions, "STALE_RUNNING_REQUEUE")
    assert a and a[0]["to_state"] == "Ready for AI"
    assert "executor likely died" in a[0]["reason"]


def test_rule6_cooldown_skipped():
    actions = _run(
        [_issue("1", state="Running", updated_at=_STALE)],
        cooldown_skip_reason="cooling",
    )
    a = _by_rule(actions, "STALE_RUNNING_REQUEUE")
    assert a and a[0]["skipped"] is True


def test_rule6_fresh_no_action():
    actions = _run([_issue("1", state="Running", updated_at=_FRESH)])
    assert not _by_rule(actions, "STALE_RUNNING_REQUEUE")


# ---------------------------------------------------------------------------
# Rule 7 — GOAL_BACKLOG_PROMOTE
# ---------------------------------------------------------------------------


def _goal_backlog(labels, parent_state="Done"):
    return [
        _issue("parent", state=parent_state),
        _issue("1", state="Backlog", labels=labels + ["original-task-id: parent"]),
    ]


def test_rule7_pattern_a_promote():
    actions = _run(
        _goal_backlog(["task-kind: goal", "source: autonomy", "source: improve-suggestion"])
    )
    a = _by_rule(actions, "GOAL_BACKLOG_PROMOTE")
    assert a and a[0]["to_state"] == "Ready for AI"
    assert "parent improve task" in a[0]["reason"]


def test_rule7_pattern_b_promote():
    actions = _run(
        _goal_backlog(
            ["task-kind: goal", "source: board_worker", "handoff-reason: improvement_applied"]
        )
    )
    assert _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


def test_rule7_cooldown_skipped():
    actions = _run(
        _goal_backlog(["task-kind: goal", "source: autonomy", "source: improve-suggestion"]),
        cooldown_skip_reason="cooling",
    )
    a = _by_rule(actions, "GOAL_BACKLOG_PROMOTE")
    assert a and a[0]["skipped"] is True


def test_rule7_parent_not_terminal_no_action():
    actions = _run(
        _goal_backlog(
            ["task-kind: goal", "source: autonomy", "source: improve-suggestion"],
            parent_state="Running",
        )
    )
    assert not _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


def test_rule7_thin_goal_skipped():
    actions = _run(
        _goal_backlog(
            ["task-kind: goal", "source: autonomy", "source: improve-suggestion", "thin-goal"]
        )
    )
    assert not _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


def test_rule7_sigkill_skipped():
    actions = _run(
        _goal_backlog(
            [
                "task-kind: goal",
                "source: autonomy",
                "source: improve-suggestion",
                "executor-signal: SIGKILL",
            ]
        )
    )
    # Rule 7 skipped; but Rule 1 won't fire (terminal? no; sigkill but retry<3) — check rule7 only
    assert not _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


def test_rule7_low_mem_skipped():
    actions = _run(
        _goal_backlog(["task-kind: goal", "source: autonomy", "source: improve-suggestion"]),
        mem_available_gb=1.0,
    )
    assert not _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


def test_rule7_no_parent_label_no_action():
    actions = _run(
        [
            _issue(
                "1",
                state="Backlog",
                labels=["task-kind: goal", "source: autonomy", "source: improve-suggestion"],
            )
        ]
    )
    assert not _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


def test_rule7_no_matching_source_pattern():
    actions = _run(
        _goal_backlog(["task-kind: goal", "source: autonomy"])  # missing improve-suggestion
    )
    assert not _by_rule(actions, "GOAL_BACKLOG_PROMOTE")


# ---------------------------------------------------------------------------
# Rule 8 — CLEAN_BLOCKED_RETRY
# ---------------------------------------------------------------------------


def test_rule8_clean_blocked_retry():
    actions = _run(
        [_issue("1", state="Blocked", labels=["task-kind: spec-author"], updated_at=_STALE)]
    )
    a = _by_rule(actions, "CLEAN_BLOCKED_RETRY")
    assert a and a[0]["to_state"] == "Backlog"


def test_rule8_too_young_no_action():
    actions = _run(
        [_issue("1", state="Blocked", labels=["task-kind: goal"], updated_at=_FRESH)],
        clean_blocked_min_minutes=5,
    )
    assert not _by_rule(actions, "CLEAN_BLOCKED_RETRY")


def test_rule8_blocked_by_excluded():
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["task-kind: goal", "blocked-by: x"],
                updated_at=_STALE,
            )
        ]
    )
    assert not _by_rule(actions, "CLEAN_BLOCKED_RETRY")


def test_rule8_exit_code_excluded():
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["task-kind: improve", "executor-exit-code: 1"],
                updated_at=_STALE,
            )
        ]
    )
    assert not _by_rule(actions, "CLEAN_BLOCKED_RETRY")


def test_rule8_no_updated_at_no_action():
    actions = _run([_issue("1", state="Blocked", labels=["task-kind: goal"], updated_at=None)])
    assert not _by_rule(actions, "CLEAN_BLOCKED_RETRY")


def test_rule8_policy_blocked_excluded():
    # A deterministic policy gate (e.g. review.required) re-blocks identically on
    # every retry — recycling it Blocked->Backlog->Ready for AI is a closed loop,
    # not a transient pre-execution infra failure. Confirmed live via ghost-audit
    # G5 (26 policy-blocked re-dispatches in one hour) before this exclusion.
    actions = _run(
        [
            _issue(
                "1",
                state="Blocked",
                labels=["task-kind: goal", "blocked-reason: policy"],
                updated_at=_STALE,
            )
        ]
    )
    assert not _by_rule(actions, "CLEAN_BLOCKED_RETRY")


# ---------------------------------------------------------------------------
# Rule 9 — SPEC_AUTHOR_BACKLOG_PROMOTE
# ---------------------------------------------------------------------------


def test_rule9_promote():
    actions = _run([_issue("1", state="Backlog", labels=["task-kind: spec-author"])])
    a = _by_rule(actions, "SPEC_AUTHOR_BACKLOG_PROMOTE")
    assert a and a[0]["to_state"] == "Ready for AI"
    assert "skipped" not in a[0]


def test_rule9_cooldown_skipped():
    actions = _run(
        [_issue("1", state="Backlog", labels=["task-kind: spec-author"])],
        cooldown_skip_reason="cooling",
    )
    a = _by_rule(actions, "SPEC_AUTHOR_BACKLOG_PROMOTE")
    assert a and a[0]["skipped"] is True


def test_rule9_low_mem_skipped():
    actions = _run(
        [_issue("1", state="Backlog", labels=["task-kind: spec-author"])],
        mem_available_gb=1.0,
    )
    assert not _by_rule(actions, "SPEC_AUTHOR_BACKLOG_PROMOTE")


def test_no_actions_for_unrelated_issue():
    actions = _run([_issue("1", state="Done")])
    assert actions == []


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, *a, **k):
        self.transitions = []
        self.comments = []
        self.closed = False
        self._issues = []
        self._raise = None
        self._fetch_issue_responses: dict[str, object] = {}

    def list_issues(self):
        if self._raise:
            raise self._raise
        return self._issues

    def fetch_issue(self, task_id: str) -> dict:
        response = self._fetch_issue_responses.get(task_id)
        if response is None:
            import httpx

            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=mock.Mock(),
                response=mock.Mock(status_code=404),
            )
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    def transition_issue(self, task_id, to_state):
        self.transitions.append((task_id, to_state))

    def comment_issue(self, task_id, body):
        self.comments.append((task_id, body))

    def close(self):
        self.closed = True


def _patch_main(monkeypatch, *, client, mem=16.0, cooldown_reason=None):
    monkeypatch.setattr(bu, "_mem_available_gb", lambda: mem)
    monkeypatch.setattr(bu, "PlaneClient", lambda **kw: client)
    fake_settings = SimpleNamespace(
        plane=SimpleNamespace(base_url="http://x", workspace_slug="ws", project_id="p"),
        plane_token=lambda: "tok",
    )
    monkeypatch.setattr(bu, "load_settings", lambda cfg: fake_settings)
    fake_store = mock.Mock()
    fake_store.load.return_value = {"events": []}
    monkeypatch.setattr(bu, "UsageStore", lambda *a, **k: fake_store)
    monkeypatch.setattr(bu, "_dispatch_cooldown_reason", lambda *a, **k: cooldown_reason)
    # ensure worker_backend_probe import inside main resolves
    probe = SimpleNamespace(refresh_cooldowns=lambda *a, **k: None)
    monkeypatch.setitem(
        __import__("sys").modules,
        "operations_center.backends.worker_backend_probe",
        probe,
    )


def _argv(monkeypatch, args):
    monkeypatch.setattr(__import__("sys"), "argv", ["board_unblock", "--config", "cfg.yaml", *args])


def test_main_low_mem_skips(monkeypatch, capsys):
    client = _FakeClient()
    _patch_main(monkeypatch, client=client, mem=1.0)
    _argv(monkeypatch, [])
    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True
    assert client.closed is True


def test_main_list_issues_error(monkeypatch, capsys):
    client = _FakeClient()
    client._raise = RuntimeError("plane down")
    _patch_main(monkeypatch, client=client)
    _argv(monkeypatch, [])
    assert bu.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert "plane_fetch_failed" in out["error"]
    assert client.closed is True


def test_main_dry_run(monkeypatch, capsys):
    client = _FakeClient()
    client._issues = [_issue("1", state="Backlog", labels=["task-kind: spec-author"])]
    _patch_main(monkeypatch, client=client)
    _argv(monkeypatch, [])
    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["apply"] is False
    assert out["actions"]
    assert out["actions"][0]["action"] == "would_apply"
    assert client.transitions == []


def test_main_apply(monkeypatch, capsys):
    client = _FakeClient()
    client._issues = [_issue("1", state="Backlog", labels=["task-kind: spec-author"])]
    _patch_main(monkeypatch, client=client)
    _argv(monkeypatch, ["--apply"])
    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["actions"][0]["action"] == "applied"
    assert client.transitions == [("1", "Ready for AI")]
    assert len(client.comments) == 1


def test_main_apply_transition_error(monkeypatch, capsys):
    client = _FakeClient()
    client._issues = [_issue("1", state="Backlog", labels=["task-kind: spec-author"])]

    def boom(task_id, to_state):
        raise RuntimeError("transition failed")

    client.transition_issue = boom
    _patch_main(monkeypatch, client=client)
    _argv(monkeypatch, ["--apply"])
    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["actions"][0]["action"] == "error"
    assert "transition failed" in out["actions"][0]["error"]


def test_main_skipped_action_passthrough(monkeypatch, capsys):
    client = _FakeClient()
    client._issues = [_issue("1", state="Backlog", labels=["task-kind: spec-author"])]
    _patch_main(monkeypatch, client=client, cooldown_reason="cooling")
    _argv(monkeypatch, ["--apply"])
    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["actions"][0]["skipped"] is True
    assert client.transitions == []  # skipped actions never transition


def test_main_cooldown_import_error(monkeypatch, capsys):
    client = _FakeClient()
    client._issues = []
    _patch_main(monkeypatch, client=client)
    # Make the in-main import raise by removing the module and blocking import
    monkeypatch.setattr(
        bu,
        "_dispatch_cooldown_reason",
        mock.Mock(side_effect=RuntimeError("should be guarded")),
    )

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "operations_center.backends.worker_backend_probe":
            raise ImportError("no probe")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    _argv(monkeypatch, [])
    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cooldown_skip_reason"] is None


# ---------------------------------------------------------------------------
# Rule 10 — ORPHANED_IN_FLIGHT_CLEAR
# ---------------------------------------------------------------------------


def _make_store_with_events(events: list[dict]) -> mock.Mock:
    store = mock.Mock()
    store.load.return_value = {"events": events}
    return store


def _started_event(
    task_id: str, backend: str = "team_executor", ts: str = "2026-05-28T12:00:00+00:00"
) -> dict:
    return {"kind": "execution_started", "task_id": task_id, "backend": backend, "timestamp": ts}


def _finished_event(
    task_id: str, backend: str = "team_executor", ts: str = "2026-05-28T12:01:00+00:00"
) -> dict:
    return {"kind": "execution_finished", "task_id": task_id, "backend": backend, "timestamp": ts}


def test_rule10_clears_deleted_task_apply():
    """Orphaned started event for 404 task → write execution_finished (apply mode)."""
    client = _FakeClient()
    # task "t-orphan" is 404 (not in _fetch_issue_responses)
    store = _make_store_with_events([_started_event("t-orphan")])

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    assert len(cleared) == 1
    assert cleared[0]["rule"] == "ORPHANED_IN_FLIGHT_CLEAR"
    assert cleared[0]["task_id"] == "t-orphan"
    assert cleared[0]["action"] == "applied"
    assert "404" in cleared[0]["reason"]
    store.record_execution_finished.assert_called_once_with(
        task_id="t-orphan", backend="team_executor", now=_NOW
    )


def test_rule10_clears_deleted_task_dry_run():
    """Orphaned started event for 404 task → would_apply in dry-run mode."""
    client = _FakeClient()
    store = _make_store_with_events([_started_event("t-orphan")])

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=False)

    assert len(cleared) == 1
    assert cleared[0]["action"] == "would_apply"
    store.record_execution_finished.assert_not_called()


def test_rule10_clears_terminal_state_task():
    """Orphaned started event for task in Done state → cleared."""
    client = _FakeClient()
    client._fetch_issue_responses["t-done"] = {
        "id": "t-done",
        "name": "Done task",
        "state": {"name": "Done"},
        "labels": [],
    }
    store = _make_store_with_events([_started_event("t-done")])

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    assert len(cleared) == 1
    assert cleared[0]["action"] == "applied"
    assert "Done" in cleared[0]["reason"]


def test_rule10_skips_active_task():
    """In_flight event for a task still Running → NOT cleared."""
    client = _FakeClient()
    client._fetch_issue_responses["t-running"] = {
        "id": "t-running",
        "name": "Active task",
        "state": {"name": "Running"},
        "labels": [],
    }
    store = _make_store_with_events([_started_event("t-running")])

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    assert len(cleared) == 0
    store.record_execution_finished.assert_not_called()


def test_rule10_skips_balanced_events():
    """Started + finished pair → not in_flight, not cleared."""
    client = _FakeClient()
    store = _make_store_with_events(
        [
            _started_event("t-balanced"),
            _finished_event("t-balanced"),
        ]
    )

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    assert len(cleared) == 0


def test_rule10_skips_stale_events_beyond_24h():
    """Events older than 24h are excluded from the in_flight window."""
    client = _FakeClient()
    old_ts = "2026-05-27T09:00:00+00:00"  # 27h before _NOW
    store = _make_store_with_events([_started_event("t-old", ts=old_ts)])

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    assert len(cleared) == 0


def test_rule10_handles_fetch_error_gracefully():
    """Non-404 fetch errors → skip (don't clear, don't raise)."""
    import httpx

    client = _FakeClient()
    client._fetch_issue_responses["t-err"] = httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=mock.Mock(),
        response=mock.Mock(status_code=503),
    )
    store = _make_store_with_events([_started_event("t-err")])

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    assert len(cleared) == 0


def test_rule10_multiple_backends():
    """Each (backend, task_id) pair tracked independently."""
    client = _FakeClient()
    store = _make_store_with_events(
        [
            _started_event("t1", backend="team_executor"),
            _started_event("t1", backend="dag_executor"),  # same task, different backend
            _finished_event("t1", backend="team_executor"),  # closes team_executor slot
        ]
    )

    cleared = bu._clear_orphaned_in_flight_events(client, store, now=_NOW, apply=True)

    # Only dag_executor slot is orphaned (t1 is 404)
    assert len(cleared) == 1
    assert cleared[0]["backend"] == "dag_executor"


def test_rule10_integrated_in_main(monkeypatch, capsys):
    """Rule 10 runs inside main() and appears in output actions."""
    from datetime import datetime, UTC, timedelta

    # Use a timestamp within the 24h window relative to real now
    recent_ts = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()

    client = _FakeClient()
    client._issues = []
    _patch_main(monkeypatch, client=client)
    # Inject a started event for a deleted task into the fake store
    fake_store = mock.Mock()
    fake_store.load.return_value = {"events": [_started_event("t-gone", ts=recent_ts)]}
    monkeypatch.setattr(bu, "UsageStore", lambda *a, **k: fake_store)
    _argv(monkeypatch, ["--apply"])

    assert bu.main() == 0
    out = json.loads(capsys.readouterr().out)
    rule10_actions = [a for a in out["actions"] if a.get("rule") == "ORPHANED_IN_FLIGHT_CLEAR"]
    assert len(rule10_actions) == 1
    assert rule10_actions[0]["action"] == "applied"
    fake_store.record_execution_finished.assert_called_once()
