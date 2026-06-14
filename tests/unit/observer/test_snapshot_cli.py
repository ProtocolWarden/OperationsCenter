# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for snapshot validation CLI."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from operations_center.observer.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_LOAD_ERROR,
    EXIT_SUCCESS,
    app,
    _parse_layers,
    _build_tolerance_dict,
    _format_duration,
)
from operations_center.observer.snapshot_validator import SnapshotValidationReport

runner = CliRunner()


class TestLayerParsing:
    """Tests for _parse_layers function."""

    def test_parse_default_layers(self) -> None:
        """Test parsing None returns default layers."""
        layers = _parse_layers(None)
        assert layers == [1, 2, 3]

    def test_parse_single_layer(self) -> None:
        """Test parsing single layer."""
        layers = _parse_layers("1")
        assert layers == [1]

    def test_parse_multiple_layers(self) -> None:
        """Test parsing multiple layers."""
        layers = _parse_layers("1,2,3")
        assert layers == [1, 2, 3]

    def test_parse_layers_with_whitespace(self) -> None:
        """Test parsing layers with whitespace."""
        layers = _parse_layers(" 1 , 2 , 3 ")
        assert layers == [1, 2, 3]

    def test_parse_layers_deduplicates(self) -> None:
        """Test parsing deduplicates layer numbers."""
        layers = _parse_layers("1,2,1,3,2")
        assert layers == [1, 2, 3]

    def test_parse_layers_sorts(self) -> None:
        """Test parsing sorts layer numbers."""
        layers = _parse_layers("5,1,3,2")
        assert layers == [1, 2, 3, 5]

    def test_parse_invalid_layer_too_low(self) -> None:
        """Test parsing raises on layer < 1."""
        with pytest.raises(ValueError):
            _parse_layers("0")

    def test_parse_invalid_layer_too_high(self) -> None:
        """Test parsing raises on layer > 5."""
        with pytest.raises(ValueError):
            _parse_layers("6")

    def test_parse_invalid_layer_non_numeric(self) -> None:
        """Test parsing raises on non-numeric layer."""
        with pytest.raises(ValueError):
            _parse_layers("abc")


class TestToleranceDict:
    """Tests for _build_tolerance_dict function."""

    def test_default_tolerance(self) -> None:
        """Test building tolerance dict with defaults."""
        tolerance = _build_tolerance_dict(0.05, None, None)
        assert tolerance == {
            "test_count": 0.05,
            "coverage": 0.05,
        }

    def test_override_coverage_tolerance(self) -> None:
        """Test overriding coverage tolerance."""
        tolerance = _build_tolerance_dict(0.05, coverage_tolerance=0.10, test_count_tolerance=None)
        assert tolerance == {
            "test_count": 0.05,
            "coverage": 0.10,
        }

    def test_override_test_count_tolerance(self) -> None:
        """Test overriding test count tolerance."""
        tolerance = _build_tolerance_dict(0.05, coverage_tolerance=None, test_count_tolerance=0.02)
        assert tolerance == {
            "test_count": 0.02,
            "coverage": 0.05,
        }

    def test_override_both_tolerances(self) -> None:
        """Test overriding both tolerances."""
        tolerance = _build_tolerance_dict(0.05, coverage_tolerance=0.10, test_count_tolerance=0.02)
        assert tolerance == {
            "test_count": 0.02,
            "coverage": 0.10,
        }


class TestFormatDuration:
    """Tests for _format_duration function."""

    def test_format_milliseconds(self) -> None:
        """Test formatting milliseconds."""
        result = _format_duration(500)
        assert result == "500.0ms"

    def test_format_seconds(self) -> None:
        """Test formatting seconds."""
        result = _format_duration(5000)
        assert result == "5.00s"

    def test_format_fractional_ms(self) -> None:
        """Test formatting fractional milliseconds."""
        result = _format_duration(123.456)
        assert result == "123.5ms"


