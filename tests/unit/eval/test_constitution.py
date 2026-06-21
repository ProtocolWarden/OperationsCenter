# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Constitution: monotonic baseline floor + report-only→blocking graduation."""

from __future__ import annotations

import json

from operations_center.eval.constitution import BaselineFloor, decide_gate


def _floor(cases=15, rate=1.0) -> BaselineFloor:
    return BaselineFloor(min_graded_cases=cases, min_graded_pass_rate=rate)


def test_floor_load(tmp_path):
    p = tmp_path / "baseline_floor.json"
    p.write_text(json.dumps({"min_graded_cases": 3, "min_graded_pass_rate": 0.9}))
    f = BaselineFloor.load(p)
    assert f.min_graded_cases == 3 and f.min_graded_pass_rate == 0.9


def test_monotonic_comparator():
    base = _floor(10, 0.9)
    assert _floor(10, 0.9).is_monotonic_successor_of(base)  # equal is allowed
    assert _floor(11, 0.95).is_monotonic_successor_of(base)  # rising is allowed
    assert not _floor(9, 0.9).is_monotonic_successor_of(base)  # fewer cases
    assert not _floor(10, 0.8).is_monotonic_successor_of(base)  # lower rate


def test_gate_is_report_only_below_threshold():
    d = decide_gate(_floor(15), graded_count=3, graded_pass_rate=1.0, gate_ok=True)
    assert d.mode == "report-only" and d.ok is True


def test_gate_blocks_and_passes_when_seeded_and_clean():
    d = decide_gate(_floor(2), graded_count=2, graded_pass_rate=1.0, gate_ok=True)
    assert d.mode == "blocking" and d.ok is True


def test_gate_blocks_and_fails_on_graded_failure():
    d = decide_gate(_floor(2), graded_count=2, graded_pass_rate=0.5, gate_ok=False)
    assert d.mode == "blocking" and d.ok is False


def test_gate_fails_when_pass_rate_below_floor():
    d = decide_gate(_floor(2, 1.0), graded_count=2, graded_pass_rate=0.9, gate_ok=True)
    assert d.mode == "blocking" and d.ok is False
