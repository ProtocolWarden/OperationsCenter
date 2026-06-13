# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for OC13: test re-implements a metric inline without calling production."""

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
_detect = _detectors._detect_oc13_test_reimplements_metric


@pytest.fixture
def ctx(tmp_path: Path) -> AuditContext:
    src = tmp_path / "src" / "operations_center"
    tests = tmp_path / "tests"
    (src / "observer").mkdir(parents=True)
    tests.mkdir(parents=True)
    # minimal production metric surface
    (src / "observer" / "flaky_metrics.py").write_text(
        "import math\n\ndef failure_entropy(p, q):\n    return -(p*math.log2(p) + q*math.log2(q))\n",
        encoding="utf-8",
    )
    return AuditContext(repo_root=tmp_path, src_root=src, tests_root=tests, config={}, plugin_modules=[])


def _t(ctx: AuditContext, name: str, body: str) -> None:
    (ctx.tests_root / name).write_text("import math\n\n\n" + body, encoding="utf-8")


def test_flags_inline_entropy_without_production_call(ctx: AuditContext) -> None:
    # The #269 anti-pattern: compute entropy inline, assert, never call production.
    _t(
        ctx,
        "test_bad.py",
        "def test_failure_entropy_calc():\n"
        "    p = q = 0.5\n"
        "    entropy = -(p*math.log2(p) + q*math.log2(q))\n"
        "    assert abs(entropy - 1.0) < 1e-5\n",
    )
    res = _detect(ctx)
    assert res.count == 1
    assert "test_failure_entropy_calc" in res.samples[0]


def test_golden_value_crosscheck_not_flagged(ctx: AuditContext) -> None:
    # Legitimate: CALLS production (_compute_pattern_entropy) and uses inline math
    # only as an independently-derived reference value. Must NOT fire.
    _t(
        ctx,
        "test_good.py",
        "def test_pattern_entropy(reporter, runs):\n"
        "    entropy = reporter._compute_pattern_entropy(runs)\n"
        "    expected = -math.log(0.5) * 0.5 * 2\n"
        "    assert abs(entropy - expected) < 1e-3\n",
    )
    assert _detect(ctx).count == 0


def test_calls_flaky_metrics_function_not_flagged(ctx: AuditContext) -> None:
    _t(
        ctx,
        "test_calls_prod.py",
        "from operations_center.observer.flaky_metrics import failure_entropy\n\n"
        "def test_entropy():\n"
        "    got = failure_entropy(0.5, 0.5)\n"
        "    ref = -(0.5*math.log2(0.5) + 0.5*math.log2(0.5))\n"
        "    assert abs(got - ref) < 1e-9\n",
    )
    assert _detect(ctx).count == 0


def test_inline_math_without_assert_not_flagged(ctx: AuditContext) -> None:
    _t(
        ctx,
        "test_noassert.py",
        "def test_logs_something():\n    x = math.log2(8)\n    print(x)\n",
    )
    assert _detect(ctx).count == 0


def test_no_inline_metric_not_flagged(ctx: AuditContext) -> None:
    _t(
        ctx,
        "test_plain.py",
        "def test_plain():\n    assert 1 + 1 == 2\n",
    )
    assert _detect(ctx).count == 0
