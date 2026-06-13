---
title: "Coverage Threshold Alerting System: User Guide"
status: production-ready
version: "1.0"
date: "2026-06-12"
spdx-license-identifier: "AGPL-3.0-or-later"
copyright: "Copyright (C) 2026 ProtocolWarden"
---

# Coverage Threshold Alerting System: User Guide

**Version**: 1.0  
**Date**: 2026-06-12  
**Status**: Production-Ready  
**Author**: Operations Center Team  
**SPDX-License-Identifier**: Apache-2.0

## Table of Contents

1. [Introduction](#introduction)
2. [Architecture Overview](#architecture-overview)
3. [API Reference](#api-reference)
4. [Configuration Guide](#configuration-guide)
5. [Usage Examples](#usage-examples)
6. [Responding to Alerts](#responding-to-alerts)
7. [Troubleshooting Guide](#troubleshooting-guide)
8. [Integration Guide](#integration-guide)
9. [Best Practices](#best-practices)
10. [FAQ](#faq)

---

## Introduction

The **Coverage Threshold Alerting System** monitors code coverage metrics in real-time and generates alerts when coverage falls below configured thresholds, exhibits regressions, or shows degrading trends. This system provides:

- **Real-time Monitoring**: Tracks coverage changes at repository, module, and file levels
- **Intelligent Alerting**: Detects threshold violations, regressions, and trend degradation
- **Multi-Channel Notifications**: Slack, Email, GitHub PR comments, and operator logs
- **Flexible Configuration**: YAML-based thresholds with environment variable overrides
- **Historical Analysis**: Stores and analyzes trends to identify patterns
- **Dashboard Integration**: Visualizes coverage metrics and alerts

### Key Concepts

**Coverage Metrics**: Statement, Branch, and Line coverage percentages at repository/module/file granularities

**Thresholds**: Configurable targets for minimum acceptable coverage (default: 80% minimum, 90% target)

**Alerts**: Four types:
- **Below Threshold**: Coverage < minimum
- **Regression Detected**: Coverage dropped ≥2% vs. previous measurement
- **Trend Degrading**: 5+ consecutive daily declines
- **Critical Module Gap**: High-touch modules >15% below target

**Severity Levels**: INFO (healthy), WARNING (at-risk), CRITICAL (significant issue), EMERGENCY (immediate action required)

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                   Coverage Metrics (pytest-cov)            │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│            CoverageCollector (Collection Layer)            │
│  - Parses coverage JSON/JSONL files                         │
│  - Extracts statement/branch/line coverage                  │
│  - Calculates module-level aggregates                       │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│          CoverageTrendManager (Storage Layer)              │
│  - Saves snapshots to local/S3/HTTP backends               │
│  - Computes trends and regressions                         │
│  - Queries historical data                                 │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│        CoverageAlertManager (Alerting Layer)               │
│  - Checks against thresholds                               │
│  - Detects regressions and trends                          │
│  - Generates alerts with severity                          │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│      Alert Routing (Configuration Layer)                    │
│  - Routes alerts to channels (Slack, Email, GitHub, Logs)  │
│  - Applies severity-based filtering                        │
│  - Formats messages for each channel                       │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
    Dashboard         Alert          Observer
    Visualization    Channels       Integration
```

### Data Flow

1. **Collection**: pytest-cov generates coverage data → CoverageCollector parses it
2. **Storage**: CoverageCollector creates snapshot → CoverageTrendManager stores it
3. **Analysis**: Historical snapshots analyzed for trends and regressions
4. **Alerting**: CoverageAlertManager checks thresholds and generates alerts
5. **Routing**: AlertChannelConfig routes alerts to appropriate channels
6. **Notification**: Formatters (Slack, Email, GitHub) deliver messages

### Integration with Observer Service

The system integrates with the RepoObserverService through:
- **CoverageSignal**: Extends observer signal with coverage metrics
- **CoverageCollector**: Implements Collector interface for observer framework
- **Dashboard Panels**: Contributes coverage panels to observer snapshots
- **Alert Generation**: Automatically triggered on each metrics collection

---

## API Reference

### CoverageMetric

Data class for a single coverage measurement.

```python
from operations_center.observer import CoverageMetric

metric = CoverageMetric(
    statement_coverage_pct=85.5,
    branch_coverage_pct=72.3,
    line_coverage_pct=86.1
)

# Access fields
print(metric.statement_coverage_pct)  # 85.5
print(metric.branch_coverage_pct)     # 72.3
```

**Fields**:
- `statement_coverage_pct: float` — Statement/condition coverage percentage (0-100)
- `branch_coverage_pct: float` — Branch coverage percentage (0-100)
- `line_coverage_pct: float` — Line execution coverage percentage (0-100)

### CoverageSnapshot

Point-in-time measurement across repository, modules, and files.

```python
from operations_center.observer import CoverageSnapshot, ModuleCoverage

snapshot = CoverageSnapshot(
    timestamp=datetime.utcnow(),
    repository_coverage=CoverageMetric(85.5, 72.3, 86.1),
    module_coverages=[
        ModuleCoverage(
            module_name="src.observer",
            coverage=CoverageMetric(88.0, 75.0, 89.0),
            file_count=24,
            health_status="healthy"
        )
    ],
    overall_health_status="healthy"
)

# Access fields
print(snapshot.repository_coverage.statement_coverage_pct)  # 85.5
print(snapshot.overall_health_status)  # "healthy"
```

**Fields**:
- `timestamp: datetime` — Measurement time (UTC)
- `repository_coverage: CoverageMetric` — Repository-wide metrics
- `module_coverages: list[ModuleCoverage]` — Per-module breakdown
- `overall_health_status: str` — "healthy", "at_risk", or "critical"

### CoverageCollector

Collects coverage metrics from pytest-cov output.

```python
from operations_center.observer import CoverageCollector
from operations_center.observer.models import ObserverContext

collector = CoverageCollector()
signal = collector.collect(context: ObserverContext)

# Returns CoverageSignal with metrics and health status
print(signal.statement_coverage_pct)
print(signal.module_coverages)
```

**Methods**:
- `collect(context: ObserverContext) -> CoverageSignal`
  - **Parameters**: ObserverContext with repository info
  - **Returns**: CoverageSignal with metrics and aggregates
  - **Throws**: CoverageCollectionError on JSON parse failures

### CoverageTrendRepository

Abstract storage layer for coverage snapshots (implemented: Local, S3, HTTP).

```python
from operations_center.observer import CoverageTrendRepository, LocalCoverageTrendRepository
from datetime import datetime, timedelta

# Create local repository
repo = LocalCoverageTrendRepository(
    storage_path="/var/coverage/snapshots",
    retention_days=30
)

# Store snapshot
repo.save_snapshot(snapshot)

# Retrieve historical data
history = repo.list_snapshots(
    start_date=datetime.utcnow() - timedelta(days=7),
    end_date=datetime.utcnow()
)

# Query by module
module_history = repo.get_module_history(
    module_name="src.observer",
    days_back=30
)
```

**Key Methods**:
- `save_snapshot(snapshot: CoverageSnapshot) -> str` — Store snapshot, returns ID
- `get_snapshot(snapshot_id: str) -> CoverageSnapshot` — Retrieve by ID
- `list_snapshots(start_date, end_date) -> list[CoverageSnapshot]` — Query by date range
- `get_module_history(module_name, days_back) -> list[CoverageSnapshot]` — Per-module history
- `cleanup_old_snapshots()` — Enforce retention policy

### CoverageTrendManager

High-level API for snapshot storage and trend analysis.

```python
from operations_center.observer import CoverageTrendManager

# Factory methods
manager = CoverageTrendManager.create_local("/var/coverage")
# or: CoverageTrendManager.create_s3("my-bucket", "coverage/")
# or: CoverageTrendManager.create_http("https://api.example.com", "token123")

# Store snapshot
snapshot_id = manager.save_snapshot(snapshot)

# Analyze trends
trend = manager.compute_trend_analysis(
    metric_type="statement",
    module_name="src.observer",
    days_back=7
)

print(f"Trend direction: {trend.trend_direction}")  # "declining", "stable", "improving"
print(f"Slope (% per day): {trend.slope_pct_per_day}")  # -0.75
print(f"7-day projection: {trend.projection_value_7day}")  # 83.2

# Detect regressions
regression = manager.detect_regression(
    snapshot,
    previous_snapshot,
    threshold_pct=2.0
)

if regression.is_regression:
    print(f"Regression detected: {regression.delta_pct}% drop")
```

**Key Methods**:
- `save_snapshot(snapshot) -> str` — Store and return ID
- `get_snapshot(snapshot_id) -> CoverageSnapshot`
- `compute_trend_analysis(metric_type, module_name, days_back) -> CoverageTrendAnalysis`
- `detect_regression(current, previous, threshold) -> RegressionResult`
- `calculate_trend_slope(snapshots) -> float` — % per day
- `calculate_volatility_score(snapshots) -> float` — 0-1 stability metric
- `get_historical_data(module_name, metric_type, days_back) -> list[CoverageSnapshot]`

### CoverageAlertManager

Generates alerts based on thresholds and trends.

```python
from operations_center.observer import CoverageAlertManager, CoverageAlertConfig

# Create config with thresholds
config = CoverageAlertConfig(
    repo_minimum_threshold=80.0,
    repo_warning_threshold=85.0,
    repo_target_threshold=90.0,
    statement_coverage_minimum=75.0,
    branch_coverage_minimum=65.0,
    line_coverage_minimum=75.0,
    regression_threshold_pct=2.0,
    trend_degradation_days=5,
    module_thresholds={
        "src.observer": 85.0,
        "src.critical": 90.0
    }
)

# Create manager
manager = CoverageAlertManager(config)

# Generate all applicable alerts
alerts = manager.generate_alerts(
    current_snapshot,
    previous_snapshot=None,
    trend_analysis=trend
)

# Filter by severity
critical_alerts = manager.filter_alerts_by_severity(alerts, ["CRITICAL", "EMERGENCY"])

# Summarize
summary = manager.summarize_alerts(alerts)
print(f"Total alerts: {summary['total']}")
print(f"Critical: {summary['by_severity']['CRITICAL']}")
print(f"By type: {summary['by_type']}")
```

**Key Methods**:
- `generate_alerts(current, previous, trend) -> list[CoverageAlert]`
  - Returns all applicable alerts (threshold, regression, trend, module gaps)
- `filter_alerts_by_severity(alerts, severities) -> list[CoverageAlert]`
- `filter_alerts_by_type(alerts, types) -> list[CoverageAlert]`
- `summarize_alerts(alerts) -> dict` — Counts by type and severity
- `classify_severity(coverage_pct) -> str` — "INFO", "WARNING", "CRITICAL", or "EMERGENCY"

**Alert Fields**:
```python
CoverageAlert(
    id="alert_20260612_001",
    timestamp=datetime.utcnow(),
    alert_type="BELOW_THRESHOLD",  # or REGRESSION_DETECTED, TREND_DEGRADING, CRITICAL_MODULE_COVERAGE
    severity="CRITICAL",  # or INFO, WARNING, EMERGENCY
    metric_type="statement",
    granularity="repository",  # or module, file
    scope="src.observer",  # module name or repo
    value=68.5,
    threshold=80.0,
    delta_pct=-2.5,
    affected_modules=["src.observer", "src.alert_channels"],
    recommendation="Review untested code paths in critical modules"
)
```

### CoverageAlertConfig

Configuration for alert thresholds and severity levels.

```python
from operations_center.observer import CoverageAlertConfig

config = CoverageAlertConfig(
    # Repository-level thresholds
    repo_minimum_threshold=80.0,      # Minimum acceptable
    repo_warning_threshold=85.0,      # Below this triggers warning
    repo_target_threshold=90.0,       # Desired level
    
    # Per-metric thresholds
    statement_coverage_minimum=75.0,
    branch_coverage_minimum=65.0,
    line_coverage_minimum=75.0,
    
    # Regression detection
    regression_threshold_pct=2.0,      # Alert if ≥2% drop
    regression_7day_threshold_pct=3.0, # 7-day window
    regression_30day_threshold_pct=5.0,
    
    # Trend detection
    trend_degradation_days=5,      # 5+ consecutive declines
    trend_degradation_velocity_pct=1.0,  # -1% per day minimum
    
    # Severity thresholds
    severity_critical_threshold=50.0,   # <50% = EMERGENCY
    severity_high_threshold=70.0,       # <70% = CRITICAL
    severity_medium_threshold=80.0,     # <80% = WARNING
    
    # Module-level overrides
    module_thresholds={
        "src.observer": 85.0,      # Critical module - higher threshold
        "src.reporting": 75.0,     # Less critical - lower threshold
    }
)

# Get module-specific threshold
observer_threshold = config.get_module_threshold("src.observer")  # 85.0
other_threshold = config.get_module_threshold("src.other")      # 80.0 (default)

# Classify severity
severity = config.classify_severity(68.5)  # "CRITICAL" (50-70%)
```

**All Fields**:
- Repository thresholds: `repo_minimum_threshold`, `repo_warning_threshold`, `repo_target_threshold`
- Coverage type minimums: `statement_coverage_minimum`, `branch_coverage_minimum`, `line_coverage_minimum`
- Regression thresholds: `regression_threshold_pct`, `regression_7day_threshold_pct`, `regression_30day_threshold_pct`
- Trend detection: `trend_degradation_days`, `trend_degradation_velocity_pct`
- Severity mapping: `severity_critical_threshold`, `severity_high_threshold`, `severity_medium_threshold`
- Module overrides: `module_thresholds: dict[str, float]`

---

## Configuration Guide

### Basic Setup

The simplest way to get started is using default thresholds:

```python
from operations_center.observer import CoverageAlertConfig

# Use built-in defaults
config = CoverageAlertConfig()

# This gives you:
# - Repository minimum: 80%, warning: 85%, target: 90%
# - Statement: 75%, Branch: 65%, Line: 75%
# - Regression: 2% per-run, 3% 7-day, 5% 30-day
```

### YAML Configuration

Create `.console/coverage-config.yaml`:

```yaml
# Repository-level thresholds
repo_minimum_threshold: 80.0
repo_warning_threshold: 85.0
repo_target_threshold: 90.0

# Per-metric thresholds
statement_coverage_minimum: 75.0
branch_coverage_minimum: 65.0
line_coverage_minimum: 75.0

# Regression detection (% change)
regression_threshold_pct: 2.0           # Per-run
regression_7day_threshold_pct: 3.0      # 7-day window
regression_30day_threshold_pct: 5.0     # 30-day window

# Trend degradation detection
trend_degradation_days: 5               # 5+ consecutive declines
trend_degradation_velocity_pct: 1.0     # -1% per day minimum

# Severity classification thresholds
severity_critical_threshold: 50.0       # <50% = EMERGENCY
severity_high_threshold: 70.0           # <70% = CRITICAL
severity_medium_threshold: 80.0         # <80% = WARNING

# Module-level overrides
module_thresholds:
  src.observer: 85.0       # Critical module
  src.custodian: 80.0
  src.execution: 75.0      # Non-critical module

# Alert routing configuration
alert_channels:
  routes:
    # Route critical/emergency alerts to Slack
    - channel_name: slack
      enabled: true
      alert_types: []  # All types
      severity_levels: [critical, emergency]
      enabled_modules: []  # All modules
    
    # Route regressions to Email
    - channel_name: email
      enabled: true
      alert_types: [regression_detected]
      severity_levels: [warning, critical, emergency]
      enabled_modules: []
    
    # Route module gaps to GitHub
    - channel_name: github
      enabled: true
      alert_types: [critical_module_coverage]
      severity_levels: []  # All levels
      enabled_modules: []
  
  default_channels: [operator]  # Fallback
```

### Environment Variable Overrides

Override any YAML setting via environment variables:

```bash
# Set minimum threshold to 85%
export COVERAGE_REPO_MINIMUM_THRESHOLD=85

# Override 7-day regression threshold
export COVERAGE_REGRESSION_7DAY_THRESHOLD_PCT=2.5

# Set critical module threshold
export COVERAGE_SEVERITY_CRITICAL_THRESHOLD=45
```

Variable naming: `COVERAGE_<UPPER_SNAKE_CASE_FIELD_NAME>`

### Production Setup with Multiple Modules

For a complex system with different thresholds per module:

```yaml
repo_minimum_threshold: 78.0    # Repo-wide minimum
repo_warning_threshold: 82.0
repo_target_threshold: 88.0

# Strict requirements for critical modules
module_thresholds:
  # Core modules - highest standards
  src.observer.core: 95.0
  src.alert_channels: 92.0
  src.data_models: 90.0
  
  # Standard modules
  src.observer: 85.0
  src.reporting: 85.0
  
  # Utilities - relaxed standards
  src.utils: 75.0
  src.helpers: 70.0

# Detect subtle regressions
regression_threshold_pct: 1.5    # Tighter than default
regression_7day_threshold_pct: 2.5
regression_30day_threshold_pct: 4.0

# Faster trend detection
trend_degradation_days: 3        # Earlier detection
trend_degradation_velocity_pct: 0.5  # Slower declines trigger alert

# Strict severity mapping
severity_critical_threshold: 60.0    # Higher emergency threshold
severity_high_threshold: 75.0        # Tighter critical range
severity_medium_threshold: 85.0      # More warnings

# Alert routing for multiple teams
alert_channels:
  routes:
    # Core team gets emergency alerts via Slack
    - channel_name: slack
      alert_types: [below_threshold, trend_degrading]
      severity_levels: [emergency]
      enabled_modules: [src.observer.core, src.alert_channels, src.data_models]
    
    # All developers get emails on regressions
    - channel_name: email
      alert_types: [regression_detected]
      severity_levels: [critical, emergency]
    
    # Critical module gaps go to GitHub for immediate code review
    - channel_name: github
      alert_types: [critical_module_coverage]
      severity_levels: [warning, critical, emergency]
    
    # Everything else to operator logs
    - channel_name: operator
      severity_levels: [info, warning]
  
  default_channels: [operator]
```

### Loading Configuration

```python
from operations_center.observer import CoverageConfigManager

# Option 1: Use built-in defaults
manager = CoverageConfigManager.create_default()

# Option 2: Load from YAML with env var overrides
manager = CoverageConfigManager.create_with_yaml("/path/to/.console/coverage-config.yaml")

# Option 3: Auto-discover YAML in standard locations
manager = CoverageConfigManager.create_auto_discovery()

# Get alert config
alert_config = manager.get_alert_config()

# Get routing config
routing_config = manager.get_alert_channel_config()

# Reload if config file changes
manager.reload()
```

---

## Usage Examples

### Example 1: Collect and Analyze Coverage

```python
from operations_center.observer import (
    CoverageCollector,
    CoverageTrendManager,
    CoverageAlertManager,
    CoverageAlertConfig
)
from operations_center.observer.models import ObserverContext

# Setup
collector = CoverageCollector()
trend_manager = CoverageTrendManager.create_local("/var/coverage")
alert_config = CoverageAlertConfig()
alert_manager = CoverageAlertManager(alert_config)

# Collect metrics
context = ObserverContext(repo_path="/path/to/repo")
signal = collector.collect(context)
snapshot = signal.coverage_snapshot

# Store in history
snapshot_id = trend_manager.save_snapshot(snapshot)

# Analyze trends
trend = trend_manager.compute_trend_analysis(
    metric_type="statement",
    module_name=None,  # Repository-wide
    days_back=7
)

# Get previous snapshot for regression detection
previous = trend_manager.get_historical_data(
    module_name=None,
    metric_type="statement",
    days_back=1
)[-2] if len(...) >= 2 else None

# Generate alerts
alerts = alert_manager.generate_alerts(
    current_snapshot=snapshot,
    previous_snapshot=previous,
    trend_analysis=trend
)

# Summarize
summary = alert_manager.summarize_alerts(alerts)
print(f"Coverage: {snapshot.repository_coverage.statement_coverage_pct:.1f}%")
print(f"Trend: {trend.trend_direction}")
print(f"Alerts: {summary['total']} total ({summary['by_severity']['CRITICAL']} critical)")
```

### Example 2: Set Custom Thresholds for Critical Modules

```python
from operations_center.observer import CoverageAlertConfig, CoverageAlertManager

# Create config with module-specific thresholds
config = CoverageAlertConfig(
    repo_minimum_threshold=80.0,  # Repo default
    module_thresholds={
        "src.observer.core": 95.0,      # Authentication - highest standard
        "src.observer.alerts": 90.0,    # Critical for reliability
        "src.observer.storage": 88.0,   # Data persistence
        "src.utils": 70.0,              # Utilities - more relaxed
    }
)

manager = CoverageAlertManager(config)

# Check what threshold applies to a module
core_threshold = config.get_module_threshold("src.observer.core")
utils_threshold = config.get_module_threshold("src.utils")
other_threshold = config.get_module_threshold("src.other")  # Falls back to 80.0

print(f"Core threshold: {core_threshold}%")   # 95.0
print(f"Utils threshold: {utils_threshold}%") # 70.0
print(f"Other threshold: {other_threshold}%") # 80.0 (default)
```

### Example 3: Respond to Alerts Programmatically

```python
from operations_center.observer import (
    CoverageAlertManager,
    CoverageAlertConfig,
    CoverageAlertRouter
)

# Setup
config = CoverageAlertConfig()
manager = CoverageAlertManager(config)
router = CoverageAlertRouter()

# Generate alerts
alerts = manager.generate_alerts(current, previous, trend)

# Process each alert type
critical_alerts = manager.filter_alerts_by_severity(alerts, ["CRITICAL", "EMERGENCY"])

for alert in critical_alerts:
    print(f"\n⚠️  {alert.alert_type}")
    print(f"   Module: {alert.scope}")
    print(f"   Coverage: {alert.value:.1f}% (target: {alert.threshold}%)")
    print(f"   Delta: {alert.delta_pct:+.1f}%")
    print(f"   Action: {alert.recommendation}")
    
    # Route alert to appropriate channels
    results = router.route_alert(alert, channels=["slack", "email"])
    
    for channel, result in results.items():
        if result.success:
            print(f"   ✓ Sent to {channel}")
        else:
            print(f"   ✗ Failed to send to {channel}: {result.error}")
```

### Example 4: Monitor Trends Over Time

```python
from operations_center.observer import CoverageTrendManager
from datetime import datetime, timedelta

manager = CoverageTrendManager.create_local("/var/coverage")

# Get 30-day trend
trend = manager.compute_trend_analysis(
    metric_type="statement",
    module_name="src.observer",
    days_back=30
)

print(f"Module: {trend.module_name}")
print(f"Trend: {trend.trend_direction}")
print(f"Slope: {trend.slope_pct_per_day:.2f}% per day")
print(f"Volatility: {trend.volatility_score:.2f} (0-1)")
print(f"30-day projection: {trend.projection_value_30day:.1f}%")

# If declining, estimate when we'll hit critical threshold (70%)
if trend.slope_pct_per_day < 0:
    days_to_critical = (trend.current_value - 70) / abs(trend.slope_pct_per_day)
    print(f"Days until critical: {days_to_critical:.0f} (at current rate)")

# Get actual historical values
history = manager.get_historical_data(
    module_name="src.observer",
    metric_type="statement",
    days_back=30
)

for snapshot in history[-5:]:  # Last 5 days
    print(f"  {snapshot.timestamp.date()}: {snapshot.repository_coverage.statement_coverage_pct:.1f}%")
```

---

## Responding to Alerts

### Alert Types and Recommended Actions

#### 1. Below-Threshold Alerts

**Severity**: INFO (≥80%), WARNING (70-80%), CRITICAL (50-70%), EMERGENCY (<50%)

**Cause**: Coverage is below configured minimum or warning threshold

**Recommended Response**:
```
For CRITICAL/EMERGENCY (coverage <70%):
1. Immediately review test coverage gaps
2. Identify untested code paths in critical modules
3. Write tests for newly added or modified code
4. Target recovery to 85%+ within 1 sprint

For WARNING (coverage 70-80%):
1. Schedule coverage improvement work
2. Document why specific areas have lower coverage
3. Plan to reach minimum threshold in next 2 sprints
```

**Example Alert**:
```
BELOW_THRESHOLD: src.observer coverage 68.5% (minimum: 80%)
Affected modules: src.observer.alerts, src.observer.storage
Recommendation: Review and test untested code paths
```

#### 2. Regression-Detected Alerts

**Severity**: WARNING (1-2% drop), CRITICAL (2-3% drop), EMERGENCY (>3% drop)

**Cause**: Coverage decreased by ≥2% since last measurement

**Recommended Response**:
```
1. Check what changed in the last commit
2. Review new/modified code for test coverage
3. Add tests for new functionality
4. Block merge if CRITICAL/EMERGENCY
5. Revert or add tests within 1 hour
```

**Example Alert**:
```
REGRESSION_DETECTED: Statement coverage -2.5% (was 88.5%, now 86.0%)
Previous baseline: commit a1b2c3d
Recommendation: Review changes and add tests for new code
```

#### 3. Trend-Degrading Alerts

**Severity**: WARNING (trending down), CRITICAL (steep decline), EMERGENCY (approaching critical)

**Cause**: 5+ consecutive days of declining coverage

**Recommended Response**:
```
1. Analyze commits from the past 5+ days
2. Identify why coverage is declining
3. Create action plan to stabilize and recover
4. Schedule coverage improvement meetings
5. Set team coverage goals
```

**Example Alert**:
```
TREND_DEGRADING: Statement coverage declining -0.8% per day
7-day trend: 89.5% → 83.6% (down 5.9%)
Projection: 78.8% in 7 more days
Recommendation: Analyze trend drivers and establish recovery goals
```

#### 4. Critical-Module-Coverage Alerts

**Severity**: WARNING, CRITICAL, or EMERGENCY (depending on gap size)

**Cause**: High-touch module is >15% below threshold

**Recommended Response**:
```
1. Focus test efforts on specified modules
2. Review what's untested in these modules
3. Prioritize based on module criticality
4. Target recovery within 2-3 sprints
```

**Example Alert**:
```
CRITICAL_MODULE_COVERAGE: src.observer.core coverage 65% (target: 90%)
Gap: 25% below target, Module touches: 156 commits/month
Recommendation: Prioritize test coverage for core authentication module
```

### Best Practices for Responding

1. **Timeliness**: Address CRITICAL/EMERGENCY alerts within 1-4 hours
2. **Root Cause**: Always identify WHY coverage changed, not just fix it
3. **Prevention**: Use regression alerts to prevent missing tests on new code
4. **Trend Analysis**: Look for patterns — is coverage declining team-wide?
5. **Documentation**: Document coverage decisions (why some code isn't tested)
6. **Team Communication**: Share alerts with team, don't fix in isolation

---

## Troubleshooting Guide

### Problem 1: Alerts Not Being Generated

**Symptoms**:
- No alerts appear even though coverage is below threshold
- History shows coverage metrics but no alerts generated

**Root Causes**:
1. CoverageAlertManager not invoked in your pipeline
2. Thresholds configured higher than actual coverage
3. Alert filtering silencing all alerts

**Solutions**:

```python
# Verify manager is creating alerts
from operations_center.observer import CoverageAlertManager, CoverageAlertConfig

config = CoverageAlertConfig()
manager = CoverageAlertManager(config)

# Check if alerts are generated
alerts = manager.generate_alerts(snapshot, None, None)
print(f"Generated {len(alerts)} alerts")

# If empty, debug each check
if not alerts:
    # Check below-threshold
    print(f"Repo coverage: {snapshot.repository_coverage.statement_coverage_pct}%")
    print(f"Repo minimum: {config.repo_minimum_threshold}%")
    
    # Check threshold
    if snapshot.repository_coverage.statement_coverage_pct < config.repo_minimum_threshold:
        print("Should generate BELOW_THRESHOLD alert")
    else:
        print("Coverage is above threshold - no alert expected")
```

**Prevention**:
- Verify CoverageAlertManager is called after each metric collection
- Check configured thresholds match your team's standards
- Test alert generation with intentionally low coverage values

---

### Problem 2: False Positives (Alerts When Coverage Is Stable)

**Symptoms**:
- Getting regression alerts even though coverage hasn't changed
- Trend alerts triggering on stable coverage

**Root Causes**:
1. Regression threshold too low (default 2% — even small measurement variance triggers)
2. Coverage.py showing different values on different machines
3. Inconsistent test environment setup

**Solutions**:

```python
# Increase regression threshold to reduce false positives
config = CoverageAlertConfig(
    regression_threshold_pct=2.5,      # Slightly higher
    regression_7day_threshold_pct=3.5
)

# Verify measurement consistency
from operations_center.observer import CoverageTrendManager

manager = CoverageTrendManager.create_local("/var/coverage")

# Check 10 consecutive snapshots for natural variance
history = manager.get_historical_data(days_back=10)
values = [s.repository_coverage.statement_coverage_pct for s in history]

import statistics
variance = statistics.stdev(values) if len(values) > 1 else 0
print(f"Natural variance: ±{variance:.2f}%")

# Set threshold above 2x natural variance
config = CoverageAlertConfig(regression_threshold_pct=variance * 2.5)
```

**Prevention**:
- Run coverage collection in consistent environments
- Use same coverage tools/settings across all runs
- Set regression threshold based on your measurement variance
- Require multiple consecutive measurements to confirm trends

---

### Problem 3: Cannot Identify Root Cause (Unknown Category)

**Symptoms**:
- Alerts show "UNKNOWN" cause instead of specific issue type
- Coverage metrics present but no clear degradation pattern

**Root Causes**:
1. Data collection incomplete (missing previous snapshots)
2. Threshold check false — coverage is actually above limit
3. Insufficient historical data for trend analysis

**Solutions**:

```python
# Ensure sufficient historical data
from operations_center.observer import CoverageTrendManager
from datetime import datetime, timedelta

manager = CoverageTrendManager.create_local("/var/coverage")

# Check we have at least 5 days of history for trend analysis
history = manager.get_historical_data(
    days_back=10,
    module_name=None,
    metric_type="statement"
)

print(f"Historical snapshots: {len(history)}")
if len(history) < 5:
    print("Warning: Insufficient history for trend analysis")

# Verify snapshot data is complete
for snapshot in history:
    print(f"{snapshot.timestamp}: {snapshot.repository_coverage.statement_coverage_pct}%")
    if snapshot.module_coverages is None or len(snapshot.module_coverages) == 0:
        print("  ⚠️  Missing module breakdown")
```

**Prevention**:
- Collect metrics consistently (daily or after each test run)
- Verify snapshots before storing (check all fields are populated)
- Keep at least 30 days of historical data for trend analysis
- Document any expected coverage drops (e.g., new feature branches)

---

### Problem 4: Storage Issues (Permissions, Space, Cleanup)

**Symptoms**:
- Cannot write snapshots: "Permission denied" errors
- Disk space growing unbounded
- Old data not being cleaned up

**Root Causes**:
1. Storage path not writable by service user
2. Retention policy not enforced
3. No cleanup task scheduled

**Solutions**:

```python
# Fix permissions
import os
storage_path = "/var/coverage/snapshots"
os.makedirs(storage_path, mode=0o755, exist_ok=True)
os.chmod(storage_path, 0o755)

# Verify writable
import tempfile
try:
    with tempfile.NamedTemporaryFile(dir=storage_path, delete=True):
        print("Storage path is writable ✓")
except PermissionError:
    print("Storage path is NOT writable ✗")

# Schedule cleanup
from operations_center.observer import CoverageTrendManager
import schedule

manager = CoverageTrendManager.create_local(storage_path)

def cleanup():
    manager.cleanup_old_snapshots()  # Enforces retention_days
    print("Cleanup completed")

# Run daily
schedule.every().day.at("02:00").do(cleanup)  # 2 AM daily
```

**Prevention**:
- Set up storage path with proper permissions during deployment
- Schedule daily cleanup (typically at low-traffic time)
- Monitor disk usage: `du -sh /var/coverage/snapshots/`
- Set retention_days to reasonable value (default 30 days)

---

### Problem 5: Alerts Going to Wrong Channel

**Symptoms**:
- Critical alerts going to operator log instead of Slack
- Emails not being sent to team
- GitHub comments not appearing on PRs

**Root Causes**:
1. Alert routing configuration incorrect
2. Channel credentials/configuration missing
3. Alert doesn't match any routes (falling back to default)

**Solutions**:

```python
from operations_center.observer import (
    CoverageAlertRouter,
    CoverageAlertConfig,
    AlertChannelRoute,
    AlertChannelConfig
)

# Check current routing configuration
routing = AlertChannelConfig(
    routes=[
        AlertChannelRoute(
            channel_name="slack",
            enabled=True,
            alert_types=["critical_module_coverage"],
            severity_levels=["critical", "emergency"],
            enabled_modules=[]
        ),
        AlertChannelRoute(
            channel_name="email",
            enabled=True,
            alert_types=["regression_detected"],
            severity_levels=[],  # All
            enabled_modules=[]
        ),
    ],
    default_channels=["operator"]
)

# Test routing for a specific alert
alert = ...  # Your alert
matching_routes = routing.get_routes_for_alert(
    alert_type=alert.alert_type,
    severity=alert.severity,
    module=alert.scope
)

print(f"Alert would be routed to: {matching_routes}")
if not matching_routes:
    print(f"No matching routes - would use default: {routing.default_channels}")

# Verify channels are enabled
router = CoverageAlertRouter()
if not router.slack_channel.enabled:
    print("⚠️  Slack channel is DISABLED")
if not router.email_channel.enabled:
    print("⚠️  Email channel is DISABLED")
```

**Prevention**:
- Validate routing configuration on startup
- Test alert delivery with a test alert
- Log which channel each alert was sent to
- Monitor delivery failures: `grep "Failed to send" logs/`

---

## Integration Guide

### Integrating with Observer Service

The coverage alerting system integrates seamlessly with the RepoObserverService:

```python
from operations_center.observer import (
    RepoObserverService,
    CoverageCollector,
    CoverageTrendManager,
    CoverageAlertManager,
    CoverageAlertConfig
)

# During service initialization
class MyObserver:
    def __init__(self):
        self.coverage_collector = CoverageCollector()
        self.trend_manager = CoverageTrendManager.create_local("/var/coverage")
        self.alert_config = CoverageAlertConfig()
        self.alert_manager = CoverageAlertManager(self.alert_config)
    
    async def run_snapshot(self, context):
        """Called by observer service to capture metrics."""
        
        # Collect current coverage
        signal = self.coverage_collector.collect(context)
        snapshot = signal.coverage_snapshot
        
        # Store in history
        self.trend_manager.save_snapshot(snapshot)
        
        # Get previous snapshot for regression detection
        history = self.trend_manager.get_historical_data(
            days_back=1,
            metric_type="statement"
        )
        previous = history[-2] if len(history) >= 2 else None
        
        # Analyze trends
        trend = self.trend_manager.compute_trend_analysis(
            metric_type="statement",
            days_back=7
        )
        
        # Generate alerts
        alerts = self.alert_manager.generate_alerts(
            current_snapshot=snapshot,
            previous_snapshot=previous,
            trend_analysis=trend
        )
        
        # Route alerts to channels (handled by observer framework)
        for alert in alerts:
            await self.notify_channels(alert)
        
        return signal
```

### Dashboard Integration

Add coverage panels to observer dashboard:

```python
from operations_center.observer import DashboardProvider

# Panels are automatically included if coverage_snapshot is provided
dashboard_data = {
    "coverage_snapshot": snapshot,
    "coverage_trends": trend,
    "coverage_signal": signal,
    # ... other dashboard data
}

provider = DashboardProvider(**dashboard_data)
snapshot = provider.generate_snapshot()

# Panels include:
# - Coverage Summary (overall metrics + health)
# - Coverage by Module (top 10 modules by coverage)
# - Coverage Trend (7-day trend with direction)
# - Coverage Alerts (active alerts by type and severity)
```

### CI/CD Integration

Integrate with your CI pipeline to block merges:

```python
# In your CI check script (e.g., .github/workflows/coverage.yml)
from operations_center.observer import (
    CoverageCollector,
    CoverageAlertManager,
    CoverageAlertConfig
)

def check_coverage():
    collector = CoverageCollector()
    context = create_context(repo_path=".")
    signal = collector.collect(context)
    
    config = CoverageAlertConfig()
    manager = CoverageAlertManager(config)
    
    alerts = manager.generate_alerts(
        current_snapshot=signal.coverage_snapshot,
        previous_snapshot=None,
        trend_analysis=None
    )
    
    # Block merge if critical alerts
    critical = manager.filter_alerts_by_severity(
        alerts,
        ["CRITICAL", "EMERGENCY"]
    )
    
    if critical:
        print("❌ Coverage check FAILED")
        for alert in critical:
            print(f"  - {alert.alert_type}: {alert.recommendation}")
        exit(1)
    else:
        print("✅ Coverage check PASSED")
        exit(0)

if __name__ == "__main__":
    check_coverage()
```

### Database Storage (S3 or HTTP)

For production deployments, use remote storage:

```python
from operations_center.observer import CoverageTrendManager

# Use S3 for durability
manager = CoverageTrendManager.create_s3(
    bucket="coverage-metrics",
    prefix="snapshots/",
    aws_region="us-west-2"
)

# Or use HTTP API
manager = CoverageTrendManager.create_http(
    base_url="https://metrics.internal.company.com",
    auth_token="bearer_token_here"
)

# All operations are transparent
snapshot_id = manager.save_snapshot(snapshot)
history = manager.get_historical_data(days_back=30)
```

---

## Best Practices

### Threshold Configuration

1. **Start Conservative**: Use default thresholds (80% minimum) initially
2. **Gradual Improvement**: Increase targets as team improves practices
3. **Module-Specific**: Set higher thresholds for critical modules
4. **Document Decisions**: Record why certain modules have lower thresholds
5. **Review Regularly**: Adjust annually or after major changes

### Alert Management

1. **Route Strategically**: Send different alert types to different teams
2. **Avoid Alert Fatigue**: Tune regression threshold to actual measurement variance
3. **Act Quickly**: Address CRITICAL/EMERGENCY alerts within 1-4 hours
4. **Communicate**: Share alerts with team and include in standups
5. **Archive**: Keep historical alerts for trend analysis

### Data Quality

1. **Consistent Collection**: Run coverage collection in standardized environment
2. **Verify Snapshots**: Check snapshots are complete before storing
3. **Retention Policy**: Keep 30-90 days of history for trend analysis
4. **Cleanup**: Schedule automatic cleanup to prevent disk bloat
5. **Backup**: Store important metrics in version control or backup system

### Team Practices

1. **Test First**: Require tests before code review
2. **Block Low Coverage**: Prevent merges below threshold
3. **Improve Gradually**: Set incremental goals each sprint
4. **Learn from Trends**: Use 30-day trends to identify patterns
5. **Celebrate Progress**: Share coverage improvements with team

---

## FAQ

### Q: What's the difference between Statement, Branch, and Line coverage?

**A**: 
- **Line Coverage**: Has the line been executed? (Simplest)
- **Statement Coverage**: Have all statements been executed?
- **Branch Coverage**: Have both sides of conditionals been tested? (Most comprehensive)

We recommend targeting Statement coverage as the baseline, with Branch coverage for critical modules.

### Q: How often should I collect coverage metrics?

**A**: Depends on your workflow:
- **On every test run** (recommended): Most timely, maximum data
- **Daily/nightly**: Good balance of data and overhead
- **Per merge**: Detects regressions but misses trends

We recommend on every test run in CI, with optional nightly collection for trending.

### Q: My coverage keeps declining. What should I do?

**A**: 

1. **Analyze the trend**: Is it gradual or sudden?
2. **Find the cause**: 
   - Are you adding untested code?
   - Did test setup break?
   - Are you skipping tests?
3. **Set a recovery goal**: "Back to 85% by end of sprint"
4. **Make it visible**: Share trend with team in standups
5. **Assign ownership**: Who's responsible for specific modules?

### Q: How do I handle legacy code with low coverage?

**A**:

```python
# Option 1: Module-specific lower threshold
config = CoverageAlertConfig(
    repo_minimum_threshold=80.0,
    module_thresholds={
        "src.legacy": 40.0,  # Accept lower coverage for now
        "src.modern": 85.0   # Require high coverage for new code
    }
)

# Option 2: Gradual improvement plan
# Set threshold that increases each quarter
# Q1: 40%, Q2: 50%, Q3: 65%, Q4: 80%
```

### Q: Can I exclude certain files from coverage?

**A**: Yes, configure pytest-cov or coverage.py to exclude files:

```python
# In pyproject.toml
[tool.coverage.run]
omit = [
    "*/tests/*",
    "*/migrations/*",
    "*/protobuf/*"
]
```

Then your CoverageCollector will only see covered code.

### Q: Should I alert on every regression, or only significant ones?

**A**:

```python
# Alert on significant regressions (conservative)
config = CoverageAlertConfig(
    regression_threshold_pct=3.0,  # Only ≥3% drops
    regression_7day_threshold_pct=4.0
)

# Alert on small regressions too (aggressive)
config = CoverageAlertConfig(
    regression_threshold_pct=1.0,  # Even small drops
    regression_7day_threshold_pct=2.0
)
```

Choose based on your team's risk tolerance and measurement consistency.

### Q: How long should I keep historical data?

**A**:
- **Minimum**: 7 days (for trend detection)
- **Recommended**: 30 days (identify patterns)
- **Optimal**: 90 days (yearly planning)

Use `retention_days` parameter in CoverageTrendManager.

---

## Support and Contact

For issues, questions, or feature requests:
- **GitHub Issues**: [OperationsCenter/issues](https://github.com/ProtocolWarden/OperationsCenter/issues)
- **Documentation**: See design documents in `docs/design/`
- **Code**: `src/operations_center/observer/coverage_*`

---

**Document Version**: 1.0  
**Last Updated**: 2026-06-12  
**Status**: Production Ready
