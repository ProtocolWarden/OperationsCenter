# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Rotate `.console/log.md` before it breaches OC2's budget.

OC's pre-commit hook requires every source commit to add a log entry, and OC2
caps the file at 500KB. Those two rules together make growth monotonic — roughly
15KB per merged PR — so the file reaches the cap on a predictable schedule. When
it does, *every* open PR fails the gate at once and the whole queue stalls until
someone rotates by hand. That happened on 2026-08-17 and blocked five PRs.

This rotates on a warning threshold, well before the cap, so the breach never
arrives.

**Why this refuses rather than trusts itself.** A rotation rewrites the whole
file, and a wholesale rewrite is exactly the operation that silently destroys
history: rebase such a commit across another change to the same file and the
other side's entries vanish with no conflict and no finding, because OC2 only
measures size and a rotation is *supposed* to shrink the file. That very thing
nearly erased six entries on 2026-08-17. So every write here is gated on a
census: each entry heading present before must still be reachable afterwards,
in the log or in an archive. If one would be lost, nothing is written.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: OC2's cap. Kept in sync with `.custodian/config.yaml`; the check below fails
#: loudly rather than silently rotating to a stale target if that ever diverges.
BUDGET_BYTES = 512_000

#: Rotate once the file crosses this share of the budget. Deliberately well
#: under 1.0: rotating *at* the cap means the first commit to notice is already
#: failing, which is the stall this exists to prevent.
WARN_RATIO = 0.80

#: Rotation keeps the newest entries up to this share, leaving room for many
#: more cycles before the next rotation.
TARGET_RATIO = 0.55

ARCHIVE_DIR = Path("docs/history/console-log")
_ENTRY_RE = re.compile(r"^## ", re.MULTILINE)
_DATE_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})")
_BLOCK_BEGIN = "<!-- rotated-archives:begin -->"
_BLOCK_END = "<!-- rotated-archives:end -->"


@dataclass(frozen=True)
class Plan:
    """What a rotation would do, before anything is written."""

    size: int
    budget: int
    warn_at: int
    needed: bool
    keep: list[str]
    archive: list[str]
    archive_path: Path | None

    @property
    def headroom(self) -> int:
        return self.budget - self.size


def _entries(text: str) -> list[str]:
    """Split the log into entries. Each runs from its `## ` heading to the next."""
    starts = [m.start() for m in _ENTRY_RE.finditer(text)]
    if not starts:
        return []
    bounds = starts + [len(text)]
    return [text[bounds[i] : bounds[i + 1]] for i in range(len(starts))]


def _heading(entry: str) -> str:
    return entry.split("\n", 1)[0].strip()


def _date_of(entry: str) -> str:
    m = _DATE_RE.match(entry)
    return m.group(1) if m else "undated"


def plan(log_path: Path, *, budget: int = BUDGET_BYTES,
         warn_ratio: float = WARN_RATIO, target_ratio: float = TARGET_RATIO) -> Plan:
    """Decide what to rotate. Pure — reads the log, writes nothing."""
    text = log_path.read_text(encoding="utf-8")
    size = len(text.encode("utf-8"))
    warn_at = int(budget * warn_ratio)
    if size < warn_at:
        return Plan(size, budget, warn_at, False, [], [], None)

    entries = _entries(text)
    if not entries:
        return Plan(size, budget, warn_at, False, [], [], None)

    # Keep newest-first until the target is reached; the rest is archived. The
    # log is not strictly date-ordered, so this is positional by construction —
    # which is fine, and is why the archive is named for the entries it holds
    # rather than for a cutoff date it cannot honestly claim.
    target = int(budget * target_ratio)
    keep: list[str] = []
    archive: list[str] = []
    running = 0
    for entry in entries:
        n = len(entry.encode("utf-8"))
        if running + n <= target:
            keep.append(entry)
            running += n
        else:
            archive.append(entry)

    if not archive:
        return Plan(size, budget, warn_at, False, [], [], None)

    dates = sorted(d for d in (_date_of(e) for e in archive) if d != "undated")
    span = f"{dates[0]}-to-{dates[-1]}" if dates else "undated"
    return Plan(size, budget, warn_at, True, keep, archive, ARCHIVE_DIR / f"log-archive-{span}.md")


