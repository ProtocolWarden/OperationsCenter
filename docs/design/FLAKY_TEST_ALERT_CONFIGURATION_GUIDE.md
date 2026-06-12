# Flaky Test Reporter — Alert Configuration Guide

**Version**: 1.0  
**Status**: Complete (Stage 5 Documentation)  
**Last Updated**: 2026-06-12

---

## Table of Contents

1. [Alert System Overview](#alert-system-overview)
2. [Alert Types](#alert-types)
3. [Threshold Configuration](#threshold-configuration)
4. [Alert Channels](#alert-channels)
5. [Custom Scenarios](#custom-scenarios)
6. [Configuration Examples](#configuration-examples)
7. [Troubleshooting Alerts](#troubleshooting-alerts)
8. [Best Practices](#best-practices)

---

## Alert System Overview

The Flaky Test Reporter includes an intelligent alert system that detects and notifies you about significant flakiness patterns. Alerts are:

- **Intelligent**: Multiple detection paths prevent false positives
- **Customizable**: Threshold and channel configuration per repository
- **Integrated**: Routes through Slack, email, GitHub, Plane, PagerDuty
- **Severity-Based**: INFO, WARNING, CRITICAL, EMERGENCY levels
- **Actionable**: Includes test metadata and recommended actions

### Alert Workflow

```
Test Execution
    ↓
Metric Calculation (failure_rate, entropy, streak_length, etc.)
    ↓
Pattern Analysis (categorization: INTERMITTENT, ENVIRONMENT, INFRASTRUCTURE)
    ↓
Alert Detection (rule matching against configured thresholds)
    ↓
Severity Determination (based on metric values)
    ↓
Channel Routing (send to configured channels)
    ↓
Notification (Slack message, email, GitHub comment, etc.)
```

---

## Alert Types

### 1. NEW_FLAKY_TEST

**Triggered When**: A previously stable test becomes flaky.

**Detection Criteria**:
- Test failure rate crosses flaky_threshold (default: 5%)
- Test was stable in previous run (failure_rate < 5%)
- Sufficient runs for confidence (default: 3+ runs)

**Severity Mapping**:
- FAILURE_RATE > 50%: 🔴 **CRITICAL**
- FAILURE_RATE 20-50%: 🟠 **WARNING**
- FAILURE_RATE 5-20%: 🟡 **INFO**

**Example Alert**:
```
🚨 New Flaky Test Detected

Test: tests/unit/auth/test_login.py::TestLogin::test_valid_credentials
Failure Rate: 45% (9/20 runs)
Category: INTERMITTENT (likely race condition)
Confidence: 92%
Detected: 2 hours ago

Recommended Action:
- Review recent changes to test or authentication module
- Check for timing dependencies or resource contention
- Run test in isolation to verify

Impact: 1 team affected (auth team)
```

---

### 2. REGRESSION_SPIKE

**Triggered When**: Failure rate increases significantly from previous baseline.

**Detection Criteria**:
- Failure rate increases by >20 percentage points in 24 hours
- Test remains above flaky_threshold (>5%)
- Change is sustained (not transient)

**Severity Mapping**:
- INCREASE > 50pp: 🔴 **CRITICAL**
- INCREASE 30-50pp: 🟠 **WARNING**
- INCREASE 20-30pp: 🟡 **INFO**

**Example Alert**:
```
⚠️ Regression Spike Detected

Test: tests/integration/api/test_endpoint.py::TestAPI::test_post_request
Previous Rate: 8% (1 week average)
Current Rate: 47% (last 24 hours)
Change: +39 percentage points (↑ CRITICAL)
Category: INFRASTRUCTURE (likely deployment issue)

Likely Root Cause:
- Recent deployment or infrastructure change
- Check CI logs for deployment events in last 24h
- Review recent commits to affected modules

Affected Team: API team
Last Seen: 30 minutes ago
```

---

### 3. CRITICAL_FLAKINESS

**Triggered When**: A single test has become extremely unreliable.

**Detection Criteria**:
- Test failure_rate > critical_threshold (default: 50%)
- Test has run at least min_runs times
- Pattern is statistically significant (confidence > 70%)

**Severity Mapping**:
- FAILURE_RATE > 80%: 🔴 **EMERGENCY**
- FAILURE_RATE 60-80%: 🔴 **CRITICAL**
- FAILURE_RATE 50-60%: 🟠 **WARNING**

**Example Alert**:
```
🚨 CRITICAL FLAKINESS DETECTED

Test: tests/integration/database/test_migration.py::TestMigration::test_schema_update
Failure Rate: 78% (14/18 runs)
Category: INFRASTRUCTURE
Confidence: 96%

STATUS: Consider disabling this test until fixed

Recent History:
- Started failing: 3 days ago
- Consistently fails on: schema validation step
- Affects: All database-dependent integration tests (blocked)

Blocking Tests: 12 other tests depend on this one
Estimated Dev Hours Lost: ~4 hours/day

Immediate Actions:
1. Disable test from CI pipeline
2. Schedule investigation meeting
3. Review database setup and migration logic
```

---

### 4. MODULE_OUTBREAK

**Triggered When**: Multiple tests in the same module become flaky simultaneously.

**Detection Criteria**:
- 3+ tests in the same module become flaky in 24 hours
- Average failure_rate across module > 20%
- Pattern suggests infrastructure or setup issue

**Severity Mapping**:
- MODULE_AVG > 50%: 🔴 **CRITICAL**
- MODULE_AVG 30-50%: 🟠 **WARNING**
- MODULE_AVG 20-30%: 🟡 **INFO**

**Example Alert**:
```
🚨 Module Outbreak Detected

Module: tests/unit/auth/
Affected Tests: 5 (out of 8 total in module)
Module Avg Failure Rate: 38%

Breakdown:
- test_login.py: 45% (↑ +30pp from yesterday)
- test_signup.py: 35% (↑ +25pp)
- test_oauth.py: 32% (↑ +28pp)
- test_mfa.py: 8% (baseline)
- test_session.py: 2% (baseline)

Pattern: All failures in authentication validation
Root Cause Hypothesis: Shared fixture or setup issue in auth module

Recommended Actions:
1. Review conftest.py fixtures in auth module
2. Check for environment setup issues
3. Look for recent changes affecting multiple tests
4. Check CI environment for resource constraints

Confidence: 88%
```

---

## Threshold Configuration

### Alert Thresholds Object

```python
class AlertThreshold:
    """Defines when alerts should trigger at different severity levels."""
    
    # Failure rate thresholds
    info_threshold: float           # Alert at this rate (INFO level)
    warning_threshold: float        # Alert at this rate (WARNING level)
    critical_threshold: float       # Alert at this rate (CRITICAL level)
    emergency_threshold: float      # Alert at this rate (EMERGENCY level)
    
    # Time windows
    regression_window_hours: int    # How far back to look for regression
    sustained_hours: int            # How long pattern must persist
    
    # Statistical requirements
    min_runs: int                   # Minimum runs for confidence
    min_confidence: float           # Minimum confidence threshold (0.0-1.0)
```

### Default Thresholds

```python
DEFAULT_THRESHOLDS = {
    # Failure rate thresholds
    "info_threshold": 0.05,           # 5% (baseline for flakiness)
    "warning_threshold": 0.20,        # 20% (concerning level)
    "critical_threshold": 0.50,       # 50% (severe flakiness)
    "emergency_threshold": 0.80,      # 80% (essentially broken)
    
    # Time windows
    "regression_window_hours": 24,    # Check last 24 hours for regression
    "sustained_hours": 6,             # Pattern must persist 6+ hours
    
    # Statistical
    "min_runs": 3,                    # Need 3+ runs for significance
    "min_confidence": 0.70,           # Need 70%+ confidence to alert
}
```

---

## Alert Channels

### 1. Slack Channel

**Configuration**:
```python
alert_channels = {
    "slack": {
        "enabled": true,
        "webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
        "channel": "#flaky-tests",
        "mention_on_critical": ["@oncall", "@build-team"],
        "quiet_hours": "18:00-09:00",  # No alerts during off-hours
    }
}
```

**Message Format**:
```
🔴 CRITICAL: New Flaky Test - test_login

failure_rate: 45% | category: INTERMITTENT | confidence: 92%

👉 Action: Review recent auth module changes

Severity: CRITICAL | Detected: 2h ago | Impact: 1 team
```

**Features**:
- Emoji severity indicators (🟢 INFO, 🟡 WARNING, 🟠 CRITICAL, 🔴 EMERGENCY)
- Thread replies for related alerts
- @mentions for on-call engineering
- Quiet hours support (no nighttime alerts)

---

### 2. Email Channel

**Configuration**:
```python
alert_channels = {
    "email": {
        "enabled": true,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "sender": "flaky-alerts@example.com",
        "recipients": ["oncall@example.com", "eng-team@example.com"],
        "severity_recipient_map": {
            "info": ["eng-team@example.com"],
            "warning": ["oncall@example.com", "eng-team@example.com"],
            "critical": ["oncall@example.com", "devops@example.com"],
            "emergency": ["director@example.com", "oncall@example.com"],
        }
    }
}
```

**Message Format**:
```
Subject: [CRITICAL] Flaky Test Alert: test_login

New Flaky Test Detected

Test Name:        tests/unit/auth/test_login.py::TestLogin::test_valid_credentials
Failure Rate:     45% (9/20 runs)
Category:         INTERMITTENT
Confidence:       92%
Detected:         2 hours ago

Root Cause Hypothesis:
  Likely race condition in authentication validation logic.
  Pattern shows high variance in execution time.

Recommended Actions:
  1. Review recent changes to authentication module
  2. Add proper synchronization to reduce race conditions
  3. Run test in isolation 10+ times to verify fix

---
Flaky Test Reporter
OperationsCenter v1.0
```

**Features**:
- Separate recipients per severity level
- HTML and plaintext versions
- Formatted tables with metrics
- Detailed remediation suggestions

---

### 3. GitHub Channel

**Configuration**:
```python
alert_channels = {
    "github": {
        "enabled": true,
        "github_token": "ghp_...",  # GitHub personal access token
        "repo_owner": "myorg",
        "repo_name": "myrepo",
        "post_on_pull_requests": true,
        "post_on_commits": false,
    }
}
```

**Comment Format**:
```markdown
<!-- flaky-alert-test_login -->
🚨 **Flaky Test Detected**: `tests/unit/auth/test_login.py::TestLogin::test_valid_credentials`

| Metric | Value |
|--------|-------|
| **Failure Rate** | 45% (9/20) |
| **Category** | INTERMITTENT |
| **Confidence** | 92% |
| **Detected** | 2h ago |

**Suspected Root Cause**: Race condition in authentication validation

**Actions**:
- [ ] Review recent commits to auth module
- [ ] Add synchronization if needed
- [ ] Run test in isolation 5+ times
- [ ] Update test documentation

---
*Posted by Flaky Test Reporter*
```

**Features**:
- Comments on related PRs
- Dismissible (user can delete comment)
- Linked to source code
- Checks for duplicate alerts

---

### 4. Plane (Task Management) Channel

**Configuration**:
```python
alert_channels = {
    "plane": {
        "enabled": true,
        "api_key": "...",
        "workspace_slug": "myworkspace",
        "project_id": "...",
        "labels": ["flaky-test", "auto-created"],
        "priority": {
            "info": "NONE",
            "warning": "MEDIUM",
            "critical": "HIGH",
            "emergency": "URGENT",
        }
    }
}
```

**Created Task**:
```
Title: Fix Flaky Test: test_login (INTERMITTENT)

Description:
Severity: CRITICAL
Failure Rate: 45%
Category: INTERMITTENT (likely race condition)

Test: tests/unit/auth/test_login.py::TestLogin::test_valid_credentials
Runs Analyzed: 20 (9 failed, 11 passed)

Root Cause:
High variance in test execution suggests race condition
Pattern: Failures are random, not deterministic

Steps to Reproduce:
1. Run: pytest tests/unit/auth/test_login.py -v --count=10
2. Observe: intermittent failures

To Fix:
1. Add proper locks/synchronization
2. Review async operations
3. Ensure test isolation

Labels: flaky-test, auto-created, intermittent
Priority: HIGH
Assignee: [build-team]
Due Date: [in 2 days]
```

**Features**:
- Auto-creates tasks for flaky tests
- Sets priority based on severity
- Tracks across multiple repositories
- Integrates with team workflows

---

### 5. PagerDuty Channel

**Configuration**:
```python
alert_channels = {
    "pagerduty": {
        "enabled": true,
        "integration_key": "...",
        "severity_mapping": {
            "info": "info",
            "warning": "warning",
            "critical": "error",
            "emergency": "critical",
        },
        "escalation_policy": {
            "critical": "oncall-engineer",
            "emergency": "oncall-manager",
        }
    }
}
```

**Incident Created**:
```
Title: CRITICAL: Flaky Test Spike in auth module

Description:
Multiple tests becoming flaky simultaneously suggests
infrastructure or setup issue in tests/unit/auth/ module.

5 tests affected:
- test_login: 45%
- test_signup: 35%
- test_oauth: 32%
- test_mfa: 28%
- test_session: 8%

Severity: CRITICAL (Module Outbreak pattern)
Escalation: Immediate (oncall-engineer on-call)
```

**Features**:
- Creates PagerDuty incidents
- Routes to on-call engineer
- Automatic escalation for EMERGENCY
- Links to dashboard for investigation

---

## Custom Scenarios

### Scenario 1: Strict Testing Repository (High Standards)

**Use Case**: Mission-critical code where any flakiness is unacceptable.

**Configuration**:
```python
FlakyTestAlertConfig(
    # Lower thresholds - alert earlier
    info_threshold=0.02,          # 2% (very strict)
    warning_threshold=0.05,       # 5% (minimal flakiness)
    critical_threshold=0.15,      # 15% (significant concern)
    emergency_threshold=0.30,     # 30% (urgent)
    
    # High confidence requirement
    min_runs=10,                  # Must see pattern in 10+ runs
    min_confidence=0.95,          # 95% confidence required
    
    # Fast alert window
    sustained_hours=2,            # Alert after 2 hours of issues
)

# Route to multiple channels for high visibility
alert_channels = {
    "slack": {"mention_on_critical": ["@all"]},
    "email": {"recipients": ["build-team@", "oncall@"]},
    "pagerduty": {"enabled": true},
}
```

**Result**:
- Alerts on minimal flakiness (2% instead of 5%)
- Higher confidence requirement prevents false positives
- Multiple notification channels
- Fast response (2-hour window)

---

### Scenario 2: High-Velocity Startup (High Tolerance)

**Use Case**: Rapid development where occasional flakiness is tolerated.

**Configuration**:
```python
FlakyTestAlertConfig(
    # Higher thresholds - ignore minor flakiness
    info_threshold=0.15,          # 15% (ignore minor flakiness)
    warning_threshold=0.35,       # 35% (concerning)
    critical_threshold=0.60,      # 60% (very serious)
    emergency_threshold=0.85,     # 85% (essentially broken)
    
    # Lower confidence requirement (move fast)
    min_runs=2,                   # Just 2 runs needed
    min_confidence=0.60,          # 60% confidence OK
    
    # Slower alert window
    sustained_hours=24,           # Alert after 24 hours of issues
)

# Conservative alert routing (only critical/emergency)
alert_channels = {
    "slack": {
        "enabled": true,
        "minimum_severity": "CRITICAL",  # Skip INFO/WARNING
    },
}
```

**Result**:
- Ignores minor flakiness (<15%)
- Fewer false positives
- Single notification channel
- Less alert fatigue

---

### Scenario 3: Data Pipeline (Environment-Sensitive)

**Use Case**: Data processing where ENVIRONMENT-category flakiness is common.

**Configuration**:
```python
FlakyTestAlertConfig(
    # Standard thresholds
    info_threshold=0.05,
    warning_threshold=0.20,
    critical_threshold=0.50,
    emergency_threshold=0.80,
    
    # Category-specific filtering
    alert_on_categories={
        "INTERMITTENT": true,           # Alert on race conditions
        "INFRASTRUCTURE": true,         # Alert on setup issues
        "ENVIRONMENT": false,           # Ignore external service issues
        "UNKNOWN": false,               # Ignore unclear patterns
    },
)

# Special handling for environment issues
alert_channels = {
    "slack": {
        "channel": "#data-pipeline-flaky",
        "enabled": true,
    },
}
```

**Result**:
- Ignores external service timeouts (ENVIRONMENT)
- Focuses on code/infrastructure issues
- Reduces alert noise from transient failures
- Team can focus on fixable issues

---

### Scenario 4: Multi-Team Repository (Distributed Ownership)

**Use Case**: Large repository with multiple teams owning different modules.

**Configuration**:
```python
# Base configuration
base_config = FlakyTestAlertConfig(
    info_threshold=0.05,
    warning_threshold=0.20,
    critical_threshold=0.50,
)

# Team-specific routing
team_routing = {
    "auth_team": {
        "modules": ["tests/unit/auth/", "tests/integration/auth/"],
        "channels": ["#auth-team-slack"],
        "escalation": "auth-oncall",
    },
    "api_team": {
        "modules": ["tests/unit/api/", "tests/integration/api/"],
        "channels": ["#api-team-slack"],
        "escalation": "api-oncall",
    },
    "data_team": {
        "modules": ["tests/unit/data/", "tests/integration/data/"],
        "channels": ["#data-team-slack"],
        "escalation": "data-oncall",
        "alert_categories": ["INTERMITTENT", "INFRASTRUCTURE"],  # Skip ENVIRONMENT
    },
}
```

**Result**:
- Alerts routed to owning team
- Each team gets notifications for their modules only
- Custom thresholds per team (e.g., data team ignores ENVIRONMENT)
- Distributed ownership and accountability

---

## Configuration Examples

### Example 1: Basic Configuration

**File**: `config/flaky_test_alerts.yaml`

```yaml
flaky_test_alerts:
  enabled: true
  
  # Thresholds
  thresholds:
    info: 0.05        # Alert if failure_rate > 5%
    warning: 0.20     # Alert if > 20%
    critical: 0.50    # Alert if > 50%
    emergency: 0.80   # Alert if > 80%
  
  # Time windows
  regression_window_hours: 24
  sustained_hours: 6
  
  # Statistical
  min_runs: 3
  min_confidence: 0.70
  
  # Channels
  channels:
    slack:
      enabled: true
      webhook_url: "${SLACK_WEBHOOK_URL}"
      channel: "#flaky-tests"
    email:
      enabled: true
      recipients:
        - "oncall@example.com"
```

**Usage in Code**:
```python
from operations_center.observer.flaky_test_alert_config import FlakyTestAlertConfig

config = FlakyTestAlertConfig.from_yaml("config/flaky_test_alerts.yaml")

alert_manager = FlakyTestAlertManager(config)
alerts = alert_manager.check_alerts(report)

for alert in alerts:
    print(f"{alert.severity}: {alert.message}")
```

---

### Example 2: Production Configuration

**File**: `config/flaky_test_alerts_prod.yaml`

```yaml
flaky_test_alerts:
  enabled: true
  environment: "production"
  
  # Strict thresholds for production
  thresholds:
    info: 0.03        # 3% (very strict)
    warning: 0.10     # 10%
    critical: 0.25    # 25%
    emergency: 0.50   # 50%
  
  # Fast response
  regression_window_hours: 12
  sustained_hours: 1   # Alert within 1 hour
  
  # High confidence
  min_runs: 20
  min_confidence: 0.95
  
  # Multi-channel alert routing
  channels:
    slack:
      enabled: true
      webhook_url: "${SLACK_WEBHOOK_PROD}"
      channel: "#prod-alerts"
      mention_on_critical: ["@oncall", "@director"]
      severity_channels:
        critical: "#prod-critical"
        emergency: "#prod-emergency"
    
    email:
      enabled: true
      severity_recipient_map:
        info: ["eng-team@example.com"]
        warning: ["oncall@example.com"]
        critical: ["director@example.com", "oncall@example.com"]
        emergency: ["ceo@example.com", "cto@example.com"]
    
    pagerduty:
      enabled: true
      severity_mapping:
        critical: "error"
        emergency: "critical"
    
    github:
      enabled: true
      repo_owner: "myorg"
      repo_name: "myrepo"
  
  # Alert filtering
  categories_to_alert:
    - INTERMITTENT
    - INFRASTRUCTURE
  # Ignore external service issues (ENVIRONMENT)
```

---

### Example 3: Development Configuration

**File**: `config/flaky_test_alerts_dev.yaml`

```yaml
flaky_test_alerts:
  enabled: true
  environment: "development"
  
  # Lenient thresholds for dev
  thresholds:
    info: 0.10        # 10%
    warning: 0.30     # 30%
    critical: 0.60    # 60%
    emergency: 0.90   # 90%
  
  # Slower response
  regression_window_hours: 48
  sustained_hours: 12  # Alert after 12 hours
  
  # Lower confidence OK in dev
  min_runs: 3
  min_confidence: 0.60
  
  # Minimal channels (just Slack for dev team)
  channels:
    slack:
      enabled: true
      webhook_url: "${SLACK_WEBHOOK_DEV}"
      channel: "#dev-flaky-tests"
      minimum_severity: "WARNING"  # Skip INFO-level alerts
```

---

## Troubleshooting Alerts

### Issue 1: Too Many Alerts (Alert Fatigue)

**Symptom**: Receiving alerts for every small flakiness, making it hard to prioritize.

**Solutions**:

1. **Increase Thresholds**:
   ```python
   config.warning_threshold = 0.30    # Instead of 0.20
   config.critical_threshold = 0.70   # Instead of 0.50
   ```

2. **Raise Confidence Requirement**:
   ```python
   config.min_runs = 10               # Require more evidence
   config.min_confidence = 0.90       # Very sure before alerting
   ```

3. **Filter by Severity**:
   ```yaml
   slack:
     minimum_severity: "WARNING"      # Skip INFO level
   ```

4. **Filter by Category**:
   ```python
   config.alert_on_categories = {
       "INTERMITTENT": true,
       "INFRASTRUCTURE": true,
       "ENVIRONMENT": false,          # Ignore external service timeouts
   }
   ```

---

### Issue 2: Missing Alerts

**Symptom**: Dashboard shows flaky tests, but no alerts sent.

**Diagnosis**:

1. **Check if enabled**:
   ```python
   assert config.enabled == True
   ```

2. **Check thresholds**:
   ```python
   # If failure_rate = 8% but warning_threshold = 15%, no alert
   assert config.warning_threshold <= 0.08
   ```

3. **Check confidence**:
   ```python
   # If only 2 runs but min_confidence requires 95%, no alert
   # Need more test runs
   ```

4. **Check categories**:
   ```python
   # If test is ENVIRONMENT but only INFRASTRUCTURE category alerted
   assert "ENVIRONMENT" in config.alert_on_categories
   ```

5. **Check channels**:
   ```python
   # If all channels disabled, no alerts sent
   assert any(config.channels.values())
   ```

---

### Issue 3: Alert Went to Wrong Channel

**Symptom**: Alert emailed instead of Slack, or went to wrong Slack channel.

**Check Channel Routing**:

```python
# Verify channel configuration
config = FlakyTestAlertConfig()

# Check which channels are enabled
print(config.alert_channels)  # List of enabled channels

# Check severity-based routing
alert = FlakyTestAlert(severity="CRITICAL")
routing = config.get_channels_for_severity(alert.severity)
print(routing)  # Should show correct channels
```

**Common Issues**:

1. **Wrong webhook URL**:
   ```yaml
   slack:
     webhook_url: "https://hooks.slack.com/services/WRONG/URL"
   ```
   → Fix: Update to correct webhook in settings

2. **Wrong channel name**:
   ```yaml
   slack:
     channel: "#wrong-channel"  # Should be #flaky-tests
   ```
   → Fix: Correct channel name in config

3. **Recipient not configured**:
   ```yaml
   email:
     recipients: []  # Empty list = no emails sent
   ```
   → Fix: Add email addresses

---

## Best Practices

### 1. Start Conservative, Increase Sensitivity

**Recommendation**: When setting up alerts:
1. Start with high thresholds (conservative)
2. Run for 1 week and observe
3. Gradually lower thresholds as needed
4. Find balance that minimizes false positives while catching real issues

**Example**:
```
Week 1: warning_threshold = 40% → See few alerts
Week 2: warning_threshold = 30% → Moderate alerts
Week 3: warning_threshold = 20% → Good signal-to-noise ratio
```

---

### 2. Tailor Thresholds to Context

**Recommendation**: Different repositories need different thresholds:

```python
# Strict: mission-critical code
strict_threshold = 0.05    # Alert on 5%+

# Moderate: important code
moderate_threshold = 0.15  # Alert on 15%+

# Lenient: non-critical tests
lenient_threshold = 0.30   # Alert on 30%+
```

---

### 3. Monitor Alert Quality

**Recommendation**: Track alert metrics:
- True Positives: Alerts that identified real problems
- False Positives: Alerts for non-issues
- False Negatives: Issues that weren't alerted
- Response Time: How quickly team responds to alerts

**Calculate**: `Signal-to-Noise Ratio = True Positives / (True Positives + False Positives)`
- Target: >80% (4 good alerts per 1 false alarm)

---

### 4. Test Your Configuration

**Recommendation**: Before deploying to production:

```python
# Mock a flaky test scenario
test_report = FlakyTestSessionReport(
    flaky_candidates=[
        FlakyTestMetric(
            test_name="test_example",
            failure_rate=0.45,  # 45% failure
            suspected_category="INTERMITTENT",
        )
    ]
)

# Check what alerts would fire
alerts = alert_manager.check_alerts(test_report)
print(f"Alerts generated: {len(alerts)}")
for alert in alerts:
    print(f"  {alert.severity}: {alert.message}")

# Verify correct severity and channels
assert alerts[0].severity == AlertSeverity.WARNING
assert "slack" in alerts[0].channels
```

---

### 5. Integrate with Incident Response

**Recommendation**: Include alert handling in incident response:
- Alert comes in → Create incident ticket
- Team investigates → Update status
- Fix deployed → Alert auto-resolves
- Weekly review → Improve thresholds

---

## Additional Resources

- [**Main User Guide**](flaky-test-reporter.md): Architecture, configuration, usage
- [**Dashboard User Guide**](FLAKY_TEST_DASHBOARD_USER_GUIDE.md): Interpreting dashboard panels
- [**CI Integration Guide**](flaky-test-reporter-ci-integration.md): CI/CD setup
- [**Architecture Document**](STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md): Technical deep dive

---

## Questions & Support

For questions about alert configuration:
1. Review this guide's [Custom Scenarios](#custom-scenarios)
2. Check the [Examples](#configuration-examples)
3. See [Troubleshooting](#troubleshooting-alerts)
4. Review the main [User Guide](flaky-test-reporter.md) for architecture

---

**Last Updated**: 2026-06-12  
**Version**: 1.0 Final
