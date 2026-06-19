# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit coverage for the shared in-flight ledger reconciliation helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import httpx
import pytest

from operations_center.in_flight_reconcile import (
    OrphanedSlot,
    clear_orphaned_in_flight,
    find_orphaned_in_flight,
    is_terminal,
    reconcile_in_flight_on_startup,
    state_name,
)

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _started(task_id: str, backend: str = "team_executor", ts: str | None = None) -> dict:
    return {
        "kind": "execution_started",
        "task_id": task_id,
        "backend": backend,
        "timestamp": ts or "2026-05-28T11:00:00+00:00",
    }


def _finished(task_id: str, backend: str = "team_executor", ts: str | None = None) -> dict:
    return {
        "kind": "execution_finished",
        "task_id": task_id,
        "backend": backend,
        "timestamp": ts or "2026-05-28T11:01:00+00:00",
    }


class _FakeClient:
    def __init__(self, responses: dict | None = None) -> None:
        self._responses = responses or {}

    def fetch_issue(self, task_id: str) -> dict:
        resp = self._responses.get(task_id)
        if resp is None:
            raise httpx.HTTPStatusError(
                "404", request=mock.Mock(), response=mock.Mock(status_code=404)
            )
        if isinstance(resp, Exception):
            raise resp
        return resp


def _store(events: list[dict], *, path: Path | None = None) -> mock.Mock:
    store = mock.Mock()
    store.load.return_value = {"events": events}
    store.path = path or Path("/tmp/does-not-need-to-exist.json")
    return store


# ── helpers ───────────────────────────────────────────────────────────────────


def test_state_name_handles_dict_and_scalar() -> None:
    assert state_name({"state": {"name": " Done "}}) == "Done"
    assert state_name({"state": "Running"}) == "Running"
    assert state_name({}) == ""


def test_is_terminal_matches_terminal_states_case_insensitively() -> None:
    assert is_terminal("Done")
    assert is_terminal("CANCELLED")
    assert not is_terminal("Running")


# ── find_orphaned_in_flight ─────────────────────────────────────────────────────


def test_404_task_is_orphaned() -> None:
    store = _store([_started("t-gone")])
    orphans = find_orphaned_in_flight(store, _FakeClient(), now=_NOW)
    assert orphans == [
        OrphanedSlot(
            task_id="t-gone",
            backend="team_executor",
            started_at="2026-05-28T11:00:00+00:00",
            reason=orphans[0].reason,
        )
    ]
    assert "404" in orphans[0].reason


def test_terminal_task_is_orphaned() -> None:
    client = _FakeClient({"t-done": {"state": {"name": "Done"}}})
    orphans = find_orphaned_in_flight(_store([_started("t-done")]), client, now=_NOW)
    assert [o.task_id for o in orphans] == ["t-done"]
    assert "Done" in orphans[0].reason


def test_running_task_is_not_orphaned() -> None:
    client = _FakeClient({"t-run": {"state": {"name": "Running"}}})
    assert find_orphaned_in_flight(_store([_started("t-run")]), client, now=_NOW) == []


def test_finished_event_closes_the_slot() -> None:
    events = [_started("t-x"), _finished("t-x")]
    # even though t-x would 404, it is no longer in flight
    assert find_orphaned_in_flight(_store(events), _FakeClient(), now=_NOW) == []


def test_events_older_than_window_are_ignored() -> None:
    old = (_NOW - timedelta(hours=48)).isoformat()
    assert (
        find_orphaned_in_flight(_store([_started("t-old", ts=old)]), _FakeClient(), now=_NOW) == []
    )


def test_non_404_http_error_is_skipped_not_cleared() -> None:
    err = httpx.HTTPStatusError("500", request=mock.Mock(), response=mock.Mock(status_code=500))
    client = _FakeClient({"t-err": err})
    assert find_orphaned_in_flight(_store([_started("t-err")]), client, now=_NOW) == []


# ── clear_orphaned_in_flight ────────────────────────────────────────────────────


def test_clear_apply_records_finished() -> None:
    store = _store([_started("t-gone")])
    cleared = clear_orphaned_in_flight(store, _FakeClient(), now=_NOW, apply=True)
    assert len(cleared) == 1
    assert cleared[0]["rule"] == "ORPHANED_IN_FLIGHT_CLEAR"
    assert cleared[0]["action"] == "applied"
    store.record_execution_finished.assert_called_once_with(
        task_id="t-gone", backend="team_executor", now=_NOW
    )


def test_clear_dry_run_does_not_record() -> None:
    store = _store([_started("t-gone")])
    cleared = clear_orphaned_in_flight(store, _FakeClient(), now=_NOW, apply=False)
    assert cleared[0]["action"] == "would_apply"
    store.record_execution_finished.assert_not_called()


def test_clear_reports_record_error_without_raising() -> None:
    store = _store([_started("t-gone")])
    store.record_execution_finished.side_effect = RuntimeError("disk full")
    cleared = clear_orphaned_in_flight(store, _FakeClient(), now=_NOW, apply=True)
    assert cleared[0]["action"] == "error"
    assert "disk full" in cleared[0]["error"]


# ── reconcile_in_flight_on_startup ──────────────────────────────────────────────


def test_startup_reconcile_clears_under_lock(tmp_path: Path) -> None:
    store = _store([_started("t-gone")], path=tmp_path / "usage.json")
    cleared = reconcile_in_flight_on_startup(store, _FakeClient(), now=_NOW)
    assert [c["task_id"] for c in cleared] == ["t-gone"]
    store.record_execution_finished.assert_called_once()


def test_startup_reconcile_skips_when_another_worker_holds_lock(tmp_path: Path) -> None:
    from operations_center.audit_governance.file_locks import FileLockTimeoutError

    store = _store([_started("t-gone")], path=tmp_path / "usage.json")
    with mock.patch(
        "operations_center.audit_governance.file_locks.locked_state_file",
        side_effect=FileLockTimeoutError("busy"),
    ):
        assert reconcile_in_flight_on_startup(store, _FakeClient(), now=_NOW) == []
    store.record_execution_finished.assert_not_called()


def test_startup_reconcile_swallows_unexpected_errors(tmp_path: Path) -> None:
    store = _store([_started("t-gone")], path=tmp_path / "usage.json")
    store.load.side_effect = RuntimeError("boom")
    assert reconcile_in_flight_on_startup(store, _FakeClient(), now=_NOW) == []


@pytest.mark.parametrize("apply", [True, False])
def test_no_events_is_noop(apply: bool, tmp_path: Path) -> None:
    store = _store([], path=tmp_path / "usage.json")
    assert clear_orphaned_in_flight(store, _FakeClient(), now=_NOW, apply=apply) == []
