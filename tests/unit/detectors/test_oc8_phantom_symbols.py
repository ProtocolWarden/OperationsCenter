# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for OC8: docs reference a symbol that doesn't exist."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from custodian.audit_kit.detector import AuditContext
from operations_center.observer.query_flaky import ExtractionHealth

_custodian_path = Path(__file__).parent.parent.parent.parent / ".custodian"
if str(_custodian_path.parent) not in sys.path:
    sys.path.insert(0, str(_custodian_path.parent))
_spec = importlib.util.spec_from_file_location("detectors", _custodian_path / "detectors.py")
assert _spec is not None and _spec.loader is not None
_detectors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_detectors)
_detect = _detectors._detect_oc8_phantom_symbols


@pytest.fixture
def ctx(tmp_path: Path) -> AuditContext:
    src = tmp_path / "src" / "operations_center"
    tests = tmp_path / "tests"
    docs = tmp_path / "docs" / "design"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    docs.mkdir(parents=True)
    return AuditContext(
        repo_root=tmp_path,
        src_root=src,
        tests_root=tests,
        config={"audit": {"common_words": [], "stale_handlers": []}},
        plugin_modules=[],
    )


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_existing_field_definition_is_not_flagged(ctx: AuditContext) -> None:
    assert ExtractionHealth.__name__ == "ExtractionHealth"
    _write(
        ctx.src_root / "models.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class ExampleModel:\n"
        "    extraction_health: int\n",
    )
    _write(
        ctx.repo_root / "docs" / "design" / "health.md",
        "**Files:** `extraction_health`\n",
    )

    result = _detect(ctx)

    assert result.count == 0


def test_missing_doc_symbol_is_reported(ctx: AuditContext) -> None:
    _write(
        ctx.src_root / "models.py",
        "def existing_symbol() -> None:\n    pass\n",
    )
    _write(
        ctx.repo_root / "docs" / "design" / "health.md",
        "**Files:** `missing_symbol`\n",
    )

    result = _detect(ctx)

    assert result.count == 1
    assert "missing_symbol" in result.samples[0]