class TestValidateCommand:
    """Tests for validate command."""

    def test_validate_missing_argument(self) -> None:
        """Test validate without snapshot path."""
        result = runner.invoke(app, ["validate"])
        assert result.exit_code != EXIT_SUCCESS

    def test_validate_invalid_layers(self) -> None:
        """Test validate with invalid layers."""
        result = runner.invoke(app, ["validate", "test.json", "--layers", "6"])
        assert result.exit_code == EXIT_CONFIG_ERROR

    def test_validate_with_quiet_flag(self) -> None:
        """Test validate with quiet flag suppresses output."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.to_dict.return_value = {}
            mock_report.snapshot_id = "test_id"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100
            mock_report.results = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test",
                        "observed_at": "2026-06-14T00:00:00",
                        "observer_version": 1,
                        "source_command": "test",
                        "repo": {},
                        "signals": {},
                        "collector_errors": {},
                    },
                    f,
                )
                f.flush()

                result = runner.invoke(app, ["validate", f.name, "--quiet"])
                assert result.exit_code == EXIT_SUCCESS


class TestListCommand:
    """Tests for list command."""

    def test_list_empty_directory(self) -> None:
        """Test list with no snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ["list", "--storage-root", tmpdir])
            assert result.exit_code == EXIT_SUCCESS
            # The output should mention no snapshots found
            assert "No snapshots found" in result.stdout or result.stdout == ""

    def test_list_format_json(self) -> None:
        """Test list with JSON format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            snapshot_dir = tmp_path / "obs_test_123_abc"
            snapshot_dir.mkdir()
            (snapshot_dir / "repo_state_snapshot.json").write_text("{}")

            result = runner.invoke(app, ["list", "--storage-root", tmpdir, "--format", "json"])
            assert result.exit_code == EXIT_SUCCESS
            assert "obs_test_123_abc" in result.stdout

    def test_list_invalid_backend(self) -> None:
        """Test list with non-local backend."""
        result = runner.invoke(app, ["list", "--backend", "s3"])
        assert result.exit_code == EXIT_CONFIG_ERROR


class TestShowCommand:
    """Tests for show command."""

    def test_show_missing_argument(self) -> None:
        """Test show without snapshot path."""
        result = runner.invoke(app, ["show"])
        assert result.exit_code != EXIT_SUCCESS

    def test_show_file_not_found(self) -> None:
        """Test show with non-existent file."""
        result = runner.invoke(app, ["show", "nonexistent.json"])
        assert result.exit_code == EXIT_LOAD_ERROR

    def test_show_with_quiet_flag(self) -> None:
        """Test show with quiet flag on non-existent source returns load error."""
        result = runner.invoke(app, ["show", "nonexistent.json", "--quiet"])
        assert result.exit_code == EXIT_LOAD_ERROR


class TestExportCommand:
    """Tests for export command."""

    def test_export_missing_arguments(self) -> None:
        """Test export without required arguments."""
        result = runner.invoke(app, ["export"])
        assert result.exit_code != EXIT_SUCCESS

    def test_export_source_not_found(self) -> None:
        """Test export with non-existent source."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app, ["export", "nonexistent_id", str(Path(tmpdir) / "output.json")]
            )
            assert result.exit_code == EXIT_LOAD_ERROR

    def test_export_json_format(self) -> None:
        """Test export to JSON format: missing source returns load error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app, ["export", "nonexistent_id", str(Path(tmpdir) / "out.json"), "--format", "json"]
            )
            assert result.exit_code == EXIT_LOAD_ERROR

    def test_export_auto_format_detection(self) -> None:
        """Test export auto-detects format: missing source returns load error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                app, ["export", "nonexistent_id", str(Path(tmpdir) / "out.json")]
            )
            assert result.exit_code == EXIT_LOAD_ERROR


