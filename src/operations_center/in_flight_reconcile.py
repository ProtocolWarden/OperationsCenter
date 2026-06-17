# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Reconcile the usage-store in-flight ledger against Plane.

A dispatch records an ``execution_started`` event and pairs it with an
``execution_finished`` event from a ``finally`` block (see
``execution/coordinator.py``).  The pairing is correct, but a ``finally`` cannot
run when the executor *process* dies between the two markers — a session-limit
kill, an OOM, or the SIGTERM a code-pull restart sends to the watcher mid-
dispatch.  The ``execution_started`` event is then never closed and the
``(backend, task_id)`` slot leaks, counting against the per-backend concurrency
cap until something records the missing ``execution_finished``.

``board_unblock`` Rule 10 (``ORPHANED_IN_FLIGHT_CLEAR``) closes these every
watchdog cycle.  This module holds that scan as a reusable helper so it can ALSO
run at watcher startup — the moment right after a code-pull restart, when leaked
slots are most likely and waiting a full watchdog cycle to reclaim the slot is
most wasteful.  ``board_unblock`` delegates to this module, keeping one
definition of "what counts as an orphan" for both callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"done", "cancelled", "cancelled by operator", "closed"}
ORPHAN_RULE = "ORPHANED_IN_FLIGHT_CLEAR"
DEFAULT_WINDOW_HOURS = 24


def state_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).strip()
    return str(state or "").strip()


def is_terminal(state: str) -> bool:
    return state.lower() in TERMINAL_STATES


@dataclass(frozen=True)
class OrphanedSlot:
    """An ``execution_started`` event whose task is gone or terminal in Plane."""

    task_id: str
    backend: str
    started_at: str | None
    reason: str


class _IssueClient(Protocol):
    def fetch_issue(self, task_id: str) -> dict[str, Any]: ...


class _UsageStore(Protocol):
    path: Any

    def load(self) -> dict[str, Any]: ...

    def record_execution_finished(
        self, *, task_id: str, backend: str, now: datetime
    ) -> None: ...


def _open_in_flight(
    events: list[dict[str, Any]], *, cutoff: datetime
) -> dict[tuple[str, str], dict[str, Any]]:
    """Fold the event log into the currently-open ``(backend, task_id)`` slots.

    A ``execution_finished`` cancels the matching ``execution_started``; what
    remains within the *cutoff* window is in flight.  Events older than the
    cutoff are ignored (the 24h stale-event sweep owns those).
    """
    in_flight: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        ts_raw = e.get("timestamp")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            continue
        tid = e.get("task_id")
        if not isinstance(tid, str):
            continue
        key = (e.get("backend") or "", tid)
        kind = e.get("kind")
        if kind == "execution_started":
            in_flight[key] = e
        elif kind == "execution_finished":
            in_flight.pop(key, None)
    return in_flight


def find_orphaned_in_flight(
    usage_store: _UsageStore,
    client: _IssueClient,
    *,
    now: datetime,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> list[OrphanedSlot]:
    """Return open in-flight slots whose Plane task is 404 or terminal.

    A 404 means the task was deleted or never created; a terminal state
    (Done/Cancelled/Closed) means it finished without the dispatch recording a
    ``execution_finished``.  Either way the slot is leaked.  Network/other
    errors are skipped — better to leave a slot than to clear one whose task is
    merely temporarily unreachable.
    """
    data = usage_store.load()
    events = data.get("events", [])
    cutoff = now - timedelta(hours=window_hours)
    in_flight = _open_in_flight(events, cutoff=cutoff)

    orphans: list[OrphanedSlot] = []
    for (backend, task_id), start_event in in_flight.items():
        try:
            issue = client.fetch_issue(task_id)
            task_state = state_name(issue)
            if not is_terminal(task_state):
                continue
            reason = f"task {task_id} is in terminal state {task_state!r}"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                reason = f"task {task_id} not found in Plane (404 — deleted or never created)"
            else:
                continue
        except Exception:
            continue
        orphans.append(
            OrphanedSlot(
                task_id=task_id,
                backend=backend,
                started_at=start_event.get("timestamp"),
                reason=reason,
            )
        )
    return orphans


def clear_orphaned_in_flight(
    usage_store: _UsageStore,
    client: _IssueClient,
    *,
    now: datetime,
    apply: bool,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> list[dict[str, Any]]:
    """Record the missing ``execution_finished`` for every orphaned slot.

    Returns one action entry per orphan, matching the shape ``board_unblock``
    has always emitted (``rule``/``reason``/``action``) so existing watchdog
    logging and tests are unaffected.  When *apply* is False the slots are
    reported as ``would_apply`` and the ledger is untouched.
    """
    cleared: list[dict[str, Any]] = []
    for slot in find_orphaned_in_flight(
        usage_store, client, now=now, window_hours=window_hours
    ):
        entry: dict[str, Any] = {
            "task_id": slot.task_id,
            "backend": slot.backend,
            "started_at": slot.started_at,
            "rule": ORPHAN_RULE,
            "reason": slot.reason,
        }
        if apply:
            try:
                usage_store.record_execution_finished(
                    task_id=slot.task_id,
                    backend=slot.backend,
                    now=now,
                )
                entry["action"] = "applied"
            except Exception as exc:  # noqa: BLE001 — surface, never abort the sweep
                entry["action"] = "error"
                entry["error"] = str(exc)
        else:
            entry["action"] = "would_apply"
        cleared.append(entry)
    return cleared


def reconcile_in_flight_on_startup(
    usage_store: _UsageStore,
    client: _IssueClient,
    *,
    now: datetime | None = None,
    lock_timeout: float = 2.0,
) -> list[dict[str, Any]]:
    """Clear orphaned in-flight slots once, at watcher startup.

    Serialised across the role processes (goal/test/improve/spec-author all boot
    together after a code-pull restart) with an exclusive lock on the usage
    store: the first worker to acquire it does the sweep, the rest find a clean
    ledger and return quickly.  Never raises — a watcher must not fail to start
    because reconciliation hit a transient Plane or filesystem error.
    """
    moment = now or datetime.now(UTC)
    try:
        from operations_center.audit_governance.file_locks import (
            FileLockTimeoutError,
            locked_state_file,
        )

        try:
            with locked_state_file(usage_store.path, timeout=lock_timeout):
                return clear_orphaned_in_flight(
                    usage_store, client, now=moment, apply=True
                )
        except FileLockTimeoutError:
            # Another worker holds the lock and is already reconciling.
            return []
    except Exception:  # noqa: BLE001 — startup must never fail on reconciliation
        logger.warning("startup in-flight reconciliation skipped", exc_info=True)
        return []


__all__ = [
    "DEFAULT_WINDOW_HOURS",
    "ORPHAN_RULE",
    "TERMINAL_STATES",
    "OrphanedSlot",
    "clear_orphaned_in_flight",
    "find_orphaned_in_flight",
    "is_terminal",
    "reconcile_in_flight_on_startup",
    "state_name",
]
