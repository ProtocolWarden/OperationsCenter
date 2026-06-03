---
campaign_id: 1502db67-3ea2-4de5-9f53-82fca0ffaebc
slug: board-unblock-rule-tests
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - entrypoints/maintenance
  - board_unblock
  - tests/unit/entrypoints
status: active
created_at: 2026-05-22T00:00:00Z
---

## Overview

The `board_unblock.py` module (495 lines, 8 decision rules) is the autonomous board-hygiene engine that resolves stuck tasks without operator intervention. It has been actively extended — Rules 7 and 8 were added in the last two commits — yet has **zero test coverage**. The core `_apply_rules` function is a pure function (issues in → actions out) making it straightforward to unit-test exhaustively.

## Goals

1. **Scaffold test file and helpers** — Create `tests/unit/entrypoints/test_board_unblock.py` with a factory function that builds minimal Plane-style issue dicts (id, name, state, labels, updated_at). Confirm the test file is discovered by the existing pytest configuration.

2. **Cover Rules 1–4 (core unblock logic)** — Write parametrized tests for each rule:
   - Rule 1: dead-remediation label → Cancelled; SIGKILL + retry≥3 → Cancelled; SIGKILL + retry<3 → no action.
   - Rule 2: investigate in R4AI → Backlog; investigate in other states → no action.
   - Rule 3: improve/goal Blocked with terminal blocker → Backlog; stale Blocked → Backlog; fresh Blocked → no action.
   - Rule 4: self-modify:approved Blocked → R4AI; skip when SIGKILL; skip when exit-code:0 no signal; skip when low memory.

3. **Cover Rules 5–8 (queue hygiene and recent additions)** — Write parametrized tests for each rule:
   - Rule 5: stale In Review → Backlog; fresh In Review → no action.
   - Rule 6: stale Running → R4AI; fresh Running → no action.
   - Rule 7: goal+autonomy+improve-suggestion in Backlog with terminal parent → R4AI; non-terminal parent → no action; low memory → no action.
   - Rule 8: clean Blocked (no signal/exit-code/blocked-by) past min age → Backlog; too young → no action; has executor labels → no action.

4. **Add edge-case and interaction tests** — Cover cross-rule interactions:
   - A task matching both Rule 1 and Rule 3 should only fire Rule 1 (early `continue`).
   - A task matching Rule 8 preconditions but also having `self-modify: approved` should be handled by Rule 4, not Rule 8.
   - Memory gating: below `_MEM_SKIP_THRESHOLD_GB` all rules produce zero actions (tested via `main()` path or `_apply_rules` with a low-mem fixture).
   - Helper functions: `_labels()` with dict-style and string-style label entries; `_parse_updated_at()` with Z-suffix and offset-aware timestamps.

## Constraints

- Tests must call `_apply_rules()` directly — do not mock the Plane API or spawn subprocesses. The function is pure; keep tests fast and deterministic.
- Use `datetime(2026, 5, 22, tzinfo=UTC)` as `now` for all time-based assertions to avoid flaky tests.
- Follow the existing test layout under `tests/unit/entrypoints/`. Use `pytest.mark.parametrize` for rule variants.
- Do not modify `board_unblock.py` itself — this campaign is test-only.
- Each goal should be completable in a single executor session (< 1 hour).

## Success Criteria

- `pytest tests/unit/entrypoints/test_board_unblock.py -v` passes with **≥ 30 test cases** covering all 8 rules.
- Every rule has at least one positive-match and one negative-match test.
- The helper functions (`_labels`, `_label_value`, `_has_label`, `_has_label_prefix`, `_parse_updated_at`, `_retry_count`) each have at least one direct test.
- No test imports or calls `PlaneClient` — all tests are pure-function tests against `_apply_rules` and helpers.
