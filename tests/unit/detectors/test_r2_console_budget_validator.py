# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for R2 detector: .console/ budget and structure validator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from custodian.audit_kit.detector import AuditContext

# Import R2 detector from .custodian/detectors.py
# The .custodian directory is at the repo root, loadable via sys.path manipulation
_custodian_path = Path(__file__).parent.parent.parent.parent / ".custodian"
if str(_custodian_path) not in sys.path:
    sys.path.insert(0, str(_custodian_path.parent))

# Now we can import from detectors
_spec = importlib.util.spec_from_file_location("detectors", _custodian_path / "detectors.py")
assert _spec is not None
assert _spec.loader is not None
_detectors_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_detectors_module)
_detect_r2_console_budget = _detectors_module._detect_r2_console_budget


def _make_valid_console_files(console: Path) -> None:
    """Create valid .console/ files with required sections."""
    task_text = "# Task\n## Objective\nTest\n## Overall Plan\nTest\n## Current Stage\nTest\n"
    (console / "task.md").write_text(task_text, encoding="utf-8")
    (console / "guidelines.md").write_text("# Guidelines\n", encoding="utf-8")
    (console / "backlog.md").write_text("# Backlog\n## In Progress\n", encoding="utf-8")
    (console / "log.md").write_text("# Log\n", encoding="utf-8")
    (console / "workers.yaml").write_text("workers: []\n", encoding="utf-8")


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
    """Create a valid .console/ directory with all required files and proper structure."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create task.md with required sections
    task_content = (
        "# Task\n\n## Objective\nTest objective\n\n"
        "## Overall Plan\nTest plan\n\n## Current Stage\nTest stage\n"
    )
    (console / "task.md").write_text(task_content, encoding="utf-8")

    # Create guidelines.md
    (console / "guidelines.md").write_text("# Guidelines\nTest guidelines\n", encoding="utf-8")

    # Create backlog.md with required sections
    (console / "backlog.md").write_text(
        "# Backlog\n\n## In Progress\nItem 1\n\n## Up Next\nItem 2\n\n## Done\nItem 3\n",
        encoding="utf-8",
    )

    # Create log.md
    (console / "log.md").write_text("# Log\nLog entry 1\n", encoding="utf-8")

    # Create valid workers.yaml
    workers_content = "workers:\n  - name: test\n    schedule: daily\n"
    (console / "workers.yaml").write_text(workers_content, encoding="utf-8")

    return console


# ──────────────────────────────────────────────────────────────────────────────
# Valid input tests (R2-T1 to R2-T4 from Stage 1 plan)
# ──────────────────────────────────────────────────────────────────────────────


def test_r2_valid_console_complete_structure(
    audit_context: AuditContext, valid_console_dir: Path
) -> None:
    """Test R2 passes with all files present and valid structure (R2-T1)."""
    result = _detect_r2_console_budget(audit_context)
    assert result.count == 0, "Should have no violations with valid structure"
    assert result.samples == [], "Should have no error samples"


def test_r2_valid_task_md_with_all_sections(tmp_path: Path) -> None:
    """Test R2 passes when task.md has all required sections (R2-T2)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create task.md with all required sections
    task_md = (
        "# Current Task\n"
        "## Objective\nImplement feature X\n"
        "## Overall Plan\nStep 1, Step 2\n"
        "## Current Stage\nStage 3\n"
    )
    (console / "task.md").write_text(task_md, encoding="utf-8")

    _make_valid_console_files(console)

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

    result = _detect_r2_console_budget(ctx)
    assert result.count == 0, "Should pass with all required task.md sections"


