# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Extraction health trends dashboard — data model, query, and Rich terminal renderer.

Aggregates historical ExtractionHealthSnapshot data into ExtractionDashboardData and
renders it as a Rich terminal display (table mode) or JSON (json mode).

Usage:
    from pathlib import Path
    from operations_center.observer.extraction_health_dashboard import (
        ExtractionDashboardQuery,
        ExtractionHealthDashboardRenderer,
    )
    from operations_center.observer.extraction_health_history import ExtractionHistoryStorage
    from rich.console import Console

    storage = ExtractionHistoryStorage(storage_root / "extraction_history")
    query = ExtractionDashboardQuery(storage)
    data = query.get_dashboard_data(days=30, granularity="daily")

    # Table mode
    renderer = ExtractionHealthDashboardRenderer()
    renderer.render(data, Console())

    # JSON mode
    import json
    print(json.dumps(data.to_dict(), indent=2))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

    from operations_center.observer.extraction_health_history import (
        ExtractionHealthSnapshot,
        ExtractionHealthTrend,
        ExtractionHistoryStorage,
    )
    from operations_center.observer.extraction_history_query import AnomalyResult

logger = logging.getLogger(__name__)

# Unicode block characters for sparkline, ordered lowest to highest fill
_BLOCK_CHARS = "▁▂▃▄▅▆▇█"


def _ascii_bar_chart(values: list[float], width: int = 60) -> str:
    """Render a single-row sparkline using Unicode block characters.

    Maps float values to block chars ▁▂▃▄▅▆▇█. Values are scaled to the
    observed min/max range. When all values are identical, every bar renders
    at mid-height (▄).

    Args:
        values: Sequence of floats to chart (e.g. success_rate 0–100).
        width: Maximum characters to output; oldest values are dropped when
               len(values) > width.

    Returns:
        Single-row string of block characters, empty if values is empty.
    """
    if not values:
        return ""

    if len(values) > width:
        values = values[-width:]

    min_val = min(values)
    max_val = max(values)
    span = max_val - min_val
    n_levels = len(_BLOCK_CHARS)

    chars: list[str] = []
    for v in values:
        if span == 0:
            idx = n_levels // 2
        else:
            idx = round((v - min_val) / span * (n_levels - 1))
        chars.append(_BLOCK_CHARS[idx])

    return "".join(chars)


@dataclass
class ExtractionDashboardData:
    """All data consumed by the dashboard renderer, assembled in one query call.

    Attributes:
        generated_at: When the dashboard data was assembled.
        window_days: How many days of history the data covers.
        granularity: Trend aggregation level ("hourly"|"daily"|"weekly"|"monthly").
        current_snapshot: Most recent point-in-time reading (None if no history).
        time_series: All snapshots in window, oldest-first (for sparkline).
        trend: Period-level aggregation (None if < 2 snapshots).
        anomalies: Detected spikes/drops in the window.
        recent_snapshots: Last N snapshots for the detail breakdown table.
    """

    generated_at: datetime
    window_days: int
    granularity: str
    current_snapshot: ExtractionHealthSnapshot | None
    time_series: list[ExtractionHealthSnapshot] = field(default_factory=list)
    trend: ExtractionHealthTrend | None = None
    anomalies: list[AnomalyResult] = field(default_factory=list)
    recent_snapshots: list[ExtractionHealthSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "window_days": self.window_days,
            "granularity": self.granularity,
            "current_snapshot": (
                self.current_snapshot.to_dict() if self.current_snapshot is not None else None
            ),
            "time_series_count": len(self.time_series),
            "trend": self.trend.to_dict() if self.trend is not None else None,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "recent_snapshots": [s.to_dict() for s in self.recent_snapshots],
        }


