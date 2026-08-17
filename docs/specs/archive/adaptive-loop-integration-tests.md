---
campaign_id: b7e3f1a4-9c82-4d6f-a3b1-e5f7d8c2a910
slug: adaptive-loop-integration-tests
phases:
  - implement
  - test
  - improve
repos:
  - SwitchBoard
area_keywords:
  - services
  - test/integration
  - api/routes_admin
  - lane/engine
status: cancelled
created_at: 2026-05-18T00:00:00Z
---

## Overview

SwitchBoard's adaptive feedback loop — where DecisionLogger accumulates routing outcomes, SignalAggregator derives per-lane health signals, AdjustmentEngine produces demote/promote/neutral actions, AdjustmentStore caches them, and LaneSelector consumes them to reroute traffic — is fully wired in `app.py` but has **zero integration tests** covering the closed loop. Each component has solid unit tests in isolation, but nothing verifies that error-heavy traffic for a lane actually causes the next `/route` call to avoid it, or that the `/admin/adaptive/*` operator endpoints correctly influence routing behavior.

## Goals

1. **Add a closed-loop adaptive routing integration test** (`test/integration/test_adaptive_feedback_loop.py`): Spin up the app via `TestClient`, send N requests that all succeed for lane A, then send enough error-status outcomes to cross the demote threshold for lane A, trigger a refresh, and assert the next `/route` call selects the fallback lane instead. This proves the full DecisionLogger → SignalAggregator → AdjustmentEngine → AdjustmentStore → LaneSelector chain works end-to-end.

2. **Add admin adaptive endpoint integration tests** (`test/integration/test_admin_adaptive_endpoints.py`): Cover the four `/admin/adaptive/*` endpoints (GET state, POST enable, POST disable, POST reset, POST refresh) through `TestClient`. Verify that `disable` causes the adjustment_query to return `None` (no demotion even when signals warrant it), that `reset` clears all cached adjustments, and that `refresh` with a window parameter recomputes from the decision log.

3. **Add an edge-case test for adjustment_query exception resilience at the app level**: Verify via monkeypatch that if `AdjustmentStore.get_adjustment` raises, the `/route` endpoint still returns a valid decision (graceful degradation) rather than a 500. The unit test covers `LaneSelector._is_demoted` catching exceptions, but nothing proves the app-level wiring preserves this safety.

## Constraints

- All new tests go under `test/integration/` alongside the existing `test_canonical_routing_records.py`.
- Use `fastapi.testclient.TestClient` with the real `create_app()` factory — no mocked app instances.
- Use `monkeypatch` + `tmp_path` for decision log paths; do not write to real filesystem locations.
- Do not modify any production source code — this campaign is test-only.
- Reuse the `_proposal()` fixture pattern already established in `test_canonical_routing_records.py`.
- Clear `load_settings.cache_clear()` in setup/teardown to avoid cross-test contamination.
- The existing decision-outcome recording mechanism writes status via the JSONL sink; for the closed-loop test, inject error records directly into `app.state.decision_logger` after the initial route call to simulate backend failures, since SwitchBoard doesn't execute backends.

## Success Criteria

- `pytest test/integration/` passes with all new tests green.
- At least one test proves: "lane X receives high error traffic → next route avoids lane X."
- At least one test proves: "operator disables adaptive → demoted lane is no longer skipped."
- At least one test proves: "operator resets adaptive → previously demoted lane is selectable again."
- No existing tests are broken or modified.
- `ruff check src/ test/` reports no new violations.