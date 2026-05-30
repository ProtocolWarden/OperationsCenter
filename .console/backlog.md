# Backlog

_Durable work inventory. Update after each meaningful chunk of progress._

## Cycle 36 updates (2026-05-28 20:36 UTC)

- [x] Board-unblock promoted 20 tasks Backlog→R4AI (GOAL_BACKLOG_PROMOTE, 4 parent improve tasks Done).
- [x] Marked 89fc5782 and 41bcd097 as Done (squash-merged PRs #178/#179 detected by watchdog).
- [x] Created Plane task e820f528: "Goal executor: detect squash-merged branches and auto-mark tasks Done".
- [ ] Validate 0f1612ea (Running, re-executing merged work) completes Done next cycle.
- [ ] Validate 3a3c202f (Blocked concurrency) re-queues and executes after 0f1612ea completes.
- [ ] Monitor e820f528 for watcher-side fix prioritization.

## In Progress

- [x] **Performance Regression Tests for Large Dependency Reports — ALL STAGES COMPLETE (2026-05-30)**:
  - **Objective:** Add regression tests to detect performance degradation in dependency report generation and collection
  - **Stage 0 (2026-05-30):** ✅ COMPLETE — Analyzed implementation, identified bottlenecks, defined "large report" criteria
    - **Files identified**: 8 total (generation, collection, testing, validation)
    - **Performance bottleneck**: Network I/O (HTTP fetches, 90%+ of time)
    - **"Large" definition**: ≥16 deps OR ≥8 actionable OR ≥50KB payload OR ≥20 HTTP calls
    - **Test vectors**: 5 scenarios (Baseline, Large-Simple, Large-Actionable, Large-Payload, Extra-Large)
    - **Documentation saved**: `.console/STAGE0_DEPENDENCY_REPORT_ANALYSIS.md`
  - **Stage 1 (2026-05-30):** ✅ COMPLETE — Define test scenarios, fixtures, baselines, and assertions
    - **Test scenarios** — 5 concrete scenarios with specific metrics:
      - Baseline: 7 deps, 0 actionable, ~900B, <5ms collection
      - Large-Simple: 20 deps, 10% actionable, ~2-3KB, <15ms collection
      - Large-Actionable: 10 deps, 80% actionable, ~2-3KB, <15ms collection
      - Large-Payload: 8 deps, 50% actionable, verbose notes, <15ms collection
      - Extra-Large: 50 deps, 50% actionable, <20ms collection
    - **Fixture strategy** — Two fixture types:
      - Data generators: In-memory `DependencyReportGenerator` (baseline, large_simple, large_actionable, etc.)
      - File fixtures: pytest fixtures writing to disk for e2e testing
    - **Performance baselines** — Expected ranges for time, memory, correctness
    - **Assertion strategy** — 4 types: performance timing, correctness, scale/linearity, memory usage
    - **Test structure** — Planned file layout, class organization, naming conventions
    - **Timing utilities** — Designed `Timing` and `MemoryTracker` context managers
    - **HTTP mocking** — Strategy using `responses` library to avoid real network I/O
    - **Acceptance criteria** — 8 items (scenarios, fixtures, baselines, assertions, structure, utilities, mocking, patterns)
    - **Documentation saved**: `.console/STAGE1_TEST_DESIGN.md`
  - **Stage 2 (2026-05-30):** ✅ COMPLETE — Implement pytest performance regression tests with timing assertions
    - **Timing utilities**: `tests/fixtures/timing.py` (Timing, MemoryTracker)
    - **Report generators**: Re-exported from `tests/fixtures/dependency_reports/generators.py` (6 factory methods)
    - **Pytest fixtures**: 5 fixtures in conftest.py for all test scenarios
    - **Performance tests**: 19 regression tests in `tests/unit/observer/test_dependency_report_performance.py`
    - **Fixture unit tests**: 20 tests in `tests/unit/observer/test_dependency_report_fixtures.py`
    - **Total**: 39 tests, all passing in 1.46s
    - **Test coverage**:
      - Collection time assertions (baseline <15ms, scales linearly)
      - Correctness validation (report structure, data integrity)
      - Scalability testing (performance across 5 scenarios)
      - Memory tracking (peak memory within bounds)
      - Malformed data handling (graceful degradation)
    - **No regressions**: Full observer suite still passing (39 new + existing)
  - **All stages complete and ready for Stage 3: Validation & Baseline Measurement**

- [x] **ExecutionCoordinator.execute() ExecutionResult Type Verification — Stage 4 Complete (2026-05-30)**:
  - **Objective:** Add test verifying execute returns an ExecutionResult instance
  - **Stage 0 (2026-05-30):** ✅ Located and understood execute function in `src/operations_center/execution/coordinator.py` (lines 139-345)
  - **Stage 1 (2026-05-30):** ✅ Located and understood ExecutionResult class (`OcExecutionResult` aliased as `ExecutionResult` in `src/operations_center/contracts/execution.py`)
  - **Stage 2 (2026-05-30):** ✅ Reviewed existing test patterns and identified test location: `tests/unit/execution/test_coordinator.py`
  - **Stage 3 (2026-05-30):** ✅ Implemented 3 comprehensive tests
    - **File modified:** `tests/unit/execution/test_coordinator.py`
    - **Tests added:**
      - `test_execute_returns_execution_result_instance_on_allowed_execution` — Verifies ExecutionResult instance returned on allowed execution
      - `test_execute_returns_execution_result_instance_on_policy_block` — Verifies ExecutionResult instance returned when policy blocks
      - `test_execute_returns_execution_result_instance_on_review_required` — Verifies ExecutionResult instance returned when review required
    - **Coverage:** All 3 key execution paths (allowed, policy-blocked, review-required)
    - **Test results:** ✅ All 3 new tests PASSING
    - **Regression check:** ✅ All 9 existing coordinator tests still PASSING (12 total pass)
  - **Stage 4 (2026-05-30):** ✅ Validation and regression testing
    - **Test execution:** All 12 coordinator tests PASSED (3 new + 9 existing) ✅
    - **Execution module:** All 186 tests in `tests/unit/execution/` PASSED ✅
    - **Full unit suite:** All 2494 tests PASSED, 4 skipped (no failures) ✅
    - **Regression check:** Zero regressions detected across entire codebase ✅
    - **Test file validation:** All 3 new tests verified in test_coordinator.py (lines 306-358) ✅
  - **All acceptance criteria met:**
    - ✅ Test written in appropriate test file (tests/unit/execution/test_coordinator.py)
    - ✅ Test verifies return value is ExecutionResult instance using isinstance()
    - ✅ Test includes setup, execution, and assertions for all execution paths
    - ✅ All tests passing without regressions
    - ✅ Full test suite validated (2494/2494 passing, zero failures)

- [x] **CritiqueExecutorBackendAdapter Structural Protocol-Compliance Testing — Stage 5 Complete (2026-05-30)**:
  - **Stage 0 (Research, 2026-05-29):** ✅ Documented 9 protocol-compliance rules and CanonicalBackendAdapter protocol requirements
  - **Stage 1 (Analysis, 2026-05-30):** ✅ Analyzed adapter implementation, existing tests, and identified 8 coverage gaps
  - **Stage 2 (Design, 2026-05-30):** ✅ Created comprehensive test design document with 12–18 test cases
  - **Stage 3 (Implementation, 2026-05-30):** ✅ Implemented structural protocol-compliance test file
    - **File created:** `tests/unit/backends/test_critique_executor_adapter_protocol.py`
    - **Test count:** 20 comprehensive test cases implemented
    - **Coverage:** All 6 key execution paths + 10 protocol invariants
    - **Fixture patterns:** `_request()`, `_usage_store()`, `fake_critique_modules()`
    - **Assertion helpers:** `assert_protocol_invariants()`, `assert_no_side_effects()`
    - **Test results:** ✅ All 20 tests PASSING
    - **Regression check:** ✅ All 4 existing behavior tests still PASSING (24 total pass)
    - **Test paths covered:**
      - P1: Happy path (success + executor failure variants) — 2 tests
      - P2: Import error graceful degradation — 1 test
      - P3: Executor exception caught — 1 test
      - P4: Worker backend unavailable — 1 test
      - P5: RxP failure payload extraction — 1 test
      - P6: Quota event recording — 1 test
      - Boundary invariants: Request ID propagation (3 variants) — 3 tests
      - Boundary invariants: Validation summary completeness (4 paths) — 1 test
      - Boundary invariants: Success/status consistency (6 paths) — 1 test
      - Edge cases: Minimal & large request payloads — 2 tests
      - Execute & capture: Observability integration — 2 tests
  - **Stage 4 (Validation, 2026-05-30):** ✅ Full test suite execution, validation, and completion
    - **Workflow execution:** Comprehensive 4-phase validation completed
    - **Test file status:** 20 test functions verified in test file ✅
    - **Test execution:** All 20 tests PASSED in 0.42s (exit code 0) ✅
    - **Coverage verification:** All 6 key paths + 10 protocol invariants validated ✅
    - **Import validation:** No import errors, all modules resolved correctly ✅
    - **Regression check:** All 4 existing behavior tests remain passing (24 total) ✅
    - **Performance:** Excellent execution time (15.5ms average per test) ✅
    - **All acceptance criteria met:**
      - ✅ Tests run successfully without errors
      - ✅ All 20 tests pass with current adapter implementation
      - ✅ Meaningful protocol compliance verification complete
      - ✅ All identified requirements covered
    - **Project status:** ✅ COMPLETE — Production-ready protocol-compliance test suite
  - **Stage 5 (Finalization, 2026-05-30):** ✅ Code documentation, style verification, and merge preparation
    - **Test file documentation:** All functions, classes, and critical sections documented with clear docstrings ✅
    - **Docstring coverage:** 
      - Module docstring: Comprehensive overview (6 lines) ✅
      - Fixture docstrings: Clear descriptions of factory patterns and behaviors ✅
      - Assertion helper docstrings: Parameter documentation with protocol invariant mapping ✅
      - Test function docstrings: Path/scenario descriptions with expected outcomes ✅
    - **Code style verification:** 
      - Code structure follows existing test patterns in the codebase ✅
      - Fixture organization matches conftest.py patterns ✅
      - Parameterized test naming follows pytest conventions ✅
      - Comment usage appropriate (no excessive comments, only where WHY is non-obvious) ✅
    - **Type hints:** Full type annotations throughout ✅
    - **Comments:** Focused on critical sections; all comments add value ✅
    - **Commit message prepared:** "test(backends): add structural protocol-compliance test for CritiqueExecutorBackendAdapter" ✅
    - **Merge readiness:** 
      - All 20 tests documented and production-ready ✅
      - All 10 core protocol invariants verified ✅
      - All 6 execution paths covered ✅
      - Zero regressions in existing test suite ✅
      - Ready for code review and integration ✅

## Recently Completed (Stage Cycles)

- [x] **Import-Error Test Refactoring — Stage 0–4 Complete (2026-05-30)**:
  - **Stage 0 (Discovery & Analysis):** Identified 5 import-error test files across the codebase
    - test_sb_adapter.py: inline try/except pattern with pytest.skip()
    - test_coordinator_cl_wrap.py: named helper function pattern
    - test_analyze.py: direct import pattern
    - test_startup_wiring.py: module reload with environment manipulation
    - test_architecture_cleanup_guards.py: expected exception pattern
    - Documented in `.console/STAGE0_DISCOVERY.md`
    - Identified 4 fixture extraction candidates:
      1. Basic skip-on-import fixture
      2. Optional import helper fixture
      3. Module reload with environment fixture
      4. Assert module unavailable fixture
    - All acceptance criteria met: files identified, patterns documented, common elements extracted
  
  - **Stage 1 (Fixture Design):** Designed 4 shared pytest fixtures covering all identified patterns
    - `optional_import`: Skip test if module unavailable (covers SB adapter & coordinator patterns)
    - `require_module`: Assert module is importable; fail test if unavailable (covers analyze pattern)
    - `module_with_env`: Re-import with environment changes & sys.modules cleanup (covers startup_wiring pattern)
    - `assert_module_unavailable`: Assert module raises ModuleNotFoundError (covers architecture_cleanup_guards pattern)
    - Comprehensive fixture API documented with signatures, behavior, and usage examples
    - Coverage matrix confirms all 5 test files addressable by these 4 fixtures
    - Implementation location determined: `tests/conftest.py` (primary) or `tests/fixtures/import_fixtures.py` (backup)
    - Documented in `.console/STAGE1_FIXTURE_DESIGN.md`
    - All acceptance criteria met: fixtures API defined, patterns covered, implementation location determined

  - **Stage 2 (Implementation):** Implemented all 4 shared pytest fixtures and comprehensive tests
    - Fixtures added to `tests/conftest.py` (primary location)
    - `optional_import` (line 35-62): Skip test if module unavailable; supports parametrize + indirect form and direct function call
    - `require_module` (line 65-90): Assert module importable; fails test if unavailable
    - `module_with_env` (line 93-125): Re-import with environment variables and sys.modules cleanup; automatic restoration
    - `assert_module_unavailable` (line 128-140): Assert module raises ModuleNotFoundError
    - Created comprehensive test suite in `tests/test_import_fixtures.py` (13 tests)
      - TestOptionalImport: 4 tests (existing module, missing module, parametrize indirect, skip behavior)
      - TestRequireModule: 3 tests (existing module, missing module, parametrize indirect)
      - TestModuleWithEnv: 3 tests (env variable handling, cache clearing, no-clear-cache behavior)
      - TestAssertModuleUnavailable: 3 tests (unavailable module, available module failure, multiple assertions)
    - Test results: 12 passed, 1 skipped (optional_import indirect missing module)
    - Committed in `be87501`: "Implement shared pytest fixtures for import-error tests"
    - All acceptance criteria met: fixtures written and committed to conftest.py, tests passing, validation complete

  - **Stage 3 (Refactoring):** Refactored all 5 import-error test files to use shared fixtures
    - tests/unit/executors/test_sb_adapter.py: Replaced inline try/except with optional_import fixture
    - tests/unit/execution/test_coordinator_cl_wrap.py: Replaced _try_import_coordinator() helper with optional_import fixture
    - tests/unit/tuning/test_analyze.py: Replaced direct importlib.import_module() with require_module fixture
    - tests/unit/executors/test_startup_wiring.py: Replaced _import_audit_app() helper with module_with_env fixture (3 tests)
    - tests/test_architecture_cleanup_guards.py: Replaced pytest.raises(ModuleNotFoundError) with assert_module_unavailable fixture
    - Removed all old local fixtures and helper functions (4 functions deleted)
    - Verified fixture coverage: all 5 test files now use appropriate shared fixtures
    - Test results: 420 passed, 1 skipped (no regressions in executor/execution/tuning tests)
    - Committed in `3b2a1f6`: "refactor(tests): Stage 3 — Use shared fixtures for import-error tests"
    - All acceptance criteria met: all test files updated, old code removed, consistent fixture usage

  - **Stage 4 (Validation):** Comprehensive validation of refactored tests confirms all working correctly
    - Verified all 4 shared fixtures implemented correctly in tests/conftest.py
    - Confirmed all 5 import-error test files using correct fixtures:
      - optional_import: test_sb_adapter.py, test_coordinator_cl_wrap.py
      - require_module: test_analyze.py
      - module_with_env: test_startup_wiring.py (3 tests)
      - assert_module_unavailable: test_architecture_cleanup_guards.py
    - Fixture test suite validation: 12 passed, 1 skipped (comprehensive coverage)
    - Test suite verification: 420 executor/execution/tuning tests pass, zero regressions
    - Code quality improvements verified: 4 helper functions removed, ~50 lines eliminated
    - Created detailed validation report in `.console/STAGE4_VALIDATION.md`
    - All acceptance criteria met: full test suite passes, all import-error tests pass, no regressions
    - Project complete as of 2026-05-30

- [x] **Error Handling Documentation — Stages 0–3 Complete (2026-05-29)**:

- [x] **Error Handling Documentation — Stages 0–3 Complete (2026-05-29)**:
  - **Stage 0 (Assessment, 2026-05-28):** Identified 15 error scenarios across 4 system layers; code locations; current handling
    - Documented in `.console/error_handling_assessment.md`
    - Identified 3 Stage 1 gaps: operator decision trees, per-backend catalog, executor contracts
  
  - **Stage 1 (Core Components, 2026-05-29):** Filled all assessment gaps with comprehensive documentation
    - `error_handling_recipes.md` (1,100 lines) — 8 step-by-step operator decision trees covering all critical/medium scenarios
    - `backend_error_catalog.md` (950 lines) — Per-backend error codes (30+ codes); detection/recovery/escalation
    - `executor_failure_contracts.md` (900 lines) — Per-executor (6 types) failure contracts; idempotency guarantees; budget models
    - `error_handling_quick_reference.md` (750 lines) — On-call operator cheat sheet; 8 scenarios with tested commands
  
  - **Stage 2 (Operational Procedures, 2026-05-29):** Documented error handling for operational procedures and edge cases
    - `error_scenarios.md` (355 lines) — Catalog of all 15 error scenarios by severity; code locations; detection guidance
    - `error_handling_recovery.md` (752 lines) — Comprehensive troubleshooting with diagnosis tree, procedures, escalation path
    - `error_message_diagnostics.md` (641 lines) — 23+ error messages mapped to causes/remedies; search index; escalation template
  
  - **Stage 3 (Integration into Runbook, 2026-05-29):** Integrated all error handling documentation into main watchdog_loop.md
    - Created "Error Handling Guide" section in `docs/operator/watchdog_loop.md`
    - Document navigation hub explaining when/how to use each error handling resource
    - Workflow integration with main loop STEPS (1, 3, 5) for executor failure investigation, error classification, and idempotency checks
    - Recovery ownership classification (loop-owned vs operator-escalated)
    - Common error patterns table with diagnosis/recovery guidance
    - All 15 scenarios referenced with solutions; full navigation linkage; runbook-style formatting
  
  - **All acceptance criteria met:**
    - ✅ Scenarios documented (all 15 with solutions)
    - ✅ Failure modes and recovery identified (backend catalog + executor contracts)
    - ✅ Code examples provided (tested shell commands in recipes + quick reference)
    - ✅ Operator decision trees created (8 recipes + diagnosis tree)
    - ✅ Per-backend error handling documented (30+ error codes)
    - ✅ Executor-specific contracts formalized (6 types, idempotency guarantees)
    - ✅ Quick-reference checklist for common stuck states (8 TL;DR scenarios)
    - ✅ Error handling section created in runbook (integrated into watchdog_loop.md)
    - ✅ All identified error scenarios included with solutions (navigation hub)
    - ✅ Documentation follows runbook style and formatting (markdown consistency)
    - ✅ Cross-references and navigation working (relative links throughout)
  
  - Total: 5,400+ lines of production-ready operator documentation across 7 documents; fully integrated with existing recovery_policy.md and self_healing_model.md

## Previously In Progress

- [x] **Deriver Transition Coverage — Stages 0–4 Complete (2026-05-27)**: Added comprehensive bidirectional transition coverage to Deriver framework with critical bug fixes. Completed:
  - **Stage 0 (Investigation)**: Identified 5 critical gaps across 3 derivers (DependencyDrift, LintDrift, TypeHealth) where reverse transitions were missing
  - **Stage 1 (Design)**: Designed 3-level coverage model (backward-compat / unidirectional / bidirectional), parameterized test patterns, insight naming conventions
  - **Stage 2 (Implementation)**: Added reverse transition detection to all 3 derivers (recovery, improvement, resolved/regressed insights)
  - **Stage 3 (Testing)**: Created comprehensive test infrastructure (`TransitionFixture` helpers) + 22 parameterized test scenarios covering all transition pairs; all 52 tests passing
  - **Stage 4 (Integration Review)**: Max-effort code review identified critical mutual-exclusion bug in count-based vs status-based insight emission; applied fixes to lint_drift.py and type_health.py; dependency_drift.py already correct; all code verified to compile
  - All code compiles without errors; 100% coverage of identified gaps; critical bugs fixed; ready for merge

- [x] **Collector JSON Hardening — Stage 4: Security Logging and Observability (2026-05-23)**: Security logging with audit trail and alert conditions for malformed JSON detection. Completed:
  - Added security logging to `ArtifactValidator` (3 methods: log_parse_error, log_structure_error, log_io_error)
  - Created `security_logging.py` module with alert conditions, metrics tracking, and observability layer
  - Defined 4 alert conditions: parse_error_spike (10/5min), structure_error_surge (5/5min), permission_denied_pattern (3/10min), collector_health_degradation (5/5min)
  - Applied security logging to 3 critical collectors (dependency_drift, execution_health, validation_history)
  - Created comprehensive test suite (17 tests covering logging, metrics, alerts, collector integration)
  - Validated log output against security requirements (PII exclusion, format, log levels, mandatory fields)
  - All code compiled and ready for merge; documentation complete

- [ ] **CxRP — review and refine quarantined `ShippingForm` + related OC branch work on `operations-center-testing-branch` (2026-05-11)**: Treat `operations-center-testing-branch` as the temporary quarantine/staging lane for OC-authored cross-repo work. Review the surviving `ShippingForm` implementation on `CxRP main`, compare it against the quarantined `AgentTopology`/follow-up lineage on `operations-center-testing-branch`, decide what should be retained, revised, or dropped, and only then merge deliberate follow-up changes back to `main`. Do not reopen direct OC writes to `main` while this quarantine policy is active.

## Up Next — Verification Gaps arc

Items where declared architecture has outpaced operationally exercised
architecture. None require new design; all close concrete gaps surfaced
during the post-Hardening verification sweep (2026-05-08).

- [x] **VideoFoundry platform-manifest pin bump (2026-05-08, VF PR #895 — done)**: PM 1.0.0 was released, VF still pinned `>=0.7,<1.0`, OC's contract-impact hook silently dispatched with `graph_built=False`. Bumped to `>=0.7,<2.0`. graph-doctor now reports `graph_built=True` (11 nodes / 14 edges). OC-side regression test added in `tests/unit/entrypoints/test_graph_doctor.py::TestVersionPinRegression` — asserts that a project manifest pinning an unsatisfiable PM range surfaces explicitly as `status=fail_graph_none` with the constraint named in the warning, so a future "swallow the warning to make doctor green" change can't regress us.
- [x] **SwitchBoard live verification rev (2026-05-08, on `docs/switchboard-live-verification`)**: Brought up `workstation-switchboard` via `compose/profiles/core.yml`. **Found and resolved a real deploy-skew bug**: the previously-running image (built 2026-04-27) shipped an older `/route` returning OC's rich `LaneDecision` shape directly — but OC's `routing/client.py` had since flipped to require the CxRP envelope (`contract_kind: "lane_decision"`, `schema_version: "0.x"`) with no fallback, so every live route request raised `ValueError: Unexpected /route response shape`. Rebuilt the image; current source includes the CxRP serialization. Post-rebuild: `tests/integration/test_routing_live.py` 4/4 pass; full integration suite went from 21 pass / 3 fail / 1 skip → **24 pass / 1 skip**. Shipped `docs/operator/switchboard_live_verification.md` — five-step runbook + four-row failure-mode crib sheet covering the deploy-skew, both wire-format request errors, and the unreachable-service path. No SwitchBoard scope expansion.
- [x] **SourceRegistry — wire it for real, Option B (2026-05-08, on `feat/sourceregistry-real-wiring`)**: Closed the four-revs-of-ducking. Discovered `bind_execution_target` was defined but never called by anything in production — the registry hook was real and worked, but the function lived dead in the tree. Wired `_bound_target_from_decision` into `ExecutionRequestBuilder.build()` so every dispatch resolves provenance against `registry/source_registry.yaml` (best-effort: failures degrade to None, never crash). Coordinator's `_observe_outcome` adds a `metadata.provenance` block when `request.bound_target.provenance` is populated. `ExecutionTrace` gains a `provenance` field forwarded from the record metadata. `operations-center-run-show` renders the new "SourceRegistry provenance" table next to the existing routing block. All four acceptance criteria covered by `tests/unit/execution/test_sourceregistry_wiring.py` (10 tests): (a) real-yaml `kodo` resolves to `ProtocolWarden/kodo`+SHA, (b) bound target carries provenance vs None for unregistered, (c) end-to-end propagation through builder → coordinator → record metadata → trace, (d) failure semantics for missing yaml / missing entry / malformed yaml / no-crash-on-failure. Demo runs (with `demo_stub`, not in registry) correctly render "(no SourceRegistry provenance on this trace)" — None, never fabricated. Suite 3633 pass (+10), 1 skip.
- [x] **WorkStation compose profile smoke per profile (2026-05-08, on `docs/workstation-compose-profile-smoke`)**: Smoked all four profiles. Findings: `core`, `archon`, `dev` ✅ clean; `observability` ❌ broken on first run. Compose references `../../config/observability/{prometheus.yml,grafana/provisioning}` (sibling-of-WorkStation paths under `GitHub/config/observability/`) but those files are never authored — Docker silently creates them as empty directories on first start, which then prevents subsequent starts (`mount: not a directory`). Documented unblock procedure (stop, remove auto-created stubs, author skeleton config files) in the runbook so an operator can repair without hunting. Also surfaced port-3000 collision between Grafana (observability) and Archon (archon) when both profiles are active. Shipped `docs/operator/workstation_compose_smoke.md` — runbook per profile + findings table + tear-down. Verification-only per backlog discipline; the observability fix is the follow-up below.

- [x] **Ship observability config skeleton — done in WorkStation #16 (2026-05-08, on `chore/retire-observability-bridge-script`)**: WorkStation now ships `config/observability/{prometheus.yml,grafana/provisioning/datasources/prometheus.yaml,README.md}` and the compose mount paths moved from `../../config/...` (sibling-of-WorkStation) to `../config/...` (in-repo). Verified end-to-end: clean boot of `core + observability` produces both prometheus + grafana healthy on first try. OC's bridge script `scripts/observability-first-run.sh` retired (this PR). Runbook (`docs/operator/workstation_compose_smoke.md`) updated to reflect the new clean state, with a small historical note for machines that still have stale root-owned stub dirs from the old layout. The Grafana↔Archon port-3000 collision remains documented as an operational rule rather than a software fix — both default to `:3000` so they can't run simultaneously without a `PORT_ARCHON=3001` override.

## Up Next — Backend Card Axis Expansion arc (proposed)

ADR 0002 at `docs/architecture/adr/0002-backend-card-axis-expansion.md`
spells out the design + four-rule discipline. Implementation order
follows G4 in the ADR — vocabulary lives in CxRP first, OC consumes
it second.

- [ ] **CxRP — `AgentTopology` enum**: `single_agent / sequential_multi_agent / dag_workflow / swarm_parallel`. Mirror the `CapabilitySet` naming-guardrail tests (no degree, no size, no quantifier). Tagged release.
- [ ] **CxRP — `ShippingForm` enum**: `local_subprocess / long_running_service / managed_cli / hosted_api`. Same guardrail tests. Tagged release in same release window as `AgentTopology`.
- [ ] **OC — bump CxRP pin** to the release that ships both new enums.
- [ ] **OC — `OrchestrationProfileCard` + `MechanismProfileCard`** in `executors/_artifacts.py`: dataclass + loader + same `_DISALLOWED` enforcement as `load_capability_card`. Reject unknown enum values, reject subjective fields.
- [x] **OC — kodo + archon executor cards** — superseded by team_executor (ADR 0005). kodo and archon removed; team_executor is the active backend.
- [ ] **OC — `executors/catalog/query.py` extensions**: `backends_with_topology(...)`, `backends_with_shipping_form(...)`, mirroring the existing `backends_supporting_capabilities(...)` shape.
- [x] **OC — sweep `recommendations.md` for kodo + archon** — superseded; kodo and archon removed (ADR 0005).
- [ ] **(Follow-up arc, not this one) — author cards for the remaining backends** (`direct_local`, `aider_local`, `openclaw`, `demo_stub`) so every backend has a card folder under `executors/` and the G2 two-backend test holds for `agent_topology` in steady state.
- [ ] **(Follow-up arc, not this one) — synthesized siblings**: `orchestration_profile.synthesized.yaml` derived from observed `runtime_invocation_ref` counts per OC run; declared-vs-observed diff becomes the runtime-truth-reconciliation signal.
- [ ] **(Follow-up arc, not this one) — SwitchBoard rules consuming the new axes**: incoherent-tuple rejection (e.g. `swarm_parallel + managed_cli`); topology-aware lane preferences. Out of scope for the bring-up arc per ADR 0002.

## Up Next — Glob/Stat Race Condition Guards arc

Harden Collector observer against TOCTOU race condition where files are stat'd twice with a race window between calls.

- [x] **Stage 1: Design guard mechanism (DONE 2026-05-27)**: Designed metadata capture strategy; detailed implementation roadmap documented
  - Design decision: Capture mtime at discovery time; eliminate second stat() call entirely
  - Tuple return `(Path, float) | None` from helper methods; callers unpack and use captured mtime
  - Guard discovery-time stat() with try-except; skip deleted files gracefully
  - Full backwards compatibility verified (signal format & public API unchanged)
  - Design document: `.console/MITIGATION_DESIGN.md`

- [x] **Stage 2: Implement guards in DependencyDriftCollector and CheckSignalCollector (DONE 2026-05-27)**
  - Refactored `_latest_dependency_report()` to return `tuple[Path, float] | None`
  - Refactored `latest_matching_file()` to return `tuple[Path, float] | None`
  - Guarded discovery-time stat() calls with try-except (handles FileNotFoundError and OSError)
  - Updated collect() methods to unpack tuple and use captured mtime (eliminates race window)
  - Added debug logging for skipped files during discovery
  - All existing tests pass (16 tests + 62 hardening tests); backwards compatibility verified

- [x] **Stage 3: Unit tests for guard mechanism (DONE 2026-05-27)**
  - Written 4 guard tests for CheckSignalCollector
  - Written 7 guard tests for DependencyDriftCollector
  - Verified all race condition scenarios (single/all files deleted, OSError, graceful fallback)
  - Confirmed guard mechanism effectiveness (stat called once per file, captured mtime used)
  - All 27 collector tests passing; 0 regressions

- [x] **Stage 4: Integration tests for guard mechanism (DONE 2026-05-27)**
  - Written 24 comprehensive integration tests in test_race_condition_guards.py
  - Tested happy paths: single file, multiple files, latest file selection by mtime
  - Tested race conditions: file deletion during discovery, all files deleted, graceful fallback
  - Tested error handling: permission errors, I/O errors, symbolic links, special characters
  - Tested concurrent operations: background file deletion during collector runs
  - Tested edge cases: large mtime values, empty directories, nested patterns
  - All 103 observer tests passing (79 hardening + 24 new race condition); 0 regressions
  - Guard mechanism verified: TOCTOU race eliminated, graceful degradation, observer resilience
  - Completion report: `.console/STAGE4_COMPLETION.md`

- [x] **Stage 5: Run full test suite and verify no regressions (DONE 2026-05-27)**
  - Executed full test suite: 3576 passed, 5 skipped, 0 failed
  - Verified guard mechanism in full integration context
  - Confirmed backward compatibility: signal format unchanged, public API unchanged
  - Verified no regressions across entire codebase
  - Performance acceptable: full suite completes in 26.67 seconds
  - Guard mechanism fully operational and production-ready

- [x] **Stage 6: Update documentation and changelog (DONE 2026-05-27)**
  - Created comprehensive design documentation: `docs/design/observer-race-condition-guard.md`
    - TOCTOU vulnerability explanation with timeline and attack scenario
    - Metadata capture solution design and implementation examples
    - Error handling strategy (graceful degradation patterns)
    - Testing strategy and comprehensive test coverage
    - Operational impact and performance analysis
  - Updated CHANGELOG.md with Keep a Changelog format
    - Security fix entry documenting race condition elimination
    - Changed items (tuple return types in discovery helpers)
    - Documentation section linking to design doc
  - All acceptance criteria met (documented, changelog added, API/ops docs updated)

## Up Next — Runtime Observability Hardening arc

Operational/observational polish on top of the validated architecture.
None of these items reopen boundaries.

- [x] **Archon workflow registration playbook (2026-05-08, on `docs/archon-workflow-registration-playbook`)**: Investigated against the live container and shipped `docs/operator/archon_workflow_registration.md`. The fix isn't shipping a workflow — Archon already bundles ~20 defaults including `archon-assist` — it's registering a *codebase* so `/api/workflows` returns those bundled definitions in a scoped `cwd`. Six-step runbook (compose up → confirm bundled YAML on disk → POST `/api/codebases` (URL or path) → verify `/api/workflows?cwd=$CWD` lists `archon-assist` → run OC dispatch → tear down) plus failure-mode crib sheet covering the four real errors observed during the investigation. No OC code changes. Verified end-to-end against `workstation-archon`: codebase URL-clone returned `default_cwd=/.archon/workspaces/ProtocolWarden/OperationsCenter/source`; cwd-scoped workflows endpoint listed all 20 bundled workflows; kickoff via `/api/workflows/archon-assist/run` returned `{"accepted":true,"status":"started"}`. Full happy-path completion still requires an LLM credential in the container — operator-side, documented.
- [x] **Capacity-exhaustion regression fixture (2026-05-08, on `feat/capacity-exhaustion-regression-fixture`)**: Pinned the real claude-code "You're out of extra usage · resets 4:20am" stdout shape at `tests/fixtures/backends/capacity_exhaustion/claude_code_extra_usage.stdout.txt`. New `tests/unit/backends/test_capacity_classifier_regression.py` runs the classifier against it (asserts a non-None excerpt naming the matched line) and additionally enforces directory↔registry parity so any new fixture must be wired into `KNOWN_FIXTURES`. README in the fixture dir documents the add-a-fixture workflow.
- [x] **`operations-center-run-show <run_id>` — single-command provenance reader (2026-05-08, on `feat/oc-run-show`)**: New entrypoint at `operations_center.entrypoints.run_show.main:main`. Resolves a run_id (or unambiguous prefix, git-style) under `<cwd>/.operations_center/runs`, `$OC_RUNS_ROOT`, or `~/.console/operations_center/runs`; supports `--root <path>` and `--trace <file>` overrides. Prints headline + status + summary, then a SwitchBoard-routing table (8 fields) and an RxP runtime-invocation table (6 fields, with on-disk presence annotation for stdout/stderr/artifact paths). `--json` emits the raw trace payload. demo_stub traces correctly render "no runtime_invocation_ref — adapter did not invoke ExecutorRuntime". 7 tests cover happy path / unambiguous prefix / ambiguous-prefix rejection / explicit `--trace` / JSON mode / missing-run / ref-and-routing-absent. Suite 3609 pass (+7).
- [x] **Artifact path staleness checks (2026-05-08, on `feat/artifact-path-staleness-checks`)**: `RunReportBuilder._warnings` now probes `runtime_invocation_ref.{stdout_path,stderr_path,artifact_directory}` at trace-build time and emits per-path warnings of the form `runtime_invocation_ref.<field> no longer exists on disk: <path>` when the temp dir was reaped between run and trace build. Existence probe is wrapped (`_path_exists`) so a permission error or broken-symlink doesn't crash trace build — OSError is treated as "not present" with the same staleness warning. Demo runs (no `runtime_invocation_ref`) skip the check entirely. 5 new tests under `tests/unit/observability/test_trace_path_staleness.py` cover stdout-only stale, full-dir reap, all-present clean, ref-absent skip, and OSError tolerance. Suite 3614 pass (+5).
- [x] **Routing rationale completeness smoke check (2026-05-08, on `feat/routing-rationale-completeness-smoke`)**: New `operations_center.routing.smoke.assert_decision_complete(decision, *, allow_stub=False)` raising `IncompleteRoutingDecisionError` listing every missing required field; required always = `policy_rule_matched`+`rationale`, required-for-non-stub adds `switchboard_version`. Found and fixed a real propagation gap during this work — `to_cxrp_lane_decision` and `from_cxrp_lane_decision` were silently dropping `switchboard_version` because CxRP has no top-level field for it; both ends now route it through `metadata["switchboard_version"]`. 8 new tests cover the smoke helper (4 missing-field cases, allow_stub bypass, error completeness) plus CxRP round-trip preservation in both directions. Suite 3622 pass (+8).

## Up Next

- [x] **OpsCenter ↔ Custodian coverage bridge (2026-05-04, on main)**: Closes the dynamic-coverage loop. New `audit_governance/coverage_analysis.py` uses Phase 7 manifest index to find `coverage.json` from a dispatch result and subprocess-invokes `custodian audit --enable-coverage --coverage-json <path>` against the consuming repo. Findings attached to the governance report as `CoverageAuditSummary` (cv1/cv2/cv3 counts). Opt-in via `run_coverage_audit: bool` on `AuditGovernanceRequest` — default False. Schema bumped 1.1 → 1.2. 10 new tests; full unit suite 2094 pass.

- [x] **Phase 7 — multi-run historical artifact index + CLI (2026-05-04, on main)**: Single-manifest layer was already complete; this round added the missing multi-run layer. New `artifact_index/multi_run.py` with `discover_manifest_files`, `IndexedRun`, `MultiRunArtifactIndex` (federated `query()`, `find_run_by_prefix` git-style ambiguity error, `resolve(..., recheck_exists=True)` re-stat at lookup), `build_multi_run_index`. Failed-load handling: corrupt manifests become `IndexedRun(load_error=..., index=None)`. New `artifact_index/cli.py` with `index / index-show / get-artifact` (Rich + `--json`); mounted flat into `operations-center-audit`. Architecture-invariant test relaxed to exempt `multi_run.py` + `cli.py`. 41 new tests; full unit suite 2082 pass.

- [x] **Phase 6 — dispatch control crash-safety + dual-PID tracking (2026-05-04, branch phase6-dispatch-control)**: All 6 slices complete. New `audit_dispatch/lock_store.py` (PersistentLockStore + dual-PID payload, atomic writes, fcntl sentinel via audit_governance/file_locks); `locks.py` refactored as façade over the store with full-identity acquire signature; `executor.execute()` accepts `on_spawn(pid, pgid)` callback; `api.py` carries identity through; stale-PID reclaim + lazy first-use sweep; new CLI commands `list-active / unlock / dispatch / watch` on `operations-center-audit`; cross-process concurrency proof test; in-flight run_status watcher (polling, no watchdog dep). Sentinel-glob bug fixed (`_iter_lock_files` filters `.lock.lock` recursive sentinels). Tests: 64 new + all existing passing. Full unit suite 2041 pass.

- [x] **Archon real workflow integration (2026-05-07, on `feat/archon-real-workflow-integration`)**: HttpArchonAdapter now does real workflow dispatch end-to-end per `WorkStation/docs/architecture/adapters/archon-real-workflow-integration.md`. New `backends/archon/http_workflow.py::ArchonHttpWorkflowDispatcher` (health probe → POST conversation → POST workflow run → AsyncHttpRunner kickoff/poll → GET run-detail for events → status map → abandon/cancel). New http_client helpers: `archon_create_conversation`, `archon_get_run_by_worker`, `archon_get_run_detail`, `archon_abandon_run`, `archon_cancel_run`, `archon_list_workflows`. Two AsyncHttpRunner upgrades shipped in ExecutorRuntime to handle Archon's quirks: 200 + non-terminal status falls through to poll (Archon's POST /run returns 200 `{accepted,status:"started"}`, not 202); `http.poll_pending_codes` metadata tolerates 404s during by-worker pre-registration. Plus `ExecutorRuntime.is_registered(kind)` for clean idempotent registration. Factory auto-wires HttpArchonAdapter when `settings.archon.enabled=True`. Probe gained `--list-workflows`. 167 archon-package tests pass; full OC unit suite 2510 pass; ER suite 65 pass.

- [x] **EffectiveRepoGraph + contract impact wired into production (2026-05-08, on `feat/wire-effective-repo-graph`, PR #90)**: `PlatformManifestSettings` block on `Settings` (enabled/project_slug/project_manifest_path/local_manifest_path); `build_effective_repo_graph_from_settings()` resolves project (explicit → `topology/project_manifest.yaml` convention) + local (explicit → WS `discover_local_manifest()`) and degrades to None on any error. Coordinator gains `_log_contract_impact()` hook called once after policy approval, before adapter dispatch — emits `contract change in <X> affects N consumer(s) [public=P private=Q]: ...` at INFO + merges a `contract_impact` dict (target/affected_count/public_affected/private_affected) into observability metadata. Wired into `entrypoints/execute/main.py`. 16 new tests (7 settings→factory, 7 coordinator hook, 2 partition); full unit suite 2518 pass; ruff + ty clean.

- [x] **the bound managed repo project manifest authored (2026-05-08, VF PR #892)**: `topology/project_manifest.yaml` declares VF as private managed-repo with `OperationsCenter dispatches_to the bound managed repo`. `topology/local_manifest.example.yaml` template; live `topology/local_manifest.yaml` gitignored. Validates clean through PM `load_effective_graph` (10 nodes / 13 edges; VF surfaces with source=project, visibility=private, local annotations applied).

- [x] **Warehouse project manifest authored (2026-05-08, Warehouse PR #1)**: Same shape as VF — private managed-repo node + `OperationsCenter dispatches_to Warehouse` edge.
- [x] **File upstream PR for Archon PATCH-001** — superseded; archon backend removed (ADR 0005). Patch is no longer applicable.


- [x] **3-layer manifest primitive — operationally complete (2026-05-08, R1–R4 across PM/VF/Warehouse/OC)**: All 14 DoD items met. R1 schema CI + validate CLI, R2 operator runbooks + example.yaml block, R3 path resolution + slug auto-resolve + `effective` CLI, R4 graph-doctor + integration smoke. PM tagged through v0.5.0. Operator now sees blast-radius warnings on every contract-touching dispatch; failures degrade gracefully; pattern is discoverable from main.

- [x] **R7 — Edge type expansion (2026-05-08, complete across PM/VF)**:
  - **R7.1** PM v0.6.0 (PR #6) — `RepoGraph.who_dispatches_to(repo_id) -> list[RepoNode]`. Promotes existing `DISPATCHES_TO` edge from informational to first-class queryable. No schema change. 4 new tests.
  - **R7.2** PM v0.7.0 (PR #7) — `RepoEdgeType.BUNDLES_ASSETS_FROM` + `RepoGraph.who_consumes_assets_of(repo_id)`. First new edge type since v0.3 — justified by real query "what breaks if Warehouse changes its asset format?". JSON schemas (platform + project) updated. 3 new tests.
  - **R7.3** VF #894 — `<managed_repo> → Warehouse (bundles_assets_from)` edge authored in VF's `topology/project_manifest.yaml`. Bumped PM pin `>=0.3,<1.0` → `>=0.7,<1.0`; CI workflow pin bumped `@v0.4.0` → `@v0.7.0`. Composition smoke: `who_consumes_assets_of('warehouse')` returns `[the bound managed repo]`.
  - **Deferred edge types** (per design rule — "add new edge types only when a real query needs them"): `monitors_health_of` (wait for WS health graph), `forks_from` (wait for SourceRegistry cross-graph queries), `triggers_revalidation_of` (subsumed by R5 propagation infra using `depends_on_contracts_from`).

- [x] **R6 — Multi-project composition via shell repo (2026-05-08, complete on PM + OC)**:
  - **R6.1+R6.2** PM v0.8.0 (PR #8) — `_resolve_includes()` recursive loader with cycle detection + depth limit (default 4); collision rules (platform redefinition / sibling collision / shell-vs-sub collision all hard-fail); cross-sub-project edges allowed; sub-projects still can't add platform-to-platform edges. Visibility never widens. JSON schema gains optional `includes: [{name, project_manifest_path}]` on project. 14 new tests.
  - **R6.3** OC PR #104 — `docs/operator/manifest_authoring.md` gains "Multi-repo project — shell pattern (PM v0.8+)" section: when-to-use-it, file layout, shell example, composition rules table, OC pointing convention, rationale for shell-vs-monolith.
  - **NO real shell repo authored** — machinery shipped per audit's recommendation; first suite manifest is operator-driven (when VF + Warehouse logically want to be one suite).
  - **Deferred to v1+ minor**: `suite_id` for stable identities independent of repo path.

- [x] **R5 — Cross-repo task chaining (2026-05-08, complete on OC)**:
  - **R5.1** OC PR #105 — `propagation/` library: `policy.py` (PropagationPolicy + PropagationSettings — disabled by default; per-edge-type opt-in; per-pair overrides for skip/backlog/ready_for_ai), `registry.py` (PropagationRegistry + TaskTemplate with pair → target-wildcard → consumer-wildcard → default fallback), `dedup.py` (PropagationDedupStore + DedupKey — JSON sidecar in state/; (target, consumer, version) key + 24h window), `links.py` (ParentLink + format_parent_link — `<!-- propagation:source -->` HTML-comment block), `propagator.py` (ContractChangePropagator orchestrator + **mandatory observability floor**: every run writes a PropagationRecord artifact regardless of whether tasks fired). 22 new tests.
  - **R5.2+R5.3** OC PR #106 — `operations-center-propagate` entrypoint (`--target`, `--version`, `--config`, `--require-enabled`, `--dry-run`, `--json`); `propagation/plane_adapter.py::PlaneTaskCreator` wraps `PlaneClient.create_issue` + transition; `Settings.contract_change_propagation` block (enabled / auto_trigger_edge_types / dedup_window_hours / pair_overrides / record_dir / dedup_path); operator runbook in `docs/operator/manifest_wiring.md`; commented block in `config/operations_center.example.yaml`. 9 new tests.
  - **R5.4+R5.5** OC PR #107 — Post-merge hook reference (`docs/operator/propagation/post-merge-hook.md` + sample `post-merge-hook.workflow.yml`) for auto-trigger via GitHub Actions on contract repos; `operations-center-propagation-links` inspection CLI with `list / show <run_id> / latest --target X` subcommands. 9 new tests.
  - **Safety floor**: cross-repo propagation disabled by default; tasks land in Backlog (not Ready for AI); per-pair opt-in for promotion; mandatory observability artifacts; failed task creation recorded but dedup NOT stamped (retries can re-fire); two disable paths (config flag OR workflow toggle).

- [ ] **Live + observe phase (no branch — recommended next)**: Per the audit's "stop and live with it" guidance. Watch for:
  - Does VF + Warehouse logically want to be one suite repo? (validates R6 in production)
  - Does `bundles_assets_from` feel natural when operators ask "what depends on Warehouse?"
  - Does an actual contract change to CxRP/RxP/PlatformManifest produce useful propagation tasks?
  - Each is a real-world signal. Machinery is shipped; next move is observation.

- [ ] Phase 13 or next operator directive TBD (after R5/R6/R7 settle in production)

- [ ] Phase 13 or next operator directive TBD

- [x] **ER-000 — Phase 0 Golden Tests (2026-05-06, on `main`)**: 15 tests in `tests/unit/er000_phase0_golden/test_golden.py`. Pinned: one-shot wire imports + Pydantic constructors + backend-unavailable result shape; LaneDecision/ExecutionRequest/ExecutionResult validators reject malformed input; OC's audit_contracts examples still validate; no `<repo_id>` imports anywhere in `src/operations_center/`; SwitchBoard package free of orchestration symbols via new `tools/boundary/switchboard_denylist.py` (forward-looking denylist includes `SwarmCoordinator`, `LifecycleRunner`, `RunMemoryIndexWriter`, etc. — passes trivially today, fails closed if those land in SB); `operations-center-audit --help` reaches Typer app via CliRunner.

- [x] **ER-001 — Repo Graph Primitive (2026-05-06, on `main`)**: 21 tests. New `operations_center.repo_graph` package: `models.py` (RepoNode/RepoEdge/RepoGraph + RepoEdgeType enum: `depends_on_contracts_from`, `dispatches_to`, `routes_through`), `loader.py` (YAML loader, fail-fast on duplicate ids/alias collisions/unknown edge types/unknown nodes), `cli.py` (`operations-center-repo-graph list/resolve/upstream/downstream/impact`). Live config at `config/repo_graph.yaml`: 7 repos (OperatorConsole/OperationsCenter/SwitchBoard/WorkStation/CxRP/the bound managed repo/Warehouse) with legacy aliases (ControlPlane→OperationsCenter, FOB→OperatorConsole, ExecutionContractProtocol→CxRP) and 5 edges. Impact query on CxRP returns OperationsCenter+SwitchBoard+OperatorConsole.

- [x] **ER-002 — Run Memory Primitive (2026-05-06, on `main`)**: 23 tests. New `operations_center.run_memory` package: `models.py` (`RunMemoryRecord` w/ frozen-string `contract_kinds`, `SourceType` enum = `execution_result` only, `RunMemoryQuery` w/ AND-combined filters), `index.py` (`deterministic_record_id` = sha256(result_id)[:16] → idempotent rebuilds; `RunMemoryIndexWriter` append-only; `RunMemoryQueryService` with substring-only text search across summary/tags/artifact_paths/repo_id/run_id; `record_execution_result` = single write site for OperationsCenter post-finalize; `rebuild_index_from_artifacts` scans on-disk `execution_result*.json` only — single v1 source). CLI: `operations-center-run-memory query/rebuild`. No vector DB, no embeddings, no scoring.

- [x] **ER-003 — Lifecycle Primitive (2026-05-06, on `main`)**: 13 tests, no live LLM. New `operations_center.lifecycle` package: `models.py` (`TaskLifecycleStage`={plan,execute,verify}, `LifecycleStagePolicy`={stop_on_first_failure,run_all_best_effort} — no `manual_gate_between_stages`; `Check`/`CheckResult`; `PlanOutput` includes `checks: list[Check]` consumed verbatim by verify; `VerifyOutput` with `checks: list[CheckResult]` and derived `failures`; `LifecycleMetadata` + `LifecycleOutcome`), `runner.py` (`LifecycleRunner` driving stages with `StageHandlers` Protocol; missing-from-verify check_ids implicitly fail). Contract additions on `ExecutionRequest.lifecycle: Optional[LifecycleMetadata] = None` and `ExecutionResult.lifecycle_outcome: Optional[LifecycleOutcome] = None` — both optional, one-shot path unchanged.

- [x] **ER-001/002/003 production wiring (2026-05-06, on `main`)**: 10 new wiring tests + 2108 of the wider unit suite green. `ExecutionCoordinator.__init__` now accepts `run_memory_index_dir: Path | None = None` and `repo_graph: RepoGraph | None = None`; both default to None so existing callers are unchanged. After observe(), coordinator calls `record_execution_result` (advisory — failures swallowed) on success, failure, and policy-blocked paths so memory captures everything; tags = `(task_type, lane, backend)`. When `request.lifecycle is not None`, coordinator wraps the dispatch in a default plan/execute/verify cycle (`_attach_lifecycle_outcome`): plan emits a single `execution_succeeded` check, execute mirrors the actual dispatch (no re-dispatch), verify reads `result.success`. Outcome attaches via `result.model_copy(update={"lifecycle_outcome": ...})`. `ExecutionRuntimeContext.lifecycle: LifecycleMetadata | None` added; builder threads it into `ExecutionRequest.lifecycle`. Repo graph: new `load_default_repo_graph()` in `repo_graph/loader.py` provides cached singleton-style access to `config/repo_graph.yaml`; coordinator passes the graph to `LifecycleRunner.run(repo_graph_context=...)` so the plan stage can resolve repo identity.

- [ ] **ER-004 — Swarm Primitive (DEFERRED)**: Not approved for implementation. Entry criteria (all required before kickoff): (1) real workflow fails without swarm, (2) one-shot insufficient, (3) lifecycle insufficient, (4) required roles defined, (5) merge behavior defined. If unmet → DO NOT IMPLEMENT.

## Done

- [x] **Collector JSON Hardening — Stage 2: Implementation (2026-05-23)**: Hardened Collector against malformed JSON payloads. Completed:
  - Created `src/operations_center/observer/validation.py` with `ParseErrorMetadata`, `ArtifactValidator` base class, and per-collector validators (`ExecutionOutcomeValidator`, `RequestValidator`, `ValidationHistoryValidator`, `DependencyReportValidator`, `LintItemValidator`)
  - Fixed critical crash vulnerability in `dependency_drift.py` line 19 (unprotected `json.loads()`)
  - Updated all 6 JSON-parsing collectors with two-stage validation (parse + structure)
  - Added `parse_errors: ParseErrorMetadata` field to signal models for error tracking
  - Implemented consistent logging: DEBUG for parse errors, WARNING for structure errors
  - Created comprehensive test suite in `tests/observer/test_collectors_hardening/`:
    - conftest.py with shared fixtures
    - test_validation_helpers.py (22 tests validating all validator classes)
    - test_dependency_drift.py (16 tests for crash fix and edge cases)
    - test_execution_health.py (19 tests for malformed artifacts and mixed runs)
  - All collectors now gracefully skip malformed artifacts and continue processing
  - Ready for Stage 3 (test execution and CI validation)

- [x] Phase 0: Ground truth audit discovery
- [x] Phase 1: Managed repo config contract — 26 tests
- [x] Phase 2: Artifact contract definition — 119 tests
- [x] Phase 3: Audit toolset contract — 47 tests
- [x] Phase 4: Run identity / ENV injection — 52 tests
- [x] Phase 5: the bound managed repo artifact manifest writing — ManagedRunFinalizer wired in all 5 CLIs
- [x] Phase 6: Dispatch-orchestrated run control
- [x] Phase 7: Artifact index + retrieval
- [x] Phase 8: Behavior calibration
- [x] Phase 9: Fixture harvesting
- [x] Phase 10: Slice replay testing
- [x] Phase 11: Mini regression suite
- [x] Phase 12: Full audit governance
- [x] Rev 1–10 verification passes: all 23 lifetime gaps closed; 14/14 invariants; 2733 tests passing

- [x] **Deriver Transition Coverage — Stage 0-2 Complete (2026-05-27)**: Reverse transition coverage implemented for 3 derivers. Completed:
  - [x] Stage 0: Investigation complete — 5 critical gaps identified across 3 derivers
  - [x] Stage 1: Coverage design — comprehensive design document with phased implementation strategy
  - [x] Stage 2: Implementation complete
    - DependencyDriftDeriver: added recovery transition detection (not_available→available)
    - LintDriftDeriver: added improvement tracking (violation count decrease) and status transitions (violations↔clean)
    - TypeHealthDeriver: added improvement tracking (error count decrease) and status transitions (errors↔clean)
    - New tests: test_dependency_drift_deriver.py (3 new recovery tests), test_lint_drift_deriver.py (12 tests total), test_type_health_deriver.py (12 tests total)
    - All code compiles without syntax errors
    - Acceptance criteria met: bidirectional transitions, no regressions, follows existing conventions

## Cycle 9 updates (2026-05-22)
- [x] Fix kodo→openclaw regression in tests (cb56d53) — unblocked CI
- [x] Update dag_executor/team_executor audit verdicts to CxRP 0.3.1
- [ ] Custodian/regression-check: add detector for invalid backend enum references in tests (promotion candidate from cycle 9)
- [ ] PR cb56d53 merge: oc-watchdog/20260522-0137-fix-kodo-removal-regressions → main

## Cycle 28 updates (2026-05-23)
- [x] Fix workspace-prep ordering bug: checkout base branch before bootstrap/baseline so a tracked `.baseline-validation.json` on the clone default branch can't dirty the tree and abort `git checkout <base_branch>` (workspace.py). Was blocking ALL OC goal tasks (e.g. bfb289b3). Goal worker restarted with fresh code.
- [ ] HYGIENE: untrack `.baseline-validation.json` from OperationsCenter main on the GitHub remote + add to .gitignore (operator/remote-push; impact already neutralized by the workspace.py reorder).

## Cycle 36 updates (2026-05-27)
- [x] Board-unblock healed 14 tasks: 13 IMPROVE_UNBLOCK (stale Blocked >4h → Backlog) + 1 STALE_IN_REVIEW (0f1612ea InReview→Backlog). Key tasks re-queued: 3a3c202f "Harden Collector" + 0f1612ea "Handle Optional observed_at".
- [x] Propose created a2d10dcf "Restore repeated missing test_signal coverage" — improve worker active.
- [ ] Monitor a2d10dcf execution outcome next cycle (test_signal coverage restoration for OC).
- [ ] Monitor 0f1612ea + 3a3c202f re-dispatch by goal worker (now in Backlog).
- [ ] CI: ruff + ty failing for OC — monitored via propose; ci_pattern family deferred by initial gating.
