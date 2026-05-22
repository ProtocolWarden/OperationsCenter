# OC Watchdog Loop — Quick Start

Bring up the platform, then paste the `/loop` block into Claude Code.
Full runbook: [`docs/operator/watchdog_loop.md`](docs/operator/watchdog_loop.md)

---

## Step 0 — Sync all repos

```bash
for repo in OperationsCenter SwitchBoard TeamExecutor DAGExecutor CritiqueExecutor CoreRunner CxRP RxP PlatformDeployment PlatformManifest Custodian SourceRegistry OperatorConsole RepoGraph ProtocolWarden ProtocolWarden.github.io; do
  dir="/home/dev/Documents/GitHub/$repo"
  [ -d "$dir/.git" ] && echo "$repo: $(git -C "$dir" pull --ff-only 2>&1 | tail -1)"
done
```

---

## Step 1 — Bring up the platform

```bash
# Plane
scripts/operations-center.sh plane-up

# PlatformDeployment / SwitchBoard
cd /home/dev/Documents/GitHub/PlatformDeployment
docker compose -f compose/docker-compose.yml -f compose/profiles/core.yml up -d
cd /home/dev/Documents/GitHub/OperationsCenter

# OC watchers
scripts/operations-center.sh watch-all
scripts/operations-center.sh watch-all-status   # expect: 8 running
```

---

## Step 2 — Training mode: reset training branches (skip for production)

```bash
scripts/reset-training-branches.sh
```

---

## Step 3 — Start the controller

```bash
cd /home/dev/Documents/GitHub/OperationsCenter
nohup python tools/loop/controller.py > /dev/null 2>&1 &
python tools/loop/controller.py --status   # confirm running
# To stop:  python tools/loop/controller.py --stop
# Log:      logs/local/loop_controller.log
```

Each iteration is a fresh `claude -p` session — context never accumulates across cycles.
The session writes `.console/loop_schedule.json` at STEP 10; the controller reads it for
adaptive delay before spawning the next session.

### What the controller passes to each session

