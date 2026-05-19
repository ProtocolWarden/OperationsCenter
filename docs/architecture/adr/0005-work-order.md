# Work Order — ADR 0005: Owned Execution Topology Layer

_Status: Draft · 2026-05-18_
_Design doc: `0005-owned-execution-topology-layer.md`_

---

## Phase 0 — CxRP Vocabulary (Prerequisite)

Must ship and be tagged before any executor repo starts. All three executors
import from CxRP.

- [ ] **P0.1** — `cxrp/vocabulary/agent_topology.py`: new `AgentTopology` enum
  (`single_agent`, `sequential`, `team`, `dag`, `adversarial`, `reflexion`).
  Mirror naming-guardrail tests from `CapabilitySet`. Tagged release.
- [ ] **P0.2** — `cxrp/vocabulary/executor.py`: remove `KODO`, `ARCHON`,
  `ARCHON_THEN_KODO` from `BackendName`; add `TEAM_EXECUTOR`, `DAG_EXECUTOR`,
  `CRITIQUE_EXECUTOR` to both `BackendName` and `ExecutorName`. Bump schema
  version. Tagged release.
- [ ] **P0.3** — Decide: add `managed_cli` / `hosted_api` to `ShippingForm` enum
  or keep as OC-internal card concepts. Document decision in ADR 0005.
- [ ] **P0.4** — OC: bump CxRP pin to the release shipping P0.1 + P0.2.

**DoD:** `AgentTopology` importable from CxRP; `BackendName` has no kodo/archon
entries; OC imports compile clean.

---

## Phase 1 — DagExecutor (New Repo)

Replaces Archon. Ships first — simpler coordination than Team.

### Repo bootstrap
- [ ] **P1.1** — Create `DagExecutor` repo with standard structure:
  `pyproject.toml`, `CLAUDE.md`, `.console/`, `.custodian/config.yaml`,
  `.hooks/`, `src/dag_executor/`, `tests/`.
- [ ] **P1.2** — Wire Custodian CI (`custodian-audit.yml` + pre-push hook).
- [ ] **P1.3** — Import CxRP + RxP as dependencies. Confirm `RuntimeInvocation`
  and `RuntimeResult` are sufficient (no RxP extension needed per audit).

### Core executor
- [ ] **P1.4** — YAML graph loader: parse nodes with `id`, `type`, `depends_on`,
  `command`/`bash`/`script`/`loop`/`gate`, `model`, `context`, `trigger_rule`.
  Validate DAG structure (cycle detection, unknown dep refs, unknown node types).
- [ ] **P1.5** — Topological sort + concurrent execution of independent nodes in
  the same layer.
- [ ] **P1.6** — Variable substitution engine: `$nodeId.output`, `$BASE_BRANCH`,
  `$ARTIFACTS_DIR`, `$WORKFLOW_ID`.
- [ ] **P1.7** — Node type implementations:
  - `agent` — Claude Code subprocess; `goal_text` verbatim (D1)
  - `bash` — shell script; stdout → `$nodeId.output`
  - `script` — typed script (Python); stdout → `$nodeId.output`
  - `loop` — iterates agent node until completion signal or limit;
    **dynamic fan-out subtype**: iterate over runtime-determined list
  - `sequential` subtype — linear chain graph (every node ≤ 1 dependency);
    skip topological sort complexity
  - `gate` — pause until operator response (manual `RuntimeResult`)
- [ ] **P1.8** — Per-node `RuntimeInvocation` emission + `RuntimeResult`
  collection. Aggregate into final `RuntimeResult` with merged `Evidence`.
- [ ] **P1.9** — Tests: 40+ covering loader validation, topo sort, concurrent
  execution, variable substitution, each node type, dynamic fan-out, gate pause,
  evidence aggregation.

### OC adapter
- [ ] **P1.10** — OC `backends/dag_executor/` adapter: accept `ExecutionRequest`,
  build invocation, run DagExecutor subprocess, parse `RuntimeResult`.
- [ ] **P1.11** — Register `BackendName.DAG_EXECUTOR` in OC factory.
- [ ] **P1.12** — Add `DagExecutorSettings` to OC `Settings`.
- [ ] **P1.13** — Author executor card: `executors/dag_executor/capability_card.yaml`
  (`agent_topology: dag`, `shipping_form: managed_cli`).

**DoD:** A real DAG graph dispatched through OC → DagExecutor produces a
`RuntimeResult` with `Evidence`. Custodian clean.

---

## Phase 2 — TeamExecutor (New Repo)

