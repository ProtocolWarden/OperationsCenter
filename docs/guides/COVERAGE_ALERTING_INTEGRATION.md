<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Copyright (C) 2026 ProtocolWarden -->

# Coverage Alerting Integration Guide

**Version**: 1.0  
**Last Updated**: 2026-06-12

## Overview

This guide is for Operations Center users who want to integrate coverage threshold alerting into their observer infrastructure.

---

## Quick Integration (5 minutes)

### Step 1: Enable Coverage Alerting

```python
# src/operations_center/observer/observer.py

from operations_center.observer import (
    CoverageTrendManager,
    CoverageAlertManager,
    CoverageAlertConfig,
    CoverageAlertRouter
)

class RepoObserverService:
    
    def __init__(self, config: Config):
        # ... existing initialization
        
        # NEW: Initialize coverage alerting
        self.coverage_trend_manager = CoverageTrendManager.create_local()
        self.coverage_alert_manager = CoverageAlertManager()
        self.coverage_alert_router = CoverageAlertRouter()
        
        # Load coverage alert configuration
        self.coverage_config = CoverageAlertConfig(
            minimum_threshold_pct=80.0,
            warning_threshold_pct=85.0,
            target_threshold_pct=90.0
        )
```

### Step 2: Collect and Store Snapshots

```python
class RepoObserverService:
    
    async def observe(self, context: ObserverContext) -> RepoSignalsSnapshot:
        # Existing signals...
        
        # NEW: Collect coverage and compute trends
        coverage_signal = await self._collect_coverage(context)
        
        if coverage_signal.status == "measured":
            # Store snapshot for history
            snapshot = self._convert_to_snapshot(coverage_signal)
            self.coverage_trend_manager.save_snapshot(snapshot)
            
            # Compute trends
            trend = self.coverage_trend_manager.compute_trend_analysis(
                metric_type="line",
                granularity="repository",
                window_days=7
            )
            
            # Generate alerts
            alerts = self.coverage_alert_manager.generate_alerts(
                snapshot=snapshot,
                config=self.coverage_config,
                history=trend
            )
            
            # Route alerts to channels
            for alert in alerts:
                self.coverage_alert_router.route_alert(alert, self.coverage_config)
            
            # Include in signal
            coverage_signal.active_alerts = alerts
        
        # Return existing snapshot structure
        return RepoSignalsSnapshot(
            coverage_signal=coverage_signal,
            # ... other signals
        )
```

### Step 3: Configure Coverage Thresholds

```yaml
# .console/coverage-config.yaml (create new file)

coverage:
  enabled: true
  
  storage:
    type: local
    base_path: .coverage_data
    retention_days: 90
  
  thresholds:
    minimum_pct: 80.0
    warning_pct: 85.0
    target_pct: 90.0
  
  alert_channels:
    operator:
      enabled: true
```

**Done!** Coverage alerting is now enabled. Move to "Detailed Integration" for production setup.

---

## Detailed Integration

### Data Flow

```
Test Execution
    ↓
Coverage Tool Output (.coverage.json)
    ↓
CoverageCollector.collect(context)
    ↓
CoverageSignal → CoverageSnapshot
    ↓
CoverageTrendManager.save_snapshot()
    ↓
Persistent Storage (.coverage_data or S3)
    ↓
CoverageTrendManager.compute_trend_analysis()
    ↓
CoverageTrendAnalysis (trends, projection)
    ↓
CoverageAlertManager.generate_alerts()
    ↓
CoverageAlert[] (structured alerts)
    ↓
CoverageAlertRouter.route_alert()
    ↓
Notification Channels (Slack, Email, GitHub, Operator)
```

### Integration Points

#### 1. Observer Service

**Location**: `src/operations_center/observer/observer.py`

