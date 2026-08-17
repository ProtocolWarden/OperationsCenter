# Documentation index

Every documentation area, what lives there, and the entry point. For *where a new
document belongs*, see [`structure.md`](structure.md).

Counts are approximate and will drift; the groupings are the contract.

## Start here

| Document | What it is |
|---|---|
| [`README.md`](README.md) | Documentation entry point |
| [`demo.md`](demo.md) | End-to-end demo of the system |
| [`operator/setup.md`](operator/setup.md) | Setup guide — first stop for a new host |
| [`structure.md`](structure.md) | Where documentation goes |

## Operating the system — `operator/` (26)

Runbooks and recovery. The densest and most load-bearing area.

- **Setup & runtime**: [`setup.md`](operator/setup.md), [`runtime.md`](operator/runtime.md),
  [`diagnostics.md`](operator/diagnostics.md), [`run-artifacts.md`](operator/run-artifacts.md)
- **The loop & review**: [`watchdog_loop.md`](operator/watchdog_loop.md),
  [`pr_review.md`](operator/pr_review.md),
  [`merge_decision_instrumentation.md`](operator/merge_decision_instrumentation.md)
- **When it breaks**: [`error_handling_recovery.md`](operator/error_handling_recovery.md),
  [`error_handling_recipes.md`](operator/error_handling_recipes.md),
  [`error_handling_quick_reference.md`](operator/error_handling_quick_reference.md),
  [`error_message_diagnostics.md`](operator/error_message_diagnostics.md),
  [`error_scenarios.md`](operator/error_scenarios.md),
  [`backend_error_catalog.md`](operator/backend_error_catalog.md),
  [`executor_failure_contracts.md`](operator/executor_failure_contracts.md)
- **Policy & healing**: [`recovery_policy.md`](operator/recovery_policy.md),
  [`self_healing_model.md`](operator/self_healing_model.md), [`tuning.md`](operator/tuning.md)
- **Integrations**: [`switchboard.md`](operator/switchboard.md),
  [`switchboard_live_verification.md`](operator/switchboard_live_verification.md),
  [`platformdeployment_compose_smoke.md`](operator/platformdeployment_compose_smoke.md),
  [`archon_workflow_registration.md`](operator/archon_workflow_registration.md)
- **Manifests & repos**: [`manifest_authoring.md`](operator/manifest_authoring.md),
  [`manifest_wiring.md`](operator/manifest_wiring.md),
  [`managed_repo_troubleshooting.md`](operator/managed_repo_troubleshooting.md)
- **Audits & tests**: [`weekly_audits.md`](operator/weekly_audits.md),
  [`slow_test_reporting.md`](operator/slow_test_reporting.md)

## Architecture — `architecture/` (39)

- **Decision records**: [`adr/`](architecture/adr/README.md) — ADRs 0001–0010, plus work orders
- **Cross-repo interfaces**: [`contracts/`](architecture/contracts/contract-map.md) —
  contract map, execution handoff, `ExecutionTarget`, lifecycle labels, PlatformManifest consumption
- **Subsystems**: [`pr_review_watcher.md`](architecture/pr_review_watcher.md),
  [`verdict_consolidation.md`](architecture/verdict_consolidation.md),
  [`maintenance_pattern.md`](architecture/maintenance_pattern.md)
- **Audit**: [`audit/`](architecture/audit/audit_architecture.md) — architecture, triage plan, code health, backend control
- **Policy**: [`policy/`](architecture/policy/anti_collapse_invariant.md) — anti-collapse invariant, pre-execution gate
- **Recovery**: [`recovery/`](architecture/recovery/recovery_loop_design.md)
- **Routing**: [`routing/`](architecture/routing/routing-contract-fidelity.md)
- **CI**: [`ci/`](architecture/ci/ci_integration_guide.md) — integration guide, coverage gating
- **Managed repos**: [`managed-repos/`](architecture/managed-repos/managed_repo_contract.md)

## Design — `design/` (47)

Live design documents. Selected entries:

- **Change control & trust**: [`COUNCIL_VERDICT.md`](design/COUNCIL_VERDICT.md),
  [`HARNESS_TRUST_HARDENING.md`](design/HARNESS_TRUST_HARDENING.md),
  [`RISK_TIERED_APPROVAL.md`](design/RISK_TIERED_APPROVAL.md),
  [`RUNTIME_CAPABILITY_ENFORCEMENT.md`](design/RUNTIME_CAPABILITY_ENFORCEMENT.md)
- **Execution**: [`SELF_HEAL_LADDER.md`](design/SELF_HEAL_LADDER.md),
  [`CODE_FAILURE_RETRY_CAP.md`](design/CODE_FAILURE_RETRY_CAP.md),
  [`execution_budget_and_safety_controls.md`](design/execution_budget_and_safety_controls.md),
  [`EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md`](design/EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md)
- **Inventory & roadmap**: [`INERT_MACHINERY_INVENTORY.md`](design/INERT_MACHINERY_INVENTORY.md),
  [`INCOMPLETE_INTEGRATION_REMEDIATION.md`](design/INCOMPLETE_INTEGRATION_REMEDIATION.md),
  [`roadmap.md`](design/roadmap.md), [`lifecycle.md`](design/lifecycle.md)
- **Testing**: [`flaky-test-reporter.md`](design/flaky-test-reporter.md),
  [`observer-race-condition-guard.md`](design/observer-race-condition-guard.md)
- **Sub-areas**: `design/autonomy/` (7), `design/deriver-coverage/` (6),
  `design/continuous-improvement/`

## Specs — `specs/` (29)

Scoped work definitions, usually authored for the fleet to execute.

## Guides and reference

| Area | Contents |
|---|---|
| [`guides/`](guides/) (4) | Coverage alerting: configuration, integration, usage, troubleshooting |
| [`user-guides/`](user-guides/) (2) | Snapshot-validation CLI guide and quick reference |
| [`reference/`](reference/) (2) | Coverage alerting API, extraction-fidelity metric |
| [`troubleshooting/`](troubleshooting/) (1) | Review-backend troubleshooting |
| [`custodian/`](custodian/) (2) | `.console/` reconciliation detectors and test strategy |
| [`backends/`](backends/) (1) | `aider_local` |

## Developing — `dev/` (3)

Working on OC itself, as opposed to operating it.

- [`TESTING.md`](dev/TESTING.md) — parallel execution with pytest-xdist
- [`TESTING_DEVELOPER_GUIDE.md`](dev/TESTING_DEVELOPER_GUIDE.md) — writing parallel-safe tests
- [`TESTING_TROUBLESHOOTING.md`](dev/TESTING_TROUBLESHOOTING.md) — diagnosing parallel-test failures

## History — `history/` (51)

**Not maintained.** Kept for provenance; see [`structure.md`](structure.md#history-is-a-graveyard-not-an-attic).

| Area | Contents |
|---|---|
| [`history/stages/`](history/stages/README.md) (18) | Per-stage work artifacts, moved out of the repo root 2026-08-17 |
| `history/audits/` (6) | Completed audit reports |
| `history/development-log/` (14) | Narrative development records |
| `history/managed-repo/` (10) | Managed-repo history |
| `history/migration/` (1) | Migration records |
