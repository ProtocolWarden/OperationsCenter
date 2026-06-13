# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests for .console/ reconcile_enforce gate.

Tests the R1 (presence validator) and R2 (budget/structure validator) detectors
against all 7 fixture repositories to verify the reconcile_enforce gate correctly
identifies violations across all malformed .console/ configurations.

Acceptance criteria:
- 8-10 integration tests validating detection across all fixture categories
- Tests verify gate responsiveness to malformed configurations
- All tests pass without regressions
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from custodian.audit_kit.detector import AuditContext
from tests.fixtures.console_malformed import get_fixture_path

# Import R1 and R2 detectors from .custodian/detectors.py
_custodian_path = Path(__file__).parent.parent.parent.parent / ".custodian"
if str(_custodian_path) not in sys.path:
    sys.path.insert(0, str(_custodian_path.parent))

_spec = importlib.util.spec_from_file_location("detectors", _custodian_path / "detectors.py")
assert _spec is not None
assert _spec.loader is not None
_detectors_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_detectors_module)
_detect_r1_console_presence = _detectors_module._detect_r1_console_presence
_detect_r2_console_budget = _detectors_module._detect_r2_console_budget


pytestmark = [pytest.mark.integration]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: all 7 console fixture repositories
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def r1_missing_console_dir() -> Path:
    """Fixture for missing .console/ directory (R1 violation)."""
    return get_fixture_path("r1_missing_console_dir")


@pytest.fixture
def r1_console_is_file() -> Path:
    """Fixture for .console/ being a file, not directory (R1 violation)."""
    return get_fixture_path("r1_console_is_file")


@pytest.fixture
def r1_missing_task_md() -> Path:
    """Fixture for missing task.md file (R1 violation)."""
    return get_fixture_path("r1_missing_task_md")


@pytest.fixture
def r1_missing_workers_yaml() -> Path:
    """Fixture for missing workers.yaml file (R1 violation)."""
    return get_fixture_path("r1_missing_workers_yaml")


@pytest.fixture
def r2_oversized_task_md() -> Path:
    """Fixture for oversized task.md file (R2 violation)."""
    return get_fixture_path("r2_oversized_task_md")


@pytest.fixture
def r2_missing_task_section() -> Path:
    """Fixture for missing task.md section (R2 violation)."""
    return get_fixture_path("r2_missing_task_section")


@pytest.fixture
def r2_invalid_workers_yaml() -> Path:
    """Fixture for invalid workers.yaml (R2 violation)."""
    return get_fixture_path("r2_invalid_workers_yaml")