Replaces kodo.

### Repo bootstrap
- [ ] **P2.1** — Create `TeamExecutor` repo (same standard structure as DagExecutor).
- [ ] **P2.2** — Wire Custodian CI + hooks.
- [ ] **P2.3** — Import CxRP + RxP.

### Core executor
- [ ] **P2.4** — Team config loader: YAML declaring named roles
  (`coordinator`, `worker_smart`, `worker_fast`, `verifier`), each with
  `backend`, `model`, `system_prompt`, `max_turns`, `timeout_s`, `fallback_model`.
  Lookup order: project `.team_executor/team.yaml` → user `~/.team_executor/teams/{name}.yaml`.
- [ ] **P2.5** — Stage planner: coordinator LLM call breaks `goal_text` into
  independently verifiable `GoalStage` objects (description + acceptance_criteria).
  `goal_text` reaches coordinator verbatim (D1).
- [ ] **P2.6** — Coordinator loop (per stage):
  - Coordinator delegates via tool calls to named workers
  - Workers run as Claude Code subprocesses
  - On worker completion: coordinator signals done or assigns follow-up
- [ ] **P2.7** — Verification cycle: verifier (architect role) reviews stage
  output. Returns structured verdict (`accept | reject + reason`). On reject:
  coordinator receives reason, re-delegates. Repeat until accept or cycle limit.
- [ ] **P2.8** — Per-role session management: each role maintains its own
  conversation across cycles within a stage. Session resume on interruption.
- [ ] **P2.9** — Cycle summarizer: compresses stage context for next stage.
- [ ] **P2.10** — `RuntimeResult` assembly: aggregate all stage evidence
  (files_changed, commands_run, tests_run, rejection rounds) into Evidence.
- [ ] **P2.11** — Tests: 35+ covering team config loading, stage planning,
  coordinator tool calls, verification reject/accept, session resume, evidence.

### OC adapter
- [ ] **P2.12** — OC `backends/team_executor/` adapter.
- [ ] **P2.13** — Register `BackendName.TEAM_EXECUTOR` in OC factory.
- [ ] **P2.14** — Add `TeamExecutorSettings` to OC `Settings`.
- [ ] **P2.15** — Author executor card: `executors/team_executor/capability_card.yaml`
  (`agent_topology: team`, `shipping_form: managed_cli`).

**DoD:** A real goal dispatched through OC → TeamExecutor completes a
verify/accept cycle and produces `RuntimeResult` with full Evidence.
Custodian clean.

---

## Phase 3 — CritiqueExecutor (New Repo)

Adversarial and reflexion subtypes.

### Repo bootstrap
- [ ] **P3.1** — Create `CritiqueExecutor` repo (standard structure).
- [ ] **P3.2** — Wire Custodian CI + hooks.
- [ ] **P3.3** — Import CxRP + RxP.

### Shared critique primitive
- [ ] **P3.4** — Structured verdict model: `CritiqueVerdict(status: accept|reject,
  reason: str, round: int)`. Reason mandatory on reject.
- [ ] **P3.5** — `CritiqueTrace`: all rounds, per-round verdicts, final accepted
  output. Stored under `Evidence.extensions["critique_trace"]`.

