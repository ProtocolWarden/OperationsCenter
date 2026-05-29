# Error Handling Quick Reference — Troubleshooting Checklist

**Document:** Quick-reference guide for common stuck states and recovery commands  
**Scope:** Stage 1 of error handling documentation  
**Last Updated:** 2026-05-29  
**Target Audience:** On-call operators, first-responders

---

## TL;DR — Common Issues & Fixes

| Symptom | Diagnosis Command | Quick Fix | See Also |
|---------|------------------|-----------|----------|
| Watchdog hasn't run in 1h | `ps aux \| grep controller` | `nohup python -m tools.loop.controller > /tmp/oc.log 2>&1 &` | Recipe 1 |
| Session exited with 124 | `tail -20 .console/log.md` | Restart watchdog | Recipe 1 |
| Both backends rate-limited | `grep -i "rate.*limit" .console/log.md` | Wait until reset time; monitor | Recipe 2 |
| Clone timeout | `timeout 10 git ls-remote <url>` | Check network; increase timeout | Recipe 3 |
| Policy rejection | `operations-center-run-show <id> --json \| jq '.policy_status'` | Check budget/concurrency limits | Recipe 4 |
| Queue stuck (Ready=0, Blocked>0) | `grep -i "blocked\|ready" .console/log.md` | Check for dedup deadlock | Recipe 5 |
| Oversized diff | `operations-center-run-show <id> --json \| jq '.diff_stats'` | Split into smaller PRs | Recipe 8 |

---

## Health Checks — Run These First

### 1. Watchdog Process

```bash
# Is watchdog running?
ps aux | grep "tools/loop/controller" | grep -v grep

# If NO: Start it
nohup python -m tools.loop.controller > /tmp/oc-watchdog.log 2>&1 &

# If YES: Check its health
tail -20 /tmp/oc-watchdog.log
```

**Expected output:**
```
[2026-05-29 15:30:12] Watchdog cycle 42 started
[2026-05-29 15:35:20] Dispatch complete: status=success
[2026-05-29 15:40:00] Cycle 42 finished. Next cycle in 3600s.
```

### 2. Session Anchor (CL)

```bash
# Is session anchor set?
echo $CL_ANCHOR

# If empty: Set it
eval $(cl session start PlatformManifest)
echo "CL_ANCHOR=$CL_ANCHOR"
```

**Expected output:** `CL_ANCHOR=/home/user/.console/` (or similar)

### 3. Backend Availability

```bash
# Test Claude
export ANTHROPIC_API_KEY=<your-key>
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  --data '{"model":"claude-opus-4-8","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  | jq '.id' && echo "Claude: OK" || echo "Claude: FAILED"

# Check rate limit status
grep -i "rate.*limit\|429\|reset" .console/log.md | tail -1
```

---

## Scenario-Based Recovery

### Scenario 1: Watchdog Stopped (No activity in 1+ hours)

**Symptoms:**
- No new entries in `.console/log.md` for > 1 hour
- No process `tools/loop/controller`

**Diagnosis (2 min):**
```bash
ps aux | grep controller
# If found: Get the exit code
wait $(pidof controller) 2>/dev/null; echo $?
# If not found: It stopped
```

**Recovery (5 min):**
```bash
# Step 1: Check the last log
tail -50 /tmp/oc-watchdog.log

# Step 2: Look for errors
grep -i "error\|exception\|failed" /tmp/oc-watchdog.log | tail -5

# Step 3: Restart
nohup python -m tools.loop.controller > /tmp/oc-watchdog.log 2>&1 &

# Step 4: Verify
sleep 5
ps aux | grep controller | grep -v grep && echo "Running" || echo "Failed to start"
```

**If it won't start:**
1. Check CL anchor: `echo $CL_ANCHOR`
2. Check disk space: `df -h`
3. Check permission on `.console/`: `ls -ld .console/`
4. Escalate to Plane: "Watchdog failed to start — see /tmp/oc-watchdog.log"

---

### Scenario 2: Session Timeout (Status 124, SIGKILL)

**Symptoms:**
- Last cycle log shows: "Session terminated by signal 9"
- Or: "Session timeout: exceeded 45 minutes"

