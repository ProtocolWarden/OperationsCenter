# OC Watchdog Loop — Sonnet Brain Prompt

Working directory: /home/dev/Documents/GitHub/OperationsCenter
Model architecture: Haiku collects signals → Sonnet analyzes and acts.
Opus fallback: if Sonnet usage is exhausted, switch model via /model; this prompt works identically on Opus.

---

## PHASE 1 — DATA COLLECTION (Haiku sub-agent)

Read the collector prompt:
```
Read /home/dev/Documents/GitHub/OperationsCenter/.console/haiku_collector_prompt.md
```

Spawn Haiku to collect all signals:
```
Agent(subagent_type="claude", model="haiku", prompt=<content of haiku_collector_prompt.md>)
```

Parse the returned JSON. If lock="aborted:live_owner" → stop immediately, do not schedule wakeup.
If lock="aborted:<other>" → log the reason, schedule 180s CRITICAL wakeup, stop.

---

## PHASE 2 — ANALYSIS (Sonnet)

### STEP 3 — BLOCKED/STALLED WORK INVESTIGATION

Read the last 3 cycle summaries from .console/log.md to identify repeated patterns.

From the Haiku JSON, classify each board_unblock.skipped and any known parked tasks:

**STARVATION** — classify immediately (single cycle sufficient) when ANY hold:
- Propose repeatedly emits candidates skipped because duplicates exist in Blocked
- tasks_created=0 or None while Blocked count nonzero
- Ready-for-AI never drains despite work existing
- Blocked tasks with self-modify:approved never transition
- Same remediation candidates generated repeatedly with zero queue movement

**CLOSED-LOOP STAGNATION** — classify and escalate immediately when platform repeatedly generates/retries equivalent remediation while queue/task/execution state does not materially change.

Actively investigate: (a) R4AI stuck >2 cycles, (b) Blocked self-modify:approved tasks, (c) same STEP 1 findings repeating, (d) repos skipped repeatedly, (e) same regressions recurring, (f) autonomy-cycle failures, (g) flow-audit gaps open multiple cycles, (h) graph invariants broken, (i) propose tasks_created=0 while candidates emitted, (j) duplicate suppression deadlock.

**BEHAVIORAL CONVERGENCE CHECK:**
- CONVERGENT / WEAKLY-CONVERGENT / NON-CONVERGENT / DIVERGENT
- NON-CONVERGENT when: same duplicates skipped 2+ cycles, same repo targeted 3+ times same outcome, regression retries recreate identical findings, blocked tasks recycled with same execution outcome, remediation titles/labels semantically equivalent across cycles.
- DIVERGENT when: board health worsening vs prior cycles, blocked count increasing while remediation runs, retries introducing new regressions.

Classify each blocked item: temporarily-blocked / infra-blocked / ownership-ambiguous / validation-blocked / structurally-blocked / crash-looping / starvation / dead-remediation / closed-loop stagnation / non-convergent / divergent / operator-blocked.

**OPERATOR-BLOCKED:** classify ONLY when the issue genuinely requires external credentials, physical infrastructure changes, or a policy decision that cannot be made programmatically. The loop IS the operator — "needs human triage" is never an acceptable classification for anything the loop can reason about. When in doubt, make the call.

**PARK TRANSITION** — STALLED → PARKED_OPERATOR_BLOCKED when ALL park criteria met for 2+ cycles.
**UNPARK CONDITIONS** — queue changed, watcher crashed, new telemetry, Plane task changed, safe retry became true, runtime config changed, new repos in tool output, execution outcome changed.

**INFRASTRUCTURE FIX ESCALATION** — before parking as OPERATOR_BLOCKED, always ask: "Is the root cause a bug in OC source code I can fix directly?" If yes → write the fix, run tests, commit. Do not park. PARKED_OPERATOR_BLOCKED is reserved for conditions requiring credentials, external infra changes, or human policy decisions — not code bugs with known fixes.

