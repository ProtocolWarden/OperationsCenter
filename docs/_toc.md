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
- **Propagation**: [`propagation/post-merge-hook.md`](operator/propagation/post-merge-hook.md)

## Architecture — `architecture/` (39)

Every document is listed individually rather than by directory: Custodian's DC7
detector flags any doc under `docs/` that no tracked document links to, and a
directory-level mention leaves its contents orphaned.

- **Decision records**: [`adr/README.md`](architecture/adr/README.md) — ADRs 0001–0010, plus work orders
- **Subsystems**: [`pr_review_watcher.md`](architecture/pr_review_watcher.md),
  [`verdict_consolidation.md`](architecture/verdict_consolidation.md),
  [`maintenance_pattern.md`](architecture/maintenance_pattern.md)
- **Cross-repo interfaces** — `contracts/`:
  [contract map](architecture/contracts/contract-map.md),
  [execution-handoff cutover](architecture/contracts/execution-handoff-cutover.md),
  [`ExecutionTarget`](architecture/contracts/execution_target.md),
  [lifecycle labels](architecture/contracts/lifecycle_labels.md),
  [PlatformManifest consumption](architecture/contracts/platform_manifest_consumption.md),
  and two redirect stubs now sourced from PlatformDeployment
  ([upstream-patch evaluation](architecture/contracts/upstream-patch-evaluation.md),
  [examples](architecture/contracts/upstream-patch-evaluation-examples.md))
- **Audit** — `audit/`: [audit architecture](architecture/audit/audit_architecture.md),
  [triage plan](architecture/audit/audit_triage_plan.md),
  [backend control audit](architecture/audit/backend_control_audit.md),
  [code-health audit](architecture/audit/code_health_audit.md)
- **Policy** — `policy/`: [anti-collapse invariant](architecture/policy/anti_collapse_invariant.md),
  [pre-execution gate](architecture/policy/policy-pre-execution-gate.md)
- **Recovery** — `recovery/`: [recovery loop design](architecture/recovery/recovery_loop_design.md),
  [phantom-helper waves](architecture/recovery/phantom_helper_waves.md)
- **Routing** — `routing/`: [contract fidelity](architecture/routing/routing-contract-fidelity.md),
  plus redirect stubs ([routing tuning](architecture/routing/routing-tuning.md),
  [examples](architecture/routing/routing-tuning-examples.md))
- **CI** — `ci/`: [integration guide](architecture/ci/ci_integration_guide.md),
  [coverage gating](architecture/ci/coverage-gating.md)
- **Managed repos** — `managed-repos/`: [managed-repo contract](architecture/managed-repos/managed_repo_contract.md),
  [audit artifact contract](architecture/managed-repos/audit_artifact_contract.md),
  [audit ground truth](architecture/managed-repos/audit_ground_truth.md)

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
  [`observer-race-condition-guard.md`](design/observer-race-condition-guard.md),
  [`test-failure-extraction.md`](design/test-failure-extraction.md)
- **Sub-areas**: `design/autonomy/` (7), `design/continuous-improvement/`

## Coverage alerting — read in this order

The one feature whose documentation legitimately spans four directories (see
[`structure.md`](structure.md#one-subject-one-home--but-not-one-document)).
Nothing else links these together, so start here:

1. [`coverage-threshold-configuration.md`](coverage-threshold-configuration.md) — the short version: thresholds and developer workflow
2. [`guides/COVERAGE_ALERTING_USAGE.md`](guides/COVERAGE_ALERTING_USAGE.md) — worked examples
3. [`guides/COVERAGE_ALERTING_CONFIGURATION.md`](guides/COVERAGE_ALERTING_CONFIGURATION.md) — full configuration
4. [`guides/COVERAGE_ALERTING_INTEGRATION.md`](guides/COVERAGE_ALERTING_INTEGRATION.md) — wiring it in
5. [`guides/COVERAGE_ALERTING_TROUBLESHOOTING.md`](guides/COVERAGE_ALERTING_TROUBLESHOOTING.md) — when it misfires
6. [`reference/COVERAGE_ALERTING_API_REFERENCE.md`](reference/COVERAGE_ALERTING_API_REFERENCE.md) — API lookup
7. [`architecture/ci/coverage-gating.md`](architecture/ci/coverage-gating.md) — how the CI gate itself works
8. [`design/coverage-threshold-alerting-design.md`](design/coverage-threshold-alerting-design.md) — why it is built this way
9. [`design/COVERAGE_THRESHOLD_ALERTING_USER_GUIDE.md`](design/COVERAGE_THRESHOLD_ALERTING_USER_GUIDE.md) — long-form guide (overlaps 2–5; consolidation candidate)

## Specs — `specs/` (29)

Scoped work definitions, usually authored for the fleet to execute.

Named specs:
[proposer consumes custodian sweep](specs/proposer-consumes-custodian-sweep.md) ·
[reviewer PR state machine](specs/reviewer-pr-state-machine.md) ·
[executor adapter unit tests](specs/executor-adapter-unit-tests.md) ·
[P5 adapter + board-unblock tests](specs/p5-adapter-and-board-unblock-tests.md) ·
[platform CLI test coverage](specs/platform-cli-test-coverage.md) ·
[watcher entrypoint test coverage](specs/watcher-entrypoint-test-coverage.md) ·
[admin API test coverage](specs/admin-api-test-coverage.md) ·
[decision-rules test coverage](specs/decision-rules-test-coverage.md) ·
[operational-health test coverage](specs/operational-health-test-coverage.md) ·
[recovery-subsystem test coverage](specs/recovery-subsystem-test-coverage.md) ·
[adaptive-loop integration tests](specs/adaptive-loop-integration-tests.md) ·
[CI coordinator decision tests](specs/ci-coordinator-decision-tests.md) ·
[CxRP backend-card vocabulary](specs/cxrp-backend-card-vocabulary.md) ·
[scene-timing audit test hardening](specs/scene-timing-audit-test-hardening.md)

The remaining `queue-drain-<timestamp>.md` files are per-run drain records
rather than authored specs.

## Guides and reference

| Area | Contents |
|---|---|
| `guides/` (4) | Coverage alerting — see the reading order above |
| `user-guides/` (2) | [Snapshot-validation CLI guide](user-guides/SNAPSHOT_VALIDATION_CLI_GUIDE.md), [quick reference](user-guides/CLI_QUICK_REFERENCE.md) |
| `reference/` (2) | [Coverage alerting API](reference/COVERAGE_ALERTING_API_REFERENCE.md), [extraction-fidelity metric](reference/EXTRACTION_FIDELITY_METRIC.md) |
| `troubleshooting/` (1) | [Review-backend troubleshooting](troubleshooting/review_backend.md) |
| `custodian/` (2) | [`.console/` reconciliation detectors](custodian/console-reconciliation-detectors.md), [test strategy](custodian/console-reconciliation-test-strategy.md) |
| `backends/` (1) | [`aider_local`](backends/aider_local.md) |

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
| `history/stages/deriver-coverage/` (7) | Deriver reverse-transition coverage episode: investigation, test inventory, completion reports |
| [`history/README.md`](history/README.md) | Overview of what the archive holds |
| `history/audits/` (6) | Completed audit reports |
| `history/development-log/` (14) | Narrative development records |
| `history/managed-repo/` (10) | Managed-repo history |
| `history/migration/` (1) | Migration records |
| `history/console-log/` (1) | Rotated `.console/log.md` entries — the file is budgeted at 500KB by Custodian's OC2 detector |
| [`history/runtime-truth-remediation-summary.md`](history/runtime-truth-remediation-summary.md) | Runtime-truth remediation |
