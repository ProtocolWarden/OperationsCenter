# Error Message to Diagnosis Mappings — OperationsCenter Runbook

**Purpose:** Quick reference mapping specific error messages and log patterns to their likely causes and remedies.

**Last Updated:** 2026-05-29

**Usage:** Search this document for the exact error message you see in logs. Follow the diagnosis and remedies listed.

---

## Backend Errors

### "Both backends rate-limited until <timestamp>"

**Location in logs:** `logs/local/watchdog_cycles/*.log`

**Cause:** Both Claude and Codex backends have exceeded their usage limits simultaneously.

**Severity:** Critical

**Diagnosis:**
- Current time should be before the mentioned `<timestamp>`
- Both backends will become available at the same time
- Platform cannot execute any work until then

**Remedies:**
1. **Wait for reset:** Sleep until `<timestamp>`, then resume normally
2. **Reduce API usage:** Check if previous cycles had excessive token consumption
3. **Increase backend quota:** Contact Anthropic to increase usage limits (long-term)

**Relevant section:** `error_handling_recovery.md` → "Both Backends Rate-Limited"

---

### "<Backend> rate-limited until <timestamp>, falling back to <alternate>"

**Location:** `logs/local/watchdog_cycles/*.log`

**Cause:** Primary backend hit rate limit; platform automatically switched to alternate backend.

**Severity:** Medium

**Diagnosis:**
- Fallback is working (not a critical error)
- Execution continues but at potentially higher latency
- Primary backend will recover at `<timestamp>`

**Remedies:**
1. **Wait for recovery:** Primary backend becomes available at `<timestamp>`
2. **Monitor fallback:** Verify alternate backend is handling requests successfully
3. **Investigate usage:** Check if usage pattern is sustainable for only one backend
4. **Increase quota:** If fallback is insufficient, request higher limits

**Relevant section:** `error_handling_recovery.md` → "Single Backend Rate Limit"

---

### "Adapter <name> not found in registry"

**Location:** `logs/local/dispatch_*.log`

**Cause:** ExecutionCoordinator tried to dispatch to a backend that's not configured in the adapter registry.

**Severity:** Critical (blocks that dispatch)

**Diagnosis:**
- Backend name may be misspelled
- Backend not registered in `src/operations_center/execution/coordinator.py`
- Configuration mismatch between policy rules and available adapters

**Remedies:**
1. **Check backend name:** Verify spelling matches configured backends (claude, codex, team_executor, etc.)
2. **List available adapters:** Run `operations-center-run-show --list-backends` (or check code)
3. **Update policy:** If using a new backend, add it to registry first
4. **Escalate:** If backend should exist but doesn't, contact platform team

**Relevant section:** `error_scenarios.md` → "Backend Unavailability"

---

### "Network error to backend <name>, retrying in <N>s"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Network request to backend failed (timeout, connection refused, DNS resolution failure, etc.)

**Severity:** Medium (transient, with automatic retry)

**Diagnosis:**
- Network connectivity issue (temporary)
- May be rate-limiting in disguise (return code 429)
- Check system network connectivity
- May clear on next retry

**Remedies:**
1. **Check network:** `ping -c 3 github.com` (for GitHub API calls)
2. **Check backend status:** Visit https://status.anthropic.com for service status
3. **Increase timeout:** If legitimate slow network, increase request timeout
4. **Retry:** Platform will automatically retry; no action needed
5. **If persistent:** Check firewall/proxy rules blocking backend access

**Relevant section:** `error_scenarios.md` → "Backend Unavailability"

---

## Workspace and Git Errors

### "git clone ... timed out after 300s"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Cloning the repository took longer than 300 seconds (workspace preparation phase).

**Severity:** Critical (blocks execution)

**Diagnosis:**
- Large repository (>1 GB)
- Slow network connection
- Git server experiencing delays
- Repository fetch is not completing before timeout

**Remedies:**
1. **Increase timeout:** Edit `src/operations_center/execution/workspace.py` line 100, increase `CLONE_TIMEOUT_SECONDS` from 300 to 600
2. **Use shallow clone:** Modify clone command to use `--depth 1` if full history not needed
3. **Check network:** `ping -c 3 github.com` and measure latency
4. **Test manually:** Try cloning the repo locally to verify it's not a persistent issue
5. **Try alternate branch:** If specific branch is slow, use a smaller branch like `main`