**SIGKILL TRIAGE — the loop decides:** When a task is SIGKILL'd and held by the SIGKILL guard, the loop must make one of these calls (do not defer):
1. **Target already resolved** → verify directly (run linter, check tests, etc.). If resolved → cancel task as stale remediation.
2. **Transient SIGKILL (OOM ruled out, retry-count < 3)** → re-queue to Ready for AI. Comment explaining the decision.
3. **Systematic SIGKILL (same task SIGKILL'd 2+ times, cause unclear)** → investigate kodo-stderr.log, dmesg, memory; if cause still unclear → re-queue once more (Rule 1 auto-cancels at retry-count≥3).
4. **Dead remediation** → add dead-remediation label; Rule 1 cancels on next cycle.

**EXECUTOR FAILURE INVESTIGATION** — if haiku JSON shows executor_investigation.triggered=true, analyze before re-queuing. OOM ruled out when memory_free_gb > 8. SIGKILL without OOM = kodo timeout or task-specific failure.

### STEP 4 — CONVERGENCE PROMOTION CHECK

For each repeated loop-only judgment: create/update Plane task to promote into responsible watcher. Same judgment in 2+ cycles = promotion candidate.

### STEP 5 — EXECUTION GATE

Direct fix allowed ONLY when ALL hold:
(a) reproduced this cycle (in Haiku JSON), (b) scoped to specific repo from tool output, (c) implementation-level, (d) not credentials/infra blocked, (e) no destructive cleanup, (f) no runtime-policy widening, (g) not dead-remediation/starvation/closed-loop stagnation.

**IMPORTANT — stagnation ≠ no fix available.** Condition (g) blocks retrying the *stagnating task* via autonomy-cycle. It does NOT block writing a direct infrastructure fix to the code that causes the stagnation. If a PARKED_OPERATOR_BLOCKED pattern has a known root cause traceable to a specific bug in OC source code (board_worker, board_unblock, execute.main, watchers, etc.), that is an implementation-level fix that passes conditions (a)–(f) and MUST be attempted directly — do not wait for operator. The fix target is the infrastructure, not the stagnating task. Example: 8871f757 cycling → root cause is empty result.json handling in board_worker → fix board_worker directly.

### STEP 6 — DIRECT FIXES (only if loop owns lock)

```bash
source .env.operations-center.local
scripts/operations-center.sh autonomy-cycle --config config/operations_center.local.yaml --execute --repo <path>
```
kodo max_concurrent=1 — never dispatch two repos simultaneously.

TRAINING MODE: sandbox_base_branch=operations-center-testing-branch. OC (self_repo_key) MAY be dispatched via autonomy-cycle. proposer auto-adds self-modify:approved. No extra gate needed.

### STEP 7 — INVARIANT ENFORCEMENT

```bash
source .env.operations-center.local
.venv/bin/pytest tests/unit/er000_phase0_golden/ -q --tb=short
```
Run targeted tests for any repo touched in STEP 6. Failure → Plane task.

### STEP 8 — WATCHER RESTART DECISIONS

From Haiku JSON watchers array:
- exit_code=143: benign, ignore.
- exit_code=1/2 AND consecutive_non143 ≥ 2: Plane task if none exists; do NOT blindly restart.
- exit_code=1/2 AND consecutive_non143 = 1: monitor next cycle before escalating.
- exit_code=0: unexpected, investigate.

**ANTI-FLAP:** same watcher crashes non-143 in TWO consecutive cycles → Plane task, do NOT blindly restart.

---

## PHASE 3 — OUTPUT

### STEP 9 — LOG + COMMIT

Append structured cycle summary to .console/log.md:
```
## 2026-MM-DD — Watchdog cycle N: <STATE> — <driving signal>

**Convergence:** <classification>

**STEP 1:** custodian: <result> | ghost: <result> | flow: <gaps> | graph: <ok/error> | reaudit: <repos> | regressions: <count>

**STEP 2:** triage: <summary>

**STEP 2.5 board-unblock:**
- APPLIED: <list>
- SKIPPED: <list>

**STEP 7:** <test results if run>
**STEP 8:** <watcher summary>

**Cadence:** <STATE> (<seconds>s) — <reason>
```

Update .console/backlog.md if any tasks were completed or newly blocked.
Commit to branch oc-watchdog/<YYYYMMDD-HHMM>-<topic>. One commit per repo per cycle.
Run `git diff --staged` before committing.

### STEP 10 — ADAPTIVE SCHEDULEWAKEUP

| State | Delay | Trigger |
|-------|-------|---------|
| CRITICAL | 180s | crash loops / graph broken / autonomy failing |
| DEGRADED | 300s | non-143 crashes / blocked queue unchanged / flow gaps |
| STALLED | 600s | starvation / closed-loop stagnation / no forward progress |
| ACTIVE | 900s | direct fixes dispatched / remediation in flight |
| PARKED_OPERATOR_BLOCKED | 1800s | root cause known, Plane escalation exists, no new evidence |
| HEALTHY | 3600s | all clean, no starvation signals, all watchers up |

Use WORST state observed across all signals. Log chosen cadence and driving signal.

Call ScheduleWakeup with:
- prompt: contents of this file path prefixed with `/loop `:
  `/loop Run the OC watchdog loop. Read /home/dev/Documents/GitHub/OperationsCenter/.console/watchdog_loop_prompt.md and follow it exactly.`
- delaySeconds: per table above
- reason: one sentence naming the driving signal

---

## KNOWN OPEN ISSUES

- **Campaign 10c50210**: STALLED. AgentTopology Done (v0.3.0). ShippingForm (2b5ff37e) Blocked (SIGKILL'd). Phase-gated tasks (3fd02e75, 60390297, 6e32031c, d126bc51) await ShippingForm Done.
- **86c8c778**: Plane task tracking board_unblock SIGKILL guard — Backlog, OperationsCenter.
- **2824d46e**: Re-queued to R4AI (cycle 18 triage). Was exit-code:0 blocked; board_worker fix now in place. Monitor next execution.
- **a969024e**: Re-queued to R4AI (cycle 18 triage). SIGKILL'd once (retry-count:1); OOM ruled out. If SIGKILL'd again → investigate further; at retry-count≥3 Rule 1 auto-cancels.
- **8871f757, b67bc0e0**: CANCELLED (cycle 18 triage). Lint targets already resolved — ruff check passes cleanly.

## GOVERNING PRINCIPLE

The loop is the operator for all conditions handled here. Do NOT log "operator action required" for stuck patterns this tool covers. Do NOT create Plane escalation tasks for conditions with a direct programmatic remedy. Operator-blocked classification is reserved for conditions that genuinely require human decisions, infrastructure changes, or code changes that the loop cannot safely make itself.
