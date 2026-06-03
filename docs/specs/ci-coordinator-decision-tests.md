---
campaign_id: a94c7e1f-6b3d-4e8a-bf02-9d1c5a4f72e3
slug: ci-coordinator-decision-tests
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - execution/ci_coordinator
  - tests/unit/execution
status: active
created_at: 2026-05-22T14:30:00Z
---

## Overview

The `CiCoordinator` (442 lines, `src/operations_center/execution/ci_coordinator.py`) orchestrates the multi-attempt refinement loop for CI-enabled proposals. It was recently wired into the board_worker call-site (commit `900fe8d`) and implements a five-state state machine (NOT_STARTED → IN_PROGRESS → ACCEPTED / BUDGET_EXHAUSTED / ABANDONED / ESCALATED) with **zero test coverage**. The `_decide()` method, `_status_for_decision()`, `_strategy_for_attempt()`, and `_append_attempt()` are pure functions that can be tested without any I/O or mocking.

## Goals

1. **Scaffold test file and factory helpers** — Create `tests/unit/execution/test_ci_coordinator.py` with factory functions that build minimal `EvaluationScore`, `ContinuousImprovementSpec`, and `ImprovementLineage` instances. Confirm the test file is discovered by the existing pytest configuration. Add a lightweight `FakeExecutionCallable` that records its calls and returns configurable `(run_id, changed_files, success)` tuples.

2. **Cover `_decide()` decision branches exhaustively** — Write parametrized tests for each branch of the decision function:
   - `guardrails_failed` non-empty → ABANDON.
   - `GUARDRAIL_VIOLATED` outcome → ABANDON.
   - `IMPROVED` outcome → ACCEPT.
   - `NEUTRAL` + `accept_on_neutral=True` → ACCEPT; `accept_on_neutral=False` + budget remaining → RETRY.
   - `INCONCLUSIVE` + budget remaining → ESCALATE; `INCONCLUSIVE` + budget exhausted → ESCALATE.
   - `REGRESSED` + budget remaining → RETRY; `REGRESSED` + budget exhausted (accounting for `failure_penalty`) → ABANDON.

3. **Cover lineage mutation and strategy selection** — Test `_append_attempt()` produces correct frozen `ImprovementLineage` with updated status, attempt list, and `accepted_attempt_number`. Test `_status_for_decision()` maps all four `RefinementDecision` values. Test `_strategy_for_attempt()` returns original strategy on first attempt and honours `vary_strategy_on_retry`.

4. **Cover `run()` integration with fake callable** — Write 3–4 integration-level tests that call `CiCoordinator.run()` with an in-memory `CiStore`, fake evaluator, and `FakeExecutionCallable`:
   - Single-attempt accept (IMPROVED on attempt 1).
   - Multi-attempt retry then accept (REGRESSED → IMPROVED).
   - Budget exhaustion (all attempts REGRESSED, final decision ABANDON).
   - Guardrail violation on first attempt (immediate ABANDON, single attempt recorded).

## Constraints

- Tests targeting `_decide`, `_status_for_decision`, `_strategy_for_attempt`, and `_append_attempt` must call these methods directly — no subprocess spawning, no filesystem I/O.
- For `run()` integration tests, use an in-memory `CiStore` (or mock) and a stub `CiEvaluator` that returns pre-configured `EvaluationScore` sequences. Do not run real validation commands.
- Follow existing test layout under `tests/unit/execution/`. Use `pytest.mark.parametrize` for decision branch variants.
- Do not modify `ci_coordinator.py` itself — this campaign is test-only.
- Use `datetime(2026, 5, 22, tzinfo=UTC)` as the reference time where needed to keep assertions deterministic.

## Success Criteria

- `pytest tests/unit/execution/test_ci_coordinator.py -v` passes with **≥ 20 test cases**.
- Every branch of `_decide()` has at least one positive test (7 branches × 1 minimum = 7 tests).
- `_status_for_decision` covers all 4 `RefinementDecision` → `RefinementStatus` mappings.
- `_strategy_for_attempt` has tests for first-attempt, retry-with-vary, and retry-without-vary.
- At least 3 `run()` integration tests exercise multi-attempt loops end-to-end with the fake callable.
- No test imports `PlaneClient` or performs real filesystem writes beyond `tmp_path`.