```python
class RepoObserverService:
    
    def __init__(self, config: Config):
        # Initialize coverage components
        self.coverage_trend_manager = CoverageTrendManager.create_local(
            base_path=config.get("coverage.storage.base_path", ".coverage_data")
        )
        self.coverage_alert_manager = CoverageAlertManager()
        self.coverage_alert_router = CoverageAlertRouter()
        
        # Load configuration
        self.coverage_config = self._load_coverage_config(config)
    
    async def observe(self, context: ObserverContext) -> RepoSignalsSnapshot:
        """Main observer method — enhanced with coverage alerts."""
        
        # Collect base signals (existing)
        flakyness_signal = await self._collect_flakiness_signal(context)
        performance_signal = await self._collect_performance_signal(context)
        
        # NEW: Collect and analyze coverage
        coverage_signal = await self._collect_coverage_signal(context)
        
        if coverage_signal.status == "measured":
            # Store for trend analysis
            snapshot = self._to_coverage_snapshot(coverage_signal)
            self.coverage_trend_manager.save_snapshot(snapshot)
            
            # Compute trends
            try:
                trend = self.coverage_trend_manager.compute_trend_analysis(
                    metric_type="line",
                    granularity="repository",
                    window_days=7
                )
            except Exception as e:
                logger.warning(f"Failed to compute trend: {e}")
                trend = None
            
            # Generate alerts
            if trend:
                alerts = self.coverage_alert_manager.generate_alerts(
                    snapshot=snapshot,
                    config=self.coverage_config,
                    history=trend
                )
                coverage_signal.active_alerts = alerts
                
                # Route to channels
                for alert in alerts:
                    try:
                        self.coverage_alert_router.route_alert(alert, self.coverage_config)
                    except Exception as e:
                        logger.error(f"Failed to route alert: {e}")
        
        return RepoSignalsSnapshot(
            coverage_signal=coverage_signal,
            flakiness_signal=flakyness_signal,
            performance_signal=performance_signal
        )
```

#### 2. Configuration Loading

**Location**: `src/operations_center/observer/coverage_config.py`

```python
def _load_coverage_config(self, config: Config) -> CoverageAlertConfig:
    """Load coverage alerting configuration."""
    
    coverage_cfg = config.get("coverage", {})
    
    return CoverageAlertConfig(
        minimum_threshold_pct=coverage_cfg.get("thresholds.minimum_pct", 80.0),
        warning_threshold_pct=coverage_cfg.get("thresholds.warning_pct", 85.0),
        target_threshold_pct=coverage_cfg.get("thresholds.target_pct", 90.0),
        
        statement_minimum=coverage_cfg.get("coverage_types.statement.minimum", 75.0),
        branch_minimum=coverage_cfg.get("coverage_types.branch.minimum", 65.0),
        line_minimum=coverage_cfg.get("coverage_types.line.minimum", 75.0),
        
        run_to_run_threshold_pct=coverage_cfg.get("regression.run_to_run", 2.0),
        window_7day_threshold_pct=coverage_cfg.get("regression.7day", 3.0),
        window_30day_threshold_pct=coverage_cfg.get("regression.30day", 5.0),
        
        min_consecutive_declining_runs=coverage_cfg.get("trend.min_runs", 5),
        min_trend_pct_per_day=coverage_cfg.get("trend.min_pct_per_day", -1.0),
        
        module_thresholds=coverage_cfg.get("module_thresholds", {}),
        
        # Load alert routes
        alert_routes=self._load_alert_routes(coverage_cfg.get("alert_routes", [])),
        default_channels=coverage_cfg.get("default_channels", ["operator"])
    )
```

#### 3. RepoSignalsSnapshot Extension

**Location**: `src/operations_center/observer/models.py`

The `RepoSignalsSnapshot` already includes `coverage_signal`. Enhancement:

```python
class RepoSignalsSnapshot(BaseModel):
    """Snapshot of all observer signals for a repository."""
    
    coverage_signal: CoverageSignal
        # Already in place, now includes:
        # - statement/branch/line coverage breakdown
        # - module-level coverage
        # - active alerts
        # - trend indicators
    
    flakiness_signal: FlakinessSignal
    performance_signal: PerformanceSignal
    
    # ... other signals
```

#### 4. Dashboard Integration

**Location**: `src/operations_center/observer/dashboard.py`

```python
class DashboardProvider:
    
    def _panel_coverage_summary(self, signal: CoverageSignal) -> dict:
        """Coverage metrics panel."""
        return {
            "title": "Coverage Status",
            "metrics": {
                "overall": f"{signal.total_coverage_pct}%",
                "statement": f"{signal.statement_coverage_pct}%",
                "branch": f"{signal.branch_coverage_pct}%",
                "line": f"{signal.line_coverage_pct}%"
            },
            "health": self._get_coverage_health_status(signal)
        }
    
    def _panel_coverage_trend(self, trend: CoverageTrendAnalysis) -> dict:
        """Coverage trend panel."""
        return {
            "title": "Coverage Trend (7 days)",
            "direction": trend.trend_direction,
            "velocity": f"{trend.trend_pct}% per day",
            "projection": f"{trend.projected_value_7days}% in 7 days",
            "measurements": [
                {"date": t.isoformat(), "coverage": v}
                for t, v in trend.measurements
            ]
        }
    
    def _panel_coverage_alerts(self, signal: CoverageSignal) -> dict:
        """Active coverage alerts panel."""
        return {
            "title": "Coverage Alerts",
            "count": len(signal.active_alerts),
            "by_severity": signal.alert_count_by_severity,
            "alerts": [
                {
                    "type": alert.alert_type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "recommendation": alert.recommendation
                }
                for alert in signal.active_alerts[:10]  # Top 10
            ]
        }
    
    def generate_snapshot(self) -> dict:
        """Main dashboard generation."""
        return {
            # ... other panels
            "coverage_summary": self._panel_coverage_summary(self.coverage_signal),
            "coverage_trend": self._panel_coverage_trend(self.coverage_trend),
            "coverage_alerts": self._panel_coverage_alerts(self.coverage_signal)
        }
```