**Diagnosis (5 min):**
```bash
# Check for timeout in logs
grep -i "timeout\|signal.*9\|sigkill\|exceeded.*45" .console/log.md | tail -3

# Check if session is hung
ps aux | grep "claude\|operations-center" | grep -v grep

# List recent runs
ls -lt .operations_center/runs/ | head -5
```

**Recovery (10–30 min):**

```bash
# Step 1: Kill any hung processes
pkill -f "operations-center.*execute" 2>/dev/null

# Step 2: Check for self-referential loop
operations-center-run-show --root .operations_center/runs <latest-run-id> \
  | grep -A5 "executor\|goal"

# Step 3: Review .console/log.md
tail -30 .console/log.md

# Step 4: Update log with intervention
cat >> .console/log.md << EOF
- **[$(date -u +%Y-%m-%d\ %H:%M\ UTC)] OPERATOR INTERVENTION:** Session timeout cycle N.
  Killed hung process. Restarted watchdog.
EOF

# Step 5: Restart
nohup python -m tools.loop.controller > /tmp/oc-watchdog.log 2>&1 &
```

**If loop persists:**
1. Check goal that was executing (it may be self-referential)
2. Escalate to Plane: "Possible executor loop detected in goal [id]"
3. Switch backend: `export OC_BACKEND=demo_stub` (for testing)

---

### Scenario 3: Rate Limit (Status 0, but cooldown active)

**Symptoms:**
- Session exited successfully (status 0)
- Log shows: "COOLING_DOWN" state
- Reset time is in future

**Diagnosis (1 min):**
```bash
grep -i "cool\|rate.*limit" .console/log.md | tail -2
# Example output:
# - Status: COOLING_DOWN (reset: 2026-05-29 18:30 UTC; remaining: 120 minutes)
```

**Recovery (0 min — automatic):**

Watchdog handles this automatically. Just monitor:

```bash
# Check reset time
grep -i "reset" .console/log.md | tail -1

# Calculate minutes remaining
reset_time="2026-05-29 18:30 UTC"
now=$(date -u +%s)
reset_epoch=$(date -d "$reset_time" -u +%s)
minutes=$((($reset_epoch - $now) / 60))
echo "Rate limit resets in $minutes minutes"

# Check watchdog delay
jq '.cadence' tools/loop/loop_schedule.json
# Should be large (e.g., 3600 = 1 hour) during cooldown
```

**If rate limit hits again within 24 hours:**
1. Escalate: "Rate limit frequency spike — [N] limits in [timespan]"
2. Request quota increase from Anthropic

---

### Scenario 4: Git Clone Timeout

**Symptoms:**
- Execution failed with: "git clone timeout"
- Execution log shows: "CalledProcessError: timeout after 300s"

**Diagnosis (3 min):**
```bash
# Test clone manually
time timeout 250 git clone --single-branch --branch main \
  https://github.com/ProtocolWarden/OperationsCenter.git /tmp/test-clone

# Check network
ping -c 3 github.com
nslookup github.com

# Check git SSH
ssh -T git@github.com
# Expected: "Hi <name>! You've successfully authenticated..."
```

**Recovery (5–15 min):**

```bash
# If network is slow
# Option 1: Increase timeout in workspace.py
# Line ~110: timeout=300  →  timeout=600

# Option 2: Test with fallback protocol
GIT_SSH_COMMAND="ssh -v" git ls-remote git@github.com:ProtocolWarden/OperationsCenter.git

# Option 3: Use HTTPS with token
git clone --single-branch --branch main \
  https://oauth2:$GITHUB_TOKEN@github.com/ProtocolWarden/OperationsCenter.git

# Retry execution
operations-center execute <request>
```

**If timeout persists:**
1. Likely network issue (check with IT/network team)
2. Escalate: "Persistent clone timeout — network diagnosis needed"

---

### Scenario 5: Queue Deadlock (Blocked > 0, Ready-for-AI = 0)

**Symptoms:**
- Cycle summary shows: `Blocked: 5, Ready-for-AI: 0`
- Pattern repeats for 3+ consecutive cycles
- No new work entering the queue

