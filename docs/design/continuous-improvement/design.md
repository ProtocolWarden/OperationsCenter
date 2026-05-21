# Continuous Improvement Schema Extension — Design

**Status:** DRAFT  
**Date:** 2026-05-21  
**Scope:** Platform work-item schema extension  
**Primary contract:** `OcPlanningProposal` + new `ContinuousImprovementSpec` block  
**Draft schema:** `docs/design/continuous-improvement/draft_schema.py`

---

## 1. Summary

This design extends the existing Platform work-item schema to support continuous-improvement workflows natively within existing operational lanes.

The extension adds a single optional block — `ContinuousImprovementSpec` — to `OcPlanningProposal`. Proposals that omit this block are unchanged. Proposals that include it opt into an evaluation-driven refinement lifecycle governed by OperationsCenter and backed by CLP continuity.

No new orchestration authority, watcher, or execution domain is introduced.

---

## 2. Conceptual Model

```
signal
→ work item (OcPlanningProposal + ContinuousImprovementSpec)
→ strategy (ImprovementStrategy.principle)
→ implementation (OcExecutionRequest → execution attempt)
→ evaluation (EvaluationSpec → EvaluationScore)
→ checkpoint (CLP LoopCheckpoint written between attempts)
→ refinement decision (accept / retry / abandon / escalate)
→ lineage artifact (LineageAttempt appended to ImprovementLineage)
```

This is not a separate subsystem. It is the existing proposal → lane → execution → result flow with evaluation and retry logic bolted on at the OC boundary.

---

## 3. Lifecycle

### 3.1 Full lifecycle diagram

```
OcPlanningProposal
│   execution_mode: improve_campaign
│   continuous_improvement: ContinuousImprovementSpec
│     strategy: ...
│     evaluation: ...
│     refinement: max_attempts=3
│     clp: ...
│
├─ OC: first dispatch
│   │
│   ├─ create ImprovementLineage (lineage_id)
│   ├─ write CLP InvestigationCapsule (.context/active/<id>.yaml)
│   ├─ create WorkerHandoff with worker_scope + lease
│   ├─ dispatch worker (existing lane: codex_cli / claude_cli)
│   │
│   └─ OcExecutionResult returned
│       │
│       ├─ OC runs evaluation (evaluation_command or heuristic scoring)
│       │   └─ EvaluationScore: outcome=IMPROVED / NEUTRAL / REGRESSED / GUARDRAIL_VIOLATED
│       │
│       ├─ OC emits RefinementDecision
│       │   ├─ ACCEPT  → append LineageAttempt, set status=ACCEPTED, propose for merge
│       │   ├─ RETRY   → append LineageAttempt, write LoopCheckpoint, re-dispatch (if budget)
│       │   ├─ ABANDON → append LineageAttempt, set status=ABANDONED, escalate if needed
│       │   └─ ESCALATE → append LineageAttempt, set status=ESCALATED, create board task
│       │
│       └─ budget check: attempts_used >= max_attempts → BUDGET_EXHAUSTED
│
├─ OC: subsequent attempt (RETRY path)
│   │
│   ├─ require checkpoint (if requires_checkpoint_between_attempts=true)
│   ├─ update ClpBinding with new checkpoint/handoff paths
│   ├─ optionally vary strategy.variation_hint
│   ├─ dispatch worker (same lane)
│   └─ repeat evaluation loop
│
└─ terminal outcome
    ├─ ACCEPTED: implementation proposed, lineage archived
    ├─ BUDGET_EXHAUSTED: all attempts failed; lineage archived; escalate
    ├─ ABANDONED: hard invariant violated; lineage archived; create board task
    └─ ESCALATED: operator decides; lineage preserved for inspection
```

### 3.2 State transitions

