# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from operations_center.entrypoints.maintenance import triage_scan as mod
from operations_center.queue_healing import (
    QueueHealingDecision,
    QueueHealingTask,
    QueueTransition,
)

MODPATH = "operations_center.entrypoints.maintenance.triage_scan"


# ── _labels ──────────────────────────────────────────────────────────────────


def test_labels_handles_dicts_strings_and_blanks():
    issue = {
        "labels": [
            {"name": " retry_safe "},
            "lifecycle: escalated",
            {"name": ""},  # blank name dropped
            {"name": None},  # None dropped
            None,  # falsy raw dropped
            {"nope": "x"},  # missing name -> None -> dropped
        ]
    }
    assert mod._labels(issue) == ("retry_safe", "lifecycle: escalated")


def test_labels_missing_key_and_none():
    assert mod._labels({}) == ()
    assert mod._labels({"labels": None}) == ()


# ── _label_value ───────────────────────────────────────────────────────────


def test_label_value_prefix_match_case_insensitive_and_stripped():
    labels = ("Retry-Lineage:  abc123  ",)
    assert mod._label_value(labels, "retry-lineage:") == "abc123"


def test_label_value_multiple_prefixes_first_wins():
    labels = ("dedup: zzz",)
    # first prefix has no match, second does
    assert mod._label_value(labels, "duplicate:", "dedup:") == "zzz"


def test_label_value_no_match_returns_none():
    assert mod._label_value(("foo", "bar"), "missing:") is None


# ── _state_name ─────────────────────────────────────────────────────────────


def test_state_name_dict_form():
    assert mod._state_name({"state": {"name": " Blocked "}}) == "Blocked"


def test_state_name_dict_missing_name():
    assert mod._state_name({"state": {}}) == ""


def test_state_name_string_form_and_none():
    assert mod._state_name({"state": " Backlog "}) == "Backlog"
    assert mod._state_name({}) == ""
    assert mod._state_name({"state": None}) == ""


# ── _parse_updated_at ───────────────────────────────────────────────────────


def test_parse_updated_at_uses_updated_then_created():
    dt = mod._parse_updated_at({"updated_at": "2026-01-01T00:00:00Z"})
    assert dt == datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_updated_at_falls_back_to_created_at():
    dt = mod._parse_updated_at({"created_at": "2026-02-02T00:00:00+00:00"})
    assert dt == datetime(2026, 2, 2, tzinfo=UTC)


def test_parse_updated_at_naive_gets_utc():
    dt = mod._parse_updated_at({"updated_at": "2026-03-03T00:00:00"})
    assert dt is not None and dt.tzinfo == UTC


def test_parse_updated_at_missing_returns_none():
    assert mod._parse_updated_at({}) is None
    assert mod._parse_updated_at({"updated_at": ""}) is None


def test_parse_updated_at_invalid_returns_none():
    assert mod._parse_updated_at({"updated_at": "not-a-date"}) is None


# ── _parse_int ──────────────────────────────────────────────────────────────


def test_parse_int_variants():
    assert mod._parse_int(None) == 0
    assert mod._parse_int("7") == 7
    assert mod._parse_int("bad") == 0


# ── _parse_executor_exit_code ──────────────────────────────────────────────


def test_parse_executor_exit_code_present():
    assert mod._parse_executor_exit_code(("executor-exit-code: 137",)) == 137


def test_parse_executor_exit_code_absent_returns_none():
    assert mod._parse_executor_exit_code(("other",)) is None


def test_parse_executor_exit_code_invalid_returns_none():
    assert mod._parse_executor_exit_code(("executor-exit-code: x",)) is None


# ── _duplicate_blocked_keys ─────────────────────────────────────────────────


