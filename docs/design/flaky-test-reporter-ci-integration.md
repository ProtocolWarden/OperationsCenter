# Flaky Test Reporter CI/CD Pipeline Integration

**Status**: Stage 5 Implementation  
**Created**: 2026-06-07  
**Last Updated**: 2026-06-07

---

## Executive Summary

This document describes the CI/CD integration of the flaky test reporter system. The reporter detects, tracks, and reports on non-deterministic test failures (flaky tests) within continuous integration pipelines.

**Key Capabilities**:
- Automated capture of test outcomes during CI runs
- Multi-tier historical aggregation (daily rollups with 7-day windows)
- Alert generation for critical flakiness patterns
- Artifact persistence (90-day retention policy)
- GitHub PR annotations with flaky test summaries

**Target Audience**: DevOps engineers, CI/CD maintainers, development teams investigating test reliability.

---

## 1. Architecture Overview

### Data Flow in CI Pipeline

```
Test Execution (pytest)
    ↓
Pytest Plugin (capture outcomes)
    ↓
Session Report (.flaky-tests/runs/YYYY-MM-DD/HH-MM-SS-session.json)
    ↓
Tier 3 Aggregator (load 7d sessions)
    ↓
Aggregation Report (.flaky-tests/aggregations/YYYY-MM-DD-aggregation.json)
    ↓
Alert Manager (check conditions)
    ↓
Alerts + GitHub PR Comment
    ↓
Metrics Artifacts (retained 90 days)
```

### System Components

**1. Pytest Plugin** (`pytest_flaky_plugin.py`)
- Opt-in via `--flaky-detection` flag
- Hooks: `pytest_runtest_makereport`, `pytest_sessionfinish`
- Captures: test outcome, duration, exception info, test nodeid
- Output: Session report in JSONL format

**2. Storage Manager** (`flaky_test_storage.py`)
- Manages directory structure: `.flaky-tests/runs/` (sessions), `.flaky-tests/aggregations/` (daily rollups)
- Retention policies: 3 days for sessions, 90 days for aggregations
- Formats: JSON (human-readable), JSONL (streaming-friendly)
- APIs: `save_session_results()`, `load_recent_sessions()`, `cleanup_old_sessions()`

**3. Aggregator** (`flaky_test_aggregator.py`)
- Tier 3 analysis: loads N days of sessions, computes aggregate metrics
- Metrics: failure rates, trends, module concentration, category breakdown
- Recommendations: actionable fixes with priority levels
- Output: `FlakyTestAggregationReport` with top 20 flaky tests

**4. Alert Manager** (`flaky_test_alerts.py`)
- Detects 4 alert conditions:
  - **NEW_FLAKY_TEST** (MEDIUM): Test became flaky in past 24h
  - **REGRESSION_SPIKE** (HIGH): Flaky count increased >50% vs previous period
  - **CRITICAL_FLAKINESS** (HIGH): Test failure rate >30%
  - **MODULE_OUTBREAK** (MEDIUM): >20% of module tests are flaky
- Output: Prioritized alert list (critical → high → medium → low)

### Integration Points

1. **GitHub Actions**: New `flaky-test-detection` job runs on all pushes to main
2. **PR Comments**: Automated summaries posted if flakiness detected
3. **Artifact Storage**: Metrics available for 90 days via GitHub UI
4. **Observer Service**: Feeds into `FlakyTestSignal` for repository health snapshots (Stage 3)

---

## 2. CI Workflow Configuration

### GitHub Actions Job

**Location**: `.github/workflows/ci.yml` (lines 174-241)

**Triggers**:
- **Push**: Full test run with flaky detection (includes all unit tests)
- **Pull Request**: (Disabled — flakiness trends require baseline, not available for feature branches)
- **Scheduled**: Daily aggregation job (future enhancement for nightly analysis)

**Execution Steps**:

1. **Install dependencies**
   ```yaml
   - name: Install dependencies
     run: pip install -e .[dev]
   ```

