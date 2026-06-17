# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# tests/spec_author/test_spec_trigger.py
"""Unit tests for spec_trigger queue_drain suppression logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch


def _make_issue(
    issue_id: str,
    state_name: str,
    labels: list[str],
    title: str = "Test",
    updated_at: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "id": issue_id,
        "name": title,
        "state": {"name": state_name},
        "labels": [{"name": lbl} for lbl in labels],
    }
    if updated_at is not None:
        issue["updated_at"] = updated_at
    return issue


_SPEC_LABELS = ["source: spec-director", "task-kind: spec-author"]


def test_existing_spec_author_in_flight_detects_r4ai():
    from operations_center.entrypoints.spec_trigger.main import _existing_spec_author_in_flight

    issues = [_make_issue("abc", "Ready for AI", _SPEC_LABELS)]
    assert _existing_spec_author_in_flight(issues) == "abc"


def test_existing_spec_author_in_flight_detects_running():
    from operations_center.entrypoints.spec_trigger.main import _existing_spec_author_in_flight

    issues = [_make_issue("xyz", "Running", _SPEC_LABELS)]
    assert _existing_spec_author_in_flight(issues) == "xyz"


def test_existing_spec_author_in_flight_ignores_blocked():
    from operations_center.entrypoints.spec_trigger.main import _existing_spec_author_in_flight

    issues = [_make_issue("blocked-id", "Blocked", _SPEC_LABELS)]
    assert _existing_spec_author_in_flight(issues) is None


def test_existing_spec_author_in_flight_ignores_backlog():
    from operations_center.entrypoints.spec_trigger.main import _existing_spec_author_in_flight

    issues = [_make_issue("backlog-id", "Backlog", _SPEC_LABELS)]
    assert _existing_spec_author_in_flight(issues) is None


def test_any_queued_spec_author_detects_blocked():
    from operations_center.entrypoints.spec_trigger.main import _any_queued_spec_author

    issues = [_make_issue("b1", "Blocked", _SPEC_LABELS)]
    assert _any_queued_spec_author(issues) == "b1"


def test_any_queued_spec_author_detects_backlog():
    from operations_center.entrypoints.spec_trigger.main import _any_queued_spec_author

    issues = [_make_issue("bl1", "Backlog", _SPEC_LABELS)]
    assert _any_queued_spec_author(issues) == "bl1"


def test_any_queued_spec_author_detects_r4ai():
    from operations_center.entrypoints.spec_trigger.main import _any_queued_spec_author

    issues = [_make_issue("r1", "Ready for AI", _SPEC_LABELS)]
    assert _any_queued_spec_author(issues) == "r1"


def test_any_queued_spec_author_ignores_done():
    from operations_center.entrypoints.spec_trigger.main import _any_queued_spec_author

    issues = [_make_issue("d1", "Done", _SPEC_LABELS)]
    assert _any_queued_spec_author(issues) is None


def test_any_queued_spec_author_ignores_cancelled():
    from operations_center.entrypoints.spec_trigger.main import _any_queued_spec_author

    issues = [_make_issue("c1", "Cancelled", _SPEC_LABELS)]
    assert _any_queued_spec_author(issues) is None


def test_any_queued_spec_author_ignores_non_spec_labels():
    from operations_center.entrypoints.spec_trigger.main import _any_queued_spec_author

    issues = [_make_issue("g1", "Blocked", ["task-kind: goal"])]
    assert _any_queued_spec_author(issues) is None


def test_count_state_running_not_in_progress():
    """running_count must use 'running' (not 'in progress') to match Plane state names."""
    from operations_center.entrypoints.spec_trigger.main import _count_state

    issues = [
        _make_issue("r1", "Running", []),
        _make_issue("r2", "Running", []),
        _make_issue("ip1", "In Progress", []),  # Plane does not use this state
    ]
    assert _count_state(issues, "running") == 2
    assert _count_state(issues, "in progress") == 1  # would match "In Progress" if it existed


def test_run_once_suppresses_queue_drain_when_backlog_spec_tasks_exist(tmp_path):
    """run_once must not create a new task when Blocked/Backlog spec-author tasks exist."""
    from operations_center.entrypoints.spec_trigger.main import run_once

    backlog_issue = _make_issue("backlog-spec", "Backlog", _SPEC_LABELS, "[Spec] queue-drain-old")
    non_spec = _make_issue("goal-task", "Backlog", ["task-kind: goal"], "Fix something")

    mock_client = MagicMock()
    mock_client.list_issues.return_value = [backlog_issue, non_spec]

    mock_sd = MagicMock()
    mock_sd.enabled = True
    mock_sd.drop_file_path = str(tmp_path / "nonexistent.md")
    mock_sd.poll_interval_seconds = 60

    mock_settings = MagicMock()
    mock_settings.spec_author = mock_sd
    mock_settings.repos = {}

    with patch(
        "operations_center.entrypoints.spec_trigger.main._has_active_campaign", return_value=False
    ):
        run_once(mock_settings, mock_client)

    mock_client.create_issue.assert_not_called()
    from operations_center.spec_author.spec_author_task import create_spec_author_task  # noqa: F401

    # create_spec_author_task wraps create_issue — we verify the client never creates a task
    assert (
        not any(call[0][0] == "POST" for call in mock_client._request.call_args_list)
        if hasattr(mock_client, "_request")
        else True
    )


def test_run_once_drop_file_fires_even_with_queued_spec_tasks(tmp_path):
    """Drop-file triggers bypass queue suppression — operator intent wins."""
    from operations_center.entrypoints.spec_trigger.main import run_once
    from operations_center.spec_author.spec_author_task import create_spec_author_task  # noqa: F401

    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("new feature idea")

    backlog_issue = _make_issue("backlog-spec", "Backlog", _SPEC_LABELS, "[Spec] queue-drain-old")

    mock_client = MagicMock()
    mock_client.list_issues.return_value = [backlog_issue]

    mock_sd = MagicMock()
    mock_sd.enabled = True
    mock_sd.drop_file_path = str(drop_file)
    mock_sd.poll_interval_seconds = 60

    mock_settings = MagicMock()
    mock_settings.spec_author = mock_sd
    mock_settings.repos = {}

    with (
        patch(
            "operations_center.entrypoints.spec_trigger.main._has_active_campaign",
            return_value=False,
        ),
        patch(
            "operations_center.entrypoints.spec_trigger.main.create_spec_author_task",
            return_value="new-issue-id",
        ) as mock_create,
    ):
        run_once(mock_settings, mock_client)

    mock_create.assert_called_once()


# ── _spec_author_recently_completed cooldown tests ────────────────────────────


def test_recently_completed_detects_done_within_window():
    from operations_center.entrypoints.spec_trigger.main import _spec_author_recently_completed

    recent_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    issue = _make_issue("d1", "Done", _SPEC_LABELS, updated_at=recent_ts)
    assert _spec_author_recently_completed([issue], cooldown_hours=6) is True


def test_recently_completed_detects_cancelled_within_window():
    from operations_center.entrypoints.spec_trigger.main import _spec_author_recently_completed

    recent_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    issue = _make_issue("c1", "Cancelled", _SPEC_LABELS, updated_at=recent_ts)
    assert _spec_author_recently_completed([issue], cooldown_hours=6) is True


def test_recently_completed_ignores_old_terminal_task():
    from operations_center.entrypoints.spec_trigger.main import _spec_author_recently_completed

    old_ts = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    issue = _make_issue("d2", "Done", _SPEC_LABELS, updated_at=old_ts)
    assert _spec_author_recently_completed([issue], cooldown_hours=6) is False


def test_recently_completed_ignores_non_spec_author_labels():
    from operations_center.entrypoints.spec_trigger.main import _spec_author_recently_completed

    recent_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    issue = _make_issue("g1", "Done", ["task-kind: goal"], updated_at=recent_ts)
    assert _spec_author_recently_completed([issue], cooldown_hours=6) is False


def test_recently_completed_ignores_active_states():
    from operations_center.entrypoints.spec_trigger.main import _spec_author_recently_completed

    recent_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    issue = _make_issue("r1", "Ready for AI", _SPEC_LABELS, updated_at=recent_ts)
    assert _spec_author_recently_completed([issue], cooldown_hours=6) is False


def test_run_once_suppresses_queue_drain_within_cooldown(tmp_path):
    """run_once must not create a new task when a spec-author finished recently."""
    from operations_center.entrypoints.spec_trigger.main import run_once

    recent_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    done_issue = _make_issue("done-spec", "Done", _SPEC_LABELS, updated_at=recent_ts)

    mock_client = MagicMock()
    mock_client.list_issues.return_value = [done_issue]

    mock_sd = MagicMock()
    mock_sd.enabled = True
    mock_sd.drop_file_path = str(tmp_path / "nonexistent.md")
    mock_sd.poll_interval_seconds = 60

    mock_settings = MagicMock()
    mock_settings.spec_author = mock_sd
    mock_settings.repos = {}

    with (
        patch(
            "operations_center.entrypoints.spec_trigger.main._has_active_campaign",
            return_value=False,
        ),
        patch(
            "operations_center.entrypoints.spec_trigger.main.create_spec_author_task",
        ) as mock_create,
    ):
        run_once(mock_settings, mock_client)

    mock_create.assert_not_called()


def test_run_once_drop_file_bypasses_cooldown(tmp_path):
    """Drop-file triggers bypass the cooldown — operator intent wins."""
    from operations_center.entrypoints.spec_trigger.main import run_once

    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("override idea")

    recent_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    done_issue = _make_issue("done-spec", "Done", _SPEC_LABELS, updated_at=recent_ts)

    mock_client = MagicMock()
    mock_client.list_issues.return_value = [done_issue]

    mock_sd = MagicMock()
    mock_sd.enabled = True
    mock_sd.drop_file_path = str(drop_file)
    mock_sd.poll_interval_seconds = 60

    mock_settings = MagicMock()
    mock_settings.spec_author = mock_sd
    mock_settings.repos = {}

    with (
        patch(
            "operations_center.entrypoints.spec_trigger.main._has_active_campaign",
            return_value=False,
        ),
        patch(
            "operations_center.entrypoints.spec_trigger.main.create_spec_author_task",
            return_value="new-id",
        ) as mock_create,
    ):
        run_once(mock_settings, mock_client)

    mock_create.assert_called_once()
