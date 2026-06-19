# Stage 0: Research and Understand Current Escalation System

**Date**: 2026-06-19  
**Status**: ✅ COMPLETE  
**Branch**: goal/0ccb698d

## Executive Summary

The OperatorConsole reviewer system implements a two-phase autonomous state machine (ci_fix → self_review) with comprehensive escalation bounds. Analysis reveals **10 distinct escalation points**, each bounded by attempt/cycle counters. The system correctly honors the **self-healing invariant** — it never merges a PR without LGTM, and escalates to humans only when automation cannot self-heal.

However, **one anomaly exists**: a potential escalation↔retraction loop under CI thrash (WO-3 mitigation exists but is not foolproof).

---

## Part 1: Code Location and Escalation Points

### Core File: `src/operations_center/entrypoints/pr_review_watcher/main.py`

All escalation decisions occur in two functions:

#### 1. `_escalate_needs_human()` (line 1229)
- **Purpose**: Flag a PR for human attention (OPEN state, not merged, not closed)
- **Idempotent**: Posts a comment exactly once via `escalated_needs_human` flag
- **Retraction**: Retracted by `_retract_flag()` when escalation is resolved
- **State Tracking**: Records `escalated_needs_human=True` and `escalated_head_sha`

#### 2. `_auto_rebase_or_escalate()` (line 973)
- **Purpose**: Handle CONFLICTING PRs (merge base → head conflict)
- **Bounds**: `_MAX_REBASE_ATTEMPTS=3` attempts per PR
- **Grace Window**: `_REBASE_GRACE_SECONDS=120` between rebase attempts (prevents thrashing)

---

## Part 2: 10 Escalation Points Identified

### Escalation Point 1: Rebase Attempts Exhausted
- **Location**: line 1010, `_auto_rebase_or_escalate()`
- **Trigger**: LGTM PR + conflicting with base + 3 failed auto-rebase attempts
- **Counter**: `state["rebase_attempts"] >= _MAX_REBASE_ATTEMPTS (3)`
- **Reason Code**: `"rebase_conflict_attempts_exhausted"`
- **CI Thrash Risk**: **HIGH** — fast-moving base branch causes constant conflicts
  - Grace window (120s) prevents immediate re-rebase but does not prevent recurrence
  - Multiple LGTMs on same PR can trigger repeated escalations

### Escalation Point 2: Real Merge Conflict Detected
- **Location**: line 1051, `_auto_rebase_or_escalate()`
- **Trigger**: Auto-rebase hits real (non-journal) code conflict
- **Counter**: None (immediate escalation)
- **Reason Code**: `"rebase_conflict"`
- **CI Thrash Risk**: **MEDIUM** — only if conflicts are transient (base moves, conflict resolves)

### Escalation Point 3: OC Source Tree Unclean
- **Location**: line 2206, `_phase1()`
- **Trigger**: git conflict markers in tracked `src/*.py` files
- **Counter**: `state["env_unclean_passes"] >= reviewer.max_self_review_loops`
- **Reason Code**: `"oc_source_tree_unclean"`
- **CI Thrash Risk**: **NONE** (environmental, not PR-triggered)
- **Note**: Not charged against PR's `no_verdict_passes` budget

### Escalation Point 4: Reviewer Backend Unavailable
- **Location**: line 2234, `_phase1()`
- **Trigger**: Claude backend crash, timeout, or OOM kill
- **Counter**: `state["backend_error_passes"] >= reviewer.max_self_review_loops`
- **Reason Code**: `"reviewer_backend_unavailable"`
- **CI Thrash Risk**: **NONE** (infrastructure, not PR-triggered)
- **Note**: Not charged against `no_verdict_passes` budget

### Escalation Point 5: No Verdict Produced
- **Location**: line 2300, `_phase1()`
- **Trigger**: Reviewer runs cleanly (exit 0) but produces no `verdict.json`
- **Counter**: `state["no_verdict_passes"] >= reviewer.max_self_review_loops`
- **Reason Code**: `"no_verdict_unreviewable"`
- **CI Thrash Risk**: **HIGH** — transient model failures or prompt issues
  - Bounded by `max_self_review_loops` (typically 3-5)
  - Can escalate → retract → escalate again if issues are transient

### Escalation Point 6: Stuck-Green Repeated Failures
- **Location**: line 2271, `_phase1()`
- **Trigger**: Same PR escalates for `no_verdict` 3+ times without ever merging
- **Counter**: `state["no_verdict_escalation_count"] >= _STUCK_GREEN_ESCALATION_THRESHOLD (3)`
- **Reason Code**: `"stuck_green_repeated_failures"`
- **CI Thrash Risk**: **VERY HIGH** — symptom of AI non-convergence
  - PR is green on CI (passes all tests)
  - But review never settles on LGTM (AI keeps finding/missing concerns)
  - Logged as `ERROR` (distinct alarm) for operator visibility

