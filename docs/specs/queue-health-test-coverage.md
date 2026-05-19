---
campaign_id: 7ce1dc94-c8ba-42f7-b652-3c14987df6c0
slug: queue-health-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - queue_healing
  - backend_health
status: active
created_at: 2026-05-18T21:00:00Z
---

## Overview

The `queue_healing` and `backend_health` packages are deterministic state-machine modules that power the autonomous watchdog loop's self-healing and backend availability decisions. Both ship production code with **zero unit tests**, making them the largest untested operational surface in the repository. This campaign closes both gaps with focused, exhaustive unit test suites.

## Goals

1. **Add `tests/unit/queue_healing/` covering all 6 decision branches in `QueueHealingEngine.decide()`**: non-blocked passthrough, recovery-attempt budget exhaustion (ESCALATE), retry-count budget exhaustion (ESCALATE), duplicate-suppression deadlock requeue (BLOCKED_TO_READY_FOR_AI), stale-blocked requeue (BLOCKED_TO_BACKLOG), and no-rule-matched fallback (NONE). Include edge cases: naive-datetime `updated_at` handling, exact-threshold boundaries for `max_retry_count` / `max_recovery_attempts`, and custom engine configuration overrides.

2. **Add `tests/unit/backend_health/` covering `BackendHealthRegistry` state transitions**: `record_success` (resets to HEALTHY from every prior state), `record_failure` progression (DEGRADED → UNSTABLE → UNAVAILABLE at threshold boundaries), SIGKILL signal extraction and cooldown application, `start_recovery` (increments attempt count, sets RECOVERING), `mark_operator_blocked` (OPERATOR_BLOCKED with reason). Also cover the helper functions: `_failure_signature` for each branch (signal, category, status, unknown), `_extract_signal` for SIGKILL/SIGTERM/SIGNAL variants, and `_extract_exit_code` for valid/missing/malformed input.

3. **Verify model constructors and enum completeness**: `QueueTransition` values match all branches the engine can emit; `BackendHealthState` and `RecoveryStrategy` values match all states the registry can assign; `BackendHealthRecord.with_update` preserves frozen-dataclass semantics (returns new instance, original unchanged).

4. **Confirm suite integration**: all new tests pass alongside the existing ~3600-test suite with no import conflicts, no fixture pollution, and ruff/ty clean.

## Constraints

- Tests are pure unit tests — no I/O, no subprocess, no network, no mocking of external services.
- Use `datetime(…, tzinfo=UTC)` for deterministic time control via the `now` parameter already exposed on both engines.
- Do not modify production code. If a bug is discovered, document it as a comment in the test with `# BUG:` prefix and write the test to assert current (buggy) behavior, so it fails-closed on fix.
- Follow existing test layout conventions: one `test_*.py` file per module under test, `conftest.py` only if shared fixtures are needed across >2 files.
- Do not add dependencies.

## Success Criteria

- `tests/unit/queue_healing/` exists with ≥12 test cases covering every `QueueTransition` variant the engine can produce, including boundary conditions.
- `tests/unit/backend_health/` exists with ≥15 test cases covering every `BackendHealthState` the registry can assign, both helper functions, and the `with_update` immutability invariant.
- `pytest tests/unit/queue_healing tests/unit/backend_health` passes with 0 failures, 0 errors.
- `ruff check` and `ty check` report no new diagnostics in test files.
- Full suite (`pytest tests/`) passes with no regressions.