class ExtractionDashboardQuery:
    """Assembles all data needed to render the extraction health dashboard.

    Wraps ExtractionHistoryQuery and combines time_series, trend, anomalies,
    and recent_snapshots into a single ExtractionDashboardData in one call.
    """

    def __init__(self, storage: ExtractionHistoryStorage) -> None:
        """Initialize with an ExtractionHistoryStorage instance."""
        from operations_center.observer.extraction_history_query import ExtractionHistoryQuery

        self._storage = storage
        self._hist_query = ExtractionHistoryQuery(storage)

    def get_dashboard_data(
        self,
        days: int = 30,
        granularity: str = "daily",
        recent_count: int = 10,
        anomaly_threshold_pct: float = 5.0,
    ) -> ExtractionDashboardData:
        """Fetch all dashboard data in a single call.

        Args:
            days: Window size in days for history and trend.
            granularity: Bucket size — "hourly", "daily", "weekly", "monthly".
            recent_count: Rows to include in the breakdown table.
            anomaly_threshold_pct: Minimum % change to flag as an anomaly.

        Returns:
            ExtractionDashboardData with all panels pre-loaded.
        """
        history_page = self._hist_query.get_success_rate_history(days=days, limit=10_000)
        time_series = history_page.snapshots

        current_snapshot = time_series[-1] if time_series else None

        trend = None
        if len(time_series) >= 2:
            trend = self._hist_query.get_success_rate_trend(days=days, granularity=granularity)

        anomalies = self._hist_query.detect_anomalies(
            days=days, threshold_pct=anomaly_threshold_pct
        )

        recent_snapshots = self._hist_query.get_recent_snapshots(count=recent_count)

        return ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=days,
            granularity=granularity,
            current_snapshot=current_snapshot,
            time_series=time_series,
            trend=trend,
            anomalies=anomalies,
            recent_snapshots=recent_snapshots,
        )


