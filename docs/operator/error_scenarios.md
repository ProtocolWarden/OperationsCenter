# Operational Error Scenarios — OperationsCenter Runbook

**Purpose:** Quick reference for all documented error scenarios in OperationsCenter, organized by severity and system layer.

**Last Updated:** 2026-05-29

---

## Critical Path Errors (Immediate Escalation Required)

These errors require immediate operator intervention and may require escalation to the SwitchBoard team or platform support.

<!-- #: scenario-1-backend-unavailability -->
### 1. Backend Unavailability

**Description:** Both Claude and Codex backends are rate-limited simultaneously, or adapter registry is missing backend implementation, or network failure to backend API.

**Impact:** Platform cannot execute any work; watchdog loop stalls.

**Code Location:** `tools/loop/controller.py` lines 693–699, 726–727

**Current Handling:**
- Dual rate-limit: sleep until reset time is available; if both backends unavailable, SystemExit
- Missing adapter: SystemExit at registry lookup
- Network failure: caught at backend call site; classified as TRANSIENT for retry

**How to Detect:**
- Watchdog log: `ERROR: Both backends rate-limited until <timestamp>`
- Watchdog log: `ERROR: Adapter <name> not found in registry`
- Watchdog log: `WARNING: Network error to backend <name>, retrying in <N>s`

---

### 2. Workspace Preparation Failures

**Description:** Git clone times out (exceeds 300s), base branch is missing on origin, or workspace directory is not empty (collision).

**Impact:** Execution cannot begin; adapter cannot interact with codebase.

**Code Location:** `src/operations_center/execution/workspace.py` lines 84–150

**Current Handling:**
- Clone timeout: RuntimeError with stderr logged; escalate to watchdog cycle summary
- Missing base branch: RuntimeError (OR auto-heal if sandbox branch — create on origin)
- Non-empty directory: RuntimeError with advice to clean workspace

**How to Detect:**
- Execution log: `git clone ... timed out after 300s`
- Execution log: `fatal: couldn't find remote ref <branch>`
- Execution log: `RuntimeError: workspace directory not empty`

---

### 3. Session Timeout

**Description:** Watchdog session subprocess is hung beyond 45 minutes (SESSION_TIMEOUT_SECONDS), indicating deadlock or self-referential loop.

**Impact:** Session does not return; watchdog cycle never completes; next iteration hangs as well.

**Code Location:** `tools/loop/controller.py` lines 577–587

**Current Handling:**
- SIGKILL sent to subprocess after 45 minutes
- Process stderr captured; timeout error recorded in log
- Fallback to alternate backend triggered on next cycle

**How to Detect:**
- Watchdog log: `ERROR: Session timed out after 45 minutes, killing PID <pid>`
- `.team_executor/checkpoint-*.json` timestamp frozen (no new checkpoint)
- Platform appears stalled with no progress

---

### 4. Policy Validation Failure

**Description:** Incoming execution request violates runtime binding policy (e.g., cost budget exhausted, concurrency limits exceeded, invalid lane binding).

**Impact:** Execution blocked at policy gate; request never reaches adapter.

**Code Location:** `src/operations_center/policy/engine.py`

**Current Handling:**
- Request evaluated against all policy rules
- PolicyDecision returned with status (APPROVED / REJECTED / DEFERRED)
- Rejected requests recorded in audit trail; escalate to Plane if pattern detected

**How to Detect:**
- Execution log: `policy_status=REJECTED reason=<rule_name>`
- Coordinator trace: `ExecutionRecord.policy_decision.rejected_rules=[...]`
- Watchdog cycle summary: "Policy rejection detected; escalating to Plane"

---

### 5. Queue Deadlock / Starvation

**Description:** Duplicate suppression is blocking all forward progress; blocked tasks never re-enter executable state; workers unable to consume Ready-for-AI tasks.

**Impact:** Platform appears healthy but produces no forward progress; automation converges to non-convergent state.

**Code Location:** `recovery_policy.md` lines 273–295, `execution/coordinator.py`

**Current Handling:**
- Watchdog detects stagnation signals (2+ cycles of same pattern)
- Escalate to Plane as structural issue
- Transition cadence to STALLED; stop retrying same work
- Document queue state in cycle summary for operator investigation

**How to Detect:**
- Watchdog log: `WARN: Stagnation detected — same audit finding in cycle X and cycle Y`
- Watchdog log: `WARN: Queue deadlock signal — propose skipped >0 but no new tasks created`
- Backlog status: Ready-for-AI count zero despite Blocked count nonzero

---

## Medium-Priority Errors (Graceful Degradation)

These errors are expected in normal operation and have built-in recovery paths.

### 6. Recovery Attempt Budget Exhaustion

**Description:** A single task has been retried the maximum number of times (default: 5 attempts) and still fails.

**Impact:** Task stops being retried; may move to STALLED or escalated state.

