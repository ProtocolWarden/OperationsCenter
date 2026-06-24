---
status: decided
---

# Lineage Steering Consumer — Decision Record (won't-build)

**Status:** DECIDED — won't-build, after THREE adversarial rounds. Two rounds
refuted every framing of "a lane plans from lineage" (v1 LLM-prompt injection, v2
standalone deterministic policy). A third round attacked the one surviving option
— a code-failure convergence brake — from both sides and resolved it: the
underlying loop is real, but the proposed mechanism is invariant-incompatible; the
fix is a small self-healing retry-cap change, NOT lineage. See §1. The lineage
read-model stays display-only (its correct current state).

## 0. What the two rounds established

**Round 1 killed the LLM-prompt framing (v1):**
- *Data discontinuity (FATAL):* retries spawn new task_ids; the producer keyed on
  task_id and wrote only on success → the chain was empty exactly in the
  remediation case.
- *Trusted-channel injection (SEV-1):* `head_sha` (attacker-pushed) and
  `failure_category` (classified from agent stdout) would enter the TRUSTED prompt
  region, bypassing `wrap_untrusted_goal`. "Attested" attests the edge, not the
  field value.
- *No mechanized benefit:* typed enums/check-ids don't change an LLM's plan; the
  free-text "why" that would is forbidden.

**Round 2 killed the standalone-policy framing (v2):**
- *It's ~90% already built.* `entrypoints/maintenance/detect_convergence_stall.py`
  (hourly via `operations-center.sh`) already: groups repeated failures by typed
  category, escalates to the operator ledger (`cl ledger capture`), and suppresses
  re-proposal (`ProposalRejectionStore`). v2 §3's three actions are that file.
- *The Self-Heal Ladder + 4 retry caps + the per-root descendant cap* already
  cover the within-PR and count-based cases; `lineage-root` is already a
  first-class throttling key (`work_ceiling.open_descendants_of_root`).
- *v2 introduced new harm:* writing the durable ledger on every dispatch is an
  O(n²) scaling landmine (full reload+verify under flock per append, never
  trimmed); escalate-instead-of-run leaks the Running slot (the prior R2 bug
  class); per-root authorship is theater over an already-closed sandbox boundary
  AND breaks writes vs the old producer; the signature key over-escalates
  converging tasks and under-escalates on flaky checks.

**The only genuine delta** across both rounds: *group repeated failures by
`lineage-root` (cross-task-id), at `failing_checks` granularity, for CODE
failures* — which `detect_convergence_stall` does NOT do (it groups by proposer
`source_family` and deliberately EXCLUDES code failures, `_STALL_CATEGORIES =
{backend_error, timeout}`).

## 1. The operator decision — RESOLVED adversarially (round 3)

The decision was: *cross the line and escalate/rescope a lineage-root after N
identical CODE failures, via the existing convergence-stall path?* Two agents
attacked each side. They converged on a **third answer** that neither yes-nor-no
captures.

**FOR wins on the PROBLEM — the loop is real and currently uncaught.** Root cause:
`retry-count` is incremented ONLY on SIGKILL (`outcomes.py:483`, `dispatch.py:238`),
so a CLEAN test/lint failure never arms `_MAX_FOLLOW_UP_RETRIES` (`outcomes.py:594`)
or board_unblock's only cancel rule (SIGKILL-gated, `board_unblock.py:330`).
Meanwhile board_unblock Rules 3/8 recycle the Blocked code-failed goal back to
Ready (the exit-code exclusion doesn't fire for clean failures), and the proposer
NEVER stamps `lineage-root` (zero hits), so every per-root/per-task cap resets on
re-proposal. The guardrail's suppressors are open-only / 7-day-window /
human-only. **Net: an objectively-doomed code task can re-execute indefinitely,
draining the shared exec budget that gates proposals for healthy work.**

**AGAINST wins on the MECHANISM — the proposed fix is invariant-incompatible.**
The convergence-stall path writes `ProposalRejectionStore`, which is a PERMANENT,
HUMAN-SEMANTIC veto: no TTL, no removal API (`rejection_store.py:6-14`), checked
FIRST as a permanent human "no" (`guardrail_adapter.py:43-49`), keyed on the
SYMPTOM not the attempt. Laundering a coarse, agent-gameable machine judgment into
that store permanently abandons solvable work with no automated recovery — and the
founding ~190× incident was an ENV fault MISCLASSIFIED as a category, proving the
"code failure" trigger is unreliable. It also escalates a recurring per-lineage
correction to a human, violating the self-healing invariant (no human in the
per-correction loop). `failure_category` is agent-stdout substring matching
(`backends/openclaw/errors.py:54`), gameable in both directions — gives up on
honest hard tasks while letting adversarial loopers through.

**RESOLUTION:** Do **NOT** build the code-failure brake via convergence-stall /
`ProposalRejectionStore`, and do NOT build the lineage consumer. But the problem
is real and should be fixed by a **self-healing, expiring** bound — NOT a
permanent veto, NOT human escalation, NOT lineage machinery:

- **Arm the existing count caps for clean code failures.** Increment `retry-count`
  on a clean code failure (not just SIGKILL), so `_MAX_FOLLOW_UP_RETRIES` and the
  board_unblock cancel rule — which already exist and are self-healing
  (count-based, expiring, never touch the permanent store) — finally terminate a
  same-task loop. This is a ~handful-of-lines fix in `outcomes.py`/`board_unblock`,
  needs no lineage, and keeps the invariant intact.
- **Optionally** extend the per-root descendant cap
  (`work_ceiling.open_descendants_of_root` — self-healing, counts OPEN tasks only)
  to also throttle cross-task-id re-entry, IF the proposer is made to stamp
  `lineage-root`. Still no permanent veto, still no human-in-loop.

This is a SEPARATE small change (a retry-cap fix), tracked on its own, gated on the
operator confirming the behavior change (tasks that currently retry forever would
terminate after N) — it is consequential and is the operator's call, but it is the
*right* lever, not the lineage brake. **IMPLEMENTED** as
[Code-Failure Retry Cap](./CODE_FAILURE_RETRY_CAP.md) (N=3 default).

## 2. What is explicitly NOT worth building

- ❌ A lane that injects lineage into the executor LLM prompt (round 1: unsafe +
  unmechanized).
- ❌ A standalone deterministic consumer with its own producer/reader/ledger
  writes (round 2: duplicates convergence-stall + adds O(n²) + slot-leak).
- ❌ Per-root authorship on the durable tier (round 2: theater over the sandbox
  boundary; breaks writes).
- ❌ Writing the durable ledger on every dispatch (round 2: scaling landmine).
- ❌ A code-failure brake via convergence-stall / `ProposalRejectionStore`
  (round 3: permanent human-semantic veto on an unreliable, gameable trigger;
  human-in-the-per-correction-loop invariant violation). Fix the real loop with a
  self-healing retry-cap arming instead (§1 resolution).

## 3. Disposition of the already-built lineage machinery

The lineage read-model (`operations_center.lineage`: projection, trust model,
durable tier, integrity chain, CLI) is NOT wasted — it remains the **human
observability** surface (the CLI `display_view`) and the attestation substrate.
Two adversarial rounds simply concluded that **no lane should consume it for
steering**, and that the one convergence use it suggested is better served by the
existing scanner reading Plane labels. The deferred "steering consumer" item is
therefore **closed as won't-build**, superseded by §2 (a small, optional
convergence-stall extension) pending the §1 operator decision.