### Adversarial subtype
- [ ] **P3.6** — Proposer + critic: two separate Claude Code subprocesses with
  isolated system prompts (neither sees the other's identity or system prompt).
- [ ] **P3.7** — Exchange loop: proposer output → critic verdict → on reject,
  proposer receives only the critique reason (not critic identity). Repeat until
  accept or exchange limit.
- [ ] **P3.8** — Exchange limit: operator-configurable per dispatch via
  `ExecutionRequest.input_payload["max_rounds"]`; hard cap at 10.

### Reflexion subtype
- [ ] **P3.9** — Single agent produces output; independent critic (fresh context,
  optionally different model) evaluates against declared criteria from
  `input_payload["criteria"]`.
- [ ] **P3.10** — On reject: original agent receives critique reason only.
  Loop repeats until accept or limit.

### OC adapter
- [ ] **P3.11** — OC `backends/critique_executor/` adapter with subtype routing
  (`adversarial` vs `reflexion` from `AgentTopology` on `ExecutionRequest`).
- [ ] **P3.12** — Register `BackendName.CRITIQUE_EXECUTOR` in OC factory.
- [ ] **P3.13** — Add `CritiqueExecutorSettings` to OC `Settings`.
- [ ] **P3.14** — Author executor cards for both subtypes.

**DoD:** Both subtypes dispatch through OC, run exchange loops, produce
`RuntimeResult` with `CritiqueTrace` in evidence. Custodian clean.

---

## Phase 4 — Cutover (Removal + Integration)

Run after all three executors reach dispatch parity.

### SourceRegistry
- [ ] **P4.1** — Remove kodo and archon entries from `registry/source_registry.yaml`.
- [ ] **P4.2** — Add TeamExecutor, DagExecutor, CritiqueExecutor entries.

### SwitchBoard
- [ ] **P4.3** — Remove `KODO`, `ARCHON`, `ARCHON_THEN_KODO` from
  `contracts/enums.py` `BackendName`.
- [ ] **P4.4** — Update `lane/defaults.py`: replace ~20 routing rules
  (kodo → team_executor, archon/archon_then_kodo → dag_executor).
- [ ] **P4.5** — Update `lane/escalation.py`: replace archon_then_kodo escalation.
- [ ] **P4.6** — Update SwitchBoard tests. Run full suite.

### OC removal
- [ ] **P4.7** — Delete `backends/archon/` (adapter, errors, http_client,
  http_workflow, invoke, mapper, models, normalize).
- [ ] **P4.8** — Delete `backends/kodo/` (adapter, errors, invoke, mapper,
  models, normalize, runner).
- [ ] **P4.9** — Delete `executors/archon/` and `executors/kodo/` card folders.
- [ ] **P4.10** — Delete `entrypoints/archon_probe/`.
- [ ] **P4.11** — Delete `patches/archon/` (PATCH-001 abandoned).
- [ ] **P4.12** — Remove `ArchonSettings`, `KodoSettings` from `settings.py`.
- [ ] **P4.13** — Remove kodo/archon entries from OC factory.
- [ ] **P4.14** — Delete ~167 archon tests + ~50 kodo tests + fixtures.
- [ ] **P4.15** — Run full OC suite; fix any remaining references. Suite must
  stay green.

### PlatformDeployment
- [ ] **P4.16** — Delete `compose/profiles/archon.yml`.
- [ ] **P4.17** — Add compose profiles for the three new executors (if
  containerized deployment is needed).

### Documentation
- [ ] **P4.18** — Mark ADR 0002 as superseded by ADR 0005 in its header.
- [ ] **P4.19** — Update `docs/operator/` guides: remove archon setup/runbooks,
  add executor operator guides.
- [ ] **P4.20** — Update `recommendations.md` files: remove kodo/archon prose
  now expressible as enum values.

### Integration validation
- [ ] **P4.21** — End-to-end: dispatch a real task through OC → each of the
  three executors. Verify `RuntimeResult` + Evidence in OC run-show.
- [ ] **P4.22** — SwitchBoard live verification: rebuild image, run
  `tests/integration/test_routing_live.py` with new backend names.
- [ ] **P4.23** — Custodian multi-repo clean pass across all affected repos.

**DoD:** Zero references to kodo or archon in any repo except git history.
All suites green. Custodian clean.

---

## Recommended Phasing

```
Phase 0  CxRP vocab                           ~5h   prerequisite
Phase 1  DagExecutor                          ~36h  replaces Archon
Phase 2  TeamExecutor                         ~30h  replaces kodo
Phase 3  CritiqueExecutor                     ~23h  new capability
Phase 4  Cutover                              ~50h  removal + integration
```

Phases 1–3 are independent of each other after Phase 0 completes and can run
in parallel if capacity allows.

## Open Questions (block Phase 0)

- [x] Language for executor repos — **Python** (matches ecosystem)
- [x] TeamExecutor coordinator — **API-only** (Anthropic/Gemini direct). CLI agents
  try to solve problems themselves rather than delegate; raw API model stays in its
  lane. Cleaner contract: API call in, tool-call decisions out, no subprocess
  lifecycle for the coordinator.
- [x] DagExecutor gate nodes — **OC poll loop**. Gate emits `awaiting_gate` status
  in `RuntimeResult`; OC surfaces via `run-show`; operator approves/rejects via
  `operations-center-run-approve <run_id>` CLI. Executor blocks on a file/pipe OC
  writes to on approval. No webhook infrastructure needed.
- [x] ShippingForm — **keep OC-internal**. CxRP's `ShippingForm` describes
  contribution delivery (`pr/patch/branch/artifact/commit`) — how output ships.
  `managed_cli`/`hosted_api` are invocation transport descriptors — how the executor
  is called. Different concept; belongs on OC executor capability cards, not CxRP.