def _audit_context(fixture_path: Path) -> AuditContext:
    """Create an AuditContext for a fixture repository."""
    src_root = fixture_path / "src" / "operations_center"
    tests_root = fixture_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    return AuditContext(
        repo_root=fixture_path,
        src_root=src_root,
        tests_root=tests_root,
        config={"audit": {"reconcile_enforce": True}},
        plugin_modules=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# R1 Detector Integration Tests: Presence Validator
# ─────────────────────────────────────────────────────────────────────────────


def test_r1_integration_missing_console_dir(r1_missing_console_dir: Path) -> None:
    """Test R1 detector correctly identifies missing .console/ directory."""
    ctx = _audit_context(r1_missing_console_dir)
    result = _detect_r1_console_presence(ctx)

    assert result.count == 1, "Should detect one R1 violation: missing .console/"
    assert len(result.samples) == 1, "Should have exactly one error sample"
    assert ".console/ directory does not exist" in result.samples[0]


def test_r1_integration_console_is_file(r1_console_is_file: Path) -> None:
    """Test R1 detector correctly identifies .console/ being a file."""
    ctx = _audit_context(r1_console_is_file)
    result = _detect_r1_console_presence(ctx)

    assert result.count == 1, "Should detect one R1 violation: .console/ is file"
    assert len(result.samples) == 1, "Should have exactly one error sample"
    assert ".console/ exists but is not a directory" in result.samples[0]


def test_r1_integration_missing_task_md(r1_missing_task_md: Path) -> None:
    """Test R1 detector correctly identifies missing task.md file."""
    ctx = _audit_context(r1_missing_task_md)
    result = _detect_r1_console_presence(ctx)

    assert result.count == 1, "Should detect one R1 violation: missing task.md"
    assert len(result.samples) == 1, "Should have exactly one error sample"
    assert "task.md" in result.samples[0]


def test_r1_integration_missing_workers_yaml(r1_missing_workers_yaml: Path) -> None:
    """Test R1 detector correctly identifies missing workers.yaml file."""
    ctx = _audit_context(r1_missing_workers_yaml)
    result = _detect_r1_console_presence(ctx)

    assert result.count == 1, "Should detect one R1 violation: missing workers.yaml"
    assert len(result.samples) == 1, "Should have exactly one error sample"
    assert "workers.yaml" in result.samples[0]


# ─────────────────────────────────────────────────────────────────────────────
# R2 Detector Integration Tests: Budget & Structure Validator
# ─────────────────────────────────────────────────────────────────────────────


def test_r2_integration_oversized_task_md(r2_oversized_task_md: Path) -> None:
    """Test R2 detector correctly identifies oversized task.md file."""
    ctx = _audit_context(r2_oversized_task_md)
    result = _detect_r2_console_budget(ctx)

    assert result.count >= 1, "Should detect at least one R2 violation: oversized file"
    # Find the sample about task.md size
    size_violation = [s for s in result.samples if "task.md" in s and "200KB" in s]
    assert len(size_violation) > 0, "Should have error about task.md exceeding 200KB budget"


def test_r2_integration_missing_task_section(r2_missing_task_section: Path) -> None:
    """Test R2 detector correctly identifies missing task.md section."""
    ctx = _audit_context(r2_missing_task_section)
    result = _detect_r2_console_budget(ctx)

    assert result.count >= 1, "Should detect at least one R2 violation: missing section"
    # Find the sample about missing section
    section_violation = [s for s in result.samples if "Current Stage" in s]
    assert len(section_violation) > 0, "Should have error about missing Current Stage section"


def test_r2_integration_invalid_workers_yaml(r2_invalid_workers_yaml: Path) -> None:
    """Test R2 detector correctly identifies invalid workers.yaml."""
    ctx = _audit_context(r2_invalid_workers_yaml)
    result = _detect_r2_console_budget(ctx)

    assert result.count >= 1, "Should detect at least one R2 violation: invalid YAML"
    # Find the sample about YAML syntax error
    yaml_violation = [s for s in result.samples if "workers.yaml" in s and "YAML" in s]
    assert len(yaml_violation) > 0, "Should have error about workers.yaml YAML syntax error"


# ─────────────────────────────────────────────────────────────────────────────
# Gate Enforcement Tests: Parametrized Across All Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture_name,fixture_getter,expected_r1_violation,expected_r2_violation",
    [
        (
            "r1_missing_console_dir",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r1_missing_console_dir",
            ),
            True,
            False,
        ),
        (
            "r1_console_is_file",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r1_console_is_file",
            ),
            True,
            False,
        ),
        (
            "r1_missing_task_md",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r1_missing_task_md",
            ),
            True,
            False,
        ),
        (
            "r1_missing_workers_yaml",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r1_missing_workers_yaml",
            ),
            True,
            False,
        ),
        (
            "r2_oversized_task_md",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r2_oversized_task_md",
            ),
            False,
            True,
        ),
        (
            "r2_missing_task_section",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r2_missing_task_section",
            ),
            False,
            True,
        ),
        (
            "r2_invalid_workers_yaml",
            lambda: pytest.importorskip("tests.fixtures.console_malformed").get_fixture_path(
                "r2_invalid_workers_yaml",
            ),
            False,
            True,
        ),
    ],
    ids=[
        "r1_missing_console_dir",
        "r1_console_is_file",
        "r1_missing_task_md",
        "r1_missing_workers_yaml",
        "r2_oversized_task_md",
        "r2_missing_task_section",
        "r2_invalid_workers_yaml",
    ],
)
def test_gate_enforcement_all_fixtures(
    fixture_name: str,
    fixture_getter: Callable[[], Path],
    expected_r1_violation: bool,
    expected_r2_violation: bool,
) -> None:
    """Test that reconcile_enforce gate correctly identifies all violation types.

    This parametrized test validates each fixture against both R1 and R2 detectors
    to ensure the gate is responsive to all categories of violations.
    """
    fixture_path = fixture_getter()
    ctx = _audit_context(fixture_path)

    r1_result = _detect_r1_console_presence(ctx)
    r2_result = _detect_r2_console_budget(ctx)

    # Verify R1 detector response
    if expected_r1_violation:
        assert r1_result.count > 0, f"Fixture {fixture_name} should have R1 violation"
        assert len(r1_result.samples) > 0, f"Fixture {fixture_name} should have R1 error samples"
    else:
        assert r1_result.count == 0, f"Fixture {fixture_name} should not have R1 violation"

    # Verify R2 detector response
    if expected_r2_violation:
        assert r2_result.count > 0, f"Fixture {fixture_name} should have R2 violation"
        assert len(r2_result.samples) > 0, f"Fixture {fixture_name} should have R2 error samples"
    else:
        assert r2_result.count == 0, f"Fixture {fixture_name} should not have R2 violation"


# ─────────────────────────────────────────────────────────────────────────────
# Cross-Fixture Validation: R1 Violations Don't Trigger R2
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture_name",
    [
        "r1_missing_console_dir",
        "r1_console_is_file",
        "r1_missing_task_md",
        "r1_missing_workers_yaml",
    ],
)
def test_r2_gracefully_handles_r1_violations(fixture_name: str) -> None:
    """Test that R2 detector gracefully handles fixtures with R1 violations.

    R2 should not crash or produce misleading errors when .console/ is missing
    or malformed. It should silently skip validation in such cases.
    """
    fixture_path = get_fixture_path(fixture_name)
    ctx = _audit_context(fixture_path)

    # R2 should not crash and should return gracefully
    r2_result = _detect_r2_console_budget(ctx)
    assert isinstance(r2_result.count, int), "R2 should return valid DetectorResult"
    assert isinstance(r2_result.samples, list), "R2 should return valid samples list"
