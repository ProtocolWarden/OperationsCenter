# Documentation structure

Where a document goes, and why. If you are adding a doc and cannot place it from
this page, the page is wrong — fix it rather than inventing a new directory.

## The rule

Sort by **what the reader wants**, not by what produced the document.

| Reader wants | Directory | Contents |
|---|---|---|
| To run or operate the system | `operator/` | Runbooks, setup, diagnostics, recovery, tuning. Answers "how do I run this / it broke, now what". |
| To use a feature | `guides/`, `user-guides/` | Task-oriented walkthroughs for a specific capability. |
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

## One subject, one home

If a feature's documentation spans four directories, the reader cannot find it
and the copies will drift apart. Prefer one document per subject with sections,
and link to it from elsewhere rather than restating it.

## Related

- [`_toc.md`](_toc.md) — index of what exists
- [`README.md`](README.md) — entry point
