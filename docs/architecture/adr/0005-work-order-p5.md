# Work Order — ADR 0005 Phase 5: Cross-Repo Wiring

_Status: In Progress · 2026-05-19_
_Continues: `0005-work-order.md` (Phases 0–4 complete)_

---

## Overview

Phases 0–4 landed all executor repos and their OC adapter scaffolding. This phase
wires the canonical execution path end-to-end:

- CritiqueExecutor critic was still calling Anthropic API directly — convert to subprocess
- OC backend factory only registered direct_local/aider_local/openclaw — wire the three new executors
- LaneDecision carries no backend metadata — add `metadata` field so SB can signal `worker_backend`
- DAGExecutor agent nodes have no codex_cli path — add `worker_backend` abstraction

---

## W1 — CritiqueExecutor: Strip Direct Anthropic API

- [x] **W1.1** — Rewrite `critic_runner.py`: replace `anthropic_client.messages.create`
  with subprocess Claude Code CLI. Accept `worker_backend` param for codex_cli support.
- [x] **W1.2** — Update `adversarial.py` + `reflexion.py`: remove `anthropic_client`
  param; pass `worker_backend` + `working_dir` through to `run_critic`.
- [x] **W1.3** — Update `executor.py`: remove `api_key` + `anthropic` import;
  add `worker_backend: str = "claude_code"` + `working_dir: str = "."`.
- [x] **W1.4** — Update `models.py` `CritiqueConfig`: add `worker_backend` field.
- [x] **W1.5** — Update `pyproject.toml`: remove `anthropic>=0.30` dependency.
- [x] **W1.6** — Update tests: remove anthropic mocks; assert subprocess calls.

**DoD:** `anthropic` package not imported anywhere in CritiqueExecutor. Tests green.

---

## W2 — OperationsCenter: Settings + Backend Adapters

- [x] **W2.1** — `settings.py`: remove `api_key` from `TeamExecutorSettings`;
  add `worker_backend: str = "claude_code"`. Remove `api_key` from `CritiqueExecutorSettings`;
  add `worker_backend: str = "claude_code"`. Add `working_dir: str = ""` to `CritiqueExecutorSettings`.
- [x] **W2.2** — Create `backends/team_executor/` adapter:
  `__init__.py` + `adapter.py` — wraps `TeamExecutorRunner.run()`; maps `RuntimeResult → ExecutionResult`.
- [x] **W2.3** — Create `backends/dag_executor/` adapter:
  `__init__.py` + `adapter.py` — calls `DAGExecutorRunner`; looks for `.dag_executor/workflow.yaml`
  in workspace; falls back to single-agent GraphSpec when absent.
- [x] **W2.4** — Create `backends/critique_executor/` adapter:
  `__init__.py` + `adapter.py` — wraps `CritiqueExecutorRunner.run()`; maps result.
- [x] **W2.5** — `factory.py`: register `TEAM_EXECUTOR`, `DAG_EXECUTOR`,
  `CRITIQUE_EXECUTOR` in `CanonicalBackendRegistry.from_settings()`.

**DoD:** `from_settings()` with all three registered; `for_backend(BackendName.TEAM_EXECUTOR)` resolves.

---

## W3 — SwitchBoard: LaneDecision Worker Backend Metadata

- [x] **W3.1** — `contracts/routing.py` `LaneDecision`: add
  `metadata: dict[str, str] = Field(default_factory=dict)`.
- [x] **W3.2** — `lane/engine.py` `select()`: populate
  `metadata["worker_backend"]` from `selected_lane`
  (`codex_cli` → `"codex_cli"`, else `"claude_code"`).
- [x] **W3.3** — Update SwitchBoard tests: assert `worker_backend` in `LaneDecision.metadata`.

**DoD:** `LaneSelector.select()` always emits `metadata["worker_backend"]`. Tests green.

---

## W4 — DAGExecutor: Worker Backend Abstraction

- [x] **W4.1** — `nodes/agent.py` `AgentNodeRunner.run()`: accept `worker_backend`
  param; route to `_run_codex()` when `"codex_cli"`.
- [x] **W4.2** — `executor.py` `DAGExecutorRunner`: add `worker_backend: str = "claude_code"`;
  pass through to `AgentNodeRunner`.
- [x] **W4.3** — Update DAGExecutor tests: cover both backends for agent node.

**DoD:** `AgentNodeRunner` dispatches to codex subprocess when `worker_backend="codex_cli"`.

---

## W5 — Commit, Push, Merge

- [ ] **W5.1** — CritiqueExecutor: feature branch → merge → push.
- [ ] **W5.2** — OperationsCenter: feature branch → merge → push.
- [ ] **W5.3** — SwitchBoard: feature branch → merge → push.
- [ ] **W5.4** — DAGExecutor: feature branch → merge → push.
- [ ] **W5.5** — ProtocolWarden: push README case fix.

---

## Recommended Order

```
W1  CritiqueExecutor   (no cross-repo deps)
W2  OC adapters        (needs TE/DE/CE importable but no code changes to them)
W3  SwitchBoard        (standalone)
W4  DAGExecutor        (standalone)
W5  Push all           (after W1-W4 complete and tested)
```
