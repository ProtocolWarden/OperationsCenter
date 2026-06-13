# Coverage Alerting Troubleshooting Guide

**Version**: 1.0  
**Last Updated**: 2026-06-12

---

## Problem 1: Coverage Data Not Being Collected

**Symptom**: Coverage signal status is "unavailable" or "partial"

### Root Cause 1: Coverage Tool Not Installed

**Diagnosis**:
```bash
python -c "import coverage; print(coverage.__version__)"
# ImportError: No module named 'coverage'
```

**Solution**:
```bash
pip install coverage
# or for pytest integration
pip install pytest-cov

# Verify installation
pytest --cov=src tests/
```

### Root Cause 2: Coverage Tool Not Generating Output

**Diagnosis**:
```bash
# Check if coverage file exists
ls -la .coverage
# Output: No such file or directory

# Check coverage.json
ls -la coverage.json
# Output: No such file or directory
```

**Solution**:
```bash
# Run tests with coverage
pytest --cov=src --cov-report=json tests/

# Verify output
cat coverage.json | head -20
```

### Root Cause 3: Coverage File in Wrong Location

**Diagnosis**:
```bash
# Collector looks in current directory
find . -name ".coverage" -o -name "coverage.json"
# Output: ./subdir/.coverage (not in expected location)
```

**Solution**:
```bash
# Configure test runner to output coverage to correct location
# pytest.ini
[pytest]
addopts = --cov=src --cov-report=json --cov-report=term

# Run from project root
cd /project/root
pytest tests/
```

### Root Cause 4: Observer Context Missing Coverage Files

**Diagnosis**:
```python
# In observer service
context = ObserverContext(...)
print(f"context.coverage_file = {context.coverage_file}")
# Output: None (not set)

# Check available files in context
print(dir(context))
# Output: Shows what's available in context
```

**Solution**:
```python
# Ensure context includes coverage paths
context = ObserverContext(
    coverage_file=".coverage",
    coverage_json_file="coverage.json",
    ...
)

# Or configure in observer service initialization
self.coverage_file = config.get("coverage.output_file", ".coverage")
```

---

## Problem 2: Too Many / Too Few Alerts

**Symptom**: Receiving excessive alerts or missing expected alerts

### Root Cause 1: Thresholds Too Strict

**Diagnosis**:
```bash
# Check configuration
grep -A5 "minimum_threshold_pct:" coverage_config.yaml
# Output: minimum_threshold_pct: 95.0  (unrealistic for most projects)

# Count daily alerts
coverage-alerter list-alerts --days=1 | wc -l
# Output: 47 alerts per day (too many)
```

**Solution**:
```yaml
# Adjust thresholds to realistic levels
coverage:
  thresholds:
    minimum_pct: 80.0   # Was 95.0 (changed to realistic)
    warning_pct: 85.0
    target_pct: 90.0

# Specific coverage types
coverage_types:
  statement:
    minimum: 75.0
  branch:
    minimum: 65.0  # Branches harder to achieve
  line:
    minimum: 75.0
```

### Root Cause 2: Regression Threshold Too Sensitive

**Diagnosis**:
```bash
# Check regression settings
grep -A3 "regression_detection:" coverage_config.yaml
# Output: run_to_run_threshold_pct: 0.5  (very sensitive)

# Count regression alerts
coverage-alerter list-alerts --type=regression_detected | wc -l
# Output: 15 per day (too many for 2% real regressions)
```

**Solution**:
```yaml
regression_detection:
  run_to_run_threshold_pct: 2.0    # Ignore noise < 2%
  window_7day_threshold_pct: 3.0
  window_30day_threshold_pct: 5.0
```

### Root Cause 3: Trend Detection Too Aggressive

**Diagnosis**:
```bash
# Check trend settings
grep -A3 "trend_detection:" coverage_config.yaml
# Output: min_consecutive_declining_runs: 2  (too few)

# Check trend alerts
coverage-alerter list-alerts --type=trend_degrading | wc -l
# Output: 5 per day (2 days of decline triggers, too sensitive)
```

