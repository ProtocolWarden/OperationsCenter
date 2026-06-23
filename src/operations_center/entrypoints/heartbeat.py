# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Liveness-vs-success heartbeats for the watcher fleet.

The original heartbeats recorded only ``at`` (a timestamp written every poll
cycle). That proves the *wrapper loop ran*, NOT that the loop did useful work —
so a watcher that catches-and-continues on every cycle (e.g. an empty GitHub
token making every poll raise) writes a perfectly fresh "active" heartbeat while
being 100% broken. That is exactly how the 2026-06-21 reviewer outage stayed
invisible: 813 identical failures, 0 restarts, heartbeat still ``"active"``.

This module separates the two signals:

- ``at`` — **liveness**: updated on every cycle, success or failure.
- ``last_success_at`` — **progress**: updated ONLY when a cycle completed its
  core work without raising. Preserved verbatim across failing cycles.
- ``consecutive_failures`` / ``last_error`` — how long it has been stuck and why.

A consumer (``HeartbeatStallTask``) can then detect the "live but not
succeeding" state — fresh ``at``, stale ``last_success_at`` — that the PID-only
watchdog and the old heartbeat both missed.

Writes are atomic (tmp + ``os.replace``) so a concurrent reader never sees a torn
file, and best-effort (a heartbeat write must never break the poll loop).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def _path(status_dir: Path, role: str) -> Path:
    return Path(status_dir) / f"heartbeat_{role}.json"


def read_heartbeat(status_dir: Path, role: str) -> dict | None:
    """Return the parsed heartbeat for ``role``, or ``None`` if absent/unreadable."""
    try:
        return json.loads(_path(status_dir, role).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_heartbeat(
    status_dir: Path,
    role: str,
    *,
    status: str = "idle",
    success: bool = True,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    """Write the ``role`` heartbeat, carrying ``last_success_at`` across failures.

    ``success=True`` stamps ``last_success_at = at`` and resets the failure
    counter; ``success=False`` updates only ``at`` (liveness) and bumps
    ``consecutive_failures`` while preserving the prior ``last_success_at`` — so a
    crash-looping watcher's ``last_success_at`` ages even as ``at`` stays fresh.
    """
    from datetime import UTC

    ts = (now or datetime.now(UTC)).isoformat()
    prior = read_heartbeat(status_dir, role) or {}
    if success:
        last_success_at: str | None = ts
        consecutive = 0
        last_error: str | None = None
    else:
        last_success_at = prior.get("last_success_at")
        consecutive = int(prior.get("consecutive_failures", 0) or 0) + 1
        last_error = error or prior.get("last_error")

    payload = {
        "role": role,
        "at": ts,
        "status": status,
        "last_success_at": last_success_at,
        "consecutive_failures": consecutive,
        "last_error": last_error,
    }
    try:
        sd = Path(status_dir)
        sd.mkdir(parents=True, exist_ok=True)
        dest = _path(sd, role)
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, dest)  # atomic on POSIX
    except Exception:
        pass


def touch_liveness(
    status_dir: Path,
    role: str,
    *,
    status: str = "executing",
    now: datetime | None = None,
) -> None:
    """Update ONLY liveness (``at`` + ``status``), preserving the progress fields.

    Used by the in-task heartbeat thread: a long-running task must keep ``at``
    fresh so the lane looks alive, but it must NOT stamp ``last_success_at`` or
    reset ``consecutive_failures`` — otherwise a lane busy-failing real tasks
    (claim → run → fail → reclaim → repeat) keeps a perpetually-fresh success
    heartbeat and re-creates the exact "live but not succeeding" blind spot the
    success/liveness split exists to close. Only a genuinely completed *successful*
    cycle (``write_heartbeat(success=True)``) may advance progress.
    """
    from datetime import UTC

    ts = (now or datetime.now(UTC)).isoformat()
    prior = read_heartbeat(status_dir, role) or {}
    payload = {
        "role": role,
        "at": ts,
        "status": status,
        # preserved verbatim — liveness must never touch progress
        "last_success_at": prior.get("last_success_at"),
        "consecutive_failures": int(prior.get("consecutive_failures", 0) or 0),
        "last_error": prior.get("last_error"),
    }
    try:
        sd = Path(status_dir)
        sd.mkdir(parents=True, exist_ok=True)
        dest = _path(sd, role)
        tmp = dest.with_suffix(".json.liveness.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, dest)
    except Exception:
        pass


def _age_seconds(ts: str | None, now: datetime) -> float | None:
    """Seconds between ISO timestamp ``ts`` and ``now``; ``None`` if unparseable."""
    if not ts:
        return None
    try:
        return (now - datetime.fromisoformat(ts)).total_seconds()
    except Exception:
        return None


def is_live(hb: dict, *, now: datetime, max_liveness_seconds: float) -> bool:
    """The watcher loop is iterating (``at`` is recent). A stale ``at`` is a
    dead/hung process — a supervisor/PID concern, not a stall."""
    age = _age_seconds(hb.get("at"), now)
    return age is not None and age <= max_liveness_seconds


def success_stalled(
    hb: dict,
    *,
    now: datetime,
    max_success_age_seconds: float,
    min_consecutive_failures: int = 3,
) -> bool:
    """True when the watcher is making no progress despite running.

    Stalled iff it has failed at least ``min_consecutive_failures`` cycles in a
    row AND its last success is older than ``max_success_age_seconds`` (or it has
    never succeeded). The failure-count guard avoids flagging a single transient
    blip; the age guard avoids flagging a genuinely idle-but-healthy lane (which
    keeps ``last_success_at`` fresh because an idle cycle is a *success*).
    """
    if int(hb.get("consecutive_failures", 0) or 0) < min_consecutive_failures:
        return False
    age = _age_seconds(hb.get("last_success_at"), now)
    if age is None:
        return True  # never succeeded, and failing repeatedly
    return age > max_success_age_seconds


__all__ = [
    "is_live",
    "read_heartbeat",
    "success_stalled",
    "write_heartbeat",
]