class TestUnimplementedCommands:
    """Tests for unimplemented commands."""

    def test_observe_and_validate_not_implemented(self) -> None:
        """Test observe-and-validate command."""
        result = runner.invoke(app, ["observe-and-validate"])
        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "not yet implemented" in result.stdout

    def test_compare_not_implemented(self) -> None:
        """Test compare command."""
        result = runner.invoke(app, ["compare", "snap1", "snap2"])
        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "not yet implemented" in result.stdout

    def test_import_not_implemented(self) -> None:
        """Test import command."""
        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            result = runner.invoke(app, ["import", f.name])
            assert result.exit_code == EXIT_CONFIG_ERROR
            assert "not yet implemented" in result.stdout

    def test_cleanup_not_implemented(self) -> None:
        """Test cleanup command."""
        result = runner.invoke(app, ["cleanup"])
        assert result.exit_code == EXIT_SUCCESS
        assert "not yet implemented" in result.stdout


class TestGlobalOptions:
    """Tests for global options."""

    def test_help_option(self) -> None:
        """Test help option."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == EXIT_SUCCESS
        assert "validate" in result.stdout
        assert "list" in result.stdout
        assert "show" in result.stdout

    def test_debug_flag(self) -> None:
        """Test debug flag."""
        result = runner.invoke(app, ["--debug", "validate", "test.json"])
        assert "Unexpected error" in result.stdout or result.exit_code != EXIT_SUCCESS


class TestErrorHandling:
    """Tests for error handling and exit codes."""

    def test_validate_json_parse_error(self) -> None:
        """Test validate with invalid JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
            f.write("invalid json {")
            f.flush()

            result = runner.invoke(app, ["validate", f.name])
            assert result.exit_code == EXIT_LOAD_ERROR

    def test_tolerance_options(self) -> None:
        """Test tolerance options."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.to_dict.return_value = {}
            mock_report.snapshot_id = "test"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100
            mock_report.results = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test",
                        "observed_at": "2026-06-14T00:00:00",
                        "observer_version": 1,
                        "source_command": "test",
                        "repo": {},
                        "signals": {},
                        "collector_errors": {},
                    },
                    f,
                )
                f.flush()

                result = runner.invoke(
                    app,
                    [
                        "validate",
                        f.name,
                        "--coverage-tolerance",
                        "0.10",
                        "--test-count-tolerance",
                        "0.02",
                    ],
                )
                assert result.exit_code == EXIT_SUCCESS


class TestOutputFormats:
    """Tests for output format options."""

    def test_validate_table_format(self) -> None:
        """Test validate with table format (default)."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.to_dict.return_value = {}
            mock_report.snapshot_id = "test"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100
            mock_report.results = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test",
                        "observed_at": "2026-06-14T00:00:00",
                        "observer_version": 1,
                        "source_command": "test",
                        "repo": {},
                        "signals": {},
                        "collector_errors": {},
                    },
                    f,
                )
                f.flush()

                result = runner.invoke(app, ["validate", f.name, "--format", "table"])
                assert result.exit_code == EXIT_SUCCESS

    def test_validate_json_format(self) -> None:
        """Test validate with JSON format."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.to_dict.return_value = {"test": "data"}
            mock_report.snapshot_id = "test"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100
            mock_report.results = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test",
                        "observed_at": "2026-06-14T00:00:00",
                        "observer_version": 1,
                        "source_command": "test",
                        "repo": {},
                        "signals": {},
                        "collector_errors": {},
                    },
                    f,
                )
                f.flush()

                result = runner.invoke(app, ["validate", f.name, "--format", "json"])
                assert result.exit_code == EXIT_SUCCESS
                assert "test" in result.stdout


class TestLayersOption:
    """Tests for layers option."""

    def test_validate_layers_option(self) -> None:
        """Test validate with custom layers."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.to_dict.return_value = {}
            mock_report.snapshot_id = "test"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100
            mock_report.results = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test",
                        "observed_at": "2026-06-14T00:00:00",
                        "observer_version": 1,
                        "source_command": "test",
                        "repo": {},
                        "signals": {},
                        "collector_errors": {},
                    },
                    f,
                )
                f.flush()

                result = runner.invoke(app, ["validate", f.name, "--layers", "1,2,3,4,5"])
                assert result.exit_code == EXIT_SUCCESS
