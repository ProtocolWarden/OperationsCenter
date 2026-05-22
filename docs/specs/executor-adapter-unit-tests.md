---
campaign_id: f3a7c1e2-9b04-4d8e-a6f1-8e2d3c5a9b17
slug: executor-adapter-unit-tests
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
  - backends/factory
status: cancelled
created_at: 2026-05-19T00:00:00Z
---

## Overview

The three P5 executor backend adapters (`TeamExecutorBackendAdapter`, `DAGExecutorBackendAdapter`, `CritiqueExecutorBackendAdapter`) were added in the recent ADR 0005 cutover but ship with zero dedicated unit tests. Only the factory wiring has a test. This campaign adds focused unit-test suites for each adapter's `execute()` path, error handling, and result mapping.

## Goals

1. **Add `tests/unit/backends/test_team_executor_adapter.py`** — Cover `TeamExecutorBackendAdapter.execute()`: successful run mapping via `_rxp_to_result`, timeout/crash `_error_result` path, and `ImportError` guard when the `team_executor` package is missing. Mock the underlying `TeamExecutorRunner`.

2. **Add `tests/unit/backends/test_dag_executor_adapter.py`** — Cover `DAGExecutorBackendAdapter.execute()`: successful dict-based result via `_dict_to_result`, workflow.yaml resolution with single-agent fallback branch, and the `_error_result` path. Mock `DAGExecutorRunner`.

3. **Add `tests/unit/backends/test_critique_executor_adapter.py`** — Cover `CritiqueExecutorBackendAdapter.execute()`: successful reflexion loop via `_rxp_to_result`, max-rounds boundary, and error path. Mock `CritiqueExecutorRunner`.

4. **Extend `tests/unit/backends/test_factory.py`** — Add edge-case tests: factory behaviour when one executor package raises `ImportError` at import time; settings with non-default values propagate correctly to adapter constructors.

## Constraints

- All new tests use `pytest` with `monkeypatch`/`unittest.mock` — no real executor packages required at test time.
- Follow the existing `tests/unit/backends/` directory layout and naming convention.
- Do not modify any production code in `src/`; this is a test-only campaign.
- Each adapter test file must be independently runnable (`pytest tests/unit/backends/test_<name>.py`).
- Avoid duplicating the recovery-subsystem test patterns that were previously cancelled.

## Success Criteria

- `pytest tests/unit/backends/` passes with all new tests green.
- Each adapter file has ≥ 3 test cases covering: happy path, error/exception path, and an edge case (ImportError, fallback branch, or boundary condition).
- No changes to files outside `tests/`.
- `ruff check` reports zero new violations in the test files.