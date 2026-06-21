# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hash-chain tamper-evidence for the EVAL corpus ledger."""

from __future__ import annotations

import json

import pytest

from operations_center.eval.corpus import (
    GENESIS_PREV_HASH,
    Case,
    CorpusIntegrityError,
    append_case,
    load_ledger,
    verify_chain,
)


def _case(cid: str, result: str = "CONCERNS") -> Case:
    return Case(
        case_id=cid,
        kind="verdict",
        input={"checks": [{"check_id": "code_quality", "status": "fail"}]},
        ground_truth={"result": result, "failing": ["code_quality"]},
        rationale="r",
    )


def test_empty_ledger_is_valid(tmp_path):
    ledger = load_ledger(tmp_path / "missing.jsonl")
    assert ledger.entries == []
    assert ledger.head_hash == GENESIS_PREV_HASH
    verify_chain(ledger)  # no raise


def test_append_chains_and_verifies(tmp_path):
    p = tmp_path / "ledger.jsonl"
    e1 = append_case(p, _case("a"))
    e2 = append_case(p, _case("b"))
    assert e1.prev_hash == GENESIS_PREV_HASH
    assert e2.prev_hash == e1.entry_hash
    ledger = load_ledger(p)
    verify_chain(ledger)
    assert [c.case_id for c in ledger.cases()] == ["a", "b"]


def test_editing_a_past_entry_breaks_the_chain(tmp_path):
    p = tmp_path / "ledger.jsonl"
    append_case(p, _case("a"))
    append_case(p, _case("b"))
    lines = p.read_text().splitlines()
    obj = json.loads(lines[0])
    # Flip the answer of the first (signed-equivalent) case without re-chaining.
    obj["ground_truth"] = {"result": "LGTM", "failing": []}
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(CorpusIntegrityError):
        verify_chain(load_ledger(p))


def test_deleting_an_entry_breaks_the_chain(tmp_path):
    p = tmp_path / "ledger.jsonl"
    append_case(p, _case("a"))
    append_case(p, _case("b"))
    append_case(p, _case("c"))
    lines = p.read_text().splitlines()
    del lines[1]  # remove the middle entry
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(CorpusIntegrityError):
        verify_chain(load_ledger(p))


def test_append_refuses_to_extend_a_corrupt_chain(tmp_path):
    p = tmp_path / "ledger.jsonl"
    append_case(p, _case("a"))
    lines = p.read_text().splitlines()
    obj = json.loads(lines[0])
    obj["rationale"] = "tampered"
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(CorpusIntegrityError):
        append_case(p, _case("b"))
