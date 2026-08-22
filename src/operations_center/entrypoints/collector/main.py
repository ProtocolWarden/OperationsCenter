# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI entry point: ``operations-center-collect``.

The capture gate between the watchdog loop's two halves. PHASE 1 of
``.console/watchdog_loop_prompt.md`` spawns a Haiku sub-agent, pipes its stdout
through this command, and only what comes out the far side reaches PHASE 2.

Two things happen here, and they are independent layers:

1. **Validate** — the output is parsed against the OUTPUT SCHEMA
   (``observer.collector_schema``). A truncated, renamed, or self-contradictory
   report exits non-zero instead of flowing on as a report full of absent
   sections that read like good news.
2. **Fence** — the validated report is wrapped in an ``<<UNTRUSTED:nonce:...>>``
   span with the collector preamble, because its strings were harvested from
   tool output across the fleet and PHASE 2 acts on what it reads.

Validation says the report is *shaped* right. Fencing says it has no *authority*.
Neither implies the other, so the default path does both.

Exit codes mirror ``observer.cli``: 0 success, 1 validation failed, 5 input
missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from operations_center.injection import wrap_untrusted_report
from operations_center.observer.collector_schema import (
    CollectorReportError,
    parse_report,
)

EXIT_SUCCESS = 0
EXIT_VALIDATION_FAILED = 1
EXIT_FILE_MISSING = 5

app = typer.Typer(
    help="Validate and fence the watchdog collector sub-agent's report.",
    add_completion=False,
)

# Diagnostics and errors go to stderr so stdout carries only the handoff payload
# and stays safe to pipe straight into the next phase.
_err = Console(stderr=True)
_out = Console(soft_wrap=True)


def _read_input(input_path: Path | None) -> str:
    if input_path is None:
        return sys.stdin.read()
    if not input_path.exists():
        _err.print(f"[red]Input file does not exist: {input_path}[/red]")
        raise typer.Exit(EXIT_FILE_MISSING)
    return input_path.read_text(encoding="utf-8")


@app.command()
def _command(
    input_path: Path | None = typer.Option(
        None,
        "--input",
        "-i",
        help="Read collector output from a file instead of stdin.",
    ),
    fence: bool = typer.Option(
        True,
        "--fence/--no-fence",
        help=(
            "Wrap the validated report in an UNTRUSTED fence for PHASE 2. "
            "--no-fence emits bare JSON and is for inspection only — never pipe "
            "an unfenced report into a prompt."
        ),
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help=(
            "Also fail when the report needed recovery (markdown fence or stray "
            "prose stripped). Use in tests to catch prompt drift early."
        ),
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress diagnostics on stderr; exit code still reports the verdict.",
    ),
) -> None:
    raw = _read_input(input_path)

    try:
        report, diagnostics = parse_report(raw)
    except CollectorReportError as exc:
        if not quiet:
            _err.print("[red]COLLECTOR REPORT REJECTED[/red]")
            _err.print(str(exc))
            _err.print(
                "\n[dim]PHASE 2 must not run on this cycle. Treat as a collection "
                "failure: log it, schedule a short retry, do not infer health from "
                "the missing signals.[/dim]"
            )
        raise typer.Exit(EXIT_VALIDATION_FAILED) from exc

    if not diagnostics.clean and not quiet:
        _err.print("[yellow]Report recovered with deviations from the prompt contract:[/yellow]")
        if diagnostics.unfenced:
            _err.print("  - output was wrapped in a markdown fence (prompt says: no fences)")
        if diagnostics.stripped_prefix:
            _err.print(f"  - stripped leading prose: {diagnostics.stripped_prefix[:120]!r}")
        if diagnostics.stripped_suffix:
            _err.print(f"  - stripped trailing prose: {diagnostics.stripped_suffix[:120]!r}")

    if strict and not diagnostics.clean:
        if not quiet:
            _err.print("[red]--strict: recovered deviations are failures.[/red]")
        raise typer.Exit(EXIT_VALIDATION_FAILED)

    if report.aborted and not quiet:
        _err.print(
            f"[yellow]Collector aborted: {report.abort_reason}[/yellow] "
            "(partial report is expected on this path)"
        )

    # Re-serialize from the validated model rather than echoing the raw input, so
    # nothing that failed to round-trip through the schema can ride along into
    # the fence. by_alias keeps the wire names PHASE 2 reads ("from"/"to").
    #
    # Nulls INSIDE a section are preserved: the OUTPUT SCHEMA documents them as
    # values ("error": null, "exit_code": <int|null>, "memory_free_gb":
    # <float|null>), and a blanket exclude_none would silently change the shape
    # PHASE 2 was written against. Only the top-level sections that are legitimately
    # absent — the abort path, where the collector emits lock alone — are dropped.
    dumped = report.model_dump(by_alias=True)
    payload = json.dumps(
        {key: value for key, value in dumped.items() if value is not None},
        indent=2,
        # The report carries task titles, commit subjects and error strings from
        # across the fleet. Escaping them to \uXXXX would make the fenced text
        # harder for PHASE 2 to read and needlessly inflate it.
        ensure_ascii=False,
    )
    _out.print(wrap_untrusted_report(payload) if fence else payload, markup=False, highlight=False)

    raise typer.Exit(EXIT_SUCCESS)


def main() -> None:
    app()


if __name__ == "__main__":
    app()
