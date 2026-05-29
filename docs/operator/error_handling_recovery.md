# Troubleshooting and Recovery Procedures — OperationsCenter Runbook

**Purpose:** Step-by-step recovery procedures for OperationsCenter errors, organized by severity and with decision trees for diagnosis.

**Last Updated:** 2026-05-29

---

<!-- #: quick-diagnosis-tree -->
## Quick Diagnosis Tree

Start here to determine error type and find the appropriate recovery procedure.

```
1. Is the watchdog loop running?
   ├─ NO → Go to section "Watchdog Loop Won't Start"
   └─ YES ↓
   
2. Is execution completing but failing?
   ├─ YES → Go to section "Execution Failures During Processing"
   └─ NO ↓
   
3. Are there error messages in the logs?
   ├─ YES → Go to "Error Message Reference" section in error_message_diagnostics.md
   └─ NO ↓
   
4. Is the platform stalled (no progress)?
   ├─ YES → Go to section "Platform Stalled / No Forward Progress"
   └─ NO → Platform is healthy; consult [Related docs](watchdog_loop.md) or create Plane escalation task if concerned
```

---

<!-- #: critical-errors-recovery -->
## Critical Errors — Recovery Procedures

### Watchdog Loop Won't Start

**Symptoms:**
- Controller process exits immediately
- Lock file present but no active process
- Startup logs show errors before first iteration

**Recovery Procedure:**

#### Step 1: Check Lock File
```bash
ls -la .console/loop.lock
cat .console/loop.lock
# Output should be: <PID>
ps -p <PID>
# If process not found → stale lock
```

#### Step 2: Reclaim Stale Lock
```bash
rm .console/loop.lock
# Re-run watchdog
```

#### Step 3: Verify ContextLifecycle Anchor
```bash
echo "CL_ANCHOR: $CL_ANCHOR"
# If empty or unset, run:
eval $(cl session start PlatformManifest)
# This sets CL_ANCHOR and initializes session
```

#### Step 4: Check Backend Availability
```bash
# Check if backends are rate-limited
grep -i "rate.limit\|both.*unavailable" logs/local/watchdog_cycles/*.log | head -20

# Manually verify backend connectivity:
# For Claude: check claude-code CLI
claude-code --version
# For Codex: check if API is accessible
curl -s https://api.codex.anthropic.com/health || echo "Codex unavailable"
```

#### Step 5: Manual Startup
```bash
# Start with verbose logging
python -m operations_center.tools.loop.controller \
  --verbose \
  --backend claude \
  --session-timeout 2700
# Monitor logs in real time
```

**If still failing:** Escalate to platform team with full logs from `.console/log.md` and latest watchdog cycle.

---

### Both Backends Rate-Limited

**Symptoms:**
- Watchdog log: `Both backends rate-limited until <timestamp>`
- Execution cannot proceed
- Platform stalled

**Recovery Procedure:**

#### Step 1: Identify Reset Times
```bash
# Parse rate-limit reset times from logs
grep -i "rate.limit.*until" logs/local/watchdog_cycles/*.log | tail -5

# Output should show:
# claude: until 2026-05-29 14:32:00 UTC
# codex: until 2026-05-29 14:45:00 UTC
```

#### Step 2: Calculate Wait Time
```bash
# Get current time
date -u

# Calculate seconds to wait (max of both reset times)
# Wait that duration
sleep $(( $(date -d "2026-05-29 14:45:00 UTC" +%s) - $(date +%s) ))
```

#### Step 3: Verify Reset
```bash
# Check if either backend is available
# For Claude:
timeout 5 curl -s https://api.claude.anthropic.com/ping && echo "Claude available" || echo "Claude unavailable"

# For Codex:
timeout 5 curl -s https://api.codex.anthropic.com/ping && echo "Codex available" || echo "Codex unavailable"
```

#### Step 4: Resume Watchdog
```bash
# Watchdog will automatically resume after reset time
# Or manually kick it:
kill -HUP $(cat .console/loop.lock)
```

**Note:** If one backend hits rate limit frequently, consider:
- Reducing concurrent execution threads
- Increasing adaptive delay between cycles
- Switching to slower backend (longer inter-cycle delay)

---

### Workspace Preparation Failures

**Symptoms:**
- Execution log: `git clone ... timed out after 300s`
- Execution log: `fatal: couldn't find remote ref <branch>`
- Execution log: `workspace directory not empty`

**Recovery Procedure:**

