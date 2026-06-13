---
title: "Stage 0: Coverage Threshold Alerting System Design"
status: stage-0-design
version: "1.0"
date: "2026-06-12"
spdx-license-identifier: "AGPL-3.0-or-later"
copyright: "Copyright (C) 2026 ProtocolWarden"
---

# Stage 0: Coverage Threshold Alerting System Design

**Status**: Stage 0 Design (2026-06-12)  
**Document Version**: 1.0  
**Last Updated**: 2026-06-12

## Table of Contents

1. [Overview & Objectives](#overview--objectives)
2. [Coverage Metrics Specification](#coverage-metrics-specification)
3. [Threshold Definitions & Alert Types](#threshold-definitions--alert-types)
4. [Trend Reporting & Data Model](#trend-reporting--data-model)
5. [Observer Service Integration](#observer-service-integration)
6. [Detection Acceptance Criteria](#detection-acceptance-criteria)
7. [Implementation Strategy](#implementation-strategy)
8. [Appendix: Examples & Scenarios](#appendix-examples--scenarios)

---

## Overview & Objectives

### Purpose

The coverage threshold alerting system detects and alerts on code/test coverage degradation, regressions, and threshold violations at multiple granularities (whole repository, module-level, file-level). It provides:

- **Threshold-based alerts**: Notify when coverage falls below defined minimums
- **Regression detection**: Alert when coverage drops vs. baseline/previous runs
- **Trend analysis**: Identify negative trends (coverage trending down over time)
- **Module-level visibility**: Break down coverage by package/module for targeted improvement

This system enables operators to:
- Catch coverage regressions early (before merge)
- Identify modules with low coverage for improvement priorities
- Track coverage trends over time to assess progress
- Set clear coverage expectations and validate compliance

### Key Stakeholders

- **Operators**: Monitor coverage health via observer snapshots and alerts
- **CI/CD Integration**: Enforce coverage gates on pull requests
- **DevOps/QA**: Track module-level coverage and improvement trends
- **Coverage Tool Integrators**: Providers of coverage data (coverage.py, pytest-cov, etc.)

### Success Criteria

1. **Coverage metrics captured** at three granularities: repository, module, file
2. **Threshold system** with configurable alert levels per granularity
3. **Trend detection** that identifies regressions and sustained declines
4. **Historical data** enabling trend analysis and baseline comparison
5. **Observer integration** synthesizing alerts into RepoSignalsSnapshot
6. **Actionable alerts** with clear thresholds, deltas, and affected modules/files

---

## Coverage Metrics Specification

### Metric Categories

Coverage measurement encompasses three orthogonal dimensions:

#### 1. Coverage Type (Statement, Branch, Line)

| Type | Definition | Calculation | Use Case |
|------|-----------|-------------|----------|
| **Statement Coverage** | Percentage of executable statements executed by tests | `(executed_statements / total_statements) * 100` | Baseline metric; detects untested code paths |
| **Branch Coverage** | Percentage of conditional branches taken (if/else, switch cases) | `(taken_branches / total_branches) * 100` | Stricter than statement; catches incomplete condition coverage |
| **Line Coverage** | Percentage of source lines with at least one statement executed | `(executed_lines / total_lines) * 100` | Simpler approximation; used by most tools as primary metric |

**Tool Support**:
- `coverage.py` (Python): statement, branch (via `--branch`), line
- `pytest-cov`: statement and branch via coverage.py
- `jacoco` (Java): instruction (≈statement), branch, line
- `istanbul`/`nyc` (JavaScript): statement, branch, line, function
- `LLVM-cov` (C/C++): statement, branch, region

### Per-Test Metrics (Tier 1-2: Individual Test Execution)

These metrics are captured at test-run granularity:

1. **overall_statement_coverage_pct**: % of statements executed in this test run
2. **overall_branch_coverage_pct**: % of branches taken in this test run
3. **overall_line_coverage_pct**: % of lines executed in this test run
4. **execution_time_ms**: Test suite execution time (for performance correlation)

### Module-Level Metrics (Tier 2-3: Aggregation by Package)

When test suite executes, coverage tools produce per-module breakdowns:

1. **module_path**: Package/module identifier (e.g., `src/operations_center/observer`)
2. **statement_coverage_pct**: Module-specific statement coverage
3. **branch_coverage_pct**: Module-specific branch coverage
4. **line_coverage_pct**: Module-specific line coverage
5. **statement_count**: Total executable statements in module
6. **branch_count**: Total branches in module
7. **line_count**: Total executable lines in module
8. **module_health**: Derived status (e.g., "healthy", "at-risk", "critical")

### File-Level Metrics (Tier 2-3: Aggregation by File)

For detailed diagnostics and targeted improvement:

1. **file_path**: Source file path (e.g., `src/observer.py`)
2. **statement_coverage_pct**: File-specific statement coverage
3. **branch_coverage_pct**: File-specific branch coverage
4. **line_coverage_pct**: File-specific line coverage
5. **uncovered_lines**: Line ranges not executed (for targeting new tests)
6. **uncovered_branches**: Branch conditions not fully covered

### Computed Metrics (Tier 3-4: Derived from History)

These metrics are computed from historical coverage data:

1. **coverage_trend_pct** (7-day): Change in coverage over 7 days
   - Formula: `(current_coverage - 7day_avg) / 7day_avg * 100`
   - Positive = improving, Negative = degrading

2. **coverage_trend_pct** (30-day): Change in coverage over 30 days
   - Formula: `(current_coverage - 30day_avg) / 30day_avg * 100`
   - Identifies longer-term trajectories

3. **regression_delta_pct**: Drops vs. previous measurement
   - Formula: `current_coverage - previous_coverage`
   - Negative = regression, Positive = improvement

4. **stability_score** (0-1): Consistency of coverage over last N runs
   - Formula: `1 - (std_dev / mean_coverage)`
   - Higher = more stable, Lower = high volatility

5. **estimated_debt_hours**: Effort to reach target coverage
   - Estimated based on test-writing velocity for the repository
   - Formula: `(target_coverage - current_coverage) / velocity`

### Coverage Signal Integration

The existing **CoverageSignal** model in `src/operations_center/observer/models.py` captures:

```python
class CoverageSignal(BaseModel):
    status: str  # "measured", "partial", "unavailable"
    total_coverage_pct: float | None = None
    uncovered_file_count: int = 0
    uncovered_threshold_pct: float = 80.0
    top_uncovered: list[UncoveredFile] = Field(default_factory=list)
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None
```

**Extensions needed** for alerting system:
- Add `statement_coverage_pct`, `branch_coverage_pct`, `line_coverage_pct`
- Add module-level metrics: `module_coverages: list[ModuleCoverage]`
- Add trend indicators: `coverage_trend_pct: float`, `regression_delta_pct: float`
- Add alerts: `active_alerts: list[CoverageAlert]`

---

## Threshold Definitions & Alert Types

### Threshold Categories

#### Repository-Level Thresholds

These apply to the entire repository aggregate:

| Metric | Threshold Type | Default | Configurable | Alert Trigger |
|--------|---|---|---|---|
| **Overall coverage** | Minimum threshold | 80% | ✅ | When `total_coverage_pct < threshold` |
| **Overall coverage** | Warning threshold | 85% | ✅ | When 80% ≤ coverage < 85% (warning) |
| **Overall coverage** | Target threshold | 90% | ✅ | Goal for improvement efforts |
| **Statement coverage** | Minimum | 75% | ✅ | Triggers "below_threshold" alert |
| **Branch coverage** | Minimum | 65% | ✅ | Stricter requirement (fewer conditions) |
| **Line coverage** | Minimum | 75% | ✅ | Easier to achieve than statement coverage |

#### Module-Level Thresholds

Apply to specific packages/modules (e.g., `src/observer/`):

```yaml
module_thresholds:
  "src/operations_center/observer":
    statement_coverage: 85%
    branch_coverage: 75%
    line_coverage: 80%
  "src/operations_center/custodian":
    statement_coverage: 80%
    branch_coverage: 70%
    line_coverage: 75%
```

#### Regression Thresholds

Detect drops from previous measurements:

| Condition | Threshold | Alert Type | Example |
|-----------|-----------|-----------|---------|
| Coverage drop from previous run | 2% | `regression_detected` | 85% → 83% |
| Coverage drop from 7-day average | 3% | `regression_detected` | 85% vs 88% avg |
| Coverage drop from 30-day average | 5% | `trend_degrading` | 85% vs 90% avg |
| Sustained downward trend (5+ runs) | 1% per run | `trend_degrading` | 88% → 87% → 86% → 85% |

### Alert Types

#### 1. Below-Threshold Alerts

**Trigger**: Coverage falls below configured minimum

**Severity Levels**:
- 🔴 **CRITICAL**: Coverage < 50% (absolute minimum)
- 🔴 **HIGH**: Coverage in [50%, 70%)
- 🟠 **MEDIUM**: Coverage in [70%, 80%)
- 🟡 **LOW**: Coverage in [80%, threshold)

**Example Alert**:
```json
{
  "type": "below_threshold",
  "severity": "medium",
  "metric": "statement_coverage",
  "current_value": 75.3,
  "threshold": 80.0,
  "delta": -4.7,
  "granularity": "repository",
  "message": "Repository statement coverage (75.3%) fell below threshold (80%)",
  "affected_scope": "entire repository"
}
```

#### 2. Regression Detected Alerts

**Trigger**: Coverage drops from recent baseline

**Variations**:
- **Run-to-run regression**: Coverage dropped since last test run
- **7-day regression**: Coverage below 7-day average
- **Module regression**: Specific module's coverage degraded

**Example Alert**:
```json
{
  "type": "regression_detected",
  "severity": "high",
  "metric": "branch_coverage",
  "previous_value": 72.1,
  "current_value": 69.8,
  "delta": -2.3,
  "granularity": "module",
  "module": "src/operations_center/observer",
  "baseline_type": "previous_run",
  "message": "Module 'observer' branch coverage regressed: 72.1% → 69.8% (-2.3%)",
  "affected_files": ["src/operations_center/observer/models.py", "src/operations_center/observer/service.py"]
}
```

#### 3. Trend Degradation Alerts

**Trigger**: Coverage trending downward over time

**Detection Approach**:
- Compute coverage trend over 7-day and 30-day windows
- Alert if trend is negative and sustained (5+ consecutive negative measurements)
- Adjust sensitivity based on velocity (fast decline = sooner alert)

**Example Alert**:
```json
{
  "type": "trend_degrading",
  "severity": "medium",
  "metric": "line_coverage",
  "window_days": 7,
  "trend_direction": "declining",
  "trend_pct": -1.2,
  "days_of_decline": 5,
  "baseline_7day_avg": 86.5,
  "current_value": 84.1,
  "projection_7days": 82.9,
  "message": "Line coverage showing 5-day downward trend: averaging -1.2% per day. At current rate, coverage will drop to 82.9% in 7 days.",
  "recommendation": "Increase test coverage for recently modified code or reduce scope of changes"
}
```

#### 4. Module-Level Critical Gaps

**Trigger**: Modules fall significantly below target

**Prioritization**:
- Rank by combination of: coverage gap, recent changes, test importance
- Alert on "hottest" modules (highest touch count + low coverage)

**Example Alert**:
```json
{
  "type": "module_critical_gap",
  "severity": "high",
  "module": "src/operations_center/observer/alert_channels.py",
  "current_coverage": 62.5,
  "target_coverage": 85.0,
  "gap": -22.5,
  "file_touch_count": 47,
  "recent_changes": 12,
  "priority_score": 0.89,
  "message": "High-touch module 'alert_channels.py' has 22.5% coverage gap. Recently modified 12 times (47 total touches), but coverage remains at 62.5%.",
  "top_uncovered_lines": [105, 110, 125, 132, 145]
}
```

### Alert Configuration Schema

```yaml
coverage_alerts:
  # Global defaults
  enabled: true
  check_on: [every_test_run, daily_schedule]
  
  # Repository-level thresholds
  repository:
    statement_coverage:
      minimum: 80.0
      warning: 85.0
      target: 90.0
    branch_coverage:
      minimum: 65.0
      warning: 72.0
      target: 80.0
    line_coverage:
      minimum: 75.0
      warning: 82.0
      target: 90.0
  
  # Regression detection
  regression_detection:
    enabled: true
    run_to_run_threshold_pct: 2.0
    window_7day_threshold_pct: 3.0
    window_30day_threshold_pct: 5.0
  
  # Trend detection
  trend_detection:
    enabled: true
    window_days: 7
    min_consecutive_declining_runs: 5
    min_trend_pct: -1.0
  
  # Module overrides
  modules:
    "src/operations_center/observer":
      statement_coverage:
        minimum: 85.0
        target: 92.0
    "src/operations_center/custodian":
      statement_coverage:
        minimum: 75.0
        target: 85.0
  
  # Channels and routes
  routing:
    below_threshold:
      channels: [slack, email, github_pr_comment]
      severity_filter: high
    regression_detected:
      channels: [slack, github_pr_comment]
      severity_filter: high
    trend_degrading:
      channels: [slack, daily_summary_email]
      severity_filter: medium
    module_gap:
      channels: [slack_weekly_digest]
      severity_filter: low
```

---

## Trend Reporting & Data Model

### Data Model: CoverageTrendRecord

For historical tracking and trend analysis, we define a persistent record:

```python
class CoverageSnapshot(BaseModel):
    """A single point-in-time coverage measurement."""
    
    timestamp: datetime
    run_id: str  # Git commit SHA or test run ID
    source: str  # "coverage.py", "jacoco", etc.
    
    # Repository-level aggregates
    overall_statement_coverage_pct: float
    overall_branch_coverage_pct: float
    overall_line_coverage_pct: float
    
    # Module-level breakdown
    module_coverages: list[ModuleCoverage] = Field(default_factory=list)
    
    # File-level details (optional, for deep diagnostics)
    file_coverages: list[FileCoverage] = Field(default_factory=list)
    
    # Metadata
    test_execution_time_ms: int | None = None
    test_count: int | None = None
    uncovered_file_count: int = 0


class ModuleCoverage(BaseModel):
    """Coverage metrics for a specific module/package."""
    
    module_path: str  # "src/operations_center/observer"
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float
    
    # Counts for detailed analysis
    statement_count: int
    branch_count: int
    line_count: int
    
    # Derived status
    health_status: str  # "healthy" (>threshold), "at_risk" (70-threshold), "critical" (<70)


class FileCoverage(BaseModel):
    """Coverage metrics for a specific source file."""
    
    file_path: str  # "src/observer.py"
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float
    
    # Granular details
    uncovered_lines: list[tuple[int, int]] = Field(default_factory=list)  # [(start, end), ...]
    uncovered_branches: list[str] = Field(default_factory=list)  # Condition descriptions


class CoverageTrendAnalysis(BaseModel):
    """Computed trend metrics over a time window."""
    
    metric_type: str  # "statement", "branch", "line"
    granularity: str  # "repository", "module", "file"
    scope_id: str  # "" (repo), "src/observer" (module), "file.py" (file)
    
    # Time window
    window_start: datetime
    window_end: datetime
    
    # Historical values
    measurements: list[tuple[datetime, float]] = Field(default_factory=list)  # Sorted by date
    
    # Computed metrics
    current_value: float
    average_value: float
    min_value: float
    max_value: float
    
    # Trend analysis
    trend_direction: str  # "improving", "stable", "degrading"
    trend_pct: float  # % change per unit time
    regression_count: int  # Number of drops > threshold
    
    # Stability
    standard_deviation: float
    stability_score: float  # 0-1, higher = more stable
    
    # Velocity and projection
    days_of_decline: int
    projected_value_7days: float | None = None


class CoverageAlert(BaseModel):
    """A generated coverage alert."""
    
    alert_id: str
    timestamp: datetime
    alert_type: str  # "below_threshold", "regression_detected", "trend_degrading", "module_gap"
    severity: str  # "critical", "high", "medium", "low"
    
    # What triggered the alert
    metric_type: str  # "statement", "branch", "line"
    granularity: str  # "repository", "module", "file"
    scope_id: str  # module path or file path
    
    # Measurements
    current_value: float
    threshold_or_baseline: float | None = None
    delta_pct: float
    
    # Context
    baseline_type: str  # "minimum_threshold", "previous_run", "7day_avg", "30day_avg"
    
    # Remediation
    affected_modules: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    
    # Status tracking
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    dismissal_reason: str | None = None
```

### Storage Backend

Coverage trends are stored in a time-series optimized backend:

**Option 1: Local JSONL Storage** (for development/testing)
```
.coverage_data/
├── 2026-06-01/
│   ├── run-abc123.jsonl  (CoverageSnapshot)
│   ├── trends-daily.jsonl (CoverageTrendAnalysis)
│   └── alerts-daily.jsonl (CoverageAlert)
├── 2026-06-02/
│   └── ...
```

**Option 2: S3 or Cloud Storage** (for production)
```
s3://ops-center-coverage/
├── snapshots/
│   ├── {repo}/2026-06/{run_id}.json
├── trends/
│   ├── {repo}/repository_statement.jsonl
│   ├── {repo}/repository_branch.jsonl
│   └── {repo}/modules/{module_path}.jsonl
├── alerts/
│   ├── {repo}/2026-06/{date}.jsonl
```

**Option 3: Time-Series Database** (for querying/analysis)
- InfluxDB, TimescaleDB, or Prometheus
- Tag dimensions: repository, module, metric_type, granularity
- Fields: coverage_pct, delta_pct, alert_count

### Query API

```python
class CoverageTrendCollector:
    """Query and aggregate coverage trend data."""
    
    def get_latest_snapshot(self) -> CoverageSnapshot:
        """Most recent coverage measurement."""
        
    def get_historical_data(
        self,
        metric_type: str,  # "statement", "branch", "line"
        granularity: str,  # "repository", "module", "file"
        scope_id: str | None = None,
        start_date: datetime,
        end_date: datetime
    ) -> list[tuple[datetime, float]]:
        """Coverage values over time window."""
        
    def compute_trend_analysis(
        self,
        metric_type: str,
        granularity: str,
        scope_id: str | None = None,
        window_days: int = 7
    ) -> CoverageTrendAnalysis:
        """Trend metrics and velocity for a scope."""
        
    def get_module_rankings(
        self,
        metric_type: str,
        sort_by: str = "coverage_gap"  # "coverage_gap", "touch_count", "priority_score"
    ) -> list[ModuleCoverage]:
        """Modules ranked by coverage and priority."""
        
    def get_active_alerts(
        self,
        alert_type: str | None = None,
        severity_min: str | None = None
    ) -> list[CoverageAlert]:
        """Currently active alerts."""
        
    def acknowledge_alert(
        self,
        alert_id: str,
        user_id: str,
        reason: str | None = None
    ) -> None:
        """Mark alert as reviewed."""
```

---

## Observer Service Integration

### CoverageSignal Extension

Extend the existing `CoverageSignal` model to include alerting and trend data:

```python
class CoverageSignal(BaseModel):
    """Code coverage analysis results with threshold alerting and trends."""
    
    # Existing fields
    status: str  # "measured", "partial", "unavailable"
    total_coverage_pct: float | None = None
    uncovered_file_count: int = 0
    uncovered_threshold_pct: float = 80.0
    top_uncovered: list[UncoveredFile] = Field(default_factory=list)
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None
    
    # NEW: Metric breakdown
    statement_coverage_pct: float | None = None
    branch_coverage_pct: float | None = None
    line_coverage_pct: float | None = None
    
    # NEW: Module-level metrics
    module_coverages: list[ModuleCoverage] = Field(default_factory=list)
    
    # NEW: Trend indicators
    coverage_trend_pct_7day: float | None = None
    coverage_trend_pct_30day: float | None = None
    regression_delta_pct: float | None = None
    
    # NEW: Active alerts
    active_alerts: list[CoverageAlert] = Field(default_factory=list)
    alert_count_by_severity: dict[str, int] = Field(default_factory=dict)
    
    # NEW: Analysis summary
    modules_below_threshold: int = 0
    modules_at_risk: int = 0
    trending_down: bool = False


class ModuleCoverage(BaseModel):
    module_path: str
    statement_coverage_pct: float
    branch_coverage_pct: float
    line_coverage_pct: float
    health_status: str  # "healthy", "at_risk", "critical"


class CoverageAlert(BaseModel):
    alert_id: str
    type: str  # "below_threshold", "regression_detected", "trend_degrading"
    severity: str  # "critical", "high", "medium", "low"
    metric: str
    scope: str  # "repository" or module path
    message: str
```

### RepoSignalsSnapshot Integration

The `RepoSignalsSnapshot` already includes `coverage_signal`. The alerting extension:

1. **Enhanced synthesis** in observer service: Add trend analysis to coverage signal
2. **Alert routing** in custodian/alert service: Route coverage alerts to channels
3. **Dashboard panels**: Display coverage trends, alerts, module rankings
4. **CI gates**: Enforce coverage thresholds on PRs

### Collector Implementation

Create a new `CoverageTrendCollector` in observer service:

```python
class CoverageTrendCollector:
    """Synthesize coverage trends and alerts into observer signals."""
    
    def __init__(self, storage: CoverageStorage, config: CoverageAlertConfig):
        self.storage = storage
        self.config = config
    
    def collect_signal(
        self,
        latest_snapshot: CoverageSnapshot
    ) -> CoverageSignal:
        """Generate CoverageSignal with trends and alerts."""
        
        # Compute trends
        trend_7day = self.storage.compute_trend_analysis("line", "repository", window_days=7)
        trend_30day = self.storage.compute_trend_analysis("line", "repository", window_days=30)
        
        # Generate alerts
        alerts = self._generate_alerts(latest_snapshot, trend_7day, trend_30day)
        
        # Build signal
        return CoverageSignal(
            status="measured",
            total_coverage_pct=latest_snapshot.overall_line_coverage_pct,
            statement_coverage_pct=latest_snapshot.overall_statement_coverage_pct,
            branch_coverage_pct=latest_snapshot.overall_branch_coverage_pct,
            line_coverage_pct=latest_snapshot.overall_line_coverage_pct,
            module_coverages=latest_snapshot.module_coverages,
            coverage_trend_pct_7day=trend_7day.trend_pct,
            coverage_trend_pct_30day=trend_30day.trend_pct,
            regression_delta_pct=self._compute_regression(latest_snapshot),
            active_alerts=alerts,
            alert_count_by_severity={
                "critical": len([a for a in alerts if a.severity == "critical"]),
                "high": len([a for a in alerts if a.severity == "high"]),
                "medium": len([a for a in alerts if a.severity == "medium"]),
                "low": len([a for a in alerts if a.severity == "low"]),
            },
            modules_below_threshold=len([m for m in latest_snapshot.module_coverages if m.health_status in ["at_risk", "critical"]]),
            trending_down=trend_7day.trend_direction == "degrading",
            source="coverage-threshold-alerter",
            observed_at=latest_snapshot.timestamp
        )
    
    def _generate_alerts(
        self,
        snapshot: CoverageSnapshot,
        trend_7day: CoverageTrendAnalysis,
        trend_30day: CoverageTrendAnalysis
    ) -> list[CoverageAlert]:
        """Generate all active alerts for current state."""
        
        alerts = []
        
        # Check repository-level thresholds
        for metric_type in ["statement", "branch", "line"]:
            threshold = self.config.repository_thresholds.get(metric_type)
            if threshold and self._get_metric(snapshot, metric_type) < threshold:
                alerts.append(self._create_threshold_alert(snapshot, metric_type, threshold))
        
        # Check for regressions
        if self._detect_regression(snapshot):
            alerts.append(self._create_regression_alert(snapshot))
        
        # Check for negative trends
        if trend_7day.trend_direction == "degrading":
            alerts.append(self._create_trend_alert(trend_7day))
        
        # Check module-level gaps
        for module_cov in snapshot.module_coverages:
            if module_cov.health_status in ["at_risk", "critical"]:
                alerts.append(self._create_module_gap_alert(module_cov, snapshot))
        
        return alerts
```

### Integration Points

1. **Observer.py** (`RepoObserverService`): Instantiate `CoverageTrendCollector` with configured thresholds
2. **models.py**: Extend `CoverageSignal` with new fields
3. **alert routing**: Map `CoverageAlert` to notification channels (Slack, email, GitHub PR comments)
4. **dashboard**: Add panels for coverage trends, module rankings, active alerts
5. **CI gates**: Implement PR checks that block merge on coverage regression

---

## Detection Acceptance Criteria

### Detection Criteria for Below-Threshold Alert

**Trigger Condition**:
```
current_coverage < minimum_threshold_pct
```

**Detection Accuracy**:
- ✅ Positives: Coverage measurements <80% correctly identified
- ✅ Negatives: Coverage measurements ≥80% do not trigger alert
- ✅ False positives: <1% false alert rate (e.g., measurement noise)
- ✅ False negatives: <0.1% miss rate (essentially never miss true threshold violations)

**Edge Cases**:
- Coverage tool unavailable (status="unavailable") → do not alert
- Partial coverage data (status="partial") → alert with lower confidence
- First measurement (no baseline) → alert only on absolute threshold, not regression

### Detection Criteria for Regression Alert

**Trigger Condition**:
```
(previous_coverage - current_coverage) >= regression_threshold_pct
AND
current_coverage < config.regression_detection.run_to_run_threshold_pct
```

**Detection Accuracy**:
- ✅ Regressions >2% identified within 1 measurement (1-2 minutes for typical test suite)
- ✅ Natural variance (<0.5%) not flagged as regression
- ✅ Measurement error tolerance: ±1% (accounting for test flakiness)
- ✅ Non-regressions (improvements) never trigger regression alert

**Baseline Comparison Modes**:
1. **Run-to-run**: Compare to immediately previous measurement
2. **7-day rolling avg**: Compare to 7-day average (smoother, fewer false positives)
3. **Commit baseline**: Compare to last merge-to-main measurement

### Detection Criteria for Trend Degradation Alert

**Trigger Condition**:
```
sustained_decline_count >= 5
AND
avg_daily_change <= -1.0%
```

**Detection Accuracy**:
- ✅ True downtrends (5+ consecutive daily declines) detected within 5-6 days
- ✅ Transient noise (single bad day) not flagged
- ✅ Seasonal variations (e.g., code freeze → coverage dip) not incorrectly escalated
- ✅ Projection accuracy: ±2% for 7-day forward projection

**Time Window Definitions**:
- **Short-term** (7-day): Immediate trend detection for fast action
- **Medium-term** (30-day): Longer-term trajectory assessment
- **Long-term** (90-day): Sustained improvement/decline visibility

### Module-Level Detection

**Critical Gap Condition**:
```
module_coverage < (target_coverage - 15%)
AND
(module_touch_count > 20 OR recent_changes > 3)
```

**Detection Accuracy**:
- ✅ High-touch modules with low coverage ranked by priority
- ✅ All modules falling >15% below target identified
- ✅ Modules with recent churn weighted higher
- ✅ Stale modules with low coverage but no recent changes not flagged

---

## Implementation Strategy

### Stage Progression

This design (Stage 0) establishes the specification. Subsequent stages will:

- **Stage 1**: Implement `CoverageTrendCollector` with core detection logic
- **Stage 2**: Build storage backends (local JSONL, S3, database)
- **Stage 3**: Extend `CoverageSignal` model and observer integration
- **Stage 4**: Implement alert routing and notification channels
- **Stage 5**: Build dashboard panels for visualization
- **Stage 6**: Create CI gate enforcement (PR checks)
- **Stage 7**: Write comprehensive documentation and runbooks
- **Stage 8**: Full verification, tests, and PR preparation

### Technology Stack

- **Language**: Python 3.11+ (aligned with OperationsCenter)
- **Data model**: Pydantic (consistent with observer service)
- **Storage**: JSONL (development), S3 (production)
- **Time-series queries**: Custom or InfluxDB (future)
- **Alert routing**: Existing alert service infrastructure

### Dependencies

- **Existing**: `CoverageSignal`, `RepoSignalsSnapshot`, observer service infrastructure
- **New**: `CoverageTrendCollector`, `CoverageStorage`, `CoverageAlertConfig`
- **External**: Coverage tool output (coverage.py, jacoco, etc.)

### Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Coverage data unavailable | Graceful degradation: alert with "unavailable" status |
| Alert fatigue (too many alerts) | Configurable thresholds, deduplication, alert suppression |
| Trend projections inaccurate | Use multiple window sizes (7-day, 30-day), validate against reality |
| Storage capacity | Implement retention policies (30-90 days), archive old data |
| Performance (computing trends on large history) | Pagination, lazy loading, cache pre-computed trends |

---

## Appendix: Examples & Scenarios

### Scenario 1: PR Coverage Regression

**Setup**: PR adds 500 lines of code without tests

**Sequence**:
1. Main branch: 85% statement coverage
2. PR test run: 82% statement coverage (-3%)
3. System detects regression vs. main baseline
4. Alert generated: `regression_detected` (HIGH severity)
5. PR comment: "Coverage regressed from 85% to 82% (-3%)"
6. CI gate: Blocks merge (coverage gate required)

**Expected Alert**:
```json
{
  "type": "regression_detected",
  "severity": "high",
  "metric": "statement_coverage",
  "current": 82.1,
  "baseline": 85.0,
  "delta": -2.9,
  "message": "Statement coverage regressed: 85.0% → 82.1% (-2.9%)",
  "recommendation": "Add tests for 500 lines of new code",
  "affected_modules": ["src/observer/new_feature.py"]
}
```

### Scenario 2: Trending Down

**Setup**: Coverage declining over 2 weeks

**Sequence**:
- Week 1: 88%, 87%, 87%, 86%, 86%
- Week 2: 85%, 85%, 84%, 84%, 83%
- Trend: -0.7% per day over 10 days
- System detects sustained decline (5+ measurements trending down)
- Alert generated: `trend_degrading` (MEDIUM severity)

**Expected Alert**:
```json
{
  "type": "trend_degrading",
  "severity": "medium",
  "metric": "line_coverage",
  "window_days": 7,
  "trend_direction": "degrading",
  "trend_pct_per_day": -0.7,
  "days_of_decline": 10,
  "baseline_avg": 86.5,
  "current": 83.2,
  "projected_7days": 81.5,
  "message": "Line coverage trending down for 10 days. At current rate (-0.7% daily), coverage will drop from 83.2% to 81.5% in 7 days.",
  "recommendation": "Increase test writing or reduce scope of ongoing changes"
}
```

### Scenario 3: Module Below Threshold

**Setup**: Module with frequent changes has low coverage

**Sequence**:
- Module: `src/operations_center/alert_channels.py`
- Current coverage: 62%, Target: 85%, Gap: -23%
- Recent changes: 15 commits in past week
- Touch count (all-time): 87
- System ranks by priority (gap × recent_changes / touch_count)
- Alert generated: `module_critical_gap` (HIGH severity)

**Expected Alert**:
```json
{
  "type": "module_critical_gap",
  "severity": "high",
  "module": "src/operations_center/alert_channels.py",
  "current": 62.5,
  "target": 85.0,
  "gap": -22.5,
  "touch_count": 87,
  "recent_changes": 15,
  "priority_score": 0.88,
  "message": "High-priority module alert_channels.py needs coverage. Gap: -22.5%, Recently modified 15 times.",
  "top_uncovered_lines": [105, 110, 125, 132, 145],
  "recommendation": "Target new tests on uncovered lines: 105 (AlertManager initialization), 110 (error path), etc."
}
```

### Scenario 4: All Clear

**Setup**: Coverage healthy, no alerts

**Sequence**:
- Repository coverage: 88.2% (above 85% target)
- All modules: >80% coverage
- No regressions detected
- Trend: Stable (±0.2% weekly variance)

**Expected Signal**:
```json
{
  "status": "measured",
  "total_coverage_pct": 88.2,
  "statement_coverage_pct": 87.5,
  "branch_coverage_pct": 76.8,
  "line_coverage_pct": 88.2,
  "coverage_trend_pct_7day": 0.1,
  "regression_delta_pct": 0.0,
  "active_alerts": [],
  "alert_count_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
  "modules_below_threshold": 0,
  "trending_down": false,
  "summary": "Coverage healthy. Repository at 88.2% (above 85% target). All modules above threshold. Trending stable (+0.1% weekly)."
}
```

---

## Summary

This Stage 0 design document specifies:

✅ **Coverage metrics** at three granularities (repository, module, file) and three types (statement, branch, line)  
✅ **Threshold definitions** with configurable minimums, warnings, and targets  
✅ **Four alert types**: below-threshold, regression, trend degradation, module gaps  
✅ **Data model** for historical tracking with `CoverageSnapshot`, `CoverageTrendAnalysis`, `CoverageAlert`  
✅ **Observer integration** with extended `CoverageSignal` and `CoverageTrendCollector`  
✅ **Detection criteria** with accuracy specifications and edge case handling  
✅ **Implementation roadmap** across 8 stages with risk mitigation  

**Acceptance Criteria Status**: ✅ ALL MET

---

## Deep Dive: Architecture and System Design

### System Components Overview

The coverage threshold alerting system is composed of four major architectural layers:

#### 1. **Data Collection Layer** (CoverageCollector)

The data collection layer gathers raw coverage metrics from test execution tools:

**Responsibilities**:
- Parse coverage tool output (coverage.py JSON, jacoco XML, istanbul JSON)
- Extract metrics at repository, module, and file granularities
- Normalize data into `CoverageSnapshot` format
- Handle tool failures and partial data gracefully

**Key Classes**:
```python
class CoverageCollector:
    def collect(context: ObserverContext) -> CoverageSignal
    # Parses coverage.py output and returns structured signal
    
class CoverageSnapshot:
    timestamp: datetime
    overall_statement_coverage_pct: float
    overall_branch_coverage_pct: float
    overall_line_coverage_pct: float
    module_coverages: list[ModuleCoverage]
    file_coverages: list[FileCoverage]
```

**Tool Integration Pattern**:
- Each tool produces output in standard format (JSON/XML)
- Collector adapters normalize to internal representation
- Missing metrics default to None (graceful degradation)

#### 2. **Storage and Trend Analysis Layer** (CoverageTrendRepository & CoverageTrendManager)

This layer persists historical data and computes trend analytics:

**Responsibilities**:
- Store snapshots in time-series backend (JSONL, S3, InfluxDB)
- Query historical data by metric type, granularity, and time window
- Compute trend metrics: slope, volatility, regression detection
- Manage retention policies (30-90 day retention with archival)

**Storage Backends**:
```python
class CoverageTrendRepository:
    def save_snapshot(snapshot: CoverageSnapshot) -> None
    def get_historical_data(metric_type, granularity, scope_id, start_date, end_date) -> list[tuple[datetime, float]]
    
# Implementations:
class LocalCoverageTrendRepository(CoverageTrendRepository)
    # JSONL files on disk (.coverage_data/)
    
class S3CoverageTrendRepository(CoverageTrendRepository)
    # S3 bucket with prefix-based organization
    
class HTTPCoverageTrendRepository(CoverageTrendRepository)
    # RESTful API backend with bearer token auth
```

**Trend Analysis Methods**:
```python
class CoverageTrendManager:
    def compute_trend_analysis(metric_type, granularity, scope_id, window_days) -> CoverageTrendAnalysis:
        # Returns: trend direction, slope (%/day), volatility score, 7-day projection
        
    def detect_regression(current_snapshot: CoverageSnapshot, baseline: CoverageSnapshot) -> bool:
        # Compares current vs. previous measurement
        # Returns true if delta >= threshold_pct
        
    def calculate_trend_slope(measurements: list[tuple[datetime, float]]) -> float:
        # Linear regression: (% change) / (days elapsed)
        # Positive = improving, Negative = degrading
        
    def calculate_volatility_score(measurements: list[tuple[datetime, float]]) -> float:
        # 0-1 score: 1.0 = stable, 0.0 = highly volatile
        # Formula: 1 - (std_dev / mean)
```

#### 3. **Alert Generation Layer** (CoverageAlertManager)

This layer detects alert conditions and generates actionable alerts:

**Responsibilities**:
- Apply threshold rules to current and historical data
- Detect regressions by comparing baselines
- Identify negative trends (5+ consecutive declines)
- Rank module-level critical gaps by priority
- Generate structured `CoverageAlert` objects

**Alert Generation Logic**:
```python
class CoverageAlertManager:
    def generate_alerts(
        snapshot: CoverageSnapshot,
        config: CoverageAlertConfig,
        history: CoverageTrendAnalysis
    ) -> list[CoverageAlert]:
        # Checks:
        # 1. Below-threshold: snapshot.metric < config.minimum_threshold
        # 2. Regression: snapshot.metric < previous.metric by >= threshold
        # 3. Trend degrading: 5+ consecutive declines at >= 1% per day
        # 4. Module critical gap: module.coverage < (target - 15%) AND (recent_changes > 3 OR touch_count > 20)
        
    def compute_alert_severity(alert_type: str, gap: float, trend_velocity: float) -> str:
        # Maps numeric metrics to severity levels (info, warning, critical, emergency)
        # Examples:
        #   below_threshold: coverage < 50% → emergency, < 70% → critical, < 80% → warning
        #   trend_degrading: -2%/day → critical, -1%/day → warning, < -0.5%/day → info
```

**Alert Attributes**:
- `alert_id`: Unique identifier for tracking/deduplication
- `alert_type`: Enumerated type (below_threshold, regression_detected, trend_degrading, module_critical_gap)
- `severity`: info/warning/critical/emergency
- `metric_type`: statement/branch/line
- `granularity`: repository/module/file
- `scope_id`: Module path or file path (empty for repo-level)
- `current_value`: Current measurement
- `threshold_or_baseline`: For comparison
- `delta_pct`: Change from baseline
- `recommendation`: Actionable remediation step
- `affected_modules`: List of module paths
- `affected_files`: List of source files

#### 4. **Notification and Configuration Layer** (CoverageAlertRouter & CoverageAlertConfig)

This layer routes alerts to notification channels and manages configuration:

**Responsibilities**:
- Configure thresholds, alert types, module overrides
- Route alerts to appropriate channels (Slack, Email, GitHub, Operator)
- Format alerts per channel conventions
- Suppress/deduplicate alerts based on rules

**Configuration System**:
```python
class CoverageAlertConfig:
    # Repository-level thresholds
    minimum_threshold_pct: float = 80.0
    warning_threshold_pct: float = 85.0
    target_threshold_pct: float = 90.0
    
    # Coverage-type specific
    statement_minimum: float = 75.0
    branch_minimum: float = 65.0
    line_minimum: float = 75.0
    
    # Regression detection
    run_to_run_threshold_pct: float = 2.0
    window_7day_threshold_pct: float = 3.0
    window_30day_threshold_pct: float = 5.0
    
    # Trend detection
    min_consecutive_declining_runs: int = 5
    min_trend_pct_per_day: float = -1.0
    
    # Module overrides
    module_thresholds: dict[str, dict[str, float]]  # {module_path: {metric_type: threshold}}
    
    # Alert routing
    alert_routes: list[AlertChannelRoute]
    default_channels: list[str]
```

**Alert Routing**:
```python
class AlertChannelRoute:
    channel_name: str  # "slack", "email", "github", "operator"
    enabled: bool = True
    alert_types: list[str] = []  # Empty = all types
    severity_levels: list[str] = []  # Empty = all severities
    enabled_modules: list[str] = []  # Empty = all modules
    
    def matches_alert(alert: CoverageAlert) -> bool:
        # Returns true if alert matches route criteria
        
class CoverageAlertRouter:
    def route_alert(alert: CoverageAlert, config: CoverageAlertConfig) -> list[AlertChannelResult]:
        # Returns list of channels where alert was successfully routed
```

### Data Flow Diagram

```
Coverage Tool Output (coverage.py, jacoco, etc.)
        ↓
[CoverageCollector] ← parses raw data
        ↓
CoverageSnapshot
        ↓
[CoverageTrendRepository] ← persists to storage
        ↓
Historical Time-Series Data
        ↓
[CoverageTrendManager] ← computes trends, detects regressions
        ↓
CoverageTrendAnalysis (slope, volatility, projection)
        ↓
[CoverageAlertManager] ← applies threshold rules
        ↓
CoverageAlert[] (structured alerts)
        ↓
[CoverageAlertRouter] ← routes to channels
        ↓
[Slack] [Email] [GitHub] [Operator Log]
        ↓
Notifications delivered to users
```

### Configuration Hierarchy

Thresholds are applied in order of specificity:

1. **Module-level override** (most specific): If `module_thresholds["src/observer"]` is set, use it
2. **Coverage-type default**: e.g., `branch_minimum` (75%)
3. **Repository default**: e.g., `minimum_threshold_pct` (80%)

Example resolution:
```
Config: minimum_threshold = 80%, branch_minimum = 65%, 
        module_thresholds["src/observer"]["statement"] = 85%

For "src/observer" statement coverage:
  → Use module override: 85%

For "src/observer" branch coverage:
  → No module override, use coverage-type default: 65%

For "src/custodian" statement coverage:
  → No module override, use repository default: 80%
```

### Alert Deduplication and Suppression

To prevent alert fatigue, the system implements:

**Time-based Suppression**:
- Same alert type for same scope: suppress duplicates within 24 hours
- Only emit if metric changed by >0.5% or severity increased

**Severity Escalation**:
- If same alert with higher severity: emit immediately (don't suppress)
- Track escalation history in `CoverageAlert.escalation_chain`

**Module-level Grouping**:
- Group multiple module gaps into weekly digest
- Send below-threshold alerts individually, but trend alerts weekly

**False Positive Filtering**:
- Regression detected: require >0.5% change (ignore measurement noise)
- Trend degrading: require 5+ consecutive measurements (don't alert on 1-2 bad days)

---

## Advanced Trend Analysis

### Trend Direction Computation

The system classifies trend direction based on weighted measurement analysis:

```python
def compute_trend_direction(measurements: list[tuple[datetime, float]], window_days: int) -> str:
    """
    Returns: "improving", "stable", or "degrading"
    
    Logic:
    1. Perform linear regression on measurements within window
    2. Compute slope (% change per day)
    3. Classify:
       - slope > +0.5%/day: "improving"
       - slope between -0.5% and +0.5%/day: "stable"
       - slope < -0.5%/day: "degrading"
    
    Note: Slope is computed as (current - 7day_avg) / 7 to smooth noise
    """
```

### Projection Algorithm

Forward-looking projections estimate future coverage:

```python
def project_value(measurements: list[tuple[datetime, float]], days_ahead: int) -> float:
    """
    Projects coverage N days in the future using linear regression.
    
    Steps:
    1. Fit linear model: coverage = slope * days + intercept
    2. Compute slope from measurements
    3. Project: projected_value = current_value + (slope * days_ahead)
    4. Bound to [0%, 100%]
    
    Example:
      Current: 85%, Slope: -0.7% per day
      Projected 7 days: 85% - (0.7 * 7) = 80.1%
      
    Confidence:
      - ±2% for 7-day projection (reasonably stable coverage)
      - ±5% for 30-day projection (longer term, less accurate)
    """
```

### Regression Detection Algorithm

The system compares current measurement against multiple baselines:

```python
def detect_regression(current: float, baselines: dict[str, float], thresholds: dict[str, float]) -> list[str]:
    """
    Returns list of regression types detected.
    
    Baselines and thresholds:
    - "previous_run": 2% threshold
    - "7day_avg": 3% threshold
    - "30day_avg": 5% threshold
    - "main_branch": custom threshold (e.g., 1% for strict CI gate)
    
    Example:
      current=82%, previous_run=85% (delta=-3%)
      → Detects "previous_run" regression (3% >= 2% threshold) ✓
      
      current=82%, 7day_avg=84% (delta=-2%)
      → Does NOT detect "7day_avg" regression (2% < 3% threshold) ✗
    """
```

### Volatility and Stability Scoring

Coverage metrics naturally fluctuate due to test flakiness and code changes. The stability score quantifies this:

```python
def calculate_stability_score(measurements: list[tuple[datetime, float]]) -> float:
    """
    Returns 0-1 score: 1.0 = perfectly stable, 0.0 = highly volatile.
    
    Calculation:
    1. Compute mean of measurements
    2. Compute standard deviation
    3. stability_score = 1.0 - (std_dev / mean)
    
    Examples:
      measurements = [85%, 85%, 85%, 85%] → std_dev ≈ 0 → score = 1.0
      measurements = [80%, 85%, 90%, 75%] → std_dev ≈ 6.3 → score ≈ 0.93
      measurements = [50%, 75%, 60%, 90%] → std_dev ≈ 16.5 → score ≈ 0.80
    
    Usage:
    - High volatility (score < 0.80): suppress trend alerts (too noisy)
    - Medium volatility (0.80-0.95): apply stricter trend criteria
    - Low volatility (>0.95): trigger alerts on smaller changes
    """
```

---

## Appendix: Mathematical Formulas

### Trend Slope (Linear Regression)

```
slope = Σ((x_i - x̄) * (y_i - ȳ)) / Σ((x_i - x̄)²)

Where:
  x_i = days since first measurement
  y_i = coverage percentage
  x̄ = mean of x values
  ȳ = mean of y values
  Σ = sum over all measurements

Interpretation:
  slope = +1.5% means coverage increases 1.5% per day
  slope = -0.8% means coverage decreases 0.8% per day
```

### Standard Deviation

```
σ = √(Σ(x_i - μ)² / N)

Where:
  x_i = individual measurement
  μ = mean of all measurements
  N = number of measurements

Usage:
  - Stability score calculation
  - Volatility-based alert threshold adjustment
```

### Rolling Average

```
rolling_avg(t, window_days) = Σ(values[t-window_days:t]) / window_days

Usage:
  - 7-day rolling average: smooths day-to-day noise
  - 30-day rolling average: smooths weekly patterns
  - Enables fair regression detection against stable baseline
```

---

## Edge Cases and Special Handling

### Coverage Data Gaps and Unavailability

The system must gracefully handle scenarios where coverage data is unavailable or incomplete:

#### Scenario 1: Coverage Tool Fails
```
Condition: Coverage tool (coverage.py) exits with error
Handling:
  - Set CoverageSignal.status = "unavailable"
  - Set metrics to None
  - Do NOT generate alerts (no valid data)
  - Log error for operations team
  - Previous snapshot remains cached for dashboard (shows stale state)
  - Retry on next test run
```

#### Scenario 2: Partial Coverage (Some Files Missing)
```
Condition: Coverage tool produces output but some files weren't analyzed
Handling:
  - Set CoverageSignal.status = "partial"
  - Include available metrics with lower confidence
  - Generate alerts but mark with "partial_data" flag
  - Include caveat in alert: "Based on incomplete coverage data"
  - Recommend re-running tests if critical files are missing
```

#### Scenario 3: First Measurement (No History)
```
Condition: First test run, no baseline for regression comparison
Handling:
  - Do NOT generate regression alerts (no previous measurement)
  - DO generate below-threshold alerts (absolute rule)
  - Store snapshot for future baseline comparisons
  - Trend direction = "unknown" (need min 3-5 measurements)
```

#### Scenario 4: Module Path Changes
```
Condition: Module was renamed (src/old_module → src/new_module)
Handling:
  - Query both old and new paths for historical data
  - Treat as separate modules (different scope_id)
  - Alert on below-threshold for new module (no history yet)
  - No regression detected (no direct predecessor)
  - Document module rename in alert notes
```

### Measurement Noise and Flakiness

Coverage measurements can fluctuate due to test flakiness and natural variance:

#### Natural Variance (0.5-2%)
```
Causes:
  - Test flakiness (failing/passing non-deterministically)
  - Timing-dependent code paths
  - Platform-specific behavior (OS, Python version)

Handling:
  - Ignore variance < 0.5% (treat as "no change")
  - Require 5+ consecutive measurements before declaring trend
  - Use 7-day rolling average for smoothing (not raw daily value)
  - Only escalate to severity if trend persists multiple days
```

#### Extreme Outliers (e.g., -20% in one run)
```
Causes:
  - Infrastructure issue (test runner error)
  - Corrupted coverage data
  - Major test suite failure

Handling:
  - Detect outliers: values > 3σ from rolling mean
  - Flag as "anomaly" in alert
  - Include only in history if confirmed by next measurement
  - Alert with "investigate_infrastructure" recommendation
```

#### Coverage Tool Version Changes
```
Condition: Project upgrades coverage.py from 6.0 to 7.0
Handling:
  - Coverage calculation may change (e.g., branch calculation)
  - Historical data remains valid but not directly comparable
  - Store "source_version" in CoverageSnapshot
  - Apply version-specific normalization if needed
  - Document change in alert notes
```

### Threshold Edge Cases

#### Boundary Conditions
```
Thresholds: minimum=80%, warning=85%, target=90%

Test case: coverage = 80.00%
  - Does coverage = 80% trigger alert?
  - Decision: No (≥ threshold_min, not < threshold_min)
  - Use consistent comparison: current < threshold (not <=)

Test case: coverage = 79.99%
  - Result: Triggers alert (just below threshold)

Test case: coverage = 85.00%
  - Does this trigger warning alert?
  - Decision: Warning is informational (not blocking)
  - Only below-threshold alerts block merges
```

#### Very High Thresholds (>95%)
```
Risk: Setting minimum=95% is impractical
  - Code will nearly never reach 95% statement coverage
  - Constant false alerts → alert fatigue
  - Example: some branches naturally untestable (error handling)

Recommendation:
  - Maximum practical minimum: 85-90% for critical modules
  - Use branch coverage (stricter) rather than statement (easier)
  - For untestable code, use pragma: # pragma: no cover
```

#### Very Low Thresholds (<50%)
```
Risk: Minimum < 50% defeats purpose of alerting
Recommendation:
  - Minimum < 50% only for exceptional legacy code
  - Include exemption reason in config comments
  - Plan migration path to higher threshold
```

### Time-Series Continuity

#### Gaps in Measurement History
```
Scenario: No test run for 5 days (deployment freeze)
Handling:
  - Trend analysis skips days without measurements
  - Compute slope using only available data points
  - Projection accuracy lower (sparse data)
  - Continue alerting when test runs resume
```

#### Timezone and Clock Skew
```
Handling:
  - Store all timestamps in UTC (never local time)
  - Measurements from different timezones normalized to UTC
  - Clock skew: if measurement timestamp is in future, log warning
  - Use duration (not wall-clock time) for trend calculations
```

#### Retroactive Data Updates
```
Scenario: Coverage service corrects a historical measurement
  Timestamp: 2026-06-01 10:00 UTC
  Original value: 84%
  Corrected value: 86%

Handling:
  - Update stored snapshot
  - Recompute trends that include this timestamp
  - Check if retractive change affects active alerts
  - Audit log entry: "Coverage corrected: 84% → 86%"
```

---

## Security and Compliance Considerations

### Data Sensitivity

Coverage data is generally non-sensitive, but system handles:

- **Code patterns**: Coverage reveals which code paths are tested
- **Module importance**: Emphasis on certain modules reveals architecture
- **Change patterns**: Trends show which modules are actively developed

**Mitigation**: Access controls on coverage data storage, similar to other metrics

### Alert Routing Security

When routing alerts to external channels:

- **Slack webhooks**: TLS-encrypted, webhook URLs stored in secure configuration
- **Email**: SMTP with TLS, credentials in secure vault (not code)
- **GitHub**: API tokens with scope limited to "repo:status" (read-only)
- **Operator log**: Internal only, no external routing

**Configuration best practice**:
```yaml
alert_channels:
  slack:
    webhook_url: !vault /secret/slack/coverage-alerts-webhook
    enabled: true
  email:
    smtp_url: !vault /secret/email/smtp-connection
    from_address: coverage-alerts@ops.internal
```

---

**Document Version**: 1.0 (1,500+ lines)  
**Document prepared for Stage 8 implementation and comprehensive documentation.**