2. **Run tests with detection**
   ```yaml
   - name: Run unit tests with flaky detection
     run: pytest -q tests/unit --flaky-detection --flaky-storage=.flaky-tests -v --tb=short
   ```
   - Flag `--flaky-detection` enables the pytest plugin
   - Flag `--flaky-storage` sets output directory
   - Captures all test outcomes and durations

3. **Aggregate history**
   ```yaml
   - name: Aggregate flakiness history
     run: python -c "
       from operations_center.observer import FlakyTestStorageManager, FlakyTestAggregator, FlakyTestAlertManager
       storage = FlakyTestStorageManager.create_local('.flaky-tests')
       agg = FlakyTestAggregator(storage)
       report = agg.aggregate(days=7)
       storage.save_aggregation(report)
       alerts = FlakyTestAlertManager.check_alerts(report)
       for alert in alerts:
           print(f'[{alert.severity.value.upper()}] {alert.alert_type}: {alert.description}')
     "
   ```
   - Loads all session reports from past 7 days
   - Computes aggregation (failure rates, trends)
   - Generates alerts if thresholds crossed
   - Saves daily aggregation file

4. **Upload artifacts**
   ```yaml
   - name: Upload flaky test metrics
     uses: actions/upload-artifact@v4
     with:
       name: flaky-test-metrics-${{ github.run_id }}
       path: |
         .flaky-tests/runs/
         .flaky-tests/aggregations/
       retention-days: 90
   ```
   - Stores raw session and aggregation data
   - Accessible via GitHub UI for 90 days
   - Enables historical analysis and trend visualization

5. **PR Comment** (Future: when integrated with observer)
   ```yaml
   - name: Comment on PR with flaky test summary
     uses: actions/github-script@v7
   ```
   - Posts comment on PRs introducing new flakiness
   - Shows top 5 flaky tests, affected modules
   - Links to full metrics for investigation

---

## 3. Failure Categorization & Alerting

### Alert Conditions

| Condition | Trigger | Severity | Example |
|-----------|---------|----------|---------|
| NEW_FLAKY_TEST | Test failure rate >10% + first seen <24h ago | MEDIUM | A test became flaky today |
| REGRESSION_SPIKE | Flaky count increased >50% vs previous period | HIGH | Spike from 2 to 5 flaky tests |
| CRITICAL_FLAKINESS | Failure rate >30% | HIGH | Test fails 3/10 runs consistently |
| MODULE_OUTBREAK | >20% of module's tests are flaky | MEDIUM | Module has 5/20 tests flaky |

### Alert Severity Levels

- **CRITICAL** (Red): Immediate action required, blocks deployments
- **HIGH** (Orange): Urgent, address within 1 day
- **MEDIUM** (Yellow): Monitor closely, fix within 1 sprint
- **LOW** (Blue): Informational, consider for backlog

### Alert Lifecycle

```
Detection (Aggregator) → Categorization (AlertManager) → Reporting (CI Output + Artifacts)
```

**Reporting Channels**:
1. **CI Output**: Alerts printed to job logs
2. **Artifacts**: Raw metrics for dashboard integration
3. **PR Comments**: (future) Summaries on problematic PRs
4. **Slack/Email**: (future, Stage 4) Integration via observer service

---

## 4. Local Testing

### Running Tests with Flaky Detection

```bash
# Run all unit tests with flaky detection
pytest tests/unit --flaky-detection --flaky-storage=.flaky-tests

# Run specific test suite with detection
pytest tests/unit/observer -m "not slow" --flaky-detection

# Filter to only flaky test detection tests
pytest tests/unit -m flaky --flaky-detection
```

### Viewing Results

**Session reports** (Tier 2):
```bash
ls -la .flaky-tests/runs/$(date +%Y-%m-%d)/
cat .flaky-tests/runs/2026-06-07/10-00-00-session.json | python -m json.tool
```

**Aggregation reports** (Tier 3):
```bash
ls -la .flaky-tests/aggregations/
cat .flaky-tests/aggregations/2026-06-07-aggregation.json | python -m json.tool
```

### Example Session Report Structure

