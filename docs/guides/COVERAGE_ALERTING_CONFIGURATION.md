# Coverage Alerting Configuration Guide

**Version**: 1.0  
**Last Updated**: 2026-06-12

## Quick Start Configuration

The simplest configuration uses defaults with no overrides:

```yaml
coverage:
  enabled: true
  storage: local  # or "s3", "http"
```

This enables the coverage alerting system with:
- Repository-level minimum threshold: 80%
- Regression detection: 2% drop from previous run
- Trend detection: 5+ consecutive declines
- Alert routing: operator channel only

---

## Basic Configuration

Here's a typical configuration for a Python project:

```yaml
coverage:
  enabled: true
  
  # Storage backend
  storage:
    type: local  # Development: local files
    base_path: .coverage_data
    retention_days: 90
  
  # Repository-level thresholds
  thresholds:
    minimum_pct: 80.0      # Blocks merge if below
    warning_pct: 85.0      # Informational warning
    target_pct: 90.0       # Improvement goal
  
  # Coverage type specifics
  coverage_types:
    statement:
      minimum: 75.0
    branch:
      minimum: 65.0
    line:
      minimum: 75.0
  
  # Regression detection
  regression_detection:
    enabled: true
    run_to_run_threshold_pct: 2.0
    window_7day_threshold_pct: 3.0
    window_30day_threshold_pct: 5.0
  
  # Trend analysis
  trend_detection:
    enabled: true
    min_consecutive_declining_runs: 5
    min_trend_pct_per_day: -1.0
  
  # Alert routing
  alert_channels:
    operator:
      enabled: true
    slack:
      enabled: false  # Optional
```

---

## Production Configuration with Module Overrides

For a larger project with different modules at different stages:

```yaml
coverage:
  enabled: true
  
  # Cloud storage for production
  storage:
    type: s3
    bucket: ops-center-coverage
    prefix: coverage-trends
    region: us-west-2
    retention_days: 180  # Longer retention
  
  # Repository defaults (most modules)
  thresholds:
    minimum_pct: 80.0
    warning_pct: 85.0
    target_pct: 90.0
  
  # Critical modules have stricter requirements
  module_thresholds:
    "src/operations_center/observer":
      minimum_pct: 85.0
      target_pct: 92.0
    
    "src/operations_center/custodian":
      minimum_pct: 85.0
      target_pct: 92.0
    
    # Legacy module: relaxed (working on improvement)
    "src/legacy/deprecated_feature":
      minimum_pct: 50.0
      target_pct: 70.0
      reason: "Legacy code, migration in progress"
  
  # Regression detection
  regression_detection:
    enabled: true
    run_to_run_threshold_pct: 2.0
    window_7day_threshold_pct: 3.0
    window_30day_threshold_pct: 5.0
  
  # Trend analysis
  trend_detection:
    enabled: true
    min_consecutive_declining_runs: 5
    min_trend_pct_per_day: -1.0
  
  # Alert routing to multiple channels
  alert_channels:
    slack:
      enabled: true
      webhook_url: !vault /secret/slack/coverage-webhook
      routes:
        # Critical coverage below minimum → Slack immediately
        - alert_types: [below_threshold]
          severity_levels: [critical, emergency]
          channels: [slack]
        
        # Regressions → Slack + GitHub PR comment
        - alert_types: [regression_detected]
          severity_levels: [warning, critical, emergency]
          channels: [slack, github]
        
        # Trends → Slack weekly digest
        - alert_types: [trend_degrading]
          channels: [slack_weekly_digest]
    
    email:
      enabled: true
      smtp_url: !vault /secret/email/smtp
      from_address: coverage-alerts@ops.internal
      routes:
        # Critical issues → Email
        - alert_types: [below_threshold]
          severity_levels: [emergency]
        
        # Daily summary email
        - alert_types: [trend_degrading]
          channels: [email_daily_digest]
    
    github:
      enabled: true
      api_token: !vault /secret/github/api-token
      routes:
        # Regressions → PR comments
        - alert_types: [regression_detected]
          severity_levels: [warning, critical, emergency]
    
    operator:
      enabled: true  # Always available fallback
```

---

## Configuration by Use Case

### Case 1: Strict Enforcement (Startup, Critical System)

For startups or critical systems where coverage is essential:

