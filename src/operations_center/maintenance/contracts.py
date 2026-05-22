# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Contracts for the maintenance-task registration pattern."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class MaintenanceContext:
    """Per-cycle context passed to every ``MaintenanceTask.run_once``.

    Attributes:
        cycle_id: UUID-like identifier shared by all tasks in a cycle so
            their results are correlatable in logs / artifacts.
        now: UTC timestamp captured at the start of the cycle.
        resources: Shared resources the watchdog has already constructed
            (e.g. ``{"plane_client": PlaneClient(...), "settings": ...}``).
            Tasks may pull what they need; if a resource is absent a task
            is responsible for constructing its own.
    """

    cycle_id: str
    now: datetime
    resources: dict[str, Any] = field(default_factory=dict)


@dataclass
class MaintenanceResult:
    """Uniform structured outcome of a single maintenance-task run."""

    name: str
    status: Literal["ok", "skipped", "failed"]
    duration_seconds: float
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class MaintenanceTask(Protocol):
    """Contract for any per-cycle maintenance operation.

    Implementations expose three attributes (``name``, ``interval_seconds``,
    ``enabled``) and a ``run_once(ctx)`` method that returns a
    ``MaintenanceResult``. ``interval_seconds`` is advisory — the registry
    honors it but a task is free to short-circuit inside ``run_once`` (e.g.
    return ``status='skipped'``) when its own preconditions aren't met.
    """

    name: str
    interval_seconds: int
    enabled: bool

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult: ...