```
NOT_STARTED
    │
    ▼  (first dispatch)
IN_PROGRESS
    │
    ├──── attempt succeeds all guardrails ──────────────► ACCEPTED
    │
    ├──── attempt fails non-fatal gate, budget remains ── (retry) → IN_PROGRESS
    │
    ├──── attempt violates hard invariant ──────────────► ABANDONED
    │
    ├──── budget exhausted (attempts_used >= max_attempts) ► BUDGET_EXHAUSTED
    │
    └──── ambiguous outcome / operator needed ──────────► ESCALATED
```

---

## 4. Schema Extension

### 4.1 Integration point

```python
class OcPlanningProposal(BaseModel):
    ...
    # NEW — optional; absence preserves one-shot behavior
    continuous_improvement: Optional[ContinuousImprovementSpec] = None
```

No other fields on `OcPlanningProposal` change. The model remains frozen. Lineage is carried by the mutable `OcContinuousImprovementState`, not by the proposal itself.

### 4.2 Top-level structure

```python
class ContinuousImprovementSpec(BaseModel):
    strategy:   ImprovementStrategy    # what to try and hard constraints
    evaluation: EvaluationSpec         # how to score it and what blocks acceptance
    refinement: RefinementPolicy       # retry/budget policy
    clp:        ClpBinding             # CLP artifact path references
```

### 4.3 ImprovementStrategy

```python
class ImprovementStrategy(BaseModel):
    principle:      str          # one-sentence improvement heuristic
    constraints:    list[str]    # must include 'fail_closed'; propagated to worker_scope
    variation_hint: Optional[str]  # set by OC on retry if vary_strategy_on_retry=True
```

The `principle` is the semantic anchor for the worker. It becomes part of the goal_text when dispatching the execution request. The `constraints` propagate directly into the WorkerHandoff `worker_scope` — they are enforced by ContextGuard, not merely advisory.

### 4.4 EvaluationSpec

```python
class EvaluationSpec(BaseModel):
    baseline_description:  str
    baseline_artifact_path: Optional[str]    # path to baseline measurement
    primary_scoring:       ScoringMetric      # what to improve
    secondary_scoring:     list[ScoringMetric]  # tracked but not blocking
    guardrails:            list[str]          # ALL must pass; any violation → ABANDON
    evaluation_command:    Optional[str]      # deterministic scoring command
```

**Guardrails vs scoring:**

| | Guardrails | Primary scoring |
|---|---|---|
| Nature | Hard gates | Improvement target |
| Violation | ABANDON immediately | RETRY within budget |
| Examples | `custodian_clean`, `regression_fixtures_pass` | `duplicate_escalations_reduced` |
| Checked by | OC evaluation step + ContextGuard (Custodian) | OC evaluation step |

### 4.5 RefinementPolicy

```python
class RefinementPolicy(BaseModel):
    enabled: bool = True
    max_attempts: int = 3                            # total dispatches, including first
    requires_checkpoint_between_attempts: bool = True
    vary_strategy_on_retry: bool = False
    failure_penalty: int = 0                         # extra budget deducted on guardrail fail
    accept_on_neutral: bool = False                  # accept if no regression/no improvement
```

**Budget enforcement:**

```
attempts_remaining = max_attempts - attempts_used - (failure_penalty if guardrail_failed else 0)
```

If `attempts_remaining <= 0` after any attempt, status → `BUDGET_EXHAUSTED`.

### 4.6 ClpBinding

```python
class ClpBinding(BaseModel):
    investigation_capsule_path: Optional[str]  # .context/active/<id>.yaml
    loop_checkpoint_path:       Optional[str]  # .context/checkpoints/<id>.yaml
    worker_handoff_path:         Optional[str]  # .context/handoffs/<id>.yaml
    worker_scope_path:           Optional[str]
    lease_expires_at:            Optional[datetime]  # quick expiry check
```

These are path references only. The worker resolves them at runtime via the CLP schema. OC updates them after each attempt (writing to `OcContinuousImprovementState.clp_binding`).

---

## 5. CLP Integration Model

### 5.1 Artifact mapping

