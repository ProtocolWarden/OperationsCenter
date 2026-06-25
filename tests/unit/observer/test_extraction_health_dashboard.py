# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the extraction health trends dashboard.

Covers:
- ExtractionDashboardData serialisation
- _ascii_bar_chart sparkline helper
- ExtractionDashboardQuery.get_dashboard_data (empty / single / many snapshots)
- ExtractionHealthDashboardRenderer (all panels, conditional omission)
- CLI command extraction-health-dashboard (table and json modes, date-range filtering)
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from operations_center.observer.cli import app
from operations_center.observer.extraction_health_dashboard import (
    ExtractionDashboardData,
    ExtractionDashboardQuery,
    ExtractionHealthDashboardRenderer,
    _ascii_bar_chart,
)
from operations_center.observer.extraction_health_history import (
    ExtractionHealthSnapshot,
    ExtractionHealthTrend,
    ExtractionHistoryStorage,
)
from operations_center.observer.extraction_history_query import AnomalyResult

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_snapshot(
    hours_ago: float = 0,
    success_rate: float = 85.0,
    complete: int = 10,
    partial: int = 2,
    no_ext: int = 0,
    total: int = 12,
    edge_cases: dict[str, int] | None = None,
) -> ExtractionHealthSnapshot:
    return ExtractionHealthSnapshot(
        observed_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        success_rate=success_rate,
        complete_extraction=complete,
        partial_extraction=partial,
        no_extraction=no_ext,
        total_flaky_tests=total,
        edge_case_summary=edge_cases or {},
    )


def _make_trend(
    slope: float = 0.5,
    mean: float = 85.0,
    min_rate: float = 70.0,
    max_rate: float = 95.0,
    std_dev: float = 4.0,
    obs: int = 10,
    edge_trends: dict | None = None,
) -> ExtractionHealthTrend:
    now = datetime.now(UTC)
    return ExtractionHealthTrend(
        period_start=now - timedelta(days=30),
        period_end=now,
        granularity="daily",
        success_rate_mean=mean,
        success_rate_min=min_rate,
        success_rate_max=max_rate,
        success_rate_std_dev=std_dev,
        success_rate_trend=slope,
        complete_extraction_mean=10.0,
        partial_extraction_mean=2.0,
        no_extraction_mean=0.0,
        observation_count=obs,
        edge_case_trends=edge_trends or {},
    )


def _make_anomaly(hours_ago: float = 5.0, anomaly_type: str = "spike_down") -> AnomalyResult:
    return AnomalyResult(
        anomaly_type=anomaly_type,
        timestamp=datetime.now(UTC) - timedelta(hours=hours_ago),
        metric="success_rate",
        value=70.0,
        baseline=87.0,
        delta_pct=-17.0,
    )


def _make_dashboard_data(
    *,
    n_snapshots: int = 5,
    with_trend: bool = True,
    with_anomalies: bool = False,
    with_edge_cases: bool = False,
) -> ExtractionDashboardData:
    snapshots = [_make_snapshot(hours_ago=i) for i in range(n_snapshots - 1, -1, -1)]
    recent = snapshots[-3:] if snapshots else []
    trend = None
    if with_trend and len(snapshots) >= 2:
        edge = (
            {"truncated_messages": {"mean": 2.1, "min": 0.0, "max": 8.0}} if with_edge_cases else {}
        )
        trend = _make_trend(edge_trends=edge)
    anomalies = [_make_anomaly()] if with_anomalies else []
    return ExtractionDashboardData(
        generated_at=datetime.now(UTC),
        window_days=30,
        granularity="daily",
        current_snapshot=snapshots[-1] if snapshots else None,
        time_series=snapshots,
        trend=trend,
        anomalies=anomalies,
        recent_snapshots=recent,
    )


def _console_output(data: ExtractionDashboardData) -> str:
    """Render dashboard to a string buffer."""
    buf = StringIO()
    con = Console(file=buf, no_color=True, width=120)
    ExtractionHealthDashboardRenderer().render(data, con)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _ascii_bar_chart
# ---------------------------------------------------------------------------


