<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Copyright (C) 2026 ProtocolWarden -->

# Coverage Alerting Usage Examples

**Version**: 1.0  
**Last Updated**: 2026-06-12

## Basic Usage

### Setting Up Coverage Thresholds

```python
from operations_center.observer import CoverageAlertConfig

# Create configuration with default thresholds
config = CoverageAlertConfig(
    minimum_threshold_pct=80.0,
    warning_threshold_pct=85.0,
    target_threshold_pct=90.0
)

# With custom coverage-type specifics
config = CoverageAlertConfig(
    minimum_threshold_pct=80.0,
    statement_minimum=75.0,     # Easier to achieve
    branch_minimum=65.0,        # Stricter (fewer branches)
    line_minimum=75.0
)
```

### Collecting Coverage Metrics

```python
from operations_center.observer import CoverageCollector, ObserverContext

# Collector extracts metrics from test output
collector = CoverageCollector()
context = ObserverContext(...)

# Collect coverage from test run
coverage_signal = collector.collect(context)

print(f"Overall coverage: {coverage_signal.total_coverage_pct}%")
print(f"Statement: {coverage_signal.statement_coverage_pct}%")
print(f"Branch: {coverage_signal.branch_coverage_pct}%")

# Check collection status
if coverage_signal.status == "measured":
    print("✓ Coverage data collected successfully")
elif coverage_signal.status == "partial":
    print("⚠ Partial coverage data (some files missing)")
else:  # unavailable
    print("✗ Coverage tool failed")
```

### Storing Historical Data

```python
from operations_center.observer import (
    CoverageTrendManager,
    CoverageMetricsSnapshot
)
from datetime import datetime, timezone

# Create manager with local storage
manager = CoverageTrendManager.create_local(base_path=".coverage_data")

# Create snapshot from current measurement
snapshot = CoverageMetricsSnapshot(
    timestamp=datetime.now(timezone.utc),
    run_id="abc123def456",  # Git commit SHA
    source="coverage.py",
    overall_statement_coverage_pct=85.2,
    overall_branch_coverage_pct=72.5,
    overall_line_coverage_pct=85.2,
    module_coverages=[...],
    test_execution_time_ms=12500
)

# Store snapshot
manager.save_snapshot(snapshot)
print("✓ Snapshot stored")
```

---

## Trend Analysis

### Computing Trends

```python
from operations_center.observer import CoverageTrendManager
from datetime import datetime, timedelta, timezone

manager = CoverageTrendManager.create_local()

# Analyze 7-day trend for repository
trend = manager.compute_trend_analysis(
    metric_type="line",
    granularity="repository",
    window_days=7
)

print(f"Current coverage: {trend.current_value}%")
print(f"7-day average: {trend.average_value}%")
print(f"Trend direction: {trend.trend_direction}")
print(f"Trend slope: {trend.trend_pct}% per day")
print(f"Stability: {trend.stability_score * 100:.0f}%")

# Interpret results
if trend.trend_direction == "improving":
    print("✓ Coverage improving")
elif trend.trend_direction == "stable":
    print("→ Coverage stable")
else:  # degrading
    print("✗ Coverage degrading")
    if trend.projected_value_7days:
        print(f"  Projected: {trend.projected_value_7days}% in 7 days")
```

### Interpreting Trend Metrics

```python
# Example trend analysis results
trend = manager.compute_trend_analysis("line", "repository", window_days=7)

# Metrics to watch:
print(f"trend_direction: {trend.trend_direction}")
# Values: "improving" (slope > +0.5%), "stable" (-0.5% to +0.5%), "degrading" (< -0.5%)

print(f"trend_pct: {trend.trend_pct}% per day")
# Negative = declining coverage
# Example: -0.7% per day means coverage drops 0.7% each day

print(f"regression_count: {trend.regression_count}")
# Number of day-to-day drops >= threshold
# High count = unstable coverage

print(f"stability_score: {trend.stability_score}")
# 0-1 score: 1.0 = perfectly stable, 0.0 = highly volatile
# < 0.8 = suppress trend alerts (too noisy)

print(f"projected_value_7days: {trend.projected_value_7days}%")
# Estimated coverage in 7 days based on current slope
# Use for forward-looking decisions
```

