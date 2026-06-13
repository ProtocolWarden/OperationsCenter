# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage metrics collection and analysis."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock


from operations_center.observer.collectors.coverage_collector import CoverageCollector
from operations_center.observer.coverage_models import (
    CoverageMetric,
    CoverageSnapshot,
    ModuleCoverage,
)


class TestCoverageMetric:
    """Tests for CoverageMetric dataclass."""

    def test_coverage_metric_creation(self) -> None:
        """Test creating a coverage metric."""
        metric = CoverageMetric(
            scope="src/operations_center/observer",
            scope_type="module",
            timestamp=datetime.now(UTC),
            source="pytest-cov",
            statement_coverage_pct=85.5,
            branch_coverage_pct=75.2,
            line_coverage_pct=86.0,
        )

        assert metric.scope == "src/operations_center/observer"
        assert metric.scope_type == "module"
        assert metric.statement_coverage_pct == 85.5
        assert metric.branch_coverage_pct == 75.2
        assert metric.line_coverage_pct == 86.0

    def test_coverage_metric_with_optional_fields(self) -> None:
        """Test coverage metric with optional fields."""
        metric = CoverageMetric(
            scope="",
            scope_type="repository",
            timestamp=datetime.now(UTC),
            source="coverage.py",
            statement_coverage_pct=88.0,
            branch_coverage_pct=80.0,
            line_coverage_pct=89.0,
            test_execution_time_ms=5000,
            test_count=150,
        )

        assert metric.test_execution_time_ms == 5000
        assert metric.test_count == 150


class TestCoverageSnapshot:
    """Tests for CoverageSnapshot dataclass."""

    def test_coverage_snapshot_creation(self) -> None:
        """Test creating a coverage snapshot."""
        module = ModuleCoverage(
            module_path="src/operations_center/observer",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )

        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="abc123",
            source="pytest-cov",
            overall_statement_coverage_pct=88.0,
            overall_branch_coverage_pct=80.0,
            overall_line_coverage_pct=89.0,
            module_coverages=[module],
        )

        assert snapshot.overall_line_coverage_pct == 89.0
        assert len(snapshot.module_coverages) == 1
        assert snapshot.module_coverages[0].health_status == "healthy"

    def test_module_coverage_health_status(self) -> None:
        """Test module coverage health status determination."""
        # Healthy
        healthy_module = ModuleCoverage(
            module_path="src/module1",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )
        assert healthy_module.health_status == "healthy"

        # At risk
        at_risk_module = ModuleCoverage(
            module_path="src/module2",
            statement_coverage_pct=75.0,
            branch_coverage_pct=65.0,
            line_coverage_pct=74.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="at_risk",
        )
        assert at_risk_module.health_status == "at_risk"

        # Critical
        critical_module = ModuleCoverage(
            module_path="src/module3",
            statement_coverage_pct=60.0,
            branch_coverage_pct=50.0,
            line_coverage_pct=59.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="critical",
        )
        assert critical_module.health_status == "critical"


