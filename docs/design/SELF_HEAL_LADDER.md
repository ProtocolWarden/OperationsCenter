---
status: implemented
---

# Self-Heal Ladder for CONCERNS verdicts

**Status:** built — Phases 0–3 shipped (see roadmap below)
**Owner:** pr_review_watcher
**Origin:** PR #313 post-mortem — a fleet-authored PR shipped broken because the
self-heal loop was binary (one shallow fix pass → give up to a human) and the
merge then bypassed the binding verdict on green CI. Governance was fixed in
\#314 (verdict-gate, CI-green guard) and the planner-side catch in #319/#320
(Custodian D12/DC10 gates). This document covers the remaining gap: making the
**fix loop itself** strong enough to actually *resolve* a CONCERNS verdict
instead of conceding early.

## The binding invariant (non-negotiable)

> Self-heal means **resolve the concern**, never **bypass it to reach green CI**.

That is precisely how #313 shipped, and #314 exists to prevent it. Everything
below stays **behind** the binding verdict:

- **LGTM is the only merge path.** Nothing in the ladder merges a PR. A PR
  leaves the loop merged only when a fresh self-review returns LGTM.
- The CI-green retraction guard (`_concerns_on_this_head`, #314) is untouched.
  The ladder never reads CI status as permission to merge over a concern.
- The ladder adds **rungs of increasing resolving power** between "first fix
  pass" and "give up to a human". It changes how *hard the system tries*, not
  *what counts as resolved*.

## What was wrong with the binary loop

`_phase1` on a CONCERNS verdict did exactly one thing per attempt: hand
`_run_fix_pass` the reviewer's prose summary plus "resolve ALL of them, run the
tests and linters." Two structural weaknesses:

1. **The acceptance bar was wrong.** The fix worker was told tests/linters
   passing is the goal. But #313's defect was a symbol that was unit-tested in
   isolation and never wired into production — tests *passed* while the
   integration was absent. "Tests pass" is necessary, not sufficient.

2. **The escalation was binary and premature.** On the first no-progress repeat
   (same PR head, fix pass pushed nothing) the loop escalated straight to a
   human. It never applied *more* resolving power — more context, a narrower
   per-concern scope — before conceding. A single shallow pass that couldn't
   resolve a multi-part concern ended the autonomous track immediately.

## The ladder

Each fix attempt runs at a **strategy level**. Level rises only when a pass
fails to make progress (pushed nothing) or fails to converge (pushed, but a
fresh review raises the same concern). A human is the **top** of the ladder,
not the second rung.

| Level | Strategy | What changes vs the level below |
|------|----------|---------------------------------|
| L0 | Standard fix pass | Structured, enumerated concerns + anti-no-op acceptance bar (Phase 1) |
| L1 | Enriched context | + the full PR diff + an explicit "the previous pass changed nothing / did not resolve X — take a different approach" |
| L2 | Decompose | One narrowly-scoped fix pass **per concern** instead of one pass for all — narrower scope, higher resolve rate |
| L3 (terminal) | Human / rescope | Escalate to a human; on fix-cap exhaustion, close + re-queue carrying the **unresolved** concerns so the fresh attempt is scoped to them |

The level is tracked in watcher state (`fix_strategy_level`) and bounded by
`ReviewerSettings.max_fix_strategy_level` (default 2 — L0..L2 autonomous, then
human). Every rung still ends in a fresh self-review; only LGTM merges.

## Acceptance bar handed to every fix pass

The fix worker is told, explicitly:

- Resolve **each** enumerated concern; a no-op pass is a failure.
- Tests and linters passing is **necessary but not sufficient**. If a concern
  is "X is defined/tested but never called in production", you must connect X
  to its production call path and show where it is invoked — you must **not**
  resolve it by adding another test.
- Before finishing, run the repository's own incomplete-integration gate
  (`custodian-multi --only D12,DC10 --include-deprecated --fail-on-findings`)
  and clear any finding it reports. This is the same deterministic gate CI
  runs; clearing it locally keeps the loop from converging on a state CI will
  reject.

## Roadmap (phases)

- **Phase 0 — this document.** ✅ Design, invariant, ladder, acceptance bar.
- **Phase 1 — Structured concern delivery + anti-no-op acceptance bar.** ✅
  `_structure_concerns()` parses the reviewer summary into an enumerated concern
  list; `_build_fix_goal()` rewrites the fix goal with `_FIX_ACCEPTANCE_BAR`
  (tests-necessary-not-sufficient; wire don't re-test; clear the D12/DC10 gate).
  No state-machine change.
- **Phase 2 — The ladder.** ✅ `fix_strategy_level` state +
  `ReviewerSettings.max_fix_strategy_level`; on no-progress the fix pass is
  re-dispatched with `_ladder_enrichment()` (L1 enriched context, L2 decompose
  per concern) instead of escalating; a human is reached only at the top rung.
- **Phase 3 — Rescope on exhaustion.** ✅ `_close_and_requeue(concerns=…)` →
  `_requeue_plane_task()` carries the still-unresolved concerns (enumerated)
  onto the re-queued task so the next attempt is scoped to what remained.

Each phase shipped as a commit on one green-gated PR. None touches the merge
gate; all keep LGTM as the sole merge path.

## Test coverage the build must keep / add

- Keep: `test_phase1_concerns_dispatches_fix_pass_below_cap`,
  `test_run_fix_pass_noop_returns_false`,
  `test_phase1_concerns_closes_and_requeues_at_fix_cap`,
  `test_wo3_no_progress_after_retraction_*` (the binding-invariant guards).
- Change: `test_phase1_repeated_concerns_after_noop_fix_escalates` — a single
  no-progress repeat now climbs a rung; the human escalation asserts at ladder
  top instead.
- Add: a structured-concerns parse test; a ladder-climb test (no-progress
  bumps `fix_strategy_level` and re-dispatches, does not escalate); a
  ladder-top test (escalates to human only when the level is maxed); a
  rescope test (re-queued task carries unresolved concerns).
