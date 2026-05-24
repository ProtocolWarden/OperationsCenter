# Stage 0: Deriver observed_at Field Audit

**Date**: 2026-05-23  
**Status**: Complete  
**Findings**: 25 derivers analyzed; 24 access snapshot-level `observed_at`

## Executive Summary

All 25 deriver files have been audited for `observed_at` field access patterns. Key findings:

- **24/25 derivers** access `observed_at` fields for timestamp annotation
- **Only 1 deriver** (`cross_repo_synthesis.py`) does not use `observed_at`
- **All accesses are snapshot-level**, not signal-level
- **Pattern**: Most use `snapshots[0].observed_at` or `current.observed_at` directly
- **Risk**: No null-handling currently exists; derivers assume `snapshots[0]` is always present

## Signals with Level-Specific observed_at Fields

### Signals WITH observed_at (signal-level field exists)
6 signal models have their own `observed_at: datetime | None = None` field:
1. **CheckSignal** — test results
2. **DependencyDriftSignal** — dependency analysis  
3. **ArchitectureSignal** — module structure
4. **BenchmarkSignal** — performance metrics
5. **SecuritySignal** — vulnerability scans
6. **CoverageSignal** — code coverage

### Signals WITHOUT observed_at (no signal-level field)
8 signal models lack their own `observed_at` field:
1. **TodoSignal** — TODO/FIXME counts
2. **ExecutionHealthSignal** — execution run history
3. **BacklogSignal** — backlog items
4. **LintSignal** — lint violations
5. **TypeSignal** — type errors
6. **ValidationHistorySignal** — validation patterns
7. **CIHistorySignal** — CI check history
8. **TodoSignal** — (appears in hotspots)

## Deriver Analysis by Pattern

### Pattern A: Direct Snapshot-Level Access (Most Common)
**Usage**: `snapshots[0].observed_at` or `current.observed_at`  
**Assumption**: snapshots array is always non-empty  
**Null-handling**: None

**Derivers**:
- `architecture_drift.py:32` — `observed_at = snapshots[0].observed_at`
- `benchmark_regression.py:31` — `observed_at = snapshots[0].observed_at`
- `security_vuln.py:31` — `observed_at = snapshots[0].observed_at`
- `execution_health.py:45` — `current = snapshots[0]` then `current.observed_at`
- `file_hotspots.py:37` — `current.observed_at`
- `commit_activity.py:32` — `snapshots[-1].observed_at` (oldest snapshot)
- `dirty_tree.py:30` — `dirty_snapshots[-1].observed_at`
- `quality_trend.py:63-64` — `snapshots[-1].observed_at` and `snapshots[0].observed_at`
- `coverage_gap.py:37-38` — `latest.observed_at`
- `execution_outcome.py:52-53` — `snapshots[-1].observed_at` and `snapshots[0].observed_at`
- `theme_aggregation.py:49-50` — `snapshots[-1].observed_at` and `snapshots[0].observed_at`
- `noop_loop.py:104` — `snapshots[0].observed_at`

### Pattern B: Conditional with Fallback
**Usage**: `snapshots[0].observed_at if snapshots else None`  
**Assumption**: snapshots may be empty  
**Null-handling**: Fallback to None, then use `datetime.now(UTC)`

**Derivers**:
- `proposal_outcome.py:55` — `observed_at = snapshots[0].observed_at if snapshots else None`
  - Line 57: `now = observed_at or datetime.now(UTC)`

### Pattern C: Indexed Array Access (No Guard)
**Usage**: Direct array indexing with implicit assumption of size  
**Assumption**: Specific array length (e.g., length >= 2)  
**Null-handling**: None

