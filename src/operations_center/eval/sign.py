# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Operator answer-key signing CLI (HARNESS_TRUST_HARDENING §4.2, D-OP-3).

This is the tool the **operator** runs **offline** to perform the one irreducibly
human step in the whole trust-hardening spec: anchoring the EVAL answer key. It
deliberately does NOT live in any fleet/controller code path — nothing automated
ever calls it, and the private key it consumes must never touch a fleet host.

Two subcommands:

* ``keygen`` — generate a fresh Ed25519 keypair. Writes the PRIVATE key to a file
  you keep offline, and prints the PUBLIC key hex to paste into
  ``eval/constitution/operator_pubkey.ed25519``. Run this on an air-gapped / local
  machine, not on a fleet host.
* ``sign`` — using your offline private key, convert unsigned candidate cases in
  the ledger into signed graded cases (re-chaining the ledger). Commit the result;
  CI then counts them toward the gate.

Security note: the signing key is the entire root of trust for EVAL. If it leaks to
a fleet host, a compromised agent could forge answer-key labels — exactly the
attack the design prevents. Keep it offline; its only use is here."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from operations_center.eval.corpus import (
    load_ledger,
    verify_chain,
    write_ledger,
)
from operations_center.eval.signing import is_graded, sign_case


def generate_keypair(private_out: Path) -> str:
    """Write a new Ed25519 private key (PEM) to ``private_out``; return pubkey hex."""
    key = Ed25519PrivateKey.generate()
    private_out.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    return key.public_key().public_bytes_raw().hex()


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file or a 64-hex-char raw file."""
    data = path.read_bytes()
    text = data.decode("utf-8", "ignore").strip()
    if "BEGIN" in text and "PRIVATE KEY" in text:
        key = load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("PEM is not an Ed25519 private key")
        return key
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(text.split()[0]))


def public_key_of(private_key: Ed25519PrivateKey) -> Ed25519PublicKey:
    return private_key.public_key()


def sign_ledger(
    ledger_path: Path,
    private_key: Ed25519PrivateKey,
    *,
    signer: str,
    case_ids: set[str] | None = None,
) -> list[str]:
    """Sign unsigned candidate cases (all, or just ``case_ids``); rewrite the chain.

    Already-graded cases are left untouched (idempotent). Returns the case_ids that
    were newly signed."""
    ledger = load_ledger(ledger_path)
    verify_chain(ledger)
    pub = private_key.public_key()
    newly_signed: list[str] = []
    out_cases = []
    for case in ledger.cases():
        target = case_ids is None or case.case_id in case_ids
        if target and not is_graded(case, pub):
            case = sign_case(case, private_key, signer=signer)
            newly_signed.append(case.case_id)
        out_cases.append(case)
    write_ledger(ledger_path, out_cases)
    return newly_signed


def _cmd_keygen(args: argparse.Namespace) -> int:
    if args.private_out.exists() and not args.force:
        print(f"refusing to overwrite existing {args.private_out} (use --force)")
        return 1
    pubhex = generate_keypair(args.private_out)
    print(f"private key written to {args.private_out} — KEEP THIS OFFLINE.")
    print("public key (paste as the sole first line of "
          "eval/constitution/operator_pubkey.ed25519):")
    print(pubhex)
    return 0


def _cmd_sign(args: argparse.Namespace) -> int:
    key = load_private_key(args.private)
    ids = set(args.case_id) if args.case_id else None
    signed = sign_ledger(args.ledger, key, signer=args.signer, case_ids=ids)
    print(f"signed {len(signed)} case(s): {', '.join(signed) if signed else '(none new)'}")
    print(f"public key hex: {key.public_key().public_bytes_raw().hex()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EVAL operator answer-key signing (offline).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    kg = sub.add_parser("keygen", help="generate an offline Ed25519 keypair")
    kg.add_argument("--private-out", type=Path, default=Path("operator_priv.pem"))
    kg.add_argument("--force", action="store_true")
    kg.set_defaults(func=_cmd_keygen)

    sg = sub.add_parser("sign", help="sign candidate cases with your offline key")
    sg.add_argument("--private", type=Path, required=True)
    sg.add_argument("--ledger", type=Path, default=Path("eval/corpus/ledger.jsonl"))
    sg.add_argument("--signer", default="operator")
    sg.add_argument("--case-id", action="append", help="limit to these case_ids (repeatable)")
    sg.set_defaults(func=_cmd_sign)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
