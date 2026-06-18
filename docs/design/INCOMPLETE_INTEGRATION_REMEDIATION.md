---
status: implemented
---

# Ecosystem Incomplete-Integration Remediation

**Status:** COMPLETE — plan of record + outcomes.
**Origin:** the PR #313 post-mortem widened into a question: how much "built +
tested but never wired" debt exists across the platform, and is the #313 pattern
(a PR claiming completion while shipping inert code) systemic? This doc records
the cross-repo audit, the adversarial dispositions, and what shipped.
Companion to [Self-Heal Ladder](./SELF_HEAL_LADDER.md).

## Headline finding (adversarial)

The #313 *claimed-complete-but-inert* pattern is **NOT systemic**. A full audit of
all 11 src-bearing repos (excluding VideoFoundry + PrivateManifest per the
private-repo deferral) found that what looks "unwired" is overwhelmingly:
- **honestly-declared cross-repo library API** awaiting an external consumer
  (SourceRegistry, RepoGraph, CoreRunner runners, OperatorConsole CxRP boundary,
  CritiqueExecutor RxP runtime) — **not debt**;
- **framework-dispatched / indirect-dispatch** handlers grep can't see — **not debt**;
- **superseded convenience wrappers** — small, benign deletes.

The genuine #313 pattern — a numbered PR explicitly claiming "Complete /
Verified / integrated end-to-end" while the code was never wired — was confined
to **OperationsCenter's observer plane** (PRs #247, #279, #250). Everywhere
else, the unwired symbols traced to pre-PR-process direct pushes (no false
claim) or were deliberate API surface.

## Guiding principle (operator correction, mid-campaign)

**Complete genuine features — don't delete them.** Deletion is the *last resort*,
used only for provably-superseded duplicates where the feature already exists in
the live path (and called out explicitly when used). The observer-plane features
were therefore **wired in to work as featured**, not removed.

## Three adversarial corrections (the audit's blind spots)

The per-repo audit ran each repo in isolation and over-flagged. Three findings
the corrections caught before acting:
1. **Cross-repo consumers.** `TeamExecutorRunner` was flagged "undispatchable"
   but is **already wired** by OC's `backends/team_executor/adapter.py`. Dropped.
2. **Indirect dispatch.** `MergeDecisionInstrumenter` was flagged "never
   instantiated" but is **already wired** via the module-level
   `record_decision_outcome` (→ `get_instrumenter()`) the reviewer calls. The
   real gap was only the unused `export_metrics_json` surface.
3. **Convention hooks flip dispositions.** PlatformManifest `parse_visibility_scope`
   was flagged DELETE, but a PlatformManifest loader convention (surfaced as a
   PreToolUse hook) mandates the loader fail-closed on visibility — so it was a
   **WIRE**, not a delete.

## Outcomes (14 green-gated PRs)

### Enforcement backbone
- **Custodian #46** — `--only` silent-skip closed: a gate naming an absent
  detector now fails loudly instead of green-passing. The gate is self-verifying.

### WIRE — genuine integrations completed
- **DAGExecutor #10** — `NodeRunner` Protocol now enforced on the runner dispatch.
- **SwitchBoard #21** — demote heuristic gates on **p95** tail latency (was mean),
  catching tail degradation the mean hid. (Also deleted `plan_routes` + `p50`.)
- **PlatformManifest #83** — `load_repo_graph` now **fail-closes on
  `visibility_scope`** (the documented security boundary), via the previously
  unwired `parse_visibility_scope`. (Also deleted `load_default_capabilities`.)
- **OperationsCenter #325** — `FlakyTestReporter` wired into the live pytest
  plugin: each session persists results in its format + writes a markdown report.
- **OperationsCenter #326** — `CoverageTrendManager` + `CoverageAlertManager`
  wired into `RepoObserverService`: bridge `CoverageSignal → CoverageSnapshot`,
  then trend + regression + alerts compute and persist on every observation.
- **OperationsCenter #327** — merge-decision metrics surfaced: the reviewer now
  exports `MergeDecisionInstrumenter` metrics each cycle (the instrumenter was
  already collecting; only `export_metrics_json` had no reader).

### DELETE — provably-superseded duplicates (nothing to complete)
- **Custodian #47** — orphan Phase-1/5 scaffold (`core/runner.py`,
  `policy/{filter,architecture}.py`), superseded by the live `cli/runner.py` path.
- **DAGExecutor #11** — `DagGraph.get_node` (test-only accessor).
- **SourceRegistry #14** — over-ported git-ops primitives + a redundant patch
  accessor (0 callers, 0 tests).
- **TeamExecutor #12** — scaffold-era `TeamSession` + `TeamConfig.verifier` shim.
- **CoreRunner #20** — `CoreRunnerError` (raised/caught nowhere, not exported).

### KEEP — audited, not debt
- **ContextLifecycle** (`select_docs`, `SessionPaths.archived_target`): clean,
  *documented* convenience accessors in a high-risk path-dispatched engine;
  `select_docs` would need a 9-test rewire and `archived_target` cascades — low
  value, real risk. Left intentionally.
- The honestly-deferred cross-repo API surfaces (SourceRegistry `__all__`,
  RepoGraph query/diff methods, CoreRunner injectable runners, OperatorConsole
  `cxrp_capture`), CritiqueExecutor (clean), Custodian `Finding` helpers,
  SwitchBoard dormant `metrics.py`.

### Ratchet
Each observer wire pruned its now-integrated names from OC
`audit.d12_baseline` (`format_flaky_tests_markdown`, `save_test_results`,
`detect_regression`, `generate_alerts`, `save_snapshot`, `save_alert`,
`export_metrics_json`); the **D12 gate confirms 0** after each — proof the wires
are real, not baseline-hidden. The still-unwired public methods
(`calculate_trend_slope`, `calculate_volatility_score`, `get_historical_data`,
`categorize_alert`, `get_routes_for_alert`) remain baselined.

## Backbone notes (infra / follow-ups, not code-fixed here)

- **B2 boundary-artifact (root cause).** OC's CI `audit` job is red on every PR
  with a single MED **B2** finding *even though* `REPOGRAPH_BOUNDARY_ARTIFACT_FILE`
  is materialized in CI. Diagnosis (read-only): `detect_b2` emits its generic
  "not provided" message only when the artifact loads with **no error but yields
  zero boundary names** — so the secret-decoded artifact is present and parseable
  but **content-less** (no boundary names). This is an **infra/secret issue**
  (`REPOGRAPH_BOUNDARY_ARTIFACT_B64` needs a real disclosure artifact), not a
  Custodian code bug — hence advisory-only and left for the operator. One genuine
  minor Custodian follow-up: B2's message should distinguish *provided-but-no-names*
  from *not-provided* (the loader already has the provenance).
- **Audit gate is advisory.** OC `main` is unprotected and the reviewer
  LGTM-merges over the advisory (B2-red) `audit` check. Making it required is
  blocked on B2 above.
- **Fleet `.venv` pinned behind** (`0fa072f`, no D12/DC10) — local/fleet pre-push
  gates are no-ops; CI carries the real check via `custodian@main`. Reinstall at
  the pin per repo when convenient (sequence so running watchers aren't disrupted).