**Solution**:
```yaml
trend_detection:
  min_consecutive_declining_runs: 5  # Require 5 days of decline
  min_trend_pct_per_day: -1.0        # Ignore small changes
```

### Root Cause 4: No Alert Routes Defined

**Diagnosis**:
```bash
# Check if routes match your alerts
coverage-alerter test-routes --alert-type=below_threshold --severity=warning
# Output: No matching routes
```

**Solution**:
```yaml
alert_routes:
  - name: "default"
    alert_types: []       # Match all types
    severity_levels: []   # Match all severities
    channels:
      - operator          # Fallback channel
```

---

## Problem 3: Storage Issues

**Symptom**: Snapshots not being persisted or historical data unavailable

### Root Cause 1: Local Storage Directory Doesn't Exist

**Diagnosis**:
```bash
# Check directory
ls -ld .coverage_data
# Output: No such file or directory

# Check permissions
touch .coverage_data/test.txt
# Output: Permission denied
```

**Solution**:
```bash
# Create directory with proper permissions
mkdir -p .coverage_data
chmod 755 .coverage_data

# Or configure alternate path
export COVERAGE_STORAGE_BASE_PATH=/tmp/coverage_data
```

### Root Cause 2: S3 Bucket Not Accessible

**Diagnosis**:
```bash
# Test S3 connectivity
aws s3 ls s3://coverage-bucket/
# Output: An error occurred (NoSuchBucket) when calling the ListObjects operation

# Check credentials
aws sts get-caller-identity
# Output: AccessDenied or not authenticated
```

**Solution**:
```bash
# Set AWS credentials
export AWS_ACCESS_KEY_ID=your-key-id
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_REGION=us-west-2

# Verify access
aws s3 ls s3://coverage-bucket/

# Or use IAM role (in production)
# EC2 instance with coverage-reporter IAM role attached
```

### Root Cause 3: Retention Policy Deleting Data Too Aggressively

**Diagnosis**:
```bash
# Check logs for deletion
grep "cleanup_old_snapshots" application.log
# Output: Deleted 847 snapshots (may be deleting needed data)

# Verify retention period
grep "retention_days:" coverage_config.yaml
# Output: retention_days: 7  (too short for trend analysis)
```

**Solution**:
```yaml
storage:
  retention_days: 90  # Keep 90 days for trend analysis
  # Adjust based on needs:
  # - Development: 30 days
  # - Production: 90-180 days
  # - Long-term analysis: 365 days
```

---

## Problem 4: Incorrect Trend Analysis

**Symptom**: Trends showing wrong direction or projection seems off

### Root Cause 1: Insufficient Historical Data

**Diagnosis**:
```python
trend = manager.compute_trend_analysis("line", "repository", window_days=7)
print(f"Measurements: {len(trend.measurements)}")
# Output: 1 (only one measurement, can't compute trend)
```

**Solution**:
```python
# Need minimum 3-5 measurements for reliable trend
# Solution: Wait for more data or use longer window

# Check how much data we have
oldest = manager.get_historical_data(..., start_date=datetime(2026, 6, 1))
print(f"Available measurements: {len(oldest)}")

# If < 5, collect more measurements before relying on trend
# Or use wider window_days
trend = manager.compute_trend_analysis(
    "line", "repository",
    window_days=30  # Wider window gets more data
)
```

### Root Cause 2: Missing Data Points (Gaps in History)

**Diagnosis**:
```python
# Check for gaps in measurements
measurements = trend.measurements
for i in range(len(measurements) - 1):
    t1, v1 = measurements[i]
    t2, v2 = measurements[i + 1]
    gap = (t2 - t1).days
    if gap > 1:
        print(f"Gap: {gap} days between measurements")
```

**Solution**:
```python
# Trend analysis handles gaps automatically
# (uses only available measurements)
# But gaps reduce reliability

# Ensure tests run daily
# Schedule: Daily coverage measurement
# CI/CD: Run tests and collect coverage daily

# Check gap handling
trend = manager.compute_trend_analysis(...)
print(f"Trend computed from {len(trend.measurements)} points")
print(f"Std Dev: {trend.standard_deviation}")
# High std dev with gaps = less reliable
```

