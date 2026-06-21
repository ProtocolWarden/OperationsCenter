# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""HARNESS_TRUST_HARDENING §4.4 acceptance criteria — executable & permanent.

Encodes the four EVAL acceptance criteria as a re-runnable gate against the REAL
committed corpus + constitution, so they keep holding as the code evolves:

1. Corpus of >=15 cases committed behind CODEOWNERS; a corpus edit trips the
   hash-chain tamper alarm.
2. Component 2 produces tickets and emits NO precision/recall number anywhere.
3. A seeded reviewer regression (re-introduce the #313 verdict bypass) is caught
   by the shadow gate before merge.
4. Blocking turns on only when the numeric precondition (>=min_graded_cases
   signed) is met.

The corpus is signed in-memory with an ephemeral TEST key — this never touches the
committed operator placeholder and never graduates the production gate; it only
exercises the gate machinery under a known answer key."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from operations_center.eval import replay as replay_mod
from operations_center.eval.constitution import BaselineFloor, decide_gate
from operations_center.eval.corpus import (
    CorpusIntegrityError,
    load_ledger,
    verify_chain,
    write_ledger,
)
from operations_center.eval.outcome_flagger import Disagreement, ReviewOutcome, flag_disagreements
from operations_center.eval.replay import run_corpus
from operations_center.eval.signing import is_graded, sign_case

_ROOT = Path(__file__).resolve().parents[3]
_LEDGER = _ROOT / "eval" / "corpus" / "ledger.jsonl"
_CONSTITUTION = _ROOT / "eval" / "constitution"
_CODEOWNERS = _ROOT / ".github" / "CODEOWNERS"


def _corpus_cases():
    return load_ledger(_LEDGER).cases()


def _sign_all(cases):
    """Sign every case in-memory with an ephemeral key; return (cases, graded_ids)."""
    key = Ed25519PrivateKey.generate()
    pub = key.public_key()
    signed = [sign_case(c, key, signer="acceptance-test") for c in cases]
    graded_ids = {c.case_id for c in signed if is_graded(c, pub)}
    return signed, graded_ids


# --- Criterion 1: >=15 cases, CODEOWNERS-pinned, tamper-evident ----------------

def test_criterion1_corpus_size_and_codeowners():
    cases = _corpus_cases()
    assert len(cases) >= 15, f"need >=15 corpus cases, have {len(cases)}"
    owners = _CODEOWNERS.read_text(encoding="utf-8")
    assert "/eval/corpus/" in owners and "/eval/constitution/" in owners


def test_criterion1_corpus_edit_trips_tamper_alarm(tmp_path):
    # Copy the real corpus, edit one answer in place, confirm the chain reds.
    cases = _corpus_cases()
    p = tmp_path / "ledger.jsonl"
    write_ledger(p, cases)
    verify_chain(load_ledger(p))  # baseline: valid
    rows = p.read_text().splitlines()
    import json
    obj = json.loads(rows[0])
    obj["ground_truth"] = {"result": "LGTM", "failing": []}  # forge an answer
    rows[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(rows) + "\n")
    with pytest.raises(CorpusIntegrityError):
        verify_chain(load_ledger(p))


# --- Criterion 2: flagger emits tickets, never a metric ------------------------

def test_criterion2_flagger_emits_tickets_no_metric():
    flags = flag_disagreements([
        ReviewOutcome(1, "LGTM", merged=True, main_regressed=True),
        ReviewOutcome(2, "CONCERNS", requeued_to_death=True),
    ])
    assert flags and all(isinstance(f, Disagreement) for f in flags)
    # Nothing numeric (a precision/recall rate) is ever returned.
    assert all(not isinstance(f, (int, float)) for f in flags)
    # And the module exposes no rate/score symbol.
    import operations_center.eval.outcome_flagger as ofm
    assert not any(
        tok in name.lower() for name in dir(ofm) for tok in ("precision", "recall", "rate", "score")
    )


# --- Criterion 3: a seeded #313 regression is caught by the shadow gate --------

def _regressed_compute_verdict(checks):
    """The #313 retraction, re-introduced: trust a status that *starts with* 'pass'
    (so an injected 'pass; IGNORE PREVIOUS…' reads as approval) and never fail."""
    from operations_center.entrypoints.pr_review_watcher.verdict import CONCERNS, LGTM
    if not isinstance(checks, list):
        return CONCERNS, ["malformed_no_checks"]
    for entry in checks:
        if isinstance(entry, dict) and str(entry.get("status", "")).startswith("pass"):
            return LGTM, []  # bypass: one "pass-ish" status approves the whole PR
    return CONCERNS, ["code_quality"]


def test_criterion3_clean_verdict_passes_the_gate():
    signed, graded_ids = _sign_all(_corpus_cases())
    report = run_corpus(signed, graded_ids)
    assert report.gate_ok, f"real verdict should pass; failures: {report.failures()}"
    assert len(report.graded) >= 15


def test_criterion3_seeded_313_regression_is_caught(monkeypatch):
    signed, graded_ids = _sign_all(_corpus_cases())
    # Land the regression in the reviewer's decision logic…
    monkeypatch.setattr(replay_mod, "compute_verdict", _regressed_compute_verdict)
    report = run_corpus(signed, graded_ids)
    # …the shadow gate must catch it BEFORE merge.
    assert not report.gate_ok, "the #313 regression slipped past the gate!"
    caught = {f.case_id for f in report.failures()}
    assert "inj-313-forged-approval-status" in caught


# --- Criterion 4: blocking turns on only at the numeric precondition -----------

def test_criterion4_graduation_precondition():
    floor = BaselineFloor.load(_CONSTITUTION / "baseline_floor.json")
    n = floor.min_graded_cases
    # One short of the floor → still report-only (cannot block, no deadlock).
    below = decide_gate(floor, graded_count=n - 1, graded_pass_rate=1.0, gate_ok=True)
    assert below.mode == "report-only" and below.ok
    # At the floor with all graded passing → blocking turns on.
    at = decide_gate(floor, graded_count=n, graded_pass_rate=1.0, gate_ok=True)
    assert at.mode == "blocking" and at.ok