def test_duplicate_blocked_keys_only_blocked_and_count_gt_one():
    items = [
        {"state": "Blocked", "labels": ["duplicate: k1"]},
        {"state": {"name": "blocked"}, "labels": ["dedup: k1"]},  # second prefix
        {"state": "Blocked", "labels": ["duplicate: solo"]},  # only once
        {"state": "Backlog", "labels": ["duplicate: k1"]},  # not blocked, ignored
        {"state": "Blocked", "labels": ["no-key"]},  # no duplicate key
    ]
    assert mod._duplicate_blocked_keys(items) == {"k1"}


def test_duplicate_blocked_keys_empty():
    assert mod._duplicate_blocked_keys([]) == set()


# ── _queue_task_from_issue ──────────────────────────────────────────────────


def test_queue_task_from_issue_full_metadata():
    issue = {
        "id": 42,
        "name": "Fix it",
        "state": "Blocked",
        "labels": [
            "duplicate: dk",
            "retry-lineage: lin1",
            "retry-safe",
            "queue-deadlock",
            "retry-count: 4",
            "recovery-attempts: 2",
            "blocked-reason: backend down",
            "blocked-by-backend: claude",
            "backend: codex",
        ],
        "updated_at": "2026-01-01T00:00:00Z",
    }
    task, no_consumer = mod._queue_task_from_issue(issue, duplicate_blocked_keys={"dk"})
    assert isinstance(task, QueueHealingTask)
    assert task.task_id == "42"
    assert task.title == "Fix it"
    assert task.state == "Blocked"
    assert task.duplicate_key == "dk"
    assert task.duplicate_exists_in_blocked is True
    assert task.retry_safe is True
    assert task.blocked_reason == "backend down"
    assert task.blocked_by_backend == "claude"
    assert task.backend_dependency == "codex"
    assert task.retry_lineage_id == "lin1"
    assert task.retry_count == 4
    assert task.recovery_attempt_count == 2
    assert task.updated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert no_consumer is True


def test_queue_task_from_issue_minimal_defaults():
    issue = {"id": "x1"}
    task, no_consumer = mod._queue_task_from_issue(issue, duplicate_blocked_keys=set())
    assert task.task_id == "x1"
    assert task.title == ""
    assert task.duplicate_key is None
    assert task.duplicate_exists_in_blocked is False
    assert task.retry_safe is False
    assert task.retry_count == 0
    assert no_consumer is False


def test_queue_task_from_issue_dup_key_not_in_blocked_set():
    issue = {"id": "1", "state": "Blocked", "labels": ["duplicate: other"]}
    task, _ = mod._queue_task_from_issue(issue, duplicate_blocked_keys={"different"})
    assert task.duplicate_key == "other"
    assert task.duplicate_exists_in_blocked is False


# ── _queue_healing_actions ──────────────────────────────────────────────────


def test_queue_healing_actions_filters_non_blocked_and_none_decisions():
    items = [
        {"id": "skip", "state": "Backlog", "labels": []},  # not blocked
        {"id": "noop", "state": "Blocked", "labels": []},  # decision NONE
        {"id": "act", "state": "Blocked", "labels": ["retry-safe"]},  # transition
        {"id": "esc", "state": "Blocked", "labels": []},  # escalate
    ]
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def fake_decide(task, *, no_consumer_can_execute, now):
        if task.task_id == "noop":
            return QueueHealingDecision(task.task_id, QueueTransition.NONE, "none")
        if task.task_id == "act":
            return QueueHealingDecision(
                task.task_id, QueueTransition.BLOCKED_TO_READY_FOR_AI, "r", safe=True
            )
        return QueueHealingDecision(task.task_id, QueueTransition.NONE, "esc-reason", escalate=True)

    with mock.patch.object(mod, "QueueHealingEngine") as Eng:
        Eng.return_value.decide.side_effect = fake_decide
        out = mod._queue_healing_actions(items, now=now)

    ids = sorted(t.task_id for t, _ in out)
    assert ids == ["act", "esc"]


# ── main() ──────────────────────────────────────────────────────────────────