#### For Git Clone Timeout (300s exceeded):

```bash
# Step 1: Check network connectivity
ping -c 3 github.com

# Step 2: Check repository size and network
# Large repos may need more time; increase if necessary
# In workspace.py, line 100:
# CLONE_TIMEOUT_SECONDS = 300  # Increase to 600 if needed
# Then restart controller

# Step 3: Try manual clone to diagnose
timeout 600 git clone --depth 1 https://github.com/<repo>.git /tmp/test-clone
# If manual clone succeeds, network is OK; increase timeout
# If manual clone times out, check GitHub status

# Step 4: Clear any partial clones
rm -rf /tmp/.workspace_*
```

#### For Missing Base Branch:

```bash
# Step 1: Verify branch exists on origin
git ls-remote https://github.com/<owner>/<repo> | grep <branch>

# Step 2: If branch doesn't exist, create it (sandbox branches):
git clone https://github.com/<owner>/<repo>.git /tmp/repo
cd /tmp/repo
git checkout -b <branch> origin/main
git push -u origin <branch>
cd -
rm -rf /tmp/repo

# Step 3: Retry execution
```

#### For Non-Empty Workspace Directory:

```bash
# Step 1: Check what's in the workspace
ls -la .workspace_<id>/

# Step 2: Clean it up
rm -rf .workspace_<id>/

# Step 3: Retry execution
# Controller will re-create workspace
```

---

### Session Timeout (45-Minute Limit)

**Symptoms:**
- Watchdog log: `ERROR: Session timed out after 45 minutes, killing PID <pid>`
- Checkpoint timestamp frozen
- No new execution results for 45+ minutes

**Recovery Procedure:**

#### Step 1: Identify the Hung Process
```bash
ps aux | grep claude-code | grep -v grep
# Look for any processes older than 45 minutes
ps -eo pid,etime,cmd | grep claude
```

#### Step 2: Investigate What Caused the Hang
```bash
# Check the checkpoint for clues
cat .team_executor/checkpoint-*.json | jq . | head -50
# Look for: current stage, current work item, timestamp

# Check execution logs for the hung dispatch
ls -lt logs/local/dispatch_* | head -5
# Read the most recent one
```

#### Step 3: Determine if Session is in Self-Loop
```bash
# Extract recent execution attempts
grep -i "goal.*execute\|dispatch.*complete" logs/local/watchdog_cycles/*.log | tail -20

# Look for patterns:
# - Same goal executed multiple times in one cycle
# - No new tasks created despite execution
# - Propose stage skipped repeatedly
```

#### Step 4: Break the Loop
If self-loop detected:

```bash
# Option A: Stop current work
# In Plane, mark the current task as "on hold"
# Add comment: "Session hung during self-loop; pausing for investigation"

# Option B: Skip to next cycle
# Kill the hung process (already done by timeout handler)
# Edit .console/backlog.md to mark the stuck task Done
# Or move it to a different status

# Option C: Investigate root cause
# Read the hung checkpoint's goal:
cat .team_executor/checkpoint-*.json | jq '.active_capsule.goal'
# The goal may contain the stuck work item

# Copy goal ID and look it up in Plane for context
```

#### Step 5: Resume Watchdog
```bash
# Watchdog automatically resumes after timeout
# Or restart manually:
kill -9 $(cat .console/loop.lock) 2>/dev/null || true
rm .console/loop.lock
# Wait 10 seconds
sleep 10
# Restart controller
```

**Prevention:**
- Review goals that consistently hit session timeout
- Break large goals into smaller subgoals
- Increase SESSION_TIMEOUT_SECONDS if legitimate long-running work exists
- Monitor for self-loop patterns in watcher output

---

### Policy Validation Failure

**Symptoms:**
- Execution log: `policy_status=REJECTED reason=<rule_name>`
- Execution result shows `success=false` before adapter dispatch
- No backend call made

**Recovery Procedure:**

#### Step 1: Identify Which Policy Failed
```bash
# From execution trace:
grep "policy_status=REJECTED" logs/local/dispatch_*.log | head -3

# Extract the rule name:
# Example: reason=COST_BUDGET_EXHAUSTED
# Example: reason=CONCURRENCY_LIMIT_EXCEEDED
# Example: reason=INVALID_LANE_BINDING
```

#### Step 2: Cost Budget Exhausted
If reason is COST_BUDGET_EXHAUSTED:

```bash
# Check current cost spend
grep -i "cost.*budget\|tokens.*spent" logs/local/watchdog_cycles/*.log | tail -5

# Find the hard budget limit
grep -i "cost.*limit\|max.*spend" src/operations_center/policy/engine.py

# Option A: Wait for budget reset (if periodic)
# In recovery_policy.md, check if budget resets per cycle
# Wait for next cycle start

# Option B: Increase budget (if allowed)
# Edit config/operations_center.yaml:
# policies:
#   cost_budget:
#     hard_limit_tokens: 1000000  # Increase from current value
# Restart watchdog
```

#### Step 3: Concurrency Limit Exceeded
If reason is CONCURRENCY_LIMIT_EXCEEDED:

```bash
# Check how many executions are currently in flight
ps aux | grep "operations_center.*execute" | grep -v grep | wc -l

# Find the limit
grep -i "max.*concurrent\|concurrency.*limit" src/operations_center/policy/engine.py

# Option A: Wait for in-flight executions to complete
# Monitor: watch -n 5 'ps aux | grep operations_center | grep -v grep | wc -l'

# Option B: Kill long-running executions (carefully)
# Identify which are safe to kill
ps aux | grep "operations_center.*execute" | grep -v grep
# Kill specific ones: kill -9 <pid>

# Option C: Increase concurrency limit
# Edit config/operations_center.yaml:
# policies:
#   concurrency:
#     max_concurrent_executions: 10  # Increase from current value
# Restart watchdog
```

#### Step 4: Invalid Lane Binding
If reason is INVALID_LANE_BINDING:

```bash
# Check the request's lane binding
grep -B5 "policy_status=REJECTED.*INVALID_LANE" logs/local/dispatch_*.log | head -20

# Find valid lane definitions
grep -i "lanes:" config/operations_center.example.yaml
grep -i "lane:" docs/operator/*.md | head -10

# Correct the request's lane binding
# In Plane, update the task or goal with valid lane
# Or contact SwitchBoard team for lane configuration help
```

---

### Queue Deadlock / Stagnation

**Symptoms:**
- Watchdog log: `same audit finding in cycle X and cycle Y`
- Watchdog log: `propose skipped >0 but no new tasks created`
- Backlog status: many Blocked tasks, zero Ready-for-AI

**Recovery Procedure:**

#### Step 1: Identify the Stagnation Signal
```bash
# Check recent cycle summaries
tail -50 .console/log.md | grep -i "stagnation\|deadlock\|closed.loop"

# Extract the specific signal:
# - Same audit finding repeated?
# - Blocked count nonzero, Ready-for-AI zero?
# - Same Plane task in follow-ups for 2+ cycles?
```

#### Step 2: Classify the Blockage Type
```bash
# Read the most recent cycle summary for classification
tail -100 .console/log.md | grep -A20 "Blocked work classification"

# Expected output:
# - temporarily-blocked → will resolve on next cycle
# - infra-blocked → requires infrastructure fix
# - structurally-blocked → requires architectural change
# - crash-looping → anti-flap rule prevents retry
# - closed-loop stagnation → automation in infinite loop
```

#### Step 3: Intervene Based on Blockage Type

**For temporarily-blocked:**
```bash
# Just wait; should resolve next cycle
# Monitor: watch -n 60 'tail .console/log.md | grep -i stagnation'
```

**For infra-blocked:**
```bash
# Find the Plane task describing the infrastructure issue
grep "infra-blocked" .console/log.md | head -3
# Get the Plane task ID and fix the infrastructure
# Once fixed, mark the task as Done in Plane
# Watchdog will auto-recover next cycle
```

**For structurally-blocked:**
```bash
# Manual intervention required; find the Plane task
grep "structurally-blocked" .console/log.md | head -3
# This usually requires:
# - Code changes to OperationsCenter
# - Changes to recovery policies
# - Changes to goal/task structure
# Escalate to development team
```

**For crash-looping:**
```bash
# Find which watcher is crashing
grep "crash.*loop\|watcher.*crash" .console/log.md | head -3

# Temporarily disable the crashing watcher in loop_schedule.json:
# {
#   "watchers": {
#     "goal": {"enabled": true},
#     "test": {"enabled": true},
#     "improve": {"enabled": false},  # Disabled due to crash loop
#     ...
#   }
# }

# Investigate the crash:
grep -A20 "improve.*crash" logs/local/watchdog_cycles/*.log | head -30

# Fix the crash (code change to watcher)
# Re-enable in loop_schedule.json
```

