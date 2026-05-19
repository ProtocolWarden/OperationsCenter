---
adr: "0006"
title: "CoreRunner — Rename ExecutorRuntime and consolidate subprocess safety"
status: "accepted"
date: "2026-05-19"
---

# ADR 0006 — CoreRunner: rename ExecutorRuntime and consolidate subprocess safety

## Status

Accepted — work order below.

## Context

`ExecutorRuntime` was built as a generic RxP-shaped runtime dispatcher
(subprocess, manual, HTTP, async HTTP runners). Its real engineering value is
the process-group-safe subprocess implementation in `SubprocessRunner`:

- `start_new_session=True` — child becomes its own process-group leader
- `os.killpg(SIGKILL)` on timeout — kills the entire descendant tree, not just
  the direct child
- Transient `SIGTERM` handler — if the Python supervisor is killed, the child
  group is reaped before exit

Currently only `OperationsCenter`'s `direct_local` and `aider_local` adapters
use `ExecutorRuntime`. The three AI execution backends — `TeamExecutor`,
`DAGExecutor`, `CritiqueExecutor` — call `subprocess.run()` directly with no
process-group safety:

| Backend | File | Gap |
|---------|------|-----|
| TeamExecutor | `agent_call.py` | raw `subprocess.run()`, no `start_new_session` |
| DAGExecutor | `nodes/base.py` | raw `subprocess.run(shell=True)`, no `start_new_session` |
| CritiqueExecutor | `critic_runner.py` | raw `subprocess.run()`, no `start_new_session` |

When a worker or critic call hangs or the executor process is killed, all
descendant processes (sub-agents spawned by claude CLI, nested tool calls)
become orphans and continue consuming CPU and API quota.

## Decision

1. **Rename** `ExecutorRuntime` → `CoreRunner` (package `executor_runtime` →
   core_runner, class `ExecutorRuntime` → `CoreRunner`).

2. **Extract** a standalone safe_run() function from `SubprocessRunner` into
   core_runner.process — a public, lightweight primitive with no RxP
   dependency. Signature:

   ```python
   def safe_run(
       cmd: list[str],
       *,
       cwd: str = ".",
       env: dict[str, str] | None = None,
       timeout_seconds: int | None = None,
       capture_output: bool = True,
   ) -> SafeRunResult:
       ...
   ```

   `SafeRunResult` is a plain dataclass: `returncode`, `stdout`, `stderr`,
   `timed_out`. No RxP contracts, no artifact descriptors.

3. **Wire** TE/DE/CE to replace their raw `subprocess.run()` calls with
   core_runner.process.safe_run(). The full RxP invocation path
   (`CoreRunner.run(invocation)`) remains unchanged for `direct_local` /
   `aider_local`.

4. **Update** all cross-repo references (imports, pyproject.toml deps, GitHub
   URL, PlatformManifest graph, OperatorConsole profiles, docs).

## Consequences

- All subprocess calls in the ecosystem share one process-group-safe
  implementation. Orphan processes on timeout or supervisor death are
  eliminated ecosystem-wide.
- TE/DE/CE take a new dependency on `CoreRunner` but only on a small,
  stable primitive — no RxP contract coupling.
- `direct_local` / `aider_local` behaviour is unchanged.
- `ExecutorRuntime` class and package name disappear; callers need import
  updates only.

---

## Work Order

### Phase 1 — Extract safe_run() in CoreRunner repo

**Files to create/change in `ExecutorRuntime` (soon `CoreRunner`):**

1. `src/core_runner/process.py` *(new)*
   - Extract `_run_with_process_group()` logic from `SubprocessRunner` into
     safe_run(cmd, *, cwd, env, timeout_seconds, capture_output) → SafeRunResult`
   - `SafeRunResult` dataclass: `returncode: int | None`, `stdout: str`,
     `stderr: str`, `timed_out: bool`
   - Keep stdout/stderr in-memory (not captured to files) — file capture is an
     RxP-layer concern, not a primitive concern

2. `src/core_runner/__init__.py`
   - Export `CoreRunner` (renamed from `ExecutorRuntime`) and safe_run`

3. `src/core_runner/runtime.py`
   - Rename class `ExecutorRuntime` → `CoreRunner`
   - Internal imports updated to core_runner.*

4. `src/core_runner/runners/subprocess_runner.py`
   - Delegate `_run_with_process_group` to core_runner.process.safe_run()
     internally (removes duplication)

5. All other files under `src/executor_runtime/` → `src/core_runner/`
   - Update all `from executor_runtime` imports → `from core_runner`