**Relevant section:** `error_handling_recovery.md` → "Workspace Preparation Failures"

---

### "fatal: couldn't find remote ref <branch>"

**Location:** `logs/local/dispatch_*.log`

**Cause:** The branch specified in the execution request doesn't exist on the remote repository.

**Severity:** Critical (blocks execution)

**Diagnosis:**
- Branch name is misspelled
- Branch was deleted from remote
- Sandbox branch wasn't created yet
- Base branch mismatch (e.g., expecting `develop` but `main` exists)

**Remedies:**
1. **Verify branch exists:** `git ls-remote https://github.com/<owner>/<repo> | grep <branch>`
2. **For sandbox branches:** Create manually if missing:
   ```bash
   git clone https://github.com/<owner>/<repo>
   cd <repo>
   git checkout -b <branch> origin/main
   git push -u origin <branch>
   ```
3. **Check branch name:** Ensure no typos in request configuration
4. **Use alternate base:** If branch is deleted, create it from a known good commit

**Relevant section:** `error_handling_recovery.md` → "Workspace Preparation Failures"

---

### "RuntimeError: workspace directory not empty"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Workspace directory already exists from a previous execution and wasn't cleaned up.

**Severity:** Medium (easily recoverable)

**Diagnosis:**
- Previous execution didn't clean up workspace
- Multiple concurrent executions writing to same workspace path
- Stale workspace directory from crashed execution

**Remedies:**
1. **Clean workspace:** `rm -rf .workspace_<id>/`
2. **Verify cleanup:** `ls -la | grep "workspace"`
3. **Check concurrency:** Ensure only one execution per workspace ID
4. **Retry execution:** Controller will re-create clean workspace

**Relevant section:** `error_handling_recovery.md` → "Workspace Preparation Failures"

---

## Policy and Validation Errors

### "policy_status=REJECTED reason=COST_BUDGET_EXHAUSTED"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Execution request would exceed cost budget before running.

**Severity:** Medium (blocks request but not critical)

**Diagnosis:**
- Cost budget for this cycle/period exhausted
- Total API token spend already at limit
- No capacity for new dispatches

**Remedies:**
1. **Wait for budget reset:** Check if budget resets per cycle (usually at hour boundary)
2. **Reduce other consumption:** Kill or defer other expensive tasks
3. **Increase budget:** Edit `config/operations_center.yaml`, increase `cost_budget.hard_limit_tokens`
4. **Prioritize:** Skip low-priority tasks to save tokens for high-priority ones

**Relevant section:** `error_handling_recovery.md` → "Policy Validation Failure"

---

### "policy_status=REJECTED reason=CONCURRENCY_LIMIT_EXCEEDED"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Too many executions already running; new request would exceed concurrency limit.

**Severity:** Medium (blocks request, not critical)

**Diagnosis:**
- Maximum concurrent executions already active
- System at capacity
- Previous executions haven't completed yet

**Remedies:**
1. **Wait for capacity:** Other executions will complete; queue will process naturally
2. **Check long-runners:** Find executions that have been running >30 minutes
3. **Kill if safe:** Identify and kill executions that are stalled or unproductive
4. **Increase limit:** Edit `config/operations_center.yaml`, increase `concurrency.max_concurrent_executions`

**Relevant section:** `error_handling_recovery.md` → "Policy Validation Failure"

---

### "policy_status=REJECTED reason=INVALID_LANE_BINDING"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Request specifies a lane (routing target) that's not configured or invalid.

**Severity:** Critical for that request (blocks dispatch)

**Diagnosis:**
- Lane name doesn't exist in routing configuration
- Lane is disabled
- Request lane conflicts with policy rules
- SwitchBoard configuration mismatch

**Remedies:**
1. **List valid lanes:** Check routing configuration in SwitchBoard
2. **Verify lane:** Ensure lane name matches exactly (case-sensitive)
3. **Check SwitchBoard:** Verify lane is enabled and available
4. **Update request:** Modify request to use valid lane
5. **Escalate:** If lane should exist, contact SwitchBoard team

**Relevant section:** `error_handling_recovery.md` → "Policy Validation Failure"

---

