# ADR 0005 — Owned Execution Topology Layer

_Status: Draft · 2026-05-18_

## Context

OperationsCenter dispatches work to external agent runtimes (kodo, Archon) via
adapter wrappers. Two structural problems forced this decision:

**D1 invariant violation — goal framing.**
`ExecutionRequest.goal_text` must reach the executing agent verbatim. Archon's
command template system wraps every `$ARGUMENTS` substitution in its own framing
(role description, instructions, a second repetition of the goal). The agent never
sees the raw goal. This is not fixable at the adapter layer without OC authoring
all of Archon's command files — at which point OC owns the loop content anyway.

**Blackbox governance.**
kodo and Archon are external projects not built under Custodian's policies. They
cannot produce structured RxP evidence natively, their invariants conflict with
OC's, and patching them (PATCH-001 for per-request model override) creates
maintenance forks that diverge indefinitely. The adapter layer required to bridge
the impedance mismatch is comparable in size to a clean owned implementation.

## Decision

Drop kodo and Archon. Build an owned execution topology layer: three new repos
that implement distinct agent coordination patterns under OC's contracts from the
start.

All three executors:
- Accept `goal_text` verbatim (D1)
- Produce `RuntimeResult` + `Evidence` natively (RxP)
- Are built and governed under Custodian policies
- Slot into ExecutorRuntime's existing dispatch path

## Topology Inventory

| Topology | Executor | Repo | Notes |
|----------|----------|------|-------|
| `single_agent` | direct dispatch | — (existing) | OC's `direct_local` backend; no new repo |
| `sequential` | DagExecutor | DagExecutor | linear DAG subtype (chain-shaped graph); no separate repo |
| `team` | TeamExecutor | TeamExecutor | replaces kodo |
| `dag` | DagExecutor | DagExecutor | replaces Archon; dynamic fan-out as loop-node subtype |
| `adversarial` | CritiqueExecutor | CritiqueExecutor | subtype: two agents, mutual critique until consensus |
| `reflexion` | CritiqueExecutor | CritiqueExecutor | subtype: single agent + independent self-critic loop |

## Repo Designs

### TeamExecutor

Replaces kodo. Coordinator LLM delegates to a named role pool via tool calls.
Independent verification before accepting a cycle's output.

**Core loop:**
1. Stage planner breaks `goal_text` into independently verifiable stages
2. For each stage: coordinator delegates to workers via tool calls
3. Verifier (architect role) reviews output → accept or reject with reason
4. On reject: coordinator re-delegates with rejection reason attached
5. On accept: summarizer compresses context, next stage begins
6. Final `RuntimeResult` aggregates all stage evidence

**Key concepts:**
- **Role** — named agent slot with backend, model, system_prompt, turn budget
- **Team config** — YAML declaring the role pool (coordinator + workers + verifiers)
- **Cycle** — one pass of work + verification per stage
- **Session** — per-role conversation that persists across cycles within a stage

**CxRP vocabulary:**
- `AgentTopology.team`
- `ShippingForm.managed_cli` (coordinator is an API call; workers are subprocesses)

---

### DagExecutor

Replaces Archon. Executes a directed acyclic graph of nodes with explicit
`depends_on` edges. Independent nodes in the same topological layer run
concurrently.

**Node types:**
- `agent` — runs a Claude Code subprocess with `goal_text` verbatim
- `bash` — shell script; stdout captured as `$nodeId.output`
- `script` — typed script (Python/TS); stdout captured
- `loop` — iterates an agent node until a completion signal or limit
  - **dynamic fan-out subtype**: loop over a runtime-determined list (data-driven
    parallelism where N is not known at graph-authoring time)
- `gate` — human approval checkpoint; pauses until operator response

**Variable substitution:** `$nodeId.output` wires node outputs into downstream
node inputs.

**Subtypes:**
- `sequential` / `pipeline` — linear DAG (every node depends on exactly the
  previous one; no branching, no parallelism). Same executor, different graph shape.
