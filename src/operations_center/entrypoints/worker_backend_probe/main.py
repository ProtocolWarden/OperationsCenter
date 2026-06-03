# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI entry point: ``operations-center-worker-backend-probe``.

Probe each cooling worker-backend model and retract cooldowns that a live probe
proves stale (the limit lifted before its estimated reset). Safe to run on a
schedule — it does nothing when no backend is cooling, and a probe can only ever
clear a cooldown, never record one.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import typer
from rich.console import Console

from operations_center.backends.worker_backend_probe import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    refresh_cooldowns,
)
from operations_center.execution.usage_store import UsageStore

app = typer.Typer(
    help="Probe cooling worker-backend models and clear stale cooldowns.",
    add_completion=False,
)

_console = Console()


@app.command()
def _command(
    usage_path: Path | None = typer.Option(
        None,
        "--usage-path",
        help="Override the execution usage store path.",
    ),
    timeout: int = typer.Option(
        DEFAULT_PROBE_TIMEOUT_SECONDS,
        "--timeout",
        help="Per-probe timeout in seconds.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the probe report as JSON.",
    ),
) -> None:
    now = datetime.now(UTC)
    store = UsageStore(path=usage_path) if usage_path is not None else UsageStore()
    report = refresh_cooldowns(
        store,
        now=now,
        timeout=timeout,
        logger=None if as_json else (lambda msg: _console.print(f"  {msg}")),
    )
    if as_json:
        typer.echo(
            json.dumps(
                {"observed_at": now.isoformat(), "report": report, "usage_path": str(store.path)},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        return
    if not report:
        _console.print("[dim]No worker backends cooling — nothing to probe.[/dim]")
        return
    cleared = sum(1 for b in report.values() for ok in b.values() if ok)
    _console.print(
        f"[bold]Probe complete[/bold] — {cleared} model(s) confirmed runnable and cleared."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    app()