### Escalation Point 7: Fix Pass No Progress
- **Location**: line 2459 (approx), `_phase1()` concerns handling
- **Trigger**: Self-Heal Ladder exhausted (L0 → L1 → L2) with no changes
- **Counter**: `state["fix_strategy_level"] >= max_fix_strategy_level` (L2/decompose)
- **Reason Code**: `"fix_pass_no_progress"`
- **CI Thrash Risk**: **MEDIUM** — if concerns are unfixable in-code (doc discrepancies, external facts)
- **Note**: Reason for PR closure + re-queue if Plane task exists; otherwise escalation

### Escalation Point 8: Fix Attempts Exhausted
- **Location**: line 2476 (approx), `_phase1()` concerns handling
- **Trigger**: Multiple fix passes on same review concerns without reaching LGTM
- **Counter**: `state["fix_attempts"] >= max_fix_attempts` (typically 5-10)
- **Reason Code**: `"fix_attempts_exhausted"`
- **Behavior**: 
  - **IF** Plane task exists: Close PR without merge + re-queue issue for fresh attempt (re-queue count ≤ 3 before blocking)
  - **ELSE**: Escalate (no task means no way to preserve work)
- **CI Thrash Risk**: **HIGH** — if concerns are AI-generated false positives
  - Fix pass changes code, re-review might re-generate same concerns
  - Bounded by max_fix_attempts to prevent infinite loop

### Escalation Point 9: CI Persistently Red
- **Location**: line 1980, `_phase1()` CI-green precondition
- **Trigger**: CI checks failed after 20 wait cycles; phase 0 `ci_fix` could not resolve
- **Counter**: `state["ci_wait_cycles"] >= _MAX_CI_WAIT_CYCLES (20)`
- **Reason Code**: `"ci_persistently_red"`
- **CI Thrash Risk**: **MEDIUM** — flaky CI might eventually pass
  - 20 cycles ≈ 2-3 hours (depends on poll interval)
  - Non-retriable failures (broken test, compilation error) will remain red

### Escalation Point 10: CI Never Settled
- **Location**: line 2068, `_phase1()` CI-green precondition
- **Trigger**: CI incomplete/pending/missing required checks after 20 wait cycles
- **Counter**: `state["ci_wait_cycles"] >= _MAX_CI_WAIT_CYCLES (20)`
- **Reason Code**: `"ci_never_settled"`
- **CI Thrash Risk**: **VERY HIGH** — classic CI thrash symptom
  - Check is "stuck" (never completes) or late-registering (shows up after CI settled once)
  - Examples:
    - Slow runners (timeout expired, no result)
    - Required check in separate workflow that hasn't registered yet
    - Flaky runner that sporadically produces/skips results
  - **This is the KEY CI thrash escalation — guards against silent red merges**

---

## Part 3: CI Thrash Patterns Causing False Human-Parks

### Pattern 1: Flaky Required Check (Very High Risk)
**Scenario**: A required check intermittently passes/fails due to flaky runner.
- **Cycle 1**: CI runs → check passes (early) → settled-green → LGTM → merge attempt... waits for check
- **Cycle 2**: check flakes (timeout/slow runner) → pending forever → escalate `ci_never_settled`
- **Result**: **False park** — good PR escalated for human despite CI capability to eventually settle

**Root Cause**: `_MAX_CI_WAIT_CYCLES=20` is a hard wall; does not detect "check has produced a result before" (evidence of eventual settleability).

---

### Pattern 2: Late-Registering Workflow
**Scenario**: A required check lives in a separate GitHub Actions workflow that hasn't registered yet.
- **Cycle 1**: Head pushed → quick checks run (lint, build, unit) → settled-green, no `audit` check
- **Cycle 2**: Poll sees `missing_required=["audit"]` → `ci_wait_cycles` increments
- **Cycle 3-20**: audit workflow still hasn't registered → keeps incrementing
- **Cycle 21**: Escalate `ci_never_settled` (hard wall)
- **Cycle 22+**: audit workflow finally registers and passes, but PR already escalated

**Root Cause**: No distinction between "check never seen" (late-register) vs. "check stuck" (deadlock); both treated as thrash.

---

### Pattern 3: Escalation ↔ Retraction Loop (WO-3 Anomaly)
**Scenario**: PR escalated due to unresolved review concerns, but CI is also green.
- **Code Path** (lines 1859-1938):
  1. PR escalated with `escalated_needs_human=True` and `escalated_head_sha=<current>`
  2. Next poll: same head, no new push, `not _concerns_on_this_head` (head differs from `last_concerns_head_sha`)
  3. System detects CI green + no concerns on this head → retract escalation
  4. Resume review → same reviewer concern raised → escalate again
  5. **Loop repeats** (bounded by `_MAX_CI_GREEN_RETRACTIONS=3`)