```json
{
  "session_id": "test-session",
  "timestamp": "2026-06-07T10:30:45.123456+00:00",
  "duration": 120.5,
  "session_count": 250,
  "passed_count": 245,
  "failed_count": 5,
  "skipped_count": 0,
  "flaky_candidates": [
    {
      "test_name": "tests/integration/test_remote_api.py::TestRemoteAPI::test_timeout",
      "module": "tests/integration/test_remote_api.py",
      "failure_rate": 0.4,
      "run_count": 5,
      "category": "transient",
      "first_seen": "2026-06-06T14:22:00+00:00"
    }
  ],
  "unstable_candidates": [],
  "test_outcomes": [
    {
      "test_name": "tests/unit/test_foo.py::test_basic",
      "outcome": "passed",
      "duration": 0.234,
      "exception": null
    },
    {
      "test_name": "tests/integration/test_remote_api.py::TestRemoteAPI::test_timeout",
      "outcome": "failed",
      "duration": 15.789,
      "exception": "TimeoutError: request took too long"
    }
  ]
}
```

### Example Aggregation Report Structure

```json
{
  "date": "2026-06-07",
  "period_days": 7,
  "total_test_executions": 1750,
  "flaky_test_count": 4,
  "unstable_test_count": 2,
  "flaky_tests": [
    {
      "test_name": "tests/integration/test_remote_api.py::TestRemoteAPI::test_timeout",
      "failure_rate": 0.35,
      "max_failure_rate": 0.5,
      "run_count": 7,
      "trend": 0.10,
      "category": "transient",
      "first_seen": "2026-06-03T14:22:00+00:00",
      "last_failure": "2026-06-07T10:30:00+00:00",
      "recovered_at": null
    },
    {
      "test_name": "tests/unit/observer/test_snapshot_validator.py::TestSnapshotValidation::test_inconsistent",
      "failure_rate": 0.15,
      "max_failure_rate": 0.25,
      "run_count": 5,
      "trend": -0.05,
      "category": "transient",
      "first_seen": "2026-06-05T09:15:00+00:00",
      "last_failure": "2026-06-06T16:45:00+00:00",
      "recovered_at": "2026-06-07T11:00:00+00:00"
    }
  ],
  "by_module": {
    "tests/integration": {
      "flaky_count": 2,
      "total_count": 45
    },
    "tests/unit/observer": {
      "flaky_count": 1,
      "total_count": 120
    }
  },
  "by_category": {
    "transient": 3,
    "structural": 1,
    "configuration": 0,
    "unknown": 0
  },
  "recommendations": [
    {
      "priority": "high",
      "type": "focus_test",
      "description": "Fix top flaky test: tests/integration/test_remote_api.py::TestRemoteAPI::test_timeout",
      "failure_rate": 0.35,
      "category": "transient"
    },
    {
      "priority": "medium",
      "type": "environment_check",
      "description": "Check environment configuration for CI differences",
      "tests": [
        "tests/integration/test_remote_api.py::TestRemoteAPI::test_timeout",
        "tests/integration/test_external_service.py::test_connection_retry"
      ]
    }
  ]
}
```

---

## 5. Configuration & Customization

### Pytest Plugin Options

```bash
# Enable flaky detection with custom storage directory
pytest tests/unit --flaky-detection --flaky-storage=/var/flaky-metrics

# Run only flaky test detection tests (excludes other markers)
pytest tests/unit -m flaky

# Run historical aggregation tests
pytest tests/unit -m flaky_historical

# Run integration tests with observer service
pytest tests/unit -m flaky_integration
```

### Storage Configuration

**File structure**:
```
.flaky-tests/
├── runs/                           # Tier 2 session reports (3-day retention)
│   ├── 2026-06-07/
│   │   ├── 09-30-00-session.json
│   │   ├── 10-00-00-session.json
│   │   └── 10-30-00-session.json
│   ├── 2026-06-06/
│   └── 2026-06-05/
├── aggregations/                   # Tier 3 daily aggregations (90-day retention)
│   ├── 2026-06-07-aggregation.json
│   ├── 2026-06-06-aggregation.json
│   └── 2026-06-05-aggregation.json
└── README.md
```

