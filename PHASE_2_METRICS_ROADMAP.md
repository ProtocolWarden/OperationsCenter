# Phase 2: Deferred Metrics Implementation Roadmap

**Status**: Architectural Decision (Phase 1 Revert, PR #271)  
**Date**: 2026-06-12  
**Branch**: fix/revert-269-green-main  
**Related PR**: #269 (reverted), #271 (revert)

---

## Executive Summary

PR #269 introduced tests for 6 per-test metrics that were not yet implemented in the codebase. These tests failed in CI due to missing metric implementations. The PR was reverted (commit b82b944) to restore main to green. This document clarifies the **architectural decision** to defer these 6 metrics to Phase 2 with a concrete implementation plan and success criteria.

**Architectural Decision**: These 6 metrics are intentionally **deferred to Phase 2** as part of the flaky test reporter's phased delivery approach. Phase 1 (MVP) focuses on core detection; Phase 2 adds advanced metrics.

---

## Background: Phase 1 Scope vs Phase 2 Scope

### Phase 1: Core Detection (Current - MVP)

**Completed Metrics** (7 planned, 1 delivered):

| Metric | Status | MVP Justification |
|--------|--------|-------------------|
| failure_rate | ✅ Implemented | Core metric for detection |
| failure_entropy | ⏳ Phase 2 | Advanced pattern analysis |
| streak_variance | ⏳ Phase 2 | Advanced pattern analysis |
| recovery_time_percentile_90 | ⏳ Phase 2 | Advanced recovery analysis |
| duration_stability | ⏳ Phase 2 | Advanced timing analysis |
| environment_correlation | ⏳ Phase 2 | Advanced root-cause analysis |
| isolation_score | ⏳ Phase 2 | Advanced test quality analysis |

**Implemented Fields** (current FlakyTestMetric dataclass):
- `failure_rate: float` ✅
- `pattern_entropy: float` (similar but distinct from failure_entropy)
- `streak_length: int` (length metric, not variance)
- `recovery_time_days: float | None` (days, not percentile-90)
- `duration_mean` and `duration_variance` (basic timing, not stability)

**Phase 1 Focus**: Minimum viable product for flakiness detection and categorization.

### Phase 2: Advanced Metrics (Planned - Future)

The 6 deferred metrics enable more sophisticated analysis:

1. **failure_entropy** — Shannon entropy of failure distribution
   - More precise than pattern_entropy
   - Quantifies randomness in failure patterns
   - Expected implementation: FlakyTestReporter._compute_failure_entropy()

2. **streak_variance** — Variance in failure streak lengths
   - Distinguishes random flakiness from systematic patterns
   - High variance = unpredictable; low variance = consistent
   - Expected implementation: FlakyTestReporter._compute_streak_variance()

3. **recovery_time_percentile_90** — 90th percentile of recovery time
   - Different from mean recovery_time_days
   - Captures tail behavior (worst-case recovery time)
   - Expected implementation: FlakyTestReporter._compute_recovery_time_percentile()

4. **duration_stability** — Stability of test execution duration
   - Different from duration_variance
   - Coefficient of variation normalized metric
   - Expected implementation: FlakyTestReporter._compute_duration_stability()

5. **environment_correlation** — Correlation with environment factors
   - Identifies tests that fail based on environment (CI vs local, Python version, etc.)
   - Expected implementation: FlakyTestReporter._compute_environment_correlation()

6. **isolation_score** — Test isolation quality metric
   - Measures test independence (how often test fails when run alone vs in suite)
   - Expected implementation: FlakyTestReporter._compute_isolation_score()

---

## Deferral Rationale

### Why Phase 2?

1. **MVP Completeness**: Phase 1 delivers sufficient metrics for core detection and categorization
2. **Implementation Complexity**: These 6 metrics require:
   - Additional data collection (not currently in FlakyTestResult)
   - More sophisticated statistical analysis
   - Extended historical data windows
3. **Testing Requirements**: Each metric needs formula validation + edge case coverage
4. **Risk Mitigation**: Deferral reduces Phase 1 complexity and risk

### Why Not Stubs or Placeholders?

The Phase 1 implementation uses **clean architecture** with no stub implementations:
- No `NotImplementedError` placeholders
- No `pass` statements in metric functions
- Metrics simply don't exist until implementation

This approach:
- ✅ Makes missing functionality obvious
- ✅ Prevents false negatives (tests shouldn't pass for missing features)
- ✅ Keeps codebase maintainable and clear

---

## Phase 2 Implementation Plan

### Acceptance Criteria

1. ✅ **All 6 metrics implemented with validated formulas**
   - Each metric has a mathematical formula documented
   - Formula validated against reference implementations
   - Test-driven verification (compute expected values from formula, not manually)

2. ✅ **FlakyTestMetric extended with 6 new fields**
   ```python
   @dataclass
   class FlakyTestMetric:
       # Phase 1 fields (existing)
       failure_rate: float
       pattern_entropy: float
       streak_length: int
       recovery_time_days: float | None
       
       # Phase 2 fields (new)
       failure_entropy: float = 0.0
       streak_variance: float = 0.0
       recovery_time_percentile_90: float = 0.0
       duration_stability: float = 0.0
       environment_correlation: float = 0.0
       isolation_score: float = 0.0
   ```

3. ✅ **FlakyTestReporter._compute_per_test_metrics() updated**
   - Currently computes 6 Phase 1 metrics (lines 200-250)
   - Phase 2 adds 6 new compute_* methods
   - Extended to 12 total metrics

4. ✅ **Unit tests with formula validation**
   - Test each metric's mathematical properties
   - Edge cases: empty data, single run, all-pass, all-fail
   - Floating-point precision tests
   - Formula consistency tests

5. ✅ **Integration with dashboard and alerts**
   - Dashboard panels updated to visualize Phase 2 metrics
   - Alert thresholds defined for each metric
   - Categorization logic updated if needed

### Implementation Strategy

**Step 1: Data Collection Enhancement**
- Extend FlakyTestResult to capture environment factors (Python version, runner ID, etc.)
- Add test isolation tracking (run in isolation vs in suite)
- Update storage backend to handle new fields

**Step 2: Metric Computation Implementation**
- Add 6 new methods to FlakyTestReporter:
  - `_compute_failure_entropy(results: list[FlakyTestResult]) -> float`
  - `_compute_streak_variance(streak_lengths: list[int]) -> float`
  - `_compute_recovery_time_percentile(recovery_times: list[float], percentile: float) -> float`
  - `_compute_duration_stability(durations: list[float]) -> float`
  - `_compute_environment_correlation(results: list[FlakyTestResult]) -> float`
  - `_compute_isolation_score(results: list[FlakyTestResult]) -> float`

**Step 3: Validation and Testing**
- Create test_snapshot_edge_cases_phase2.py with proper formula validation
- Verify each metric against reference implementations
- Test edge cases and boundary conditions
- Validate floating-point precision (avoid hardcoded values like 0.081296)

**Step 4: Dashboard & Alert Integration**
- Add new dashboard panels for Phase 2 metrics
- Define alert thresholds and conditions
- Update alert severity logic if needed

**Step 5: Documentation Update**
- Update flaky-test-reporter.md with Phase 2 metrics
- Add examples using Phase 2 metrics
- Update design document with Phase 2 implementation details

### Estimated Scope

| Category | Effort | Details |
|----------|--------|---------|
| Implementation | 3-4 days | 6 metric functions (~50-100 lines each) |
| Testing | 2-3 days | Unit tests + formula validation + edge cases |
| Documentation | 1 day | Guides, examples, design doc updates |
| Integration | 1 day | Dashboard, alerts, API updates |
| **Total** | **7-9 days** | Equivalent to ~1 full sprint task |

### Success Criteria

- ✅ All 6 metrics implemented and tested
- ✅ Zero false formula mismatches (use test-driven computation)
- ✅ 8+ unit tests per metric (covering happy path + 3+ edge cases)
- ✅ Integration tests verifying metrics in dashboard and alerts
- ✅ Documentation complete with examples
- ✅ All tests passing (8,200+ repository tests)
- ✅ Code quality verified (ruff clean, type checking passes)

---

## Lessons Learned from Phase 1

### What Worked
- ✅ MVP scope clearly defined (1 core metric)
- ✅ Clean architecture (no stubs or placeholders)
- ✅ Comprehensive test coverage for implemented features
- ✅ Design document specified full 7-metric vision

### What to Improve for Phase 2
- **Formula Validation**: Use test-driven computation (compute from formula, not manually)
- **Edge Case Testing**: Validate floating-point precision with multiple test cases
- **Phased Approach**: Implement metrics in stages (2-3 per iteration) rather than all at once
- **Reference Implementations**: Validate against reference before CI tests

### Test-Driven Metric Development

PR #269 attempted TDD but **failed at validation step**:
```python
# ❌ AVOID: Hardcoded expected values
test_case = dict(
    failure_sequence=[fail, pass, fail, ...],
    expected_entropy=0.081296  # ← Manual calculation, unvalidated
)

# ✅ DO: Compute from formula
import math
def compute_expected_entropy(failure_sequence):
    """Compute from formula: -Σ(p_i * log(p_i))"""
    failure_rate = sum(failure_sequence) / len(failure_sequence)
    p = [failure_rate, 1 - failure_rate]
    return -sum(x * math.log(x) for x in p if x > 0)

expected_entropy = compute_expected_entropy(failure_sequence)
```

**Phase 2 Requirement**: All expected values computed from formula, not hardcoded.

---

## References

### Related Documents
- **Design Specification**: `docs/design/STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md` (Section 4.1)
- **User Guide**: `docs/design/flaky-test-reporter.md` (Section 6: Metric Reference)
- **Investigation Report**: `INVESTIGATION_REPORT_REVIEW_CONCERNS.md` (Review Concern #4)

### Code Locations

**Current Implementation** (Phase 1):
- FlakyTestMetric definition: `src/operations_center/observer/flaky_test_models.py` (lines 34-50)
- Metric computation: `src/operations_center/observer/flaky_test_reporter.py` (lines 200-250)
- Tests: `tests/unit/observer/test_flaky_test_reporter.py` (73 tests)

**Phase 2 Implementation** (Future):
- New metric methods in FlakyTestReporter (to be added)
- Extended FlakyTestMetric dataclass (6 new fields)
- Phase 2 test file: `tests/unit/observer/test_snapshot_edge_cases_phase2.py` (to be created)

---

## Tracking & Follow-up

### GitHub Issues to Create (Phase 2 Kickoff)
1. **Issue: "Phase 2 Metrics Implementation - failure_entropy, streak_variance"**
   - Estimate: 2-3 days
   - Metrics: failure_entropy, streak_variance, recovery_time_percentile_90
   
2. **Issue: "Phase 2 Metrics Implementation - Advanced Analysis"**
   - Estimate: 2-3 days
   - Metrics: duration_stability, environment_correlation, isolation_score

3. **Issue: "Phase 2 Dashboard & Alert Integration"**
   - Estimate: 1-2 days
   - Update dashboards and alert thresholds for new metrics

### Review Checklist for Phase 2 Implementation
- [ ] All 6 metric formulas documented and validated
- [ ] Expected values computed from formula (not hardcoded)
- [ ] Edge case tests: empty data, single run, all-pass, all-fail
- [ ] Floating-point precision tests with multiple test cases
- [ ] Integration tests with dashboard and alerts
- [ ] Documentation updated with metric definitions
- [ ] Design doc updated with Phase 2 architecture
- [ ] All tests passing (8,200+)
- [ ] Code quality verified (ruff clean, type checking passes)

---

## Conclusion

The revert of PR #269 (commit b82b944) represents a **healthy architectural decision**, not a failure. Phase 1 delivers a solid MVP with core detection logic. Phase 2 will add the 6 deferred metrics as a planned future effort.

**Key Takeaway**: Clean deferral (no stubs, clear documentation, explicit roadmap) is superior to incomplete implementation in production code.

**Status**: Ready for Phase 2 implementation once Phase 1 stabilizes.
