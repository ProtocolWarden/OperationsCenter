---
campaign_id: e6c2f731-8091-4dbe-beb6-5a741f6b4d40
slug: p5-adapter-and-board-unblock-tests
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - backends/team_executor
  - backends/dag_executor
  - backends/critique_executor
  - entrypoints/maintenance
status: active
created_at: 2026-05-19T00:00:00Z
---

## Overview

The P5 executor backend adapters (TeamExecutor, DAGExecutor, CritiqueExecutor) and the board-unblock maintenance subsystem are both freshly written, recently bug-fixed, and have zero dedicated unit tests. This campaign adds focused unit-test suites for these two areas to prevent regressions on code that has already shipped multiple hotfixes.

## Goals

1. **Add unit tests for each P5 executor adapter** — Create `tests/unit/backends/test_team_executor_adapter.py`, `test_dag_executor_adapter.py`, and `test_critique_executor_adapter.py`. Each suite must cover: happy-path `execute()` returning a mapped `ExecutionResult`, settings propagation (e.g. `team_name`, `topology`, `artifacts_dir`), and error/timeout handling when the underlying runner raises. Mock the runner layer; do not invoke real executors.

2. **Add unit tests for `board_unblock._apply_rules()`** — Create `tests/unit/entrypoints/maintenance/test_board_unblock.py`. Cover all six rules (DEAD_REMEDIATION_CANCEL, INVESTIGATE_DEPRIORITISE, IMPROVE_UNBLOCK, SELF_MODIFY_REQUEUE, STALE_IN_REVIEW, STALE_RUNNING_REQUEUE) with synthetic task dicts. Verify label-matching helpers (`_label_value`, `_has_label`, `_has_label_prefix`) handle whitespace-in-value labels correctly — the exact bug fixed in commit `e4c18ab`.

3. **Extend the existing factory test** — In `tests/unit/backends/test_factory.py`, add assertions that each adapter returned by `CanonicalBackendRegistry.from_settings()` satisfies the `CanonicalBackendAdapter` protocol (i.e. has a callable `execute` accepting `ExecutionRequest` and returning `ExecutionResult`).

4. **Add a parametrised staleness-threshold test for Rules 5 & 6** — Verify that tasks exactly at, just under, and just over the `--stale-blocked-hours` / `--stale-running-hours` thresholds are handled correctly. These rules were added in the last 24 hours and have no coverage.

## Constraints

- All new tests must be pure unit tests — no network, no Docker, no Plane API calls. Use `unittest.mock` or `pytest` monkeypatch exclusively.
- Do not modify production source code; only add or extend test files.
- Follow the existing test layout: `tests/unit/<mirror-of-src-path>/test_<module>.py`.
- Keep each test file under 250 lines to stay within a single kodo run budget.
- Do not re-propose recovery-subsystem or QueueHealingEngine tests (cancelled).

## Success Criteria

- `pytest tests/unit/backends/test_team_executor_adapter.py tests/unit/backends/test_dag_executor_adapter.py tests/unit/backends/test_critique_executor_adapter.py` passes with ≥ 5 tests per file.
- `pytest tests/unit/entrypoints/maintenance/test_board_unblock.py` passes with ≥ 8 tests covering all six rules plus label-parsing edge cases.
- `pytest tests/unit/backends/test_factory.py` passes with the new protocol-conformance assertions.
- No existing tests broken by the additions (`pytest tests/` exits 0).