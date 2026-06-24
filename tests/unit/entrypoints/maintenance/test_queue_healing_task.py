# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for QueueHealingTask (inventory #4).

Pins three things:
  * FAIL-SAFE: registered-but-disabled by default; no-op on a clean board.
  * The activated path recycles a retry-safe Blocked task non-destructively.
  * It satisfies the MaintenanceTask contract and uses an injected PlaneClient.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from operations_center.entrypoints.maintenance.queue_healing_task import QueueHealingTask
from operations_center.maintenance.contracts import (
    MaintenanceContext,
    MaintenanceResult,
    MaintenanceTask,
)


def _settings(**over):
    base = dict(
        plane=SimpleNamespace(base_url="http://x", workspace_slug="w", project_id="p"),
        plane_token=lambda: "tok",
        queue_healing_enabled=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(client=None):
    resources = {"plane_client": client} if client is not None else {}
    return MaintenanceContext(cycle_id="c1", now=datetime.now(UTC), resources=resources)


def _blocked_issue(task_id, labels, *, updated_at=None):
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": "Blocked"},
        "labels": [{"name": label} for label in labels],
        "updated_at": (updated_at or datetime.now(UTC)).isoformat(),
    }


class TestFailSafeDefault:
    def test_disabled_by_default(self):
        """The whole point of fail-safe: enabled=False unless the operator opts in."""
        task = QueueHealingTask(_settings())
        assert task.enabled is False

    def test_enabled_when_setting_flipped(self):
        task = QueueHealingTask(_settings(queue_healing_enabled=True))
        assert task.enabled is True

    def test_satisfies_maintenance_protocol(self):
        assert isinstance(QueueHealingTask(_settings()), MaintenanceTask)

    def test_clean_board_is_noop(self):
        """No retry-safe Blocked tasks → zero decisions, zero mutations."""
        client = mock.Mock()
        client.list_issues.return_value = [
            {"id": "x", "name": "Ready task", "state": {"name": "Ready for AI"}, "labels": []},
        ]
        task = QueueHealingTask(_settings(queue_healing_enabled=True), plane_client=client)
        result = task.run_once(_ctx(client))
        assert isinstance(result, MaintenanceResult)
        assert result.status == "ok"
        assert result.details["decisions"] == 0
        assert result.details["applied"] == 0
        client.transition_issue.assert_not_called()


class TestActivatedPath:
    def test_stale_blocked_retry_safe_recycled_to_backlog(self):
        """A stale retry-safe Blocked task is transitioned to Backlog + commented —
        non-destructively (no delete)."""
        old = datetime.now(UTC) - timedelta(hours=2)
        client = mock.Mock()
        client.list_issues.return_value = [
            _blocked_issue("t1", ["retry_safe"], updated_at=old),
        ]
        task = QueueHealingTask(
            _settings(queue_healing_enabled=True), plane_client=client, apply=True
        )
        result = task.run_once(_ctx(client))

        assert result.status == "ok"
        assert result.details["applied"] == 1
        client.transition_issue.assert_called_once_with("t1", "Backlog")
        client.comment_issue.assert_called_once()
        # Non-destructive: the client is never asked to delete anything.
        assert not any(
            "delete" in name.lower() for name, *_ in client.method_calls
        )

    def test_dry_run_mutates_nothing(self):
        old = datetime.now(UTC) - timedelta(hours=2)
        client = mock.Mock()
        client.list_issues.return_value = [_blocked_issue("t1", ["retry_safe"], updated_at=old)]
        task = QueueHealingTask(
            _settings(queue_healing_enabled=True), plane_client=client, apply=False
        )
        result = task.run_once(_ctx(client))

        assert result.details["actions"][0]["action"] == "would_transition"
        client.transition_issue.assert_not_called()
        client.comment_issue.assert_not_called()

    def test_plane_fetch_failure_is_failed_status_not_raise(self):
        client = mock.Mock()
        client.list_issues.side_effect = RuntimeError("plane down")
        task = QueueHealingTask(_settings(queue_healing_enabled=True), plane_client=client)
        result = task.run_once(_ctx(client))
        assert result.status == "failed"
        assert "plane_fetch_failed" in (result.error or "")