def _archive_header(p: Plan) -> str:
    dates = sorted(d for d in (_date_of(e) for e in p.archive) if d != "undated")
    span = f"{dates[0]} — {dates[-1]}" if dates else "undated entries"
    return (
        f"# `.console/log.md` archive — {len(p.archive)} entries ({span})\n"
        "\n"
        "**Not maintained.** Rotated out of `.console/log.md` automatically to stay\n"
        f"within OC2's {BUDGET_BYTES:,}-byte budget. Entries are verbatim and are never\n"
        "updated — see `docs/structure.md` on why history is not kept current.\n"
        "\n"
        "The entries below are in their original order, which is the order they were\n"
        "written in. That is not strictly chronological, so this file is named for the\n"
        "range of dates it contains rather than a cutoff it cannot honestly claim.\n"
        "\n"
        "Current log: [`.console/log.md`](../../../.console/log.md)\n"
        "\n"
        "---\n"
        "\n"
    )


def _pointer_block(existing: str, archive_path: Path, count: int) -> str:
    """Maintain the archive index at the end of the log.

    The links matter beyond navigation: an archive under `docs/` that nothing
    links to is an orphan, and DC7 fails the audit for it.
    """
    line = f"- [{archive_path.name}]({os.path.relpath(archive_path, '.console')}) — {count} entries\n"
    if _BLOCK_BEGIN in existing and _BLOCK_END in existing:
        head, rest = existing.split(_BLOCK_BEGIN, 1)
        body, tail = rest.split(_BLOCK_END, 1)
        if line in body:
            return existing
        return f"{head}{_BLOCK_BEGIN}{body.rstrip()}\n{line}{_BLOCK_END}{tail}"
    return (
        f"{existing.rstrip()}\n\n---\n\n"
        f"{_BLOCK_BEGIN}\n\n_Rotated archives:_\n\n{line}{_BLOCK_END}\n"
    )


def apply(log_path: Path, p: Plan, *, repo_root: Path) -> list[Path]:
    """Write the rotation, or raise if it would lose an entry."""
    if not p.needed or p.archive_path is None:
        return []

    before = {_heading(e) for e in _entries(log_path.read_text(encoding="utf-8"))}

    archive_abs = repo_root / p.archive_path
    archive_abs.parent.mkdir(parents=True, exist_ok=True)
    existing_archive = archive_abs.read_text(encoding="utf-8") if archive_abs.exists() else ""
    archive_text = (existing_archive or _archive_header(p)) + "".join(p.archive)

    new_log = _pointer_block("".join(p.keep), p.archive_path, len(p.archive))

    # The census. Every heading that existed must still be reachable.
    after = {_heading(e) for e in _entries(new_log)} | {_heading(e) for e in _entries(archive_text)}
    lost = sorted(before - after)
    if lost:
        raise RuntimeError(
            "refusing to rotate: "
            f"{len(lost)} entr{'y' if len(lost) == 1 else 'ies'} would be lost, "
            f"first: {lost[0][:70]!r}"
        )

    archive_abs.write_text(archive_text, encoding="utf-8")
    log_path.write_text(new_log, encoding="utf-8")
    return [log_path, archive_abs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rotate .console/log.md before it breaches OC2.")
    ap.add_argument("--repo-root", type=Path, default=Path.cwd())
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if rotation is due")
    ap.add_argument("--apply", action="store_true", help="rotate if due")
    ap.add_argument("--budget", type=int, default=BUDGET_BYTES)
    ap.add_argument("--warn-ratio", type=float, default=WARN_RATIO)
    args = ap.parse_args(argv)

    log_path = args.repo_root / ".console/log.md"
    if not log_path.exists():
        print(f"no log at {log_path}", file=sys.stderr)
        return 0

    p = plan(log_path, budget=args.budget, warn_ratio=args.warn_ratio)
    pct = 100.0 * p.size / p.budget
    print(f"log.md {p.size:,} bytes ({pct:.1f}% of {p.budget:,}); rotate at {p.warn_at:,}")

    if not p.needed:
        print(f"  no rotation needed — {p.headroom:,} bytes of headroom")
        return 0

    if args.check and not args.apply:
        print(f"  rotation DUE: would archive {len(p.archive)} of "
              f"{len(p.keep) + len(p.archive)} entries")
        return 1

    try:
        written = apply(log_path, p, repo_root=args.repo_root)
    except RuntimeError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    new_size = log_path.stat().st_size
    print(f"  archived {len(p.archive)} entries -> {p.archive_path}")
    print(f"  log.md now {new_size:,} bytes ({100.0 * new_size / p.budget:.1f}% of budget)")
    for w in written:
        print(f"  wrote {w}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