---

## Storage Backend Selection

### Development: Local Storage

```python
# Simple, no dependencies
manager = CoverageTrendManager.create_local(base_path=".coverage_data")

# Uses JSONL files in .coverage_data/YYYY-MM-DD/ directory
# Retention: 90 days, auto-cleanup on access
```

**Pros**:
- No external dependencies
- Works offline
- Easy to inspect (plain JSONL files)

**Cons**:
- Not suitable for long-term storage
- Not distributed (single machine only)

### Production: S3 Storage

```python
# AWS S3 for production
manager = CoverageTrendManager.create_s3(
    bucket="company-coverage-metrics",
    prefix="coverage-trends",
    region="us-west-2"
)

# Uses S3 keys:
# s3://company-coverage-metrics/
#   coverage-trends/snapshots/{run_id}.json
#   coverage-trends/trends/{metric}.jsonl
```

**Pros**:
- Distributed, highly available
- Long-term retention (cost-effective)
- Integrates with other AWS services
- Encrypted by default

**Cons**:
- Requires AWS credentials
- Network latency
- Costs (minimal for coverage data)

**Setup**:
```bash
# Create S3 bucket
aws s3 mb s3://company-coverage-metrics

# Set bucket policy
aws s3api put-bucket-versioning \
  --bucket company-coverage-metrics \
  --versioning-configuration Status=Enabled

# IAM role for EC2 (if running in AWS)
# Attach policy: AmazonS3FullAccess (or restrict to bucket)
```

---

## Configuration Examples

### Minimal (Development)

```yaml
coverage:
  enabled: true
  storage:
    type: local
```

Uses all defaults:
- Minimum: 80%
- Local JSONL storage
- Operator channel only

### Standard (Production)

```yaml
coverage:
  enabled: true
  storage:
    type: s3
    bucket: coverage-metrics
    region: us-west-2
  
  thresholds:
    minimum_pct: 80.0
    warning_pct: 85.0
    target_pct: 90.0
  
  module_thresholds:
    "src/operations_center/observer":
      minimum_pct: 85.0
  
  alert_channels:
    slack:
      enabled: true
    github:
      enabled: true
```

### Advanced (Multi-Team)

```yaml
coverage:
  enabled: true
  storage:
    type: s3
    bucket: coverage-metrics
    region: us-west-2
    retention_days: 180
  
  thresholds:
    minimum_pct: 80.0
    target_pct: 90.0
  
  regression_detection:
    run_to_run_threshold_pct: 2.0
    window_7day_threshold_pct: 3.0
  
  trend_detection:
    min_consecutive_declining_runs: 5
    min_trend_pct_per_day: -1.0
  
  # Module overrides for different teams
  module_thresholds:
    # Core infrastructure: strict
    "src/operations_center/observer":
      minimum_pct: 85.0
    "src/operations_center/custodian":
      minimum_pct: 85.0
    
    # Feature modules: standard
    "src/features/api":
      minimum_pct: 80.0
    
    # Legacy: relaxed
    "src/legacy/v1":
      minimum_pct: 50.0
  
  # Complex alert routing
  alert_routes:
    - name: "critical-immediate"
      alert_types: [below_threshold]
      severity_levels: [critical, emergency]
      channels: [slack, email]
    
    - name: "regressions-pr"
      alert_types: [regression_detected]
      severity_levels: [warning, critical]
      channels: [github]
    
    - name: "trends-weekly"
      alert_types: [trend_degrading]
      channels: [slack_weekly_digest]
    
    - name: "default"
      channels: [operator]
```

---

## Testing Integration

### Unit Tests