class TestAsciiBarChart:
    def test_empty_returns_empty_string(self) -> None:
        assert _ascii_bar_chart([]) == ""

    def test_single_value_returns_one_char(self) -> None:
        result = _ascii_bar_chart([50.0])
        assert len(result) == 1

    def test_all_equal_returns_mid_char(self) -> None:
        # When all values are the same, mid-height block is used
        result = _ascii_bar_chart([80.0, 80.0, 80.0])
        assert len(result) == 3
        assert len(set(result)) == 1  # all the same character

    def test_ascending_uses_increasing_blocks(self) -> None:
        result = _ascii_bar_chart([0.0, 50.0, 100.0])
        assert result[0] <= result[1] <= result[2]

    def test_descending_uses_decreasing_blocks(self) -> None:
        result = _ascii_bar_chart([100.0, 50.0, 0.0])
        assert result[0] >= result[1] >= result[2]

    def test_min_maps_to_lowest_block(self) -> None:
        result = _ascii_bar_chart([0.0, 50.0, 100.0])
        assert result[0] == "▁"

    def test_max_maps_to_highest_block(self) -> None:
        result = _ascii_bar_chart([0.0, 50.0, 100.0])
        assert result[2] == "█"

    def test_width_truncates_oldest_values(self) -> None:
        values = list(range(20))
        result = _ascii_bar_chart(values, width=5)
        assert len(result) == 5

    def test_width_equal_to_length_uses_all(self) -> None:
        values = [10.0, 20.0, 30.0]
        result = _ascii_bar_chart(values, width=3)
        assert len(result) == 3

    def test_returns_only_block_chars(self) -> None:
        block_chars = set("▁▂▃▄▅▆▇█")
        result = _ascii_bar_chart([10.0, 50.0, 90.0, 30.0, 70.0])
        assert all(c in block_chars for c in result)


# ---------------------------------------------------------------------------
# ExtractionDashboardData
# ---------------------------------------------------------------------------


class TestExtractionDashboardData:
    def test_to_dict_keys_present(self) -> None:
        data = _make_dashboard_data()
        d = data.to_dict()
        for key in (
            "generated_at",
            "window_days",
            "granularity",
            "current_snapshot",
            "time_series_count",
            "trend",
            "anomalies",
            "recent_snapshots",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_time_series_count(self) -> None:
        data = _make_dashboard_data(n_snapshots=7)
        d = data.to_dict()
        assert d["time_series_count"] == 7

    def test_to_dict_json_serializable(self) -> None:
        data = _make_dashboard_data(with_anomalies=True, with_edge_cases=True)
        d = data.to_dict()
        serialized = json.dumps(d)
        assert json.loads(serialized) == d

    def test_to_dict_no_current_snapshot(self) -> None:
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=30,
            granularity="daily",
            current_snapshot=None,
        )
        d = data.to_dict()
        assert d["current_snapshot"] is None
        assert d["time_series_count"] == 0
        assert d["trend"] is None
        assert d["anomalies"] == []
        assert d["recent_snapshots"] == []

    def test_to_dict_with_anomalies(self) -> None:
        data = _make_dashboard_data(with_anomalies=True)
        d = data.to_dict()
        assert len(d["anomalies"]) == 1
        anomaly = d["anomalies"][0]
        assert "anomaly_type" in anomaly
        assert "timestamp" in anomaly
        assert "delta_pct" in anomaly

    def test_to_dict_with_trend(self) -> None:
        data = _make_dashboard_data(with_trend=True)
        d = data.to_dict()
        assert d["trend"] is not None
        assert "success_rate_mean" in d["trend"]

    def test_to_dict_generated_at_is_iso_string(self) -> None:
        data = _make_dashboard_data()
        d = data.to_dict()
        parsed = datetime.fromisoformat(d["generated_at"])
        assert isinstance(parsed, datetime)


# ---------------------------------------------------------------------------
# ExtractionDashboardQuery
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path

        storage = ExtractionHistoryStorage(Path(tmpdir))
        yield storage