class ExtractionHealthDashboardRenderer:
    """Renders ExtractionDashboardData as a Rich terminal display.

    Each panel is conditional: omitted when its required data is absent or
    empty, keeping output clean for new installs with no history.
    """

    def render(self, data: ExtractionDashboardData, console: Console) -> None:
        """Render all dashboard panels to the console.

        Args:
            data: Aggregated dashboard data.
            console: Rich Console to write output to.
        """
        self._render_header(data, console)

        if data.current_snapshot is not None:
            self._render_current_status(data, console)

        if len(data.time_series) >= 2:
            self._render_success_rate_chart(data, console)

        if data.trend is not None:
            self._render_trend_summary(data, console)

        if data.recent_snapshots:
            self._render_extraction_breakdown(data, console)

        if data.trend is not None and data.trend.edge_case_trends:
            self._render_edge_case_trends(data, console)

        if data.anomalies:
            self._render_anomalies(data, console)

    def _render_header(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel
        from rich.text import Text

        generated = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
        text = Text()
        text.append(f"Window: {data.window_days}d", style="bold")
        text.append("  │  ", style="dim")
        text.append(f"Granularity: {data.granularity}")
        text.append("  │  ", style="dim")
        text.append(generated, style="dim")
        console.print(Panel(text, title="Extraction Health Dashboard", border_style="blue"))

    def _render_current_status(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel
        from rich.table import Table

        snap = data.current_snapshot
        if snap is None:
            return

        rate = snap.success_rate
        if rate >= 90:
            rate_markup = f"[green]{rate:.1f}%[/green]"
        elif rate >= 70:
            rate_markup = f"[yellow]{rate:.1f}%[/yellow]"
        else:
            rate_markup = f"[red]{rate:.1f}%[/red]"

        slope_markup = ""
        if data.trend is not None:
            s = data.trend.success_rate_trend
            if s > 0.05:
                slope_markup = f"  [green]▲ +{s:.2f}%/day[/green]"
            elif s < -0.05:
                slope_markup = f"  [red]▼ {s:.2f}%/day[/red]"
            else:
                slope_markup = "  [dim]→ stable[/dim]"

        table = Table.grid(padding=(0, 2))
        table.add_column("label", style="bold")
        table.add_column("value")
        table.add_row("Success Rate", f"{rate_markup}{slope_markup}")
        table.add_row(
            "Breakdown",
            f"Complete [bold]{snap.complete_extraction}[/bold]  "
            f"Partial [bold]{snap.partial_extraction}[/bold]  "
            f"None [bold]{snap.no_extraction}[/bold]  "
            f"Total [bold]{snap.total_flaky_tests}[/bold]",
        )
        console.print(Panel(table, title="Current Status", border_style="green"))

    def _render_success_rate_chart(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel

        values = [s.success_rate for s in data.time_series]
        chart = _ascii_bar_chart(values, width=70)

        first_ts = data.time_series[0].observed_at.strftime("%b %d")
        last_ts = data.time_series[-1].observed_at.strftime("%b %d")
        pad = max(1, len(chart) - len(first_ts) - len(last_ts))
        axis_line = f"{first_ts}{' ' * pad}{last_ts}"

        min_v = min(values)
        max_v = max(values)
        lines = [
            f"[dim]{max_v:5.1f} ┤[/dim] {chart}",
            f"[dim]{min_v:5.1f} ┤[/dim]",
            f"[dim]       {'─' * len(chart)}[/dim]",
            f"[dim]       {axis_line}[/dim]",
        ]
        console.print(
            Panel(
                "\n".join(lines),
                title=f"Success Rate — Last {data.window_days} Days",
                border_style="cyan",
            )
        )

    def _render_trend_summary(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel
        from rich.table import Table

        trend = data.trend
        if trend is None:
            return

        s = trend.success_rate_trend
        if s > 0.05:
            slope_markup = f"[green]+{s:.3f}%/day (improving)[/green]"
        elif s < -0.05:
            slope_markup = f"[red]{s:.3f}%/day (degrading)[/red]"
        else:
            slope_markup = f"[dim]{s:.3f}%/day (stable)[/dim]"

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row("Mean", f"{trend.success_rate_mean:.1f}%")
        table.add_row("Min", f"{trend.success_rate_min:.1f}%")
        table.add_row("Max", f"{trend.success_rate_max:.1f}%")
        table.add_row("Std Dev", f"{trend.success_rate_std_dev:.2f}%")
        table.add_row("Slope", slope_markup)
        table.add_row("Observations", str(trend.observation_count))

        console.print(Panel(table, title="Trend Summary", border_style="cyan"))

    def _render_extraction_breakdown(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel
        from rich.table import Table

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("Timestamp")
        table.add_column("Success%", justify="right")
        table.add_column("Complete", justify="right")
        table.add_column("Partial", justify="right")
        table.add_column("None", justify="right")
        table.add_column("Total", justify="right")

        for snap in data.recent_snapshots:
            table.add_row(
                snap.observed_at.strftime("%Y-%m-%d %H:%M"),
                f"{snap.success_rate:.1f}%",
                str(snap.complete_extraction),
                str(snap.partial_extraction),
                str(snap.no_extraction),
                str(snap.total_flaky_tests),
            )

        console.print(
            Panel(
                table,
                title=f"Extraction Breakdown — Last {len(data.recent_snapshots)} Snapshots",
                border_style="blue",
            )
        )

    def _render_edge_case_trends(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel
        from rich.table import Table

        trend = data.trend
        if trend is None:
            return

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Issue Type")
        table.add_column("Mean", justify="right")
        table.add_column("Min", justify="right")
        table.add_column("Max", justify="right")

        for issue_type, stats in sorted(trend.edge_case_trends.items()):
            table.add_row(
                issue_type,
                f"{stats.get('mean', 0.0):.1f}",
                f"{stats.get('min', 0.0):.1f}",
                f"{stats.get('max', 0.0):.1f}",
            )

        console.print(Panel(table, title="Edge Case Trends", border_style="yellow"))

    def _render_anomalies(self, data: ExtractionDashboardData, console: Console) -> None:
        from rich.panel import Panel
        from rich.table import Table

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("Timestamp")
        table.add_column("Type")
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_column("Baseline", justify="right")
        table.add_column("Δ%", justify="right")

        for anomaly in data.anomalies:
            delta = anomaly.delta_pct
            delta_markup = (
                f"[red]{delta:+.1f}%[/red]" if delta < 0 else f"[green]{delta:+.1f}%[/green]"
            )
            table.add_row(
                anomaly.timestamp.strftime("%Y-%m-%d %H:%M"),
                anomaly.anomaly_type,
                anomaly.metric,
                f"{anomaly.value:.1f}%",
                f"{anomaly.baseline:.1f}%",
                delta_markup,
            )

        console.print(
            Panel(
                table,
                title=f"Anomalies ({len(data.anomalies)} detected)",
                border_style="red",
            )
        )