### Root Cause 3: Outliers Skewing Results

**Diagnosis**:
```python
# Check measurements for outliers
measurements = [v for _, v in trend.measurements]
mean = statistics.mean(measurements)
stdev = statistics.stdev(measurements)

for t, v in trend.measurements:
    z_score = (v - mean) / stdev
    if abs(z_score) > 3:
        print(f"Outlier: {t} = {v}% (z-score: {z_score:.2f})")
```

**Solution**:
```python
# Check for outlier causes:
# 1. Test infrastructure issue
# 2. Coverage tool version change
# 3. Major code refactoring

# Options:
# A. Exclude outlier if confirmed artifact
# B. Investigate root cause
# C. Use larger window (more data smooths outliers)

# Consider stability score
print(f"Stability: {trend.stability_score}")
if trend.stability_score < 0.80:
    print("Coverage too volatile for reliable trends")
    # Solution: Fix flaky tests, stabilize coverage tool
```

---

## Problem 5: Alert Routing Issues

**Symptom**: Alerts not reaching expected channels

### Root Cause 1: Routes Not Matching Alerts

**Diagnosis**:
```bash
# Check route matching
coverage-alerter debug-routes \
  --alert-type=below_threshold \
  --severity=warning \
  --scope=src/observer

# Output: No matching routes (expected: operator)
```

**Solution**:
```yaml
# Check route configuration
alert_routes:
  # First route
  - alert_types: [regression_detected]  # ← only matches regressions
    channels: [slack]
  
  # Need catch-all route
  - alert_types: []  # Empty = all types
    channels: [operator]
```

### Root Cause 2: Channel Disabled

**Diagnosis**:
```yaml
alert_channels:
  slack:
    enabled: false  # ← Channel disabled
    webhook_url: https://...
  
  email:
    enabled: true
```

**Solution**:
```yaml
alert_channels:
  slack:
    enabled: true  # ← Re-enable
    webhook_url: https://hooks.slack.com/services/...
```

### Root Cause 3: Channel Configuration Invalid

**Diagnosis**:
```bash
# Test route delivery
coverage-alerter test-channel slack

# Output: Connection failed to https://hooks.slack.com/services/invalid
```

**Solution**:
```yaml
alert_channels:
  slack:
    enabled: true
    # Verify webhook URL
    webhook_url: https://hooks.slack.com/services/T12345/B67890/xxxxx
    # Check URL is complete and not expired
    # Slack webhooks expire after inactivity
```

### Root Cause 4: Rate Limiting

**Diagnosis**:
```bash
# Check alert router logs
grep "429\|rate.*limit" application.log

# Output: 429 Too Many Requests (rate limit exceeded)
```

**Solution**:
```python
# Add backoff/retry logic
from time import sleep

for channel in channels:
    try:
        router.route_alert(alert, channel)
    except RateLimitError:
        sleep(60)  # Wait before retry
        router.route_alert(alert, channel)

# Or batch alerts
# Instead of sending immediately, queue and send in batch
router.batch_route_alerts(alerts, config, batch_interval=5)  # Every 5 min
```

---

## Problem 6: Configuration Issues

**Symptom**: Configuration not being applied or syntax errors

### Root Cause 1: YAML Syntax Error

**Diagnosis**:
```bash
# Validate YAML
python -c "import yaml; yaml.safe_load(open('coverage_config.yaml'))"
# Output: 
# yaml.YAMLError: mapping values are not allowed here
# line 15, column 20
```

**Solution**:
```bash
# Fix YAML indentation
# Before:
# coverage:
#  enabled: true
#   thresholds:  # ← Wrong indentation
#     minimum: 80

# After:
# coverage:
#   enabled: true
#   thresholds:
#     minimum: 80

# Validate again
python -c "import yaml; yaml.safe_load(open('coverage_config.yaml')); print('✓ Valid')"
```

### Root Cause 2: Invalid Threshold Values