| CLP artifact | CI lifecycle role |
|---|---|
| `InvestigationCapsule` | Carries the improvement hypothesis, evidence gathered, and active hypotheses across retry attempts. Created on first dispatch; updated by the worker after each attempt. |
| `LoopCheckpoint` | Written by OC between attempts (required when `requires_checkpoint_between_attempts=true`). Carries `context_risk` flags; ContextGuard enforces these on the next dispatch. |
| `WorkerHandoff` | Scoped per dispatch. Contains the worker_scope section (allowed_paths, forbidden_paths, mutation_policy derived from `strategy.constraints`) and lease (expires_at from `RefinementPolicy` budget). |
| `worker_scope` | Derived from `ImprovementStrategy.constraints`. `fail_closed` maps to `mutation_policy: write_allowed` on allowed paths only; `no_runtime_policy_widening` maps to forbidden_paths excluding runtime config. |
| `lease` | `expires_at` set per-attempt based on `timeout_seconds` + buffer. `max_subagents` set from `RefinementPolicy`. |

### 5.2 CLP-enforced invariants

ContextGuard enforces these during CI refinement attempts:

| What | CLP mechanism | Enforcement |
|---|---|---|
| Worker scope (allowed_paths) | WorkerHandoff.worker_scope.allowed_paths | pre_write → BLOCK if outside |
| Forbidden paths (runtime config) | WorkerHandoff.worker_scope.forbidden_paths | pre_write → BLOCK |
| Read-only scope (if applicable) | WorkerHandoff.worker_scope.mutation_policy | pre_write → BLOCK |
| Lease expiry | WorkerHandoff.lease.expires_at | pre_action → BLOCK |
| Subagent budget | WorkerHandoff.lease.max_subagents | pre_spawn → BLOCK |
| Checkpoint required | LoopCheckpoint.context_risk.checkpoint_stale | pre_action → BLOCK |
| Session overrun | LoopCheckpoint.context_risk.long_lived_session | pre_action → WARN |

### 5.3 Continuity across retries

On each retry, OC:
1. Reads the previous attempt's `checkpoint_path` from `LineageAttempt`
2. Updates `InvestigationCapsule.handoff_notes` with findings from the previous attempt
3. Writes a fresh `LoopCheckpoint` with `checkpoint_stale: false`
4. Creates a new `WorkerHandoff` with a fresh lease
5. Updates `OcContinuousImprovementState.clp_binding` with new paths
6. Dispatches the worker with `ClpBinding` paths in the execution request

The worker on resume:
1. Reads `loop_checkpoint_path` → restores orchestrator context
2. Reads `investigation_capsule_path` → restores investigation state, hypotheses, evidence
3. Reads `worker_handoff_path` → understands task scope and lease
4. Continues from `handoff_notes` rather than restarting

This is explicit operational continuity via artifacts — not hidden prompt-state persistence.

---

## 6. Evaluation-Driven Refinement Flow

```
attempt N dispatched
│
├─ worker executes (bounded by WorkerHandoff lease + ContextGuard)
│
├─ OcExecutionResult returned to OC
│
├─ OC runs evaluation
│   ├─ if evaluation_command: run command, parse output → EvaluationScore
│   └─ if no command: heuristic scoring from OcExecutionResult artifacts
│
├─ guardrail check (ALL must pass)
│   ├─ custodian_clean → run Custodian, check 0 findings
│   ├─ regression_fixtures_pass → run validation_commands
│   ├─ no_lost_escalations → check escalation count vs baseline
│   └─ ... (any custom guardrail in evaluation.guardrails)
│
├─ primary metric check
│   ├─ IMPROVED → candidate for ACCEPT
│   ├─ NEUTRAL  → ACCEPT if accept_on_neutral=True, else RETRY
│   └─ REGRESSED → RETRY (if budget) or ABANDON
│
├─ OC emits RefinementDecision + EvaluationScore
│
└─ OC appends LineageAttempt to ImprovementLineage
```

### Scoring pipeline

OC evaluation runs after the `OcExecutionResult` is received. It produces an `EvaluationScore`:

```python
score = EvaluationScore(
    primary_metric_value=<measured from artifacts or command output>,
    primary_metric_delta=<value - baseline_value>,
    secondary_metrics={...},
    guardrails_passed=[...],
    guardrails_failed=[...],
    outcome=<IMPROVED|NEUTRAL|REGRESSED|GUARDRAIL_VIOLATED|INCONCLUSIVE>,
    evidence_paths=[...],
)
```

**Decision table:**

| outcome | guardrails | budget | decision |
|---------|-----------|--------|----------|
| IMPROVED | all passed | any | ACCEPT |
| NEUTRAL | all passed | any | ACCEPT if `accept_on_neutral`, else RETRY |
| REGRESSED | all passed | > 0 | RETRY |
| REGRESSED | all passed | = 0 | ABANDON (BUDGET_EXHAUSTED) |
| any | any failed | any | ABANDON (hard invariant) |
| INCONCLUSIVE | all passed | > 0 | RETRY with `variation_hint` |
| INCONCLUSIVE | all passed | = 0 | ESCALATE |

---

## 7. Lineage and Provenance Model

### 7.1 Structure

```
ImprovementLineage
│   lineage_id: "lin-20260521-watchdog-cooldown"
│   status: IN_PROGRESS
│   current_attempt_number: 2
│   accepted_attempt_number: null
│
├─ LineageAttempt #1
│   run_id: "run-abc123"
│   strategy_used: {principle: "fingerprint repeated failures and apply cooldown"}
│   started_at: "2026-05-21T14:00:00Z"
│   completed_at: "2026-05-21T14:28:00Z"
│   implementation_artifact_path: ".context/capsules/lin-001/attempt-1/diff.patch"
│   evaluation_artifact_path:     ".context/capsules/lin-001/attempt-1/eval.json"
│   checkpoint_path:               ".context/checkpoints/chk-20260521-1430.yaml"
│   score: {outcome: REGRESSED, primary_metric_delta: +2.0, guardrails_failed: []}
│   decision: RETRY
│   replay_metadata: {base_commit_sha: "abc123", goal_hash: "sha256:...", ...}
│
└─ LineageAttempt #2
    run_id: "run-def456"
    strategy_used: {principle: "...", variation_hint: "use 1h cooldown window not 30m"}
    started_at: "2026-05-21T14:35:00Z"
    ...
```

### 7.2 Lineage branching

A lineage branch is created when:
- `operator_restart` — operator explicitly restarts the refinement
- `strategy_variation` — OC varies the strategy on retry (when `vary_strategy_on_retry=True`)

Branched lineages carry `parent_lineage_id` pointing to the originating lineage. This allows the full provenance tree to be reconstructed:

```
lin-001 (initial)
├─ attempt 1 (RETRY)
├─ attempt 2 (RETRY, strategy varied)
└─ attempt 3 (ABANDONED)

lin-002 (operator_restart, parent=lin-001)
├─ attempt 1 (IMPROVED → ACCEPTED)
```

### 7.3 Artifact paths

All artifacts use a stable path convention under `.context/capsules/<lineage_id>/`:

```
.context/capsules/<lineage_id>/
  attempt-1/
    diff.patch
    eval.json
    capsule-snapshot.yaml
  attempt-2/
    diff.patch
    eval.json
    capsule-snapshot.yaml
  lineage.json              ← ImprovementLineage serialized
```

Long-term storage (after the proposal is resolved) moves to Warehouse via the standard artifact pipeline.

### 7.4 Replay semantics

Each `LineageAttempt.replay_metadata` captures:

```json
{
  "base_commit_sha": "abc123def456",
  "goal_text_hash": "sha256:...",
  "strategy_principle_hash": "sha256:...",
  "validation_command_hashes": ["sha256:...", "sha256:..."],
  "runtime_binding_kind": "cli_subscription",
  "runtime_binding_model": "claude-opus-4-7",
  "clp_schema_version": "0.1",
  "oc_schema_version": "0.3"
}
```