**For closed-loop stagnation:**
```bash
# Platform is stuck in a non-productive loop
# Find what goal/task is causing it:
grep "closed.loop" .console/log.md | tail -1

# Options:
# A) Mark the stuck goal as Done and start fresh
# B) Edit the goal to break the loop (different parameters, different target)
# C) Move the goal to a holding status and investigate

# To break the loop:
# Edit .console/backlog.md:
# 1. Mark current goal as Done or Blocked
# 2. Update next priority
# 3. Restart watchdog

# Restart:
kill -9 $(cat .console/loop.lock) 2>/dev/null || true
rm .console/loop.lock
sleep 10
# Watchdog will restart automatically or:
python -m operations_center.tools.loop.controller
```

---

## Medium-Priority Errors — Recovery Procedures

### Budget Exhaustion (Retry Attempts)

**Symptoms:**
- Execution log: `recovery_decision=STOP_ATTEMPT_BUDGET_EXHAUSTED`
- Same task fails 5 times (or configured max)
- Execution stops retrying

**Recovery Procedure:**

```bash
# Step 1: Check the failure reason
grep "recovery_decision=STOP_ATTEMPT_BUDGET_EXHAUSTED" logs/local/dispatch_*.log

# Step 2: Understand why it's failing
# Read the last few attempts:
grep -B10 "STOP_ATTEMPT_BUDGET_EXHAUSTED" logs/local/dispatch_*.log | grep -i "error\|failure" | tail -5

# Step 3: Determine fix strategy
# Is it a transient error? (network, rate limit, timeout)
#   → Increase retry budget
# Is it a permanent error? (invalid config, wrong target)
#   → Fix the root cause
# Is it a recovery engine issue?
#   → Check if retry classification is wrong

# Step 4: Fix the root cause
# Examples:
# - If network timeout → increase timeout in workspace.py
# - If rate limit → wait for reset or switch backend
# - If invalid config → update request configuration
# - If target doesn't exist → update or create target

# Step 5: Retry
# Reset retry counter and re-dispatch:
# Edit the Plane task to remove completed attempts
# Set retry count to 0
# Or create a new task to retry with fresh budget
```

---

### Single Backend Rate Limit

**Symptoms:**
- Watchdog log: `Claude rate-limited until <timestamp>, falling back to codex`
- Execution switches to alternate backend
- Higher latency expected

**Recovery Procedure:**

```bash
# Step 1: Verify fallback is working
# Check logs for successful codex executions:
grep "backend=codex" logs/local/dispatch_*.log | wc -l
# Should be > 0 if fallback is active

# Step 2: Wait for reset time
# Extract from logs:
RESET_TIME=$(grep "rate.limit.*until" logs/local/watchdog_cycles/*.log | grep claude | tail -1 | grep -o "[0-9-]* [0-9:]*")
NOW=$(date -u "+%Y-%m-%d %H:%M:%S")
echo "Reset at: $RESET_TIME"
echo "Current:  $NOW"

# Step 3: Monitor recovery
# Once reset time passes, claude should automatically become available
watch -n 5 'grep -i "claude.*available\|backend.*switch" logs/local/watchdog_cycles/*.log | tail -3'

# Step 4: If not auto-recovering after reset time:
# Kill and restart watchdog
kill -9 $(cat .console/loop.lock) 2>/dev/null || true
rm .console/loop.lock
sleep 10
```

---

### Oversized Diff Detection

**Symptoms:**
- Execution log: `ERROR: Commit size exceeded limits: <N> files (max 50), <M> lines (max 2000)`
- Execution stops; commit rejected
- Executor appears to be generating too-large diffs

**Recovery Procedure:**

```bash
# Step 1: Identify the executor that created the oversized diff
grep -B20 "Commit size exceeded" logs/local/dispatch_*.log | grep "executor=" | tail -1

# Example output: executor=goal_executor

# Step 2: Investigate why it's creating large diffs
# Check the goal/task definition:
# - Is it applying multiple large refactors at once?
# - Is it importing large libraries?
# - Is it code-generating large files?

# Step 3: Increase limits if legitimate
# Edit workspace.py lines 54-56:
# _DEFAULT_MAX_FILES = 50  → increase to 100
# _DEFAULT_MAX_LINES = 2000  → increase to 5000
# Restart controller

# Step 4: Or break the goal into smaller steps
# Edit the Plane task to:
# - Apply one refactor at a time
# - Import in separate commits
# - Generate files in batches

# Step 5: Retry with fixed approach
```

---

