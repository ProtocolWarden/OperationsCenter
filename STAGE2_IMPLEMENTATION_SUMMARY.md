# Stage 2 (Revised): Implementation Summary

## Objective
Implement the unified **signal→snapshot fallback pattern** (`signal.observed_at or snapshot.observed_at`) for all 6 derivers that access signals with optional `observed_at` fields.

## Acceptance Criteria ✅

✅ **Specific signal.observed_at null-checks implemented** — Not generic guards, but actual signal-level timestamp checks  
✅ **Fallback to snapshot.observed_at established** — Consistent pattern: `signal.observed_at or snapshot.observed_at`  
✅ **Unified pattern across all 6 derivers** — Single pattern applied uniformly  
✅ **All files compile** — Syntax validation passed (6/6 derivers)  
✅ **Pattern matches Stage 1 specification** — Aligns with documented signal model behavior  

## Changes Summary

### 6 Derivers Updated

| Deriver | Signal | Pattern | Status |
|---------|--------|---------|--------|
| architecture_drift.py | ArchitectureSignal | `arch.observed_at or snapshots[0].observed_at` | ✅ |
| benchmark_regression.py | BenchmarkSignal | `bench.observed_at or snapshots[0].observed_at` | ✅ |
| security_vuln.py | SecuritySignal | `sec.observed_at or snapshots[0].observed_at` | ✅ |
| coverage_gap.py | CoverageSignal | Multi-snapshot with `sig.observed_at or snap.observed_at` | ✅ |
| dependency_drift.py | DependencyDriftSignal | Two contexts with signal→snapshot fallback | ✅ |
| observation_coverage.py | CheckSignal | Conditional signal-specific fallback | ✅ |

### Key Code Changes

**Example 1: Simple fallback (architecture_drift.py)**
```python
# Before
observed_at = snapshots[0].observed_at

# After
observed_at = arch.observed_at or snapshots[0].observed_at
```

**Example 2: Complex multi-snapshot (dependency_drift.py)**
```python
# Before
first_seen_at=available_snapshots[-1].observed_at,
last_seen_at=available_snapshots[0].observed_at,

# After
first_seen = available_snapshots[-1].signals.dependency_drift.observed_at or available_snapshots[-1].observed_at
last_seen = available_snapshots[0].signals.dependency_drift.observed_at or available_snapshots[0].observed_at
first_seen_at=first_seen,
last_seen_at=last_seen,
```

**Example 3: Iteration with signal access (coverage_gap.py)**
```python
# Before
last_seen = latest.observed_at
for snap in reversed(snapshots):
    snap_sig = snap.signals.coverage_signal
    if snap_sig.status == "measured":
        first_seen = snap.observed_at  # ← Uses snapshot-level only
        break

# After
last_seen = sig.observed_at or latest.observed_at  # ← Signal first
first_seen = last_seen
for snap in reversed(snapshots):
    snap_sig = snap.signals.coverage_signal
    if snap_sig.status == "measured":
        first_seen = snap_sig.observed_at or snap.observed_at  # ← Signal→snapshot fallback
        break
```

## Why This Matters

1. **Signal-level preferred**: External tools (linters, security scanners, benchmarks) record their own invocation time — more accurate than snapshot time
2. **Snapshot-level fallback**: Always guaranteed non-None, so derivers never see null timestamps
3. **Unified pattern**: Consistent across all 6 derivers, reduces cognitive load, easier to test

## Relationship to Stages

- **Stage 0**: Identified 6 signals with optional observed_at
- **Stage 1**: Documented the fallback strategy in models.py
- **Stage 2 (This)**: Implemented the pattern in all 6 derivers ← **YOU ARE HERE**
- **Stage 3**: Comprehensive test coverage validates the pattern works

## Files Modified

1. `src/operations_center/insights/derivers/architecture_drift.py` (1 line)
2. `src/operations_center/insights/derivers/benchmark_regression.py` (1 line)
3. `src/operations_center/insights/derivers/security_vuln.py` (1 line)
4. `src/operations_center/insights/derivers/coverage_gap.py` (4 lines)
5. `src/operations_center/insights/derivers/dependency_drift.py` (8 lines)
6. `src/operations_center/insights/derivers/observation_coverage.py` (8 lines)

**Total**: 23 lines of implementation across 6 files

## Verification

✅ Syntax validation: All 6 files compile  
✅ Pattern consistency: Same `signal.observed_at or snapshot.observed_at` pattern applied everywhere  
✅ Documentation: Matches Stage 1 specification  
✅ Ready for testing: Stage 3 test suite validates correctness  

## Next: Stage 3

Stage 3 (Test Coverage) will:
1. Verify derivers correctly handle None signal.observed_at
2. Validate fallback to snapshot.observed_at works
3. Cover edge cases: multiple snapshots, filtered lists, signal transitions
4. Ensure no timestamp is ever None in the final insight

## Completion Status

**Stage 2 is COMPLETE** ✅

All 6 derivers now implement the standardized signal→snapshot fallback pattern. The codebase is ready for comprehensive test coverage in Stage 3.
