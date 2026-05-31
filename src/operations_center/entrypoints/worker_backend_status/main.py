# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI entry point: ``operations-center-worker-backend-status``."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.execution.usage_store import UsageStore

app = typer.Typer(
    help="Show current executor worker-backend cooldown state.",
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
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the cooldown snapshot as JSON.",
    ),
) -> None:
    now = datetime.now(UTC)
    store = UsageStore(path=usage_path) if usage_path is not None else UsageStore()
    snapshot = store.current_worker_backend_cooldowns(now=now)
    payload = {
        "observed_at": now.isoformat(),
        "worker_backends": snapshot,
        "usage_path": str(store.path),
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return

    _console.print("[bold]Worker backend cooldowns[/bold]")
    _console.print(f"  observed_at: {payload['observed_at']}")
    _console.print(f"  usage_path : {payload['usage_path']}")
    table = Table(show_header=True, header_style="bold")
    table.add_column("worker_backend")
    table.add_column("limit_kind")
    table.add_column("model")
    table.add_column("cooling_down")
    table.add_column("reset_at")
    table.add_column("seconds_remaining")
    for worker_backend, details in snapshot.items():
        cooldowns = details.get("cooldowns") or []
        if not cooldowns:
            # No per-kind detail (e.g. legacy events) — one summary row.
            table.add_row(
                worker_backend,
                "—",
                "—",
                "yes" if details["cooling_down"] else "no",
                str(details["reset_at"] or "—"),
                str(details["seconds_remaining"] or "—"),
            )
            continue
        for idx, cd in enumerate(cooldowns):
            table.add_row(
                worker_backend if idx == 0 else "",
                str(cd.get("limit_kind") or "—"),
                str(cd.get("model") or "all"),
                "yes",
                str(cd.get("reset_at") or "—"),
                str(cd.get("seconds_remaining") or "—"),
            )
    _console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    app()