```
Run the OC/Platform stabilization and audit cycle from /home/dev/Documents/GitHub/OperationsCenter. Source .env.operations-center.local first. Use .venv/bin/ for all CLIs. This loop is controller-driven — do NOT call ScheduleWakeup.

STEP 0 — OWNERSHIP + PREFLIGHT:
Acquire/verify logs/local/watchdog_loop.lock via:
  scripts/operations-center.sh watchdog-loop-acquire
If another live owner exists, abort. If stale, reclaim.
Sync all repos:
  for repo in OperationsCenter SwitchBoard TeamExecutor DAGExecutor CritiqueExecutor CoreRunner CxRP RxP PlatformDeployment PlatformManifest Custodian SourceRegistry OperatorConsole RepoGraph ProtocolWarden ProtocolWarden.github.io; do
    dir="/home/dev/Documents/GitHub/$repo"
    [ -d "$dir/.git" ] && echo "$repo: $(git -C "$dir" pull --ff-only 2>&1 | tail -1)"
  done
Then confirm: Plane at http://localhost:8080, PlatformDeployment/SwitchBoard at http://localhost:20401/health, all 8 OC watchers running, .venv CLIs present, runtime low-cost policy (sonnet/haiku), team_executor max_concurrent=1 in config, working tree state via git status.

STEP 1 — INVESTIGATE (run in parallel where safe):
  .venv/bin/operations-center-custodian-sweep --config config/operations_center.local.yaml --emit
  .venv/bin/operations-center-ghost-audit     --config config/operations_center.local.yaml --since 1h
  .venv/bin/operations-center-flow-audit      --config config/operations_center.local.yaml
  .venv/bin/operations-center-graph-doctor
  .venv/bin/operations-center-reaudit-check   --json
  .venv/bin/operations-center-check-regressions --config config/operations_center.local.yaml --lookback-hours 1 --dry-run
Collect exit codes and finding counts. Determine affected repos only from tool output — not from vibes or unrelated logs. If an affected repo cannot be determined confidently, create a Plane task and skip direct execution for that finding.

EXECUTOR FAILURE INVESTIGATION — run this whenever triage output shows executor_exit_code/executor_signal
on a blocked task, OR whenever watcher logs mention a signal kill. Run BEFORE creating a Plane task.
If root cause is determinable from these logs, include it directly in the task or fix:
  # Board worker logs — blocked tasks with signal exits
  grep -h "board_worker.*blocked\|exit_code\|executor" logs/local/watch-all/*.log 2>/dev/null | grep -v "^$" | tail -40
  # OS OOM killer evidence
  dmesg | grep -iE "oom|killed process|out of memory" | tail -20
  journalctl -k --since "2h ago" 2>/dev/null | grep -iE "killed|oom" | tail -20
  # System memory at time of investigation
  free -h
  # Most recent executor stderr artifacts
  find logs/ -name "executor-stderr.log" 2>/dev/null | sort -t/ -k1 | tail -3 | \
    xargs -I{} sh -c 'echo "=== {} ==="; tail -40 "{}"'
This investigation applies to ALL backends (team_executor, aider, etc.). Any executor
that exits with a signal or unexpected code should be investigated the same way.

STEP 2 — TRIAGE:
  .venv/bin/operations-center-triage-scan --config config/operations_center.local.yaml --apply
This scan now includes queue self-healing when tasks carry structured evidence labels:
  retry_safe, queue_deadlock/no_consumer, dedup:<key>, retry-lineage:<id>.
It may transition Blocked→Ready-for-AI or Blocked→Backlog only when retry budgets
and safety evidence allow it; otherwise it comments an escalation.

STEP 2.5 — AUTONOMOUS BOARD UNBLOCKING:
  .venv/bin/operations-center-board-unblock --config config/operations_center.local.yaml --apply

GOVERNING PRINCIPLE: The loop is the operator for all conditions handled here.
  Do NOT log "operator action required" for stuck patterns this tool covers.
  Do NOT create Plane escalation tasks for conditions with a direct programmatic remedy.
  When a new stuck pattern appears in Step 3 investigation, ADD A RULE HERE — not a note.
  Operator-blocked classification is reserved for conditions that genuinely require human
  decisions, infrastructure changes, or code changes that the loop cannot safely make itself.

This step resolves known stuck patterns without human intervention:
  Rule 1 DEAD_REMEDIATION_CANCEL — cancel tasks labelled dead-remediation OR with ≥3 SIGKILL retries
    that are not already in a terminal state (Cancelled/Done).
  Rule 2 INVESTIGATE_DEPRIORITISE — move task-kind:investigate tasks out of Ready for AI → Backlog.
    No board_worker consumer exists for this task-kind; they stall the R4AI slot indefinitely.
  Rule 3 IMPROVE_UNBLOCK — move improve tasks from Blocked → Backlog when:
    (a) their blocking dependency (blocked-by: label) is now Cancelled/Done, OR
    (b) they have been Blocked for >4h with no executor progress (stale).
  Rule 4 SELF_MODIFY_REQUEUE — move self-modify:approved tasks from Blocked → Ready for AI when:
    (a) their blocked-by dependency is absent, OR
    (b) their blocked-by dependency is now Cancelled/Done.
    Operator approval is already on record; holding these Blocked is queue waste.
Log all actions taken (applied or would_apply) in the cycle summary.

STEP 3 — BLOCKED/STALLED WORK INVESTIGATION:
Read the last 3 cycle summaries from .console/log.md to identify repeated patterns.

STARVATION — classify immediately (single cycle is sufficient) when ANY of these hold:
  - Propose repeatedly emits candidates that are skipped because duplicates exist in Blocked
  - tasks_created=0 or None across consecutive propose runs while Blocked count is nonzero
  - Ready-for-AI never drains despite work existing on the board
  - Blocked tasks with self-modify:approved never transition state
  - Same remediation candidates generated repeatedly with zero queue movement
Do NOT require multiple cycles of evidence when a closed retry/no-progress loop is already demonstrated.
Do NOT classify demonstrated starvation as "potential starvation" or "monitoring for recurrence."

CLOSED-LOOP STAGNATION — classify and escalate immediately when:
  - The platform repeatedly generates or retries equivalent remediation while queue/task/execution state does not materially change
  - Duplicate suppression is causing a deadlock (propose skips because Blocked duplicates exist; workers never consume Blocked; no one re-queues them)
  - Ready-for-AI count stays at zero while blocked count stays nonzero across a cycle
  - Autonomy-cycle retries produce no net task state change

Then actively investigate all of:
  (a) Plane tasks stuck in Ready-for-AI for >2 cycles without being claimed
  (b) Tasks in Blocked with self-modify:approved that should be Ready-for-AI
  (c) Same findings repeating from STEP 1 across consecutive cycles
  (d) Repos skipped repeatedly by the execution gate
  (e) Same regressions recurring across cycles
  (f) Autonomy-cycle failures in recent cycles
  (g) Flow-audit gaps open across multiple cycles
  (h) Graph invariants remaining broken
  (i) propose tasks_created=0/None while candidates are being emitted
  (j) Duplicate suppression causing queue deadlock (blocked duplicates preventing new task creation)

QUEUE-UNBLOCKING INVESTIGATION — when starvation or closed-loop stagnation is detected:
  - Identify why Blocked tasks remain blocked (failed execution? manual block? phase gate?)
  - Check whether duplicate suppression is the deadlock cause
  - Determine whether tasks should safely move: Blocked→Backlog or Blocked→Ready-for-AI
  - Do not blindly mutate queue state — but investigate the unblock path and escalate immediately

WATCHER HANDOFF INVESTIGATION — for each blocked/stalled item:
  - Which watcher produced this state?
  - Which watcher should consume this state next?
  - Did the producing watcher emit enough structured evidence for the consumer to act?
  - Did a handoff contract fail? (producer emitted; consumer ignored or errored)
  - Did the queue state become non-consumable by any watcher?
  - Is this a missing watcher behavior or a broken watcher behavior?
If the answer required manual log inference, that inference is a promotion candidate.

BEHAVIORAL CONVERGENCE CHECK — after stagnation/starvation classification:
Read the last 3 cycle summaries and classify automation behavior:
  CONVERGENT: retries materially evolve platform state toward resolution
  WEAKLY-CONVERGENT: progress occurring slowly but directionally
  NON-CONVERGENT: retries reproduce semantically equivalent outcomes with no net state change
  DIVERGENT: automation making platform health measurably worse each cycle

Classify NON-CONVERGENT when ANY hold:
  - Same propose duplicates skipped in 2+ consecutive cycles with no queue evolution
  - Same repo targeted by autonomy-cycle 3+ times with same failure or no-change outcome
  - Regression retries recreate identical findings each cycle
  - Blocked tasks recycled to Ready-for-AI with the same execution outcome repeatedly
  - Remediation titles/labels/root-causes semantically equivalent across multiple cycles

Classify DIVERGENT when:
  - Board health metrics worsening vs prior cycles despite active remediation
  - Blocked count increasing cycle-over-cycle while remediation runs
  - Retries introducing new regressions rather than resolving existing ones

SEMANTIC DUPLICATE DETECTION — compare across cycles:
  - Task titles with high textual similarity targeting the same repo
  - Same root-cause keywords in consecutive remediation attempts
  - Same regression signature reproduced after a "successful" fix
  - Same failure outcome for the same repo+task_type combination
If detected: classify non-convergent, escalate via Plane task, do NOT retry equivalent path.

REMEDIATION LINEAGE — before any direct fix or Plane task, check:
  - How many prior cycles targeted this exact finding?
  - Did prior remediation attempts change the execution outcome?
  - Did the remediation strategy adapt after failure?
If 2+ equivalent prior attempts with no outcome change: classify dead-remediation.
Do NOT replay identical remediation paths. Include prior-attempt history in the Plane task.

AUTOMATION SELF-DECEPTION — classify and escalate immediately when:
  - Retries/cycles occur but queue/task state never changes (activity without state evolution)
  - Tasks recreated under new IDs with semantically equivalent scope
  - Watchers healthy and propose active, but board state frozen across 2+ cycles
  - Remediation logs "completed" but the same regression immediately recurs
This condition forbids HEALTHY cadence regardless of individual audit cleanliness.

EXECUTOR-QUALITY INVESTIGATION — when non-convergent, divergent, or self-deception is classified:
  Investigate what the automation/framework actually DID, not merely whether it ran:
  - Did any retry change the execution strategy?
  - Did the planner emit tasks that evolved the queue state?
  - Did the autonomy-cycle pick a different remediation path after prior failure?
  - Did the propose stage adapt its candidate selection after repeated skips?
  - Did the execution path ever reach a worker, or was it blocked before dispatch?
  Do not accept "automation ran" as evidence of quality. Require evidence of adaptation.

Classify each blocked item:
  - temporarily-blocked: retry next cycle — only valid when forward progress IS occurring elsewhere
  - infra-blocked: platform instability preventing execution
  - ownership-ambiguous: affected repo not determinable
  - validation-blocked: failing tests or regressions
  - structurally-blocked: requires operator or design action
  - crash-looping: same watcher or process failing repeatedly
  - starvation: work exists and candidates generated, but no net forward progress
  - dead-remediation: retries occurring without any state improvement or adaptation
  - closed-loop stagnation: activity without measurable queue or execution progress
  - non-convergent: retries reproducing equivalent outcomes with no evolution
  - divergent: automation making platform health measurably worse
  - operator-blocked: root cause known, direct remediation impossible, requires operator/infrastructure action

For starvation, dead-remediation, closed-loop stagnation, non-convergent, divergent, or structurally-blocked: create/update Plane task immediately. Do not use "monitor for recurrence" language. Do not retry identical failing remediation paths.

OPERATOR-BLOCKED CLASSIFICATION — classify as operator-blocked when ALL hold:
  - Root cause is already known and has not changed across ≥3 cycles
  - A Plane escalation task exists and covers the blocker
  - No queue evolution has occurred across those cycles
  - No safe retry path exists (unsafe, pointless, or infrastructure-gated)
  - No new evidence has emerged (see NEW EVIDENCE EVALUATION below)
Required metadata: blocker_summary, first_detected_cycle, affected_tasks, related_plane_tasks,
  safe_retry_condition, last_new_evidence_cycle, retry_forbidden_reason.

NEW EVIDENCE EVALUATION — evaluate each cycle when in STALLED or PARKED state.
NEW_EVIDENCE_DETECTED = yes ONLY if at least one of these changed since the prior cycle:
  - watcher state (new crash, new PID, new exit code or signal)
  - queue state (Blocked/R4AI/InReview counts changed)
  - remediation outcome (different result for same task)
  - exit signature (new signal, exit code, or failure message)
  - stacktrace or error detail (changed content, not just timestamp)
  - regression profile (new or resolved regression)
  - task transitions (any Plane task moved state)
  - runtime behavior (config changed, new watcher behavior)
  - graph state (node/edge counts changed)
  - telemetry detail (new structured field emitted by a watcher)
  - execution path (new code path reached or blocked)
Repeated identical observations are NOT new evidence. Timestamp differences alone are NOT new evidence.

PARK TRANSITION — evaluate STALLED → PARKED_OPERATOR_BLOCKED when ALL hold:
  - operator-blocked classification is active this cycle
  - Same root cause for ≥3 consecutive cycles (no root-cause change)
  - Same affected tasks across those cycles
  - Plane escalation exists and is current
  - No queue evolution across those cycles
  - No remediation adaptation across those cycles
  - NEW_EVIDENCE_DETECTED = no for 2+ consecutive cycles
  - No safe retry path
When parked: do NOT rerun deep investigation each cycle. Check only for evidence change (unpark conditions).
Do NOT remain in STALLED indefinitely once park criteria are met.

UNPARK CONDITIONS — check each parked cycle. If ANY hold, transition back to STALLED/DEGRADED/ACTIVE:
  - Queue state changed (any count difference)
  - Watcher crashed or restarted unexpectedly (non-143)
  - New telemetry appeared (exit signal, stacktrace, or error message changed)
  - Plane task status changed (escalated, commented on, closed, or resolved)
  - Operator took action on the blocker
  - Safe retry condition became true
  - Runtime config changed
  - New affected repos appeared in tool output
  - Execution outcome changed from prior identical attempts
If no unpark condition holds: remain parked, schedule at PARKED_OPERATOR_BLOCKED cadence (1800s).

FORWARD PROGRESS CHECK — before classifying as temporarily-blocked, confirm at least one of:
  - Blocked count decreased vs prior cycle
  - Ready-for-AI drained vs prior cycle
  - Task state transitions occurred
  - Regressions resolved
  - Watcher stabilized after crash
  - Autonomy-cycle outcomes improved
  - Remediation strategy demonstrably adapted (not just re-ran)
If none apply and remediation is actively running, classify as stagnation, not temporary delay.

KNOWN OPEN ISSUES (carry forward until resolved, remove when closed):
- 9c7f4bb9: executor SIGKILL (-9) confirmed. Executor exited -9 at "Analyzing project and creating plan".
  Hypothesis: time-of-day resource exhaustion, not task complexity. Root cause not yet determined.
  Investigate via STEP 1 EXECUTOR FAILURE INVESTIGATION (dmesg, journalctl, free -h).
  In training mode, OC tasks and ShippingForm (2b5ff37e) MAY be re-queued once investigation
  identifies root cause and confirms safe retry conditions. Do not re-queue blindly before that.
- Campaign 10c50210: STALLED. AgentTopology Done (v0.3.0). ShippingForm (2b5ff37e) Blocked (SIGKILL'd).
  ShippingForm may be re-queued after SIGKILL root cause is determined.
  Test/Improve Backlog tasks (3fd02e75, 60390297, 6e32031c, d126bc51) remain phase-gated until
  ShippingForm reaches Done.

STEP 4 — CONVERGENCE PROMOTION CHECK:
For each blocked/stalled/non-convergent behavior found in STEP 3, ask:
  - Did the /loop perform this same judgment in a prior cycle?
  - Which watcher should eventually own this detection or transition?
  - Did the responsible watcher emit enough structured evidence?
    (duplicate candidate count, skipped candidate reason, blocked task reason,
     last attempted remediation id, prior remediation lineage, retry strategy changed,
     queue transition attempted, handoff target watcher, why task is not executable,
     why task is safe/unsafe to re-queue)
  - Is there a watcher handoff gap?
    (Which watcher produced this state? Which watcher should consume it?
     Did the producing watcher emit enough evidence for the next watcher?
     Did a handoff contract fail? Is the queue state non-consumable by any watcher?
     Is this missing watcher behavior or broken watcher behavior?)
  - Should this become a Plane task for watcher improvement, guardrail enforcement,
    or telemetry improvement?
If the same loop-only judgment occurred in 2+ cycles: create/update a Plane task to
promote that behavior into the responsible watcher or guardrail.
Do NOT redesign immediately. Capture evidence and ownership.
Do NOT create watcher redesign tasks for one-off failures unless they reveal a missing
invariant or repeated pattern.

STEP 5 — EXECUTION GATE:
For each finding, decide: Plane task only vs direct fix.
Direct fix is allowed ONLY when ALL of these hold:
  (a) finding reproduced in the current cycle
  (b) scoped to a specific repo determined from tool output
  (c) implementation-level work (not ADR / policy / design-only)
  (d) not blocked on credentials or operator infrastructure
  (e) not requiring destructive cleanup (no git reset --hard, no volume wipes)
  (f) not requiring runtime-policy widening (no max_concurrent increase, no model upgrade)
  (g) not classified as dead-remediation, starvation, or closed-loop stagnation in STEP 3
If any condition fails → create/update Plane task, skip direct fix.

STEP 6 — DIRECT FIXES (only if loop owns the lock):
For each affected repo that passes the execution gate, run one at a time:
  scripts/operations-center.sh autonomy-cycle --config config/operations_center.local.yaml --execute --repo <path>
Respect team_executor max_concurrent=1 — do not dispatch two repos simultaneously.

TRAINING MODE — OC SELF-MODIFICATION:
In training mode (sandbox_base_branch = operations-center-testing-branch), the self_repo_key repo
(OperationsCenter) MAY be dispatched via autonomy-cycle like any other managed repo. Changes land
on the testing branch, not main — the operator reviews before merging. The proposer already adds
"self-modify: approved" to all OC-targeted tasks automatically. No additional approval gate is
needed in training mode. Dispatch OC tasks the same way as any other repo.

STEP 7 — INVARIANT ENFORCEMENT:
  .venv/bin/pytest tests/unit/er000_phase0_golden/ -q --tb=short
Also run targeted tests for any repo touched in STEP 6.
If any test fails and cannot be fixed safely in this cycle → create/update Plane task.

STEP 8 — WATCHER HEALTH + RESTART INVESTIGATION:
  scripts/operations-center.sh watch-all-status
  grep -h "watcher_restart\|exit_code\|ERROR\|Traceback" logs/local/watch-all/*.log | tail -50
Classify restarts:
  - exit 143 (SIGTERM): benign deliberate stop/restart — note only
  - exit 1/2: crash — read log context, find root cause, fix config/code or open Plane task
  - exit 0: unexpected clean exit — read last 30 lines of that watcher log
Anti-flap rule: if the same watcher crashes unexpectedly (non-143) in TWO consecutive cycles,
do NOT blindly restart it — escalate with a Plane task instead. Do not pretend the platform
is healthy if a watcher cannot be restarted cleanly.
Restart a stopped watcher (only after root-cause is understood):
  scripts/operations-center.sh watch --role <role>

STEP 9 — LOG + COMMIT HYGIENE:
Append the structured cycle summary (see template in runbook) to .console/log.md.
The summary MUST include behavioral convergence fields AND convergence promotion fields:
  - Convergence phase estimate: <1-7>
  - Loop-owned recovery decisions this cycle: <N>
  - Watcher-owned recoveries this cycle: <N>
  - Automatic recovery actions executed: <list or "none">
  - Manual inference required: <yes/no + reason>
  - Recovery ownership migration candidates: <details or "none">
  - Behavioral convergence: <convergent|weakly-convergent|non-convergent|divergent>
  - Executor adaptation observed: <yes/no + reason>
  - Semantic duplicate remediation suspected: <yes/no>
  - Automation self-deception detected: <yes/no>
  - Retry quality: <adaptive|repetitive|degenerate>
  - Queue evolution quality: <healthy|stalled|cycling>
  - Convergence promotion candidates: <watcher=behavior,...> or "none"
  - Loop-only judgments repeated: <judgment=N cycles,...> or "none"
  - Watcher handoff gaps: <producer→consumer: gap,...> or "none"
  - Missing watcher evidence: <watcher=evidence needed,...> or "none"
  - Behavior to move out of /loop: <details or "none">
  - Convergence maturity metrics: loop_only_judgments_per_cycle=<N>,
    manual_inference_events=<N>, watcher_owned_recovery_rate=<0-1>,
    automatic_queue_heal_rate=<0-1>, parked_transition_accuracy=<0-1>,
    recovery_adaptation_rate=<0-1>, operator_escalation_rate=<0-1>
  - Operator-blocked state: <yes/no>
  - Parked state active: <yes — since cycle N | no>
  - Park reason: <blocker summary or "none">
  - New evidence detected: <yes — detail | no>
  - Safe retry condition: <condition or "none">
  - Last evidence-changing cycle: <cycle id or "N/A">
  - Repeated unchanged cycles: <N>
  - Active remediation suspended: <yes — reason | no>
Update .console/backlog.md for any new/closed gaps.
IMPORTANT: Run git diff --staged before committing to ensure only loop-owned files are staged.
Commit only after validation passes. Do not commit to main unless the operator has
explicitly allowed it for the current task/session. If not allowed, create a branch:
  git checkout -b oc-watchdog/<YYYYMMDD-HHMM>-<short-topic>
One logical commit per repo per cycle. Commit message must name: root cause, affected repo,
gate/check fixed. Never force-push, amend old loop commits, or commit generated noise.

STEP 10 — ADAPTIVE SCHEDULEWAKEUP:
Assess platform health state and choose ScheduleWakeup delay accordingly:

  CRITICAL                — crash loops / graph broken / autonomy failing repeatedly:              180s
  DEGRADED                — watcher crashes (non-143) / blocked queue unchanged / flow gaps:       300s
  STALLED                 — starvation active / closed-loop stagnation / no forward progress:      600s
  ACTIVE                  — direct fixes dispatched this cycle / remediation in flight:            900s
  PARKED_OPERATOR_BLOCKED — root cause known, Plane escalation exists, no new evidence:           1800s
  HEALTHY                 — all audits clean, no starvation signals, all watchers up:             3600s

PARK TRANSITION DECISION — evaluate STALLED → PARKED_OPERATOR_BLOCKED at end of each STALLED cycle:
  If ALL of the following hold, transition to PARKED (skip deep investigation next cycle):
    - operator-blocked classification active this cycle
    - Same root cause for ≥3 consecutive cycles (no root-cause change)
    - Same affected tasks across those cycles
    - Plane escalation exists and is current
    - No queue evolution across those cycles
    - No remediation adaptation across those cycles
    - NEW_EVIDENCE_DETECTED = no for 2+ consecutive cycles
    - No safe retry path
  If parked: set delaySeconds=1800, check only unpark conditions next cycle.
  Do NOT remain STALLED indefinitely once park criteria are met.

UNPARK TRANSITION DECISION — evaluate each parked cycle before scheduling:
  If ANY hold, transition back to STALLED/DEGRADED/ACTIVE (run full cycle):
    - Queue state changed (any count difference)
    - Watcher crashed or restarted unexpectedly (non-143)
    - New telemetry appeared (exit signal, stacktrace, or error message changed)
    - Plane task status changed (escalated, commented on, closed, or resolved)
    - Operator took action on the blocker
    - Safe retry condition became true
    - Runtime config changed
    - New affected repos appeared in tool output
    - Execution outcome changed from prior identical attempts
  If no unpark condition holds: remain parked at 1800s.

FORBIDDEN: Do not choose HEALTHY cadence if any of these are true:
  - starvation classified this cycle
  - closed-loop stagnation detected
  - Blocked tasks with self-modify:approved and zero Ready-for-AI
  - propose tasks_created=0/None while candidates emitted
  - blocked count unchanged from prior cycle while remediation ran
  - behavioral convergence classified as non-convergent or divergent
  - automation self-deception detected
  - executor signal-kill (SIGKILL/SIGTERM) confirmed this cycle AND root cause not yet determined
  - 2+ consecutive cycles with semantically equivalent failed remediation and no adaptation

FORBIDDEN: Do not choose STALLED cadence when PARKED_OPERATOR_BLOCKED criteria are met.
  PARKED is not a downgrade — it is the correct state when the blocker is known, escalated,
  and no new evidence is available. Remaining at STALLED when park criteria are met wastes cycles.

Non-convergent automation: STALLED minimum cadence.
Divergent automation: DEGRADED minimum cadence.
Automation self-deception: DEGRADED minimum cadence + create Plane escalation task.

Use the WORST health state observed across all steps. Starvation/stagnation/convergence signals
force STALLED minimum immediately — single cycle evidence is sufficient.
Log the chosen cadence and the driving signal in the cycle summary.
Pass this full /loop prompt verbatim as the ScheduleWakeup prompt.
```