def test_r2_valid_workers_yaml_structure(tmp_path: Path) -> None:
    """Test R2 passes with valid YAML in workers.yaml (R2-T3)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create valid YAML workers.yaml with complex structure
    workers_yaml = (
        "workers:\n"
        "  intake:\n"
        "    enabled: true\n"
        "    interval: 300\n"
        "  goal:\n"
        "    enabled: true\n"
        "    max_concurrent: 2\n"
    )
    (console / "workers.yaml").write_text(workers_yaml, encoding="utf-8")

    _make_valid_console_files(console)
    # Override workers.yaml (keep the complex one)
    (console / "workers.yaml").write_text(workers_yaml, encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count == 0, "Should pass with valid YAML structure"


def test_r2_valid_backlog_with_required_sections(tmp_path: Path) -> None:
    """Test R2 passes when backlog.md has required sections (R2-T4)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create backlog.md with standard sections
    backlog_md = (
        "# Backlog\n"
        "## In Progress\n"
        "- [ ] Task 1\n"
        "- [ ] Task 2\n"
        "## Up Next\n"
        "- [ ] Task 3\n"
        "## Done\n"
        "- [x] Task 4\n"
    )
    (console / "backlog.md").write_text(backlog_md, encoding="utf-8")

    _make_valid_console_files(console)

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

    result = _detect_r2_console_budget(ctx)
    assert result.count == 0, "Should pass with required backlog sections"


# ──────────────────────────────────────────────────────────────────────────────
# Malformed input tests (R2-T5 to R2-T9 from Stage 1 plan)
# ──────────────────────────────────────────────────────────────────────────────


