---
campaign_id: 2477ef44-590b-4b1c-99f4-83db51f398f6
slug: admin-api-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - SwitchBoard
area_keywords:
  - api/routes_admin
  - services/decision_logger
  - test/integration
  - test/unit
status: active
created_at: 2026-05-18T00:00:00Z
---

## Overview

The SwitchBoard admin API exposes 8 HTTP endpoints for decision observation and adaptive-policy operator controls, all fully implemented but with zero HTTP-level test coverage. The `DecisionLogger` service backing these endpoints has only 3 unit tests despite rich ring-buffer, JSONL persistence, and summarization functionality. This campaign adds integration tests for all admin routes and fills DecisionLogger unit-test gaps.

## Goals

1. **Add integration tests for decision observation endpoints** — Using `TestClient(create_app())`, test `GET /admin/decisions/recent`, `GET /admin/decisions/{request_id}`, and `GET /admin/summary`. Cover happy-path responses (status 200, response schema matches Pydantic models), the 404 case for nonexistent `request_id`, and `n` parameter bounds validation. Target file: `test/integration/test_admin_decisions.py`.

2. **Add integration tests for adaptive-policy control endpoints** — Test all 5 adaptive endpoints: `GET /admin/adaptive`, `POST /admin/adaptive/enable`, `POST /admin/adaptive/disable`, `POST /admin/adaptive/reset`, `POST /admin/adaptive/refresh`. Verify state transitions (enable → disable → reset → refresh → enable), response schema correctness (`AdaptiveStateResponse`), idempotent no-op behavior (e.g., enable when already enabled), and `n` parameter bounds on `/refresh`. Target file: `test/integration/test_admin_adaptive.py`.

3. **Expand DecisionLogger unit tests** — Add tests for: ring-buffer overflow at 1000 records (oldest dropped), `last_n()` boundary cases (`n=0`, `n` exceeding buffer size), JSONL sink append and read-back, and `summarize()` latency percentile accuracy with known data. Target file: `test/unit/test_decision_logger.py` (extend existing).

4. **Add end-to-end adaptive refresh loop test** — A single integration test that exercises the full signal pipeline: insert decision records via `/route`, call `POST /admin/adaptive/refresh`, then verify `GET /admin/adaptive` returns the expected demote/promote state computed from those records. This validates the closed loop from decisions → signals → adjustments → operator-visible state. Target file: `test/integration/test_adaptive_loop_e2e.py`.

## Constraints

- **Test-only campaign** — no production code modifications unless a bug is discovered blocking test creation (document any such fix as a separate commit).
- **Use existing test infrastructure** — follow the patterns in `test/integration/test_canonical_routing_records.py` for TestClient setup and fixture conventions.
- **Pydantic response validation** — assert response JSON against the existing Pydantic models (`DecisionRecordResponse`, `AdaptiveStateResponse`, `SummaryResponse`) rather than raw dict key checks.
- **No mocking of the signal pipeline** — integration tests should exercise real `SignalAggregator` → `AdjustmentEngine` → `AdjustmentStore` flow; only external I/O (file system for JSONL) may use tmp_path fixtures.
- **Avoid overlap** — this campaign targets `api/routes_admin` and `services/decision_logger` only; do not add tests for `lane/engine.py`, `adjustment_store.py`, or `signal_aggregator.py` (already well-covered).

## Success Criteria

- All 8 admin endpoints have at least one happy-path and one error/edge-case integration test.
- `DecisionLogger` unit tests cover ring-buffer overflow, `last_n` boundaries, JSONL persistence, and summarize accuracy (≥10 new test methods).
- The e2e adaptive refresh loop test passes with deterministic test data that triggers at least one demote and one promote action.
- All new tests pass in CI with no flakiness on 3 consecutive runs.
- Zero modifications to production source files.
