# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

Flow-hygiene work order — close the five lifecycle gaps found in the
2026-06-07 full PR-history audit (243 PRs reviewed; findings in log.md).
Operator-directed. Work each item to green via the normal branch→PR→review
flow, one PR per item where practical.

## Overall Plan

- **WO-1**: Close-with-receipt invariant — no bot close without a durable receipt
- **WO-2**: Drive resurrected PRs #249/#250 to green
- **WO-3**: Self-retracting reviewer verdicts
- **WO-4**: Orphan-branch detector
- **WO-5**: Spec-author title + dedup hygiene

## Current Stage

WO-1 — not started. Items are independent; WO-2 can proceed in parallel
whenever review-watcher capacity allows.

## Work Items

### WO-1: Close-with-receipt invariant (highest value)

Any automated PR close MUST leave a durable receipt: create/update a Plane
task linking the PR number, head ref (`refs/pull/<n>/head` survives branch
deletion), and associated spec file — OR the close comment must explicitly
state "no salvage value" with a one-line justification. Never delete a
branch whose close comment claims work is preserved on it.

Evidence: #235 closed 2h after "work preserved / re-queued" with no requeue
(implementation recovered by operator as PR #250); #227–#233 closed with
"spec file preserved in the branch" then the branches were deleted.

- [ ] Implement in the watchdog/review close paths (wherever `gh pr close`
      or close decisions are emitted)
- [ ] Unit-test: close without receipt is rejected/blocked
- [ ] Backfill: audit the 34 closed-unmerged PRs for unreceipted salvage
      (operator already recovered #235 and the t8 orphan branch → #249/#250)

### WO-2: Drive the resurrected PRs to green

- [ ] PR #250 (verdict consolidation, resurrects #235): assess remaining
      spec-compliance gap vs docs/specs/queue-drain-20260602T234758.md
      (18–23 integration tests specified) and complete it
- [ ] PR #249 (t8 orphan recovery): review for redundancy against main's
      merged R1/R2 tests (#244); merge what's net-new, drop what's duplicate
- [ ] After #249 merges: delete superseded branch improve/d43ac217

### WO-3: Self-retracting reviewer verdicts

When the reviewer posts "Needs human attention" / "Self-review concerns"
and the blocking condition later clears (CI green, PR merged, or superseding
fix lands), it must update or strike its own comment. Stale flags on merged
PRs caused operator confusion (5 found: #234, #243–#246; retracted manually).

- [ ] Track posted-flag state per PR; clear-on-condition in the review sweep
- [ ] Also retract when the PR is closed with a receipt (WO-1)

### WO-4: Orphan-branch detector

Remote branch with commits ahead of main + no open PR + older than 24h →
escalate (Plane task or watchdog finding). Candidate: custodian detector or
watchdog STEP-2 check.

Evidence: oc-watchdog/20260607-0340-t8 (~2,089 lines, no PR — recovered as
#249) and improve/d43ac217 (task marked Done, branch unmerged, no PR).

- [ ] Implement + test
- [ ] First sweep: verify no further orphans exist

### WO-5: Spec-author hygiene

- [ ] PR titles: derive from spec title/content — never the literal task
      header ("# Spec authoring task" shipped as the title of 16 merged PRs)
- [ ] Dedup gate: before minting a new spec, check open/recently-closed
      specs for the same target (7 queue-drain specs minted on 2026-06-02
      alone; 14 spec-author PRs closed unmerged)

### WO-6: Reviewer planning isolation (partially shipped)

The reviewer's planning subprocess imports `operations_center` from
`oc_root/src` — the shared, mutable live checkout. A concurrent session leaving
a dirty/conflicted tree crashes planning at import for EVERY PR (2026-06-07
~4h outage; root cause of #245/#246 hand-merges + #247 stuck-green).

- [x] Pre-flight conflict-marker guard + distinct ENVIRONMENT classification
      (OCSourceTreeUncleanError) so it doesn't burn the no-verdict budget and
      escalates with the specific cause — shipped (fix/reviewer-clean-tree-guard, #251)
- [x] Proactive sweep ordering: merge-ready PRs before slow fix loops so a
      quick LGTM isn't starved behind a multi-pass battle — shipped (#252)
- [x] Conflict-magnet fix: `.console/log.md merge=union` so concurrent PRs
      don't all go CONFLICTING on every sibling merge — shipped (on main)
- [x] Reviewer auto-rebase — shipped (#254, adversarially designed). LAZY (fires
      only at LGTM→merge), CI-backstopped (clean rebase pushed but not merged that
      cycle; CI + next review re-validate), never force-pushes, real conflict →
      escalate, rebase_attempts orthogonal to fix_attempts, 120s grace. Live-pipeline
      validation pending: confirm a real CONFLICTING PR self-clears once the watchers
      run main's code (oc_root must sync to main first — currently on a loop branch).
- [ ] Deeper isolation: run planning/execute against a clean dedicated git
      worktree pinned at the merge ref, NOT the shared mutable checkout. Needs
      the live pipeline (SwitchBoard + backends) to validate — can't be tested
      offline. This removes the shared-tree fragility class entirely.
- [ ] Distinguish crash-from-verdict in the retry budget generally (a transient
      backend/rate-limit no-verdict should retry later, not exhaust the budget
      and park a good PR — same principle as the env-unclean path)
- [ ] Stuck-green escalation: a PR green on CI but unmerged for >N sweeps with
      repeated reviewer failures should raise a loud, specific alarm (ties to
      WO-1's close-with-receipt and WO-3's self-retracting verdicts)

## Definition of Done

- All six items implemented, tested, merged to green main
- Each lands via its own branch + PR through the review gate
- Backfill sweeps (WO-1, WO-4) documented in the cycle summary
- .console/log.md gets one line per completed item