## Recovery and Retry Errors

### "recovery_decision=STOP_ATTEMPT_BUDGET_EXHAUSTED"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Task has been retried the maximum number of times (default: 5) and still fails.

**Severity:** Medium

**Diagnosis:**
- Transient failures persisted across all retry attempts
- Permanent error that won't be fixed by retrying
- Retry budget exhausted before success

**Remedies:**
1. **Check failure reason:** Look at error messages from failed attempts
2. **Fix root cause:** If transient (network, timeout), increase timeout; if permanent (invalid config), fix config
3. **Reset budget:** Create new task/dispatch with fresh retry counter
4. **Investigate pattern:** If many tasks hit budget exhaustion, there's a systemic issue

**Relevant section:** `error_handling_recovery.md` → "Budget Exhaustion"

---

### "recovery_decision=STOP_IDEMPOTENCY_REQUIRED"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Request was sent to backend but response not received; cannot safely retry due to idempotency concerns.

**Severity:** Medium

**Diagnosis:**
- Network failure after request was sent
- Backend may have started executing the request
- Retry could cause duplicated work
- Safe retry not possible without side-effect detection

**Remedies:**
1. **Wait for natural recovery:** Backend may complete the request even without confirmation
2. **Check backend state:** Query backend to see if request was actually executed
3. **Manual intervention:** If possible, verify result and skip retry
4. **Escalate:** Contact platform team if stuck in ambiguous state

**Relevant section:** `error_scenarios.md` → "Non-Idempotent Request Post-Send Failure"

---

## Execution Size and Output Errors

### "ERROR: Commit size exceeded limits: <N> files (max 50), <M> lines (max 2000)"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Execution produced a diff that exceeded maximum size limits.

**Severity:** Critical for that execution (blocked)

**Diagnosis:**
- Executor applied multiple large changes at once
- Large files were created/modified
- Code generation or import added substantial lines
- Executor behavior is unexpected (going too wide)

**Remedies:**
1. **Increase limits temporarily:** Edit `workspace.py` lines 54–56 to increase thresholds
2. **Break into smaller changes:** Modify executor instructions to apply changes incrementally
3. **Investigate executor:** Check what the executor is trying to do; may be buggy
4. **Monitor:** If pattern repeats with same executor, escalate as executor bug

**Relevant section:** `error_handling_recovery.md` → "Oversized Diff Detection"

---

## Serialization and Integration Errors

### "WARN: Failed to serialize work item for ContextLifecycle: <error>"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Work item couldn't be converted to dict format for ContextLifecycle hydrate.

