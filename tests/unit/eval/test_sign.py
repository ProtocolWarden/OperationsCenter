# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Operator signing CLI: keygen, candidate→graded conversion, re-chaining."""

from __future__ import annotations

from operations_center.eval.corpus import (
    Case,
    append_case,
    load_ledger,
    verify_chain,
)
from operations_center.eval.sign import (
    generate_keypair,
    load_private_key,
    main,
    sign_ledger,
)
from operations_center.eval.signing import is_graded, load_public_key


def _case(cid: str) -> Case:
    return Case(
        case_id=cid,
        kind="verdict",
        input={"checks": [{"check_id": "code_quality", "status": "fail"},
                          {"check_id": "no_tooling_artifacts", "status": "pass"}]},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    )


def test_keygen_roundtrip(tmp_path):
    priv = tmp_path / "operator_priv.pem"
    pubhex = generate_keypair(priv)
    assert priv.exists()
    assert len(bytes.fromhex(pubhex)) == 32
    key = load_private_key(priv)
    assert key.public_key().public_bytes_raw().hex() == pubhex


def test_sign_ledger_grades_candidates_and_keeps_chain_valid(tmp_path):
    priv = tmp_path / "k.pem"
    pubhex = generate_keypair(priv)
    key = load_private_key(priv)
    ledger_path = tmp_path / "ledger.jsonl"
    append_case(ledger_path, _case("a"))
    append_case(ledger_path, _case("b"))

    # Before: both candidates.
    pub = load_public_key_from_hex(tmp_path, pubhex)
    assert not any(is_graded(c, pub) for c in load_ledger(ledger_path).cases())

    signed = sign_ledger(ledger_path, key, signer="operator")
    assert set(signed) == {"a", "b"}

    ledger = load_ledger(ledger_path)
    verify_chain(ledger)  # re-chained correctly
    assert all(is_graded(c, pub) for c in ledger.cases())
    assert all(c.signer == "operator" for c in ledger.cases())


def test_sign_ledger_is_idempotent(tmp_path):
    priv = tmp_path / "k.pem"
    generate_keypair(priv)
    key = load_private_key(priv)
    ledger_path = tmp_path / "ledger.jsonl"
    append_case(ledger_path, _case("a"))
    assert sign_ledger(ledger_path, key, signer="op") == ["a"]
    assert sign_ledger(ledger_path, key, signer="op") == []  # already graded


def test_sign_ledger_limits_to_case_ids(tmp_path):
    priv = tmp_path / "k.pem"
    pubhex = generate_keypair(priv)
    key = load_private_key(priv)
    ledger_path = tmp_path / "ledger.jsonl"
    append_case(ledger_path, _case("a"))
    append_case(ledger_path, _case("b"))
    signed = sign_ledger(ledger_path, key, signer="op", case_ids={"b"})
    assert signed == ["b"]
    pub = load_public_key_from_hex(tmp_path, pubhex)
    graded = {c.case_id for c in load_ledger(ledger_path).cases() if is_graded(c, pub)}
    assert graded == {"b"}


def test_cli_keygen_then_sign(tmp_path, capsys):
    priv = tmp_path / "priv.pem"
    rc = main(["keygen", "--private-out", str(priv)])
    assert rc == 0
    pubhex = capsys.readouterr().out.strip().splitlines()[-1]

    ledger_path = tmp_path / "ledger.jsonl"
    append_case(ledger_path, _case("a"))
    rc = main(["sign", "--private", str(priv), "--ledger", str(ledger_path), "--signer", "op"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "signed 1 case" in out
    assert pubhex in out


def test_cli_keygen_refuses_overwrite(tmp_path):
    priv = tmp_path / "priv.pem"
    priv.write_text("existing")
    assert main(["keygen", "--private-out", str(priv)]) == 1


def load_public_key_from_hex(tmp_path, pubhex):
    p = tmp_path / "pub.ed25519"
    p.write_text(pubhex + "\n")
    return load_public_key(p)