**Code Location:** `execution/recovery_loop/engine.py` lines 97–100

**Current Handling:**
- Recovery decision: STOP_ATTEMPT_BUDGET_EXHAUSTED
- Record failure reason in execution trace
- If pattern detected across multiple tasks, escalate to Plane as non-convergent behavior

**How to Detect:**
- Execution log: `recovery_decision=STOP_ATTEMPT_BUDGET_EXHAUSTED`
- Execution trace: `recovery_metadata.final_decision=STOP_ATTEMPT_BUDGET_EXHAUSTED`
- Watchdog cycle summary: "Task X exceeded max retry attempts"

---

### 7. Rate Limit on Single Backend

**Description:** Claude or Codex hits usage limit and returns a rate-limit response with reset time.

**Impact:** Primary backend becomes unavailable; platform switches to fallback backend; execution continues at reduced throughput.

**Code Location:** `tools/loop/controller.py` lines 514–525, 464–512

**Current Handling:**
- Parse reset time from backend response or log (regex patterns for multiple formats)
- Cool down backend until reset time
- If primary (claude) hits limit: immediately fallback to codex
- If both hit limit: sleep until earliest reset time, then retry

**How to Detect:**
- Watchdog log: `WARN: Claude rate-limited until <timestamp>, falling back to codex`
- Watchdog log: `WARN: Both backends rate-limited until <timestamp>, sleeping <N>s`
- Controller state: backend marked unavailable in loop_schedule.json

---

### 8. Non-Idempotent Request Post-Send Failure

**Description:** Adapter sent a request to backend but failed to receive confirmation before error. Retry is not safe because the original request may already be executing.

**Impact:** Request state is ambiguous; cannot safely retry without risk of duplication.

**Code Location:** `execution/recovery_loop/models.py` lines 51–57

**Current Handling:**
- Classify as STOP_IDEMPOTENCY_REQUIRED
- Skip retry; record in trace as non-retryable
- Escalate to operator if as part of repeated pattern

**How to Detect:**
- Execution log: `recovery_decision=STOP_IDEMPOTENCY_REQUIRED reason=...`
- Execution trace: `failure_kind=BACKEND_UNAVAILABLE` AND failure post-send

---

### 9. Oversized Diff Detection

**Description:** Commit exceeds maximum file count (50 files) or maximum line count (2000 lines), indicating executor may be going wide unintentionally.

**Impact:** Commit is rejected; execution stops; operator must investigate executor behavior.

**Code Location:** `src/operations_center/execution/workspace.py` lines 54–56

**Current Handling:**
- Check run after finalization phase
- Reject commit with error message
- Log context (actual file count, actual line count)
- Stop execution and return error result

**How to Detect:**
- Execution log: `ERROR: Commit size exceeded limits: <N> files (max 50), <M> lines (max 2000)`
- Execution trace: `error_kind=OVERSIZED_DIFF`

---

### 10. Serialization Failures in ContextLifecycle Wrapping

**Description:** Work item cannot be serialized to dict for ContextLifecycle hydrate, or result cannot be captured to ContextLifecycle after execution.

**Impact:** ContextLifecycle metadata is unavailable; execution proceeds with graceful degradation.

**Code Location:** `src/operations_center/execution/cl_wrap.py` lines 60–82, 150–154, 188

**Current Handling:**
- Best-effort serialization: attempt dict conversion, fallback to empty dict if fails
- Capture failure is logged but never masks dispatch (dispatch result is never suppressed)
- No-op fallback if CL_ANCHOR environment variable unset

**How to Detect:**
- Execution log: `WARN: Failed to serialize work item for ContextLifecycle: <error>`
- Execution log: `WARN: Failed to capture result to ContextLifecycle: <error>`
- Execution trace: `lineage_id=None` indicates CL wrapping unavailable

---

## Low-Priority Errors (Monitoring & Logging)

These errors are typically diagnostic signals that don't block execution but may indicate systemic issues.

### 11. Watcher Handoff Gaps

**Description:** A watcher (goal, test, improve, propose, review, spec) did not emit structured evidence in the format expected by the watchdog loop.

**Impact:** Watchdog must infer behavior from logs or status; telemetry is incomplete.

**Code Location:** `recovery_policy.md` lines 170–207

**Current Handling:**
- Document gap in cycle summary
- Create Plane task for telemetry improvement
- Continue with best-effort inference from available data

**How to Detect:**
- Watchdog log: `WARN: Watcher <name> missing expected evidence field: <field>`
- Watchdog cycle summary: "Handoff gap detected in <watcher> output"

---

### 12. Automation Self-Deception Detection

**Description:** Platform logs activity but state doesn't evolve; appears healthy but produces no forward progress (closed loops, non-convergent cycles).

**Impact:** Platform may appear to be working while actually stalled; requires operator investigation to break the loop.