**Severity:** Low (doesn't block execution)

**Diagnosis:**
- Complex object type not JSON-serializable
- Custom type without __dict__ conversion
- Circular reference in object graph
- ContextLifecycle wrapping not critical; execution proceeds without it

**Remedies:**
1. **Ignore if not critical:** Execution continues; this is just lost CL metadata
2. **Check CL integration:** If CL metadata is needed, fix serialization
3. **Implement __dict__:** If custom type, add `__dict__` or custom serializer
4. **Skip CL wrapping:** If not needed, disable wrapping in config

**Relevant section:** `error_scenarios.md` → "Serialization Failures in ContextLifecycle Wrapping"

---

### "WARN: Failed to capture result to ContextLifecycle: <error>"

**Location:** `logs/local/dispatch_*.log`

**Cause:** Execution result couldn't be captured to ContextLifecycle after dispatch completed.

**Severity:** Low (doesn't block execution, doesn't suppress result)

**Diagnosis:**
- Result serialization failed
- ContextLifecycle service unavailable
- Write permission issue to CL storage
- Similar to serialization error, but post-execution

**Remedies:**
1. **Ignore if not critical:** Result is captured locally; CL capture is auxiliary
2. **Check CL connection:** Verify CL_ANCHOR is set and CL service is reachable
3. **Permissions:** Check if CL storage directory is writable
4. **Escalate:** If CL integration is critical, contact CL team

**Relevant section:** `error_scenarios.md` → "Serialization Failures in ContextLifecycle Wrapping"

---

## Watchdog Loop Errors

### "ERROR: Session timed out after 45 minutes, killing PID <pid>"

**Location:** `logs/local/watchdog_cycles/*.log`

**Cause:** Watchdog session subprocess hung or entered self-loop and didn't complete within 45-minute timeout.

**Severity:** Critical

**Diagnosis:**
- Subprocess is stuck (deadlock, infinite loop, waiting on I/O)
- Self-referential automation (goal executing itself repeatedly)
- Subprocess exited without being detected
- Session timeout is hardcoded safety net

**Remedies:**
1. **Kill process:** Already done (SIGKILL sent)
2. **Investigate checkpoint:** Check `.team_executor/checkpoint-*.json` for what was executing
3. **Check logs:** Find what stage/work caused the hang
4. **Break loop:** Modify task/goal to prevent re-execution
5. **Increase timeout:** If legitimate long-running work, increase SESSION_TIMEOUT_SECONDS (but investigate first)

**Relevant section:** `error_handling_recovery.md` → "Session Timeout (45-Minute Limit)"

---

### "WARN: Reclaiming stale lock held by dead PID <pid>"

**Location:** `logs/local/watchdog_cycles/*.log` or startup logs

**Cause:** Controller lock file exists but owner process is dead. Lock is being reclaimed by new instance.

**Severity:** Low (handled automatically)

**Diagnosis:**
- Previous controller crashed without cleanup
- Lock persisted; process died
- New controller instance detected and cleaned up the stale lock
- This is normal recovery behavior

**Remedies:**
1. **No action needed:** Lock reclamation is automatic and correct
2. **Monitor for frequency:** If happening frequently, investigate previous crashes
3. **Check logs:** Look for crash dumps from previous controller instance

**Relevant section:** `error_scenarios.md` → "Stale Lock File Handling"

---

### "Watcher <name> missing expected evidence field: <field>"

**Location:** `logs/local/watchdog_cycles/*.log`

**Cause:** Watcher output doesn't include a required field for watchdog cycle analysis.

**Severity:** Low (doesn't block but reduces telemetry quality)

**Diagnosis:**
- Watcher output format changed
- Watcher didn't produce complete output
- Watchdog expectations updated but watcher didn't
- Handoff gap (missing evidence for decision making)

**Remedies:**
1. **Document gap:** Note in cycle summary for telemetry team
2. **Create task:** File Plane task for watcher owner to fix output format
3. **Best-effort:** Watchdog continues with incomplete evidence
4. **Monitor:** Check if gap persists across multiple cycles

**Relevant section:** `error_scenarios.md` → "Watcher Handoff Gaps"

---

## State and Stagnation Errors

### "Stagnation detected — same audit finding in cycle X and cycle Y"

**Location:** `logs/local/watchdog_cycles/*.log` and `.console/log.md`

**Cause:** Same audit finding (evidence) appeared in consecutive cycles, indicating lack of forward progress.

**Severity:** Medium (indicates need for intervention)

**Diagnosis:**
- Platform detected and produced same finding twice
- State didn't evolve between cycles
- Possibly in a convergence loop or stuck waiting on something
- Pattern detected triggers escalation investigation

**Remedies:**
1. **Wait one more cycle:** Sometimes convergence happens next cycle
2. **Check for blockages:** See if something is blocking resolution (infra, permissions, etc.)
3. **Investigate:** Check watchdog cycle summary for details
4. **Escalate:** Create Plane task for investigation if it persists

**Relevant section:** `error_handling_recovery.md` → "Queue Deadlock / Stagnation"

---

### "Queue deadlock signal — propose skipped >0 but no new tasks created"

**Location:** `.console/log.md` (cycle summary)

**Cause:** Propose stage didn't create any new tasks, but some proposals were skipped (not executed).

**Severity:** Medium (indicates dead task creation)

**Diagnosis:**
- Task creation is blocked for some reason
- Proposals were evaluated but none resulted in new Backlog/Ready-for-AI tasks
- Queue stuck in state where proposals can't advance
- Possible deduplication preventing task creation

**Remedies:**
1. **Check deduplication:** Look for "already exists" or "duplicate" in propose logs
2. **Review backlog:** Manually check if expected tasks are missing
3. **Check Plane:** Verify tasks aren't in different status
4. **Escalate:** File Plane task for queue healing investigation

**Relevant section:** `error_handling_recovery.md` → "Queue Deadlock / Stagnation"

---

### "Self-deception detected — same finding in consecutive cycles"

**Location:** `.console/log.md` (cycle summary)

**Cause:** Platform detected activity (commits, executions) but state didn't actually change; produced same evidence finding as previous cycle.

**Severity:** Medium (indicates non-productive work)

**Diagnosis:**
- Activity is happening but not moving the needle
- Possible infinite loop of same operation
- Automation going in circles without progress
- Requires investigation to break the pattern

**Remedies:**
1. **Let settle:** Sometimes self-deception resolves in next cycle
2. **Check activity:** See what operations are running
3. **Break loop:** Modify task or goal to prevent same operation repeating
4. **Escalate:** If continues, there's a systemic loop in automation logic

**Relevant section:** `error_handling_recovery.md` → "Self-Deception / Activity Without Progress"

---

## ContextLifecycle and Integration Errors

### "DEBUG: ContextLifecycle wrapping skipped — CL_ANCHOR not set"

**Location:** `logs/local/dispatch_*.log`

**Cause:** CL_ANCHOR environment variable not set; ContextLifecycle integration unavailable.

**Severity:** Low (execution proceeds without CL metadata)

**Diagnosis:**
- Environment not initialized for ContextLifecycle
- Session may not have been started with `cl session start`
- Graceful degradation in effect
- No impact on execution or functionality

**Remedies:**
1. **Set up CL session:** Run `eval $(cl session start PlatformManifest)`
2. **Export CL_ANCHOR:** `export CL_ANCHOR=...` before starting controller
3. **Verify:** `echo $CL_ANCHOR` should show manifest path
4. **No urgency:** Execution works fine without CL; this is just metadata

**Relevant section:** `error_scenarios.md` → "Missing ContextLifecycle Session Anchor"

---

## Combining Errors — Multi-Error Scenarios

### When you see multiple different errors in one cycle:

**Pattern:** Multiple error messages appearing together

**Approach:**
1. **Identify primary error:** Usually the first one in logs
2. **Check if cascading:** Secondary errors may be symptoms of the primary
3. **Fix primary:** Resolve the root cause first
4. **Re-run:** Secondary errors often clear once primary is fixed

**Example:**
```
Primary: "Both backends rate-limited"
Secondary: "Adapter not found in registry"
Result: Second error caused by fallback logic trying alternate backend

Fix: Wait for rate-limit reset; secondary error clears automatically
```

---

## Error Search Index

| Error Pattern | Document Section |
|---|---|
| rate.limit | Backend Errors |
| Adapter.*not found | Backend Errors |
| Network error | Backend Errors |
| git clone.*timeout | Workspace and Git Errors |
| couldn't find remote ref | Workspace and Git Errors |
| workspace directory not empty | Workspace and Git Errors |
| policy_status=REJECTED | Policy and Validation Errors |
| recovery_decision=STOP | Recovery and Retry Errors |
| Commit size exceeded | Execution Size and Output Errors |
| serialize\|capture | Serialization and Integration Errors |
| Session timed out | Watchdog Loop Errors |
| stale lock | Watchdog Loop Errors |
| missing expected evidence | Watchdog Loop Errors |
| Stagnation detected | State and Stagnation Errors |
| Queue deadlock | State and Stagnation Errors |
| Self-deception | State and Stagnation Errors |
| ContextLifecycle | ContextLifecycle and Integration Errors |

---

## How to Use This Document

1. **Find your error message:** Search (Ctrl+F) for the exact error text
2. **Read the diagnosis:** Understand what caused the error
3. **Follow the remedies:** Apply fixes in order
4. **Check relevant section:** See cross-reference for detailed procedures
5. **If not listed:** Escalate with error message + context logs

---

## Escalation Template

If error is not found in this document:

```
Subject: Unknown Error in OperationsCenter — <error text>

Error message:
[Copy exact error from logs]

Location:
[logs/local/dispatch_*.log or logs/local/watchdog_cycles/*.log]

Time:
[Timestamp from error message]

Cycle/Execution ID:
[From checkpoint or execution trace]

Attempted fixes:
[What have you already tried]

Attachments:
- Last 50 lines of relevant log file
- Last 50 lines of .console/log.md
- Checkpoint state: .team_executor/checkpoint-*.json
```
