# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for OC12: domain-model constructed with a non-field keyword arg."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from custodian.audit_kit.detector import AuditContext

_custodian_path = Path(__file__).parent.parent.parent.parent / ".custodian"
if str(_custodian_path.parent) not in sys.path:
    sys.path.insert(0, str(_custodian_path.parent))
_spec = importlib.util.spec_from_file_location("detectors", _custodian_path / "detectors.py")
assert _spec is not None and _spec.loader is not None
_detectors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_detectors)
_detect = _detectors._detect_oc12_model_field_mismatch


@pytest.fixture
def ctx(tmp_path: Path) -> AuditContext:
    src = tmp_path / "src" / "operations_center"
    tests = tmp_path / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    return AuditContext(repo_root=tmp_path, src_root=src, tests_root=tests, config={}, plugin_modules=[])


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def test_flags_construction_with_nonexistent_field(ctx: AuditContext) -> None:
    # Reproduces #269: model has pattern_entropy, construction passes failure_entropy.
    _write(
        ctx.src_root,
        "models.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass FlakyTestMetric:\n    nodeid: str\n    pattern_entropy: float = 0.0\n",
    )
    _write(
        ctx.tests_root,
        "test_x.py",
        "from operations_center.models import FlakyTestMetric\n"
        "m = FlakyTestMetric(nodeid='t', failure_entropy=0.5)\n",
    )
    res = _detect(ctx)
    assert res.count == 1
    assert "failure_entropy" in res.samples[0]
    assert "FlakyTestMetric" in res.samples[0]


def test_clean_construction_not_flagged(ctx: AuditContext) -> None:
    _write(
        ctx.src_root,
        "models.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass M:\n    a: int\n    b: int = 0\n",
    )
    _write(ctx.tests_root, "test_ok.py", "from x import M\nm = M(a=1, b=2)\n")
    assert _detect(ctx).count == 0


def test_intentional_singular_plural_pair_not_flagged(ctx: AuditContext) -> None:
    # FlakyTestMetric vs FlakyTestMetrics — distinct, both constructed with THEIR
    # own fields. Must NOT fire (the detector keys on fields, never on names).
    _write(
        ctx.src_root,
        "a.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass FlakyTestMetric:\n    nodeid: str\n    pattern_entropy: float = 0.0\n",
    )
    _write(
        ctx.src_root,
        "b.py",
        "from dataclasses import dataclass, field\n\n"
        "@dataclass\nclass FlakyTestMetrics:\n    total_flaky_tests: int = 0\n    trend: float = 0.0\n",
    )
    _write(
        ctx.tests_root,
        "test_pair.py",
        "from a import FlakyTestMetric\nfrom b import FlakyTestMetrics\n"
        "x = FlakyTestMetric(nodeid='t', pattern_entropy=0.1)\n"
        "y = FlakyTestMetrics(total_flaky_tests=3, trend=0.2)\n",
    )
    assert _detect(ctx).count == 0


def test_subclass_inherited_field_not_flagged(ctx: AuditContext) -> None:
    _write(
        ctx.src_root,
        "models.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass Base:\n    a: int\n\n"
        "@dataclass\nclass Child(Base):\n    b: int = 0\n",
    )
    _write(ctx.tests_root, "test_sub.py", "from m import Child\nc = Child(a=1, b=2)\n")
    assert _detect(ctx).count == 0


def test_external_base_skipped(ctx: AuditContext) -> None:
    # Base class isn't local → field set unresolved → class skipped (no false positive).
    _write(
        ctx.src_root,
        "models.py",
        "from somewhere import ExternalBase\nfrom dataclasses import dataclass\n\n"
        "@dataclass\nclass Sub(ExternalBase):\n    a: int\n",
    )
    _write(ctx.tests_root, "test_ext.py", "from m import Sub\ns = Sub(a=1, inherited_field=2)\n")
    assert _detect(ctx).count == 0


def test_extra_allow_pydantic_skipped(ctx: AuditContext) -> None:
    _write(
        ctx.src_root,
        "models.py",
        "from pydantic import BaseModel, ConfigDict\n\n"
        "class Loose(BaseModel):\n"
        "    model_config = ConfigDict(extra='allow')\n    a: int\n",
    )
    _write(ctx.tests_root, "test_loose.py", "from m import Loose\nx = Loose(a=1, anything=2)\n")
    assert _detect(ctx).count == 0


def test_kwargs_expansion_not_flagged(ctx: AuditContext) -> None:
    _write(
        ctx.src_root,
        "models.py",
        "from dataclasses import dataclass\n\n@dataclass\nclass M:\n    a: int\n",
    )
    _write(ctx.tests_root, "test_kw.py", "from m import M\nd = {'a': 1}\nx = M(**d)\n")
    assert _detect(ctx).count == 0