- `dynamic fan-out` — loop node that iterates over a runtime-determined list;
  N workers not known at graph-authoring time.

**Graph implementation:** `rustworkx.PyDiGraph` (Rust-backed directed graph).
Topological sort, cycle detection, and dependency traversal provided natively —
not hand-rolled. `rustworkx.topological_sort()` drives concurrent layer execution.

**CxRP vocabulary:**
- `AgentTopology.dag`
- `ShippingForm.managed_cli` (subprocess nodes) or a hosted-api variant if HTTP nodes are added later

---

### CritiqueExecutor

Two subtypes sharing a common critique-loop primitive.

**Adversarial subtype:**
Two agents (proposer + critic) exchange outputs until critic signals acceptance or
exchange limit is reached. Neither agent sees the other's system prompt. Produces
a `CritiqueTrace` — all rounds, final accepted output, rejection reasons per round.

**Reflexion subtype:**
Single agent produces output; independent critic (different model or same model,
fresh context) evaluates it against declared criteria. On rejection, the original
agent receives only the critique, not the critic's identity. Loop repeats until
acceptance or limit.

**Shared invariant:** the critic's evaluation is always a structured verdict
(`accept | reject + reason`), not freeform commentary. Reason becomes evidence.

**CxRP vocabulary:**
- `AgentTopology.adversarial` / `AgentTopology.reflexion`
- `ShippingForm.managed_cli`

## Shared Dependencies (Rust-backed)

All three executor repos use the same Rust-backed Python library stack.

| Library | What it is | Used for |
|---------|-----------|----------|
| **orjson** | JSON serialization in Rust | Contract serialization/deserialization (CxRP/RxP payloads) |
| **rustworkx** | Directed graph library in Rust | DagExecutor — topological sort, cycle detection, layer execution |
| **rapidfuzz** | Fuzzy/prefix string matching in Rust | Git-style unambiguous run ID prefix resolution |
| **tiktoken** | BPE tokenizer in Rust (OpenAI) | TeamExecutor — context size estimation before cycle summarization |
| **blake3** | BLAKE3 hashing in Rust | Deterministic record IDs (replaces sha256) |

All are PyO3-based — pure Rust under the hood, Python API on top. No C extensions.

## CxRP Impact

`AgentTopology` enum (not yet authored — redefine before writing):

```python
class AgentTopology(str, Enum):
    single_agent  = "single_agent"
    sequential    = "sequential"   # linear DAG subtype
    team          = "team"
    dag           = "dag"
    adversarial   = "adversarial"
    reflexion     = "reflexion"
```

Old ADR 0002 values (`sequential_multi_agent`, `dag_workflow`, `swarm_parallel`)
are superseded. `swarm_parallel` is not a recognized topology — drop it.

## Removal Scope

When the three executors reach dispatch parity with their predecessors:

- **OC**: `backends/archon/` package, `HttpArchonAdapter`,
  `ArchonHttpWorkflowDispatcher`, all HTTP client helpers, archon settings block,
  ~167 archon tests
- **OC**: kodo adapter (subprocess wrapper, settings block, kodo tests)
- **WorkStation**: `compose/profiles/archon.yml`
- **PATCH-001**: abandon upstream PR gate — no longer relevant
- **ADR 0002**: backend card axis expansion arc — replace with new executor cards
- **SourceRegistry**: remove kodo and archon entries; register TeamExecutor,
  DagExecutor, CritiqueExecutor

## Open Questions

- [ ] What language for the three executor repos? Python (matches OC/CxRP/RxP ecosystem) or something else?
- [ ] TeamExecutor coordinator: API-only (Gemini/Anthropic direct) or support Claude Code as coordinator too?
- [ ] DagExecutor gate nodes: where does the operator response come from? OC poll loop? Webhook?
- [ ] CritiqueExecutor exchange limit: hard cap or operator-configurable per dispatch?
- [ ] Phasing: which executor ships first?
