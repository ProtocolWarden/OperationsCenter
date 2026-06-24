---
status: implemented
---

# Code-Failure Retry Cap

**Status:** implemented. Closes the loop identified in the 3-round adversarial
review of [Lineage Steering Consumer](./LINEAGE_STEERING_CONSUMER.md) §1 — the
*right* fix for the real bug that review surfaced.

## Problem

`retry-count` is incremented **only on SIGKILL** (`outcomes.py` handle_failure,
`dispatch.py` kill path). A *clean* code failure — the executor exits normally
with `success=false` and `failure_category=validation_failed` (tests/lint failed)
— never touches the counter. So:

- `_MAX_FOLLOW_UP_RETRIES` and board_unblock's Rule-1 cancel (both count-gated)
  never arm for code failures.
- board_unblock Rules 3/8 recycle the Blocked task back to Ready (the
  executor-exit-code exclusion doesn't fire for clean failures).

**Net:** a task that fails the same way forever has no terminal — it loops and
drains the shared execution budget that gates proposals for healthy work.

## Why NOT the convergence-stall / lineage brake (rejected)

The adversarial review rejected routing this through `detect_convergence_stall` /
`ProposalRejectionStore`: that store is a **permanent, human-semantic veto** (no
TTL, checked first as a permanent human "no"), the `failure_category` trigger is
agent-gameable and was the *misclassified* root of the founding ~190× incident,
and escalating a recurring per-task correction to a human violates the
self-healing invariant. See the decision record. This design instead arms the
**existing self-healing, expiring** count cap — no permanent veto, no human
escalation, no lineage machinery.

## Design

A dedicated `code-fail-count:` label counter, parallel to `retry-count:`:

1. **Producer** — `handle_failure` increments `code-fail-count` when a CLEAN
   (no-signal) failure has a category in `_CODE_FAILURE_CATEGORIES`. Default set:
   the two failure_category values "validation_failed" and "no_changes" — the
   canonical "the approach isn't working and won't converge by re-running the same
   thing" cases. Deliberately EXCLUDES transient/env categories (backend_error,
   timeout, budget_exhausted, routing_error, conflict — self-resolving or handled
   by convergence-stall), scope_too_wide (handled by the split path), and unknown
   (too ambiguous — the founding incident was a misclassified env fault, so we
   never terminate on the unknown category). Configurable.

2. **Terminal** — board_unblock Rule 1 gains a third cancel condition:
   `code-fail-count >= settings.code_failure_retry_cap`. It runs in the same
   already-non-terminal pass that cancels SIGKILL-exhausted tasks, so it fires
   while the task is Blocked, BEFORE Rule 8 would re-promote it. Cancel → the task
   leaves the loop and frees the budget.

3. **N** — `Settings.code_failure_retry_cap: int = 3` (0 = disabled). Default 3
   matches the existing board caps. See header for the tradeoff.

## Why this is self-healing (not a veto)

Cancel sets state `Cancelled` — it does NOT write `ProposalRejectionStore` and
does NOT escalate to the operator ledger. The proposer may re-raise the work
after its dedup window if it is still relevant; if the underlying cause was fixed
meanwhile (new model, dependency bump, fixed flaky check), the next attempt
succeeds. So the loop is bounded to N attempts *per proposal cycle*
(self-healing), not forbidden forever (the rejected veto). This satisfies the
self-healing invariant: no human in the per-correction loop.

## Honest residuals

- **Coarse, not same-signature.** Like the SIGKILL cap, this counts ANY code
  failure in the set, not N-identical. A genuinely-progressing task (each attempt
  fixing a different sub-bug, still red) can be cancelled at N. N=3 gives margin;
  same-signature grouping was deliberately rejected as complexity (and is
  agent-gameable). Accepted tradeoff.
- **Agent-influenceable category.** `failure_category` is classified from agent
  stdout; an agent could force early self-cancel (self-DoS, operator-visible) or
  vary its category to evade. The conservative default set + cancel-not-veto
  bounds the blast radius to the agent's own task.