**Diagnosis (5 min):**
```bash
# Check stagnation evidence
grep -E "Blocked|Ready.*AI|Stagnation" .console/log.md | tail -10

# List blocked tasks (via Plane API or board state)
# tasks with status=Blocked and dependency on tasks with status!=Done

# Check for duplicate suppression
grep -i "duplicate\|dedup\|suppressed" .console/log.md | tail -3

# Check if operator-blocked
grep -i "parked\|operator" .console/log.md | tail -1
```

**Recovery (10–30 min):**

```bash
# Option 1: Duplicate suppression deadlock
# Check which goals are blocked by dedup
operations-center-run-show --root .operations_center/runs <run_id> \
  | grep -A3 "fingerprint\|duplicate"

# If you need to unblock one:
# Mark the duplicate as "skipped" or "superseded" manually
# Coordinate with board-worker

# Option 2: Unmet dependencies
# For each blocked task:
for task_id in <task1> <task2> ...; do
  # Check what it depends on
  # Mark dependents as Done once blocker completes
done

# Option 3: Operator-blocked state
# Check what approval is needed
operations-center-run-show <run_id> | grep -A2 "operator"
# Provide approval, mark as Done

# Step: Log the decision
cat >> .console/log.md << EOF
- **[$(date -u +%Y-%m-%d\ %H:%M\ UTC)] OPERATOR ACTION:** Unblocked queue.
  Reason: [duplicate suppression / dependency resolution / operator approval]
EOF
```

**If deadlock persists:**
1. Escalate: "Queue deadlock for 4+ hours — investigation needed"
2. Consider manual intervention: Force-promote tasks if safe

---

### Scenario 6: Policy Rejection (Budget or Concurrency)

**Symptoms:**
- Execution log shows: `policy_status: REJECTED`
- Reason: "Cost budget exceeded" or "Concurrency limit hit"

**Diagnosis (2 min):**
```bash
# Check the rejection reason
operations-center-run-show <run_id> --json | jq '.policy_decision'

# Check current state
jq '.budget_remaining, .concurrent_executions' <policy-state>
```

**Recovery (5–15 min):**

```bash
# If cost budget exhausted
# Option 1: Reduce scope
# Ask executor to work on fewer files

# Option 2: Wait for reset
# Budget resets at [time] — check config

# Option 3: Check if budget is realistic
# Escalate: "Cost budget too low — need N, have M"

# If concurrency limit hit
# Option 1: Wait for in-flight tasks to complete
ps aux | grep "operations-center.*execute" | wc -l

# Option 2: Kill hung tasks
pkill -f "operations-center.*execute"

# Option 3: Increase concurrency limit
# In policy/engine.py or config: MAX_CONCURRENT_EXECUTIONS
```

---

### Scenario 7: Oversized Diff (> 50 files or > 2000 lines)

**Symptoms:**
- Execution failed with: "Commit exceeds maximum"
- Message: "50 files / 2000 lines"

**Diagnosis (2 min):**
```bash
# Check the diff stats
operations-center-run-show <run_id> --json | jq '.diff_stats'
# Example: {"files": 75, "lines": 3200}
```

**Recovery (10–30 min):**

```bash
# Option 1: Split into smaller PRs
# Create sub-goals:
#  - Goal A: Files 1–30 (1000 lines)
#  - Goal B: Files 31–60 (1100 lines)
#  - Goal C: Files 61–75 (1100 lines)

# Option 2: Adjust limits (if this is expected)
# In workspace.py, lines 54–56:
#   _DEFAULT_MAX_FILES = 100  # was 50
#   _DEFAULT_MAX_LINES = 3000  # was 2000

# Option 3: Review executor scope
# Check why executor touched so many files
# May indicate scope creep (intended to fix N files, touched M)
```

**If this is a pattern:**
1. Escalate: "Max diff limits need tuning — real workflows need N files"
2. Adjust limits in config; re-test

---

### Scenario 8: Backend Auth Failure (API Key Invalid)

**Symptoms:**
- Execution log shows: `failure_kind: AUTH_FAILED`
- Or: `401 Unauthorized`

**Diagnosis (2 min):**
```bash
# Check API key
echo "API key set: ${ANTHROPIC_API_KEY:+yes}"

# Check CL anchor
echo "CL_ANCHOR set: ${CL_ANCHOR:+yes}"

# Test backend directly
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  | jq '.error'
```

**Recovery (5 min):**

