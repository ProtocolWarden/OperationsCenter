# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/integrity.py — hash chain + authorship binding (Phase D1).

The spec (§2, §3 surface 9) makes integrity a hard prerequisite for any lineage
edge to ever become *steerable*: today lineage records are unsigned, unchained,
and not authorship-bound, so a compromised lane could write false lineage about
ANOTHER lane's work and steer the fleet from a forged diary.

This module provides the missing integrity stack, mirroring the eval ledger's
model:

  * **Hash chain** — each entry commits to the prior entry's hash, so tampering
    with any historical entry is detectable on ``verify()``.
  * **Authorship binding** — the first writer of a ``lineage_id`` establishes its
    owner; a later append from a different author is REJECTED (quarantined), so a
    lane cannot mutate another lane's lineage.

It is deliberately decoupled from the projection: the projection reads on-disk
signals and labels edges ``unverified``; a future durable tier (A5) will append
to this ledger and only THEN may an edge be upgraded to ``chained``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .models import Integrity, TrustFlags

_GENESIS = "0" * 64


def entry_hash(prior_hash: str, *, lineage_id: str, author: str, payload: dict) -> str:
    """Deterministic SHA-256 over (prior_hash, lineage_id, author, payload).

    Canonical JSON (sorted keys) so the hash is stable across processes.
    """

    blob = json.dumps(
        {"prior": prior_hash, "lineage_id": lineage_id, "author": author, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuthorshipError(ValueError):
    """Raised when a lane tries to write to a lineage it does not own."""


@dataclass(frozen=True)
class LedgerEntry:
    lineage_id: str
    author: str
    payload: dict
    prior_hash: str
    this_hash: str


@dataclass
class LineageLedger:
    """Append-only, per-lineage hash-chained ledger with authorship binding."""

    entries: list[LedgerEntry] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    _owner: dict[str, str] = field(default_factory=dict)
    _tip: dict[str, str] = field(default_factory=dict)

    def owner_of(self, lineage_id: str) -> str | None:
        return self._owner.get(lineage_id)

    def append(self, lineage_id: str, author: str, payload: dict) -> LedgerEntry:
        """Append an entry. The first author of a lineage owns it; a mismatched
        author is rejected (and recorded in ``quarantined``) — never silently
        accepted, so a forged cross-lane write cannot enter the chain."""

        owner = self._owner.get(lineage_id)
        if owner is None:
            self._owner[lineage_id] = author
        elif owner != author:
            record = {"lineage_id": lineage_id, "author": author, "payload": payload}
            self.quarantined.append(record)
            raise AuthorshipError(
                f"author {author!r} may not write lineage {lineage_id!r} owned by {owner!r}"
            )
        prior = self._tip.get(lineage_id, _GENESIS)
        h = entry_hash(prior, lineage_id=lineage_id, author=author, payload=payload)
        entry = LedgerEntry(lineage_id, author, dict(payload), prior, h)
        self.entries.append(entry)
        self._tip[lineage_id] = h
        return entry

    def verify(self) -> bool:
        """Recompute every per-lineage chain; return False if any link is broken
        (an entry's stored hash or prior-link does not match recomputation)."""

        tip: dict[str, str] = {}
        for entry in self.entries:
            prior = tip.get(entry.lineage_id, _GENESIS)
            if entry.prior_hash != prior:
                return False
            expected = entry_hash(
                prior,
                lineage_id=entry.lineage_id,
                author=entry.author,
                payload=entry.payload,
            )
            if entry.this_hash != expected:
                return False
            tip[entry.lineage_id] = entry.this_hash
        return True


def chained_trust(base: TrustFlags) -> TrustFlags:
    """Upgrade a trust label's integrity dimension to CHAINED.

    Only call this for an edge whose backing ledger entry exists and whose
    ``ledger.verify()`` is True and whose author equals the lineage owner. It is
    the single sanctioned way the integrity dimension goes green.
    """

    return TrustFlags(
        provenance=base.provenance,
        integrity=Integrity.CHAINED,
        completeness=base.completeness,
        order=base.order,
    )


__all__ = [
    "AuthorshipError",
    "LedgerEntry",
    "LineageLedger",
    "chained_trust",
    "entry_hash",
]