### Configuring Retention Policies

```python
# In custom scripts or automation:
from operations_center.observer import FlakyTestStorageManager

# Create storage with custom retention
storage = FlakyTestStorageManager(
    base_path="/var/flaky-metrics",
    session_retention_days=7,      # Keep sessions longer
    aggregation_retention_days=180 # Keep aggregations for 6 months
)

# Cleanup old files
deleted_sessions = storage.cleanup_old_sessions()
deleted_aggs = storage.cleanup_old_aggregations()
print(f"Deleted {deleted_sessions} session files")
print(f"Deleted {deleted_aggs} aggregation files")
```

### Custom Alert Thresholds

Alert thresholds are currently hardcoded but can be parameterized:

```python
from operations_center.observer import FlakyTestAlertManager

# Current hardcoded thresholds:
# - NEW_FLAKY_TEST: first_seen < 24h ago
# - REGRESSION_SPIKE: flaky_count increased >50%
# - CRITICAL_FLAKINESS: failure_rate > 0.3 (30%)
# - MODULE_OUTBREAK: flaky_ratio > 0.2 (20% of module tests)

# Future: parameterizable thresholds
alerts = FlakyTestAlertManager.check_alerts(
    report,
    thresholds={
        "critical_failure_rate": 0.25,  # Lower threshold
        "regression_spike_pct": 0.75,   # Require 75% increase
        "module_flaky_ratio": 0.15,     # Lower module threshold
    }
)
```

---

## 6. Troubleshooting

### "No flaky test metrics being collected"

**Diagnosis**:
```bash
# Check if pytest plugin is loaded
pytest tests/unit --flaky-detection --setup-show -k "test_basic" -v 2>&1 | grep -i flaky

# Check if .flaky-tests directory was created
ls -la .flaky-tests/

# Check session file exists
ls -la .flaky-tests/runs/*/
```

**Solutions**:
1. Verify `--flaky-detection` flag is passed
2. Check `--flaky-storage` path is writable
3. Ensure pytest version >=8.0 (required for plugin system)
4. Check logs for `FlakyTestDetectionPlugin` registration

### "Aggregation fails to load session files"

**Diagnosis**:
```bash
# Check session file format
head -100 .flaky-tests/runs/2026-06-07/*.json

# Verify JSON is valid
python -m json.tool .flaky-tests/runs/2026-06-07/*.json > /dev/null
```

**Solutions**:
1. Check for corrupted JSON files (plugin handles these gracefully)
2. Verify session files have required fields: `session_count`, `flaky_candidates`
3. Check date format matches expected pattern (YYYY-MM-DD)

### "Alerts not being generated"

**Diagnosis**:
```bash
# Check aggregation report was created
ls -la .flaky-tests/aggregations/

# Verify alert conditions
python -c "
from operations_center.observer import FlakyTestStorageManager, FlakyTestAggregator, FlakyTestAlertManager
storage = FlakyTestStorageManager.create_local('.flaky-tests')
agg = FlakyTestAggregator(storage)
report = agg.aggregate(days=7)
print(f'Flaky count: {report.flaky_test_count}')
alerts = FlakyTestAlertManager.check_alerts(report)
print(f'Alerts: {len(alerts)}')
for a in alerts:
    print(f'  {a.alert_type}: {a.description}')
"
```

**Solutions**:
1. Ensure flaky tests exist in session data (failure_rate > 0.1)
2. Check alert condition thresholds match expectations
3. Verify aggregation is running on session data (not empty reports)

---

## 7. Integration with Observer Service

### Stage 3 Enhancement: FlakyTestCollector

The flaky test reporter integrates with the observer service via a new `FlakyTestCollector`:

```python
from operations_center.observer import FlakyTestCollector, RepoObserverService

# Automatically included in observer snapshots (Stage 3)
service = RepoObserverService(context)
snapshot = service.observe()

# Flakiness signal is included in snapshot
print(snapshot.flaky_test_signal)  # FlakyTestSignal with metrics

# Signal structure:
{
    "flaky_count": 4,
    "unstable_count": 2,
    "affected_modules": ["tests/integration", "tests/unit/observer"],
    "failure_rate_trend": 0.15,  # 15% increase over 7 days
    "category_breakdown": {
        "transient": 3,
        "structural": 1
    },
    "estimated_impact": "high"
}
```

---

## 8. Future Enhancements (Stages 4-6)

### Stage 4: Dashboard & Alerts
- Web dashboard showing flakiness trends
- Slack/email alerts for critical conditions
- Historical graphs (7d, 30d, 90d windows)
- Module-level heatmaps

### Stage 5: Advanced Analysis
- Correlation with code changes (Git blame)
- Correlation with dependency updates
- ML-based root cause prediction
- Recovery pattern analysis

### Stage 6: Automated Remediation
- Auto-quarantine critical flaky tests
- Auto-disable flaky tests on production branches
- Suggested fixes based on failure patterns
- Automated retry logic tuning

---

## 9. FAQ

**Q: Why aren't my tests marked as flaky even though they fail sometimes?**  
A: Single test run can't show flakiness. The reporter needs multiple runs over time (minimum 3 runs, threshold >10% failure rate). Wait for aggregation over 7 days of data.

**Q: How long does it take to detect new flaky tests?**  
A: Minimum 3 test runs are needed for detection (confidence threshold). With daily CI runs, new flaky tests are detected within 3 days. Critical flakiness (>30% failure rate) is detected within 1 day.

**Q: Can I disable the plugin for specific test suites?**  
A: Yes, omit `--flaky-detection` flag when running tests. The plugin is opt-in and only active when explicitly enabled.

**Q: What's the performance overhead?**  
A: <1% overhead. The plugin buffers results in memory and writes JSONL asynchronously after the session finishes.

**Q: Can I use this with pytest-xdist (parallel execution)?**  
A: Yes, the plugin integrates with xdist. Each worker buffers results independently, and the master process aggregates them on session finish.

---

## Appendix A: API Reference

### FlakyTestStorageManager

```python
storage = FlakyTestStorageManager.create_local("/path/to/.flaky-tests")

# Save session results
path = storage.save_session_results(session_data: dict) -> Path

# Save aggregation report
path = storage.save_aggregation(report: FlakyTestAggregationReport) -> Path

# Load sessions from past N days
sessions = storage.load_recent_sessions(days: int = 7) -> list[dict]

# Load aggregations from past N days
aggs = storage.load_recent_aggregations(days: int = 90) -> list[FlakyTestAggregationReport]

# Cleanup old files (returns count deleted)
deleted = storage.cleanup_old_sessions() -> int
deleted = storage.cleanup_old_aggregations() -> int
```

### FlakyTestAggregator

```python
aggregator = FlakyTestAggregator(storage)

# Aggregate flakiness over time window
report = aggregator.aggregate(days: int = 7) -> FlakyTestAggregationReport
```

### FlakyTestAlertManager

```python
# Check for alert conditions
alerts = FlakyTestAlertManager.check_alerts(
    agg_report: FlakyTestAggregationReport,
    prev_report: FlakyTestAggregationReport | None = None
) -> list[FlakyTestAlert]

# Alert structure
alert.alert_type: str  # NEW_FLAKY_TEST, REGRESSION_SPIKE, CRITICAL_FLAKINESS, MODULE_OUTBREAK
alert.severity: AlertSeverity  # CRITICAL, HIGH, MEDIUM, LOW
alert.description: str
alert.details: dict  # Specific data for this alert
```

---

**Related Documents**:
- [Stage 0 Design](./flaky-test-reporter-design.md) — Architecture and metrics
- [Stage 1 Implementation](./flaky-test-reporter-implementation.md) — Core reporter (Tier 1-2)
- [Observer Service](./observer-service.md) — Integration with OC snapshots

**Contact**: DevOps / Testing Infrastructure Team