To replay attempt N:
1. Checkout `base_commit_sha` in the target repo
2. Reconstruct goal_text from proposal + strategy.principle
3. Load the `worker_handoff_path` snapshot from attempt N-1's checkpoint
4. Dispatch with the same runtime_binding
5. Run `evaluation_command` against the output

Replay is deterministic for deterministic workers (Custodian, validation commands). Non-deterministic workers (generative LLM) produce a semantically equivalent attempt, not bit-identical.

---

## 8. Governance Boundaries

```
┌─────────────────────────────────────────────────────────────────────┐
│ Intelligencer / Proposer                                            │
│   • signal intake                                                   │
│   • opportunity discovery                                           │
│   • creates OcPlanningProposal with ContinuousImprovementSpec       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ proposal (immutable after creation)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ OperationsCenter                                                    │
│   • EXECUTION AUTHORITY — all dispatches go through OC             │
│   • manages OcContinuousImprovementState                            │
│   • enforces RefinementPolicy (budget, checkpoint requirements)     │
│   • runs evaluation and emits RefinementDecision                    │
│   • updates ImprovementLineage after each attempt                   │
│   • creates / updates CLP artifacts (capsule, checkpoint, handoff)  │
└──────┬───────────────────────────────────────────────────────────────┘
       │ OcExecutionRequest (per attempt)
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│ SwitchBoard                                                         │
│   • lane routing (unchanged — CI proposals route like any other)   │
│   • sees execution_mode=improve_campaign                            │
└──────┬───────────────────────────────────────────────────────────────┘
       │ lane + backend selection
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Backend (CritiqueExecutor / TeamExecutor / claude_cli / etc.)       │
│   • bounded execution under WorkerHandoff lease                     │
│   • ContextGuard enforces path scope, lease expiry, subagent budget  │
│   • produces OcExecutionResult + artifacts                          │
└──────┬───────────────────────────────────────────────────────────────┘
       │ result
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Custodian                                                           │
│   • runs on every attempt's output (guardrail: custodian_clean)     │
│   • invariant enforcement is not bypassed by CI lifecycle           │
│   • findings cause ABANDON regardless of improvement score          │
└─────────────────────────────────────────────────────────────────────┘
```

**What CI refinement logic may NOT do:**
- Self-authorize deployment (OC must explicitly propose → SwitchBoard routes → backend executes)
- Widen runtime policy (WorkerHandoff.worker_scope is enforced by ContextGuard)
- Bypass Custodian (custodian_clean is a hard guardrail)
- Bypass OperationsCenter (all dispatches go through OC boundary)
- Mutate production state outside existing governance (standard lane + execution constraints apply)
- Extend its own lease (expires_at is set by OC, not the worker)

**Refinement is not authority. Evaluation is not orchestration.**

---

## 9. Component Responsibility Table

| Component | Responsibility in CI lifecycle |
|---|---|
| Intelligencer / Proposer | Creates OcPlanningProposal with ContinuousImprovementSpec; no execution authority |
| OperationsCenter | Execution governance, RefinementDecision, lineage management, CLP artifact lifecycle |
| SwitchBoard | Lane routing (unchanged); `improve_campaign` is just another execution_mode |
| CLP / ContextGuard | Continuity across attempts, lease/scope/checkpoint enforcement |
| Backend (executor) | Bounded execution per attempt; no knowledge of refinement lifecycle |
| Custodian | Invariant enforcement; custodian_clean is a mandatory guardrail |
| Warehouse | Long-term artifact storage for completed lineages |

---

## 10. Example Work Items

See `docs/design/continuous-improvement/examples/` for complete YAML examples.

### 10.1 Minimal — duplicate escalation reduction

