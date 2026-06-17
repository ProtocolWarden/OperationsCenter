# Error Handling Recipes — Operator Decision Trees

**Document:** Operational decision trees and recovery recipes for OperationsCenter error scenarios  
**Scope:** Stage 1 of error handling documentation initiative  
**Last Updated:** 2026-05-29

---

<!-- #: recipes-overview -->
## Overview

This document provides **step-by-step decision trees** for operators to triage and recover from OperationsCenter errors. Each recipe maps an observable symptom → diagnostics → recovery action.

All recipes assume:
- Watchdog loop is running (or you have logs from a recent run)
- You have access to `.console/log.md` and `.operations_center/runs/` artifacts
- ContextLifecycle (CL) session anchor is set: `CL_ANCHOR=$HOME/.console/`

---

<!-- #: recipe-1-session-timeout -->
## Recipe 1: Session Timeout (45-minute hung subprocess)

**Symptom:** Watchdog controller exited with status 124 (SIGKILL); last log line shows session start time > 45 minutes ago.

**Decision Tree:**

```
Session timed out?
├─ YES → [A] Immediate: Check for hung/stalled cycle
│        [B] Root cause: Session self-referential loop?
│        [C] Escalate: Plane task for executor investigation
│
└─ NO  → Check backend rate-limit or availability (→ Recipe 6)
```

### Step-by-Step Recovery

**[A] Immediate stabilization (< 5 minutes):**

1. Read watchdog cycle log:
   ```bash
   tail -50 .console/log.md  # last 50 entries
   ```
   Look for: repeated cycle numbers, same error appearing 2+ times, "Step X" messages.

2. Check if duplicate controller process is running:
   ```bash
   ps aux | grep 'loop/controller'
   ```
   If YES → Kill stale process:
   ```bash
   kill -9 <pid>
   ```
   Wait 10s, then restart watchdog:
   ```bash
   nohup python -m tools.loop.controller > /tmp/oc-watchdog.log 2>&1 &
   ```

3. Mark the timeout in `.console/log.md`:
   ```markdown
   - **[2026-05-29 14:30 UTC] OPERATOR INTERVENTION:** Session timeout cycle 42. 
     Killed controller PID=9876. Restarted watchdog.
   ```

**[B] Root cause investigation (5–30 minutes):**

Check for self-referential executor behavior (executor dispatches goal that dispatches executor):

1. Find the last execution trace:
   ```bash
   ls -lt .operations_center/runs/ | head -5
   ```

2. Inspect the session log for loops:
   ```bash
   jq '.final_output' <latest-run-trace.json | head -100
   ```
   Look for repeated cycle markers or goal re-dispatch patterns.

3. Check goal queue state:
   ```bash
   # Via Plane API or local state
   operations-center-run-show --root .operations_center/runs <run_id>
   ```
   If the same goal appears 2+ times in the same cycle, escalate to [C].

**[C] Escalation to Plane (if loop detected):**

1. Create a Plane task:
   ```markdown
   Title: "Watchdog session timeout: possible self-referential executor loop (cycle 42)"
   Description:
   - Session hung at 45-minute mark during goal execution
   - Last goal: <goal_id>
   - Suspected root cause: executor re-dispatches itself
   - Action required: Review goal executor logic; add cycle-depth guard
   - Trace artifact: .operations_center/runs/<run_id>/
   ```

2. Mark in `.console/log.md`:
   ```markdown
   - Escalated to Plane task TASK_XYZ: executor loop investigation
   - Cadence: DEGRADED (manual monitoring; normal resume on fix)
   ```

3. If this is a training/test branch: consider switching to `demo_stub` backend to unblock:
   ```bash
   export OC_BACKEND=demo_stub
   # Restart watchdog
   ```

---

<!-- #: recipe-2-backend-unavailability -->
## Recipe 2: Backend Unavailability (Rate Limit on Both Claude + Codex)

**Symptom:** Session exited with status 0 but logs show "rate_limit" repeated for both backends; `.console/log.md` shows `COOLING_DOWN` state with reset time.

**Decision Tree:**

```
Both backends rate-limited?
├─ YES → [A] Check reset time window
│        [B] Cool down until reset
│        [C] Monitor recovery; escalate if repeated
│
└─ Single backend → [D] Switch to alternate backend (no action needed)
```

### Step-by-Step Recovery

