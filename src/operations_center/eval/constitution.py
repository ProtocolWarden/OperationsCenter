# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The constitution — the irreducible operator-anchored root (§4.2, D-OP-3).

Three surfaces change ~never and are the *only* human-gated part of EVAL:

1. the **signature-verify workflow** (`.github/workflows/eval-corpus-integrity.yml`)
   — runs ``verify.py`` as a required, non-bypassable check;
2. the **branch-protection / required-checks + CODEOWNERS** config that makes (1)
   un-disarmable and pins corpus/constitution edits to the operator;
3. the **monotonic baseline floor** (this file's data, `baseline_floor.json`) — the
   bar the graded corpus must clear, which may only *rise* automatically, never
   fall.

This module owns (3): loading the floor and enforcing that a proposed change never
lowers it. The self-healing body may *raise* the floor as the corpus grows and the
fleet's demonstrated competence climbs (a ratchet); it can never lower it. Lowering
requires editing this committed file, which CODEOWNERS pins to the operator and the
hash/sig-independent ``min_*`` comparison below flags — so a fleet PR that weakens
the exam is structurally visible.

The floor also encodes the **report-only → blocking graduation** precondition
(D-EVAL-3): the gate blocks only once enough operator-signed cases exist AND they
pass. Below that, EVAL runs in report-only mode — it never fails the build, so it
cannot deadlock the fleet before the answer key is seeded (§0.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASELINE_FLOOR_FILENAME = "baseline_floor.json"


@dataclass(frozen=True)
class BaselineFloor:
    """The monotonic bar. ``min_graded_cases`` is the graduation precondition; the
    gate stays report-only until at least this many signed cases exist."""

    min_graded_cases: int
    min_graded_pass_rate: float
    note: str = ""

    @staticmethod
    def load(path: Path) -> BaselineFloor:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return BaselineFloor(
            min_graded_cases=int(data["min_graded_cases"]),
            min_graded_pass_rate=float(data["min_graded_pass_rate"]),
            note=str(data.get("note", "")),
        )

    def is_monotonic_successor_of(self, prior: BaselineFloor) -> bool:
        """True iff ``self`` does not LOWER either bar relative to ``prior``."""
        return (
            self.min_graded_cases >= prior.min_graded_cases
            and self.min_graded_pass_rate >= prior.min_graded_pass_rate
        )


@dataclass(frozen=True)
class GateDecision:
    mode: str  # "report-only" | "blocking"
    ok: bool
    reason: str


def decide_gate(
    floor: BaselineFloor, *, graded_count: int, graded_pass_rate: float, gate_ok: bool
) -> GateDecision:
    """Resolve the gate per the graduation precondition (D-EVAL-3).

    Below ``min_graded_cases`` signed cases → **report-only** (never fails the
    build; the answer key isn't seeded yet — no deadlock before signing). At/above
    it → **blocking**: every graded case must pass and the pass rate must clear the
    floor."""
    if graded_count < floor.min_graded_cases:
        return GateDecision(
            "report-only",
            True,
            f"{graded_count}/{floor.min_graded_cases} signed cases — gate is "
            f"report-only until the answer key is seeded",
        )
    if not gate_ok:
        return GateDecision("blocking", False, "a graded corpus case failed replay")
    if graded_pass_rate < floor.min_graded_pass_rate:
        return GateDecision(
            "blocking",
            False,
            f"graded pass rate {graded_pass_rate:.3f} < floor {floor.min_graded_pass_rate:.3f}",
        )
    return GateDecision("blocking", True, "all graded cases pass and floor is cleared")


__all__ = [
    "BASELINE_FLOOR_FILENAME",
    "BaselineFloor",
    "GateDecision",
    "decide_gate",
]