```yaml
coverage:
  thresholds:
    minimum_pct: 85.0   # Strict minimum
    warning_pct: 90.0
    target_pct: 95.0
  
  coverage_types:
    statement:
      minimum: 80.0
    branch:
      minimum: 75.0     # Branch coverage stricter
    line:
      minimum: 85.0
  
  regression_detection:
    run_to_run_threshold_pct: 1.0  # 1% drop triggers alert
    window_7day_threshold_pct: 2.0
  
  trend_detection:
    min_consecutive_declining_runs: 3  # Alert faster
    min_trend_pct_per_day: -0.5
  
  alert_channels:
    slack:
      enabled: true
    email:
      enabled: true
    github:
      enabled: true
      # Regressions block merge (CI gate)
```

### Case 2: Permissive (Legacy Codebase)

For systems transitioning from no coverage to better coverage:

```yaml
coverage:
  thresholds:
    minimum_pct: 50.0   # Permissive while improving
    warning_pct: 70.0
    target_pct: 85.0
  
  regression_detection:
    run_to_run_threshold_pct: 5.0  # Only alert on major drops
    window_7day_threshold_pct: 10.0
  
  trend_detection:
    min_consecutive_declining_runs: 10  # Very long trends
    min_trend_pct_per_day: -2.0
  
  alert_channels:
    operator:
      enabled: true
    slack:
      enabled: true
      routes:
        # Only critical issues to Slack
        - severity_levels: [critical, emergency]
  
  # Plan roadmap to stricter thresholds
  roadmap:
    - date: 2026-09-01
      minimum_pct: 60.0
    - date: 2026-12-01
      minimum_pct: 70.0
    - date: 2027-03-01
      minimum_pct: 80.0
```

### Case 3: Multi-Language Project

For polyglot projects with different coverage tools:

```yaml
coverage:
  # Python modules
  python:
    storage:
      type: local
    collector: coverage.py
    thresholds:
      minimum_pct: 80.0
  
  # JavaScript/TypeScript modules
  javascript:
    storage:
      type: local
    collector: istanbul
    thresholds:
      minimum_pct: 75.0  # JS coverage often harder
  
  # Java modules
  java:
    storage:
      type: s3
    collector: jacoco
    thresholds:
      minimum_pct: 70.0  # Java often lower coverage
  
  # Aggregate across all
  aggregate:
    minimum_pct: 75.0
    strategy: weighted  # Weight by LOC
```

---

## Alert Route Configuration

Alert routes determine which channels receive which alerts:

### Route Structure

```yaml
alert_routes:
  - name: "critical-immediate"
    enabled: true
    
    # Match conditions (all must match for route to apply)
    alert_types:
      - below_threshold
      - regression_detected
    
    severity_levels:
      - critical
      - emergency
    
    enabled_modules:
      - "src/operations_center"
      # Empty list = all modules
    
    # Deliver to these channels
    channels:
      - slack
      - email
  
  - name: "warning-digest"
    enabled: true
    
    alert_types:
      - trend_degrading
    
    severity_levels:
      - warning
      - info
    
    # Channels support grouping
    channels:
      - slack_weekly_digest
      - email_daily_digest
  
  # Default route (fallback)
  - name: "default"
    enabled: true
    
    alert_types: []  # All types
    severity_levels: []  # All severities
    
    channels:
      - operator  # Always available
```

### Route Matching Rules

Routes are evaluated in order (first match wins):

```python
# Routes evaluated:
routes = [
  {alert_types: [below_threshold], channels: [slack, email]},
  {alert_types: [trend_degrading], channels: [slack_digest]},
  {alert_types: [], channels: [operator]},  # Catch-all
]

alert = CoverageAlert(
  type="below_threshold",
  severity="critical",
  scope_id="src/observer"
)

# Evaluation:
# 1. Route 1: type matches → DELIVER (slack, email)
#    (stops here, doesn't evaluate route 2)
# 2. Route 2: type mismatch → skip
# 3. Route 3: would catch, but route 1 already matched
```

---

## Module Threshold Overrides

Modules can have custom thresholds based on criticality:

```yaml
module_thresholds:
  # Core modules: stricter
  "src/operations_center/observer":
    minimum_pct: 85.0
    statement: 85.0
    branch: 80.0
    line: 85.0
  
  "src/operations_center/custodian":
    minimum_pct: 85.0
  
  # Secondary modules: default
  "src/operations_center/scheduler":
    # Uses repository defaults
  
  # Legacy modules: relaxed with migration plan
  "src/legacy/old_api":
    minimum_pct: 50.0
    notes: "Legacy code; migration targeted for 2026-Q4"
  
  # Utilities: may have lower coverage by design
  "src/utils/helpers":
    minimum_pct: 70.0
    reason: "Test utilities; high test/low prod code ratio"
```

