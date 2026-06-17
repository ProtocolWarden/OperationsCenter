# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for R1 detector: .console/ directory presence validator."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from custodian.audit_kit.detector import AuditContext

# Import R1 detector from .custodian/detectors.py
# The .custodian directory is at the repo root, loadable via sys.path manipulation
_custodian_path = Path(__file__).parent.parent.parent.parent / ".custodian"
if str(_custodian_path) not in sys.path:
    sys.path.insert(0, str(_custodian_path.parent))

_spec = importlib.util.spec_from_file_location("detectors", _custodian_path / "detectors.py")
assert _spec is not None
assert _spec.loader is not None
_detectors_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_detectors_module)
_detect_r1_console_presence = _detectors_module._detect_r1_console_presence


@pytest.fixture
def audit_context(tmp_path: Path) -> AuditContext:
    """Create a minimal AuditContext pointing to tmp_path as repo_root."""
    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    return AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )


@pytest.fixture
def valid_console_dir(tmp_path: Path) -> Path:
    """Create a valid .console/ directory with all required files."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create all required files
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"]:
        (console / filename).write_text(f"# {filename}\n", encoding="utf-8")

    return console


# ──────────────────────────────────────────────────────────────────────────────
# Valid input tests
# ──────────────────────────────────────────────────────────────────────────────


def test_r1_valid_console_with_all_required_files(
    audit_context: AuditContext, valid_console_dir: Path
) -> None:
    """Test R1 passes when .console/ exists with all required files."""
    result = _detect_r1_console_presence(audit_context)
    assert result.count == 0, "Should have no violations"
    assert result.samples == [], "Should have no error samples"


def test_r1_valid_console_with_empty_files(tmp_path: Path) -> None:
    """Test R1 passes when .console/ files are empty (presence check only)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create files with zero content
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"]:
        (console / filename).write_text("", encoding="utf-8")

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    result = _detect_r1_console_presence(ctx)
    assert result.count == 0, "Should pass even with empty files (presence check)"
    assert result.samples == [], "Should have no error samples"


# ──────────────────────────────────────────────────────────────────────────────
# Missing directory tests
# ──────────────────────────────────────────────────────────────────────────────


def test_r1_missing_console_directory(audit_context: AuditContext) -> None:
    """Test R1 fails when .console/ directory is missing entirely."""
    result = _detect_r1_console_presence(audit_context)
    assert result.count == 1, "Should have 1 violation"
    assert len(result.samples) == 1, "Should have 1 error sample"
    assert ".console/ directory does not exist" in result.samples[0]


# ──────────────────────────────────────────────────────────────────────────────
# Console is not a directory tests
# ──────────────────────────────────────────────────────────────────────────────


def test_r1_console_is_file_not_directory(tmp_path: Path) -> None:
    """Test R1 fails when .console/ exists as a file instead of directory."""
    # Create .console as a file
    (tmp_path / ".console").write_text("not a directory")

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    result = _detect_r1_console_presence(ctx)
    assert result.count == 1, "Should have 1 violation"
    assert len(result.samples) == 1, "Should have 1 error sample"
    assert "not a directory" in result.samples[0]


# ──────────────────────────────────────────────────────────────────────────────
# Missing individual files tests (parameterized)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "missing_file",
    ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"],
)
def test_r1_missing_single_required_file(tmp_path: Path, missing_file: str) -> None:
    """Test R1 fails when a single required file is missing."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create all files except the missing one
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"]:
        if filename != missing_file:
            (console / filename).write_text(f"# {filename}\n", encoding="utf-8")

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    result = _detect_r1_console_presence(ctx)
    assert result.count == 1, f"Should have 1 violation for missing {missing_file}"
    assert len(result.samples) == 1, "Should have 1 error sample"
    assert missing_file in result.samples[0], f"Error message should mention {missing_file}"


# ──────────────────────────────────────────────────────────────────────────────
# Multiple missing files test
# ──────────────────────────────────────────────────────────────────────────────


def test_r1_missing_multiple_required_files(tmp_path: Path) -> None:
    """Test R1 reports multiple missing files correctly."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create only some files
    (console / "task.md").write_text("# task\n", encoding="utf-8")
    (console / "log.md").write_text("# log\n", encoding="utf-8")

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    result = _detect_r1_console_presence(ctx)
    assert result.count == 3, "Should have 3 violations (guidelines.md, backlog.md, workers.yaml)"
    assert len(result.samples) == 3, "Should have 3 error samples"

    # Verify all missing files are reported
    sample_str = " ".join(result.samples)
    assert "guidelines.md" in sample_str
    assert "backlog.md" in sample_str
    assert "workers.yaml" in sample_str


# ──────────────────────────────────────────────────────────────────────────────
# Boundary condition tests
# ──────────────────────────────────────────────────────────────────────────────


def test_r1_required_file_is_directory_not_file(tmp_path: Path) -> None:
    """Test R1 fails when a required file is a directory instead of a file."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create all files
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md"]:
        (console / filename).write_text(f"# {filename}\n", encoding="utf-8")

    # Create workers.yaml as a directory instead of file
    (console / "workers.yaml").mkdir()

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    result = _detect_r1_console_presence(ctx)
    assert result.count == 1, "Should have 1 violation"
    assert len(result.samples) == 1, "Should have 1 error sample"
    assert "workers.yaml" in result.samples[0]
    assert "not a file" in result.samples[0]


@pytest.mark.skipif(os.getuid() == 0, reason="Cannot test permission denied as root")
def test_r1_console_with_unreadable_file(tmp_path: Path) -> None:
    """Test R1 behavior with unreadable file (permission denied)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create all required files
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"]:
        (console / filename).write_text(f"# {filename}\n", encoding="utf-8")

    # Make one file unreadable
    unreadable_file = console / "task.md"
    unreadable_file.chmod(0o000)

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    try:
        # The detector checks existence, not readability, so this should still pass
        result = _detect_r1_console_presence(ctx)
        assert result.count == 0, "R1 checks existence only, not readability"
    finally:
        # Restore permissions for cleanup
        unreadable_file.chmod(0o644)


def test_r1_console_with_additional_optional_files(tmp_path: Path) -> None:
    """Test R1 passes when .console/ has required files plus optional extras."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create required files
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"]:
        (console / filename).write_text(f"# {filename}\n", encoding="utf-8")

    # Add optional files/subdirectories
    (console / "validation").mkdir()
    (console / ".context").mkdir()
    (console / "notes.txt").write_text("Optional notes\n", encoding="utf-8")

    src_root = tmp_path / "src" / "operations_center"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True, exist_ok=True)
    tests_root.mkdir(parents=True, exist_ok=True)

    ctx = AuditContext(
        repo_root=tmp_path,
        src_root=src_root,
        tests_root=tests_root,
        config={},
        plugin_modules=[],
    )

    result = _detect_r1_console_presence(ctx)
    assert result.count == 0, "Should pass with optional extra files/directories"
    assert result.samples == [], "Should have no error samples"
