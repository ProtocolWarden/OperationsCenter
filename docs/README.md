# OperationsCenter documentation

OC-specific architecture, design, operator runbooks, and historical material.

Cross-repo platform architecture — ownership, contracts, routing, backend
adapters, policy guardrails — is **not** here. It lives in
[PlatformDeployment/docs/architecture/](https://github.com/ProtocolWarden/PlatformDeployment/tree/main/docs/architecture).
Several files in `architecture/contracts/` and `architecture/routing/` are
deliberate redirect stubs pointing there; that is not rot.

## Find things

| | |
|---|---|
| **[`_toc.md`](_toc.md)** | Index of every documentation area, with entry points |
| **[`structure.md`](structure.md)** | Where a new document goes, and why |

## Start here

- [`operator/setup.md`](operator/setup.md) — first-time setup on a new host
- [`operator/runtime.md`](operator/runtime.md) — day-to-day operation
- [`demo.md`](demo.md) — end-to-end planning → routing → execution walkthrough
- [`operator/diagnostics.md`](operator/diagnostics.md) — inspecting a run
- [`operator/error_handling_recovery.md`](operator/error_handling_recovery.md) — when it breaks

## Execution model

OC dispatches work through two kinds of backend.

**Executor lanes** — orchestration services coordinating one or more worker
backends. Route here when multi-agent or critique topology is needed:

- `team_executor` — parallel team with configurable composition
- `dag_executor` — DAG workflow with per-node agent assignments
- `critique_executor` — proposer + critic refinement loop

**Direct worker backends** — single-agent runtimes OC invokes itself.
Conceptually equivalent to `claude_code`/`codex_cli` but local; these migrate
into executor services in a future work order:

- `aider_local` — aider against a local Ollama model ([`backends/aider_local.md`](backends/aider_local.md))
- `direct_local` — aider against a remote SwitchBoard model proxy

## A note on this file

It used to be a hand-maintained index of ~120 links. That duplicated
[`_toc.md`](_toc.md) and drifted — it is the kind of file that is wrong within
a month of anyone forgetting it exists. It is now an entry point only.

Add new documents to [`_toc.md`](_toc.md), and place them per
[`structure.md`](structure.md).
