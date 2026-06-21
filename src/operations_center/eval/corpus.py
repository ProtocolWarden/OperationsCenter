# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Append-only, hash-chained EVAL corpus (HARNESS_TRUST_HARDENING §4.2, D-EVAL-2).

The corpus is the *exam* the reviewer/worker agents are graded against. It must be
tamper-evident: a fleet PR (or an injected agent) that edits or deletes a case —
especially a signed ground-truth label — has to be **structurally** detectable, not
merely discouraged. So the corpus is stored as an append-only JSONL ledger where
each entry carries the hash of the previous one:

    entry_hash = sha256( prev_hash || canonical(payload-without-entry_hash) )

Editing any field of any past entry changes its ``entry_hash``, which breaks the
``prev_hash`` link of every later entry → the integrity check (``verify.py``,
a required, non-bypassable workflow) goes red. The genesis entry chains from
64 zeros.

A case is one ``(input, ground-truth verdict, rationale)`` fixture. Whether it is a
*graded* case (counts toward the gate) or an *unsigned candidate* (scored, but not
counted until an operator signs it once) is decided by ``signing.py`` against the
operator public key in the constitution — never by a field the fleet can flip here.
This module only owns the chain; it knows nothing about signatures' validity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GENESIS_PREV_HASH = "0" * 64

# Fields that are part of the signed/gradeable identity of a case (see signing.py).
# Kept here so the canonical form is defined in exactly one place.
_GRADEABLE_FIELDS = ("case_id", "kind", "input", "ground_truth")


def canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON for hashing/signing: sorted keys, no whitespace.

    ``ensure_ascii=False`` keeps non-ASCII bytes verbatim (deterministic under
    UTF-8) instead of expanding to ``\\uXXXX`` escapes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def gradeable_view(entry: dict[str, Any]) -> dict[str, Any]:
    """The subset of an entry an operator signature binds: the case identity and
    its ground-truth answer. Excludes chain metadata (``prev_hash``/``entry_hash``)
    and human-readable prose so a signature stays valid regardless of the case's
    position in the chain — but still binds the answer to its exact input."""
    return {k: entry[k] for k in _GRADEABLE_FIELDS if k in entry}


def compute_entry_hash(payload: dict[str, Any], prev_hash: str) -> str:
    """The chain hash for ``payload`` (which must NOT contain ``entry_hash``)."""
    body = prev_hash + canonical(payload)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Case:
    """One corpus fixture.

    ``signature``/``signer`` are absent on an unsigned candidate. ``kind`` selects
    the graded layer (``"verdict"`` → replayed through the code-computed verdict)."""

    case_id: str
    kind: str
    input: dict[str, Any]
    ground_truth: dict[str, Any]
    context: str = ""
    rationale: str = ""
    signature: str | None = None
    signer: str | None = None

    def payload(self) -> dict[str, Any]:
        """The entry body (everything except the chain's own ``entry_hash``)."""
        body: dict[str, Any] = {
            "case_id": self.case_id,
            "kind": self.kind,
            "input": self.input,
            "ground_truth": self.ground_truth,
            "context": self.context,
            "rationale": self.rationale,
        }
        if self.signature is not None:
            body["signature"] = self.signature
        if self.signer is not None:
            body["signer"] = self.signer
        return body


@dataclass
class LedgerEntry:
    case: Case
    prev_hash: str
    entry_hash: str


@dataclass
class Ledger:
    entries: list[LedgerEntry] = field(default_factory=list)

    @property
    def head_hash(self) -> str:
        return self.entries[-1].entry_hash if self.entries else GENESIS_PREV_HASH

    def cases(self) -> list[Case]:
        return [e.case for e in self.entries]


def _case_from_payload(payload: dict[str, Any]) -> Case:
    return Case(
        case_id=str(payload["case_id"]),
        kind=str(payload["kind"]),
        input=payload["input"],
        ground_truth=payload["ground_truth"],
        context=str(payload.get("context", "")),
        rationale=str(payload.get("rationale", "")),
        signature=payload.get("signature"),
        signer=payload.get("signer"),
    )


class CorpusIntegrityError(Exception):
    """Raised when the hash chain does not validate (tamper-evidence tripped)."""


def load_ledger(path: Path) -> Ledger:
    """Parse the JSONL ledger WITHOUT validating the chain (use ``verify_chain``).

    Returns an empty ledger if the file is missing — an empty corpus is a valid
    bootstrap state, not an error."""
    ledger = Ledger()
    if not path.exists():
        return ledger
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusIntegrityError(f"line {lineno}: invalid JSON: {exc}") from exc
        entry_hash = obj.pop("entry_hash", None)
        prev_hash = obj.pop("prev_hash", None)
        if not isinstance(entry_hash, str) or not isinstance(prev_hash, str):
            raise CorpusIntegrityError(f"line {lineno}: missing prev_hash/entry_hash")
        ledger.entries.append(
            LedgerEntry(_case_from_payload(obj), prev_hash=prev_hash, entry_hash=entry_hash)
        )
    return ledger


def verify_chain(ledger: Ledger) -> None:
    """Raise ``CorpusIntegrityError`` if any link is broken or any hash is wrong."""
    prev = GENESIS_PREV_HASH
    for i, entry in enumerate(ledger.entries):
        if entry.prev_hash != prev:
            raise CorpusIntegrityError(
                f"entry {i} ({entry.case.case_id}): prev_hash {entry.prev_hash[:12]}… "
                f"!= expected {prev[:12]}… (chain broken — a prior entry was edited/removed)"
            )
        recomputed = compute_entry_hash(entry.case.payload(), prev)
        if recomputed != entry.entry_hash:
            raise CorpusIntegrityError(
                f"entry {i} ({entry.case.case_id}): entry_hash mismatch "
                f"(content was modified after it was chained)"
            )
        prev = entry.entry_hash


def append_case(path: Path, case: Case) -> LedgerEntry:
    """Append one case, chaining from the current head. Re-verifies the existing
    chain first so we never extend a corrupted ledger."""
    ledger = load_ledger(path)
    verify_chain(ledger)
    prev = ledger.head_hash
    entry_hash = compute_entry_hash(case.payload(), prev)
    record = dict(case.payload())
    record["prev_hash"] = prev
    record["entry_hash"] = entry_hash
    line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    entry = LedgerEntry(case, prev_hash=prev, entry_hash=entry_hash)
    ledger.entries.append(entry)
    return entry


__all__ = [
    "GENESIS_PREV_HASH",
    "Case",
    "CorpusIntegrityError",
    "Ledger",
    "LedgerEntry",
    "append_case",
    "canonical",
    "compute_entry_hash",
    "gradeable_view",
    "load_ledger",
    "verify_chain",
]
