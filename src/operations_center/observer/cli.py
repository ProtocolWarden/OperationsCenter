# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Snapshot validation CLI — operations-center-observer-snapshot.

Command-line interface for validating repository state snapshots with:
- 8 main commands: validate, observe-and-validate, list, show, compare, export, import, cleanup
- Multiple output formats: table, JSON, markdown, text
- Error handling with distinct exit codes for different failure classes
- Configurable validation layers and tolerance thresholds
- Configuration from environment variables and command-line options
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.observer.collectors.extraction_history_collector import (
    ExtractionHistoryCollector,
)
from operations_center.observer.extraction_history_query import ExtractionHistoryQuery
from operations_center.observer.extraction_report_formatter import ExtractionReportFormatter
from operations_center.observer.query import TestSignalQuery, TimeRange
from operations_center.observer.snapshot_loader import SnapshotLoadError, SnapshotLoader
from operations_center.observer.snapshot_output_formatter import (
    OutputFormat,
    SnapshotOutputFormatter,
)
from operations_center.observer.snapshot_validation_engine import (
    ValidationConfig,
    ValidationError,
    SnapshotValidationEngine,
)

__version__ = "0.1.0"


app = typer.Typer(
    help="Snapshot validator CLI for manual CI run testing.",
    no_args_is_help=True,
)
console = Console()
logger = logging.getLogger(__name__)

# Exit codes (matching artifact_index precedent)
EXIT_SUCCESS = 0
EXIT_VALIDATION_FAILED = 1
EXIT_NOT_FOUND = 2
EXIT_LOAD_ERROR = 3
EXIT_CONFIG_ERROR = 4
EXIT_FILE_MISSING = 5


def _get_env_or_default(key: str, default: str | None = None) -> str | None:
    """Get configuration value from environment variable.

    Environment variables follow the pattern OC_SNAPSHOT_<SETTING>.
    Examples:
    - OC_SNAPSHOT_REPO_PATH: repository path for accuracy checks
    - OC_SNAPSHOT_TOLERANCE: global tolerance as decimal
    - OC_SNAPSHOT_TIMEOUT: max seconds for accuracy checks
    - OC_SNAPSHOT_LOG_LEVEL: logging level

    Args:
        key: Configuration key (e.g., 'REPO_PATH' → 'OC_SNAPSHOT_REPO_PATH')
        default: Default value if env var not set

    Returns:
        Environment variable value or default
    """
    env_key = f"OC_SNAPSHOT_{key.upper()}"
    return os.environ.get(env_key, default)


