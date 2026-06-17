# Executor Failure Contracts — Specific Behaviors and Recovery Expectations

**Document:** Per-executor failure modes, contract guarantees, and recovery expectations  
**Scope:** Stage 1 of error handling documentation  
**Last Updated:** 2026-05-29

---

## Overview

Each OperationsCenter executor (worker) has its own contract for:
1. What failures it can encounter
2. What guarantees it makes (e.g., idempotency, atomicity)
3. How to recover from each failure mode
4. Expected timeline for completion

This document formalizes the **failure contract** for each executor type, so operators and automated recovery systems know what to expect.

---

## 1. Goal Executor

**Role:** Executes goal tasks that produce pull requests  
**Run Environment:** Managed git worktree on host filesystem  
**Expected Duration:** 15–40 minutes

### Failure Contract

#### 1.1 Idempotency Guarantee

**Statement:**
> A Goal execution is **safe to replay** if it fails before the PR is opened. Once the PR is opened (return code 0 with PR URL in output), the execution must never be retried without human review.

**Implication:**
- Pre-PR-creation failure → Safe to retry (no GitHub side-effects)
- Post-PR-creation failure → **STOP_IDEMPOTENCY_REQUIRED** (to prevent duplicate PRs)

**Detection:**
```bash
jq '.final_output.pr_url' <execution-output>
```
- If `pr_url` is null/missing → Retry-safe (pre-PR)
- If `pr_url` is present → Do not retry without human approval

**OC Recovery:**
```python
# In recovery_loop/engine.py
if failure_kind == BACKEND_UNAVAILABLE and execution_step == "after_pr_creation":
    return RecoveryDecision.STOP_IDEMPOTENCY_REQUIRED
```

---

#### 1.2 Workspace Isolation Guarantee

**Statement:**
> Each goal execution runs in an isolated git worktree. On success or any failure, the worktree is deleted. No filesystem artifacts persist beyond execution.

**Implication:**
- A failed goal leaves no worktree remnants
- You cannot inspect the workspace after failure (logs are your only window)
- Clean environment for retry

**Exceptions:**
- If executor crashes mid-delete, worktree may be orphaned
- Watchdog periodically cleans orphaned worktrees

**Cleanup command:**
```bash
git worktree list
git worktree remove --force <path>
```

---

#### 1.3 Budget Tracking

**Statements:**
- **Attempt Budget:** Each goal can be retried max 5 times (configurable)
- **Cost Budget:** Each goal is assigned a cost limit (in tokens); retries count against the same budget

**Implication:**
- A goal that fails 5 times is terminal; it goes to STOPPED_ATTEMPT_BUDGET_EXHAUSTED
- If cost budget exhausted (tokens spent), no more retries even if attempts remain
- Budget is **atomic**: single goal can't exceed its allocation

**Monitoring:**
```bash
jq '.recovery_metadata.attempts, .recovery_metadata.cost_spent' <execution-trace>
```

---

#### 1.4 Failure Classes

| Failure Class | Meaning | Retry Safe | Examples |
|---------------|---------|-----------|----------|
| **Input Validation** | Goal spec invalid | No retry | Missing base_branch; invalid goal_id |
| **Workspace Prep** | Clone/checkout failed | Yes, if pre-execution | Clone timeout; branch missing |
| **Execution Error** | Executor/LLM error | Depends on stage | Plan fails; PR creation fails |
| **Post-PR Error** | PR exists but follow-up fails | **No retry** | Auto-merge failed; CI check failed |
| **Timeout** | Execution > 40 minutes | Yes | Slow executor or network |
| **Budget Exhausted** | Retries/cost limit hit | No retry | Terminal state |

---

### Recovery by Failure Type

#### Input Validation Failure

**Example:** `base_branch: "nonexistent"`

