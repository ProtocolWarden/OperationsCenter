# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for snapshot validation CLI."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from operations_center.observer.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_LOAD_ERROR,
    EXIT_SUCCESS,
    __version__,
    app,
    _build_tolerance_dict,
    _format_duration,
    _get_env_or_default,
    _parse_layers,
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
                app,
                ["export", "nonexistent_id", str(Path(tmpdir) / "out.json"), "--format", "json"],
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


class TestVersionOption:
    """Tests for version flag."""

    def test_version_flag_with_command(self) -> None:
        """Test --version flag works before commands."""
        result = runner.invoke(app, ["--version", "validate", "test.json"])
        assert result.exit_code == EXIT_SUCCESS
        assert __version__ in result.stdout
        assert "operations-center-observer-snapshot" in result.stdout

    def test_version_in_help(self) -> None:
        """Test version is documented in help."""
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == EXIT_SUCCESS
        # Strip ANSI escape codes before checking: Rich may insert codes mid-token
        # (e.g. \x1b[1m--\x1b[0mversion) on some Python/Rich version combinations.
        clean = re.sub(r"\x1b\[[0-9;]*[mK]", "", result.stdout)
        assert "--version" in clean

    def test_version_with_no_color_env(self) -> None:
        """Test version output respects NO_COLOR environment variable."""
        # With NO_COLOR set, output should not contain ANSI codes
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            # Need to reimport to pick up the env var in module-level console init
            result = CliRunner(env={"NO_COLOR": "1"}).invoke(app, ["--version"])
            assert result.exit_code == EXIT_SUCCESS
            assert __version__ in result.stdout
            # Should not have bold/cyan ANSI codes
            assert "\x1b[1m" not in result.stdout or "\x1b[36m" not in result.stdout

    def test_version_without_color_when_no_tty(self) -> None:
        """Test version output is plain when stdout is not a TTY."""
        # CliRunner simulates non-TTY output by default
        result = CliRunner().invoke(app, ["--version"])
        assert result.exit_code == EXIT_SUCCESS
        assert __version__ in result.stdout
        assert "operations-center-observer-snapshot" in result.stdout

    def test_help_output_without_ansi(self) -> None:
        """Test help output can be processed without ANSI codes on all Python versions."""
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == EXIT_SUCCESS
        # Should be able to strip all ANSI codes and still have valid content
        clean = re.sub(r"\x1b\[[0-9;]*[mK]", "", result.stdout)
        assert "validate" in clean  # At least one command should be visible
        assert "help" in clean.lower()  # Help text should be present

    def test_error_output_formatting(self) -> None:
        """Test error output formatting without spurious ANSI codes."""
        # Try to validate a non-existent file
        result = runner.invoke(app, ["validate", "nonexistent.json"])
        # Should fail with appropriate exit code
        assert result.exit_code != EXIT_SUCCESS
        # Error message should not have malformed ANSI sequences
        # Check that if there are ANSI codes, they follow valid patterns
        invalid_ansi_patterns = [
            r"\x1b\[(?![0-9;]*[mK])",  # ESC[ not followed by valid sequence
            r"\x1b\[[0-9;]*[^mK\s](?![0-9;])",  # Invalid ending character
        ]
        for pattern in invalid_ansi_patterns:
            matches = re.findall(pattern, result.stdout + result.stderr)
            assert not matches, f"Found invalid ANSI sequences: {matches}"