```python
import pytest
from operations_center.observer import (
    CoverageAlertManager,
    CoverageSnapshot,
    CoverageAlertConfig
)

def test_coverage_alerts_generated():
    """Test alert generation."""
    
    # Arrange
    config = CoverageAlertConfig(minimum_threshold_pct=80.0)
    manager = CoverageAlertManager()
    
    snapshot = CoverageSnapshot(
        timestamp=datetime.now(timezone.utc),
        run_id="test-123",
        source="coverage.py",
        overall_statement_coverage_pct=75.0,  # Below threshold
        overall_branch_coverage_pct=70.0,
        overall_line_coverage_pct=75.0
    )
    
    # Act
    alerts = manager.generate_alerts(snapshot, config)
    
    # Assert
    assert len(alerts) > 0
    assert alerts[0].alert_type == "below_threshold"
    assert alerts[0].severity in ["warning", "critical"]
```

### Integration Tests

```python
@pytest.mark.integration
async def test_observer_includes_coverage_alerts():
    """Test full observer integration."""
    
    # Arrange
    service = RepoObserverService(config)
    context = ObserverContext(...)
    
    # Act
    snapshot = await service.observe(context)
    
    # Assert
    assert snapshot.coverage_signal is not None
    assert snapshot.coverage_signal.status in ["measured", "partial"]
    # Alerts generated if coverage below threshold
```

### Dry-Run Testing

```bash
# Test configuration without side effects
coverage-alerter --dry-run observe

# Output:
# Generated 2 alerts:
# 1. below_threshold: line coverage 78% < 80%
# 2. regression_detected: statement 84% vs 86% (-2%)
# 
# Routing:
# Alert 1 → slack, email
# Alert 2 → github
# 
# (No notifications sent in dry-run mode)
```

---

## Monitoring Integration Health

### Health Checks

```python
def check_coverage_integration_health() -> dict:
    """Check if coverage alerting is healthy."""
    
    checks = {
        "coverage_tool_available": False,
        "storage_accessible": False,
        "alert_routes_valid": False,
        "recent_snapshot": False
    }
    
    try:
        # Check 1: Coverage tool
        signal = collector.collect(context)
        checks["coverage_tool_available"] = signal.status != "unavailable"
    except Exception as e:
        logger.error(f"Coverage tool check failed: {e}")
    
    try:
        # Check 2: Storage accessible
        manager.save_snapshot(test_snapshot)
        checks["storage_accessible"] = True
    except Exception as e:
        logger.error(f"Storage check failed: {e}")
    
    try:
        # Check 3: Alert routes valid
        config_valid = len(coverage_config.alert_routes) > 0
        checks["alert_routes_valid"] = config_valid
    except Exception as e:
        logger.error(f"Routes check failed: {e}")
    
    try:
        # Check 4: Recent snapshot (within 24 hours)
        latest = manager.get_latest_snapshot()
        age = datetime.now(timezone.utc) - latest.timestamp
        checks["recent_snapshot"] = age < timedelta(hours=24)
    except Exception as e:
        logger.error(f"Recent snapshot check failed: {e}")
    
    health_score = sum(checks.values()) / len(checks)
    return {
        "healthy": health_score >= 0.75,
        "score": health_score,
        "checks": checks
    }
```

### Metrics to Track

```python
# In application metrics/monitoring
metrics.gauge("coverage.statement_pct", signal.statement_coverage_pct)
metrics.gauge("coverage.branch_pct", signal.branch_coverage_pct)
metrics.gauge("coverage.line_pct", signal.line_coverage_pct)

metrics.counter("coverage.alerts_total", len(signal.active_alerts))
metrics.counter(
    "coverage.alerts_by_severity",
    signal.alert_count_by_severity.get("critical", 0),
    tags={"severity": "critical"}
)

metrics.gauge(
    "coverage.trend_direction",
    1 if trend.trend_direction == "improving" else
    0 if trend.trend_direction == "stable" else -1
)
```

---

## Troubleshooting Integration

### Common Issues

**Coverage signal status is "unavailable"**
- Cause: Coverage tool not installed or test output missing
- Solution: Verify `pytest --cov` produces .coverage or coverage.json

**Alerts not being routed**
- Cause: No matching alert routes in configuration
- Solution: Add catch-all route with empty alert_types/severity_levels

**Performance degradation**
- Cause: Too much historical data
- Solution: Reduce retention_days or switch to S3 backend

**Trend analysis unreliable**
- Cause: Insufficient historical data (< 5 measurements)
- Solution: Wait for more test runs or use wider time window

---

**Integration Guide Version**: 1.0