**[A] Identify reset window:**

1. Parse rate-limit reset time from recent session log:
   ```bash
   grep -i "rate.*reset\|reset.*until" .console/log.md | tail -1
   ```
   Example output: `Rate limit: claude resets 2026-05-29 18:30 UTC`

2. Calculate remaining cool-down:
   ```bash
   now=$(date +%s)
   reset_epoch=$(date -d "2026-05-29 18:30 UTC" +%s)
   remaining=$((reset_epoch - now))
   echo "Cool down remaining: $((remaining / 60)) minutes"
   ```

**[B] Cool-down state management:**

1. Update `.console/log.md` with cool-down start time:
   ```markdown
   - **[2026-05-29 15:45 UTC] Both backends rate-limited**
     - claude: resets 2026-05-29 18:30 UTC (165 minutes)
     - codex: resets 2026-05-29 19:00 UTC (195 minutes)
     - Status: COOLING_DOWN (no new dispatches until 2026-05-29 19:00 UTC)
   ```

2. Read `tools/loop/loop_schedule.json` to verify watchdog delay is set to 300s (5 min):
   ```bash
   jq '.cadence' tools/loop/loop_schedule.json
   ```
   Should show a large value like `3600` (1 hour) during cool-down. If not, controller will update it on next session.

3. Watchdog will automatically:
   - Sleep until reset time
   - Retry with primary backend (Claude) first
   - Fall back to Codex if Claude still unavailable

**[C] Monitoring for repeated limits:**

1. Watch the watchdog log in real-time:
   ```bash
   tail -f .console/log.md
   ```

2. If both backends hit rate-limit again within 24 hours:
   - This suggests high dispatch frequency relative to quota
   - Escalate to Plane: "Rate limit frequency analysis needed — both backends limited N times in X hours"
   - Consider temporary mode change: `export OC_MODE=training` (reduces dispatch frequency)

**[D] Single backend limit (automatic recovery):**

No operator action needed. Controller automatically:
- Cools down the limited backend
- Switches to the alternate backend
- Resumes normal operation

---

<!-- #: recipe-3-workspace-failure -->
## Recipe 3: Workspace Preparation Failure (Clone Timeout or Missing Base Branch)

**Symptom:** Session exited with error containing "git clone" or "No such reference"; workspace directory was cleaned up.

**Decision Tree:**

```
Workspace prep failed?
├─ Clone timeout (300s exceeded)?
│  ├─ YES → [A] Network issue or large repo
│  └─ NO  → [B] Check git credentials / firewall
│
└─ Missing base branch?
   ├─ Sandbox branch deleted on origin?
   │  └─ YES → [C] Auto-healing: branch recreated
   ├─ Track base branch doesn't exist?
   │  └─ YES → [D] Verify branch exists in CxRP / platform
   └─ NO → [E] Stale worktree; clear it manually
```

### Step-by-Step Recovery

**[A] Clone timeout (network/large repo):**

1. Check error log:
   ```bash
   operations-center-run-show --root .operations_center/runs <run_id> | grep -A5 "Clone"
   ```

2. Verify network connectivity:
   ```bash
   timeout 10 git ls-remote https://github.com/ProtocolWarden/OperationsCenter.git HEAD
   ```
   If timeout: network issue. Check firewall rules or DNS.

3. If network is OK, repo may be large. Monitor clone manually:
   ```bash
   git clone --single-branch --branch main \
     https://github.com/ProtocolWarden/OperationsCenter.git /tmp/test-clone
   # Time this; if > 250s, consider increasing timeout in workspace.py line 110
   ```

4. Escalate if timeout persists:
   - Plane task: "Clone timeout for [repo_name] — consider bumping timeout from 300s"

**[B] Git credentials / firewall:**

1. Verify git SSH key is loaded:
   ```bash
   ssh -T git@github.com
   # Should return: "Hi <username>! You've successfully authenticated, but GitHub does not provide shell access."
   ```

2. Test git operation:
   ```bash
   git ls-remote git@github.com:ProtocolWarden/OperationsCenter.git
   ```

3. If auth fails: Configure SSH key in controller's environment:
   ```bash
   export SSH_KEY_PATH=$HOME/.ssh/id_ed25519
   # Or use SSH agent: eval "$(ssh-agent -s)" && ssh-add $SSH_KEY_PATH
   ```

