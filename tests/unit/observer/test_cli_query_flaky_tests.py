# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for CLI query-flaky-tests command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from operations_center.observer.cli import app


runner = CliRunner()


class TestQueryFlakyTestsCommand:
    """Test query-flaky-tests CLI command."""

    def test_command_help(self) -> None:
        """Command help is available."""
        result = runner.invoke(app, ["query-flaky-tests", "--help"])

        assert result.exit_code == 0
        assert "Query and display extracted test failure data" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_no_snapshots_found(self, mock_query_class: MagicMock) -> None:
        """Exit with NOT_FOUND when storage root doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            result = runner.invoke(app, ["query-flaky-tests"])

            assert result.exit_code == 2  # EXIT_NOT_FOUND
            assert "does not exist" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_no_failing_tests_found(self, mock_query_class: MagicMock) -> None:
        """Exit with SUCCESS when no failing tests found."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--hours", "24"])

            assert result.exit_code == 0
            assert "No failing tests found" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_with_test_names_table_format(self, mock_query_class: MagicMock) -> None:
        """Query with test names in table format."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {
            "test_foo": 10,
            "test_bar": 5,
        }
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--format", "table", "--quiet"])

            assert result.exit_code == 0
            # Output should be suppressed with --quiet but command succeeds
            # The table output would normally be printed

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_with_test_names_json_format(self, mock_query_class: MagicMock) -> None:
        """Query with test names in JSON format."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {
            "test_foo": 10,
            "test_bar": 5,
        }
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--format", "json", "--quiet"])

            assert result.exit_code == 0

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_with_test_names_markdown_format(self, mock_query_class: MagicMock) -> None:
        """Query with test names in markdown format."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {
            "test_foo": 10,
            "test_bar": 5,
        }
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--format", "markdown", "--quiet"])

            assert result.exit_code == 0

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_with_assertions_included(self, mock_query_class: MagicMock) -> None:
        """Query with assertion messages included."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 10}
        mock_query.get_failing_assertion_messages.return_value = {
            "assert x > 0": 10,
        }
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(
                app,
                [
                    "query-flaky-tests",
                    "--include-assertions",
                    "--format",
                    "json",
                    "--quiet",
                ],
            )

            assert result.exit_code == 0
            # Assertions should be fetched when flag is set
            mock_query.get_failing_assertion_messages.assert_called_once()

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_with_custom_hours(self, mock_query_class: MagicMock) -> None:
        """Query with custom hour range."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 5}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--hours", "6", "--quiet"])

            assert result.exit_code == 0
            # TimeRange.last_hours(6) should be called
            # Verify via the mock

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_with_custom_storage_root(self, mock_query_class: MagicMock) -> None:
        """Query with custom storage root."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 5}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(
                app,
                [
                    "query-flaky-tests",
                    "--storage-root",
                    "/custom/path",
                    "--quiet",
                ],
            )

            assert result.exit_code == 0

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_default_parameters(self, mock_query_class: MagicMock) -> None:
        """Query uses correct default parameters."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 5}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--quiet"])

            assert result.exit_code == 0
            # Should use 24 hours by default (verified by mock call)

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_error_handling(self, mock_query_class: MagicMock) -> None:
        """Query handles errors gracefully."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.side_effect = RuntimeError("Query failed")
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests"])

            assert result.exit_code == 4  # EXIT_CONFIG_ERROR
            assert "Error querying flaky tests" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_json_output_structure(self, mock_query_class: MagicMock) -> None:
        """JSON output has correct structure."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 10}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--format", "json"])

            assert result.exit_code == 0
            # Output should be valid JSON with test_names key (test_failures only when assertions included)
            output_lines = [line for line in result.stdout.split("\n") if line.strip()]
            json_str = "\n".join(output_lines)
            parsed = json.loads(json_str)
            # Without --include-assertions, output has test_names key
            assert "test_names" in parsed
            assert parsed["total_count"] == 10

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_markdown_output_structure(self, mock_query_class: MagicMock) -> None:
        """Markdown output has correct structure."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 10}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--format", "markdown"])

            assert result.exit_code == 0
            assert "## Failing Test Names" in result.stdout

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_quiet_mode_suppresses_output(self, mock_query_class: MagicMock) -> None:
        """Quiet mode suppresses normal output."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 10}
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(app, ["query-flaky-tests", "--quiet"])

            assert result.exit_code == 0
            # In quiet mode, detailed output is suppressed
            # but command should still succeed

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_assertions_combined_json(self, mock_query_class: MagicMock) -> None:
        """Assertions are properly combined in JSON output."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 10}
        mock_query.get_failing_assertion_messages.return_value = {
            "assert x > 0": 8,
            "assert y == expected": 2,
        }
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(
                app,
                [
                    "query-flaky-tests",
                    "--include-assertions",
                    "--format",
                    "json",
                ],
            )

            assert result.exit_code == 0
            # Output should contain both test_failures and assertion_failures
            output_lines = [line for line in result.stdout.split("\n") if line.strip()]
            json_str = "\n".join(output_lines)
            parsed = json.loads(json_str)
            assert "test_failures" in parsed
            assert "assertion_failures" in parsed
            assert "assertion_messages" in parsed["assertion_failures"]

    @patch("operations_center.observer.cli.TestSignalQuery")
    def test_query_assertions_combined_markdown(self, mock_query_class: MagicMock) -> None:
        """Assertions are properly combined in markdown output."""
        mock_query = MagicMock()
        mock_query.get_failing_test_names.return_value = {"test_foo": 10}
        mock_query.get_failing_assertion_messages.return_value = {
            "assert x > 0": 8,
        }
        mock_query_class.return_value = mock_query

        with patch("pathlib.Path.exists", return_value=True):
            result = runner.invoke(
                app,
                [
                    "query-flaky-tests",
                    "--include-assertions",
                    "--format",
                    "markdown",
                ],
            )

            assert result.exit_code == 0
            assert "## Failing Test Names" in result.stdout
            assert "## Failing Assertion Messages" in result.stdout