### Responding to Trends

**If trend is degrading**:

```python
if trend.trend_direction == "degrading":
    # Determine urgency
    if trend.trend_pct < -2.0:
        severity = "URGENT"  # Declining fast
        action = "Pause new features, focus on test coverage"
    elif trend.trend_pct < -1.0:
        severity = "HIGH"
        action = "Increase test writing, review recent changes"
    else:
        severity = "MEDIUM"
        action = "Monitor, prepare improvement plan"
    
    print(f"{severity}: Coverage declining {trend.trend_pct}% per day")
    print(f"Action: {action}")
    
    # Look at modules
    if trend.projection_7days < 70:  # Will drop below critical
        print(f"WARNING: Coverage will drop to {trend.projected_value_7days}% in 7 days")
        print("Recommend emergency coverage improvement initiative")
```

**If trend is stable**:

```python
if trend.trend_direction == "stable":
    print("✓ Coverage stable")
    
    # Check if at target
    if trend.current_value >= config.target_threshold_pct:
        print("✓ Coverage at target level")
    elif trend.current_value >= config.minimum_threshold_pct:
        print("→ Coverage above minimum, but below target")
        print(f"  Gap to target: {config.target_threshold_pct - trend.current_value:.1f}%")
```

---

## Alert Generation and Routing

### Generating Alerts

```python
from operations_center.observer import (
    CoverageAlertManager,
    CoverageMetricsSnapshot,
    CoverageAlertConfig
)

manager = CoverageAlertManager()
config = CoverageAlertConfig(minimum_threshold_pct=80.0)

# Get latest snapshot and trends
snapshot = storage.get_latest_snapshot()
trend = trend_manager.compute_trend_analysis(
    "line", "repository", window_days=7
)

# Generate all applicable alerts
alerts = manager.generate_alerts(
    snapshot=snapshot,
    config=config,
    history=trend
)

print(f"Generated {len(alerts)} alerts:")
for alert in alerts:
    print(f"  [{alert.severity.upper()}] {alert.alert_type}: {alert.message}")
```

### Understanding Alert Types

```python
# Alert Type 1: Below Threshold
alert = CoverageAlert(
    alert_type="below_threshold",
    metric_type="line",
    current_value=78.5,
    threshold_or_baseline=80.0,
    delta_pct=-1.5,
    severity="warning",
    message="Line coverage (78.5%) fell below threshold (80%)"
)
# → Action: Add tests for uncovered code

# Alert Type 2: Regression Detected
alert = CoverageAlert(
    alert_type="regression_detected",
    metric_type="statement",
    current_value=82.1,
    threshold_or_baseline=85.0,
    delta_pct=-2.9,
    baseline_type="previous_run",
    severity="high",
    message="Statement coverage regressed: 85.0% → 82.1% (-2.9%)",
    affected_modules=["src/observer/new_feature.py"]
)
# → Action: Review PR, add tests for new code

# Alert Type 3: Trend Degrading
alert = CoverageAlert(
    alert_type="trend_degrading",
    metric_type="line",
    current_value=83.2,
    trend_velocity=-0.7,  # % per day
    days_of_decline=10,
    severity="medium",
    message="Coverage trending down for 10 days. At current rate, will drop to 81.5% in 7 days"
)
# → Action: Investigate recent changes, increase test emphasis

# Alert Type 4: Module Critical Gap
alert = CoverageAlert(
    alert_type="module_critical_gap",
    metric_type="statement",
    scope_id="src/operations_center/alert_channels.py",
    current_value=62.5,
    threshold_or_baseline=85.0,
    delta_pct=-22.5,
    severity="high",
    message="High-touch module has 22.5% coverage gap",
    affected_modules=["src/operations_center/alert_channels.py"]
)
# → Action: Target coverage improvement on this module
```

