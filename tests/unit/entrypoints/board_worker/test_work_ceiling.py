# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the global fleet work ceiling (Phase B2, determinism surface 6)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from operations_center.entrypoints.board_worker.work_ceiling import (
    ceiling_reached,
    fleet_open_work_count,
    open_descendants_of_root,
    root_descendant_cap_reached,
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


# ── Per-root descendant cap (B3, determinism surface 7) ────────────────────────


def _rooted(root, *, state="Ready for AI"):
    return _issue(state=state, labels=[f"lineage-root: {root}", "source: board_worker"])


def test_open_descendants_counts_only_matching_root():
    issues = [
        _rooted("R1"),
        _rooted("R1"),
        _rooted("R1", state="Done"),  # terminal → excluded
        _rooted("R2"),
        _issue(labels=["task-kind: goal"]),  # no root → excluded
    ]
    assert open_descendants_of_root(issues, "R1") == 2
    assert open_descendants_of_root(issues, "R2") == 1


def test_root_cap_disabled_by_default():
    client = MagicMock()
    assert root_descendant_cap_reached(client, SimpleNamespace(), "R1") is False
    client.list_issues.assert_not_called()


def test_root_cap_empty_root_never_throttles():
    client = MagicMock()
    settings = SimpleNamespace(max_descendants_per_root=1)
    assert root_descendant_cap_reached(client, settings, "") is False
    client.list_issues.assert_not_called()


def test_root_cap_reached():
    client = MagicMock()
    client.list_issues.return_value = [_rooted("R1") for _ in range(4)]
    settings = SimpleNamespace(max_descendants_per_root=4)
    assert root_descendant_cap_reached(client, settings, "R1") is True


def test_root_cap_not_reached_for_other_root():
    client = MagicMock()
    client.list_issues.return_value = [_rooted("R1") for _ in range(9)]
    settings = SimpleNamespace(max_descendants_per_root=3)
    # a different root with no descendants is unaffected by R1's overflow
    assert root_descendant_cap_reached(client, settings, "R2") is False


def test_root_cap_fail_open_on_error():
    client = MagicMock()
    client.list_issues.side_effect = RuntimeError("plane down")
    settings = SimpleNamespace(max_descendants_per_root=1)
    assert root_descendant_cap_reached(client, settings, "R1") is False