class TestEnvironmentVariables:
    """Tests for environment variable configuration."""

    def test_get_env_or_default_found(self) -> None:
        """Test _get_env_or_default when env var is set."""
        with patch.dict(os.environ, {"OC_SNAPSHOT_REPO_PATH": "/test/path"}):
            result = _get_env_or_default("REPO_PATH")
            assert result == "/test/path"

    def test_get_env_or_default_not_found(self) -> None:
        """Test _get_env_or_default when env var is not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OC_SNAPSHOT_MISSING", None)
            result = _get_env_or_default("MISSING", "default_value")
            assert result == "default_value"

    def test_get_env_or_default_no_default(self) -> None:
        """Test _get_env_or_default with no default value."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OC_SNAPSHOT_MISSING", None)
            result = _get_env_or_default("MISSING")
            assert result is None

    def test_log_level_from_env(self) -> None:
        """Test log level from environment variable."""
        with patch.dict(os.environ, {"OC_SNAPSHOT_LOG_LEVEL": "debug"}):
            result = runner.invoke(app, ["--help"])
            assert result.exit_code == EXIT_SUCCESS

    def test_tolerance_from_env(self) -> None:
        """Test tolerance configuration from environment variable."""
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

                with patch.dict(os.environ, {"OC_SNAPSHOT_TOLERANCE": "0.02"}):
                    result = runner.invoke(app, ["validate", f.name, "--quiet"])
                    assert result.exit_code == EXIT_SUCCESS

    def test_repo_path_from_env(self) -> None:
        """Test repo path from environment variable."""
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

                with patch.dict(os.environ, {"OC_SNAPSHOT_REPO_PATH": "/tmp"}):
                    result = runner.invoke(app, ["validate", f.name, "--quiet"])
                    assert result.exit_code == EXIT_SUCCESS

    def test_timeout_from_env(self) -> None:
        """Test timeout from environment variable."""
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

                with patch.dict(os.environ, {"OC_SNAPSHOT_TIMEOUT": "120"}):
                    result = runner.invoke(app, ["validate", f.name, "--quiet"])
                    assert result.exit_code == EXIT_SUCCESS

    def test_layers_from_env(self) -> None:
        """Test layers from environment variable."""
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

                with patch.dict(os.environ, {"OC_SNAPSHOT_LAYERS": "1,2"}):
                    result = runner.invoke(app, ["validate", f.name, "--quiet"])
                    assert result.exit_code == EXIT_SUCCESS


