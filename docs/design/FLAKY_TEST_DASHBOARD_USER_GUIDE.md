# Flaky Test Reporter — Dashboard User Guide

**Version**: 1.0  
**Status**: Complete (Stage 5 Documentation)  
**Last Updated**: 2026-06-12

---

## Table of Contents

1. [Dashboard Overview](#dashboard-overview)
2. [Panel Descriptions](#panel-descriptions)
3. [Interpreting Metrics](#interpreting-metrics)
4. [Status Indicators](#status-indicators)
5. [Common Workflows](#common-workflows)
6. [Troubleshooting Guide](#troubleshooting-guide)
7. [Best Practices](#best-practices)

---

## Dashboard Overview

The Flaky Test Reporter dashboard provides real-time visibility into test flakiness across your repository. It integrates seamlessly with the OperationsCenter observer service, appearing as a native panel in the repository health dashboard.

### Key Features

- **Real-time Updates**: Dashboard updates as tests run and metrics accumulate
- **4-Tier Coverage**: Per-run, session, historical, and repository-level insights
- **Visual Summaries**: Charts and tables for quick assessment
- **Actionable Data**: Prioritized test lists and categorized flakiness patterns
- **Historical Trends**: Week-over-week comparisons and trend analysis

### Access

The dashboard is available in the observer service health snapshots at:
```
/observer/dashboard
→ Flaky Test Summary Panel
→ Category Breakdown Panel
→ Most Problematic Tests Panel
```

---

## Panel Descriptions

### 1. Flaky Test Summary Panel

**Purpose**: Quick health assessment of test flakiness in the repository.

**Displayed Metrics**:

| Metric | Description | Range | Good | Warning | Critical |
|--------|-------------|-------|------|---------|----------|
| **Flaky Test Count** | Number of tests with failure rate > 5% | 0-N | 0 | 1-5 | 6+ |
| **Unstable Test Count** | Tests with failure rate 2-5% | 0-N | 0 | 1-10 | 11+ |
| **Recovery Rate** | Percentage of flaky tests showing improvement | 0-100% | >80% | 50-80% | <50% |
| **Failure Rate Trend** | 7-day trend in failure rates | ↑↓→ | → or ↓ | Stable | ↑ |
| **Health Score** | Overall repository flakiness health | 0.0-100 | >90 | 50-90 | <50 |

**Visual Indicators**:
- 🟢 **HEALTHY**: 0 flaky tests, recovery >80%, health score >90
- 🟡 **NOMINAL**: 1-5 flaky tests, recovery 50-80%, health score 50-90
- 🟠 **DEGRADED**: 6-10 flaky tests, recovery <50%, health score 25-50
- 🔴 **CRITICAL**: 10+ flaky tests, negative trends, health score <25

**Example**:
```
┌─────────────────────────────────────────────┐
│ Flaky Test Summary                   🟡 NOMINAL │
├─────────────────────────────────────────────┤
│ Flaky Tests:        3 (↓ from 5 last week) │
│ Unstable Tests:     7 (+2 new)             │
│ Recovery Rate:      65% (↓ 5%)             │
│ Failure Trend:      ↑ Rising               │
│ Health Score:       72/100 (Degrading)     │
└─────────────────────────────────────────────┘
```

**Interpretation Guide**:
- **Flaky Count Increasing?** Indicates new regressions. Check recent commits.
- **Recovery Rate Low?** Tests have been failing for multiple days. Prioritize fixes.
- **Health Score Declining?** Repository stability is getting worse. Review trends panel.

---

### 2. Category Breakdown Panel

**Purpose**: Understand the distribution of flakiness root causes.

**Categories**:

| Category | Pattern | Root Cause | Common Symptoms |
|----------|---------|-----------|-----------------|
| **INTERMITTENT** | Random alternation, high variance | Race conditions, timing-dependent code | 50% failure rate, unpredictable pattern |
| **ENVIRONMENT** | Correlated with external factors | Resource starvation, network timeouts | Timeout exceptions, slow markers |
| **INFRASTRUCTURE** | Consistent failures, low variance | Test pollution, missing setup | Same failure every time it fails |
| **UNKNOWN** | No clear pattern detected | Insufficient data or complex interaction | Inconsistent symptoms, <3 runs |

**Visual Representation (Pie Chart)**:
```
INTERMITTENT:   45% (7 tests)  ▓▓▓▓▓
ENVIRONMENT:    25% (4 tests)  ▓▓▓
INFRASTRUCTURE: 20% (3 tests)  ▓▓
UNKNOWN:        10% (2 tests)  ▓
```

**Interpretation Guide**:
- **High INTERMITTENT %**: Focus on race condition detection and synchronization
- **High ENVIRONMENT %**: Check CI environment resources (CPU, memory, disk)
- **High INFRASTRUCTURE %**: Review test setup/teardown and isolation
- **High UNKNOWN %**: Need more test runs or manual analysis

---

### 3. Most Problematic Tests Panel

**Purpose**: Identify which tests need immediate attention.

**Displayed Information**:

| Column | Description | Example |
|--------|-------------|---------|
| **Test** | Full test nodeid | `tests/unit/auth/test_login.py::TestLogin::test_valid_credentials` |
| **Failure Rate** | Percentage of failures | 65% (13/20 runs) |
| **Category** | Flakiness root cause | INTERMITTENT |
| **Status** | Trend direction | ↑ Worsening |
| **Confidence** | Statistical confidence in diagnosis | 95% |

**Top 10 Tests Table**:
```
┌──────────────────────────────────────────────────────────┐
│ Most Problematic Tests (Top 10)                          │
├──────────────┬─────────┬──────────────┬────────┬────────┤
│ Test         │ Failure │ Category     │ Status │ Conf.  │
├──────────────┼─────────┼──────────────┼────────┼────────┤
│ test_login   │ 65%     │ INTERMITTENT │ ↑      │ 95%    │
│ test_signup  │ 45%     │ ENVIRONMENT  │ →      │ 88%    │
│ test_auth    │ 35%     │ UNKNOWN      │ ↓      │ 72%    │
└──────────────┴─────────┴──────────────┴────────┴────────┘
```

**Interpretation Guide**:
- **Red Shading (↑ Worsening)**: Test has regressed. Investigate recent changes.
- **Yellow Shading (→ Stable)**: Test is consistently flaky. Medium priority.
- **Green Shading (↓ Improving)**: Fix is working. Monitor until stable.
- **Low Confidence (<70%)**: Need more test runs before taking action.

---

## Interpreting Metrics

### Failure Rate

**Definition**: Percentage of test runs that failed.

```
Failure Rate = (Failed Runs / Total Runs) × 100
```

**Interpretation**:
- **0-5%**: Likely a transient timeout or environmental issue. Low priority.
- **5-20%**: Flaky test. Needs investigation and fix.
- **20-50%**: Highly flaky test. High priority for fixing.
- **50%+**: Borderline broken test. Consider disabling until fixed.

**Example**:
```
Test run history: PASS, PASS, FAIL, PASS, FAIL, PASS
Failure Rate = 2/6 = 33% (Flaky)
```

### Failure Rate Trend

**Definition**: How failure rates are changing over the past 7 days.

**Visual Indicators**:
- ↑ **Rising**: Failure rate increased from 7 days ago. New regression.
- → **Stable**: Failure rate unchanged. Existing known flakiness.
- ↓ **Declining**: Failure rate decreased. Fix is working.

**Example**:
```
Day 1: 20% failure rate
Day 7: 35% failure rate
Trend: ↑ Rising (15 percentage point increase)
```

### Recovery Rate

**Definition**: Percentage of previously flaky tests that have become stable (failure rate <5%).

```
Recovery Rate = (Tests Now Stable / Tests That Were Flaky) × 100
```

**Interpretation**:
- **>80%**: Excellent. Most flaky tests are being fixed.
- **50-80%**: Good. Some tests stabilizing, others still flaky.
- **<50%**: Concerning. Fixes aren't keeping up with new flakiness.

### Confidence Score

**Definition**: Statistical confidence in the flakiness diagnosis (0-100%).

**Based on**:
- Number of test runs (more runs = higher confidence)
- Consistency of failure pattern
- Environmental stability during runs

**Interpretation**:
- **>90%**: Highly confident diagnosis. Act on categorization.
- **70-90%**: Reasonably confident. Categorization is likely correct.
- **<70%**: Low confidence. Run more tests before acting.

**Improving Confidence**:
1. Run tests multiple times (at least 5 runs)
2. Use consistent CI environment
3. Isolate test dependencies

---

## Status Indicators

### Health Score Color Coding

The dashboard uses a 4-color system to indicate health:

```
🟢 GREEN   (Score > 90):   Healthy. Few flaky tests, recovering well.
🟡 YELLOW  (Score 50-90):  Nominal. Some flakiness, stable trend.
🟠 ORANGE  (Score 25-50):  Degraded. Noticeable flakiness, worsening.
🔴 RED     (Score < 25):   Critical. Severe flakiness, urgent action needed.
```

### Trend Arrows

Displayed next to metrics to show direction:

| Arrow | Meaning | Action |
|-------|---------|--------|
| ↑ | Worsening/Increasing | Investigate recent changes |
| → | Stable/Unchanged | Monitor, plan fixes |
| ↓ | Improving/Decreasing | Continue current approach |

---

## Common Workflows

### Workflow 1: Respond to Critical Alert

**Scenario**: Dashboard shows 🔴 CRITICAL health score.

**Steps**:
1. Check **Failure Rate Trend**: Is it ↑ Rising? This indicates a new regression.
2. Go to **Most Problematic Tests**: Identify the top 3 failing tests.
3. Click on each test to see:
   - Recent failure times
   - Common error messages
   - Affected code modules
4. Check recent commits:
   - Did something change in the affected modules?
   - Are there any changes to test infrastructure?
5. Take action:
   - **If recent commit**: Revert or fix the breaking change
   - **If environment issue**: Check CI resource usage
   - **If infrastructure issue**: Review test setup/teardown

**Example**:
```
🔴 Health Score: 18/100
↑ Failure Trend: Rising (from 45% to 68% in 2 days)

Top problematic: test_database_migration (72% failure)
Category: INFRASTRUCTURE
Recent commit: "Optimize database initialization" (2 days ago)

→ Action: Review commit, likely caused test isolation issue
```

### Workflow 2: Prioritize Flakiness Fixes

**Scenario**: You have multiple flaky tests. Which should you fix first?

**Steps**:
1. Use the **Most Problematic Tests** panel sorted by failure rate
2. Filter by **Category**:
   - INTERMITTENT: Usually quickest to fix (add synchronization)
   - ENVIRONMENT: Check CI environment (resource allocation)
   - INFRASTRUCTURE: Most time-consuming (refactor test setup)
3. Consider **Impact**:
   ```
   Impact Score = Failure Rate × Confidence × Number of Affected Developers
   ```
4. Prioritize high-impact, low-effort fixes:
   - Fix INTERMITTENT (high impact, low effort)
   - Defer INFRASTRUCTURE (high impact, high effort)

**Example Priority Matrix**:
```
High Impact, Low Effort:   test_api_timeout (ENVIRONMENT) → FIX FIRST
High Impact, High Effort:  test_database (INFRASTRUCTURE) → FIX AFTER
Low Impact, Low Effort:    test_utility (INTERMITTENT) → FIX IF TIME
Low Impact, High Effort:   test_edge_case (INFRASTRUCTURE) → DEFER
```

### Workflow 3: Monitor Fix Progress

**Scenario**: You implemented a fix. How do you know it's working?

**Steps**:
1. Watch the **Failure Rate Trend** for that test
2. Expected: ↓ Declining (rate should drop by >10% per day)
3. Check the **Recovery Rate** metric:
   - If increasing: Fix is working
   - If stable: Fix didn't help
4. Monitor the **Status** indicator:
   - Test should move from 🔴 RED to 🟡 YELLOW to 🟢 GREEN

**Timeline Example**:
```
Day 1 (After fix):  Failure Rate 35% → 30% (↓ Improving) → 95% Confidence
Day 2:              Failure Rate 30% → 15% (↓ Improving) → Keep monitoring
Day 3:              Failure Rate 15% → 5%  (↓ Improving) → Test stabilized
Day 4:              Failure Rate 5%  → 2%  (↓ Stable)    → Fix confirmed
```

---

## Troubleshooting Guide

### Issue 1: Dashboard Shows No Flaky Tests, But Tests Are Failing

**Symptoms**:
- Dashboard shows 0 flaky tests
- Test suite has frequent failures
- CI status is red

**Root Causes**:
1. **Insufficient Data**: Need at least 3 test runs to detect flakiness
2. **Collector Not Enabled**: FlakyTestCollector not configured in observer service
3. **All Tests Below Threshold**: No tests have >5% failure rate

**Solutions**:
1. Run test suite multiple times:
   ```bash
   for i in {1..5}; do pytest tests/; done
   ```

2. Verify collector is enabled:
   ```python
   # In observer service configuration
   from operations_center.observer.flaky_test_collector import FlakyTestCollector
   
   collector = FlakyTestCollector(storage_root="/path/to/flaky-tests")
   observer_service = RepoObserverService(
       flaky_test_collector=collector  # Must be provided
   )
   ```

3. Check storage location:
   ```bash
   ls -la /path/to/flaky-tests/
   # Should contain aggregations.jsonl, sessions.jsonl, runs/ directory
   ```

---

### Issue 2: Flaky Tests Disappear After Fix

**Symptoms**:
- Test showed 45% failure rate
- Fixed the flakiness
- Dashboard now shows 0% failure rate
- Test disappeared from "Most Problematic" list

**Expected Behavior**: This is correct! ✅

**Explanation**:
- Dashboard only shows tests with >5% failure rate (flaky threshold)
- Once your fix brings the rate below 5%, test is considered stable
- Metrics are kept for historical trending (visible in reports)

**To Verify Fix**:
1. Check **Recovery Rate** metric: Should be increasing
2. Check recent test in **Category Breakdown**: Should show improvement
3. Review **Failure Rate Trend**: Should show ↓ Declining

---

### Issue 3: Test Shows as UNKNOWN Category

**Symptoms**:
- Test appears flaky but category is "UNKNOWN"
- Not sure what's causing the flakiness
- Confidence score is low (<70%)

**Root Causes**:
1. **Insufficient Data**: Only 1-2 test runs available
2. **Inconsistent Failures**: Pattern doesn't match known categories
3. **Complex Interaction**: Multiple factors causing flakiness

**Solutions**:
1. **Increase Test Runs** (5+ recommended):
   ```bash
   pytest tests/test_specific.py -v --count=5
   ```

2. **Analyze Failure Pattern**:
   - Check CI logs for error messages
   - Look for timing-related errors (race conditions) → INTERMITTENT
   - Look for resource errors → ENVIRONMENT
   - Look for setup/teardown errors → INFRASTRUCTURE

3. **Manual Investigation**:
   - Run test in isolation multiple times
   - Run test with other tests to check for interference
   - Check for dependency on external services

---

### Issue 4: High Alert Fatigue (Too Many Alerts)

**Symptoms**:
- Receiving too many flakiness alerts
- Difficult to prioritize which tests to fix
- Alert notifications are overwhelming

**Solutions** (configured via Alert Configuration Guide):
1. **Adjust Severity Thresholds**:
   ```yaml
   # Only alert on high-impact tests
   alert_threshold:
     failure_rate: 30%  # Was 5%, now only alert on severe
     affected_tests: 5  # Only alert if 5+ tests affected
   ```

2. **Filter by Category**:
   ```yaml
   # Only alert on INFRASTRUCTURE issues
   alert_on_categories:
     - INFRASTRUCTURE
   ```

3. **Set Quiet Hours**:
   ```yaml
   alert_channels:
     slack:
       enabled: true
       quiet_hours: "18:00-09:00"  # No alerts during off-hours
   ```

---

## Best Practices

### 1. Review Dashboard Weekly

**Recommendation**: Spend 5 minutes every Monday reviewing:
- ✅ Overall health score trend
- ✅ New flaky tests (compare to last week)
- ✅ Fixed tests (moved to stable)
- ✅ Recovery rate progress

### 2. Set Alerts for Critical Changes

**Recommendation**: Configure alerts for:
- Health score drop >20 points in one day
- New flaky test appears (failure rate >50%)
- Failure rate trend ↑ (worsening)

### 3. Monitor After Deployments

**Recommendation**: After each deployment:
1. Run full test suite 3-5 times
2. Check dashboard within 1 hour
3. Revert if health score drops significantly

### 4. Use Historical Data for Planning

**Recommendation**: When planning sprint work:
- Review 2-week flakiness trend
- Allocate time to fix high-impact flaky tests
- Consider test infrastructure improvements if INFRASTRUCTURE category is high

### 5. Communicate with Team

**Recommendation**:
- Share dashboard link in team chat
- Post weekly flakiness report
- Celebrate fixed tests (team morale)
- Alert team to new regressions

---

## Additional Resources

- [**Main User Guide**](flaky-test-reporter.md): Architecture, configuration, usage
- [**CI Integration Guide**](flaky-test-reporter-ci-integration.md): CI/CD setup
- [**Alert Configuration Guide**](FLAKY_TEST_ALERT_CONFIGURATION_GUIDE.md): Custom alerts and thresholds
- [**Architecture Document**](STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md): Technical deep dive

---

## Questions & Support

For questions or issues:
1. Check the [Troubleshooting Guide](#troubleshooting-guide) above
2. Review the main [User Guide](flaky-test-reporter.md)
3. Check dashboard help tooltips (hover over metric names)
4. Contact your repository's build engineer

---

**Last Updated**: 2026-06-12  
**Version**: 1.0 Final