**Resolution Algorithm**:
```
For metric_type="statement", module_path="src/observer":

1. Check module_thresholds["src/observer"]["statement"]
   → Found: 85.0, USE IT

For metric_type="statement", module_path="src/other":

1. Check module_thresholds["src/other"]["statement"]
   → Not found, fall through
2. Check coverage_types["statement"]["minimum"]
   → Found: 75.0, USE IT

For metric_type="branch", module_path="src/observer":

1. Check module_thresholds["src/observer"]["branch"]
   → Found: 80.0, USE IT
```

---

## Storage Backend Configuration

### Local (Development)

```yaml
storage:
  type: local
  base_path: .coverage_data
  retention_days: 90
  
  # Creates structure:
  # .coverage_data/
  # ├── 2026-06-01/
  # │   ├── run-abc123.jsonl
  # │   └── run-def456.jsonl
  # ├── 2026-06-02/
  # │   └── run-ghi789.jsonl
```

### S3 (Production)

```yaml
storage:
  type: s3
  bucket: company-coverage-metrics
  prefix: coverage-trends
  region: us-west-2
  
  # Creates structure:
  # s3://company-coverage-metrics/
  # └── coverage-trends/
  #     ├── snapshots/repo-name/run-abc123.json
  #     ├── trends/repo-name/repository_line.jsonl
  #     ├── trends/repo-name/modules/src_observer.jsonl
  #     └── alerts/repo-name/2026-06-12.jsonl
  
  retention_days: 180
  
  # Required AWS credentials (via environment or IAM role)
  # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or IAM role)
```

### HTTP (Remote API)

```yaml
storage:
  type: http
  base_url: https://coverage-api.internal.company.com
  
  authentication:
    type: bearer
    token: !vault /secret/coverage-api-token
  
  # API endpoints called:
  # POST /snapshots → save snapshot
  # GET /snapshots/{run_id} → retrieve snapshot
  # GET /trends?metric={type}&granularity={gran}&start={date}&end={date}
  
  retention_days: 180
```

---

## Environment Variables

Configuration can be overridden with environment variables:

```bash
# Enable/disable
COVERAGE_ENABLED=true

# Thresholds
COVERAGE_MINIMUM_PCT=80
COVERAGE_WARNING_PCT=85
COVERAGE_TARGET_PCT=90

# Storage
COVERAGE_STORAGE_TYPE=s3
COVERAGE_STORAGE_BUCKET=my-bucket
COVERAGE_STORAGE_PREFIX=coverage

# Alert channels
COVERAGE_SLACK_WEBHOOK=https://hooks.slack.com/services/...
COVERAGE_EMAIL_SMTP_URL=smtp://smtp.company.com:587
COVERAGE_GITHUB_TOKEN=ghp_...

# Regression thresholds
COVERAGE_REGRESSION_RUN_TO_RUN_PCT=2.0
COVERAGE_REGRESSION_7DAY_PCT=3.0
```

---

## Validation and Testing Configuration

### Validate Configuration

```bash
# Check for syntax errors
coverage-alerter validate-config coverage.yaml

# Output:
# ✓ Configuration valid
# ✓ Thresholds: min=80%, warning=85%, target=90%
# ✓ Storage: local (.coverage_data)
# ✓ Alert channels: operator, slack
```

### Test Alert Routes

```bash
# Verify routes match expected alerts
coverage-alerter test-routes coverage.yaml

# Input: test alert
# Output:
# Alert: below_threshold, severity=critical, scope=repository
# Matched routes: [critical-immediate]
# Channels: [slack, email]
```

### Dry-Run Mode

```bash
# Generate alerts without sending (test configuration)
coverage-alerter --dry-run observe

# Output:
# Generated 3 alerts:
# 1. below_threshold (severity=warning): line coverage 78% < 80%
# 2. regression_detected (severity=high): statement 84% vs 86% (-2%)
# 3. trend_degrading (severity=info): 5-day decline at -0.8%/day
#
# Routes:
# Alert 1 → slack, email
# Alert 2 → slack, github
# Alert 3 → operator
#
# (No notifications sent in dry-run mode)
```

---

## Configuration Best Practices

1. **Start Permissive**: Set minimum threshold lower than current coverage, then increase over time
2. **Module Overrides**: Only override for good reasons (legacy code, different language, etc.)
3. **Storage Backend**: Use S3 for production, local for development
4. **Alert Routes**: Route critical alerts immediately, non-critical alerts to digests
5. **Validation**: Always validate configuration before deploying
6. **Secrets**: Never commit tokens/webhooks; use vault or environment variables
7. **Retention**: Keep 90 days local, 180 days S3 for trend analysis

---

**Configuration Guide Version**: 1.0
