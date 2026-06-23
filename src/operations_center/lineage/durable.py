# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/durable.py — the durable lineage tier (Phase A5).

The projection reads signals that the fleet GCs at ~44 days, so a rebuild past
that horizon yields a smaller graph than the live one — "derived ⇒ can't drift"
is false once source lifetime < projection lifetime (spec §1.3). This tier closes
that: lineage entries are appended to a git-trackable JSON ledger whose retention
is independent of the source, so an edge backed by the durable tier stays
``completeness=durable`` even after its source record is GC'd.

It wraps the D1 ``LineageLedger`` (hash chain + authorship binding), so a durable
entry is also tamper-evident and authorship-bound — the two properties an edge
needs before it may ever be steerable.

Writes are atomic (tmp + ``os.replace``). The store never deletes; trimming is an
operator decision, not the fleet's.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from .integrity import LedgerEntry, LineageLedger

_DEFAULT_PATH = Path("state") / "lineage" / "ledger.jsonl"


class DurableLineageStore:
    """Append-only, on-disk lineage ledger backing the durable tier."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_PATH
        self._ledger = self._load()

    # ── load / persist ────────────────────────────────────────────────────────
    def _load(self) -> LineageLedger:
        ledger = LineageLedger()
        if not self.path.is_file():
            return ledger
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                # Reconstruct each entry VERBATIM (preserving the stored prior/
                # this hashes) rather than re-appending — re-appending would
                # recompute the chain and silently re-bless a tampered payload,
                # defeating tamper-evidence. verify() then recomputes from the
                # payload and compares to the stored hash, so a mutated line is
                # caught. A malformed line is skipped, not fatal (§0.1).
                entry = LedgerEntry(
                    lineage_id=rec["lineage_id"],
                    author=rec["author"],
                    payload=rec["payload"],
                    prior_hash=rec["prior_hash"],
                    this_hash=rec["this_hash"],
                )
                ledger.entries.append(entry)
                ledger._owner.setdefault(entry.lineage_id, entry.author)
                ledger._tip[entry.lineage_id] = entry.this_hash
            except Exception:  # noqa: BLE001 — a bad line must not break load
                continue
        return ledger

    def _persist_entry(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True)
        # Append durably: read-existing + rewrite via atomic replace so a crash
        # mid-write can't leave a torn last line that breaks the next load.
        existing = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(existing + line + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    # ── public API ────────────────────────────────────────────────────────────
    def append(self, lineage_id: str, author: str, payload: dict) -> LedgerEntry:
        """Append an entry (authorship-bound, hash-chained) and persist it.

        Raises ``AuthorshipError`` if ``author`` does not own ``lineage_id`` — the
        forged write is neither chained nor persisted.
        """

        entry = self._ledger.append(lineage_id, author, payload)
        self._persist_entry(entry)
        return entry

    def verify(self) -> bool:
        return self._ledger.verify()

    def durable_lineage_ids(self) -> set[str]:
        """The set of lineage_ids the durable tier vouches for — but only if the
        whole chain verifies. A tampered ledger vouches for NOTHING (returns an
        empty set), so a corrupted durable tier degrades to source-age semantics
        rather than falsely marking edges durable."""

        if not self._ledger.verify():
            return set()
        return {e.lineage_id for e in self._ledger.entries}

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._ledger.entries)


def lineage_id_for_task(task_id: str) -> str:
    """The lineage_id the fleet derives for a task (mirrors outcomes.py)."""

    return f"lin-{task_id[:12]}"


__all__ = ["DurableLineageStore", "lineage_id_for_task"]