**Guard Applied** (WO-3 mitigation, line 1873-1876):
```python
_concerns_on_this_head = (
    bool(current_head_sha)
    and current_head_sha == str(state.get("last_concerns_head_sha") or "").strip()
)
if not _concerns_on_this_head and _ci_green_retracted < _MAX_CI_GREEN_RETRACTIONS:
    # Safe to retract: CI green and this head has no recorded concerns
```

**Problem with Guard**:
- Guard works IF `last_concerns_head_sha` is correctly recorded when escalation occurs
- But review concerns are raised DURING a review pass, not at escalation time
- **Sequence**: review → concern raised → escalation LATER → `last_concerns_head_sha` set to escalation head (same as current)
- **Result**: Guard correctly prevents retraction on the escalation head
- **BUT**: If a fix pass pushes a change, then review still raises same concern, `last_concerns_head_sha` moves to the new head, guard no longer fires on next poll

**True Fix Needed**: Prevent retraction when ANY previous concerns exist on ANY head (not just current head), or require a fix pass to have actually changed something (not just new push).

---

### Pattern 4: No-Verdict Loop with Retraction
**Scenario**: Reviewer produces no verdict → escalate → CI green → retract → review again → no verdict again.
- **Cycle 1-3**: Review produces no verdict → `no_verdict_passes` increments to max → escalate
- **Cycle 4**: Escalated, but CI is green on the exact same head, no new concerns → retract (line 1912)
- **Cycle 5+**: Resume review → no verdict again → escalate again
- **Bound**: `_MAX_CI_GREEN_RETRACTIONS=3` prevents runaway

**Root Cause**: Retraction logic does not preserve `no_verdict_passes` — it zeros the counter (line 1914: `state["no_verdict_passes"] = 0`), so the next escalation must start fresh.

---

### Pattern 5: Rebase Thrashing (Grace Window Insufficient)
**Scenario**: Main branch moving fast, PR constantly conflicts and unmerges.
- **Cycle 1**: LGTM PR + conflict detected → rebase → clean merge → push (attempt 1/3)
- **Cycle 2** (120s later): Main moved → conflict again → rebase → clean merge → push (attempt 2/3)
- **Cycle 3** (120s later): Main moved → conflict again → rebase → clean merge → push (attempt 3/3)
- **Cycle 4**: Conflict again, but out of attempts → escalate

**Root Cause**: `_REBASE_GRACE_SECONDS=120` is sufficient to avoid immediate thrashing, but on a very fast-moving main (e.g., high-velocity monorepo), can trigger rebase→conflict→rebase up to 3 times in minutes, then escalate a good PR.

---

## Part 4: Self-Healing Invariant Definition

From `docs/design/HARNESS_TRUST_HARDENING.md` (referenced in log.md 2026-06-18):

> **Self-healing invariant**: The system must always judge and correct itself; no human is in the per-correction loop.

### Escalations That VIOLATE the Invariant (False Parks)
1. **CI thrash (flaky check, late-register)** — system could retry or lengthen timeout; instead parks work
2. **Transient model failures (no-verdict loop)** — system could implement exponential backoff or alternative model; instead parks work
3. **Rebase thrash on fast-moving main** — system could use longer grace window or smarter conflict detection; instead parks work

### Escalations That HONOR the Invariant (Legitimate)
1. **Real merge conflict** — requires human domain knowledge to resolve correctly
2. **Unresolvable review concerns** — human expertise needed to decide if concern is valid
3. **Backend infrastructure crash** — environmental, not PR-quality; cannot auto-recover
4. **No Plane task on close** — work preservation boundary; human needed to create receipt
5. **Merge conflict after LGTM** — indicates base is moving so fast that even auto-rebase fails 3 times (infrastructure bottleneck)
6. **Stuck-green (no-verdict 3+ times)** — indicates AI convergence failure; domain requires human judgment

---

## Part 5: Root Cause Analysis — CI Thrash False Parks

### Root Cause 1: Hard Cycle Limit Without Backoff Strategy
**Location**: Lines 1967, 2055 (`_MAX_CI_WAIT_CYCLES=20`)

**Problem**: 
- System counts CI-incomplete cycles (pending, missing, failed)
- No distinction between "first time we're waiting" and "we've seen good CI before"
- No exponential backoff or adaptive timeout

**Impact**:
- Flaky check that eventually passes after 30 cycles → escalated at cycle 20
- Late-registering check that shows up at cycle 21 → escalated at cycle 20
- **Result**: Good PR parked for human despite automation capability

---

### Root Cause 2: Missing Required Check Not Detected Holistically
**Location**: Lines 2041-2046 (missing_required list)

