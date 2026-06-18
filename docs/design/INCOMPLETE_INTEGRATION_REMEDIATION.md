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
all 11 src-bearing repos (excluding the two private repos per the
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

## Outcomes (12 green-gated PRs)

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

## Closure (2026-06-18) — the three backbone follow-ups, now resolved

The three infra items this doc once carried as open follow-ups are closed.

- **B2 boundary-artifact — FIXED.** Root cause: the `REPOGRAPH_BOUNDARY_ARTIFACT_B64`
  CI secret decoded to a **content-less** payload (parsed fine, zero
  `forbidden_names`), so `require_boundary_artifact=true` had nothing to enforce
  and B2 fired. Fix: refreshed the secret from the canonical boundary artifact
  (`PrivateManifest@83d600bd`; `forbidden_names` = the five private repos) on **all
  18 public repos** (#330 + fleet-wide). Refreshing it also activates B1/R2, which
  surfaced two genuine leaks (a doc line + a `.console/log.md` alias) — both
  scrubbed. *Verification:* `custodian-multi --repos .` → **0 findings**; the OC
  CI `audit` job flipped red→green (run on `1ec51f7e`). The Custodian B2 message
  now distinguishes *content-less* from *not-provided* (Custodian #48), which also
  un-masked a detector-ID collision that had hidden a real R2 finding.
- **Audit gate — now REQUIRED.** OC `main` branch protection requires the `audit`
  status check with `enforce_admins=true` (#2). No PR — fleet or manual — merges
  over a red audit. The reviewer's verdict is likewise now a required
  `reviewer-verdict` status check (#333), closing the manual-merge bypass.
- **Fleet `.venv` — current.** Bumped the custodian pin `0fa072f → d6ba8ab` (#331,
  DAGExecutor #12) and reinstalled the venvs, so the reviewer fleet (which runs
  `OC/.venv/bin/custodian-multi`) audits with the same detectors as CI.

See `docs/design/SELF_HEAL_LADDER.md` and the session memory
`boundary-b2-required-gate` for the operational detail. The earlier root-cause
investigation scratch notes were folded into this section and removed.