**Recovery:**
1. Fix the goal input
2. Create new goal (don't retry the failed one)
3. Escalate to goal-creator: "Invalid base_branch in goal X"

---

#### Workspace Prep Failure (git clone timeout)

**Example:** Clone takes > 300 seconds

**Recovery:**
1. **Automatic:** Recovery engine retries with backoff (5s → 10s → 30s)
2. **Manual (if retries exhausted):**
   ```bash
   # Verify network
   timeout 10 git ls-remote https://github.com/ProtocolWarden/OperationsCenter.git
   
   # If network OK, try increasing timeout in workspace.py
   ```
3. **Escalate if timeout persists:**
   - Plane task: "Clone timeout for [repo] — consider bumping timeout from 300s"

---

#### Execution Error (executor or LLM fails)

**Examples:**
- Plan validation fails (executor can't parse requirements)
- Implementation crashes (executor encounters unrecoverable error)
- LLM returns nonsense (model performance issue)

**Recovery:**
1. Check executor logs:
   ```bash
   jq '.final_output.plan_log // .final_output.error' <execution-output>
   ```

2. **If plan validation failed:**
   - Refine goal spec
   - Create new goal with better constraints

3. **If implementation crashed:**
   - Check if it's a known issue (search `.console/log.md`)
   - If new issue: Escalate to executor team

4. **If LLM response degraded:**
   - Try with Codex backend (fallback)
   - Or wait for model recovery

---

#### Post-PR Error (PR exists, follow-up fails)

**Examples:**
- PR created, but auto-merge failed
- CI checks fail (after PR creation)
- Push to origin fails (but PR already open)

**Recovery:**
1. **DO NOT RETRY** — This will create a duplicate PR
2. **Manual cleanup:**
   ```bash
   gh pr close <pr_url> --reason "Duplicate or follow-up failed"
   ```

3. **Root cause analysis:**
   - If auto-merge failed: Check PR merge requirements (branch protection, CI)
   - If CI failed: Check test output; may need code fix
   - If push failed: Likely stale branch; close PR and retry

4. **Escalate:**
   - Create Plane task documenting the error
   - If pattern emerges: Escalate to platform team

---

#### Timeout (execution > 40 minutes)

**Recovery:**
1. Check what the goal was doing:
   ```bash
   tail -100 <execution-output-log>
   ```

2. **If stuck in LLM call:**
   - LLM timeout (Claude backend timeout on complex task)
   - Reduce scope: break goal into smaller parts

3. **If stuck in compilation/tests:**
   - Code is slow to compile/test
   - Executor needs performance tuning
   - Try with demo_stub backend first (to validate logic)

4. **Retry with reduced scope:**
   - Split goal into multiple smaller goals
   - Each smaller goal should complete in < 30 minutes

---

### Goal Executor Health Checks

**Healthy indicators:**
- 80%+ execution success rate
- Median execution time: 20–30 minutes
- No repeated timeouts
- < 2 goals in STOPPED_ATTEMPT_BUDGET_EXHAUSTED per week

**Degraded indicators:**
- Success rate 50–80%
- Frequent timeouts (> 2 per day)
- Repeated "Input validation" failures

**Critical indicators:**
- Success rate < 50%
- All executions failing with same error
- 0 successful goal completions in 24 hours

---

## 2. Test Executor

**Role:** Runs test suites and validates goal implementations  
**Run Environment:** Subprocess with test runner (pytest, jest, etc.)  
**Expected Duration:** 5–15 minutes

### Failure Contract

#### 2.1 Idempotency Guarantee

**Statement:**
> A test execution is **always safe to replay**. Test runs are read-only; they never create artifacts or modify state.

**Implication:**
- Test failures can be safely retried
- No budget limit (retries don't count against goal budget)
- Transient test flakes are expected and automatically retried

---

#### 2.2 Test Isolation

**Statement:**
> Each test run executes in a clean environment (no state from prior tests). Tests may share fixtures but not mutable state.

**Implication:**
- Test order is irrelevant
- A test failure doesn't affect subsequent tests
- Retrying a failed test resets all preconditions

---

#### 2.3 Failure Classes

| Failure Class | Meaning | Action |
|---|---|---|
| **Setup Failure** | Test environment can't be prepared | Retry; if persists, escalate |
| **Test Failure** | One or more tests failed | Check test output; fix implementation |
| **Timeout** | Test > deadline (usually 10 min) | Likely infinite loop or deadlock in code |
| **Crash** | Test harness crashed | Rare; escalate to test executor team |

---

### Recovery by Failure Type

#### Test Setup Failure

**Examples:** Missing dependency, build fails

**Recovery:**
1. **Automatic:** Recovery engine retries (transient issues often resolve on retry)
2. **Manual:**
   ```bash
   # Re-run locally to debug
   pytest --verbose --tb=short <test-file>
   ```
3. **If still failing:**
   - Dependencies may be missing in container
   - Escalate: "Test setup failure due to missing [dep]"

---

#### Test Failure (assertion failed)

**Recovery:**
1. **Check the assertion:**
   ```bash
   jq '.test_output.failed_tests[0]' <test-result>
   ```

2. **Fix the implementation:**
   - Goal executor likely produced incorrect output
   - Code change is needed

3. **Re-run tests:**
   - Auto-promoted by watchdog once goal is fixed

---

#### Test Timeout

**Recovery:**
1. **Likely infinite loop in code:**
   - Check implementation for loops that don't terminate

2. **Or deadlock:**
   - Check for circular waits, locks held too long

3. **Fix the code; re-run tests**

---

## 3. Improve Executor

**Role:** Auto-improves implementations (lint, format, optimize)  
**Run Environment:** Code transformation tool (ruff, black, clang-format)  
**Expected Duration:** 2–5 minutes

### Failure Contract

#### 3.1 Idempotency

**Statement:**
> Improve execution is idempotent: running the same improve task twice produces the same output (transformation applied once).

**Implication:**
- Safe to retry
- If improvement already applied, second run is no-op (correct behavior)

---

#### 3.2 Tool Failures

| Failure | Example | Recovery |
|---|---|---|
| **Unsupported Language** | Improving Rust code with Python formatter | Skip; note unsupported language |
| **Syntax Error** | Input code has syntax error | Code must be fixed before improve |
| **Tool Crash** | Formatter crashes on unusual pattern | Escalate; provide minimal reproduction |

---

### Recovery

Most improve failures are transient (tool version mismatch, temporary network). **Automatic retry** handles them.

For persistent failures:
1. Check tool version:
   ```bash
   ruff --version
   # Should match version in pyproject.toml
   ```

2. If version mismatch:
   ```bash
   pip install -U ruff==$(grep ruff pyproject.toml | cut -d= -f3)
   ```

3. Retry

---

## 4. Propose Executor

**Role:** Creates proposals (feature ideas, architecture suggestions)  
**Run Environment:** LLM-based text generation  
**Expected Duration:** 5–10 minutes

### Failure Contract

#### 4.1 Non-Deterministic Output

**Statement:**
> Each propose execution may generate different output (LLM is non-deterministic). There is no "correct" output; quality is assessed post-hoc.

**Implication:**
- Retrying doesn't guarantee better output
- Multiple proposals for the same goal may differ widely
- Human review required to select quality proposals

---

#### 4.2 Idempotency

**Statement:**
> Propose is idempotent: the same goal input always generates proposals (no side-effects).

**Implication:**
- Safe to retry
- Retries may produce different proposals
- Budget limits apply (cost for LLM calls)

---

### Recovery

**Failure to generate proposals:**
1. Check LLM backend availability:
   ```bash
   # See backend_error_catalog.md
   ```

2. Retry (may succeed if transient)

**Low-quality proposals:**
1. Review proposal content (human judgment)
2. If unsatisfactory: Create new proposal with refined constraints
3. Escalate if propose executor consistently generates low-quality ideas

---

## 5. Review Executor

**Role:** Performs code review and quality checks  
**Run Environment:** LLM-based analysis  
**Expected Duration:** 3–8 minutes

### Failure Contract

#### 5.1 Determinism

**Statement:**
> Review output is semi-deterministic. The same code usually produces similar reviews, but exact wording/emphasis may vary (LLM behavior).

**Implication:**
- Retrying may produce slightly different reviews
- Core findings (bugs, performance issues) are usually consistent

---

#### 5.2 Completeness

**Statement:**
> A review covers all code changes (no code in PR is left unreviewed). However, reviewer may miss subtle issues or false positives (inherent to code review).

**Implication:**
- Review output is complete but not perfect
- Human review recommended for critical changes

---

### Recovery

**Failed reviews:**
1. Check LLM backend
2. Retry if transient
3. For persistent failures: Escalate to platform team

**Low-quality reviews:**
1. Review is subjective; assess if findings are valid
2. If consistently low-quality: May indicate executor needs improvement

---

## 6. Spec Executor

**Role:** Generates specification documents for APIs, data models, workflows  
**Run Environment:** LLM-based text generation  
**Expected Duration:** 2–5 minutes

### Failure Contract

#### 6.1 Output Format

**Statement:**
> Spec output is always valid Markdown (or specified format). Formatting is enforced; syntax errors are prevented.

**Implication:**
- Output is parseable
- Always matches the requested schema/structure

---

#### 6.2 Completeness

**Statement:**
> Spec covers all requested sections. Omissions indicate a request validation failure (spec executor rejects incomplete requests).

**Implication:**
- No missing sections
- If a section is empty, it's intentional (e.g., "no deprecations" → empty deprecations section)

---

### Recovery

**Same as Propose Executor** — mostly transient failures on backend availability.

---

## Cross-Executor Patterns

### Failure Propagation

When executor X fails, downstream executors are affected:

```
Goal Executor
├─ On success → Test Executor runs
├─ On failure → Recovery loop decides: retry or escalate
│
Test Executor
├─ On success → Improve Executor (optional)
├─ On failure → Goal Executor retries (fix code; re-test)
│
Improve Executor
├─ On success → Done (improvements applied)
├─ On failure → Skip (not critical)
│
Propose Executor
├─ On success → Human decides if to pursue proposal
├─ On failure → Retry (transient failures likely)
│
Review Executor
├─ On success → Human reviews findings
├─ On failure → Retry; or skip if non-critical
│
Spec Executor
├─ On success → Artifact stored for reference
├─ On failure → Retry (critical for specification)
```

### Shared Budget Model

All executors share a **per-goal budget**:

```
Goal Budget = Total Cost Allowance (e.g., 10,000 tokens)

├─ Goal Executor: 60% (6,000 tokens)
├─ Test Executor: 20% (2,000 tokens)
├─ Improve Executor: 10% (1,000 tokens)
└─ Propose/Review/Spec: 10% combined (1,000 tokens)
```

When one executor uses more than allocated:
1. Budget borrowed from less-critical executors
2. Once budget exhausted: No more retries (even if attempts remain)

**Monitoring:**
```bash
jq '.recovery_metadata.budget_remaining' <execution-trace>
```

---

## Failure Classification for Recovery

The RecoveryEngine uses this decision tree for all executors:

```
Executor failed?
│
├─ Pre-send failure (LLM not reached)?
│  └─ Retry-safe: TRANSIENT / TIMEOUT / NETWORK_ERROR
│
├─ Post-send failure (LLM received request, failed to respond)?
│  ├─ Idempotent task? → Safe to retry
│  └─ Non-idempotent task? → STOP_IDEMPOTENCY_REQUIRED
│
├─ Rate-limited?
│  └─ Cool down backend; switch; retry
│
├─ Budget exhausted?
│  └─ STOP (no more retries)
│
└─ Unknown / unexpected?
   └─ [policy.retry_unknowns] → retry or escalate
```

---

## Escalation Criteria by Executor

| Executor | Escalate When | Plane Task Tag |
|---|---|---|
| Goal | All failures after 5 retries | `executor/goal` |
| Test | Repeated setup failures (3+ in 24h) | `executor/test` |
| Improve | Unsupported language pattern | `executor/improve` |
| Propose | Consistent low-quality output | `executor/propose` |
| Review | Contradictory findings (bug + no-bug same code) | `executor/review` |
| Spec | Missing output sections | `executor/spec` |

---

## Executor Health Metrics

For each executor, monitor:

1. **Success Rate:** `successes / (successes + failures)`
   - Healthy: > 80%
   - Degraded: 50–80%
   - Critical: < 50%

2. **Mean Time to Completion:** Average duration
   - Healthy: Within expected range (e.g., Goal: 20–30 min)
   - Degraded: 1.5x–2x expected
   - Critical: > 2x expected (likely hung)

3. **Retry Rate:** Retries / attempts
   - Healthy: < 30%
   - Degraded: 30–60%
   - Critical: > 60%

4. **Budget Efficiency:** Tokens used / tokens allocated
   - Healthy: < 80%
   - Degraded: 80–95%
   - Critical: > 95% (will hit budget limits)

---

## References

- `src/operations_center/execution/recovery_loop/` — Recovery engine implementation
- `src/operations_center/backends/` — Per-backend executor adapters
- `.console/recovery_policy.md` — Policy rules for failure handling
- `docs/operator/error_handling_recipes.md` — Operator decision trees