**[C] Sandbox branch auto-healing:**

1. Check if branch was auto-recreated:
   ```bash
   git branch -a | grep "goal/$(date +%Y%m%d)"
   ```

2. If YES → No action needed; next execution will use the recreated branch.

3. If NO (and this is a sandbox branch) → Manually create it:
   ```bash
   git checkout -b goal/$(date +%Y%m%d)-manual origin/main
   git push -u origin goal/$(date +%Y%m%d)-manual
   ```

**[D] Track base branch doesn't exist:**

1. Verify the base branch name in the work order:
   ```bash
   grep "base_branch\|track" .console/task.md
   ```

2. Check if branch exists in the remote:
   ```bash
   git ls-remote origin <base_branch>
   ```

3. If not found:
   - Escalate to board-worker (who assigned this work)
   - Mark task as BLOCKED with reason: "Base branch <name> doesn't exist on origin"

**[E] Stale worktree cleanup:**

1. List git worktrees:
   ```bash
   git worktree list
   ```

2. If orphaned worktrees exist (created but not cleaned up):
   ```bash
   git worktree remove --force <path>
   rm -rf <path>  # if removal fails
   ```

3. Restart execution.

---

<!-- #: recipe-4-policy-validation -->
## Recipe 4: Policy Validation Failure (Request Rejected at Gate)

**Symptom:** Execution log shows `policy_status=REJECTED` with a reason; execution did not proceed.

**Decision Tree:**

```
Policy rejection?
├─ Cost budget exceeded?
│  └─ [A] Current cost state; consider reducing scope
│
├─ Attempt budget exhausted?
│  └─ [B] Too many retries; stop or escalate
│
├─ Concurrency limit hit?
│  └─ [C] Wait for in-flight tasks; coordinate with board-worker
│
└─ Other policy violation?
   └─ [D] Check policy rules in recovery_policy.md
```

### Step-by-Step Recovery

**[A] Cost budget exhaustion:**

1. Check current cost state:
   ```bash
   grep -i "cost_budget\|cost_remaining" .console/log.md | tail -1
   ```

2. Inspect the request that was rejected:
   ```bash
   operations-center-run-show --root .operations_center/runs <run_id> --json | jq '.request.budget'
   ```

3. If approaching limit:
   - Reduce scope: ask executor to work on fewer files / shorter tasks
   - Wait for cycle cadence to reset budget (if per-cycle)
   - Check `policy/engine.py` for budget calculation rules

4. Escalate if budget is unrealistic:
   - Plane task: "Policy cost budget too tight for [task_type] — current limit N, need M"

**[B] Attempt budget exhausted:**

1. Check retry count:
   ```bash
   operations-center-run-show <run_id> | grep -i "attempt\|retry"
   ```

2. If this is a transient issue (network glitch, temporary backend issue):
   - Wait for reset (default retry window is ~10 minutes)
   - Manually retry if needed:
     ```bash
     # Re-dispatch the same work order
     ```

3. If this is a structural issue (always fails the same way):
   - Escalate to Plane: "Task [id] exhausted retry budget — suspected root cause: [diagnosis]"
   - Consider moving to manual queue for human review

**[C] Concurrency limit:**

1. Check active executions:
   ```bash
   ps aux | grep "operations-center.*execute\|ExecutionCoordinator"
   ```

2. Check if any are hung:
   ```bash
   grep -i "timeout\|hung" .console/log.md | tail -3
   ```

3. If hung tasks exist → Kill and clean up:
   ```bash
   kill -9 <pid>
   # Monitor watchdog to detect the failure and escalate
   ```

4. Otherwise: Wait for in-flight tasks to complete (check logs for progress).

**[D] Other policy violations:**

1. Read the rejection reason from the trace:
   ```bash
   operations-center-run-show <run_id> --json | jq '.policy_decision.rejection_reason'
   ```

