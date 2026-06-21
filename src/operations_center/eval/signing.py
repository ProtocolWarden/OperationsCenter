# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Operator answer-key signatures (HARNESS_TRUST_HARDENING §4.2, D-OP-3).

This is the *only* place the irreducible human anchor enters EVAL. A corpus case
becomes a **graded** ground-truth label exactly when it carries an Ed25519
signature, over the case's gradeable identity, that verifies against the operator
public key committed in the constitution. Everything else — appending candidate
cases, replay, drift detection, reconfirmation — is automatic and needs no key.

Why asymmetric (not an HMAC / shared secret): the verifier runs inside the fleet's
own CI, which the agents under evaluation can read and influence. A shared secret
visible to the verifier is forgeable by anything that compromises the verifier. The
**private** signing key is operator-held and offline; its only use is converting an
appended candidate into a graded case. No amount of compute *inside* the trust
boundary can mint a label that verifies — that is the whole point (see §4.2 and the
[[harness-trust-hardening-spec]] "adversarial can refute, not anchor" argument).

The signing helper here exists for operator tooling and tests; in production the
operator signs offline and commits only the signature + public key.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from operations_center.eval.corpus import Case, canonical, gradeable_view

# A constitution pubkey file may be a real key or this sentinel placeholder, which
# means "the operator has not yet anchored a key" → no case can be graded yet
# (the gate stays in bootstrap/report-only mode; see constitution.py).
PLACEHOLDER_MARKER = "OPERATOR_PUBKEY_PLACEHOLDER"


def signing_bytes(case: Case) -> bytes:
    """The exact bytes an operator signs: the canonical gradeable view of the case.

    Binds the verdict to its precise input + case_id, so a valid signature cannot
    be lifted onto a different input or a relabeled case."""
    entry = case.payload()
    entry.setdefault("case_id", case.case_id)
    return canonical(gradeable_view({**entry, "case_id": case.case_id, "kind": case.kind})).encode(
        "utf-8"
    )


def sign_case(case: Case, private_key: Ed25519PrivateKey, *, signer: str) -> Case:
    """Return a copy of ``case`` carrying a detached operator signature (hex).

    Operator/test tooling only — production signing happens offline."""
    sig = private_key.sign(signing_bytes(case)).hex()
    return Case(
        case_id=case.case_id,
        kind=case.kind,
        input=case.input,
        ground_truth=case.ground_truth,
        context=case.context,
        rationale=case.rationale,
        signature=sig,
        signer=signer,
    )


def load_public_key(path: Path) -> Ed25519PublicKey | None:
    """Load the operator Ed25519 public key, or ``None`` if not yet anchored.

    Accepts a 64-hex-char raw key or PEM. The placeholder sentinel (and a missing
    file) return ``None`` — a valid, explicit "no key yet" bootstrap state."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text or PLACEHOLDER_MARKER in text:
        return None
    if "BEGIN PUBLIC KEY" in text:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        key = load_pem_public_key(text.encode("utf-8"))
        return key if isinstance(key, Ed25519PublicKey) else None
    # Otherwise treat the first token as raw hex (32 bytes / 64 chars).
    raw = bytes.fromhex(text.split()[0])
    return Ed25519PublicKey.from_public_bytes(raw)


def is_graded(case: Case, public_key: Ed25519PublicKey | None) -> bool:
    """True iff this case carries a signature that verifies against the operator
    key. No key anchored, or no/invalid signature → it is a candidate, not graded.

    Fail-closed: any verification error reads as 'not graded', never as graded."""
    if public_key is None or not case.signature:
        return False
    try:
        public_key.verify(bytes.fromhex(case.signature), signing_bytes(case))
        return True
    except (InvalidSignature, ValueError):
        return False


__all__ = [
    "PLACEHOLDER_MARKER",
    "is_graded",
    "load_public_key",
    "sign_case",
    "signing_bytes",
]
