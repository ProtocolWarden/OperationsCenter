# Documentation structure

Where a document goes, and why. If you are adding a doc and cannot place it from
this page, the page is wrong — fix it rather than inventing a new directory.

## The rule

Sort by **what the reader wants**, not by what produced the document.

| Reader wants | Directory | Contents |
|---|---|---|
| To run or operate the system | `operator/` | Runbooks, setup, diagnostics, recovery, tuning. Answers "how do I run this / it broke, now what". |
| To use a feature | `guides/`, `user-guides/` | Task-oriented walkthroughs for a specific capability. |
| To work on OC itself | `dev/` | Contributing to the codebase: test strategy, local workflow. Distinct from `operator/`, which is about running it. |
| To look something up | `reference/` | Stable, enumerable facts: APIs, metrics, error codes. |
| To know why it is built this way | `architecture/` | Structure and decisions. `adr/` for decision records, `contracts/` for cross-repo interfaces. |
| To understand a subsystem's design | `design/` | Live design docs for things that exist or are being built. |
| A precise change definition | `specs/` | Scoped work definitions, usually authored for the fleet. |
| To know what we used to do | `history/` | Superseded material, kept for provenance. Never edited to stay current. |

## `history/` is a graveyard, not an attic

Anything under `history/` is **not maintained** and may be wrong. It is kept
because knowing what was tried, and why it was abandoned, is expensive to
reconstruct. Do not update it to reflect current behaviour — supersede it and
leave the original intact.

- `history/stages/` — per-stage work artifacts from a single change (STAGE_1_DESIGN,
  VERIFICATION_REPORT_*, TEST_RESULTS). Point-in-time records of one task.
- `history/audits/` — completed audit reports.
- `history/development-log/` — narrative development records.
- `history/migration/`, `history/managed-repo/` — migration and per-repo history.

**Work artifacts do not belong outside `history/`.** A file named for a stage,
a verification, or a test run is a record of an episode, not documentation. It
goes in `history/stages/` the moment the work lands.

## Naming

- Lowercase with hyphens: `pr-review.md`, not `PR_REVIEW.md`.
  (Existing SCREAMING_CASE files predate this and are being converted opportunistically —
  do not add more.)
- Name for the subject, not the process: `flaky-test-reporter.md`, not `STAGE2_IMPL.md`.
- ADRs keep their `NNNN-slug.md` numbering; that ordering is the point.

## Status headers

A doc describing something not yet true must say so in its first lines:

```
Status: SPEC | DRAFT | SHIPPED | SUPERSEDED by <path>
```

90 documents already carry one. Absence means "describes current behaviour" —
so if it does not, add the header rather than letting a reader trust it.

## One subject, one home — but not one document

The rule is **no duplication**, not "everything about X in one file".

A substantial feature legitimately has a walkthrough in `guides/`, a lookup table
in `reference/`, and a rationale in `design/` — that is the reader-intent split
above working as intended, not fragmentation. Coverage alerting is the worked
example: ~6,800 lines across `guides/` (4), `reference/` (1), `design/` (2) and
`architecture/ci/` (1), each correctly placed.

What that split costs is *discoverability*: nothing tells a reader the other six
documents exist. The fix is a hub entry in [`_toc.md`](_toc.md) listing the parts
in reading order — not merging them, and not copying content between them.

Genuine violations look different: the same fact stated in two places, which will
drift. When you find one, pick the home the reader-intent table dictates and make
the other a link.

## Related

- [`_toc.md`](_toc.md) — index of what exists
- [`README.md`](README.md) — entry point
