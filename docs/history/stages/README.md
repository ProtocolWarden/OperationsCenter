# Stage artifacts — archived

**These documents are not maintained and may be wrong.** They are point-in-time
records of individual pieces of work: the analysis written before a stage, the
verification written after it, the test results captured on the day.

They were moved here from the **repository root** on 2026-08-17, where 18 of them
had accumulated alongside `README.md` and `CONTRIBUTING.md`. Nothing in the source
tree, and no other document, linked to any of them — the only inbound references
were entries in `.console/log.md` recording that the work happened, and links
between these files themselves. Those sibling links were preserved by moving the
group intact.

## Why they are kept

A stage report is the cheapest available answer to "why is it like this, and what
was already tried". Reconstructing that from the diff is expensive. They are not
deleted for the same reason `history/audits/` is not deleted.

## Why they are not documentation

They describe an *episode*, not the system. A reader wanting to know how something
works today must not land here — which is exactly what happened while these sat in
the root, the first thing anyone sees when opening the repository.

If you need the current behaviour, start at [`docs/_toc.md`](../../_toc.md).

## Contents

Four unrelated pieces of work are represented:

- **`STAGE_*`** — a numbered stage sequence (analysis → design → validation rules →
  implementation → verification → deployment), with several verification reports.
- **`VERIFICATION_REPORT_STAGE*`** — verification passes, including a separate mypy run.
- **`AUDIT_STAGE_0_FINDINGS.md`** — a stage-0 audit finding set.
- **`BOUNDARY_B1_B2_*`** — boundary investigation and secret-refresh evidence.
- **`TEST_RESULTS.md`, `IMPLEMENTATION_VERIFICATION_SUMMARY.md`** — captured runs.

Do not add to this directory by hand. Work artifacts land here when their change
merges; if you are writing one now, it belongs here from the start rather than in
the repository root.
