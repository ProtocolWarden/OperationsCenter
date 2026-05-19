---
campaign_id: 61a61b5d-b0f0-424d-a609-ac845d5d3b26
slug: infra-adapter-health-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - adapters
  - backend_health
  - tests
status: active
created_at: 2026-05-18T00:00:00Z
---

## Overview

The `GitHubPRClient` adapter (14 public methods) and `BackendHealthRegistry` state machine are critical-path infrastructure with zero unit test coverage. This campaign adds deterministic, fast unit tests for both modules, using mocked HTTP clients and frozen timestamps to verify contracts that the autonomous loop and PR workflow depend on daily.

## Goals

1. **GitHubPRClient unit tests** — Add tests for `github_pr.py` covering: PR CRUD (`create_pr`, `get_pr`, `merge_pr`, `close_pr`), file listing (`list_pr_files`), check-run queries (`get_failed_checks`, `get_check_runs`), comment operations (`post_comment`, `post_or_update_comment`), and label management. Mock `httpx.Client` responses; assert correct URL construction, header propagation, and rate-limit retry behaviour (the `_request` wrapper). Target: ≥ 12 test cases in `tests/test_github_pr_client.py`.

2. **GitHubPRClient error-path tests** — Verify exception-swallowing semantics: network timeouts return `None`/empty-list (not raise), 404 on missing PR returns `None`, 422 on merge conflict is handled gracefully. Confirm logging side-effects via `caplog`. Target: ≥ 5 additional negative-path test cases.

3. **BackendHealthRegistry state-machine tests** — Test the full transition graph in `backend_health/registry.py`: healthy → record_failure (below threshold) → still healthy; healthy → N failures → UNSTABLE at threshold 2; UNSTABLE → threshold 5 → UNAVAILABLE; SIGKILL detection → pressure-reduction strategy; `record_success` resets to HEALTHY; cooldown expiry logic with frozen `time.time()`. Target: ≥ 8 test cases in `tests/test_backend_health_registry.py`.

4. **BackendHealthRegistry model round-trip tests** — Validate `BackendHealthRecord`, `BackendHealthState`, and `RecoveryStrategy` frozen dataclass contracts: construction, equality, serialisation if applicable. Target: ≥ 3 test cases alongside the registry tests.

## Constraints

- All tests must be pure unit tests — no network, no filesystem, no subprocesses.
- Mock HTTP via `httpx`-compatible fakes or `unittest.mock.patch`; follow the pattern in `tests/test_escalation.py` and `tests/unit/backends/direct_local/test_adapter.py`.
- Freeze time with `unittest.mock.patch("time.time", return_value=...)` for cooldown assertions — do not add `freezegun` as a dependency.
- Do not modify production source files unless a minor refactor (e.g., extracting a constant) materially improves testability. Any such change must be called out in the PR description.
- Tests go in `tests/` at top level (matching existing layout), not inside `src/`.

## Success Criteria

- `pytest tests/test_github_pr_client.py tests/test_backend_health_registry.py` passes with ≥ 28 total test cases.
- Every public method of `GitHubPRClient` is exercised at least once (happy path).
- Every state transition edge in `BackendHealthRegistry` is covered (healthy ↔ unstable ↔ unavailable, plus reset and cooldown).
- No new dependencies added to `pyproject.toml`.
- Full test suite (`pytest`) still green with no regressions.