def _settings():
    return SimpleNamespace(
        plane=SimpleNamespace(
            base_url="http://plane",
            workspace_slug="ws",
            project_id="proj",
        ),
        plane_token=lambda: "tok",
    )


def _make_client():
    client = mock.MagicMock()
    client.workspace_slug = "ws"
    client.project_id = "proj"
    return client


@pytest.fixture
def patched(monkeypatch):
    """Patch all main() collaborators; return a control namespace."""
    client = _make_client()
    monkeypatch.setattr(mod, "load_settings", lambda cfg: _settings())
    monkeypatch.setattr(mod, "PlaneClient", lambda **kw: client)
    monkeypatch.setattr(mod, "handle_priority_rescore_scan", lambda items, now: [])
    monkeypatch.setattr(mod, "handle_awaiting_input_scan", lambda items, c, state_name: [])
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [])
    return SimpleNamespace(client=client)


def _run_main(args):
    with mock.patch("sys.argv", ["prog", *args]):
        return mod.main()


def _capture(capsys):
    return json.loads(capsys.readouterr().out)


def test_main_plane_fetch_failure_returns_1(patched, capsys):
    patched.client.list_issues.side_effect = RuntimeError("boom")
    rc = _run_main(["--config", "c.yaml"])
    assert rc == 1
    patched.client.close.assert_called_once()
    out = json.loads(capsys.readouterr().out)
    assert "plane_fetch_failed: boom" in out["error"]


def test_main_dry_run_emits_would_actions(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    rescore = SimpleNamespace(
        task_id="t1",
        title="T1",
        current_priority="low",
        proposed_priority="high",
        reason="old",
    )
    awaiting = SimpleNamespace(task_id="a1", title="A1", new_comment_count=3)
    task = QueueHealingTask(task_id="q1", title="Q1", state="Blocked")
    decision = QueueHealingDecision(
        "q1", QueueTransition.BLOCKED_TO_READY_FOR_AI, "stale", safe=True
    )
    monkeypatch.setattr(mod, "handle_priority_rescore_scan", lambda items, now: [rescore])
    monkeypatch.setattr(mod, "handle_awaiting_input_scan", lambda items, c, state_name: [awaiting])
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])

    rc = _run_main(["--config", "c.yaml"])
    assert rc == 0
    out = _capture(capsys)
    assert out["apply"] is False
    assert out["rescore"][0]["action"] == "would_apply"
    assert out["awaiting"][0]["action"] == "would_transition"
    assert out["queue_healing"][0]["action"] == "would_transition"
    # raw patch never used in dry run
    patched.client._client.patch.assert_not_called()
    patched.client.transition_issue.assert_not_called()
    patched.client.close.assert_called_once()