```bash
# Step 1: Refresh CL session
eval $(cl session start PlatformManifest)

# Step 2: Verify CL hydrated credentials
echo "CL_ANCHOR=$CL_ANCHOR"
env | grep ANTHROPIC

# Step 3: Test again
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-opus-4-8","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  | jq '.id' && echo "Auth: OK"

# Step 4: Retry execution
nohup python -m tools.loop.controller > /tmp/oc-watchdog.log 2>&1 &
```

**If still failing:**
1. Check if API key was revoked (check Anthropic dashboard)
2. Regenerate API key if needed
3. Escalate if issue persists

---

## Decision Tree — Which Scenario?

```
Platform issue?
│
├─ Watchdog not running?
│  └─ → Scenario 1: Start watchdog
│
├─ Session exited with 124?
│  └─ → Scenario 2: Restart watchdog; check for loops
│
├─ Both backends rate-limited?
│  └─ → Scenario 3: Wait for reset; monitor
│
├─ Git clone timeout?
│  └─ → Scenario 4: Test network; retry
│
├─ Queue stuck (Blocked, no Ready)?
│  └─ → Scenario 5: Check dedup/dependencies
│
├─ Execution rejected (policy)?
│  └─ → Scenario 6: Check budget/concurrency
│
├─ Commit too large (> 50 files)?
│  └─ → Scenario 7: Split into smaller PRs
│
├─ API key error (401)?
│  └─ → Scenario 8: Refresh CL session
│
└─ Other error?
   └─ Check error_handling_recipes.md for more scenarios
```

---

## Escalation Checklist

Use this before creating a Plane task:

- [ ] **Reproduced the issue?** Tried to restart/retry?
- [ ] **Checked logs?** `.console/log.md`, `/tmp/oc-watchdog.log`, `.operations_center/runs/`?
- [ ] **Checked health?** Watchdog running? Backend available? Network OK?
- [ ] **Consulted recipes?** Found matching scenario in `error_handling_recipes.md`?
- [ ] **Tried manual recovery?** Restart, retry, force-kill hung processes?
- [ ] **Timing?** Note how long issue persisted before escalation
- [ ] **Artifacts?** Attach run ID, execution trace, relevant logs

---

## Useful Commands (Cheat Sheet)

```bash
# Watch watchdog in real-time
tail -f /tmp/oc-watchdog.log

# Show last 30 entries in cycle log
tail -30 .console/log.md

# List all runs
ls -lt .operations_center/runs/ | head -10

# Inspect a run
operations-center-run-show --root .operations_center/runs <run_id> | head -50

# Check process health
ps aux | grep -E "controller|execute|operations"

# Kill a hung task
pkill -f "operations-center.*execute"

# Check rate limit status
grep -i "rate\|cooldown\|reset" .console/log.md | tail -3

# Check queue state (Blocked vs. Ready)
grep -E "Blocked|Ready.*AI" .console/log.md | tail -5

# Verify backend auth
echo $ANTHROPIC_API_KEY | wc -c  # Should be > 20 chars

# List orphaned git worktrees
git worktree list | grep -v " branch"

# Clean orphaned worktree
git worktree remove --force <path>

# Get the reset time (extract and calculate)
reset_text=$(grep -oP 'resets? \K[^;]+' .console/log.md | tail -1)
date -d "$reset_text" -u +%s  # Unix timestamp of reset
```

---

## When to Page Oncall

Escalate immediately (page oncall) if:
1. **Platform down for > 15 minutes** (no executions possible)
2. **All backends failing** (not recoverable by operator)
3. **Data corruption suspected** (git/state corruption)
4. **Security incident** (unauthorized access, leaked credentials)
5. **Repeated critical errors** with no known cause

Otherwise: Create Plane task and monitor for pattern.

---

## References

For deeper dives:
- **Recipes:** `docs/operator/error_handling_recipes.md`
- **Backend codes:** `docs/operator/backend_error_catalog.md`
- **Executor contracts:** `docs/operator/executor_failure_contracts.md`
- **Policy rules:** `.console/recovery_policy.md`
- **Architecture:** `docs/operator/watchdog_loop.md`

---

## Updates

This document is maintained by the platform team.  
Last updated by: Operations Center automation  
Last reviewed: 2026-05-29

File issues/updates via Plane task: `[doc]error-handling-quick-reference`