2. Cross-reference with `docs/operator/recovery_policy.md` section ["Execution Gate — Criteria Reference"](recovery_policy.md#execution-gate--criteria-reference) (lines 366–378).

3. If the policy rule is too strict:
   - Escalate to Plane: "Policy rule [name] is blocking valid work — recommend relaxation"

---

<!-- #: recipe-5-queue-deadlock -->
## Recipe 5: Queue Deadlock / Starvation

**Symptom:** Watchdog cycle summary shows "Blocked tasks > 0" and "Ready-for-AI = 0" repeatedly (3+ cycles).

**Decision Tree:**

```
Queue starvation?
├─ Duplicate suppression active?
│  ├─ YES → [A] Dedup-caused block; escalate for unblock decision
│  └─ NO  → [B] Check if Blocked tasks have unmet dependencies
│
└─ No approval for new work?
   └─ [C] Operator-blocked state; manually review and approve
```

### Step-by-Step Recovery

**[A] Duplicate suppression deadlock:**

1. List Blocked tasks:
   ```bash
   # Via Plane API: all tasks in "Blocked" state
   # Or check .console/backlog.md for annotation
   ```

2. Check for duplicate fingerprints:
   ```bash
   grep -i "duplicate\|fingerprint\|dedup" .console/log.md | tail -5
   ```

3. Understand the dedup rule:
   - Two work items are suppressed if they have the same fingerprint (usually goal_id + base_branch)
   - This prevents double-execution of the same work

4. If safe to re-enable one:
   - Identify which duplicate is "real" (the one you want to execute)
   - Mark the other as "superseded" or "skip" in the queue system
   - Escalate to board-worker to coordinate

5. Plane escalation:
   ```markdown
   Title: "Queue deadlock: duplicate suppression blocking N tasks"
   Description:
   - Ready-for-AI = 0 for 3+ cycles
   - Blocked tasks: [list of task IDs]
   - Suspected duplication keys: [list]
   - Recommended action: Manual review + selective dedup exemption
   ```

**[B] Unmet dependencies:**

1. For each Blocked task, check its blockers:
   ```bash
   # Via Plane API or board state
   for task_id in <blocked_task_ids>; do
     echo "Task: $task_id"
     grep "blocked_by\|depends_on" <task_data>
   done
   ```

2. For each blocker, check status:
   - If blocker is Done → Task should move to Ready-for-AI (watchdog will promote next cycle)
   - If blocker is Blocked → Recursive block (escalate)
   - If blocker is Running → Wait for completion (check if hung)

3. If a blocker is hung (Running for > 1 hour):
   - Kill the process
   - Mark task as failed
   - Re-queue dependents

**[C] Operator-blocked state:**

1. Check cycle summary for PARKED_OPERATOR_BLOCKED:
   ```bash
   grep -i "parked\|operator.*block" .console/log.md | tail -3
   ```

2. Verify what approval is needed:
   ```bash
   operations-center-run-show <run_id> | grep -A3 "operator_decision\|approval"
   ```

3. Provide approval:
   ```bash
   # Via Plane API or CLI
   operations-center-board promote <goal_id> --reason "operator approval for [reason]"
   ```

4. Log the decision:
   ```markdown
   - **[2026-05-29 16:00 UTC] OPERATOR APPROVAL:** Goal [id] approved for execution.
     Reason: [brief explanation].
   ```

---

<!-- #: recipe-6-single-backend-limit -->
## Recipe 6: Single Backend Rate Limit (Automatic Fallback)

**Symptom:** Session log shows "claude rate_limit" or "codex rate_limit" (singular); watchdog continues running.

**No operator action required.** Watchdog automatically:
1. Parses reset time from logs
2. Switches to alternate backend
3. Resumes dispatch with cooldown tracking

Monitor logs to verify fallback succeeded:
```bash
grep -i "fallback\|switched.*backend\|now using" .console/log.md | tail -1
```

If both backends are now limited → see Recipe 2.

---

<!-- #: recipe-7-post-send-failure -->
## Recipe 7: Non-Idempotent Request Post-Send Failure

**Symptom:** Execution log shows `failure_kind=BACKEND_UNAVAILABLE` with `idempotent=false`; result shows `STOP_IDEMPOTENCY_REQUIRED`.

**Decision Tree:**

```
Post-send failure (already sent to backend)?
├─ Confirm backend received the request
├─ Check if executor side-effects occurred
└─ Decide: Retry (safe) or Skip (risk)
```

### Step-by-Step Recovery

1. Inspect the execution trace:
   ```bash
   operations-center-run-show <run_id> --json | jq '.failure_kind, .idempotent, .result'
   ```

2. Check backend logs to confirm receipt:
   - For Claude: check Claude Code session history
   - For Codex: check Codex pipeline logs (if accessible)

3. If side-effects confirmed (e.g., file was created):
   - **DO NOT RETRY** — avoid duplicate file creation
   - Mark task as "manually resolved" and move to Done

4. If no side-effects (e.g., network failed before backend processed):
   - Safe to retry — mark task for manual re-queue

5. Escalate for visibility:
   ```markdown
   Title: "Idempotency gap detected: post-send failure [run_id]"
   Description: 
   - Request: [summary]
   - Recovery action taken: [skip|retry with caution]
   - Risk level: [low|medium|high]
   ```

---

<!-- #: recipe-8-oversized-diff -->
## Recipe 8: Oversized Diff Rejection

**Symptom:** Execution failed with message "Commit exceeds maximum — 50 files / 2000 lines"; diff was rejected.

**Decision Tree:**

```
Oversized diff?
├─ Is this expected (large refactor)?
│  ├─ YES → [A] Split into multiple PRs
│  └─ NO  → [B] Executor going too wide; investigate
│
└─ [C] Adjust limits or approve as-is
```

### Step-by-Step Recovery

**[A] Large refactor — split work:**

1. Identify the scope:
   ```bash
   operations-center-run-show <run_id> --json | jq '.diff_stats'
   ```

2. Create sub-tasks:
   - Example: 120-file refactor → 3 PRs of 40 files each
   - Coordinate with task sequencing (ensure dependencies are correct)

3. Re-queue each sub-task separately.

**[B] Executor going wide unexpectedly:**

1. Check executor logs:
   ```bash
   operations-center-run-show <run_id> | grep -A20 "executor.*output\|final_output"
   ```

2. If executor kept touching unrelated files:
   - Escalate: "Executor scope issue: touched N files beyond requested scope"
   - Review executor system prompt / constraints

3. Add a safety guard in the executor:
   - Limit files-per-PR directive
   - Add pre-commit scope validation

**[C] Adjust limits:**

If this is a legitimate large-scale change:

1. Temporarily adjust limits in `workspace.py` lines 54-56:
   ```python
   _DEFAULT_MAX_FILES = 100  # was 50
   _DEFAULT_MAX_LINES = 3000  # was 2000
   ```

2. Re-run the execution.

3. Mark for permanent adjustment if this becomes a pattern:
   - Plane task: "Max diff limits need tuning — review against real workflow sizes"

---

<!-- #: summary-recipe-lookup -->
## Summary — Recipe Selection Quick Lookup

| Symptom | Recipe | Action |
|---------|--------|--------|
| Session exited with status 124 | 1 | Check for hung loop; restart watchdog |
| Both backends rate-limited | 2 | Cool down; monitor reset time |
| Clone timeout or missing branch | 3 | Verify network; auto-healing or manual create |
| Policy rejection | 4 | Check budget/concurrency; escalate if limits unrealistic |
| Blocked tasks, Ready-for-AI=0 | 5 | Dedup deadlock or unmet dependencies |
| Single backend rate-limited | 6 | No action; fallback automatic |
| Post-send failure (idempotent=false) | 7 | Check backend receipt; decide retry vs. skip |
| Commit > 50 files or 2000 lines | 8 | Split into smaller PRs or escalate scope issue |

---

<!-- #: escalation-plane-template -->
## Escalation to Plane — Template

Use this template when creating a Plane task for operator-level investigation:

```markdown
Title: [Error type]: [brief description]

**Symptom:**
- [What the operator saw]

**Scope:**
- Affected task(s): [IDs]
- Frequency: [first time | recurring]
- Reproducibility: [always | intermittent]

**Root Cause (suspected):**
- [What you believe is happening]

**Impact:**
- Platform cadence: [ACTIVE | DEGRADED | STALLED]
- Work items blocked: [count]

**Recommended Action:**
- [Immediate fix | Investigation | Architecture change | Config tuning]

**Artifacts:**
- Execution trace: [run_id]
- Logs: [file paths]

**Timeline:**
- Occurred: [timestamp]
- Critical until: [deadline, if any]
```

---

<!-- #: error-handling-references -->
## References

- `.console/recovery_policy.md` — Machine-enforceable policy rules
- `docs/operator/watchdog_loop.md` — Watchdog loop architecture
- `tools/loop/controller.py` — Watchdog implementation
- `src/operations_center/execution/recovery_loop/` — Recovery engine details
