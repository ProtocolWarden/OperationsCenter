# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""End-to-end verify CLI: report-only bootstrap, tamper-evidence, signed blocking."""

from __future__ import annotations

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from operations_center.eval.corpus import Case, append_case
from operations_center.eval.signing import sign_case
from operations_center.eval.verify import verify


def _constitution(tmp_path, *, pubkey_hex=None, min_cases=15):
    d = tmp_path / "constitution"
    d.mkdir()
    (d / "baseline_floor.json").write_text(
        json.dumps({"min_graded_cases": min_cases, "min_graded_pass_rate": 1.0})
    )
    pk = d / "operator_pubkey.ed25519"
    pk.write_text(pubkey_hex + "\n" if pubkey_hex else "OPERATOR_PUBKEY_PLACEHOLDER\n")
    return d


def _concerns_case(cid, *, wrong=False) -> Case:
    gt = {"result": "LGTM", "failing": []} if wrong else {"result": "CONCERNS", "failing": ["code_quality"]}
    return Case(
        case_id=cid,
        kind="verdict",
        input={"checks": [{"check_id": "code_quality", "status": "fail"},
                          {"check_id": "no_tooling_artifacts", "status": "pass"}]},
        ground_truth=gt,
    )


def test_report_only_passes_with_unsigned_candidates(tmp_path):
    corpus = tmp_path / "ledger.jsonl"
    append_case(corpus, _concerns_case("a"))
    append_case(corpus, _concerns_case("b"))
    code, lines = verify(corpus, _constitution(tmp_path))
    assert code == 0
    assert any("report-only" in ln for ln in lines)


def test_tampered_chain_fails(tmp_path):
    corpus = tmp_path / "ledger.jsonl"
    append_case(corpus, _concerns_case("a"))
    append_case(corpus, _concerns_case("b"))
    rows = corpus.read_text().splitlines()
    obj = json.loads(rows[0])
    obj["ground_truth"] = {"result": "LGTM", "failing": []}
    rows[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    corpus.write_text("\n".join(rows) + "\n")
    code, lines = verify(corpus, _constitution(tmp_path))
    assert code == 1
    assert any("TAMPER" in ln for ln in lines)


def test_signed_cases_block_and_pass(tmp_path):
    key = Ed25519PrivateKey.generate()
    corpus = tmp_path / "ledger.jsonl"
    for i in range(2):
        append_case(corpus, sign_case(_concerns_case(f"s{i}"), key, signer="op"))
    constitution = _constitution(
        tmp_path, pubkey_hex=key.public_key().public_bytes_raw().hex(), min_cases=2
    )
    code, lines = verify(corpus, constitution)
    assert code == 0
    assert any("gate [blocking]" in ln for ln in lines)


def test_signed_wrong_answer_fails_the_blocking_gate(tmp_path):
    key = Ed25519PrivateKey.generate()
    corpus = tmp_path / "ledger.jsonl"
    append_case(corpus, sign_case(_concerns_case("s0"), key, signer="op"))
    # A signed case whose committed answer is wrong (simulates a verdict regression).
    append_case(corpus, sign_case(_concerns_case("s1", wrong=True), key, signer="op"))
    constitution = _constitution(
        tmp_path, pubkey_hex=key.public_key().public_bytes_raw().hex(), min_cases=2
    )
    code, lines = verify(corpus, constitution)
    assert code == 1
    assert any("GRADED FAIL s1" in ln for ln in lines)