class TestExtractionDashboardQuery:
    def test_empty_history_returns_no_current_snapshot(
        self, temp_storage: ExtractionHistoryStorage
    ) -> None:
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30)
        assert data.current_snapshot is None
        assert data.time_series == []
        assert data.trend is None
        assert data.anomalies == []

    def test_single_snapshot_no_trend(self, temp_storage: ExtractionHistoryStorage) -> None:
        snap = _make_snapshot(hours_ago=1)
        temp_storage.save_snapshot(snap)
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30)
        assert data.current_snapshot is not None
        assert len(data.time_series) == 1
        assert data.trend is None  # not enough points

    def test_two_snapshots_produces_trend(self, temp_storage: ExtractionHistoryStorage) -> None:
        for i in [2, 1]:
            temp_storage.save_snapshot(_make_snapshot(hours_ago=i))
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30)
        assert data.trend is not None
        assert data.trend.observation_count == 2

    def test_many_snapshots_time_series_oldest_first(
        self, temp_storage: ExtractionHistoryStorage
    ) -> None:
        for i in range(10, 0, -1):
            temp_storage.save_snapshot(_make_snapshot(hours_ago=i))
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30)
        timestamps = [s.observed_at for s in data.time_series]
        assert timestamps == sorted(timestamps)

    def test_recent_count_respected(self, temp_storage: ExtractionHistoryStorage) -> None:
        for i in range(20, 0, -1):
            temp_storage.save_snapshot(_make_snapshot(hours_ago=i))
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30, recent_count=5)
        assert len(data.recent_snapshots) == 5

    def test_days_filter_excludes_old_snapshots(
        self, temp_storage: ExtractionHistoryStorage
    ) -> None:
        # Save one old (40 days) and one recent (1 day) snapshot
        old = _make_snapshot(hours_ago=40 * 24)
        recent = _make_snapshot(hours_ago=24)
        temp_storage.save_snapshot(old)
        temp_storage.save_snapshot(recent)
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30)
        # Only the recent one should be in the time series
        assert len(data.time_series) == 1
        assert abs((data.time_series[0].observed_at - recent.observed_at).total_seconds()) < 5

    def test_granularity_passed_to_trend(self, temp_storage: ExtractionHistoryStorage) -> None:
        for i in range(5, 0, -1):
            temp_storage.save_snapshot(_make_snapshot(hours_ago=i))
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=30, granularity="weekly")
        assert data.granularity == "weekly"
        if data.trend:
            assert data.trend.granularity == "weekly"

    def test_generated_at_is_utc_aware(self, temp_storage: ExtractionHistoryStorage) -> None:
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data()
        assert data.generated_at.tzinfo is not None

    def test_window_days_recorded(self, temp_storage: ExtractionHistoryStorage) -> None:
        query = ExtractionDashboardQuery(temp_storage)
        data = query.get_dashboard_data(days=7)
        assert data.window_days == 7


# ---------------------------------------------------------------------------
# ExtractionHealthDashboardRenderer
# ---------------------------------------------------------------------------


class TestExtractionHealthDashboardRenderer:
    def test_header_always_rendered(self) -> None:
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=30,
            granularity="daily",
            current_snapshot=None,
        )
        output = _console_output(data)
        assert "Extraction Health Dashboard" in output

    def test_header_contains_window_and_granularity(self) -> None:
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=14,
            granularity="weekly",
            current_snapshot=None,
        )
        output = _console_output(data)
        assert "14d" in output
        assert "weekly" in output

    def test_current_status_shown_when_snapshot_present(self) -> None:
        data = _make_dashboard_data(n_snapshots=1, with_trend=False)
        output = _console_output(data)
        assert "Current Status" in output
        assert "85.0%" in output

    def test_current_status_omitted_when_no_snapshot(self) -> None:
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=30,
            granularity="daily",
            current_snapshot=None,
        )
        output = _console_output(data)
        assert "Current Status" not in output

    def test_sparkline_shown_with_two_or_more_points(self) -> None:
        data = _make_dashboard_data(n_snapshots=3)
        output = _console_output(data)
        assert "Success Rate" in output
        # Should contain block chars
        assert any(c in output for c in "▁▂▃▄▅▆▇█")

    def test_sparkline_omitted_with_single_point(self) -> None:
        data = _make_dashboard_data(n_snapshots=1, with_trend=False)
        output = _console_output(data)
        # Should not show "Last N Days" chart title
        assert "Last 30 Days" not in output

    def test_trend_summary_shown_when_trend_present(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_trend=True)
        output = _console_output(data)
        assert "Trend Summary" in output
        assert "Mean" in output
        assert "Slope" in output

    def test_trend_summary_omitted_when_no_trend(self) -> None:
        data = _make_dashboard_data(n_snapshots=1, with_trend=False)
        output = _console_output(data)
        assert "Trend Summary" not in output

    def test_extraction_breakdown_shown_when_recent_present(self) -> None:
        data = _make_dashboard_data(n_snapshots=5)
        output = _console_output(data)
        assert "Extraction Breakdown" in output

    def test_extraction_breakdown_omitted_when_no_recent(self) -> None:
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=30,
            granularity="daily",
            current_snapshot=None,
        )
        output = _console_output(data)
        assert "Extraction Breakdown" not in output

    def test_edge_case_trends_shown_when_present(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_edge_cases=True)
        output = _console_output(data)
        assert "Edge Case Trends" in output
        assert "truncated_messages" in output

    def test_edge_case_trends_omitted_when_empty(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_trend=True, with_edge_cases=False)
        output = _console_output(data)
        assert "Edge Case Trends" not in output

    def test_anomalies_shown_when_present(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_anomalies=True)
        output = _console_output(data)
        assert "Anomalies" in output
        assert "spike_down" in output

    def test_anomalies_omitted_when_none(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_anomalies=False)
        output = _console_output(data)
        assert "Anomalies" not in output

    def test_improving_slope_shown_in_status(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_trend=True)
        # Set a clearly positive slope
        assert data.trend is not None
        data.trend.success_rate_trend = 1.5
        output = _console_output(data)
        assert "+1.50%/day" in output

    def test_degrading_slope_shown_in_status(self) -> None:
        data = _make_dashboard_data(n_snapshots=5, with_trend=True)
        assert data.trend is not None
        data.trend.success_rate_trend = -2.3
        output = _console_output(data)
        assert "-2.30%/day" in output

    def test_high_success_rate_present(self) -> None:
        snap = _make_snapshot(success_rate=95.0)
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=30,
            granularity="daily",
            current_snapshot=snap,
            recent_snapshots=[snap],
        )
        output = _console_output(data)
        assert "95.0%" in output

    def test_empty_data_renders_only_header(self) -> None:
        data = ExtractionDashboardData(
            generated_at=datetime.now(UTC),
            window_days=30,
            granularity="daily",
            current_snapshot=None,
        )
        output = _console_output(data)
        assert "Extraction Health Dashboard" in output
        assert "Current Status" not in output
        assert "Trend Summary" not in output
        assert "Anomalies" not in output

    def test_renders_without_raising(self) -> None:
        for n in [0, 1, 2, 5, 20]:
            data = _make_dashboard_data(
                n_snapshots=n,
                with_trend=(n >= 2),
                with_anomalies=(n >= 5),
                with_edge_cases=(n >= 2),
            )
            output = _console_output(data)
            assert "Extraction Health Dashboard" in output


