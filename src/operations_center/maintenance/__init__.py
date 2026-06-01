# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Generic maintenance-task registration pattern (ADR 0007 follow-up D).

Contract-first surface so any per-cycle hygiene operation can register with
the watchdog and run with uniform observability, instead of hardcoded
sequences inside individual watchers.

Public surface:
    * ``MaintenanceTask``  — Protocol every maintenance op implements
    * ``MaintenanceContext`` — per-cycle context handed to ``run_once``
    * ``MaintenanceResult`` — uniform structured outcome
    * ``MaintenanceRegistry`` — registers tasks, runs the ones whose
      advisory interval has elapsed, persists last-run state
"""

from __future__ import annotations

from .contracts import (
    MaintenanceContext,
    MaintenanceResult,
    MaintenanceTask,
)
from .registry import MaintenanceRegistry

__all__ = [
    "MaintenanceContext",
    "MaintenanceResult",
    "MaintenanceTask",
    "MaintenanceRegistry",
]