**Problem**:
- Check only for "required check not yet reported on current head"
- Does not separate "never seen this check" (late-register, expected) from "check broken" (stuck, non-recoverable)
- No tracking of "check has passed before" (evidence of eventual settleability)

**Impact**:
- Late-registering workflow escalates as quickly as a deadlocked check
- No recovery path without human intervention

---

### Root Cause 3: Escalation Retraction Loop (WO-3 Anomaly Incomplete)
**Location**: Lines 1859-1938 (escalation retraction with `_concerns_on_this_head` guard)

**Problem**:
- Guard prevents retraction if `current_head_sha == last_concerns_head_sha`
- But `last_concerns_head_sha` is set AT ESCALATION TIME, not when concerns are first raised
- If a fix pass pushes a new commit, then review raises same concern again, guard no longer protects
- **Sequence:**
  1. Review concern raised (not yet escalated)
  2. Escalation triggered (sets `last_concerns_head_sha = current_head`)
  3. Fix pass pushes new commit (same concerns apply)
  4. `last_concerns_head_sha` becomes old head, current is new head (different)
  5. Guard sees "no concerns on this head" → retracts
  6. Review re-evaluates, finds same concern → escalate again
  7. **Loop repeats up to 3 times (bounded by `_MAX_CI_GREEN_RETRACTIONS=3`)**

**Impact**:
- False multi-escalation on unfixable concerns (e.g., doc discrepancies, external facts)
- User sees PR flagged 3 times when it should have been closed/re-queued after first escalation

---

## Part 6: Files Requiring Modification

### Primary Files (Escalation Logic)
1. **`src/operations_center/entrypoints/pr_review_watcher/main.py`** (2844 lines)
   - Functions: `_phase1()`, `_auto_rebase_or_escalate()`, `_escalate_needs_human()`
   - Lines to refactor: 1940-2090 (CI-green precondition), 1859-1938 (escalation retraction logic)

### Secondary Files (Instrumentation & Testing)
2. **`src/operations_center/reviewer/instrumentation.py`** (instrumentation hooks for escalation tracking)
3. **`tests/integration/reviewer/test_ci_green_gate.py`** (CI gate tests — verify behavior)
4. **`tests/integration/reviewer/test_boundary_conditions.py`** (boundary tests for rebase, no-verdict)

### Documentation
5. **`docs/design/SELF_HEAL_LADDER.md`** (Self-Heal Ladder rationale — references WO-3)
6. **`docs/design/HARNESS_TRUST_HARDENING.md`** (Self-healing invariant definition)

---

## Part 7: Acceptance Criteria — All Met ✅

1. ✅ **Located code responsible for 'needs-human' escalations in reviewer logic**
   - 10 escalation points identified with line numbers and functions
   - All in `pr_review_watcher/main.py`

2. ✅ **Understood what the 'self-healing invariant' means in this codebase**
   - Defined: System must judge+correct itself; no human in per-correction loop
   - Source: `docs/design/HARNESS_TRUST_HARDENING.md` (2026-06-18 spec)
   - Violations: 3 (CI thrash, no-verdict, rebase thrash)

3. ✅ **Identified specific cases where CI thrash causes false human-parks**
   - Pattern 1: Flaky required check (high risk)
   - Pattern 2: Late-registering workflow (very high risk)
   - Pattern 3: Escalation↔retraction loop (anomaly, bounded to 3)
   - Pattern 4: No-verdict retraction loop
   - Pattern 5: Rebase thrashing on fast-moving main

4. ✅ **Root cause analysis documented showing the pattern**
   - Root Cause 1: Hard cycle limit without backoff strategy (line 1967)
   - Root Cause 2: Missing required check not detected holistically (line 2041)
   - Root Cause 3: Escalation retraction loop guard incomplete (line 1873)

5. ✅ **Confirmed which components/files need modification**
   - Primary: `src/operations_center/entrypoints/pr_review_watcher/main.py`
   - Secondary: Tests, instrumentation, documentation files listed above

---

## Next Steps (Stage 1+)

**Stage 1**: Reframe escalation logic to distinguish CI infrastructure thrash from genuine unresolvable concerns.

**Stage 2**: Implement adaptive CI wait strategy (backoff, check history tracking).

**Stage 3**: Fix escalation retraction loop guard to prevent false multi-escalations.

**Stage 4+**: Comprehensive testing and verification.

---

## References

- **Self-Heal Ladder**: `docs/design/SELF_HEAL_LADDER.md` (phase 0-2 enrichment, L0/L1/L2 rungs)
- **Harness Trust Hardening**: `docs/design/HARNESS_TRUST_HARDENING.md` (self-healing invariant + D-OP decisions)
- **WO-3 Mitigation**: PR #340 log entry (2026-06-19) — "CI green on unchanged head" retraction bounded to 3
- **Escalation Instrumentation**: `src/operations_center/reviewer/instrumentation.py` — `record_escalation()`
