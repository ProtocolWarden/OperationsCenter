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

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_output_includes_gaps_key(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON output includes a gaps key containing a list of test ID strings."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                success_rate=80.0,
                no_extraction=1,
                gaps=["test_module::test_missing_both"],
                edge_cases=[],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "gaps" in payload
        assert isinstance(payload["gaps"], list)
        assert "test_module::test_missing_both" in payload["gaps"]

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_output_includes_edge_cases_key(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON output includes an edge_cases key containing a list of dicts."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                success_rate=80.0,
                edge_cases=[{"test_id": "test_module::test_foo", "issue": "truncated_message"}],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "edge_cases" in payload
        assert isinstance(payload["edge_cases"], list)
        assert payload["edge_cases"][0]["test_id"] == "test_module::test_foo"
        assert payload["edge_cases"][0]["issue"] == "truncated_message"

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_format_shows_gaps_section_when_non_empty(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format shows gaps sample lines when gaps list is non-empty."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                success_rate=80.0,
                no_extraction=1,
                gaps=["test_module::test_missing_both"],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "gaps" in result.stdout
        assert "test_module::test_missing_both" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_format_shows_edge_cases_section_when_non_empty(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format shows edge_cases sample lines when edge_cases list is non-empty."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                success_rate=80.0,
                edge_cases=[{"test_id": "test_module::test_foo", "issue": "truncated_message"}],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "edge_cases" in result.stdout
        assert "test_module::test_foo" in result.stdout
        assert "truncated_message" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_format_omits_sample_sections_when_both_empty(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format omits gap/edge_case sample sections when both lists are empty."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(success_rate=100.0, gaps=[], edge_cases=[])
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "gaps (" not in result.stdout
        assert "edge_cases (" not in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_gaps_is_list_of_strings_not_objects(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON gaps contains string values, not dicts or other types."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(gaps=["module::test_a", "module::test_b"])
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        for item in payload["gaps"]:
            assert isinstance(item, str)

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_gaps_is_empty_list_not_null_when_no_gaps(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON gaps is [] (not null or absent) when no gaps exist."""
        mock_cls.return_value = _query_returning(ExtractionHealth(gaps=[]))
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "gaps" in payload
        assert payload["gaps"] == []

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_multiple_gaps_all_present(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON gaps preserves all gap test IDs up to the cap."""
        gaps = [f"module::test_missing_{i}" for i in range(5)]
        mock_cls.return_value = _query_returning(ExtractionHealth(gaps=gaps))
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["gaps"]) == 5
        for gap in gaps:
            assert gap in payload["gaps"]

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_edge_cases_is_empty_list_not_null_when_no_edge_cases(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON edge_cases is [] (not null or absent) when no edge cases exist."""
        mock_cls.return_value = _query_returning(ExtractionHealth(edge_cases=[]))
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "edge_cases" in payload
        assert payload["edge_cases"] == []

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_edge_cases_items_have_exactly_test_id_and_issue(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Every edge_cases entry in JSON has exactly test_id and issue keys."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                edge_cases=[
                    {"test_id": "module::test_a", "issue": "truncated_message"},
                    {"test_id": "module::test_b", "issue": "special_chars"},
                ]
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        for entry in payload["edge_cases"]:
            assert set(entry.keys()) == {"test_id", "issue"}

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_edge_cases_issue_value_is_truncated_message_singular(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON edge_cases issue value for long assertions is 'truncated_message' (singular)."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                edge_cases=[{"test_id": "module::test_long", "issue": "truncated_message"}]
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["edge_cases"][0]["issue"] == "truncated_message"

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_json_multiple_edge_cases_all_present(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """JSON edge_cases preserves all entries with test_id and issue intact."""
        edge_cases = [
            {"test_id": f"module::test_{i}", "issue": "truncated_message"} for i in range(3)
        ]
        mock_cls.return_value = _query_returning(ExtractionHealth(edge_cases=edge_cases))
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["edge_cases"]) == 3
        assert payload["edge_cases"][0]["test_id"] == "module::test_0"
        assert payload["edge_cases"][2]["test_id"] == "module::test_2"

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_hides_edge_cases_section_when_only_gaps_present(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format shows gaps section but omits edge_cases section when edge_cases is empty."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                no_extraction=1,
                gaps=["module::test_gap"],
                edge_cases=[],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "gaps" in result.stdout
        assert "edge_cases" not in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_hides_gaps_section_when_only_edge_cases_present(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format shows edge_cases section but omits gaps section when gaps is empty."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                gaps=[],
                edge_cases=[{"test_id": "module::test_foo", "issue": "truncated_message"}],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "edge_cases" in result.stdout
        assert "gaps (" not in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_shows_multiple_gaps_test_ids(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format lists all gap test IDs under the gaps section."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                no_extraction=2,
                gaps=["module::test_alpha", "module::test_beta"],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "module::test_alpha" in result.stdout
        assert "module::test_beta" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_shows_multiple_edge_cases_with_test_id_and_issue(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table format shows all edge_cases entries with their test_id and issue."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                edge_cases=[
                    {"test_id": "module::test_long", "issue": "truncated_message"},
                    {"test_id": "module::test_unicode", "issue": "special_chars"},
                ]
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "module::test_long" in result.stdout
        assert "module::test_unicode" in result.stdout
        assert "truncated_message" in result.stdout
        assert "special_chars" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_gaps_header_includes_no_extraction_count(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table gaps header mentions no_extraction count so operator sees full scope."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                no_extraction=7,
                gaps=["module::test_a", "module::test_b"],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        assert "7" in result.stdout

    @patch("operations_center.observer.cli.ExtractionHistoryCollector")
    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_table_edge_cases_header_includes_total_issue_count(
        self, mock_cls: MagicMock, _mock_collector: MagicMock
    ) -> None:
        """Table edge_cases header mentions total issue count from edge_case_summary."""
        mock_cls.return_value = _query_returning(
            ExtractionHealth(
                edge_case_summary={
                    "truncated_messages": 4,
                    "special_chars": 2,
                    "malformed_exceptions": 0,
                },
                edge_cases=[
                    {"test_id": "module::test_a", "issue": "truncated_message"},
                ],
            )
        )
        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["extraction-health", "--format", "table"])
        assert result.exit_code == 0
        # sum of edge_case_summary = 6
        assert "6" in result.stdout

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
        assert result is not None
        payload = json.loads(result.stdout)
        h = payload["history"]
        assert h["window_days"] == 7
        assert h["trend"] is not None  # >=2 snapshots → daily trend computable
        assert h["weekly_trend"]["granularity"] == "weekly"
        assert "slope" in h
        assert isinstance(h["anomalies"], list)
        assert h["observations"] >= 2
        assert isinstance(h["recent"], list) and len(h["recent"]) >= 2
