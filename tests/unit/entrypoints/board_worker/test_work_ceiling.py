# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the global fleet work ceiling (Phase B2, determinism surface 6)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from operations_center.entrypoints.board_worker.work_ceiling import (
    ceiling_reached,
    fleet_open_work_count,
)


def _issue(*, state="Ready for AI", labels=None):
    return {"id": "x", "state": {"name": state}, "labels": [{"name": n} for n in (labels or [])]}


def _fleet(**kw):
    kw.setdefault("labels", ["source: board_worker"])
    return _issue(**kw)


def test_counts_only_open_fleet_tasks():
    issues = [
        _fleet(),  # open fleet → counts
        _fleet(state="Done"),  # terminal → no
        _fleet(state="Cancelled"),  # terminal → no
        _issue(labels=["task-kind: goal"]),  # human-authored → no
        _issue(labels=["original-task-id: abc"]),  # fleet via lineage label → counts
    ]
    assert fleet_open_work_count(issues) == 2


def test_human_tasks_never_counted():
    issues = [_issue(labels=["task-kind: goal", "repo: a/b"]) for _ in range(50)]
    assert fleet_open_work_count(issues) == 0


def test_ceiling_disabled_by_default():
    client = MagicMock()
    settings = SimpleNamespace()  # no max_open_fleet_tasks attr
    assert ceiling_reached(client, settings) is False
    client.list_issues.assert_not_called()  # short-circuits before any API call


def test_ceiling_reached_when_over_cap():
    client = MagicMock()
    client.list_issues.return_value = [_fleet() for _ in range(5)]
    settings = SimpleNamespace(max_open_fleet_tasks=3)
    assert ceiling_reached(client, settings) is True


def test_ceiling_not_reached_under_cap():
    client = MagicMock()
    client.list_issues.return_value = [_fleet() for _ in range(2)]
    settings = SimpleNamespace(max_open_fleet_tasks=3)
    assert ceiling_reached(client, settings) is False


def test_ceiling_fail_open_on_list_error():
    client = MagicMock()
    client.list_issues.side_effect = RuntimeError("plane down")
    settings = SimpleNamespace(max_open_fleet_tasks=1)
    # a broken count must not deadlock self-healing
    assert ceiling_reached(client, settings) is False
