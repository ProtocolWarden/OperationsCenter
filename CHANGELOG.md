# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Fixed TOCTOU race condition in Collector** — Eliminated file deletion race by capturing mtime at discovery time. Files deleted during discovery are now skipped gracefully instead of crashing.

### Changed
- `CheckSignalCollector.latest_matching_file()` now returns `tuple[Path, float] | None` (was `Path | None`), where the float is the captured mtime at discovery time.
- `DependencyDriftCollector._latest_dependency_report()` now returns `tuple[Path, float] | None` (was `Path | None`), where the float is the captured mtime at discovery time.
- Both collectors now unpack the returned tuple and use the captured mtime in signal generation, eliminating the second `stat()` call.

### Documentation
- Added `docs/design/observer-race-condition-guard.md` documenting the TOCTOU race condition vulnerability, the metadata capture guard mechanism, implementation examples, error handling strategy, testing approach, and operational impact.

### Reconciled
_Console history consolidated to the private archive (2026-06-04). Items shipped:_

- 2026-06-04: reconciled `unit-coverage-90-gate` — Unit coverage climb to a 90% gate (history archived).
- 2026-06-04: reconciled `observer-coverage-spec-authoring` — Spec authoring — observer test coverage campaign (history archived).
- 2026-06-04: reconciled `ci-coverage-threshold-gate` — Update CI/CD pipeline to gate on coverage threshold (Stages 0-4) (history archived).
- 2026-06-04: reconciled `export-validation-failure-metrics` — Export validation failure metrics for alerting (history archived).
- 2026-06-04: reconciled `import-error-test-refactor` — Import-error test refactoring (shared pytest fixtures) (history archived).
- 2026-06-04: reconciled `error-handling-documentation` — Error handling documentation (recipes, catalog, contracts, runbook) (history archived).
- 2026-06-04: reconciled `deriver-transition-coverage` — Deriver transition coverage (bidirectional, Stages 0-4) (history archived).
- 2026-06-04: reconciled `collector-json-hardening` — Collector JSON hardening (validation + security logging) (history archived).
- 2026-06-04: reconciled `switchboard-live-verification` — SwitchBoard live verification rev (deploy-skew bug + runbook) (history archived).
- 2026-06-04: reconciled `sourceregistry-real-wiring` — SourceRegistry — wire it for real (Option B, provenance propagation) (history archived).
- 2026-06-04: reconciled `platformdeployment-compose-profile-smoke` — WorkStation compose profile smoke per profile (history archived).
- 2026-06-04: reconciled `observability-config-skeleton` — Ship observability config skeleton (WorkStation (history archived).
- 2026-06-04: reconciled `oc-kodo-archon-cards-superseded` — OC — kodo + archon executor cards (superseded by team_executor) (history archived).
- 2026-06-04: reconciled `archon-workflow-registration-playbook` — Archon workflow registration playbook (history archived).
- 2026-06-04: reconciled `capacity-exhaustion-regression-fixture` — Capacity-exhaustion regression fixture (history archived).
- 2026-06-04: reconciled `oc-run-show-provenance-reader` — operations-center-run-show single-command provenance reader (history archived).
- 2026-06-04: reconciled `artifact-path-staleness-checks` — Artifact path staleness checks (history archived).
- 2026-06-04: reconciled `routing-rationale-completeness-smoke` — Routing rationale completeness smoke check (history archived).
- 2026-06-04: reconciled `opscenter-custodian-coverage-bridge` — OpsCenter <-> Custodian coverage bridge (history archived).
- 2026-06-04: reconciled `phase7-multi-run-artifact-index` — Phase 7 — multi-run historical artifact index + CLI (history archived).
- 2026-06-04: reconciled `phase6-dispatch-control-crash-safety` — Phase 6 — dispatch control crash-safety + dual-PID tracking (history archived).
- 2026-06-04: reconciled `effective-repo-graph-contract-impact-wiring` — EffectiveRepoGraph + contract impact wired into production (history archived).
- 2026-06-04: reconciled `archon-patch-001-upstream-pr-superseded` — File upstream PR for Archon PATCH-001 (superseded; archon removed) (history archived).
- 2026-06-04: reconciled `three-layer-manifest-primitive` — 3-layer manifest primitive — operationally complete (R1-R4) (history archived).
- 2026-06-04: reconciled `r5-cross-repo-task-chaining` — R5 — Cross-repo task chaining (propagation library + CLIs) (history archived).
- 2026-06-04: reconciled `er000-phase0-golden-tests` — ER-000 — Phase 0 golden tests (history archived).
- 2026-06-04: reconciled `er003-lifecycle-primitive` — ER-003 — Lifecycle primitive (history archived).
- 2026-06-04: reconciled `collector-json-hardening-stage2` — Collector JSON hardening — Stage 2 implementation (history archived).
- 2026-06-04: reconciled `managed-repo-audit-phases` — Managed-repo audit system — Phases 0-12 + verification passes (history archived).
- 2026-06-04: reconciled `cycle-board-unblock-housekeeping` — Cycle board-unblock + task housekeeping (operational) (history archived).
- 2026-06-04: reconciled `kodo-openclaw-regression-fix` — Fix kodo->openclaw regression in tests; CxRP 0.3.1 verdict bump (history archived).