```yaml
proposal_id: "prop-20260521-watchdog-cooldown"
task_id: "task-4421"
project_id: "operations"
task_type: "refactor"
execution_mode: "improve_campaign"
goal_text: "Reduce duplicate watchdog escalation tasks"
target:
  repo_key: "OperationsCenter"
  clone_url: "git@github.com:ProtocolWarden/OperationsCenter.git"
  base_branch: "main"
  allowed_paths: ["src/operations_center/watcher/", "tests/"]

continuous_improvement:
  strategy:
    principle: "Fingerprint repeated failures and apply cooldown window"
    constraints:
      - fail_closed
      - preserve_existing_escalations
      - no_runtime_policy_widening
  evaluation:
    baseline_description: "current_watchdog_behavior — 12 duplicate escalations per 24h"
    primary_scoring:
      metric: "duplicate_escalations_reduced"
      direction: "lower_is_better"
      baseline_value: 12.0
      target_delta: -4.0
    guardrails:
      - no_lost_escalations
      - custodian_clean
      - no_architecture_violations
      - regression_fixtures_pass
  refinement:
    enabled: true
    max_attempts: 3
    requires_checkpoint_between_attempts: true
    vary_strategy_on_retry: true
    failure_penalty: 1
  clp:
    investigation_capsule_path: ".context/active/inv-watchdog-cooldown.yaml"
    loop_checkpoint_path: ".context/checkpoints/chk-20260521-1400.yaml"
    worker_handoff_path: ".context/handoffs/handoff-20260521-001.yaml"
```

---

## 11. Failure-Mode Analysis

### F-01: Guardrail failure on first attempt

**Scenario:** Worker introduces a change that causes Custodian to find a boundary violation.

**What happens:**
1. OC runs evaluation; Custodian scan finds finding
2. guardrail `custodian_clean` → FAILED
3. `failure_penalty` deducted from budget (if > 0)
4. Decision: ABANDON (hard invariant violated, not a soft retry)
5. LineageAttempt appended with `decision=ABANDON`, `score.guardrails_failed=["custodian_clean"]`
6. InvestigationCapsule status → `blocked`
7. OC creates board task: `[CI Blocked] <proposal title> — guardrail violation`

**Not possible:** Retry past a guardrail failure.

---

### F-02: Budget exhausted without acceptance

**Scenario:** 3 attempts all score REGRESSED or INCONCLUSIVE, no guardrail violations.

**What happens:**
1. After attempt 3: `attempts_used (3) >= max_attempts (3)`
2. `RefinementStatus → BUDGET_EXHAUSTED`
3. All 3 LineageAttempts preserved in lineage
4. OC writes final LoopCheckpoint with `context_risk.checkpoint_stale: false`
5. InvestigationCapsule status → `abandoned`
6. OC creates board task: `[CI Budget Exhausted] <proposal title>`
7. Lineage archived to Warehouse

**Recovery:** Operator may create a new proposal with a different strategy (new lineage, `parent_lineage_id` pointing to exhausted one).

---

### F-03: Lease expiry mid-attempt

**Scenario:** Worker exceeds the WorkerHandoff `expires_at`.

**What happens:**
1. ContextGuard pre_action: `expires_at` in the past → BLOCK
2. Worker cannot make further tool calls
3. OcExecutionResult returned with `status=timed_out`
4. OC: `EvaluationOutcome=INCONCLUSIVE`
5. If budget remains: RETRY (as a timeout, not a guardrail violation)
6. If `failure_penalty > 0`, deduct from budget
7. New WorkerHandoff issued for retry with fresh lease

**The lease mechanism is enforced by ContextGuard, not by OC polling. Expiry is hard.**

---

### F-04: Checkpoint required but missing

**Scenario:** OC attempts to dispatch attempt N+1 but `requires_checkpoint_between_attempts=True` and no checkpoint exists for attempt N.

**What happens:**
1. OC validation before dispatch: check `LineageAttempt[N].checkpoint_path` exists and is readable
2. If missing: dispatch blocked at OC boundary (not by ContextGuard — this is an OC pre-dispatch check)
3. OC status → `ESCALATED`
4. Board task: `[CI Missing Checkpoint] Attempt N produced no checkpoint`

**Note:** ContextGuard also enforces this at the hook level: `context_risk.checkpoint_stale=True` → BLOCK. Both layers enforce independently.