### Routing Alerts to Channels

```python
from operations_center.observer import CoverageAlertRouter

router = CoverageAlertRouter()

# Route alert to appropriate channels
results = router.route_alert(alert, config)

for result in results:
    if result.success:
        print(f"✓ Delivered to {result.channel_name}")
    else:
        print(f"✗ Failed to deliver to {result.channel_name}: {result.error_message}")

# Example output:
# ✓ Delivered to slack
# ✓ Delivered to email
# ✗ Failed to deliver to github: API rate limit exceeded
```

---

## Module-Level Analysis

### Analyzing Module Coverage

```python
from operations_center.observer import CoverageTrendManager

manager = CoverageTrendManager.create_local()

# Get coverage for specific module
module_trend = manager.compute_trend_analysis(
    metric_type="line",
    granularity="module",
    scope_id="src/operations_center/observer",
    window_days=7
)

print(f"Module: {module_trend.scope_id}")
print(f"Current coverage: {module_trend.current_value}%")
print(f"Trend: {module_trend.trend_direction}")

# Get module health status
snapshot = storage.get_latest_snapshot()
module = next(
    m for m in snapshot.module_coverages 
    if m.module_path == "src/operations_center/observer"
)

print(f"Health status: {module.health_status}")
if module.health_status == "critical":
    print("⚠ Module coverage is critical (<70%)")
    print(f"  Statements: {module.statement_count}")
    print(f"  Coverage: {module.statement_coverage_pct}%")
```

### Module Threshold Overrides

```python
# Configure module-specific thresholds
config = CoverageAlertConfig(
    minimum_threshold_pct=80.0,  # Repository default
    module_thresholds={
        "src/operations_center/observer": {
            "statement": 85.0,
            "branch": 80.0
        },
        "src/legacy/deprecated": {
            "statement": 50.0  # Relaxed for legacy
        }
    }
)

# Check effective threshold for module
threshold = config.get_threshold(
    metric_type="statement",
    granularity="module",
    scope_id="src/operations_center/observer"
)
# Returns: 85.0 (module override)

threshold = config.get_threshold(
    metric_type="statement",
    granularity="module",
    scope_id="src/other_module"
)
# Returns: 80.0 (repository default, no override)
```

---

## Integration Examples

### In Observer Service

```python
class RepoObserverService:
    
    async def observe(self, context: ObserverContext) -> RepoSignalsSnapshot:
        # Collect coverage
        coverage_signal = await self._collect_coverage(context)
        
        # Analyze trends
        if coverage_signal.status == "measured":
            trends = self.trend_manager.compute_trend_analysis(
                "line", "repository", window_days=7
            )
            
            # Generate alerts
            alerts = self.alert_manager.generate_alerts(
                snapshot=coverage_signal,
                config=self.config,
                history=trends
            )
            
            # Route alerts
            for alert in alerts:
                self.alert_router.route_alert(alert, self.config)
            
            # Include in signal
            coverage_signal.active_alerts = alerts
        
        return RepoSignalsSnapshot(
            coverage_signal=coverage_signal
        )
```

### In CI/CD Pipeline

```bash
#!/bin/bash
# .github/workflows/coverage-check.yml

- name: Check coverage thresholds
  run: |
    coverage-alerter check-thresholds
    # Exits with:
    # 0 = All thresholds met
    # 1 = Below-threshold alert
    # 2 = Regression detected
    # 127 = Coverage unavailable
    
- name: Report on alerts
  if: failure()
  run: |
    coverage-alerter report-alerts --format=json > alerts.json
    # Include in CI output for review
```

### In Dashboard