**Diagnosis**:
```yaml
coverage:
  thresholds:
    minimum_pct: 150  # ← Invalid (>100%)
    warning_pct: 80
    target_pct: 90
```

**Solution**:
```yaml
# Use valid percentages (0-100)
coverage:
  thresholds:
    minimum_pct: 80    # Valid (0-100)
    warning_pct: 85
    target_pct: 90

# Order: minimum < warning < target
# Validate: minimum (80) < warning (85) < target (90) ✓
```

### Root Cause 3: Missing Required Fields

**Diagnosis**:
```bash
coverage-alerter validate-config coverage_config.yaml
# Output: KeyError: 'minimum_threshold_pct' (required field)
```

**Solution**:
```yaml
# Add missing fields with defaults
coverage:
  enabled: true
  # Required fields:
  thresholds:
    minimum_pct: 80.0       # Required
    warning_pct: 85.0
    target_pct: 90.0
  
  regression_detection:
    enabled: true
    run_to_run_threshold_pct: 2.0
  
  trend_detection:
    enabled: true
    min_consecutive_declining_runs: 5
```

---

## Problem 7: Performance Issues

**Symptom**: Trend analysis slow or system taking too long to generate alerts

### Root Cause 1: Too Much Historical Data

**Diagnosis**:
```bash
# Check data size
du -sh .coverage_data/
# Output: 2.5GB (large, causing slow analysis)

# Count snapshots
find .coverage_data/ -name "*.jsonl" | wc -l
# Output: 5000+ snapshots
```

**Solution**:
```bash
# Reduce retention period
# Before: Keep 365 days → 5000+ snapshots
# After: Keep 90 days → 600 snapshots

# Config change
yaml
storage:
  retention_days: 90  # Reduce from 365

# Clean up old data
coverage-alerter cleanup --older-than=90days

# Or use S3 (better for large datasets)
```

### Root Cause 2: Slow Storage Backend

**Diagnosis**:
```bash
# Measure operation time
time coverage-alerter compute-trend --window=30
# Output: real 45s (too slow)
```

**Solution**:
```bash
# Check current backend
grep "storage:" coverage_config.yaml
# Output: type: http (slow, network latency)

# Migrate to faster backend
# Development: Switch from HTTP to local
# Production: Use S3 (faster than HTTP API)

# New config
storage:
  type: s3
  bucket: company-coverage
  region: us-west-2

# Performance improvement: 45s → 2s
```

### Root Cause 3: Alert Generation Creating Many Alerts

**Diagnosis**:
```python
alerts = manager.generate_alerts(snapshot, config, history)
print(f"Generated {len(alerts)} alerts")
# Output: Generated 847 alerts (very slow to process)
```

**Solution**:
```python
# Reduce alert generation
# Option 1: Use alert deduplication
unique_alerts = {}
for alert in alerts:
    key = (alert.alert_type, alert.scope_id, alert.metric_type)
    if key not in unique_alerts or alert.severity > unique_alerts[key].severity:
        unique_alerts[key] = alert

# Option 2: Filter low-severity alerts
high_priority_alerts = [a for a in alerts if a.severity in ["critical", "emergency"]]

# Option 3: Increase thresholds (fewer false alerts)
config.run_to_run_threshold_pct = 3.0  # From 2.0
```

---

## Quick Reference: Common Solutions

| Problem | Solution |
|---------|----------|
| Coverage not collected | Run `pytest --cov` with proper output format |
| Too many alerts | Increase thresholds (min 2% regression, 5+ days trend) |
| Coverage tool fails | Check tool installation: `pytest --cov` |
| S3 access denied | Verify AWS credentials and bucket permissions |
| Alerts not routed | Check `alert_routes` config includes catch-all route |
| Slow trend analysis | Reduce retention_days or switch to S3 backend |
| Inconsistent trends | Wait for more data (minimum 3-5 measurements) |
| YAML syntax error | Validate with: `python -c "import yaml; yaml.safe_load(open(...))` |

---

**Troubleshooting Guide Version**: 1.0
