# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Global fleet work ceiling (determinism surface 6).

The audit found ~15 independent self-filers (follow-ups, scope-splits,
heartbeat-stall, drift, dependency, egress-probe, …) each with its OWN dedup/
retry cap but NO aggregate brake — and a settings comment that claimed a "global
budget applies" when no such object existed. A single systemic fault (e.g. every
lane's cycle failing → every detector firing) can therefore fan out unbounded
board work.

This is the missing aggregate brake: count OPEN, fleet-created tasks across the
whole board and refuse to create more past ``settings.max_open_fleet_tasks``.
Disabled by default (0) to preserve degrade-never-halt; opt-in via config.
"""

from __future__ import annotations

import logging

from .labels import label_value

logger = logging.getLogger(__name__)

# A task is "fleet-created" if it carries one of these origin markers. Human-
# authored tasks have none of these, so the ceiling never throttles operators.
_FLEET_ORIGIN_PREFIXES = ("source: board_worker", "source: autonomy", "source: improve")
_FLEET_ORIGIN_LABEL_KEYS = ("original-task-id", "handoff-reason", "lineage-id")

# Terminal states do not count toward the OPEN ceiling.
_TERMINAL_STATES = {"done", "cancelled", "canceled"}


def _label_names(issue: dict) -> list[str]:
    return [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
    ]


def _is_fleet_created(issue: dict) -> bool:
    names_lower = [n.lower() for n in _label_names(issue)]
    if any(n.startswith(_FLEET_ORIGIN_PREFIXES) for n in names_lower):
        return True
    labels = issue.get("labels", [])
    return any(label_value(labels, key) for key in _FLEET_ORIGIN_LABEL_KEYS)


def _is_open(issue: dict) -> bool:
    st = issue.get("state")
    name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
    return name not in _TERMINAL_STATES


def fleet_open_work_count(issues: list[dict]) -> int:
    """Number of open, fleet-created tasks on the board."""

    return sum(1 for issue in issues if _is_open(issue) and _is_fleet_created(issue))


def ceiling_reached(client, settings) -> bool:
    """True if the fleet has reached its global open-work ceiling.

    Fail-open: a disabled ceiling (0) or an unreadable board never throttles —
    a broken count must not deadlock self-healing (§0.1).
    """

    cap = int(getattr(settings, "max_open_fleet_tasks", 0) or 0)
    if cap <= 0:
        return False
    try:
        issues = client.list_issues()
    except Exception:
        logger.warning("work_ceiling: failed to list issues — not throttling (fail-open)")
        return False
    count = fleet_open_work_count(issues)
    if count >= cap:
        logger.warning(
            "work_ceiling: REACHED — %d/%d open fleet tasks; refusing new fleet work "
            '{"event": "work_ceiling_reached", "open": %d, "cap": %d}',
            count,
            cap,
            count,
            cap,
        )
        return True
    return False


__all__ = ["ceiling_reached", "fleet_open_work_count"]
