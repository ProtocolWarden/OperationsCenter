# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""LedgerMaintainTask — run the operator-interventions ledger consolidation loop.

The fleet already *captures* candidates (pr_review_watcher shells out to
``cl ledger capture``). This maintenance task runs the other half each cycle, so
the controller — not a human — does the observing and the verifiable promoting:

- ``cl ledger promote --repos-root <root>`` — auto-promote each recurrence of a
  signal whose first (human) judgment carries a live ``[check: ref]``, by
  re-verifying that check still resolves. Exit 1 means an encoded check
  *regressed* (its candidates are left for a human); that is surfaced in the
  result details, not treated as a task failure.
- ``cl ledger observe`` — surface signals recurring without a judgment yet (the
  patterns a human still needs to encode once).

Both are best-effort shell-outs to the ``cl`` CLI (ContextLifecycle), matching
the capture-side pattern. Neither commits or pushes the private manifest —
mutations land in the working tree only; promotion writes are idempotent (a
promoted line is no longer a candidate, so a re-run won't touch it).
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from operations_center.config.settings import Settings
from operations_center.maintenance.contracts import MaintenanceContext, MaintenanceResult

logger = logging.getLogger(__name__)

# 1 hour: candidates accrue slowly (a genuine human intervention is rare), and
# promotion is idempotent, so there's no value polling tighter than this.
DEFAULT_INTERVAL_SECONDS = 3600
_TIMEOUT_SECONDS = 30


def resolve_repos_root(settings: Settings) -> Path:
    """Directory holding the managed repo checkouts (for resolving ``[check: ref]``).

    A check ref names a repo by directory (``custodian:Custodian:OC3``), so the
    root is the parent those checkouts share. Prefer a configured repo's
    ``local_path`` parent; fall back to this package's checkout layout
    (``…/<repos-root>/OperationsCenter/src/operations_center/…``).
    """
    for cfg in settings.repos.values():
        if cfg.local_path:
            return Path(cfg.local_path).resolve().parent
    # parents: ledger_maintain → maintenance → operations_center → src → OC root
    return Path(__file__).resolve().parents[4]


class LedgerMaintainTask:
    """MaintenanceTask: promote verifiable recurrences + observe novel patterns."""

    name = "ledger_maintain"

    def __init__(
        self,
        settings: Settings,
        *,
        repos_root: Path | None = None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        enabled: bool = True,
        cl_bin: str = "cl",
        runner=subprocess.run,
    ) -> None:
        self._settings = settings
        self._repos_root = repos_root if repos_root is not None else resolve_repos_root(settings)
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._cl_bin = cl_bin
        self._run = runner

    def _cl(self, *args: str) -> tuple[int, str]:
        """Best-effort `cl` call. Returns (returncode, combined output)."""
        try:
            proc = self._run(
                [self._cl_bin, *args],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never break the cycle
            logger.debug("ledger_maintain: `cl %s` failed — %s", " ".join(args), exc)
            return (-1, str(exc))
        out = (proc.stdout or "") + (proc.stderr or "")
        return (proc.returncode, out.strip())

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult:
        started = time.monotonic()
        details: dict[str, object] = {"repos_root": str(self._repos_root)}

        # Promote first: clear the verifiable recurrences and catch regressions.
        promote_rc, promote_out = self._cl(
            "ledger", "promote", "--repos-root", str(self._repos_root)
        )
        details["promote_rc"] = promote_rc
        details["promoted"] = promote_out.count("\n  ✓ ")
        details["regressed"] = promote_rc == 1  # exit 1 == an encoded check rotted
        if promote_out:
            details["promote_out"] = promote_out[:2000]

        # Then observe: what novel patterns still await a first human judgment.
        observe_rc, observe_out = self._cl("ledger", "observe")
        details["observe_rc"] = observe_rc
        details["recurring"] = observe_out.count("\n  x")
        if observe_out:
            details["observe_out"] = observe_out[:2000]

        return MaintenanceResult(
            name=self.name,
            status="ok",
            duration_seconds=time.monotonic() - started,
            details=details,
        )


__all__ = ["DEFAULT_INTERVAL_SECONDS", "LedgerMaintainTask", "resolve_repos_root"]
