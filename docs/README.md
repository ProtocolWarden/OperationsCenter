# OperationsCenter Documentation

Index for the `docs/` tree. The README at the repo root covers OC's primary
operator model and execution boundary; this directory holds OC-internal
architecture, design notes, operator how-tos, and historical material.

The cross-repo platform architecture (ownership, contracts, routing, backend
adapters, policy guardrails) lives in [PlatformDeployment/docs/architecture/](https://github.com/ProtocolWarden/PlatformDeployment/tree/main/docs/architecture).
This directory holds OC-specific material.

## Testing

- [TESTING.md](TESTING.md) — Parallel test execution with pytest-xdist: setup, usage, configuration, and performance.
- [TESTING_DEVELOPER_GUIDE.md](TESTING_DEVELOPER_GUIDE.md) — Writing parallel-safe tests: fixture patterns, common pitfalls, and best practices.
- [TESTING_TROUBLESHOOTING.md](TESTING_TROUBLESHOOTING.md) — Diagnosing and fixing parallel test issues: shared state, race conditions, worker crashes.

## Operator

- [operator/setup.md](operator/setup.md) — First-time setup and config.
- [operator/runtime.md](operator/runtime.md) — Day-to-day runtime usage.
- [operator/diagnostics.md](operator/diagnostics.md) — How to inspect a run.
- [operator/tuning.md](operator/tuning.md) — Threshold and policy tuning.
- [operator/pr_review.md](operator/pr_review.md) — PR-review worker usage.
- [operator/run-artifacts.md](operator/run-artifacts.md) — Layout and contents of
  `~/.console/operations_center/runs/<run_id>/`.
- [operator/switchboard.md](operator/switchboard.md) — Operating against
  SwitchBoard from OC.
- [operator/weekly_audits.md](operator/weekly_audits.md) — Weekly audit cadence.
- [operator/manifest_authoring.md](operator/manifest_authoring.md) — How to author a `topology/project_manifest.yaml` for a project repo.
- [operator/manifest_wiring.md](operator/manifest_wiring.md) — How OC picks up project + local manifests at runtime; the `platform_manifest:` settings block.
- [operator/slow_test_reporting.md](operator/slow_test_reporting.md) — Per-test duration reporting and slow-test threshold warning: usage, configuration, and CI integration.
- [operator/propagation/post-merge-hook.md](operator/propagation/post-merge-hook.md) — Post-merge propagation hook setup.
- [operator/archon_workflow_registration.md](operator/archon_workflow_registration.md) — Historical: Archon workflow registration handshake (Archon removed; kept for historical reference).

## Executor Lanes & Worker Backends

OC dispatches work through two categories of backend:

**Executor Lanes** — orchestration services that coordinate one or more worker
backends. Route tasks here when multi-agent or critique topology is needed:
- `team_executor` — parallel team of agents with configurable team composition
- `dag_executor` — DAG-structured workflow with per-node agent assignments
- `critique_executor` — proposer + critic refinement loop

**Direct Worker Backends** — single-agent runtimes invoked by OC directly.
Conceptually equivalent to `claude_code`/`codex_cli` but run locally. These
will migrate into executor services in a future work order:
- `aider_local` — aider against a local Ollama model
- `direct_local` — aider against a remote SwitchBoard model proxy

Backend docs:

- [backends/aider_local.md](backends/aider_local.md) — `aider_local` backend
  adapter behaviour and config.

## Architecture (OC-specific)

- [architecture/audit/audit_architecture.md](architecture/audit/audit_architecture.md) —
  Audit-dispatch architecture inside OC.
- [architecture/audit/audit_triage_plan.md](architecture/audit/audit_triage_plan.md)
- [architecture/policy/anti_collapse_invariant.md](architecture/policy/anti_collapse_invariant.md)
- [architecture/audit/backend_control_audit.md](architecture/audit/backend_control_audit.md)
- [architecture/ci/ci_integration_guide.md](architecture/ci/ci_integration_guide.md)
- [architecture/audit/code_health_audit.md](architecture/audit/code_health_audit.md)
- [architecture/contracts/contract-map.md](architecture/contracts/contract-map.md)
- [architecture/contracts/platform_manifest_consumption.md](architecture/contracts/platform_manifest_consumption.md) —
  How OC consumes PlatformManifest as topology/visibility metadata without
  owning the ontology, CxRP, or RxP.
- [architecture/contracts/execution-handoff-cutover.md](architecture/contracts/execution-handoff-cutover.md)
- [architecture/contracts/execution_target.md](architecture/contracts/execution_target.md)
- [architecture/contracts/lifecycle_labels.md](architecture/contracts/lifecycle_labels.md)
- [architecture/contracts/upstream-patch-evaluation.md](architecture/contracts/upstream-patch-evaluation.md) —
  Upstream-patch evaluation policy + decision shape.
- [architecture/contracts/upstream-patch-evaluation-examples.md](architecture/contracts/upstream-patch-evaluation-examples.md) —
  Worked examples for the upstream-patch policy.
- [architecture/recovery/phantom_helper_waves.md](architecture/recovery/phantom_helper_waves.md)
- [architecture/policy/policy-pre-execution-gate.md](architecture/policy/policy-pre-execution-gate.md)
- [architecture/recovery/recovery_loop_design.md](architecture/recovery/recovery_loop_design.md)
- [architecture/routing/routing-contract-fidelity.md](architecture/routing/routing-contract-fidelity.md)
- [architecture/routing/routing-tuning.md](architecture/routing/routing-tuning.md) —
  Routing knobs and how to tune them per workload.
- [architecture/routing/routing-tuning-examples.md](architecture/routing/routing-tuning-examples.md) —
  Worked tuning examples.
- [architecture/adr/](architecture/adr/) — OC architecture decision records.
  - [architecture/adr/0001-execution-boundary-operationscenter.md](architecture/adr/0001-execution-boundary-operationscenter.md) —
    Decision to make OperationsCenter the execution boundary.
  - [architecture/adr/0002-backend-card-axis-expansion.md](architecture/adr/0002-backend-card-axis-expansion.md) —
    Backend card axis expansion (orchestration + mechanism).
  - [architecture/adr/0003-tiered-cognition-experimental-rails.md](architecture/adr/0003-tiered-cognition-experimental-rails.md) —
    Tiered cognition + experimental rails.
  - [architecture/adr/0005-owned-execution-topology-layer.md](architecture/adr/0005-owned-execution-topology-layer.md) —
    Drop external runtimes (kodo/Archon — historical); build TeamExecutor, DAGExecutor, CritiqueExecutor.
  - [architecture/adr/0005-work-order.md](architecture/adr/0005-work-order.md) —
    4-phase work order for the owned execution topology layer (ADR 0005).
  - [architecture/adr/0005-work-order-p5.md](architecture/adr/0005-work-order-p5.md) —
    Phase 5 cross-repo wiring: executor adapters, LaneDecision metadata, worker_backend.
    Current executor runtime also supports worker-backend round robin
    (`claude_code` preferred, `codex_cli` fallback with cooldown persistence).

### Managed-repo audit contracts

The contracts a managed repo must honor for OperationsCenter to audit
it. Operator-private bindings (which name a specific managed repo)
live under `config/managed_repos/local/`; the docs below describe the
shape, not any particular bound repo.

- [architecture/managed-repos/managed_repo_contract.md](architecture/managed-repos/managed_repo_contract.md)
- [architecture/managed-repos/audit_artifact_contract.md](architecture/managed-repos/audit_artifact_contract.md)
- [architecture/managed-repos/audit_ground_truth.md](architecture/managed-repos/audit_ground_truth.md)

> **Tuning and upstream-patch evaluation** (`routing-tuning.md`,
> `upstream-patch-evaluation.md` and their examples) are now sourced from
> PlatformDeployment: see [PlatformDeployment/docs/architecture/](https://github.com/ProtocolWarden/PlatformDeployment/tree/main/docs/architecture).

## Design

- [design/roadmap.md](design/roadmap.md)
- [design/lifecycle.md](design/lifecycle.md)
- [design/improve_worker.md](design/improve_worker.md)
- [design/continuous-improvement/design.md](design/continuous-improvement/design.md) — DRAFT: schema extension for evaluation-driven refinement within existing lanes
- [design/plane_kodo_wrapper.md](design/plane_kodo_wrapper.md) — Historical: Plane/kodo integration design (kodo replaced by TeamExecutor)
- [design/autonomy/repo_aware_autonomy.md](design/autonomy/repo_aware_autonomy.md)
- [design/execution_budget_and_safety_controls.md](design/execution_budget_and_safety_controls.md)
- [design/observer-race-condition-guard.md](design/observer-race-condition-guard.md) — TOCTOU hardening for CheckSignal and DependencyDrift collectors
- [design/deriver-coverage/DERIVER_REVERSE_TRANSITION_COVERAGE_ANALYSIS.md](design/deriver-coverage/DERIVER_REVERSE_TRANSITION_COVERAGE_ANALYSIS.md) — Deriver reverse transition coverage analysis
- [design/deriver-coverage/IMPLEMENTATION_VERIFICATION_CHECKLIST.md](design/deriver-coverage/IMPLEMENTATION_VERIFICATION_CHECKLIST.md) — Deriver implementation verification checklist
- [design/deriver-coverage/STAGE0_INVESTIGATION_SUMMARY.md](design/deriver-coverage/STAGE0_INVESTIGATION_SUMMARY.md) — Stage 0 investigation summary
- [design/deriver-coverage/STAGE3_COMPLETION_REPORT.md](design/deriver-coverage/STAGE3_COMPLETION_REPORT.md) — Stage 3 testing completion report
- [design/deriver-coverage/STAGE3_TESTING_VERIFICATION.md](design/deriver-coverage/STAGE3_TESTING_VERIFICATION.md) — Stage 3 testing verification
- [design/deriver-coverage/STAGE3_TEST_INVENTORY.md](design/deriver-coverage/STAGE3_TEST_INVENTORY.md) — Stage 3 test inventory
- [design/autonomy/autonomy_decision_engine.md](design/autonomy/autonomy_decision_engine.md)
- [design/autonomy/autonomy_gaps.md](design/autonomy/autonomy_gaps.md)
- [design/autonomy/autonomy_insight_engine.md](design/autonomy/autonomy_insight_engine.md)
- [design/autonomy/autonomy_proposer_integration.md](design/autonomy/autonomy_proposer_integration.md)
- [design/autonomy/autonomy_repo_observer.md](design/autonomy/autonomy_repo_observer.md)
- [design/autonomy/autonomy_self_tuning_regulator.md](design/autonomy/autonomy_self_tuning_regulator.md)

## Specs

- [specs/proposer-consumes-custodian-sweep.md](specs/proposer-consumes-custodian-sweep.md)
- [specs/reviewer-pr-state-machine.md](specs/reviewer-pr-state-machine.md)
- [specs/watcher-entrypoint-test-coverage.md](specs/watcher-entrypoint-test-coverage.md)
- [specs/executor-adapter-unit-tests.md](specs/executor-adapter-unit-tests.md)
- [specs/p5-adapter-and-board-unblock-tests.md](specs/p5-adapter-and-board-unblock-tests.md)
- [specs/platform-cli-test-coverage.md](specs/platform-cli-test-coverage.md)

## Demo & Backlog

- [demo.md](demo.md) — End-to-end planning → routing → execution walkthrough.
- [backlog.md](backlog.md) — Backlog of in-flight work (see also `.console/backlog.md`).

## History

One-shot audit reports, completed migrations, and the managed-repo audit
subsystem phase docs (now stable). Retained for context; not authoritative
guidance.

- [history/audits/](history/audits/) — `dod_verification_final`, the rename-refactor
  verifications, integration-invariants verification, `flow_audit`, `ghost_work_audit`.
- [history/migration/](history/migration/) — `controlplane-execution-extraction`.
- [history/managed-repo/](history/managed-repo/) — Managed-repo audit subsystem
  phase docs: dispatch, toolset contract, artifact index, run identity, behavior
  calibration, full-audit governance, fixture harvesting, mini-regression suite,
  slice replay, final verification.
- [history/development-log/](history/development-log/) (under previous `history/`) —
  development log and remediation summaries.

- [ADR 0006 — CoreRunner rename and subprocess safety consolidation](architecture/adr/0006-corerunner-subprocess-consolidation.md)
- [ADR 0009 — Work Order: Execution Hygiene](architecture/adr/0009-work-order-execution-hygiene.md)