6. `pyproject.toml`
   - `name = "core-runner"`
   - `[project.urls] Repository` → updated GitHub URL

7. `README.md` — rewrite for CoreRunner + expanded scope

8. `.custodian/config.yaml`
   - `repo_key: CoreRunner`
   - `src_root: src/core_runner`

9. All test files under `tests/`
   - `from executor_runtime` → `from core_runner`
   - `ExecutorRuntime()` → `CoreRunner()`

---

### Phase 2 — Wire TE/DE/CE to core_runner.safe_run()

**TeamExecutor — `src/team_executor/agent_call.py`:**
- Replace `subprocess.run(cmd, ...)` in `_claude_call()` and `_codex_call()`
  with safe_run(cmd, cwd=working_dir, timeout_seconds=role.timeout_seconds)`
- `pyproject.toml` — add `core-runner` dependency

**DAGExecutor — `src/dag_executor/nodes/base.py`:**
- Replace `run_subprocess()` implementation with safe_run()
- Remove `shell=True` (use list form — already done in agent node)
- `pyproject.toml` — add `core-runner` dependency

**CritiqueExecutor — `src/critique_executor/critic_runner.py`:**
- Replace `subprocess.run(cmd, ...)` in `_claude_critic()` and `_codex_critic()`
  with safe_run(cmd, cwd=working_dir, timeout_seconds=timeout_seconds)`
- `pyproject.toml` — add `core-runner` dependency

---

### Phase 3 — Update OperationsCenter

**Source:**
- `backends/direct_local/adapter.py` — `from executor_runtime import ExecutorRuntime` → `from core_runner import CoreRunner`
- `backends/aider_local/adapter.py` — same
- `backends/openclaw/invoke.py` — same
- `backends/_runtime_ref.py` — docstring only, update text

**Dependencies:**
- `pyproject.toml` — `executor-runtime @ git+...ExecutorRuntime` → `core-runner @ git+...CoreRunner`

**Docs:**
- `docs/architecture/adr/0005-owned-execution-topology-layer.md:34` — update reference
- `docs/architecture/contracts/platform_manifest_consumption.md` — update references
- `LOOP_START.md`, `docs/operator/watchdog_loop.md` — update references

**.custodian/config.yaml:**
- Update any suppression paths referencing `executor_runtime`

---

### Phase 4 — Update PlatformManifest

**`src/platform_manifest/data/platform_manifest.yaml`:**
- Node key `executor_runtime` → core_runner
- `canonical_name: ExecutorRuntime` → `CoreRunner`
- All edge references: `executor_runtime` → core_runner

**Tests:**
- `tests/test_repo_graph.py`, `test_validate.py`, `test_ontology_relationships.py`,
  `test_architecture_docs.py` — update all `ExecutorRuntime` / `executor_runtime`
  fixture strings

---

### Phase 5 — Update remaining repos

**RxP** — `README.md`, `.github/` templates, `CONTRIBUTING.md`, `SECURITY.md`,
`rxp/vocabulary/runtime_kind.py` — update all `ExecutorRuntime` → `CoreRunner`

**CxRP** — `operations_center.md` integration doc — update table entry

**OperatorConsole:**
- `config/profiles/executorruntime.yaml` → rename to `corerunner.yaml`, update `name: CoreRunner`
- `config/profiles/platform.yaml` — `executorruntime` → `corerunner`
- `src/operator_console/git_watcher.py` — `"ExecutorRuntime"` → `"CoreRunner"` in frozenset

---

### Phase 6 — GitHub repo rename

```bash
gh api --method PATCH repos/ProtocolWarden/ExecutorRuntime \
  --field name=CoreRunner
```

Update all `git+https://github.com/ProtocolWarden/ExecutorRuntime.git` URLs
across all `pyproject.toml` files after the rename.

---

### Test checkpoints

After each phase, all existing test suites must pass:

| Repo | Command |
|------|---------|
| CoreRunner | `pytest tests/ -q` |
| TeamExecutor | `pytest tests/ -q` |
| DAGExecutor | `pytest tests/ -q` |
| CritiqueExecutor | `pytest tests/ -q` |
| OperationsCenter | `pytest tests/ -q` |
| PlatformManifest | `pytest tests/ -q` |

New tests to add in CoreRunner Phase 1:
- `tests/test_safe_run.py` — process-group kill on timeout, SIGTERM propagation,
  zero-exit success, nonzero-exit failure, stdout/stderr capture
