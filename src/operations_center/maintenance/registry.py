# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""MaintenanceRegistry — runs registered MaintenanceTasks per cycle.

Per-task interval is honored: a task runs only if ``interval_seconds`` has
elapsed since its previous run (or it has never run). Failures are caught,
recorded as ``status='failed'`` results, and do NOT block the rest of the
cycle.

Last-run timestamps persist to a small JSON sidecar so intervals survive
watchdog restarts. Default location: ``.console/maintenance_state.json``.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .contracts import MaintenanceContext, MaintenanceResult, MaintenanceTask

_logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path(".console/maintenance_state.json")


class MaintenanceRegistry:
    """Registry + scheduler for ``MaintenanceTask`` instances."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._tasks: list[MaintenanceTask] = []
        self._state_path: Path = state_path if state_path is not None else _DEFAULT_STATE_PATH
        self._last_run: dict[str, float] = self._load_state()

    # ---- registration --------------------------------------------------

    def register(self, task: MaintenanceTask) -> None:
        for existing in self._tasks:
            if existing.name == task.name:
                raise ValueError(f"MaintenanceTask {task.name!r} already registered")
        self._tasks.append(task)

    def list(self) -> list[MaintenanceTask]:
        return list(self._tasks)

    # ---- scheduling ----------------------------------------------------

    def _is_due(self, task: MaintenanceTask, now_epoch: float) -> bool:
        if not task.enabled:
            return False
        last = self._last_run.get(task.name)
        if last is None:
            return True
        return (now_epoch - last) >= float(task.interval_seconds)

    def run_due(self, ctx: MaintenanceContext) -> list[MaintenanceResult]:
        """Run every registered task whose interval has elapsed.

        Tasks not due are skipped silently (no result emitted). A failing
        task yields a ``status='failed'`` result with an ``error`` string;
        the registry continues to the next task.
        """
        now_epoch = ctx.now.timestamp()
        results: list[MaintenanceResult] = []
        for task in self._tasks:
            if not self._is_due(task, now_epoch):
                continue
            started = time.monotonic()
            try:
                result = task.run_once(ctx)
            except Exception as exc:  # noqa: BLE001 — uniform failure surface
                duration = time.monotonic() - started
                _logger.exception("maintenance task %s failed", task.name)
                result = MaintenanceResult(
                    name=task.name,
                    status="failed",
                    duration_seconds=duration,
                    details={},
                    error=str(exc),
                )
            # Mark run regardless of status so a chronically failing task
            # doesn't hog every cycle. Operators get visibility via the
            # 'failed' status; the interval still applies.
            self._last_run[task.name] = now_epoch
            results.append(result)
        self._save_state()
        return results

    # ---- state persistence --------------------------------------------

    def _load_state(self) -> dict[str, float]:
        try:
            if self._state_path.exists():
                raw = json.loads(self._state_path.read_text(encoding="utf-8"))
                last = raw.get("last_run", {}) if isinstance(raw, dict) else {}
                # Coerce values to float, drop malformed entries silently.
                out: dict[str, float] = {}
                for k, v in last.items():
                    try:
                        out[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
                return out
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("maintenance_state load failed: %s", exc)
        return {}

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_run": dict(self._last_run),
                "written_at": datetime.now(timezone.utc).isoformat(),
            }
            self._state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            _logger.warning("maintenance_state save failed: %s", exc)

    # ---- helpers ------------------------------------------------------

    def register_many(self, tasks: Iterable[MaintenanceTask]) -> None:
        for t in tasks:
            self.register(t)

    @property
    def state_path(self) -> Path:
        return self._state_path