class TestSmokeTests:
    """Basic smoke tests for CLI functionality."""

    def test_help_command(self) -> None:
        """Test help command displays usage information."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == EXIT_SUCCESS
        assert "Snapshot validator CLI" in result.stdout or "validate" in result.stdout

    def test_help_validate_command(self) -> None:
        """Test help for validate command."""
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == EXIT_SUCCESS
        assert "snapshot" in result.stdout.lower() or "path" in result.stdout.lower()

    def test_invalid_command(self) -> None:
        """Test invalid command returns error."""
        result = runner.invoke(app, ["invalid-command"])
        assert result.exit_code != EXIT_SUCCESS

    def test_missing_required_argument(self) -> None:
        """Test validate without required snapshot path."""
        result = runner.invoke(app, ["validate"])
        assert result.exit_code != EXIT_SUCCESS


class TestValidationLayerIntegration:
    """Tests for validation layer integration in CLI."""

    def test_validate_layer_1_schema(self) -> None:
        """Test Layer 1 schema validation through CLI."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-001"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 50.0
            mock_report.layers_checked = [1]

            result1 = ValidationResult(
                passed=True,
                check_name="schema_validation",
                message="Schema validation passed",
                errors=[],
                duration_ms=45.0,
            )
            mock_report.results = [result1]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-001",
                "layers_checked": [1],
                "passed": True,
                "results": [result1.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-001",
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

                result = runner.invoke(app, ["validate", f.name, "--layers", "1"])
                assert result.exit_code == EXIT_SUCCESS

    def test_validate_layer_2_completeness(self) -> None:
        """Test Layer 2 completeness validation through CLI."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-002"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 75.0
            mock_report.layers_checked = [2]

            result2 = ValidationResult(
                passed=True,
                check_name="completeness_validation",
                message="Completeness validation passed",
                errors=[],
                duration_ms=70.0,
            )
            mock_report.results = [result2]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-002",
                "layers_checked": [2],
                "passed": True,
                "results": [result2.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-002",
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

                result = runner.invoke(app, ["validate", f.name, "--layers", "2"])
                assert result.exit_code == EXIT_SUCCESS

    def test_validate_layer_3_consistency(self) -> None:
        """Test Layer 3 consistency validation through CLI."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-003"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 90.0
            mock_report.layers_checked = [3]

            result3 = ValidationResult(
                passed=True,
                check_name="consistency_validation",
                message="Consistency validation passed",
                errors=[],
                duration_ms=85.0,
            )
            mock_report.results = [result3]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-003",
                "layers_checked": [3],
                "passed": True,
                "results": [result3.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-003",
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

                result = runner.invoke(app, ["validate", f.name, "--layers", "3"])
                assert result.exit_code == EXIT_SUCCESS

    def test_validate_all_layers_passing(self) -> None:
        """Test all 5 layers passing validation."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-all"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 5000.0
            mock_report.layers_checked = [1, 2, 3, 4, 5]

            results = []
            for layer in [1, 2, 3, 4, 5]:
                check_names = {
                    1: "schema_validation",
                    2: "completeness_validation",
                    3: "consistency_validation",
                    4: "accuracy_validation",
                    5: "regression_validation",
                }
                result = ValidationResult(
                    passed=True,
                    check_name=check_names[layer],
                    message=f"Layer {layer} passed",
                    errors=[],
                    duration_ms=1000.0,
                )
                results.append(result)

            mock_report.results = results
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-all",
                "layers_checked": [1, 2, 3, 4, 5],
                "passed": True,
                "results": [r.to_dict() for r in results],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-all",
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

    def test_validate_failing_validation(self) -> None:
        """Test validation failure returns correct exit code."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import (
                ValidationError,
                ValidationFailureCategory,
                ValidationResult,
            )

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = False
            mock_report.snapshot_id = "test-run-fail"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100.0
            mock_report.layers_checked = [1, 2]

            error = ValidationError(
                layer=2,
                category=ValidationFailureCategory.STRUCTURAL,
                message="Missing required signal",
                details={"signal_name": "test_signal"},
                is_retryable=False,
            )

            result1 = ValidationResult(
                passed=True,
                check_name="schema_validation",
                message="Schema validation passed",
                errors=[],
                duration_ms=50.0,
            )
            result2 = ValidationResult(
                passed=False,
                check_name="completeness_validation",
                message="Completeness validation failed",
                errors=[error],
                duration_ms=50.0,
            )
            mock_report.results = [result1, result2]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-fail",
                "layers_checked": [1, 2],
                "passed": False,
                "results": [result1.to_dict(), result2.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-fail",
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

                from operations_center.observer.cli import EXIT_VALIDATION_FAILED

                result = runner.invoke(app, ["validate", f.name, "--layers", "1,2"])
                assert result.exit_code == EXIT_VALIDATION_FAILED

    def test_validate_with_baseline_for_regression(self) -> None:
        """Test Layer 5 regression detection with baseline."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-current"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 200.0
            mock_report.layers_checked = [5]

            result5 = ValidationResult(
                passed=True,
                check_name="regression_validation",
                message="No regressions detected",
                errors=[],
                duration_ms=190.0,
            )
            mock_report.results = [result5]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-current",
                "layers_checked": [5],
                "passed": True,
                "results": [result5.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f1:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f2:
                    json.dump(
                        {
                            "run_id": "test-run-current",
                            "observed_at": "2026-06-14T00:00:00",
                            "observer_version": 1,
                            "source_command": "test",
                            "repo": {},
                            "signals": {},
                            "collector_errors": {},
                        },
                        f1,
                    )
                    f1.flush()

                    json.dump(
                        {
                            "run_id": "test-run-baseline",
                            "observed_at": "2026-06-13T00:00:00",
                            "observer_version": 1,
                            "source_command": "test",
                            "repo": {},
                            "signals": {},
                            "collector_errors": {},
                        },
                        f2,
                    )
                    f2.flush()

                    result = runner.invoke(
                        app,
                        [
                            "validate",
                            f1.name,
                            "--layers",
                            "5",
                            "--baseline",
                            f2.name,
                        ],
                    )
                    assert result.exit_code == EXIT_SUCCESS

    def test_validate_output_formats(self) -> None:
        """Test different output formats for validation results."""
        formats = ["table", "json", "markdown", "text"]

        for fmt in formats:
            with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
                from operations_center.observer.snapshot_validator import ValidationResult

                mock_report = MagicMock(spec=SnapshotValidationReport)
                mock_report.passed = True
                mock_report.snapshot_id = f"test-run-{fmt}"
                mock_report.observed_at = MagicMock()
                mock_report.overall_duration_ms = 100.0
                mock_report.layers_checked = [1]

                result = ValidationResult(
                    passed=True,
                    check_name="schema_validation",
                    message="Schema validation passed",
                    errors=[],
                    duration_ms=95.0,
                )
                mock_report.results = [result]
                mock_report.to_dict.return_value = {
                    "snapshot_id": f"test-run-{fmt}",
                    "layers_checked": [1],
                    "passed": True,
                    "results": [result.to_dict()],
                }
                mock_report.get_retryable_errors.return_value = []

                mock_engine_instance = MagicMock()
                mock_engine_instance.validate_with_retry.return_value = (
                    mock_report,
                    False,
                )
                mock_engine.return_value = mock_engine_instance

                with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                    json.dump(
                        {
                            "run_id": f"test-run-{fmt}",
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

                    cmd_result = runner.invoke(
                        app,
                        ["validate", f.name, "--format", fmt],
                    )
                    assert cmd_result.exit_code == EXIT_SUCCESS

    def test_validate_with_output_file(self) -> None:
        """Test saving validation report to file."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-output"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100.0
            mock_report.layers_checked = [1, 2, 3]

            results = []
            for layer in [1, 2, 3]:
                check_names = {
                    1: "schema_validation",
                    2: "completeness_validation",
                    3: "consistency_validation",
                }
                result = ValidationResult(
                    passed=True,
                    check_name=check_names[layer],
                    message=f"Layer {layer} passed",
                    errors=[],
                    duration_ms=30.0,
                )
                results.append(result)

            mock_report.results = results
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-output",
                "layers_checked": [1, 2, 3],
                "passed": True,
                "results": [r.to_dict() for r in results],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f_input:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False
                ) as f_output:
                    json.dump(
                        {
                            "run_id": "test-run-output",
                            "observed_at": "2026-06-14T00:00:00",
                            "observer_version": 1,
                            "source_command": "test",
                            "repo": {},
                            "signals": {},
                            "collector_errors": {},
                        },
                        f_input,
                    )
                    f_input.flush()

                    output_path = f_output.name

                try:
                    result = runner.invoke(
                        app,
                        [
                            "validate",
                            f_input.name,
                            "--output",
                            output_path,
                        ],
                    )
                    assert result.exit_code == EXIT_SUCCESS
                finally:
                    Path(output_path).unlink(missing_ok=True)

    def test_validate_with_tolerance_options(self) -> None:
        """Test validation with custom tolerance thresholds."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import ValidationResult

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = True
            mock_report.snapshot_id = "test-run-tol"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100.0
            mock_report.layers_checked = [4]

            result = ValidationResult(
                passed=True,
                check_name="accuracy_validation",
                message="Accuracy validation passed",
                errors=[],
                duration_ms=95.0,
            )
            mock_report.results = [result]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-tol",
                "layers_checked": [4],
                "passed": True,
                "results": [result.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-tol",
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
                        "--tolerance",
                        "0.02",
                        "--coverage-tolerance",
                        "0.03",
                        "--test-count-tolerance",
                        "0.01",
                    ],
                )
                assert result.exit_code == EXIT_SUCCESS

    def test_validate_with_verbose_output(self) -> None:
        """Test validation with verbose output for detailed error info."""
        with patch("operations_center.observer.cli.SnapshotValidationEngine") as mock_engine:
            from operations_center.observer.snapshot_validator import (
                ValidationError,
                ValidationFailureCategory,
                ValidationResult,
            )

            mock_report = MagicMock(spec=SnapshotValidationReport)
            mock_report.passed = False
            mock_report.snapshot_id = "test-run-verbose"
            mock_report.observed_at = MagicMock()
            mock_report.overall_duration_ms = 100.0
            mock_report.layers_checked = [1]

            error = ValidationError(
                layer=1,
                category=ValidationFailureCategory.STRUCTURAL,
                message="Schema validation failed",
                details={"error_type": "ValueError", "reason": "Invalid JSON"},
                is_retryable=False,
            )

            result = ValidationResult(
                passed=False,
                check_name="schema_validation",
                message="Schema validation failed",
                errors=[error],
                duration_ms=50.0,
            )
            mock_report.results = [result]
            mock_report.to_dict.return_value = {
                "snapshot_id": "test-run-verbose",
                "layers_checked": [1],
                "passed": False,
                "results": [result.to_dict()],
            }
            mock_report.get_retryable_errors.return_value = []

            mock_engine_instance = MagicMock()
            mock_engine_instance.validate_with_retry.return_value = (mock_report, False)
            mock_engine.return_value = mock_engine_instance

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                json.dump(
                    {
                        "run_id": "test-run-verbose",
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

                from operations_center.observer.cli import EXIT_VALIDATION_FAILED

                result = runner.invoke(app, ["validate", f.name, "--verbose"])
                assert result.exit_code == EXIT_VALIDATION_FAILED
