# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the observer `extraction-health` CLI command.

This command is what `haiku_collector_prompt.md` STEP 3 calls. It exposes the
aggregate ExtractionHealth from get_extraction_health() — the integration that
#313 claimed but never wired (STEP 3 had parsed the wrong command's output).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from operations_center.observer.cli import app
from operations_center.observer.query_flaky import ExtractionHealth

runner = CliRunner()


def _query_returning(health: ExtractionHealth) -> MagicMock:
    q = MagicMock()
    q.get_extraction_health.return_value = health
    return q


class TestExtractionHealthCommand:
    def test_command_help(self) -> None:
        result = runner.invoke(app, ["extraction-health", "--help"])
        assert result.exit_code == 0
        assert "extraction coverage health" in result.stdout.lower()

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_no_snapshots_found(self, mock_cls: MagicMock) -> None:
        with patch("pathlib.Path.exists", return_value=False):
            result = runner.invoke(app, ["extraction-health"])
        assert result.exit_code == 2  # EXIT_NOT_FOUND
        assert "does not exist" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_output_is_the_extraction_health_shape(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        # The regression: STEP 3 must receive the aggregate ExtractionHealth
        # fields, NOT a test_failures array (which query-flaky-tests emits).
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                success_rate=80.0,
                complete_extraction=4,
                partial_extraction=0,
                no_extraction=1,
                edge_case_summary={"truncated_messages": 2, "special_chars": 0},
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Exactly the keys STEP 3's python parser reads:
        assert payload["success_rate"] == 80.0
        assert payload["complete_extraction"] == 4
        assert payload["partial_extraction"] == 0
        assert payload["no_extraction"] == 1
        assert payload["edge_case_summary"]["truncated_messages"] == 2
        assert "test_failures" not in payload  # NOT the query-flaky-tests shape

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_step3_parser_maps_the_output(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        # Exercise STEP 3's exact mapping over this command's output, proving the
        # end-to-end contract: success_rate/extracted/total/gap all resolve.
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                success_rate=75.0,
                complete_extraction=3,
                partial_extraction=0,
                no_extraction=1,
                edge_case_summary={"truncated_messages": 1, "special_chars": 0},
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        h = json.loads(result.stdout)
        complete, partial, missing = (
            int(h["complete_extraction"]),
            int(h["partial_extraction"]),
            int(h["no_extraction"]),
        )
        total = complete + partial + missing
        mapped = {
            "success_rate": round(float(h["success_rate"]), 1),
            "extracted_count": complete + partial,
            "total_count": total,
            "gap_count": missing,
            "edge_case_count": sum(int(v) for v in h["edge_case_summary"].values()),
        }
        assert mapped == {
            "success_rate": 75.0,
            "extracted_count": 3,
            "total_count": 4,
            "gap_count": 1,
            "edge_case_count": 1,
        }

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_format(self, mock_cls: MagicMock, _mock_collector: MagicMock) -> None:
        mock_cls.return_value = _query_returning(ExtractionHealth(success_rate=50.0))
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "Extraction Health" in result.stdout
        assert "success_rate" in result.stdout
        assert "50.0 %" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_error_handling(self, mock_cls: MagicMock) -> None:
        q = MagicMock()
        q.get_extraction_health.side_effect = RuntimeError("boom")
        mock_cls.return_value = q
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health"])
        assert result.exit_code == 4  # EXIT_CONFIG_ERROR
        assert "Error querying extraction health" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_empty_health_when_no_flaky_tests(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        mock_cls.return_value = _query_returning(ExtractionHealth())  # all defaults
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["success_rate"] == 0.0
        assert payload["no_extraction"] == 0

    def test_records_history_and_attaches_trend(self, tmp_path) -> None:
        # End-to-end through the REAL TestSignalQuery + ExtractionHistoryCollector
        # (no mocks): proves the command actually wires collect_snapshot + the
        # trend-query API into production — each run appends to the time series
        # and surfaces the longitudinal ``history`` section.
        root = tmp_path / "obs"
        root.mkdir()
        result = None
        for _ in range(2):  # >=2 snapshots → trend is computable
            result = runner.invoke(
                app,
                ["extraction-health", "--storage-root", str(root), "--format", "json"],
            )
            assert result.exit_code == 0

        # The collector wrote the extraction-history time series...
        hist_file = root / "extraction_history" / "extraction_health_history.jsonl"
        assert hist_file.exists()
        assert len(hist_file.read_text(encoding="utf-8").strip().splitlines()) >= 2

        # ...and the command surfaced the wired trend/slope/anomaly section,
        # covering both the mixin (trend/slope/anomalies) and the standalone
        # ExtractionHistoryQuery (weekly_trend/observations/recent).
        payload = json.loads(result.stdout)
        h = payload["history"]
        assert h["window_days"] == 7
        assert h["trend"] is not None  # >=2 snapshots → daily trend computable
        assert h["weekly_trend"]["granularity"] == "weekly"
        assert "slope" in h
        assert isinstance(h["anomalies"], list)
        assert h["observations"] >= 2
        assert isinstance(h["recent"], list) and len(h["recent"]) >= 2

    def test_jsonl_entry_uses_observed_at_not_recorded_at(self, tmp_path) -> None:
        # Verifies the JSONL time-series field name documented in diagnostics.md
        # command 6: the key is ``observed_at``, not ``recorded_at``.
        root = tmp_path / "obs"
        root.mkdir()
        runner.invoke(
            app,
            ["extraction-health", "--storage-root", str(root), "--format", "json"],
        )
        hist_file = root / "extraction_history" / "extraction_health_history.jsonl"
        assert hist_file.exists()
        entry = json.loads(hist_file.read_text(encoding="utf-8").splitlines()[0])
        assert "observed_at" in entry, "JSONL key should be 'observed_at', not 'recorded_at'"
        assert "recorded_at" not in entry

    def test_table_format_multiline_header(self, tmp_path) -> None:
        # Verifies the table format documented in diagnostics.md command 2:
        # multi-line output starting with "Extraction Health".
        root = tmp_path / "obs"
        root.mkdir()
        result = runner.invoke(
            app,
            ["extraction-health", "--storage-root", str(root), "--format", "table"],
        )
        assert result.exit_code == 0
        lines = result.stdout.strip().splitlines()
        assert lines[0] == "Extraction Health"
        assert any("success_rate" in ln for ln in lines)
        assert any("complete_extraction" in ln for ln in lines)
        assert any("partial_extraction" in ln for ln in lines)
        assert any("no_extraction" in ln for ln in lines)

    def test_anomaly_structure_uses_timestamp_not_recorded_at(self, tmp_path) -> None:
        # Verifies diagnostics.md command 3 / Step 2: anomaly dicts use "timestamp",
        # not "recorded_at" or "observed_at".  Requires >=10 snapshots to trigger
        # anomaly detection (window_size * 2 = 10).
        root = tmp_path / "obs"
        root.mkdir()
        # Write 10 snapshots with a sharp drop at position 7 to trigger spike_down.
        from operations_center.observer.extraction_health_history import (
            ExtractionHistoryStorage,
            ExtractionHealthSnapshot,
        )
        from datetime import datetime, timezone

        history_root = root / "extraction_history"
        history_root.mkdir(parents=True, exist_ok=True)
        storage = ExtractionHistoryStorage(base_path=history_root)
        rates = [90.0] * 5 + [91.0] * 2 + [60.0] + [90.0] * 2  # drop at index 7
        for i, rate in enumerate(rates):
            ts = datetime(2026, 6, 10 + i, 14, 0, 0, tzinfo=timezone.utc)
            snap = ExtractionHealthSnapshot(
                observed_at=ts,
                success_rate=rate,
                complete_extraction=int(rate),
                partial_extraction=0,
                no_extraction=100 - int(rate),
                total_flaky_tests=100,
                extracted_count=int(rate),
            )
            storage.save_snapshot(snap)

        result = runner.invoke(
            app,
            ["extraction-health", "--storage-root", str(root), "--format", "json", "--trend-days", "30"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        anomalies = payload["history"]["anomalies"]
        assert len(anomalies) >= 1, "expected at least one anomaly from the sharp drop"
        a = anomalies[0]
        assert "timestamp" in a, f"anomaly key should be 'timestamp', got: {list(a.keys())}"
        assert "recorded_at" not in a
        assert "observed_at" not in a
        assert "type" in a
        assert "metric" in a
        assert "delta_pct" in a
        assert "current_value" in a

    def test_alert_log_format_includes_float_threshold(self) -> None:
        # Verifies diagnostics.md command 7 example: threshold appears as 80.0
        # (float), not 80 (int), because FlakyTestAlertConfig returns a float.
        from operations_center.observer.alert_channels import OperatorLogChannel
        import logging

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture()
        logger = logging.getLogger("test_alert_format")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        channel = OperatorLogChannel(logger_name="test_alert_format")
        channel.notify(
            {
                "severity": "WARNING",
                "condition_name": "EXTRACTION_SUCCESS_RATE_LOW",
                "error_count": 74,
                "threshold": 80.0,
                "collector_name": "extraction-health",
                "time_window_minutes": 1440,
            }
        )
        assert records, "expected one log record"
        msg = records[0].getMessage()
        assert "74/80.0 errors" in msg, f"expected '74/80.0 errors' in: {msg!r}"
        assert "74/80 errors" not in msg.replace("74/80.0", "")