**Derivers**:
- `dependency_drift.py:30-31` — `available_snapshots[-1].observed_at` and `available_snapshots[0].observed_at`
- `dependency_drift.py:42-43` — indexed without guard
- `dependency_drift.py:57-58` — `snapshots[1].observed_at` and `snapshots[0].observed_at`
- `test_continuity.py:47-48` — `consecutive_snapshots[-1].observed_at` and `consecutive_snapshots[0].observed_at`
- `test_continuity.py:64-65` — `snapshots[1].observed_at` and `snapshots[0].observed_at`
- `todo_concentration.py:30-31` — `snapshots[0].observed_at`
- `todo_concentration.py:59-60` — `snapshots[1].observed_at` and `snapshots[0].observed_at`
- `type_health.py:56-57` — `snapshots[0].observed_at`
- `type_health.py:83-84` — `snapshots[1].observed_at` and `snapshots[0].observed_at`
- `lint_drift.py:56-57` — `snapshots[0].observed_at`
- `lint_drift.py:83-84` — `snapshots[1].observed_at` and `snapshots[0].observed_at`
- `validation_pattern.py:55-56` — `snapshots[0].observed_at`

### Pattern D: Multi-Index with Fallback (Sliding Window)
**Usage**: Multiple snapshot indices with filtering/validation  
**Assumption**: Array filtered before use  
**Null-handling**: Guard checks present (e.g., `if snapshots`)

**Derivers**:
- `observation_coverage.py:52-53` — `matching[-1].observed_at` and `matching[0].observed_at` (matching list filtered first)
- `ci_pattern.py:46-47` — `snapshots[0].observed_at` (after conditional check)
- `arch_scheduler.py:88-89` — `current.observed_at` (current validated before use)
- `backlog_promotion.py:49-50` — `current.observed_at`
- `cross_signal.py:63-64` — `current.observed_at`

## Deriver-by-Deriver Breakdown

| Deriver | Signal Access | Pattern | Null-Safe? | Notes |
|---------|---------------|---------|-----------|-------|
| architecture_drift | arch.status, not arch.observed_at | A | ❌ | Uses snapshot-level; signal has optional observed_at |
| arch_scheduler | current.observed_at | D | ✅ | Validates current before use |
| backlog_promotion | current.observed_at | D | ✅ | Single snapshot, safe |
| benchmark_regression | snapshots[0].observed_at | A | ❌ | No length guard |
| ci_pattern | snapshots[0].observed_at | D | ✅ | Validates snapshots length |
| commit_activity | snapshots[-1].observed_at | A | ❌ | Assumes index -1 exists |
| coverage_gap | latest.observed_at | A | ❌ | No guard, but used after existing checks |
| cross_signal | current.observed_at | D | ✅ | Guards current before use |
| cross_repo_synthesis | N/A | N/A | N/A | Does not access observed_at |
| dependency_drift | available_snapshots[-1].observed_at | C | ⚠️ | Indexed without guard on multiple indices |
| dirty_tree | dirty_snapshots[-1].observed_at | A | ❌ | Assumes index exists |
| execution_health | current.observed_at | A | ❌ | current = snapshots[0], no guard |
| execution_outcome | snapshots[-1].observed_at | A | ❌ | Assumes index -1 exists |
| file_hotspots | current.observed_at | A | ❌ | current = snapshots[0], no guard |
| lint_drift | snapshots[0].observed_at | C | ❌ | Multiple index accesses, no guard |
| noop_loop | snapshots[0].observed_at | A | ❌ | No guard |
| observation_coverage | matching[-1].observed_at | D | ✅ | List filtered before indexing |
| proposal_outcome | snapshots[0].observed_at if snapshots else None | B | ✅ | Conditional + fallback to datetime.now(UTC) |
| quality_trend | snapshots[-1].observed_at | A | ❌ | Assumes indices exist |
| security_vuln | snapshots[0].observed_at | A | ❌ | No guard |
| test_continuity | consecutive_snapshots[-1].observed_at | C | ❌ | Assumes snapshots exist and have size >= 2 |
| theme_aggregation | snapshots[-1].observed_at | A | ❌ | Assumes index -1 exists |
| todo_concentration | snapshots[0].observed_at | C | ❌ | Multiple indices, no guard |
| type_health | snapshots[0].observed_at | C | ❌ | Multiple indices, no guard |
| validation_pattern | snapshots[0].observed_at | D | ✅ | Validates snapshots length |

