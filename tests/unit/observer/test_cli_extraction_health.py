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

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_output_is_the_extraction_health_shape(self, mock_cls: MagicMock) -> None:
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

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_step3_parser_maps_the_output(self, mock_cls: MagicMock) -> None:
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

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_format(self, mock_cls: MagicMock) -> None:
        mock_cls.return_value = _query_returning(ExtractionHealth(success_rate=50.0))
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "success_rate=50.0%" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_error_handling(self, mock_cls: MagicMock) -> None:
        q = MagicMock()
        q.get_extraction_health.side_effect = RuntimeError("boom")
        mock_cls.return_value = q
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health"])
        assert result.exit_code == 4  # EXIT_CONFIG_ERROR
        assert "Error querying extraction health" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_empty_health_when_no_flaky_tests(self, mock_cls: MagicMock) -> None:
        mock_cls.return_value = _query_returning(ExtractionHealth())  # all defaults
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["success_rate"] == 0.0
        assert payload["no_extraction"] == 0
