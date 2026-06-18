---
status: in-progress
---

# Ecosystem Incomplete-Integration Remediation

**Status:** in-progress (autonomous /loop) — plan of record
**Origin:** the PR #313 post-mortem widened into a question: how much "built +
tested but never wired" debt exists across the platform, and is the #313 pattern
(a PR claiming completion while shipping inert code) systemic? This doc records
the cross-repo audit, the adversarial dispositions, and the phased remediation.
Companion to [Self-Heal Ladder](./SELF_HEAL_LADDER.md).

## Headline finding (adversarial)

The #313 *claimed-complete-but-inert* pattern is **NOT systemic**. A full audit of
all 11 src-bearing repos (excluding VideoFoundry + PrivateManifest per the
private-repo deferral) found that what looks "unwired" is overwhelmingly:
- **honestly-declared cross-repo library API** awaiting an external consumer
  (SourceRegistry, RepoGraph, CoreRunner runners, OperatorConsole CxRP boundary,
  CritiqueExecutor/TeamExecutor RxP runtimes) — **not debt**;
- **framework-dispatched** CLI/route handlers grep can't see — **not debt**;
- **superseded convenience wrappers** — small, benign DELETEs.

The genuine #313 pattern — a numbered PR explicitly claiming "Complete /
Verified / integrated end-to-end" while the code was never wired — was found in
**exactly one place: OperationsCenter's observer plane** (PRs #247, #279, #250).
Everywhere else, the unwired symbols trace to pre-PR-process **direct pushes**
(no false claim) or are deliberate API surface.

Per-repo genuinely-unwired counts (of public symbols scanned): ContextLifecycle
3/189, CoreRunner 6/21, CritiqueExecutor 0/23, Custodian ~5/250, DAGExecutor
2/30, OperatorConsole 3/96, PlatformManifest 3/40, RepoGraph 1/136,
SourceRegistry 4/97, SwitchBoard 4/125, TeamExecutor 3/34. OperationsCenter
carries the bulk (~111 baselined), concentrated in the observer plane.

## Enforcement backbone (do first — a hollow gate makes everything else theater)

- **B1 — Custodian `--only` silent-skip** *(DONE, Custodian #46)*: a gate naming
  a detector the install lacks filtered to an empty set and passed green. Now
  refuses unknown ids. The gate is self-verifying.
- **B2 — fleet `.venv` is pinned behind** (`0fa072f`, no D12/DC10). Local/fleet
  pre-push gates are no-ops; CI carries the real check via `custodian@main`.
  Action: reinstall custodian at the pyproject pin in each repo's venv. Operational,
  not a code PR; sequence carefully so running fleet watchers aren't disrupted.
- **B3 — CI pins `@main`, not the pyproject pin** for the audit job. With B1 in
  place a vanished detector now errors, but the audit workflow should pin a
  known-good Custodian SHA rather than floating `@main`.

## Phases

Dispositions are adversarially adjudicated here — no item is deferred to a human.
WIRE = finish the integration; DELETE = remove symbol + its tests (provably dead,
no external contract); KEEP = legitimate API/dispatch surface, do not touch.

### Phase 1 — enforcement backbone *(DONE)*
- Custodian #46 (B1). B2/B3 tracked above.

### Phase 2 — WIRE genuinely-broken integrations
| repo | item | action |
|------|------|--------|
| TeamExecutor | `TeamExecutorRunner` never registered as an RxP runtime → the whole executor is undispatchable | WIRE: add the RxP runtime/entry-point registration |
| PlatformManifest | `parse_visibility_scope` — `visibility_scope` field shipped in `data/platform_manifest.yaml`, parsed nowhere | WIRE into `load_repo_graph`; surface on the graph |
| SwitchBoard | `p95_latency_ms` computed but the demote heuristic reads only `mean_latency_ms` (outlier-sensitive) | WIRE p95 into `AdjustmentEngine._evaluate` |
| DAGExecutor | `NodeRunner` Protocol defined but the runner dispatch isn't typed to it (inert) | WIRE: type the runner registry as `dict[NodeType, NodeRunner]` |

### Phase 3 — DELETE provably-dead superseded code
| repo | items | rationale |
|------|------|-----------|
| Custodian | `core/runner.py`, `policy/filter.py`, `policy/architecture.py` (+ tests) | orphaned Phase-1/5 scaffold, superseded by `cli/runner.py` + `detectors/structure.py`; imported by zero src |
| ContextLifecycle | `select_docs`, `SessionPaths.archived_target` | superseded by `select_docs_split` / `archived_root()` |
| PlatformManifest | `load_default_capabilities`, `parse_also_hosts` | convenience mirror of a used fn; `also_hosts` vestigial for public PM |
| SourceRegistry | `remote_url`, `list_files_changed_between`, `PatchRegistry.all_patches` | over-ported primitives + redundant accessor, 0 callers, 0 tests |
| SwitchBoard | `LaneSelector.plan_routes` | redundant wrapper; prod constructs `DecisionPlanner` directly |
| TeamExecutor | `TeamSession`, `TeamConfig.verifier` | scaffold state never adopted; back-compat shim with no consumer |
| DAGExecutor | `DagGraph.get_node` | test-only accessor; executor walks by `layers()` |
| CoreRunner | `CoreRunnerError` | base exception raised/caught nowhere |

### Phase 4 — OperationsCenter observer plane (the real #313 cluster)
The genuine debt. Per-subsystem adjudication required (wire-vs-delete), because
some may duplicate live collectors:
- `CoverageTrendManager` (#279) — trend/volatility/regression
- `CoverageAlertManager` (#279) — alert generation/routing
- `FlakyTestReporter` (#247) — flaky query/markdown/table
- `MergeDecisionInstrumenter` / `export_metrics_json` (#250) — reviewer metrics
Approach: for each, determine whether a live entrypoint should drive it (WIRE) or
it duplicates existing wiring / is abandoned (DELETE). Then prune the
corresponding names from `audit.d12_baseline` so the gate stops grandfathering
them. This is the largest phase; sequence after the backbone + small wins.

### Phase 5 — ratchet cleanup
As Phases 3-4 land, prune the burned-down names from each repo's
`audit.d12_baseline`, so D12 (once the venvs are current) catches NEW regressions
against a smaller accepted set.

## KEEP list (audited — NOT debt, do not "complete")
SourceRegistry `__all__` engine surface; RepoGraph topology/diff/capabilities
query methods + `RepoGraphDiff`; CoreRunner injectable runners
(`ManualRunner`/`Dispatcher`/HTTP runners/`read_invocation`); OperatorConsole
`cxrp_capture` boundary; CritiqueExecutor (clean); Custodian `Finding` helper
methods; SwitchBoard dormant `metrics.py` (deliberate instrument-now/expose-later).
