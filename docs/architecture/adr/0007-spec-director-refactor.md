# ADR 0007 — Fold spec_director into the backend executor pipeline

_Status: Accepted · 2026-05-22_
_Audit: in this document_
_Work tracked: branch `feat/spec-director-refactor`, phases A–F below_

---

## Context

`spec_director` is an OC watcher that, until this change, contained three Claude-bypassing code paths:

1. **Half 1 — board hygiene** (archive expired specs, bootstrap orphan campaigns, auto-promote backlog→R4AI, recovery of stale campaigns). Pure Plane state operations, no LLM.
2. **Half 2 — campaign generation** (trigger on drop-file or queue-drain → bundle context → call Claude via `_claude_cli.py` subprocess → write spec to `docs/specs/` → create Plane campaign tasks). LLM call bypasses SwitchBoard and the entire backend executor stack.
3. **Phase orchestration** (separate file `phase_orchestrator.py`) ALSO calls Claude directly via `_claude_cli.py` to rewrite specs/tasks between phases.

All three direct-Claude paths skipped: model binding policy, rate limiting, retry logic, audit trail via `RunArtifactWriter`, lifecycle capture via `cl_dispatch_wrap`, structured failure categorization. There is no `run_id` linkage from a generated spec to any execution record — the spec just appears on disk with no provenance.

## Decision

Fold all LLM-needing operations through the normal Plane task → `board_worker` → `ExecutionCoordinator` → backend pipeline. Split the non-LLM operations (hygiene, trigger detection) into focused watchers that emit Plane tasks instead of executing work themselves.

**Four concerns, separately owned:**

| Concern | Owner | LLM? |
|---|---|---|
| Hygiene (archive, bootstrap, auto-promote, recovery) | `spec_hygiene` watcher | No |
| Trigger detection (drop-file, queue-drain) | `spec_trigger` watcher | No |
| Spec generation execution | `board_worker` via new `spec-author` task-kind | Yes (via backend) |
| Phase orchestration LLM step | Same `spec-author` handler with `task_phase` label | Yes (via backend) |

**Single-writer invariants preserved:**
- Plane is the source of truth for all task state.
- `spec_hygiene` is the only writer of `state/campaigns/active.json` (which OperatorConsole reads).
- The backend executor is the only writer of `~/.console/operations_center/runs/<run_id>/`.

## Architecture

```
                    ┌────────────────────────────────────────────┐
                    │  Plane (source of truth for all task state)│
                    └──────┬──────────────────────────────┬──────┘
                           │                              │
              writes (tasks, transitions, labels)         │ reads
                           │                              │
        ┌──────────────────┴───────────┐                  │
        │                              │                  │
┌───────────────┐              ┌──────────────────┐       │
│ spec_trigger  │              │  spec_hygiene    │───────┤
│   (watcher)   │              │    (watcher)     │       │
│               │              │                  │       │
│ - drop-file   │              │ - archive expired│       │
│ - queue-drain │              │ - bootstrap orph │       │
│               │              │ - auto-promote   │       │
│ on fire:      │              │ - recovery       │       │
│ creates Plane │              │ - phase-advance  │       │
│ task (kind:   │              │   detection      │       │
│ spec-author)  │              │ - rebuilds JSON  │       │
└───────────────┘              │   projection     │       │
                               │                  │       │
                               │ on phase-advance │       │
                               │ detected: creates│       │
                               │ Plane task (kind:│       │
                               │ spec-author,     │       │
                               │ task_phase: ...) │       │
                               └──────────────────┘       │
                                                          │
                               ┌──────────────────┐       │
                               │  OperatorConsole │───────┘
                               │  watcher_status  │  (reads
                               │  reads JSON      │   active.json
                               │  projection      │   projection)
                               └──────────────────┘

                                  ┌────────────────────────────┐
                                  │  board_worker              │
                                  │  picks up spec-author task │
                                  │                            │
                                  │  planning → execute        │
                                  │  → ExecutionCoordinator    │
                                  │  → cl_dispatch_wrap        │
                                  │  → backend writes file in  │
                                  │    OC repo, commits, pushes│
                                  │  → ExecutionResult         │
                                  │  → RunArtifactWriter       │
                                  │  → _handle_success() opens │
                                  │    spec, creates campaign  │
                                  │    sub-tasks               │
                                  └────────────────────────────┘
```

### Key invariants