```python
# Dashboard panel for coverage alerts
def render_coverage_panel(snapshot: CoverageMetricsSnapshot):
    return {
        "title": "Coverage Status",
        "metrics": {
            "overall": f"{snapshot.total_coverage_pct}%",
            "statement": f"{snapshot.statement_coverage_pct}%",
            "branch": f"{snapshot.branch_coverage_pct}%",
            "line": f"{snapshot.line_coverage_pct}%",
        },
        "alerts": [
            {
                "type": alert.alert_type,
                "severity": alert.severity,
                "message": alert.message,
                "recommendation": alert.recommendation
            }
            for alert in snapshot.active_alerts
        ],
        "trend": {
            "direction": trend.trend_direction,
            "velocity": f"{trend.trend_pct}% per day",
            "projection_7days": f"{trend.projected_value_7days}%"
        }
    }
```

---

## Advanced Scenarios

### Handling Coverage Tool Unavailability

```python
# Coverage tool fails
signal = collector.collect(context)

if signal.status == "unavailable":
    print("⚠ Coverage tool unavailable")
    print("  Action: Check coverage tool logs")
    print("  Dashboard shows stale data from last run")
    
    # Don't generate alerts (no valid data)
    alerts = []
else:
    # Proceed with normal analysis
    alerts = manager.generate_alerts(signal, config, trends)
```

### Responding to Measurement Anomalies

```python
# Coverage suddenly drops (possible tool error)
previous = 85.0
current = 55.0
delta = current - previous  # -30%

# Check if anomaly
if abs(delta) > 20:
    print("⚠ Detected coverage anomaly: large sudden change")
    print("  Possible causes:")
    print("  1. Coverage tool version upgrade")
    print("  2. Test infrastructure issue")
    print("  3. Coverage data corruption")
    print("  Action: Investigate and re-run tests")
    
    # Alert with "investigate" recommendation
    alert.recommendation = "Verify coverage tool version and test execution"
```

### Managing Alert Fatigue

```python
# Too many alerts? Adjust configuration
if daily_alert_count > 20:
    print("Alert fatigue detected")
    print("Recommendations:")
    print("1. Increase regression threshold (2% → 3%)")
    print("2. Increase trend detection threshold (5 runs → 7 runs)")
    print("3. Route lower-severity alerts to weekly digest")
    print("4. Add module exceptions for known issues")
    
    # Example config adjustment
    config = CoverageAlertConfig(
        run_to_run_threshold_pct=3.0,  # Increased from 2.0
        min_consecutive_declining_runs=7  # Increased from 5
    )
```

---

## Troubleshooting Common Issues

### Coverage Data Not Being Collected

```python
# Check collector
signal = collector.collect(context)
print(f"Status: {signal.status}")

if signal.status == "unavailable":
    # Debug: Check coverage tool output file
    import os
    coverage_file = ".coverage"
    if os.path.exists(coverage_file):
        print("✓ Coverage file exists")
    else:
        print("✗ Coverage file missing")
        print("  Action: Run 'pytest --cov' to generate coverage")
```

### Alerts Not Being Routed

```python
# Check route configuration
routes = config.get_routes_for_alert(alert)
print(f"Matched routes: {routes}")

if not routes:
    print("✗ No matching routes")
    print(f"  Alert type: {alert.alert_type}")
    print(f"  Severity: {alert.severity}")
    print("  Action: Add route to coverage_alerting_config.yaml")
```

### High False Alert Rate

```python
# Trends too noisy
trend = manager.compute_trend_analysis("line", "repository")
print(f"Stability: {trend.stability_score}")

if trend.stability_score < 0.8:
    print("⚠ Coverage highly volatile")
    print("  Possible causes:")
    print("  1. Test flakiness (tests passing/failing non-deterministically)")
    print("  2. Dynamic code generation")
    print("  3. Coverage tool version issues")
    
    # Solutions
    print("  Solutions:")
    print("  1. Fix flaky tests")
    print("  2. Increase trend threshold from 5 to 7+ runs")
    print("  3. Verify coverage tool version")
```

---

**Usage Guide Version**: 1.0