def test_r2_task_md_missing_objective_section(tmp_path: Path) -> None:
    """Test R2 fails when task.md is missing Objective section (R2-T5)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create task.md without Objective section
    task_md = "# Current Task\n## Overall Plan\nStep 1, Step 2\n## Current Stage\nStage 3\n"
    (console / "task.md").write_text(task_md, encoding="utf-8")

    _make_valid_console_files(console)
    # Override task.md to have missing Objective
    (console / "task.md").write_text(task_md, encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count > 0, "Should detect missing Objective section"
    assert any("Objective" in s for s in result.samples), "Should report missing Objective"


def test_r2_workers_yaml_malformed_syntax(tmp_path: Path) -> None:
    """Test R2 fails when workers.yaml has YAML syntax error (R2-T7)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create malformed YAML (missing colon)
    malformed_yaml = "workers\n  - name: test\n    enabled: true\n"
    (console / "workers.yaml").write_text(malformed_yaml, encoding="utf-8")

    _make_valid_console_files(console)
    # Override with malformed YAML
    (console / "workers.yaml").write_text(malformed_yaml, encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count > 0, "Should detect YAML syntax error"
    assert any("YAML" in s for s in result.samples), "Should report YAML syntax error"


def test_r2_backlog_md_missing_sections(tmp_path: Path) -> None:
    """Test R2 fails when backlog.md has no standard sections (R2-T8)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create backlog.md with no standard sections
    backlog_content = "# Backlog\n\nSome random content without standard sections\n"
    (console / "backlog.md").write_text(backlog_content, encoding="utf-8")

    _make_valid_console_files(console)
    # Override with backlog without sections
    (console / "backlog.md").write_text(backlog_content, encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count > 0, "Should detect missing standard sections"
    assert any("sections" in s for s in result.samples), "Should report missing sections"


def test_r2_corrupted_file_invalid_utf8(tmp_path: Path) -> None:
    """Test R2 fails when file has invalid UTF-8 encoding (R2-T9)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create a file with invalid UTF-8 bytes
    (console / "log.md").write_bytes(b"\x80\x81\x82\x83")

    # Create other valid files manually
    task_text = "# Task\n## Objective\nTest\n## Overall Plan\nTest\n## Current Stage\nTest\n"
    (console / "task.md").write_text(task_text, encoding="utf-8")
    (console / "guidelines.md").write_text("# Guidelines\n", encoding="utf-8")
    (console / "backlog.md").write_text("# Backlog\n## In Progress\n", encoding="utf-8")
    (console / "workers.yaml").write_text("workers: []\n", encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count > 0, "Should detect corrupted file"
    assert any("UTF-8" in s for s in result.samples), "Should report encoding error"


# ──────────────────────────────────────────────────────────────────────────────
# Boundary condition tests (R2-T10 to R2-T12 from Stage 1 plan)
# ──────────────────────────────────────────────────────────────────────────────


def test_r2_file_at_size_boundary_100kb(tmp_path: Path) -> None:
    """Test R2 with file exactly at 100KB boundary (R2-T10)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create file exactly 99KB (below limit)
    content_99kb = "x" * (99 * 1024)
    (console / "log.md").write_text(f"# Log\n{content_99kb}\n", encoding="utf-8")

    _make_valid_console_files(console)

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

    result = _detect_r2_console_budget(ctx)
    # Should pass at 99KB (below limit)
    assert not any("exceeds" in s for s in result.samples), "Should pass at 99KB"


def test_r2_file_exceeds_size_boundary(tmp_path: Path) -> None:
    """Test R2 fails when file exceeds 500KB budget."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create file over 500KB (exceeds limit)
    content_501kb = "x" * (501 * 1024)
    (console / "log.md").write_text(f"# Log\n{content_501kb}\n", encoding="utf-8")

    # Create other valid files manually
    task_text = "# Task\n## Objective\nTest\n## Overall Plan\nTest\n## Current Stage\nTest\n"
    (console / "task.md").write_text(task_text, encoding="utf-8")
    (console / "guidelines.md").write_text("# Guidelines\n", encoding="utf-8")
    (console / "backlog.md").write_text("# Backlog\n## In Progress\n", encoding="utf-8")
    (console / "workers.yaml").write_text("workers: []\n", encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count > 0, "Should detect file exceeding 500KB"
    assert any("exceeds" in s for s in result.samples), "Should report size violation"


def test_r2_task_md_minimal_content(tmp_path: Path) -> None:
    """Test R2 passes with minimal content in task.md sections (R2-T11)."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create task.md with minimal but valid content (1 char per section)
    minimal_task = "# Task\n## Objective\nX\n## Overall Plan\nY\n## Current Stage\nZ\n"
    (console / "task.md").write_text(minimal_task, encoding="utf-8")

    _make_valid_console_files(console)
    # Override task.md with minimal content
    (console / "task.md").write_text(minimal_task, encoding="utf-8")

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

    result = _detect_r2_console_budget(ctx)
    assert result.count == 0, "Should pass with minimal but valid content"


def test_r2_no_console_directory(audit_context: AuditContext) -> None:
    """Test R2 gracefully handles missing .console/ directory."""
    result = _detect_r2_console_budget(audit_context)
    # R2 silently passes if .console/ doesn't exist (R1 checks presence)
    assert result.count == 0, "R2 should silently pass when .console/ missing (R1 checks presence)"


def test_r2_multiple_violations_reported(tmp_path: Path) -> None:
    """Test R2 reports multiple violations in one run."""
    console = tmp_path / ".console"
    console.mkdir(exist_ok=True)

    # Create file with insufficient size (will pass)
    (console / "task.md").write_text(
        "# Task\n"
        "## Objective\nX\n"
        "## Overall Plan\nY\n"
        # Missing Current Stage
        "## Something Else\nZ\n",
        encoding="utf-8",
    )

    # Malformed YAML
    (console / "workers.yaml").write_text("workers invalid yaml\n", encoding="utf-8")

    # Backlog with no standard sections
    (console / "backlog.md").write_text("# Backlog\n\nRandom content\n", encoding="utf-8")

    (console / "guidelines.md").write_text("# Guidelines\n")
    (console / "log.md").write_text("# Log\n")

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

    result = _detect_r2_console_budget(ctx)
    # Should report multiple violations
    assert result.count >= 2, f"Should report multiple violations, got {result.count}"
    assert len(result.samples) >= 2, "Should have multiple error samples"


def test_console_detector_ids_do_not_collide_with_builtin_readme_r1_r2():
    """Regression: the custom .console detectors must NOT register as 'R1'/'R2' —
    those collide with Custodian's builtin README R1/R2, masking each other so a
    .console violation gets mislabeled as a 'README first H1' finding (the bug that
    stalled goal/c99f3159). They must use the OC-prefixed ids OC1/OC2."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "oc_detectors", str(Path(__file__).resolve().parents[3] / ".custodian" / "detectors.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ids = {d.id for d in mod.build_oc_detectors()}
    assert "R1" not in ids and "R2" not in ids, f"custom detectors still collide: {ids & {'R1','R2'}}"
    assert "OC1" in ids and "OC2" in ids, f"expected OC1/OC2, got {sorted(ids)}"
