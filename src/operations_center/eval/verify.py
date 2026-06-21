# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""EVAL corpus integrity + gate CLI — the required, non-bypassable check (D-OP-3).

Run by `.github/workflows/eval-corpus-integrity.yml` on every PR. It enforces the
three things the fleet must never be able to quietly subvert:

1. **Chain integrity** — the hash chain validates (no signed label edited/deleted).
2. **Answer-key gate** — every operator-signed case still passes the code-computed
   verdict; below the graduation threshold it is report-only (no deadlock).
3. **Monotonic floor** — the committed baseline floor was not lowered vs the base
   ref (checked by the workflow diffing the file; this CLI exposes the comparator).

Exit non-zero on (1) any tamper, or (2) a blocking-mode gate failure. Report-only
mode and a not-yet-anchored operator key both exit zero — EVAL must never block the
fleet before the answer key exists (§0.1 degrade-never-halt)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from operations_center.eval.constitution import (
    BASELINE_FLOOR_FILENAME,
    BaselineFloor,
    decide_gate,
)
from operations_center.eval.corpus import (
    CorpusIntegrityError,
    load_ledger,
    verify_chain,
)
from operations_center.eval.replay import run_corpus
from operations_center.eval.signing import is_graded, load_public_key

DEFAULT_CORPUS = Path("eval/corpus/ledger.jsonl")
DEFAULT_CONSTITUTION = Path("eval/constitution")


def verify(corpus_path: Path, constitution_dir: Path) -> tuple[int, list[str]]:
    """Return ``(exit_code, report_lines)``."""
    lines: list[str] = []

    # 1) Chain integrity — the tamper-evidence.
    try:
        ledger = load_ledger(corpus_path)
        verify_chain(ledger)
    except CorpusIntegrityError as exc:
        return 1, [f"TAMPER: corpus hash chain invalid: {exc}"]
    lines.append(f"chain OK: {len(ledger.entries)} entries, head {ledger.head_hash[:12]}…")

    # 2) Classify graded vs candidate against the operator key.
    pubkey = load_public_key(constitution_dir / "operator_pubkey.ed25519")
    cases = ledger.cases()
    graded_ids = {c.case_id for c in cases if is_graded(c, pubkey)}
    if pubkey is None:
        lines.append("operator key: NOT YET ANCHORED — all cases are candidates")
    lines.append(f"cases: {len(cases)} total, {len(graded_ids)} graded, "
                 f"{len(cases) - len(graded_ids)} candidate")

    # 3) Replay + gate decision under the baseline floor.
    report = run_corpus(cases, graded_ids)
    floor = BaselineFloor.load(constitution_dir / BASELINE_FLOOR_FILENAME)
    decision = decide_gate(
        floor,
        graded_count=len(graded_ids),
        graded_pass_rate=report.graded_pass_rate,
        gate_ok=report.gate_ok,
    )
    lines.append(f"gate [{decision.mode}]: {decision.reason}")
    for r in report.candidates:
        lines.append(f"  candidate {r.case_id}: {'pass' if r.passed else 'FAIL'} {r.detail}".rstrip())
    for r in report.failures():
        lines.append(f"  GRADED FAIL {r.case_id}: {r.detail}")

    return (0 if decision.ok else 1), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--constitution", type=Path, default=DEFAULT_CONSTITUTION)
    args = parser.parse_args(argv)
    code, lines = verify(args.corpus, args.constitution)
    for line in lines:
        print(line)
    print("RESULT:", "PASS" if code == 0 else "FAIL")
    return code


if __name__ == "__main__":
    sys.exit(main())
