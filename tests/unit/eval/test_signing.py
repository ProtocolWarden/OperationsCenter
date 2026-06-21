# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Operator answer-key signatures: a graded case requires a verifying signature."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from operations_center.eval.corpus import Case
from operations_center.eval.signing import (
    PLACEHOLDER_MARKER,
    is_graded,
    load_public_key,
    sign_case,
)


def _case(cid: str = "c1") -> Case:
    return Case(
        case_id=cid,
        kind="verdict",
        input={"checks": [{"check_id": "code_quality", "status": "pass"},
                          {"check_id": "no_tooling_artifacts", "status": "pass"}]},
        ground_truth={"result": "LGTM", "failing": []},
        rationale="clean",
    )


def test_unsigned_case_is_not_graded():
    key = Ed25519PrivateKey.generate()
    assert is_graded(_case(), key.public_key()) is False


def test_signed_case_verifies_and_is_graded():
    key = Ed25519PrivateKey.generate()
    signed = sign_case(_case(), key, signer="operator")
    assert signed.signature
    assert is_graded(signed, key.public_key()) is True


def test_signature_does_not_transfer_to_a_different_input():
    """A label signed for one input must not validate a swapped answer/input."""
    key = Ed25519PrivateKey.generate()
    signed = sign_case(_case(), key, signer="operator")
    # Attacker keeps the signature but flips the ground truth.
    forged = Case(
        case_id=signed.case_id,
        kind=signed.kind,
        input=signed.input,
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
        signature=signed.signature,
        signer=signed.signer,
    )
    assert is_graded(forged, key.public_key()) is False


def test_wrong_key_does_not_verify():
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    signed = sign_case(_case(), key, signer="operator")
    assert is_graded(signed, other.public_key()) is False


def test_no_anchored_key_means_no_case_is_graded():
    key = Ed25519PrivateKey.generate()
    signed = sign_case(_case(), key, signer="operator")
    assert is_graded(signed, None) is False


def test_placeholder_pubkey_loads_as_none(tmp_path):
    p = tmp_path / "operator_pubkey.ed25519"
    p.write_text(PLACEHOLDER_MARKER + "\n# instructions\n")
    assert load_public_key(p) is None


def test_missing_pubkey_loads_as_none(tmp_path):
    assert load_public_key(tmp_path / "absent.ed25519") is None


def test_raw_hex_pubkey_roundtrip(tmp_path):
    key = Ed25519PrivateKey.generate()
    hexkey = key.public_key().public_bytes_raw().hex()
    p = tmp_path / "operator_pubkey.ed25519"
    p.write_text(hexkey + "\n")
    loaded = load_public_key(p)
    assert loaded is not None
    signed = sign_case(_case(), key, signer="op")
    assert is_graded(signed, loaded) is True