class TestCoverageCollector:
    """Tests for CoverageCollector."""

    def test_collector_initialization(self) -> None:
        """Test initializing a coverage collector."""
        collector = CoverageCollector()
        assert collector.coverage_json_path is None or isinstance(collector.coverage_json_path, str)

    def test_collector_with_specific_path(self) -> None:
        """Test initializing collector with specific coverage file path."""
        collector = CoverageCollector(coverage_json_path="/path/to/coverage.json")
        assert collector.coverage_json_path == "/path/to/coverage.json"

    def test_extract_module_path(self) -> None:
        """Test module path extraction from file paths."""
        collector = CoverageCollector()

        # Test src-based paths
        assert (
            collector._extract_module_path("src/operations_center/observer/models.py")
            == "src/operations_center/observer"
        )
        assert (
            collector._extract_module_path("src/operations_center/custodian/service.py")
            == "src/operations_center/custodian"
        )

        # Test non-src paths
        result = collector._extract_module_path("tests/unit/observer/test_models.py")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_determine_health_status(self) -> None:
        """Test health status determination."""
        collector = CoverageCollector()

        assert collector._determine_health(85.0) == "healthy"
        assert collector._determine_health(80.0) == "healthy"
        assert collector._determine_health(79.9) == "at_risk"
        assert collector._determine_health(75.0) == "at_risk"
        assert collector._determine_health(70.0) == "at_risk"
        assert collector._determine_health(69.9) == "critical"
        assert collector._determine_health(50.0) == "critical"

    def test_parse_coverage_json(self) -> None:
        """Test parsing pytest-cov JSON output."""
        collector = CoverageCollector()

        coverage_data = {
            "meta": {"version": "5.5"},
            "totals": {"percent_covered": 88.5, "percent_covered_branch": 82.3},
            "files": {
                "src/operations_center/observer/models.py": {
                    "summary": {"percent_covered": 92.0},
                },
                "src/operations_center/observer/service.py": {
                    "summary": {"percent_covered": 85.0},
                },
                "src/operations_center/custodian/service.py": {
                    "summary": {"percent_covered": 78.0},
                },
            },
        }

        snapshot = collector._parse_coverage_json(coverage_data)

        assert snapshot is not None
        assert snapshot.overall_line_coverage_pct == 88.5
        assert len(snapshot.module_coverages) == 2  # 2 modules

        # Verify module aggregation
        modules = {m.module_path: m for m in snapshot.module_coverages}
        assert "src/operations_center/observer" in modules
        assert "src/operations_center/custodian" in modules

    def test_load_coverage_snapshot_missing_file(self) -> None:
        """Test loading coverage snapshot with missing file."""
        collector = CoverageCollector(coverage_json_path="/nonexistent/path.json")
        snapshot = collector._load_coverage_snapshot()
        assert snapshot is None

    def test_load_coverage_snapshot_valid_file(self) -> None:
        """Test loading coverage snapshot from valid file."""
        coverage_data = {
            "totals": {"percent_covered": 88.5},
            "files": {
                "src/test/file.py": {"summary": {"percent_covered": 88.5}},
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(coverage_data, f)
            temp_path = f.name

        try:
            collector = CoverageCollector(coverage_json_path=temp_path)
            snapshot = collector._load_coverage_snapshot()
            assert snapshot is not None
            assert snapshot.overall_line_coverage_pct == 88.5
        finally:
            Path(temp_path).unlink()

    def test_load_coverage_snapshot_invalid_json(self) -> None:
        """Test loading coverage snapshot with invalid JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json")
            temp_path = f.name

        try:
            collector = CoverageCollector(coverage_json_path=temp_path)
            snapshot = collector._load_coverage_snapshot()
            assert snapshot is None
        finally:
            Path(temp_path).unlink()

    def test_generate_summary(self) -> None:
        """Test generating coverage summary."""
        collector = CoverageCollector()

        healthy_module = ModuleCoverage(
            module_path="src/observer",
            statement_coverage_pct=85.0,
            branch_coverage_pct=75.0,
            line_coverage_pct=86.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="healthy",
        )

        critical_module = ModuleCoverage(
            module_path="src/custodian",
            statement_coverage_pct=60.0,
            branch_coverage_pct=50.0,
            line_coverage_pct=59.0,
            statement_count=100,
            branch_count=50,
            line_count=100,
            health_status="critical",
        )

        snapshot = CoverageSnapshot(
            timestamp=datetime.now(UTC),
            run_id="test",
            source="pytest-cov",
            overall_statement_coverage_pct=85.0,
            overall_branch_coverage_pct=75.0,
            overall_line_coverage_pct=86.0,
            module_coverages=[healthy_module, critical_module],
        )

        summary = collector._generate_summary(snapshot)
        assert "86.0%" in summary
        assert "2 modules" in summary
        assert "1 critical" in summary

    def test_collect_signal_unavailable(self) -> None:
        """Test collecting coverage signal when data is unavailable."""
        collector = CoverageCollector(coverage_json_path="/nonexistent/file.json")
        context = MagicMock()

        signal = collector.collect(context)

        assert signal.status == "unavailable"
        assert signal.total_coverage_pct is None

    def test_collect_signal_with_data(self) -> None:
        """Test collecting coverage signal with valid data."""
        coverage_data = {
            "totals": {"percent_covered": 88.5},
            "files": {
                "src/operations_center/observer/models.py": {
                    "summary": {"percent_covered": 92.0},
                },
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(coverage_data, f)
            temp_path = f.name

        try:
            collector = CoverageCollector(coverage_json_path=temp_path)
            context = MagicMock()

            signal = collector.collect(context)

            assert signal.status == "measured"
            assert signal.total_coverage_pct == 88.5
            assert signal.line_coverage_pct == 88.5
            assert signal.source == "pytest-cov"
            assert isinstance(signal.summary, str)
        finally:
            Path(temp_path).unlink()


class TestCoverageCollectorEdgeCases:
    """Edge case tests for coverage collection."""

    def test_parse_coverage_with_empty_files(self) -> None:
        """Test parsing coverage with empty files dict."""
        collector = CoverageCollector()

        coverage_data = {
            "totals": {"percent_covered": 0.0},
            "files": {},
        }

        snapshot = collector._parse_coverage_json(coverage_data)

        assert snapshot is not None
        assert snapshot.overall_line_coverage_pct == 0.0
        assert len(snapshot.module_coverages) == 0

    def test_parse_coverage_with_zero_coverage(self) -> None:
        """Test parsing coverage with zero coverage percentages."""
        collector = CoverageCollector()

        coverage_data = {
            "totals": {"percent_covered": 0.0},
            "files": {
                "src/untested.py": {"summary": {"percent_covered": 0.0}},
            },
        }

        snapshot = collector._parse_coverage_json(coverage_data)

        assert snapshot is not None
        assert snapshot.overall_line_coverage_pct == 0.0
        assert len(snapshot.module_coverages) == 1
        assert snapshot.module_coverages[0].health_status == "critical"

    def test_parse_coverage_with_100_percent(self) -> None:
        """Test parsing coverage with 100% coverage."""
        collector = CoverageCollector()

        coverage_data = {
            "totals": {"percent_covered": 100.0},
            "files": {
                "src/perfect.py": {"summary": {"percent_covered": 100.0}},
            },
        }

        snapshot = collector._parse_coverage_json(coverage_data)

        assert snapshot is not None
        assert snapshot.overall_line_coverage_pct == 100.0
        assert snapshot.module_coverages[0].health_status == "healthy"

    def test_collect_with_multiple_modules(self) -> None:
        """Test collecting coverage across multiple modules."""
        coverage_data = {
            "totals": {"percent_covered": 82.0},
            "files": {
                "src/operations_center/observer/models.py": {
                    "summary": {"percent_covered": 92.0},
                },
                "src/operations_center/observer/service.py": {
                    "summary": {"percent_covered": 88.0},
                },
                "src/operations_center/custodian/service.py": {
                    "summary": {"percent_covered": 65.0},
                },
                "src/other/module.py": {"summary": {"percent_covered": 71.0}},
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(coverage_data, f)
            temp_path = f.name

        try:
            collector = CoverageCollector(coverage_json_path=temp_path)
            context = MagicMock()

            signal = collector.collect(context)

            assert signal.status == "measured"
            assert len(signal.module_coverages) == 3  # 3 modules
            assert signal.total_coverage_pct == 82.0
        finally:
            Path(temp_path).unlink()

    def test_uncovered_file_counting(self) -> None:
        """Test counting files below coverage threshold."""
        coverage_data = {
            "totals": {"percent_covered": 75.0},
            "files": {
                "src/file1.py": {"summary": {"percent_covered": 90.0}},  # covered
                "src/file2.py": {"summary": {"percent_covered": 79.0}},  # uncovered
                "src/file3.py": {"summary": {"percent_covered": 70.0}},  # uncovered
                "src/file4.py": {"summary": {"percent_covered": 80.0}},  # covered
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(coverage_data, f)
            temp_path = f.name

        try:
            collector = CoverageCollector(coverage_json_path=temp_path)
            context = MagicMock()

            signal = collector.collect(context)

            assert signal.uncovered_file_count == 2
        finally:
            Path(temp_path).unlink()