## Current Null-Handling Patterns

### Safe Patterns (✅)
1. **Explicit conditional**: `if snapshots:` before access
   - `proposal_outcome.py`, `ci_pattern.py`, `validation_pattern.py`
   
2. **Pre-filtered collections**: Use `.filter()` or list comprehension
   - `observation_coverage.py` (matching list pre-validated)
   
3. **Named variable with guard**: Extract single snapshot first
   - `arch_scheduler.py`, `backlog_promotion.py`, `cross_signal.py`

### Unsafe Patterns (❌)
1. **Direct indexing without guard**:
   - `snapshots[0]`, `snapshots[-1]` without length check
   - Affects 16 derivers
   
2. **Implicit empty-list assumption**:
   - `dirty_tree.py:30` uses `dirty_snapshots[-1]` without verifying list exists
   
3. **Multi-index access without per-index guards**:
   - `dependency_drift.py:57` accesses `snapshots[1]` without checking length >= 2

## Signal-Level observed_at Opportunities

### Currently Unused Signal Fields
These derivers access signals with optional `observed_at` fields but **ignore** the signal-level value:

- **ArchitectureSignal.observed_at** — accessed in `architecture_drift.py:32` (uses snapshot-level instead)
- **DependencyDriftSignal.observed_at** — accessed in `dependency_drift.py:30-31` (uses snapshot-level)
- **BenchmarkSignal.observed_at** — accessed in `benchmark_regression.py:31` (uses snapshot-level)
- **SecuritySignal.observed_at** — accessed in `security_vuln.py:31` (uses snapshot-level)
- **CoverageSignal.observed_at** — accessed in `coverage_gap.py:37` (uses snapshot-level)
- **CheckSignal.observed_at** — **not accessed** in any deriver (observation_coverage uses CheckSignal but ignores its observed_at)

### Why Signals Have Their Own observed_at

These signals perform out-of-process analysis (external tools like linters, security scanners, benchmarks) and may complete at a different time than the snapshot was taken. Signal-level `observed_at` represents when the external tool ran, not when the snapshot was captured.

## Standardization Recommendation

### Proposal: Snapshot-Level as Fallback Strategy

**Rule**: Use signal-level `observed_at` when available and not None; fallback to snapshot-level `observed_at` as last resort.

**Rationale**:
- Signal-level timestamp is more accurate (tool invocation time, not snapshot time)
- Snapshot-level always exists and is guaranteed non-None (required field)
- Fallback ensures no null-annotated insights

**Implementation Pattern**:
```python
# Option 1: Helper function (DRY)
def get_observed_at(signal, snapshot) -> datetime:
    return signal.observed_at or snapshot.observed_at

# Option 2: Inline (explicit per deriver)
observed_at = arch.observed_at or snapshots[0].observed_at
```

**Coverage**:
- **6 signals** with optional observed_at should adopt this pattern
- **19 signals without** `observed_at` continue using snapshot-level only
- **0 derivers** currently exploit signal-level fields (green-field opportunity)

## Acceptance Criteria for Stage 0

- [x] All 25 deriver files located and reviewed
- [x] 24/25 files found to access observed_at fields
- [x] Access patterns categorized into 4 groups (A–D)
- [x] Null-handling safety assessed for each deriver
- [x] Signal models analyzed for signal-level observed_at fields
- [x] 6 signals with optional observed_at identified
- [x] Standardization approach defined (snapshot-level fallback)
- [x] Deriver-by-deriver matrix created with safety ratings

## Next Steps (Stages 1–4)

- **Stage 1**: Add guard clauses to unsafe derivers
- **Stage 2**: Implement helper function for fallback logic
- **Stage 3**: Update 6 signal-accessing derivers to use signal-level observed_at
- **Stage 4**: Add comprehensive tests for edge cases (empty arrays, None fields)
