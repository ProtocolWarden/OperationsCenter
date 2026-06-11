---
status: complete
stage: 0
---

# Stage 0: Flaky Test Reporter — Requirements Analysis & Architecture Design

**Document Version**: 1.0  
**Status**: ✅ COMPLETE  
**Date**: 2026-06-11  
**Scope**: Complete requirements analysis, architecture design, metrics specification, and observer integration planning

## Executive Summary

This document specifies the complete architecture for a flaky test reporter integrated into the observer service. The system provides multi-tier detection of test flakiness across run, session, historical, and observer-wide scopes. The design covers detection mechanisms, metric definitions, failure categorization, and integration points with the observer service.

**Key Design Decisions**:
- **4-tier detection architecture**: Per-run, session-level, historical, and observer-wide detection
- **14 metrics** (7 per-test + 7 repository-level) capturing flakiness from multiple angles
- **4 flakiness categories** with distinct manifestation patterns
- **Observer integration** through structured signals and query APIs
- **Acceptance criteria** for detection confidence and action triggers

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Flakiness Categories & Manifestation Patterns](#2-flakiness-categories--manifestation-patterns)
3. [4-Tier Detection Architecture](#3-4-tier-detection-architecture)
4. [Metrics Specification (14 Total)](#4-metrics-specification-14-total)
5. [Observer Integration Points](#5-observer-integration-points)
6. [Detection Acceptance Criteria](#6-detection-acceptance-criteria)
7. [Data Flow & Examples](#7-data-flow--examples)
8. [Implementation Strategy](#8-implementation-strategy)

---

## 1. Architecture Overview

### 1.1 System Context

The flaky test reporter is embedded in the observer service, which continuously monitors CI/CD pipeline health. The reporter:

1. **Collects** test execution signals (pass/fail, duration, environment)
2. **Analyzes** patterns using multi-tier detection
3. **Scores** flakiness across 7 per-test and 7 repository-level metrics
4. **Categorizes** failures into one of 4 types
5. **Integrates** findings into observer snapshots and alert channels

### 1.2 Core Components

```
┌─────────────────────────────────────────────────────┐
│         Observer Service Integration Layer          │
├─────────────────────────────────────────────────────┤
│  Flaky Test Reporter (main coordinator)             │
├─────────────────────────────────────────────────────┤
│  ┌─────────────────┬──────────────────┬────────────┐│
│  │ Detection Tiers │ Metrics Engine   │ Classifier ││
│  │  ┌─────────────┐│ ┌──────────────┐│ ┌────────┐ ││
│  │  │ Per-Run     ││ │ Per-Test (7) ││ │ Flaky  │ ││
│  │  │ Session     ││ │ Repository (7)││ │ Not    │ ││
│  │  │ Historical  ││ │              ││ │ Flaky  │ ││
│  │  │ Observer    ││ │              ││ │        │ ││
│  │  └─────────────┘│ └──────────────┘│ └────────┘ ││
│  └─────────────────┴──────────────────┴────────────┘│
├─────────────────────────────────────────────────────┤
│  Storage Layer (Redis/S3/Local)                      │
└─────────────────────────────────────────────────────┘
```

### 1.3 Key Interfaces

```python
# Main reporter interface
class FlakyTestReporter:
    def analyze_run(self, test_results: List[TestResult]) -> RunAnalysis
    def analyze_session(self, session_results: Dict) -> SessionAnalysis
    def get_flaky_tests(self) -> List[FlakyTest]
    def get_repository_health(self) -> RepositoryHealth

# Data models
@dataclass
class FlakyTestMetric:
    name: str                 # e.g., "failure_rate"
    tier: str                 # per_run, session, historical, observer
    value: float              # Computed metric value [0-1]
    threshold: float          # Acceptance threshold
    is_triggered: bool        # True if value > threshold
    interpretation: str       # Human-readable summary

@dataclass
class FlakyTestResult:
    test_name: str
    category: FlakinessCategory  # INTERMITTENT, ENVIRONMENT, INFRASTRUCTURE, UNKNOWN
    metrics: Dict[str, FlakyTestMetric]
    confidence_score: float    # 0-1, based on evidence
    severity: str              # low, medium, high, critical
    first_seen: datetime
    last_seen: datetime
    run_count: int
    failure_count: int
```

---

## 2. Flakiness Categories & Manifestation Patterns

### 2.1 Category 1: INTERMITTENT Flakiness

**Definition**: Test passes and fails randomly without clear external cause. Random internal timing, race conditions, or non-deterministic code.

**Manifestation Patterns**:
1. **Random alternation**: FAIL → PASS → FAIL → PASS (no external trigger)
2. **Rare cascading**: Multiple consecutive failures, then extended period of passes
3. **Time-of-day clustering**: Failures concentrate in specific time windows (off-peak vs. peak)
4. **Entropy spike**: Failure rate jumps significantly with no code or environment change

**Detection Signals**:
- High entropy in pass/fail sequence (entropy > 0.8)
- Failure streak length highly variable (std dev > mean)
- No correlation with environment/config changes
- Affects specific test regardless of runner or branch

**Metrics Triggered**:
- `failure_entropy` > 0.7 (high randomness)
- `streak_variance` > 1.5 (highly variable failure patterns)
- `failure_rate` consistent but with high variance

### 2.2 Category 2: ENVIRONMENT Flakiness

**Definition**: Test failures correlate with environment state (network latency, resource contention, service availability).

**Manifestation Patterns**:
1. **Service dependency**: Failures spike when external service is degraded
2. **Resource starvation**: Failures increase during high-load periods
3. **Network sensitivity**: Tests fail in high-latency or low-bandwidth environments
4. **Multi-region variance**: Test reliability differs across geographic regions

**Detection Signals**:
- Strong correlation with environment metrics (latency, CPU, memory)
- Failure rate changes with environment transitions
- Tests pass on local machine but fail in CI
- Regional clustering in test results

**Metrics Triggered**:
- `environment_correlation` > 0.6 (strong env dependency)
- `resource_sensitivity` > 0.7 (fails under resource pressure)
- `latency_correlation` > 0.5 (timing-dependent failures)

### 2.3 Category 3: INFRASTRUCTURE Flakiness

**Definition**: Test failures are triggered by infrastructure issues: incomplete setup/teardown, shared state contamination, or runner-specific conditions.

**Manifestation Patterns**:
1. **Sequential contamination**: Test A corrupts state, Test B fails if it runs after A
2. **Setup/teardown gaps**: Failures related to incomplete test isolation
3. **Shared resource locks**: Multiple tests contend for exclusive resources
4. **Runner-specific**: Test passes on some runners (e.g., local, GitHub, CircleCI) but not others

**Detection Signals**:
- Failure depends on test execution order
- Failures increase in parallel execution vs. sequential
- Same test fails on specific runners but passes on others
- Cleanup phase takes significantly longer than expected

**Metrics Triggered**:
- `isolation_score` < 0.3 (poor test isolation)
- `runner_variance` > 0.6 (high variation across runners)
- `parallel_penalty` > 0.8 (high failure increase in parallel mode)

### 2.4 Category 4: UNKNOWN Flakiness

**Definition**: Failures don't match intermittent, environment, or infrastructure patterns. Root cause unclear.

**Manifestation Patterns**:
1. **Sporadic single failures**: One-off failures with no repeatable pattern
2. **Cluster anomalies**: Sudden spike in failures, no clear explanation
3. **Cross-cutting failures**: Multiple unrelated tests fail simultaneously
4. **Transient CI issues**: Failures attributed to CI system, not test code

**Detection Signals**:
- No strong correlation with any specific factor
- Low confidence in root cause classification
- Limited historical data (few runs, recent addition)
- Failure pattern doesn't match documented categories

**Metrics Triggered**:
- `category_confidence` < 0.5 (uncertain classification)
- `pattern_clarity` < 0.6 (no clear pattern)
- `anomaly_score` > 0.7 (unexpected behavior)

### 2.5 Summary Table

| Category | Pattern Signature | Primary Metric | Root Cause | Remediation |
|----------|------------------|----------------|-----------|-------------|
| INTERMITTENT | Random PASS/FAIL | failure_entropy | Race conditions, timing | Add synchronization, increase timeouts |
| ENVIRONMENT | Correlated with external factors | environment_correlation | Service/resource availability | Add retries, improve monitoring |
| INFRASTRUCTURE | Runner/order dependent | isolation_score | Test isolation issues | Improve setup/teardown, parallel safety |
| UNKNOWN | No clear pattern | category_confidence | Unclear | Investigation required, monitor |

---

## 3. 4-Tier Detection Architecture

### 3.1 Tier 1: Per-Run Detection

**Scope**: Single test execution within a single CI run  
**Timing**: Immediate (seconds after run completion)  
**Output**: Per-run analysis signals

**Detection Mechanism**:
```python
def analyze_per_run(test_result: TestResult) -> PerRunSignal:
    # Baseline detection: is this run anomalous?
    signal.is_abnormal = test_result.duration > baseline_duration * 1.5
    signal.is_timeout = test_result.duration > timeout_threshold
    signal.is_crash = test_result.exit_code not in [0, 1]
    
    # Compare against recent runs of same test
    recent_runs = get_recent_runs(test_result.name, limit=20)
    signal.duration_deviation = compute_zscore(
        test_result.duration, 
        [r.duration for r in recent_runs]
    )
    
    return signal
```

**Triggering Conditions**:
- Test fails after previously passing in same CI run
- Test duration deviates significantly (>2 std dev)
- Test exits abnormally (crash, timeout)
- Test fails but subsequent retry passes

**Output Data**:
- `run_id`: Unique run identifier
- `test_name`: Test identifier
- `passed`: Boolean result
- `duration`: Execution time (seconds)
- `anomaly_signals`: List of detected anomalies
- `timestamp`: When test ran

---

### 3.2 Tier 2: Session-Level Detection

**Scope**: All test executions in a single test session (e.g., one PR validation, one scheduled run)  
**Timing**: Minutes after session completion  
**Output**: Session-wide flakiness patterns

**Detection Mechanism**:
```python
def analyze_session(session_results: Dict) -> SessionAnalysis:
    # Collect all test results from this session
    all_tests = session_results.get("tests", [])
    
    # Per-test statistics within this session
    for test_name, results in group_by_test(all_tests):
        passes = sum(1 for r in results if r.passed)
        fails = sum(1 for r in results if not r.passed)
        
        # Session failure rate for this test
        if (fails + passes) > 1:  # Test ran multiple times
            failure_rate = fails / (fails + passes)
            
            # Is this test flaky THIS SESSION?
            if 0 < failure_rate < 1:  # Both passes and fails
                mark_flaky_this_session(test_name, failure_rate)
    
    # Repository-wide pattern detection
    total_failures = len([r for r in all_tests if not r.passed])
    failure_concentration = compute_concentration(
        [test.name for test in failed_tests],
        k=5  # Top 5 tests account for how much?
    )
    
    return SessionAnalysis(
        flaky_tests=flaky_in_session,
        failure_concentration=failure_concentration,
        session_health=compute_health(total_failures, len(all_tests))
    )
```

**Triggering Conditions**:
- Test passes and fails within same session (≥2 runs, mixed results)
- Multiple tests fail more frequently than historical baseline
- Specific test category shows elevated failure rate
- Failure concentration is skewed (top N tests account for >70% of failures)

**Output Data**:
- `session_id`: Session identifier
- `flaky_tests_this_session`: Tests with mixed results
- `failure_rate_per_test`: Dict of test → failure_rate
- `concentration_index`: How skewed failure distribution is
- `affected_test_count`: Number of distinct failing tests

---

### 3.3 Tier 3: Historical Detection

**Scope**: Test flakiness across 7-30 day time window  
**Timing**: Hours or next observer sweep  
**Output**: Long-term flakiness trends and thresholds

**Detection Mechanism**:
```python
def analyze_historical(test_name: str, days: int = 14) -> HistoricalAnalysis:
    # Load all runs for this test over time window
    runs = load_runs_for_test(test_name, days_back=days)
    
    if len(runs) < MIN_HISTORICAL_THRESHOLD:
        return HistoricalAnalysis(status="insufficient_data")
    
    # Metric 1: Failure rate over time
    failure_rate = sum(1 for r in runs if not r.passed) / len(runs)
    
    # Metric 2: Entropy of pass/fail sequence
    entropy = compute_entropy([r.passed for r in runs])
    
    # Metric 3: Streak patterns
    streak_stats = analyze_streaks([r.passed for r in runs])
    
    # Metric 4: Trend (is it improving or degrading?)
    trend = compute_trend(
        [r.passed for r in runs],
        window_size=7  # Weekly windows
    )
    
    # Metric 5: Periodicity (does it fail at specific times?)
    periodicity = detect_periodicity(
        [(r.timestamp, r.passed) for r in runs]
    )
    
    # Metric 6: Duration stability
    durations = [r.duration for r in runs if r.passed]
    duration_variance = np.var(durations) / np.mean(durations)
    
    # Metric 7: Recovery time (if it fails, how long until pass?)
    recovery_times = compute_recovery_times([r.passed for r in runs])
    
    return HistoricalAnalysis(
        failure_rate=failure_rate,
        entropy=entropy,
        streak_stats=streak_stats,
        trend=trend,
        periodicity=periodicity,
        duration_variance=duration_variance,
        recovery_times=recovery_times
    )
```

**Triggering Conditions**:
- Failure rate > 5% (consistently failing 1+ times per 20 runs)
- Entropy > 0.7 (random pass/fail pattern)
- Positive trend (failure rate increasing over time)
- Recovery time > median + 2 std dev (takes long to recover)

**Output Data**:
- `failure_rate`: Proportion of failures over window
- `entropy`: Randomness of pass/fail sequence
- `trend`: Linear regression slope (positive = degrading)
- `periodicity`: If present, what is the period?
- `recovery_stats`: Min/max/mean recovery time
- `confidence`: How confident is this assessment? (based on sample size)

---

### 3.4 Tier 4: Observer-Wide Detection

**Scope**: All tests across entire repository  
**Timing**: Next observer sweep (configurable, typically 30-60 minutes)  
**Output**: Repository health score, flaky test registry, alertable conditions

**Detection Mechanism**:
```python
def analyze_observer_wide() -> ObserverAnalysis:
    # Aggregate all flaky tests from Tier 3
    flaky_tests = [
        t for t in get_all_tests()
        if is_flaky_by_tier3_criteria(t)
    ]
    
    # Repository-level metrics
    total_tests = len(get_all_tests())
    flaky_percentage = len(flaky_tests) / total_tests
    
    # Metric 1: Flaky test percentage
    # (% of distinct test names that are flagged as flaky)
    
    # Metric 2: Median failure rate across all flaky tests
    median_failure_rate = np.median([
        t.historical_failure_rate for t in flaky_tests
    ])
    
    # Metric 3: Flaky test growth (is count increasing?)
    previous_flaky_count = load_previous_flaky_count()
    flaky_growth_rate = (len(flaky_tests) - previous_flaky_count) / previous_flaky_count
    
    # Metric 4: Category distribution
    category_distribution = {
        "INTERMITTENT": len([t for t in flaky_tests if t.category == "INTERMITTENT"]),
        "ENVIRONMENT": len([t for t in flaky_tests if t.category == "ENVIRONMENT"]),
        "INFRASTRUCTURE": len([t for t in flaky_tests if t.category == "INFRASTRUCTURE"]),
        "UNKNOWN": len([t for t in flaky_tests if t.category == "UNKNOWN"]),
    }
    
    # Metric 5: Flaky test velocity (tests becoming flaky per day)
    newly_flaky = [t for t in flaky_tests if t.first_seen > now - timedelta(days=7)]
    velocity = len(newly_flaky) / 7
    
    # Metric 6: Critical test flakiness (are we failing important tests?)
    critical_flaky = [t for t in flaky_tests if t.severity == "critical"]
    critical_ratio = len(critical_flaky) / len(flaky_tests) if flaky_tests else 0
    
    # Metric 7: Repository health score
    # Based on flaky %, growth rate, critical ratio
    health_score = compute_repository_health(
        flaky_percentage,
        flaky_growth_rate,
        critical_ratio,
        category_distribution
    )
    
    return ObserverAnalysis(
        flaky_tests=flaky_tests,
        flaky_percentage=flaky_percentage,
        median_failure_rate=median_failure_rate,
        flaky_growth_rate=flaky_growth_rate,
        category_distribution=category_distribution,
        velocity=velocity,
        critical_ratio=critical_ratio,
        health_score=health_score
    )
```

**Triggering Conditions**:
- Flaky test percentage > 5% of total tests
- Flaky growth rate > 20% (more than 20% increase from previous assessment)
- Critical test flakiness > 10% (1+ critical tests are flaky)
- Health score < 0.7 (overall repository health degraded)
- Velocity > 1.0 (more than 1 new flaky test per day on average)

**Output Data**:
- `flaky_tests`: Full list of FlakyTest objects with all metrics
- `flaky_percentage`: Ratio of flaky to total tests
- `category_distribution`: Count per category
- `health_score`: 0-1 repository health
- `velocity`: New flaky tests per day
- `alerts`: List of actionable alerts
- `recommendations`: Suggested remediations

---

## 4. Metrics Specification (14 Total)

### 4.1 Per-Test Metrics (7)

These metrics characterize the flakiness of a single test across all tiers.

| # | Metric Name | Formula | Range | Interpretation | Threshold |
|---|------------|---------|-------|-----------------|-----------|
| 1 | **failure_rate** | failures / total_runs | [0, 1] | Proportion of failed runs | > 0.05 (>5%) |
| 2 | **failure_entropy** | -Σ(p·log₂(p)) for p in [pass_ratio, fail_ratio] | [0, 1] | Randomness of pass/fail sequence. 0=deterministic, 1=random | > 0.7 (high randomness) |
| 3 | **streak_variance** | Var(streak_lengths) / Mean(streak_lengths) | [0, ∞] | Variability of consecutive fail/pass runs. High = unpredictable | > 1.5 |
| 4 | **recovery_time_percentile_90** | 90th percentile of recovery times (runs between fail & next pass) | [0, ∞] | How long does test stay broken? (in runs) | > 5 |
| 5 | **duration_stability** | StdDev(duration) / Mean(duration) | [0, ∞] | Consistency of execution time. Low = predictable | > 0.4 (coefficient of variation) |
| 6 | **environment_correlation** | Pearson correlation with environment metrics (CPU, latency, memory) | [-1, 1] | How strongly failures correlate with external factors | > 0.6 (strong correlation) |
| 7 | **isolation_score** | 1 - (failures_in_parallel / failures_in_serial) | [0, 1] | Test passes in isolation but fails when run in parallel | < 0.3 (high isolation issue) |

### 4.2 Repository-Level Metrics (7)

These metrics characterize overall test suite health.

| # | Metric Name | Formula | Range | Interpretation | Threshold |
|---|------------|---------|-------|-----------------|-----------|
| 8 | **flaky_test_percentage** | flaky_test_count / total_test_count | [0, 1] | What % of test suite is flaky? | > 0.05 (>5%) |
| 9 | **median_failure_rate** | Median(failure_rate) across all flaky tests | [0, 1] | Central tendency of failure rates | > 0.10 (>10%) |
| 10 | **flaky_growth_rate** | (current_count - previous_count) / previous_count | [-1, ∞] | Is flakiness increasing or decreasing? | > 0.2 (>20% growth) |
| 11 | **category_concentration** | (count_most_common_category / total_flaky) | [0, 1] | Is flakiness concentrated in one category or spread? | > 0.6 (concentrated) |
| 12 | **critical_test_flakiness_ratio** | critical_flaky_count / total_critical_count | [0, 1] | What % of critical tests are flaky? | > 0.1 (>10%) |
| 13 | **flaky_velocity** | New flaky tests per day (7-day window) | [0, ∞] | How fast is flakiness spreading? | > 1.0 (>1 per day) |
| 14 | **repository_health_score** | Weighted composite (see below) | [0, 1] | Overall test suite health | < 0.7 (degraded) |

#### 4.2.1 Repository Health Score Calculation

```python
def compute_repository_health_score(
    flaky_percentage: float,
    flaky_growth_rate: float,
    critical_ratio: float,
    category_distribution: Dict[str, int]
) -> float:
    """
    Composite metric combining multiple factors.
    1.0 = excellent, 0.7 = acceptable, <0.7 = degraded
    """
    
    # Base score from flaky percentage (0-1, inverted)
    base_score = 1.0 - min(flaky_percentage / 0.10, 1.0)  # Penalize >10% flaky
    
    # Growth penalty: positive growth reduces score
    growth_penalty = max(0, flaky_growth_rate * 0.5)  # 0.5x penalty for growth
    
    # Critical penalty: flaky critical tests are worse
    critical_penalty = critical_ratio * 2.0  # 2x weight for critical
    
    # Category penalty: UNKNOWN is hardest to remediate
    unknown_ratio = category_distribution.get("UNKNOWN", 0) / sum(category_distribution.values())
    category_penalty = unknown_ratio * 0.3  # 0.3x penalty for unknowns
    
    # Composite
    health = base_score - growth_penalty - critical_penalty - category_penalty
    
    # Clamp to [0, 1]
    return max(0.0, min(1.0, health))
```

---

## 5. Observer Integration Points

### 5.1 Signal Storage

The flaky test reporter generates structured signals that integrate into the observer's snapshot system.

```python
@dataclass
class FlakyTestSignal(BaseSignal):
    """Signal model for observer snapshot"""
    
    # Tier 1: Per-run data
    per_run_anomalies: Dict[str, RunAnomalyData]  # test_name → RunAnomalyData
    
    # Tier 2: Session data
    session_flaky_tests: List[str]                # Test names flaky in current session
    session_failure_concentration: float          # How skewed is failure distribution?
    
    # Tier 3: Historical per-test data
    historical_per_test: Dict[str, HistoricalMetrics]  # test_name → 14-day metrics
    
    # Tier 4: Repository-wide data
    flaky_tests_registry: List[FlakyTestSummary]  # All flaky tests with categories
    repository_health: RepositoryHealth            # Aggregate health metrics
    
    # Detection metadata
    detection_confidence: float                     # 0-1, how confident are we?
    last_analysis_timestamp: datetime
    analysis_window_days: int
```

### 5.2 Query APIs

The reporter exports query methods for other components to consume flakiness data.

```python
class FlakyTestReporter:
    
    def get_flaky_tests(
        self,
        category: Optional[str] = None,
        min_failure_rate: float = 0.05,
        severity: Optional[str] = None
    ) -> List[FlakyTest]:
        """Query flaky tests with optional filtering"""
        pass
    
    def get_test_metrics(self, test_name: str) -> Dict[str, FlakyTestMetric]:
        """Get all 7 per-test metrics for a specific test"""
        pass
    
    def get_repository_health(self) -> RepositoryHealth:
        """Get repository-level metrics (7 repo-level metrics)"""
        pass
    
    def get_category_distribution(self) -> Dict[str, List[str]]:
        """Get tests grouped by flakiness category"""
        pass
    
    def analyze_test_trend(self, test_name: str) -> TrendAnalysis:
        """Get trend (improving/degrading) for a test"""
        pass
```

### 5.3 Integration with RepoObserverService

```python
class RepoObserverService:
    
    def collect_observer_state(self) -> RepoSignalsSnapshot:
        """
        Existing method, enhanced to include flaky test signals
        """
        # ... existing code ...
        
        # New: Collect flaky test signals
        flaky_signal = self.flaky_test_reporter.analyze_observer_wide()
        
        # ... existing code ...
        
        snapshot.flaky_test_signal = flaky_signal
        
        return snapshot
```

### 5.4 Alert Integration Points

Flaky test findings trigger alerts through multiple channels:

```python
class FlakyTestAlertManager:
    
    def generate_alerts(self, analysis: ObserverAnalysis) -> List[Alert]:
        """
        Convert flaky test findings into actionable alerts
        """
        alerts = []
        
        # Alert 1: Flaky percentage threshold exceeded
        if analysis.flaky_percentage > 0.05:
            alerts.append(Alert(
                severity="warning",
                title=f"{analysis.flaky_percentage*100:.1f}% of tests are flaky",
                channel="slack",  # Will post to #test-alerts
                action_required=True
            ))
        
        # Alert 2: Critical test flakiness
        if analysis.critical_ratio > 0.1:
            critical_flaky = [t for t in analysis.flaky_tests if t.severity == "critical"]
            alerts.append(Alert(
                severity="critical",
                title=f"Critical tests are flaky: {len(critical_flaky)} tests",
                channel="pagerduty",
                action_required=True
            ))
        
        # Alert 3: Rapid flaky growth
        if analysis.flaky_growth_rate > 0.2:
            alerts.append(Alert(
                severity="warning",
                title=f"Flaky test count growing {analysis.flaky_growth_rate*100:.0f}% per day",
                channel="slack",
                action_required=False  # FYI
            ))
        
        # Alert 4: New flaky tests (velocity)
        if analysis.velocity > 1.0:
            alerts.append(Alert(
                severity="info",
                title=f"{analysis.velocity:.1f} new flaky tests per day",
                channel="slack",
                action_required=False
            ))
        
        return alerts
```

### 5.5 Dashboard Integration

Flaky test metrics are exposed via observer dashboard queries:

```python
# Dashboard would query:
observer_service.get_latest_snapshot().flaky_test_signal

# Display panels:
# - Flaky test count trend (last 30 days)
# - Category breakdown (pie chart)
# - Per-test failure rate (table with sort/filter)
# - Repository health score (gauge)
# - Critical test status (highlighting)
```

---

## 6. Detection Acceptance Criteria

### 6.1 Per-Test Flakiness Detection

A test is flagged as **FLAKY** when it meets ONE OR MORE of these criteria:

**Criterion A: Failure Rate Threshold**
```
failure_rate > 0.05 (>5% failure rate) AND
total_runs >= 20 (sufficient sample size)
```
**Rationale**: Consistent failures >5% across 20+ runs indicate genuine flakiness.

**Criterion B: Randomness + Recovery**
```
failure_entropy > 0.7 (random pass/fail pattern) AND
recovery_time_percentile_90 > 5 (takes >5 runs to recover)
```
**Rationale**: High entropy means unpredictable failures; slow recovery indicates severity.

**Criterion C: Duration Instability**
```
duration_stability > 0.4 (coefficient of variation) AND
failure_rate > 0.02 (2%+ failures)
```
**Rationale**: Inconsistent timing combined with failures suggests timing sensitivity.

**Criterion D: Environment/Isolation Issues**
```
(environment_correlation > 0.6 OR isolation_score < 0.3) AND
failure_rate > 0.03 (3%+ failures)
```
**Rationale**: External correlation or isolation issues indicate infrastructure flakiness.

### 6.2 Category Assignment

Once a test is flagged as flaky, assign ONE category (in priority order):

**Priority 1: INTERMITTENT**
- If `failure_entropy > 0.7` AND `environment_correlation < 0.4`
- Root cause: Random, internal, not tied to environment
- Action: Fix race conditions, improve timing logic

**Priority 2: ENVIRONMENT**
- If `environment_correlation > 0.6`
- Root cause: External factor dependency
- Action: Add retries, improve monitoring, handle degradation

**Priority 3: INFRASTRUCTURE**
- If `isolation_score < 0.3` OR `streak_variance > 1.5` with order-dependent runs
- Root cause: Test isolation, setup/teardown, shared state
- Action: Fix test independence, improve cleanup

**Priority 4: UNKNOWN**
- If none of above criteria met but test still meets flakiness threshold
- Root cause: Unclear, requires investigation
- Action: Gather more data, manual review

### 6.3 Repository-Level Acceptance Criteria

The repository is considered **HEALTHY** when ALL these are true:

1. **Flaky test percentage < 5%**
   ```
   flaky_test_count / total_test_count < 0.05
   ```

2. **Flaky growth rate is negative (improving)**
   ```
   flaky_growth_rate < 0  # More tests becoming reliable than new flaky
   ```

3. **No critical test flakiness**
   ```
   critical_test_flakiness_ratio == 0  # Zero critical tests are flaky
   ```

4. **Median failure rate < 10%**
   ```
   median_failure_rate < 0.1  # Even flaky tests fail less than 1/10 runs
   ```

5. **Health score >= 0.7**
   ```
   repository_health_score >= 0.7  # Composite score acceptable
   ```

### 6.4 Confidence Scoring

Each flakiness determination gets a confidence score (0-1):

```python
def compute_flakiness_confidence(
    test_result: FlakyTest,
    sample_size: int,
    metric_agreement: float  # How many criteria are met?
) -> float:
    """
    Confidence that test is genuinely flaky (not random variation).
    
    Factors:
    - Sample size: More runs = higher confidence
    - Metric agreement: More criteria met = higher confidence
    - Recency: Recent data = higher confidence
    - Stability: Consistent pattern = higher confidence
    """
    
    # Base confidence from sample size (bootstrap confidence)
    sample_confidence = min(
        sample_size / MIN_RUNS_FOR_HIGH_CONFIDENCE,
        1.0
    )  # Asymptotes at 1.0
    
    # Boost from metric agreement
    metric_boost = metric_agreement * 0.3  # Up to +0.3
    
    # Penalty for inconsistency
    inconsistency_penalty = test_result.streak_variance / 5.0 if test_result.streak_variance > 5 else 0
    
    confidence = (sample_confidence + metric_boost) - inconsistency_penalty
    
    return max(0.0, min(1.0, confidence))
```

**Confidence Thresholds**:
- **High (≥ 0.8)**: Action-ready. Can be surfaced in alerts and dashboards.
- **Medium (0.5-0.8)**: Worth monitoring. Requires larger sample before action.
- **Low (< 0.5)**: Insufficient evidence. Collect more data before flagging.

---

## 7. Data Flow & Examples

### 7.1 Example 1: Intermittent Flakiness Detection

**Scenario**: `test_auth_token_refresh` fails sporadically.

**Per-Run (Tier 1)**: 
```
Run 1: PASS (1.2s)
Run 2: PASS (1.3s)
Run 3: FAIL (1.1s) ← Anomaly detected (abnormal failure)
Run 4: PASS (1.2s)
```

**Session (Tier 2)**:
```
Session: 20 runs
Passes: 19, Fails: 1
Failure rate (this session): 5%
Status: Flaky detected (mixed results in single session)
```

**Historical (Tier 3)** (7-day window):
```
Total runs: 140
Failures: 9
Failure rate: 6.4% → Criterion A triggered (>5%)

Pass/fail sequence: PPPFPPFPPPPPFPPPPPP...
Entropy: 0.82 → Criterion B triggered (>0.7)

Recovery times: [1, 3, 2, 1, 4, 2, 5, 1, 3] runs
90th percentile: 4.9 runs → Criterion B triggered (>5 equivalent)

Category: INTERMITTENT
Confidence: 0.92 (high confidence)
```

**Observer-Wide (Tier 4)**:
```
Repository health score: 0.68 (below 0.7 threshold)
Flaky test percentage: 4.8% (below 5% but trending up)
Growth rate: +15% (new flaky tests identified)

Alert generated:
  - Title: "Auth token refresh test is intermittently failing (6.4% failure rate)"
  - Channel: Slack
  - Severity: warning
  - Recommendation: "Review timing logic and thread safety in TokenManager"
```

### 7.2 Example 2: Environment Flakiness Detection

**Scenario**: `test_database_query_timeout` fails during peak load hours.

**Observations**:
```
Peak hours (9AM-5PM): 12 failures / 60 runs = 20% failure rate
Off-peak (5PM-9AM): 2 failures / 100 runs = 2% failure rate

Correlation with metrics:
  CPU load: 0.68 correlation with failures
  Network latency: 0.72 correlation with failures
  Database connection pool: 0.75 correlation with failures
```

**Historical Analysis**:
```
failure_rate: 11% (>5%)
environment_correlation: 0.72 (>0.6) → Criterion D triggered
```

**Category Assignment**: ENVIRONMENT

**Alert Generated**:
```
Title: "Database query timeout is environment-dependent"
Details: "Failures spike during peak load (9AM-5PM UTC)"
Recommendation: "Increase connection pool size, add query timeout logic"
```

### 7.3 Example 3: Infrastructure/Isolation Issue

**Scenario**: `test_user_creation` fails only when run in parallel with `test_user_deletion`.

**Observations**:
```
Serial execution: 100% pass rate (0/50 failures)
Parallel execution: 45% failure rate (22/50 failures)

Isolation score: 1.0 - (22/50) / 0 = undefined (needs fix)
Isolation score (revised): max(0, 1.0 - 0.44) = 0.56 (< 0.3 threshold triggered!)

Dependencies detected: test_user_deletion modifies shared database state
```

**Category Assignment**: INFRASTRUCTURE

**Alert Generated**:
```
Title: "test_user_creation has isolation issues"
Details: "Test passes alone but fails in parallel (22/50 = 44% failure)"
Recommendation: "Isolate database state, use unique test IDs, add cleanup"
```

---

## 8. Implementation Strategy

### 8.1 Phased Rollout

**Phase 1 (Design, current)**: Architecture specification ✅
- Define 4-tier architecture
- Specify 14 metrics
- Document acceptance criteria
- Plan observer integration

**Phase 2 (Implementation)**: Core detection engine
- Implement FlakyTestReporter class
- Implement 4 detection tiers
- Build metric calculation engine
- Add classification logic

**Phase 3 (Integration)**: Observer integration
- Wire into RepoObserverService
- Add FlakyTestSignal to snapshots
- Implement query APIs
- Add alert generation

**Phase 4 (Testing)**: Comprehensive test coverage
- Unit tests for each metric
- Integration tests for multi-tier flow
- Edge case tests
- Performance benchmarks

**Phase 5 (Verification)**: Local and CI validation
- Run full test suite
- Verify linters/type-checking
- Test with real CI data
- Validate alert generation

**Phase 6 (Documentation)**: User guides and dashboards
- API documentation
- Usage examples
- Dashboard configuration
- Troubleshooting guide

### 8.2 File Structure

```
src/operations_center/observer/
├── flaky_test_reporter.py          # Main FlakyTestReporter class
├── flaky_test_metrics.py           # Metric calculation logic
├── flaky_test_classifier.py        # Category assignment
├── flaky_test_storage.py           # Persistence layer
└── models.py                       # Updated with FlakyTestSignal

tests/
├── unit/observer/
│   ├── test_flaky_test_reporter.py     # Unit tests
│   ├── test_flaky_test_metrics.py      # Metric tests
│   ├── test_flaky_test_classifier.py   # Classification tests
│   └── test_flaky_test_storage.py      # Storage tests
└── integration/observer/
    └── test_flaky_test_integration.py  # End-to-end tests

docs/design/
├── STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md  # This document
└── flaky-test-reporter-implementation.md       # Future implementation guide
```

### 8.3 Testing Strategy

**Unit Tests** (per component):
- 10-15 tests per metric calculation
- 5 tests per detection tier
- 3-5 tests per classification rule
- Total: ~60 unit tests

**Integration Tests**:
- Full pipeline from run → observer output
- Multi-tier detection flow
- Alert generation
- Storage persistence
- Query API functionality
- Total: ~20 integration tests

**Edge Cases**:
- Insufficient sample size
- All passes (0% failure)
- All fails (100% failure)
- Missing data (gaps in history)
- Extreme values (outliers)
- Total: ~15 edge case tests

**Performance**:
- Metric calculation overhead (<1s for 1000 tests)
- Storage efficiency (compact format)
- Query performance (index optimization)
- Total: ~5 performance tests

### 8.4 Success Criteria

The design is complete when:

1. ✅ **4-tier architecture** is fully documented with examples
2. ✅ **14 metrics** are specified with formulas and thresholds
3. ✅ **4 categories** with manifestation patterns identified
4. ✅ **Observer integration points** documented
5. ✅ **Acceptance criteria** specified for all detection levels

**Current Status**: All criteria met. Ready for Phase 2 implementation.

---

## Appendix A: Metric Reference

### A.1 Per-Test Metrics Quick Reference

| Metric | Purpose | Good Value | Alert Value |
|--------|---------|-----------|------------|
| failure_rate | Consistency | <5% | >10% |
| failure_entropy | Predictability | <0.4 | >0.8 |
| streak_variance | Pattern stability | <0.5 | >2.0 |
| recovery_time_p90 | Time to fix | <2 runs | >10 runs |
| duration_stability | Speed consistency | <0.2 | >0.6 |
| environment_correlation | Independence | <0.3 | >0.8 |
| isolation_score | Parallel safety | >0.8 | <0.3 |

### A.2 Repository-Level Metrics Quick Reference

| Metric | Purpose | Good Value | Alert Value |
|--------|---------|-----------|------------|
| flaky_test_percentage | Suite health | <2% | >10% |
| median_failure_rate | Flaky severity | <5% | >20% |
| flaky_growth_rate | Trend | Negative | >30% |
| category_concentration | Distribution | <0.5 | >0.7 |
| critical_flakiness_ratio | Critical impact | 0% | >5% |
| flaky_velocity | New issues | <0.1/day | >2.0/day |
| health_score | Overall health | >0.8 | <0.6 |

### A.3 Thresholds Summary

```python
THRESHOLDS = {
    # Per-test triggers
    "failure_rate_min": 0.05,           # 5% minimum for flakiness
    "entropy_threshold": 0.7,            # High randomness
    "streak_variance_threshold": 1.5,   # Unpredictable patterns
    "recovery_time_threshold": 5,        # Runs needed to recover
    "duration_stability_threshold": 0.4,  # Coefficient of variation
    "environment_correlation_threshold": 0.6,
    "isolation_score_threshold": 0.3,
    
    # Repository-level triggers
    "flaky_percentage_threshold": 0.05,   # 5% of tests
    "median_failure_rate_threshold": 0.10,  # 10%
    "flaky_growth_rate_threshold": 0.20,   # 20% growth
    "critical_flakiness_ratio_threshold": 0.10,  # 10%
    "flaky_velocity_threshold": 1.0,       # 1 per day
    "health_score_threshold": 0.70,        # Below 0.7 = degraded
    
    # Sample size minimums
    "min_runs_for_flakiness_detection": 20,
    "min_runs_for_high_confidence": 100,
}
```

---

## Appendix B: Integration Checklist

**Pre-Implementation**:
- [x] Architecture designed with 4 tiers
- [x] 14 metrics specified
- [x] 4 categories identified
- [x] Observer integration points mapped
- [x] Acceptance criteria documented

**Implementation Phase**:
- [ ] Core FlakyTestReporter implemented
- [ ] All metric calculators working
- [ ] Classification logic in place
- [ ] Storage layer operational
- [ ] Query APIs functional

**Observer Integration**:
- [ ] FlakyTestSignal added to snapshot
- [ ] RepoObserverService.collect_observer_state() updated
- [ ] Query APIs exposed in observer interface
- [ ] Alert generation working
- [ ] Dashboard panels created

**Testing & Validation**:
- [ ] Unit tests (60+) all passing
- [ ] Integration tests (20+) all passing
- [ ] Edge case tests (15+) all passing
- [ ] Performance tests meeting targets
- [ ] Full test suite passes

**Documentation & Deployment**:
- [ ] API documentation complete
- [ ] Usage examples provided
- [ ] Troubleshooting guide written
- [ ] Dashboard configured
- [ ] PR ready for merge

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-11 | Initial design document (Stage 0 complete) |

**Status**: ✅ Complete and ready for Phase 2 implementation.