def test_main_apply_rescore_success_and_error(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    ok = SimpleNamespace(
        task_id="ok", title="", current_priority="a", proposed_priority="b", reason="r"
    )
    bad = SimpleNamespace(
        task_id="bad", title="", current_priority="a", proposed_priority="b", reason="r"
    )
    monkeypatch.setattr(mod, "handle_priority_rescore_scan", lambda items, now: [ok, bad])

    def patch_side(path, json):
        if "bad" in path:
            raise RuntimeError("patch failed")

    patched.client._client.patch.side_effect = patch_side

    rc = _run_main(["--config", "c.yaml", "--apply"])
    assert rc == 0
    out = _capture(capsys)
    actions = {e["task_id"]: e for e in out["rescore"]}
    assert actions["ok"]["action"] == "applied"
    assert actions["bad"]["action"] == "error"
    assert "patch failed" in actions["bad"]["error"]


def test_main_apply_awaiting_success_and_error(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    ok = SimpleNamespace(task_id="ok", title="", new_comment_count=1)
    bad = SimpleNamespace(task_id="bad", title="", new_comment_count=2)
    monkeypatch.setattr(mod, "handle_awaiting_input_scan", lambda items, c, state_name: [ok, bad])

    def transition_side(task_id, state):
        if task_id == "bad":
            raise RuntimeError("transition failed")

    patched.client.transition_issue.side_effect = transition_side

    rc = _run_main(["--config", "c.yaml", "--apply"])
    assert rc == 0
    out = _capture(capsys)
    actions = {e["task_id"]: e for e in out["awaiting"]}
    assert actions["ok"]["action"] == "transitioned"
    assert actions["bad"]["action"] == "error"
    assert "transition failed" in actions["bad"]["error"]


def test_main_awaiting_input_state_arg_passed_through(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    seen = {}

    def fake_awaiting(items, c, state_name):
        seen["state"] = state_name
        return []

    monkeypatch.setattr(mod, "handle_awaiting_input_scan", fake_awaiting)
    _run_main(["--config", "c.yaml", "--awaiting-input-state", "Custom State"])
    assert seen["state"] == "Custom State"


def test_main_queue_healing_none_action(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.NONE, "nothing")
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    assert out["queue_healing"][0]["action"] == "none"


def test_main_queue_healing_escalate_apply_success(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(
        task_id="q",
        title="",
        state="Blocked",
        labels=("executor-exit-code: 1", "executor-signal: SIGKILL"),
    )
    decision = QueueHealingDecision(
        "q", QueueTransition.ESCALATE, "boom", retry_lineage_id="lin", escalate=True
    )
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    entry = out["queue_healing"][0]
    assert entry["action"] == "escalation_commented"
    assert entry["executor_exit_code"] == 1
    assert entry["executor_signal"] == "SIGKILL"
    patched.client.comment_issue.assert_called_once()


def test_main_queue_healing_escalate_dry_run(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.ESCALATE, "boom", escalate=True)
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    _run_main(["--config", "c.yaml"])
    out = _capture(capsys)
    assert out["queue_healing"][0]["action"] == "escalate"
    patched.client.comment_issue.assert_not_called()


def test_main_queue_healing_escalate_apply_error(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.ESCALATE, "boom", escalate=True)
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    patched.client.comment_issue.side_effect = RuntimeError("comment failed")
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    entry = out["queue_healing"][0]
    assert entry["action"] == "error"
    assert "comment failed" in entry["error"]


def test_main_queue_healing_transition_to_backlog_success(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.BLOCKED_TO_BACKLOG, "dup", safe=True)
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    assert out["queue_healing"][0]["action"] == "transitioned"
    patched.client.transition_issue.assert_called_once_with("q", "Backlog")


def test_main_queue_healing_transition_to_ready_success(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.BLOCKED_TO_READY_FOR_AI, "ok", safe=True)
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    assert out["queue_healing"][0]["action"] == "transitioned"
    patched.client.transition_issue.assert_called_once_with("q", "Ready for AI")


def test_main_queue_healing_transition_apply_error(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.BLOCKED_TO_BACKLOG, "dup", safe=True)
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    patched.client.transition_issue.side_effect = RuntimeError("nope")
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    entry = out["queue_healing"][0]
    assert entry["action"] == "error"
    assert "nope" in entry["error"]


def test_main_queue_healing_unsafe_transition_would_transition(patched, capsys, monkeypatch):
    patched.client.list_issues.return_value = []
    # transition set but not safe and not escalate -> else branch even with apply
    task = QueueHealingTask(task_id="q", title="", state="Blocked")
    decision = QueueHealingDecision("q", QueueTransition.BLOCKED_TO_BACKLOG, "unsafe", safe=False)
    monkeypatch.setattr(mod, "_queue_healing_actions", lambda items, now: [(task, decision)])
    _run_main(["--config", "c.yaml", "--apply"])
    out = _capture(capsys)
    assert out["queue_healing"][0]["action"] == "would_transition"
    patched.client.transition_issue.assert_not_called()


def test_main_module_entrypoint_exists():
    assert callable(mod.main)