def _setup_logging(log_level: str, debug: bool) -> None:
    """Configure logging.

    Args:
        log_level: Logging level (debug|info|warning|error)
        debug: Enable debug mode (overrides log_level)
    """
    if debug:
        log_level = "debug"

    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }

    logging.basicConfig(
        level=level_map.get(log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _version_callback(value: bool) -> None:
    """Handle version flag."""
    if value:
        console.print(f"[cyan]operations-center-observer-snapshot[/cyan] {__version__}")
        raise typer.Exit(0)


def _parse_layers(layers_str: str | None) -> list[int]:
    """Parse layer specification string.

    Args:
        layers_str: Comma-separated layer numbers (e.g., "1,2,3")

    Returns:
        List of layer numbers

    Raises:
        ValueError: If layers_str is invalid
    """
    if layers_str is None:
        return [1, 2, 3]

    try:
        layers = [int(part.strip()) for part in layers_str.split(",")]
        for layer in layers:
            if layer < 1 or layer > 5:
                raise ValueError(f"Layer must be 1-5, got {layer}")
        return sorted(set(layers))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid layers specification '{layers_str}': {e}") from e


def _build_tolerance_dict(
    global_tolerance: float,
    coverage_tolerance: float | None,
    test_count_tolerance: float | None,
) -> dict[str, float]:
    """Build tolerance configuration dict.

    Args:
        global_tolerance: Default tolerance for all metrics
        coverage_tolerance: Coverage-specific tolerance (overrides global)
        test_count_tolerance: Test count-specific tolerance (overrides global)

    Returns:
        Tolerance dict
    """
    tolerance = {
        "test_count": test_count_tolerance or global_tolerance,
        "coverage": coverage_tolerance or global_tolerance,
    }
    return tolerance


def _format_duration(ms: float) -> str:
    """Format duration in milliseconds.

    Args:
        ms: Duration in milliseconds

    Returns:
        Formatted duration string
    """
    if ms < 1000:
        return f"{ms:.1f}ms"
    seconds = ms / 1000
    return f"{seconds:.2f}s"


@app.callback()
def config_callback(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit",
        is_eager=True,
        callback=_version_callback,
    ),
    log_level: str = typer.Option(
        None,
        "--log-level",
        help="Logging level: debug|info|warning|error (or OC_SNAPSHOT_LOG_LEVEL env var)",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug mode (implies --log-level debug)",
    ),
) -> None:
    """Configure CLI globally."""

    final_log_level = log_level or _get_env_or_default("LOG_LEVEL", "info") or "info"
    _setup_logging(final_log_level, debug)


@app.command("validate")
def cmd_validate(
    snapshot_path: str = typer.Argument(
        ...,
        help="Path to snapshot JSON/YAML or run_id if loading from storage",
    ),
    layers: str | None = typer.Option(
        None,
        "--layers",
        help="Comma-separated layer numbers (1,2,3,4,5) — default: 1,2,3 (or OC_SNAPSHOT_LAYERS)",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Path to baseline snapshot for layer 5 (or OC_SNAPSHOT_BASELINE)",
    ),
    repo_path: Path | None = typer.Option(
        None,
        "--repo-path",
        help="Repository path for accuracy checks (or OC_SNAPSHOT_REPO_PATH, default: cwd)",
    ),
    tolerance: float | None = typer.Option(
        None,
        "--tolerance",
        help="Global tolerance as decimal (or OC_SNAPSHOT_TOLERANCE, default: 0.05)",
    ),
    coverage_tolerance: float | None = typer.Option(
        None,
        "--coverage-tolerance",
        help="Coverage tolerance (overrides --tolerance or OC_SNAPSHOT_COVERAGE_TOLERANCE)",
    ),
    test_count_tolerance: float | None = typer.Option(
        None,
        "--test-count-tolerance",
        help="Test count tolerance (overrides --tolerance or OC_SNAPSHOT_TEST_COUNT_TOLERANCE)",
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Max seconds for layer 4 (or OC_SNAPSHOT_TIMEOUT, default: 60)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed error information",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save validation report to file (JSON format)",
    ),
    format_str: str = typer.Option(
        "table",
        "--format",
        help="Output format: table|json|markdown|text",
    ),
    retry_transient: bool = typer.Option(
        False,
        "--retry-transient",
        help="Auto-retry on transient errors (network, timeout)",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        help="Max retry attempts for transient errors",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Validate snapshot against configured layers."""
    final_layers = layers or _get_env_or_default("LAYERS")
    try:
        parsed_layers = _parse_layers(final_layers)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    try:
        final_tolerance = tolerance
        if final_tolerance is None:
            env_tolerance = _get_env_or_default("TOLERANCE")
            final_tolerance = float(env_tolerance) if env_tolerance else 0.05

        final_repo_path = repo_path
        if final_repo_path is None:
            env_repo = _get_env_or_default("REPO_PATH")
            final_repo_path = Path(env_repo) if env_repo else Path.cwd()

        final_timeout = timeout
        if final_timeout is None:
            env_timeout = _get_env_or_default("TIMEOUT")
            final_timeout = int(env_timeout) if env_timeout else 60

        final_baseline = baseline
        if final_baseline is None:
            env_baseline = _get_env_or_default("BASELINE")
            final_baseline = Path(env_baseline) if env_baseline else None

        tolerance_dict = _build_tolerance_dict(
            final_tolerance, coverage_tolerance, test_count_tolerance
        )

        config = ValidationConfig(
            layers=parsed_layers,
            tolerance=tolerance_dict,
            repo_path=final_repo_path,
            timeout=final_timeout,
            retry_on_transient=retry_transient,
            max_retries=max_retries,
        )

        engine = SnapshotValidationEngine()
        baseline_path = str(final_baseline) if final_baseline else None

        try:
            report, was_retried = engine.validate_with_retry(
                snapshot_path,
                config=config,
                baseline_source=baseline_path,
            )
        except ValidationError as e:
            if not quiet:
                console.print(f"[red]Error: {e.message}[/red]")
                if verbose and e.context:
                    console.print(
                        f"[dim]{json.dumps(e.context, indent=2, ensure_ascii=False)}[/dim]"
                    )
            raise typer.Exit(EXIT_LOAD_ERROR)

        formatter = SnapshotOutputFormatter()
        output_text = formatter.format(report, OutputFormat(format_str))

        if not quiet:
            console.print(output_text)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report.to_dict(), indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            if not quiet:
                console.print(f"[green]✓[/green] Report saved to {output}")

        exit_code = EXIT_SUCCESS if report.passed else EXIT_VALIDATION_FAILED
        raise typer.Exit(exit_code)

    except typer.Exit:
        raise
    except Exception as e:
        if not quiet:
            console.print(f"[red]Unexpected error: {e}[/red]")
        logger.exception("Unexpected error in validate command")
        raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("observe-and-validate")
def cmd_observe_and_validate(
    repo_path: Path | None = typer.Option(
        None,
        "--repo-path",
        help="Repository path (default: current directory)",
    ),
    output_dir: Path = typer.Option(
        Path("tools/report/operations_center/observer"),
        "--output-dir",
        help="Where to save snapshot",
    ),
    format_snapshot: str = typer.Option(
        "json",
        "--format",
        help="Snapshot format: json|yaml",
    ),
    layers: str | None = typer.Option(
        None,
        "--layers",
        help="Validation layers to run (default: 1,2,3)",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Include slow layers (4,5) — takes 60-120s",
    ),
    skip_validation: bool = typer.Option(
        False,
        "--skip-validation",
        help="Collect snapshot but skip validation",
    ),
    output_report: Path | None = typer.Option(
        None,
        "--output",
        help="Save validation report to file",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Detailed output",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Generate snapshot and validate it."""
    if not quiet:
        console.print("[cyan]observe-and-validate[/cyan] command not yet implemented")
        console.print("This command requires RepoObserver integration.")
    raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("list")
def cmd_list(
    limit: int = typer.Option(
        10,
        "--limit",
        help="Max snapshots to list",
    ),
    order: str = typer.Option(
        "recent",
        "--order",
        help="Sort order: recent|oldest|name",
    ),
    filter_status: str | None = typer.Option(
        None,
        "--filter",
        help="Filter: valid|invalid (if validation cached)",
    ),
    format_str: str = typer.Option(
        "table",
        "--format",
        help="Output format: table|json|csv",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Include file size, checksum, validation status",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="Storage backend: local|s3|http",
    ),
    storage_root: Path | None = typer.Option(
        None,
        "--storage-root",
        help="Storage root directory (local backend)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """List stored snapshots."""
    if backend != "local":
        if not quiet:
            console.print("[red]Error: Non-local backends not yet supported[/red]")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    root = storage_root or Path("tools/report/operations_center/observer")

    if not root.exists():
        if not quiet:
            console.print("[yellow]No snapshots found (directory does not exist)[/yellow]")
        return

    try:
        snapshot_dirs = sorted(
            [d for d in root.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=(order == "recent"),
        )
        snapshot_dirs = snapshot_dirs[:limit]

        if not snapshot_dirs:
            if not quiet:
                console.print("[yellow]No snapshots found[/yellow]")
            return

        if format_str == "table":
            table = Table(title=f"Snapshots ({len(snapshot_dirs)} total)")
            table.add_column("run_id", style="cyan")
            table.add_column("observed_at", style="magenta")
            table.add_column("size", style="yellow")

            for snapshot_dir in snapshot_dirs:
                json_file = snapshot_dir / "repo_state_snapshot.json"
                size = ""
                if json_file.exists():
                    size_bytes = json_file.stat().st_size
                    if size_bytes < 1024:
                        size = f"{size_bytes} B"
                    elif size_bytes < 1024 * 1024:
                        size = f"{size_bytes / 1024:.1f} KB"
                    else:
                        size = f"{size_bytes / (1024 * 1024):.1f} MB"

                table.add_row(snapshot_dir.name, "—", size)

            if not quiet:
                console.print(table)

        elif format_str == "json":
            snapshots = [{"run_id": d.name} for d in snapshot_dirs]
            if not quiet:
                console.print(json.dumps(snapshots, indent=2, ensure_ascii=False))

    except Exception as e:
        if not quiet:
            console.print(f"[red]Error listing snapshots: {e}[/red]")
        logger.exception("Error in list command")
        raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("show")
def cmd_show(
    snapshot_path: str = typer.Argument(
        ...,
        help="Path to snapshot or run_id",
    ),
    field: str | None = typer.Option(
        None,
        "--field",
        help="Show specific field (e.g. repo, signals.test_signal)",
    ),
    format_str: str = typer.Option(
        "json",
        "--format",
        help="Output format: json|yaml|markdown",
    ),
    pretty: bool = typer.Option(
        False,
        "--pretty",
        help="Color-coded pretty print",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="Storage backend",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Display snapshot contents."""
    try:
        loader = SnapshotLoader()
        snapshot = loader.load(snapshot_path)

        if field:
            parts = field.split(".")
            obj = snapshot.model_dump()
            for part in parts:
                if isinstance(obj, dict):
                    obj = obj.get(part)
                else:
                    raise ValueError(f"Cannot access field '{part}' on non-dict")
        else:
            obj = snapshot.model_dump()

        if format_str == "json":
            output = json.dumps(obj, indent=2, default=str, ensure_ascii=False)
        elif format_str == "yaml":
            import yaml

            output = yaml.dump(obj, default_flow_style=False)
        else:
            if not quiet:
                console.print("[red]Error: unsupported format (use json or yaml)[/red]")
            raise typer.Exit(EXIT_CONFIG_ERROR)

        if pretty:
            console.print_json(output)
        else:
            if not quiet:
                console.print(output)

        raise typer.Exit(EXIT_SUCCESS)

    except SnapshotLoadError as e:
        if not quiet:
            console.print(f"[red]Error: {e.message}[/red]")
        raise typer.Exit(EXIT_LOAD_ERROR)
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error: {e}[/red]")
        logger.exception("Error in show command")
        raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("compare")
def cmd_compare(
    snapshot1: str = typer.Argument(..., help="First snapshot path/ID"),
    snapshot2: str = typer.Argument(..., help="Second snapshot path/ID"),
    format_str: str = typer.Option(
        "diff",
        "--format",
        help="Output format: diff|json|table",
    ),
    signals_only: str | None = typer.Option(
        None,
        "--signals",
        help="Compare specific signals (comma-separated)",
    ),
    stats: bool = typer.Option(
        False,
        "--stats",
        help="Show change statistics",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Save comparison to file",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="Storage backend",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Compare two snapshots."""
    if not quiet:
        console.print("[cyan]compare[/cyan] command not yet implemented")
    raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("export")
def cmd_export(
    snapshot_id: str = typer.Argument(
        ...,
        help="run_id or path of snapshot to export",
    ),
    output_path: Path = typer.Argument(
        ...,
        help="Output file path",
    ),
    format_str: str | None = typer.Option(
        None,
        "--format",
        help="Format: json|yaml|jsonl (auto-detect from output extension if not set)",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="Storage backend",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Export snapshot to file."""
    try:
        loader = SnapshotLoader()
        snapshot = loader.load(snapshot_id)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format_str is None:
            suffix = output_path.suffix.lower()
            if suffix == ".json":
                format_str = "json"
            elif suffix in {".yaml", ".yml"}:
                format_str = "yaml"
            elif suffix == ".jsonl":
                format_str = "jsonl"
            else:
                format_str = "json"

        if format_str == "json":
            import json

            output_path.write_text(
                json.dumps(snapshot.model_dump(), indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        elif format_str == "yaml":
            import yaml

            output_path.write_text(
                yaml.dump(snapshot.model_dump(), default_flow_style=False), encoding="utf-8"
            )
        elif format_str == "jsonl":
            import json

            output_path.write_text(
                json.dumps(snapshot.model_dump(), default=str, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            if not quiet:
                console.print(f"[red]Error: unsupported format '{format_str}'[/red]")
            raise typer.Exit(EXIT_CONFIG_ERROR)

        if not quiet:
            console.print(f"[green]✓[/green] Exported snapshot to {output_path}")

        raise typer.Exit(EXIT_SUCCESS)

    except SnapshotLoadError as e:
        if not quiet:
            console.print(f"[red]Error: {e.message}[/red]")
        raise typer.Exit(EXIT_LOAD_ERROR)
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error: {e}[/red]")
        logger.exception("Error in export command")
        raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("import")
def cmd_import(
    input_path: Path = typer.Argument(
        ...,
        help="Input file path (JSON/YAML/JSONL)",
    ),
    format_str: str | None = typer.Option(
        None,
        "--format",
        help="Format: json|yaml (auto-detect if not set)",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="Storage backend",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Where to store (local backend)",
    ),
    validate_after: bool = typer.Option(
        True,
        "--validate-after/--no-validate-after",
        help="Run validation after import",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Import snapshot from file."""
    if not quiet:
        console.print("[cyan]import[/cyan] command not yet implemented")
    raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("cleanup")
def cmd_cleanup(
    days: int = typer.Option(
        30,
        "--days",
        help="Delete snapshots older than N days",
    ),
    keep_count: int = typer.Option(
        50,
        "--keep-count",
        help="Keep at least N most recent snapshots",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Actually delete (default: dry-run preview)",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="Storage backend",
    ),
    storage_root: Path | None = typer.Option(
        None,
        "--storage-root",
        help="Storage root directory (local backend)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Remove old snapshots."""
    if not quiet:
        console.print("[cyan]cleanup[/cyan] command not yet implemented")
    raise typer.Exit(EXIT_SUCCESS)


@app.command("query-flaky-tests")
def cmd_query_flaky_tests(
    hours: int = typer.Option(
        24,
        "--hours",
        help="Look back N hours (default: 24)",
    ),
    storage_root: Path | None = typer.Option(
        None,
        "--storage-root",
        help="Storage root for snapshots (default: tools/report/operations_center/observer)",
    ),
    format_str: str = typer.Option(
        "table",
        "--format",
        help="Output format: table|json|markdown",
    ),
    include_assertions: bool = typer.Option(
        False,
        "--include-assertions",
        help="Include assertion messages (slower, more detailed)",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        help="Max tests to display (0 = all)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
) -> None:
    """Query and display extracted test failure data.

    Shows test names and assertion messages from failures in the last N hours.
    Supports JSON, table, and markdown output formats.

    Examples:

        # Show failing tests from last 24 hours as table
        cli query-flaky-tests --hours 24

        # Get JSON output for integration
        cli query-flaky-tests --format json

        # Include assertion messages
        cli query-flaky-tests --include-assertions --hours 6
    """
    try:
        root = storage_root or Path("tools/report/operations_center/observer")
        if not root.exists():
            if not quiet:
                console.print(f"[yellow]Warning: snapshot root does not exist: {root}[/yellow]")
            raise typer.Exit(EXIT_NOT_FOUND)

        query = TestSignalQuery(root=root)
        timerange = TimeRange.last_hours(hours)

        # Get test names
        test_names = query.get_failing_test_names(timerange)
        assertions = None
        if include_assertions:
            assertions = query.get_failing_assertion_messages(timerange)

        if not test_names:
            if not quiet:
                console.print(f"[yellow]No failing tests found in the last {hours} hours[/yellow]")
            raise typer.Exit(EXIT_SUCCESS)

        formatter = ExtractionReportFormatter()

        if format_str == "json":
            output = formatter.format_test_names_as_json(test_names)
            if include_assertions and assertions:
                # Combine both into single JSON output
                test_json = json.loads(formatter.format_test_names_as_json(test_names))
                assertions_json = json.loads(
                    formatter.format_assertion_messages_as_json(assertions)
                )
                combined = {
                    "test_failures": test_json,
                    "assertion_failures": assertions_json,
                }
                output = json.dumps(combined, indent=2, ensure_ascii=False)
        elif format_str == "markdown":
            output = formatter.format_test_names_as_markdown(test_names)
            if include_assertions and assertions:
                output += "\n\n" + formatter.format_assertion_messages_as_markdown(assertions)
        else:  # table
            output = formatter.format_test_names_as_table(test_names)
            if include_assertions and assertions:
                output += "\n\n" + formatter.format_assertion_messages_as_table(assertions)

        if not quiet:
            console.print(output)

        raise typer.Exit(EXIT_SUCCESS)

    except typer.Exit:
        raise
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error querying flaky tests: {e}[/red]")
        logger.exception("Error in query-flaky-tests command")
        raise typer.Exit(EXIT_CONFIG_ERROR)


@app.command("extraction-health")
def cmd_extraction_health(
    hours: int = typer.Option(24, "--hours", help="Look back N hours (default: 24)"),
    storage_root: Path | None = typer.Option(
        None,
        "--storage-root",
        help="Storage root for snapshots (default: tools/report/operations_center/observer)",
    ),
    format_str: str = typer.Option("json", "--format", help="Output format: json|table"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    trend_days: int = typer.Option(
        7, "--trend-days", help="Days of history to summarize in the trend section"
    ),
) -> None:
    """Report extraction coverage health for flaky test data.

    Emits the structured ExtractionHealth metrics (success_rate,
    complete/partial/no_extraction counts, edge_case_summary) from
    ``get_extraction_health()`` — the signal the watchdog collector consumes.
    Unlike ``query-flaky-tests`` (which emits per-test extraction *records*),
    this emits the aggregate coverage health as a single object.

    Each run also appends the current reading to the extraction-history time
    series and (best-effort) augments the JSON with a ``history`` section —
    trend, regression slope, and detected anomalies over ``--trend-days`` — so
    repeated collector runs build a longitudinal signal, not just a snapshot.

    Examples:

        # JSON for the collector (haiku_collector_prompt.md STEP 3)
        cli extraction-health --format json --hours 24
    """
    from dataclasses import asdict

    try:
        root = storage_root or Path("tools/report/operations_center/observer")
        if not root.exists():
            if not quiet:
                console.print(f"[yellow]Warning: snapshot root does not exist: {root}[/yellow]")
            raise typer.Exit(EXIT_NOT_FOUND)

        query = TestSignalQuery(root=root)
        health = query.get_extraction_health(TimeRange.last_hours(hours))
        payload = asdict(health)

        # Record this reading into the extraction-history time series and surface
        # the longitudinal trend. Best-effort: the point-in-time health is the
        # contract STEP 3 depends on and must always emit, so any history failure
        # (no prior snapshots, unwritable storage) degrades to omitting the
        # ``history`` section rather than failing the command.
        try:
            history_root = root / "extraction_history"
            total_flaky = (
                health.complete_extraction + health.partial_extraction + health.no_extraction
            )
            collector = ExtractionHistoryCollector(history_root)
            collector.collect_snapshot(
                success_rate=health.success_rate,
                complete_extraction=health.complete_extraction,
                partial_extraction=health.partial_extraction,
                no_extraction=health.no_extraction,
                total_flaky_tests=total_flaky,
                edge_case_summary=dict(health.edge_case_summary),
            )
            # Two complementary views over the same storage: the mixin on
            # TestSignalQuery (daily trend, regression slope, anomaly detection,
            # retention prune) and the standalone ExtractionHistoryQuery (a weekly
            # aggregation, total observation count, and the most-recent readings).
            hist_query = ExtractionHistoryQuery(collector.storage)
            daily_trend = query.get_extraction_health_trend(days=trend_days)
            weekly_trend = hist_query.get_success_rate_trend(days=trend_days, granularity="weekly")
            history = {
                "window_days": trend_days,
                "trend": daily_trend.to_dict() if daily_trend is not None else None,
                "weekly_trend": weekly_trend.to_dict(),
                "slope": query.get_extraction_trend_slope(days=trend_days),
                "anomalies": query.get_extraction_anomalies(days=trend_days),
                "observations": hist_query.get_success_rate_history(days=trend_days).total_count,
                "recent": [s.to_dict() for s in hist_query.get_recent_snapshots(count=5)],
                "snapshots_pruned": query.cleanup_old_extraction_history(),
            }
            # guard: only attach if fully JSON-serializable
            json.dumps(history, ensure_ascii=False)
            payload["history"] = history
        except Exception as e:  # noqa: BLE001 — history is supplementary, never fatal
            logger.debug("extraction history augmentation skipped: %s", e)

        if format_str == "json":
            # typer.echo (not the rich console) so piped/redirected JSON is not
            # soft-wrapped — the watchdog collector parses this from a file.
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:  # table
            console.print(
                f"extraction success_rate={payload['success_rate']:.1f}%  "
                f"complete={payload['complete_extraction']}  "
                f"partial={payload['partial_extraction']}  "
                f"none={payload['no_extraction']}"
            )
        raise typer.Exit(EXIT_SUCCESS)

    except typer.Exit:
        raise
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error querying extraction health: {e}[/red]")
        logger.exception("Error in extraction-health command")
        raise typer.Exit(EXIT_CONFIG_ERROR)


def main() -> None:
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