# ---------------------------------------------------------------------------
# CLI command: extraction-health-dashboard
# ---------------------------------------------------------------------------


class TestCLIExtractionHealthDashboard:
    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["extraction-health-dashboard", "--help"])
        assert result.exit_code == 0
        assert "extraction health" in result.stdout.lower()

    def test_missing_storage_root_exits_not_found(self) -> None:
        with patch("pathlib.Path.exists", return_value=False):
            result = runner.invoke(
                app,
                ["extraction-health-dashboard", "--storage-root", "/nonexistent/path"],
            )
        assert result.exit_code == 2  # EXIT_NOT_FOUND

    def test_table_mode_renders_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            history_path = storage_path / "extraction_history"
            history_path.mkdir()
            result = runner.invoke(
                app,
                ["extraction-health-dashboard", "--storage-root", str(storage_path)],
            )
        assert result.exit_code == 0
        assert "Extraction Health Dashboard" in result.stdout

    def test_json_mode_returns_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            history_path = storage_path / "extraction_history"
            history_path.mkdir()
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "generated_at" in payload
        assert "window_days" in payload
        assert "granularity" in payload

    def test_json_mode_default_window_30_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            (storage_path / "extraction_history").mkdir()
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["window_days"] == 30

    def test_json_mode_custom_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            (storage_path / "extraction_history").mkdir()
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--days",
                    "7",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["window_days"] == 7

    def test_json_mode_granularity_reflected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            (storage_path / "extraction_history").mkdir()
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--granularity",
                    "weekly",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["granularity"] == "weekly"

    def test_json_mode_with_snapshot_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            storage = ExtractionHistoryStorage(storage_path / "extraction_history")
            storage.save_snapshot(_make_snapshot(hours_ago=1))
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["time_series_count"] == 1
        assert payload["current_snapshot"] is not None
        assert payload["current_snapshot"]["success_rate"] == 85.0

    def test_table_mode_with_snapshot_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            storage = ExtractionHistoryStorage(storage_path / "extraction_history")
            for i in range(5, 0, -1):
                storage.save_snapshot(_make_snapshot(hours_ago=i))
            result = runner.invoke(
                app,
                ["extraction-health-dashboard", "--storage-root", str(storage_path)],
            )
        assert result.exit_code == 0
        assert "Current Status" in result.stdout
        assert "85.0%" in result.stdout

    def test_recent_flag_controls_breakdown_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            storage = ExtractionHistoryStorage(storage_path / "extraction_history")
            for i in range(20, 0, -1):
                storage.save_snapshot(_make_snapshot(hours_ago=i))
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--recent",
                    "3",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["recent_snapshots"]) == 3

    def test_anomaly_threshold_flag_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            (storage_path / "extraction_history").mkdir()
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--anomaly-threshold",
                    "10.0",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0

    def test_json_empty_no_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            storage_path = Path(tmpdir)
            (storage_path / "extraction_history").mkdir()
            result = runner.invoke(
                app,
                [
                    "extraction-health-dashboard",
                    "--format",
                    "json",
                    "--storage-root",
                    str(storage_path),
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["anomalies"] == []