---

### F-05: CLP capsule malformed on resume

**Scenario:** Worker resumes and attempts to load `investigation_capsule_path`; YAML is corrupt.

**What happens:**
1. ContextGuard (if `require_capsule=True`): detects malformed YAML → BLOCK with `malformed:<reason>`
2. Worker cannot proceed; attempt terminates with `status=cancelled`
3. OC: `EvaluationOutcome=INCONCLUSIVE`
4. OC flags `clp_binding.investigation_capsule_path` as suspect
5. If budget: RETRY with fresh capsule (status `active`, `parent_capsule_id` pointing to corrupt one)

---

### F-06: Evaluation command failure

**Scenario:** `evaluation_command` exits non-zero or produces unparseable output.

**What happens:**
1. OC evaluation step: command exit code != 0 or parse error
2. `EvaluationOutcome=INCONCLUSIVE`
3. `score.evaluation_notes` records the command failure
4. If budget: RETRY (evaluation failure is not a guardrail violation)
5. If no budget: ESCALATE (cannot determine outcome)
6. Evidence artifact preserves command stderr for inspection

---

### F-07: Worker produces no changes

**Scenario:** Backend completes with `status=succeeded` but `changed_files=[]`.

**What happens:**
1. `OcExecutionResult.status=succeeded` but no diff
2. OC evaluation: `EvaluationOutcome=NEUTRAL` (nothing changed = nothing improved)
3. If `accept_on_neutral=False`: RETRY
4. If `accept_on_neutral=True`: ACCEPT (the work item's goal may have been "clean up" something already resolved)

---

## 12. Open Questions (for operator decision)

These require explicit decisions before production implementation:

1. **Evaluation command ownership:** Who writes and validates the `evaluation_command`? Is it provided by the Proposer, by OC, or derived from validation_profile?

2. **Guardrail extensibility:** Should guardrails be a closed enum or open strings? Open strings are flexible but reduce automated enforcement. Closed enum allows ContextGuard integration per guardrail.

3. **Lineage storage:** Should lineage live in the repo's `.context/capsules/` (CLP-native) or in OC's run store? The design assumes `.context/` for now; Warehouse archival happens post-resolution.

4. **CxRP wire extension:** Does `ContinuousImprovementSpec` need to appear in the CxRP wire contract, or is it OC-internal? The current design keeps it OC-internal (not in CxRP `TaskProposal`), consistent with how `LifecycleMetadata` is handled.

5. **ExecutionMode entry:** Should a new `ExecutionMode.IMPROVE_CAMPAIGN_CI` distinguish CI-backed improve_campaign from the existing `improve_campaign`? Or is the presence of `continuous_improvement` block sufficient?

---

## 13. Implementation Checklist (when design is approved)

- [ ] Add `ContinuousImprovementSpec` and supporting types to `src/operations_center/contracts/`
- [ ] Add `Optional[ContinuousImprovementSpec]` to `OcPlanningProposal`
- [ ] Add `OcContinuousImprovementState` to run store
- [ ] Add evaluation runner: `src/operations_center/execution/ci_evaluator.py`
- [ ] Add refinement coordinator: `src/operations_center/execution/ci_coordinator.py`
- [ ] Wire CLP artifact creation into CI dispatch: capsule create, checkpoint write, handoff create
- [ ] Add `custodian_clean` guardrail implementation (run Custodian scan on result diff)
- [ ] Add `regression_fixtures_pass` guardrail implementation (run validation_commands)
- [ ] Add `ImprovementLineage` serialization/deserialization to `.context/capsules/<lineage_id>/lineage.json`
- [ ] Add `RefinementStatus` to board task labels for observability
- [ ] Custodian: add invariant for CI proposals — `fail_closed` must be in `strategy.constraints`
- [ ] Update ADR-0003 (tiered cognition) to reference this design
- [ ] Write unit tests for RefinementPolicy budget arithmetic
- [ ] Write integration test: full 3-attempt cycle with mocked evaluation