## Low-Priority Errors — Monitoring Procedures

### Self-Deception / Activity Without Progress

**Symptoms:**
- Watchdog log: `same finding in consecutive cycles`
- Commit history shows activity but no new goals/tasks created
- Cycle duration increases but ready-for-AI count stays zero

**Recovery Procedure:**

```bash
# Step 1: Confirm the pattern
tail -100 .console/log.md | grep -A5 "self.deception\|activity without progress"

# Step 2: Identify what activity is happening
# Check commit history:
git log --oneline -20 | head -10

# Check which watchers are running:
grep "watcher.*executed" .console/log.md | tail -10

# Step 3: Check if state is actually changing
# Extract findings/signals from two consecutive cycles:
grep "Audit findings:" .console/log.md | tail -2
# Are they identical?

# Step 4: If truly non-convergent:
# Option A: Let platform settle for one more cycle
# (Sometimes self-deception resolves after one iteration)
wait_seconds=$(( 3600 + RANDOM % 300 ))
echo "Waiting $wait_seconds seconds for potential convergence..."
sleep $wait_seconds

# Option B: Check if any long-running work exists
ps aux | grep operations_center | grep -v grep

# Option C: Escalate to development team
# Provide: last 5 cycles (tail .console/log.md | head -100)
#          recent commits (git log --oneline -30)
#          checkpoint state (cat .team_executor/checkpoint-*.json | jq .)
```

---

### Watcher Handoff Gaps

**Symptoms:**
- Watchdog log: `Watcher <name> missing expected evidence field: <field>`
- Cycle summary: "Handoff gap detected"
- Telemetry is incomplete for that watcher

**Recovery Procedure:**

```bash
# Step 1: Identify which watcher has the gap
grep "missing expected evidence" logs/local/watchdog_cycles/*.log | head -3

# Example: Watcher "improve" missing: "execution_results"

# Step 2: Check the watcher's output format
# Verify watcher is producing the expected fields:
grep -A20 "improve.*output" logs/local/watchdog_cycles/*.log | head -30

# Step 3: This is typically a telemetry issue, not blocking
# Platform continues with best-effort inference
# Create a Plane task for the watcher owner to fix the output format

# Step 4: Monitor if the gap persists
grep "missing expected evidence" logs/local/watchdog_cycles/*.log | wc -l
# If count keeps growing, escalate the bug
```

---

## Diagnostic Commands Reference

### Check Platform Health
```bash
# Current cycle state
tail -20 .console/log.md

# Recent errors
grep -i "error\|failed\|timeout" logs/local/watchdog_cycles/*.log | head -20

# Backend availability
grep -i "available\|rate.limit\|unavailable" logs/local/watchdog_cycles/*.log | tail -10

# Queue status
grep -i "blocked\|ready.for.ai\|backlog" .console/log.md | tail -5

# Execution rate
ls -lt logs/local/dispatch_* | head -10 | wc -l
# Should be > 0 per cycle
```

### Monitor Current Execution
```bash
# Watch in real time
tail -f logs/local/watchdog_cycles/*.log

# Check active processes
ps aux | grep claude-code | grep -v grep

# Monitor resource usage
top -b -n 1 | grep claude-code

# Check lock status
cat .console/loop.lock
ps -p $(cat .console/loop.lock)
```

### Investigate Specific Failures
```bash
# Find executions from last hour
find logs/local/dispatch_* -mmin -60 | head -20

# Find all failures
grep "success=false" logs/local/dispatch_*/execution_result.json

# Find rate-limit events
grep -i "rate.limit" logs/local/dispatch_*/execution_*.log

# Find timeout events
grep -i "timeout\|timed out" logs/local/dispatch_*.log
```

---

## Escalation Path

If after following these procedures the issue persists:

1. **Gather diagnostics:**
   ```bash
   # Collect logs
   tar czf diagnostics-$(date +%s).tar.gz logs/local .console/log.md .console/backlog.md
   
   # Include checkpoint
   cp .team_executor/checkpoint-*.json ./checkpoint-latest.json
   
   # Include config
   cp config/operations_center.yaml ./config-latest.yaml
   ```

2. **Create Plane task with:**
   - Error scenario (from `error_scenarios.md`)
   - Recovery steps already attempted
   - Current state (last 20 lines of .console/log.md)
   - Checkpoint state
   - Diagnostic logs attachment

3. **Contact:**
   - OperationsCenter team on Slack
   - Or: platform-support@anthropic.com