**Code Location:** `recovery_policy.md` lines 211–239

**Current Handling:**
- Classify cycle as containing deception if same audit finding in 2+ cycles
- Escalate to Plane; transition cadence to DEGRADED
- Log evidence (timestamps, finding fingerprints)

**How to Detect:**
- Watchdog log: `WARN: Self-deception detected — same finding in consecutive cycles`
- Watchdog cycle summary: "Activity without progress; platform may be stuck"
- Commit history: no new goals created despite execution attempts

---

### 13. Evidence Fingerprint Collisions

**Description:** Two distinct evidence states produce the same fingerprint (e.g., only timestamp changed, not actual state).

**Impact:** Watchdog cannot distinguish real change from noise; may fail to detect regressions or improvements.

**Code Location:** `recovery_policy.md` lines 69–88

**Current Handling:**
- Use canonical hash inputs (ignore timestamps, file paths, etc.)
- Require observable state change (e.g., task count, finding count) for escalation
- Log fingerprint on each evidence capture for diagnostics

**How to Detect:**
- Watchdog log: `DEBUG: Evidence fingerprint unchanged from cycle N-1`
- Cycle summary: "State unchanged; skipping escalation pending evidence change"

---

### 14. Stale Lock File Handling

**Description:** Controller lock is held by a process that has died (PID no longer exists), blocking new controller instances from running.

**Impact:** Platform appears stalled; watchdog loop doesn't start until lock is cleared.

**Code Location:** `tools/loop/controller.py` lines 399–415

**Current Handling:**
- On lock acquisition: check if owner PID is alive via `kill(pid, 0)`
- If dead: reclaim lock and proceed
- If alive: wait or fail depending on configuration
- Log reclaim event for audit

**How to Detect:**
- Watchdog startup log: `WARN: Reclaiming stale lock held by dead PID <pid>`
- Lock file: `cat .console/loop.lock` shows PID no longer in process list

---

### 15. Missing ContextLifecycle Session Anchor

**Description:** CL_ANCHOR environment variable is unset or session context cannot be hydrated (CL infrastructure unavailable).

**Impact:** ContextLifecycle wrapping is skipped; execution proceeds with no CL metadata capture.

**Code Location:** `src/operations_center/execution/cl_wrap.py` lines 90–98

**Current Handling:**
- Graceful no-op: check if CL_ANCHOR is set before attempting hydrate
- If missing or error: skip wrapping and proceed with normal execution
- No impact on test execution (existing tests unaffected)

**How to Detect:**
- Execution log: `DEBUG: ContextLifecycle wrapping skipped — CL_ANCHOR not set`
- Execution trace: `lineage_id=None` indicates CL wrapping unavailable

---

## Error Categorization Summary

### By Severity

| Severity | Count | Scenarios | Typical Response |
|----------|-------|-----------|------------------|
| **Critical** | 5 | Backend unavailability, workspace prep, session timeout, policy failure, queue deadlock | Stop execution, escalate, fallback backend, PARKED or DEGRADED |
| **Medium** | 5 | Budget exhaustion, single-backend rate limit, post-send failures, oversized diffs, serialization | Graceful stop, skip retry, escalate pattern, fallback path |
| **Low** | 5 | Handoff gaps, self-deception, fingerprint collisions, lock files, missing anchor | Document, monitor, improve telemetry, graceful degradation |

### By System Layer

| Layer | Error Count | Scenarios |
|-------|---|-----------|
| **Execution Boundary** | 7 | Workspace prep, policy failure, oversized diffs, serialization, non-idempotent, post-send, session timeout |
| **Recovery Engine** | 5 | Budget exhaustion, rate limit, backend unavailability, idempotency, oversized diffs |
| **Watchdog Controller** | 5 | Session timeout, backend rate limit, lock files, missing anchor, stale state |
| **Queue & State Management** | 4 | Queue deadlock, stagnation, self-deception, handoff gaps |
| **Policy & Validation** | 2 | Policy validation failure, evidence fingerprints |

### By Error Type

| Type | Count | Pattern | Response |
|------|-------|---------|----------|
| **Transient** | 8 | Network, backend unavailable, rate limit, serialization | Retry with backoff, fallback backend |
| **Rate Limit** | 2 | Backend usage limit | Cool down, switch backend |
| **Structural** | 3 | Policy failure, oversized diff, session timeout | Escalate, stop retry, kill process |
| **State (stagnation)** | 4 | Queue deadlock, self-deception, handoff gaps, lock files | Detect pattern, escalate, transition cadence |
| **Validation** | 3 | Policy violation, evidence collision, missing evidence | Reject, record reason, monitor |

---

## Next Steps

Refer to `docs/operator/error_handling_recovery.md` for detailed troubleshooting procedures and recovery steps for each scenario.

Refer to `docs/operator/error_message_diagnostics.md` for mappings of specific error messages to their causes and remedies.