- **All LLM calls go through the backend executor.** `_claude_cli.py` is deleted; CI grep guard prevents reintroduction.
- **Plane is single-source-of-truth for task state.** `state/campaigns/active.json` is a Plane-derived projection rebuilt by `spec_hygiene` at the top of every cycle; OperatorConsole reads it.
- **Audit trail is end-to-end closed:**
  - Spec file embeds `<!-- generated_by_run: <run_id> -->` so any spec can be traced back to its execution.
  - Campaign sub-tasks created by `_handle_success` carry `parent_run: <run_id>` label.
  - `runs/<run_id>/run_metadata.json` includes `spec_slug` when the task-kind is `spec-author`.

## `spec-author` task payload

What `spec_trigger` or `spec_hygiene` writes when creating a Plane task. Specific so the planning subprocess has nothing to invent:

```yaml
# Plane task body (markdown)
task-kind: spec-author
source: spec-director
spec_slug: <derived from seed or queue-drain timestamp>
trigger_source: drop_file | queue_drain | phase_advance
task_phase: <unset> | implement | verify | next-phase-id   # set only for phase-advance
target_path: docs/specs/<slug>.md
seed_text: |
  <operator drop-file content, OR empty for queue-drain, OR phase-state digest>
context_bundle:
  recent_git_log_repos: [...]
  existing_specs: [...]
  board_snapshot: { ready: N, running: M, ... }
```

Planning subprocess composes the prompt from this payload. Execute subprocess runs Claude in the OC repo workspace via the existing backend; backend writes the file, commits, pushes. `_handle_success()` reads `target_path` from the committed branch, parses the new spec, creates campaign sub-tasks (CampaignBuilder logic moved into the success handler).

## Work phases (executed on `feat/spec-director-refactor`)

| Phase | Scope | Depends on |
|---|---|---|
| **A** | Extract `spec_hygiene` watcher. Move Half 1 + active.json projection rebuild. | — |
| **B** | Extract `spec_trigger` watcher. Drop-file + queue-drain detection. Plane task creation with dedupe. | — |
| **C** | Add `spec-author` task-kind to `board_worker._ROLE_KINDS`. Planning + execute + `_handle_success` for spec authoring. | A, B |
| **D** | Refactor `phase_orchestrator.py` to detection-only; emit `spec-author` task with `task_phase` label. | C |
| **E** | Delete `_claude_cli.py` + every importer. CI grep guard added. | C, D |
| **F** | Retire `spec_director` entrypoint. Update docs, OperatorConsole status pane label. | A, B, E |

## Considered alternatives

- **Fold Half 1 into the watchdog's `run_once()`.** Rejected: watchdog has no registration mechanism; adding hardcoded steps conflates spec concerns with general health. Separate `spec_hygiene` entrypoint is clearer and easier to disable.
- **New `spec-generate` and `phase-advance` as separate task-kinds.** Rejected: they're structurally the same operation (write structured doc to OC repo). One `spec-author` kind with a `task_phase` label is minimum new surface.
- **Eliminate `state/campaigns/active.json` entirely.** Rejected: OperatorConsole `watcher_status_pane.py:403` reads it directly. Keeping the file as a Plane-derived projection (single writer = `spec_hygiene`) is safer.

## Out of scope

- Generic watcher-registration mechanism for the watchdog (would be useful but is its own work).
- Migrating other watchers (e.g., custodian, board_unblock) into the same task-kind pattern.
- Backend support for "open existing file, modify, commit" — relied on but not changed here; if the existing path doesn't already support it, that's a separate fix.

## Follow-up primitive worth integrating later

For Phase D (phase orchestration / spec rewrite), the naive approach is full-regen: hand the LLM the existing spec + phase state, ask for the updated version, accept whatever comes back. This loses intentional decisions, drifts on structure, and is wasteful for small advances.

A better primitive is **prompt-diff** — the LLM emits a structured patch ("update section X, leave everything else alone") that's applied to the existing spec rather than replacing it. `temm1e-labs/promptlabs` (https://github.com/temm1e-labs/promptlabs) is one implementation; we plan to **clone its repo and copy the primitives** into OC (or a new internal package) rather than add it as a third-party dependency. The package has uncertain maintenance trajectory and a small surface area worth owning.

This ADR's Phase D handler should be built so the swap is straightforward:
- The handler reads the existing spec content from the workspace (it has to anyway, for context).
- The backend prompt is what differs between "full regen" and "diff-apply" — handler logic stays the same.
- ExecutionResult shape is identical (file modified, committed, pushed).

When the copied-in prompt-diff primitive lands, only the prompt-construction step changes. The audit/lifecycle/Plane integration built in this ADR carries forward.

## Risks

1. Phase orchestration's prompt assumes the backend can modify existing files in the OC workspace, not just write new ones. Validated in Phase D against the existing backend execution model.
2. `spec_trigger` dedup must handle multiple trigger types firing in the same cycle; deterministic priority (drop-file wins) emits at most one task.
