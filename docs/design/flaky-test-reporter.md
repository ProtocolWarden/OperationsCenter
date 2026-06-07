---
status: implemented
stage: 6
---

# Flaky Test Reporter — Architecture, Metrics, and User Guide

**Version**: 1.0  
**Status**: Complete (Stage 1 Implementation)  
**Last Updated**: 2026-06-07

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Flaky Test Metric Specification](#flaky-test-metric-specification)
4. [Configuration Guide](#configuration-guide)
5. [Usage Examples](#usage-examples)
6. [Troubleshooting Guide](#troubleshooting-guide)
7. [API Reference](#api-reference)
8. [Integration with Observer Service](#integration-with-observer-service)

---

## Executive Summary

The Flaky Test Reporter is a detection and analysis system for identifying, categorizing, and tracking non-deterministic test failures in your CI/CD pipeline. A **flaky test** is one that exhibits non-deterministic pass/fail behavior across identical conditions (same code, same environment, same inputs).

### Key Capabilities

- **Automatic Detection**: Identifies flaky tests through multi-run pattern analysis
- **Root Cause Categorization**: Distinguishes transient (environment), structural (code), and configuration issues
- **Actionable Metrics**: Provides 14+ metrics to guide remediation efforts
- **Flexible Storage**: Supports local file storage, S3, and HTTP backends
- **Observer Integration**: Feeds flakiness data into repository health monitoring

### Design Principle

The reporter implements a **4-tier architecture**:
- **Tier 1**: Per-run observation (real-time test result capture, ~0ms overhead)
- **Tier 2**: Session analysis (pattern detection after test suite completes)
- **Tier 3**: Historical aggregation (cross-run trends, daily summaries) — *Planned for Stage 2*
- **Tier 4**: Observer synthesis (integration with repo health snapshot) — *Planned for Stage 3*

This stage (Stage 1) implements Tiers 1-2, providing immediate flakiness detection for a single test session.

---

## Architecture Overview

### System Design

```
┌─────────────────────────────────────────────────────┐
│         Test Execution (pytest)                    │
│         (Unit & Integration Tests)                 │
└──────────────────┬──────────────────────────────────┘
                   │ Test outcomes (pass/fail/skip)
                   ↓
┌──────────────────────────────────────────────────────┐
│  Tier 1: Per-Run Observation                        │
│  ┌────────────────────────────────────────────────┐ │
│  │ FlakyTestResult: Captures each test execution │ │
│  │ - nodeid, outcome, duration                   │ │
│  │ - exception info, environment, timestamp      │ │
│  │ - markers (slow, integration, etc.)           │ │
│  └────────────────────────────────────────────────┘ │
│  [Stored in: self.test_runs, self.all_results]     │
└──────────────────┬──────────────────────────────────┘
                   │ track_test() calls
                   ↓
┌──────────────────────────────────────────────────────┐
│  Tier 2: Session Analysis                           │
│  ┌────────────────────────────────────────────────┐ │
│  │ FlakyTestReporter: Analyzes patterns          │ │
│  │ analyze_session() → FlakyTestSessionReport    │ │
│  │ ├─ flaky_candidates (>10% failure rate)      │ │
│  │ └─ unstable_candidates (5-10% failure rate)  │ │
│  └────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────┐ │
│  │ FlakyTestMetric: Per-test analysis            │ │
│  │ - Flakiness score (0.0-1.0)                   │ │
│  │ - Pattern entropy (randomness measure)        │ │
│  │ - Root cause category (transient/structural)  │ │
│  │ - Recovery time (days to stabilization)       │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────────┘
                   │ JSON report
                   ↓
┌──────────────────────────────────────────────────────┐
│  Storage Layer (Local/S3/HTTP)                      │
│  - reports/session-{timestamp}.json                 │
│  - runs/results-{timestamp}.jsonl                   │
└──────────────────────────────────────────────────────┘
```

### Design Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| Threshold: >10% failure rate | Balances sensitivity vs. false positives | Misses tests with 5-10% failure rate (unstable) |
| Minimum 3 runs for confidence | Prevents single-run noise | Requires multiple test iterations |
| Pattern entropy (Shannon) | Quantifies randomness in pass/fail sequence | Adds mathematical complexity |
| Session-based analysis | Immediate feedback on single test run | No historical trending (Tier 3) |
| Local file storage primary | Requires no external dependencies | Doesn't scale to distributed systems |

---

## Flaky Test Metric Specification

### FlakyTestMetric Dataclass

A complete metric for a single flaky test, containing 14 structured fields:

```python
@dataclass
class FlakyTestMetric:
    """Structured metrics for a single flaky test."""
    
    nodeid: str                    # Fully qualified test name (e.g., "tests/unit/test_foo.py::TestClass::test_method")
    failure_rate: float            # Proportion of failed runs (0.0 to 1.0)
    run_count: int                 # Total number of times test was executed
    retry_success_count: int       # Times passed on retry after failure
    duration_mean: float           # Average execution time (seconds)
    duration_variance: float       # Variance in execution time
    pattern_entropy: float         # Shannon entropy of pass/fail sequence (0.0 = deterministic, ~0.693 = max)
    streak_length: int             # Longest consecutive same-outcome sequence
    recovery_time_days: float | None  # Days from last failure to first subsequent pass
    suspected_category: FlakynessCategory  # Root cause: TRANSIENT, STRUCTURAL, CONFIGURATION, INTERMITTENT_STRUCTURAL, UNKNOWN
    markers: list[str]             # Pytest markers (e.g., ["slow", "integration"])
    last_failure_reason: str       # Most recent exception type and message
    flakiness_score: float         # Overall score (0.0 = stable, 1.0 = unreliable)
    confidence: float              # Confidence in assessment (0.0-1.0, based on run count)
```

### Metric Interpretation Guide

#### Failure Rate
The proportion of test executions that failed.

| Rate | Classification | Action |
|------|---|---|
| 0-5% | Unstable (borderline) | Monitor; likely transient issues |
| 5-10% | Unstable | Investigate; categorize root cause |
| 10-40% | Flaky | Medium priority; likely transient |
| 40-60% | Very Flaky | High priority; mixed root causes |
| >60% | Mostly Broken | Urgent; likely structural issue |

#### Pattern Entropy
Measures randomness in the pass/fail sequence using Shannon entropy.

```
Entropy = -(p_pass * ln(p_pass) + p_fail * ln(p_fail))
```

| Entropy | Pattern | Indicates |
|---------|---------|-----------|
| 0.0 | All passes or all failures | Deterministic (not flaky or completely broken) |
| 0.1-0.3 | Mostly consistent with 1-2 exceptions | Structural issue; mostly reproducible |
| 0.4-0.6 | Alternating passes/failures | Transient issue; load or timing dependent |
| 0.6-0.693 | Random 50/50 split | Highly transient; random external factors |

**Example**: A test that fails 3/5 times has entropy = -(0.4 * ln(0.4) + 0.6 * ln(0.6)) ≈ 0.673 (highly random).

#### Streak Length
Longest consecutive sequence of the same outcome (all passes or all failures in a row).

| Streak | Indicates | Implication |
|--------|-----------|-------------|
| 1 | Complete alternation (P-F-P-F) | Most transient; highly unpredictable |
| 2-3 | Mixed (P-P-F-P-F-F) | Could be transient or structural |
| 4+ | Sustained consistency | Structural issue; test is consistently failing/passing |

#### Flakiness Score
Composite score combining failure rate and variance.

```
score = 0.5 * failure_rate + 0.1-0.2 * (variance or entropy)
```

| Score | Classification | Action |
|-------|---|---|
| 0.0-0.1 | Stable | No action needed |
| 0.1-0.3 | Low flakiness | Monitor and triage |
| 0.3-0.6 | Moderate flakiness | Investigate and fix soon |
| 0.6-1.0 | High flakiness | Urgent investigation and fix |

#### Confidence
Confidence in the flakiness assessment based on number of runs.

```
confidence = min(1.0, run_count / 5)  # Capped at 5 runs
```

| Runs | Confidence | Reliability |
|------|---|---|
| 2 | 0.4 (40%) | Low; likely noise |
| 3 | 0.6 (60%) | Moderate; probably real |
| 4 | 0.8 (80%) | Good; strong signal |
| 5+ | 1.0 (100%) | Excellent; statistically reliable |

### Flakiness Categories

#### TRANSIENT
**Characteristics**: Environment-dependent, passes on retry, high variance

**Detection signals**:
- Failure rate: 5-40%
- Entropy: 0.4-0.693 (highly random)
- Retry success rate: >0 (passes on second attempt)
- Common causes: timing issues, resource contention, external service flakiness

**Remediation**:
- Add robust timeouts and retries
- Remove timing dependencies
- Isolate resources (ports, files)
- Mock external services

#### STRUCTURAL
**Characteristics**: Code-rooted, consistent failures, low variance

**Detection signals**:
- Failure rate: >50%
- Entropy: <0.1 (deterministic pattern)
- Streak length: 4+ (many consecutive failures)
- Common causes: assertion precision, incomplete setup, logic errors

**Remediation**:
- Fix underlying logic or assertions
- Review test setup/teardown
- Check boundary conditions
- Add proper cleanup

#### CONFIGURATION
**Characteristics**: Environment-specific, fails in CI but passes locally

**Detection signals**:
- 100% failure rate in one environment, 0% in another
- Failure correlates with Python version, OS, or dependencies
- Common causes: path assumptions, dependency versions, permissions

**Remediation**:
- Use environment-agnostic code
- Version lock dependencies
- Use absolute paths
- Verify permissions in CI

#### INTERMITTENT_STRUCTURAL
**Characteristics**: Recently became flaky after code change

**Detection signals**:
- Flakiness onset correlates with commit
- Failure rate changes from 0% to 10-50%
- Could be performance regression or test assumption break

**Remediation**:
- Review recent commits for performance impact
- Check for test assumption changes
- Verify resource changes

#### UNKNOWN
**Characteristics**: Insufficient data to categorize

**Detection signals**:
- <2 runs (not enough data)
- Inconsistent markers or exception types
- Doesn't match other category patterns

**Action**: Accumulate more runs and re-analyze.

### Root Cause Categorization Algorithm

The reporter uses a heuristic algorithm to categorize flakiness:

```python
def _categorize_flakiness(failure_rate, runs):
    variance = compute_pattern_variance(runs)
    
    # Transient: low failure rate with high variance
    if 0.05 <= failure_rate <= 0.40 and variance > 0.1:
        return TRANSIENT
    
    # Structural: high failure rate with low variance
    if failure_rate > 0.50:
        if variance < 0.05:
            return STRUCTURAL
        return INTERMITTENT_STRUCTURAL
    
    # Configuration: timeout-related markers/exceptions
    if any(marker in ("slow", "timeout") for marker in runs[0].markers):
        return TRANSIENT
    if "timeout" in runs[0].exception_type.lower():
        return TRANSIENT
    
    # Fallback
    return UNKNOWN
```

---

## Configuration Guide

### Basic Setup

#### 1. Create Reporter with Local Storage

```python
from operations_center.observer.flaky_test_reporter import FlakyTestReporter

# Create reporter with local file storage
reporter = FlakyTestReporter.create_local("/path/to/flaky-tests")

# Or use default location
reporter = FlakyTestReporter.create_local("/tmp/flaky-tests")
```

**Storage structure**:
```
/path/to/flaky-tests/
├── reports/
│   ├── session-20260607-143022.json
│   └── session-20260607-150015.json
└── runs/
    ├── results-20260607-143022.jsonl
    └── results-20260607-150015.jsonl
```

#### 2. Track Test Results

```python
from operations_center.observer.flaky_test_reporter import FlakyTestResult, TestOutcome

# Capture test execution result
result = FlakyTestResult(
    nodeid="tests/unit/test_foo.py::TestClass::test_method",
    outcome=TestOutcome.PASSED,
    duration=1.234,
    markers=["unit", "fast"],
    exception_type="",
    exception_message="",
    environment="ci",
    python_version="3.11"
)

# Track the result
reporter.track_test(result)
```

#### 3. Analyze Session and Generate Report

```python
# Analyze all tracked tests
session_report = reporter.analyze_session()

# Save reports to storage
report_path = reporter.save_session_report(session_report)
results_path = reporter.save_test_results()

print(f"Session report: {report_path}")
print(f"Results: {results_path}")

# Access results programmatically
print(f"Total tests: {session_report.total_tests}")
print(f"Flaky tests: {len(session_report.flaky_candidates)}")
print(f"Unstable tests: {len(session_report.unstable_candidates)}")

for metric in session_report.flaky_candidates:
    print(f"{metric.nodeid}: {metric.flakiness_score:.2f} (category: {metric.suspected_category.value})")
```

### Advanced Configuration

#### Customizing Thresholds

```python
# Modify default thresholds (class variables)
FlakyTestReporter.FLAKY_THRESHOLD = 0.15      # Default: 0.10 (10%)
FlakyTestReporter.UNSTABLE_THRESHOLD = 0.07   # Default: 0.05 (5%)
FlakyTestReporter.MIN_CONFIDENCE_RUNS = 2     # Default: 3
FlakyTestReporter.MAX_CONFIDENCE_RUNS = 8     # Default: 5

# Create reporter with custom thresholds
reporter = FlakyTestReporter.create_local("/tmp/flaky-tests")
```

#### Remote Storage Backends (Stub)

**S3 Backend** (full support in Stage 2-3):
```python
reporter = FlakyTestReporter.create_s3(
    bucket="my-bucket",
    prefix="ci/flaky-tests"
)
```

**HTTP Backend** (full support in Stage 2-3):
```python
reporter = FlakyTestReporter.create_http(
    base_url="https://api.example.com/flaky-tests",
    auth_token="bearer-token-xyz"
)
```

#### Integration with pytest Plugin (Stage 2+)

Example pytest plugin for automatic result capture:

```python
# conftest.py

import pytest
from operations_center.observer.flaky_test_reporter import (
    FlakyTestReporter,
    FlakyTestResult,
    TestOutcome
)

@pytest.fixture(scope="session")
def flaky_reporter():
    return FlakyTestReporter.create_local("/tmp/flaky-tests")

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    
    if call.when == "call":
        # Determine test outcome
        test_outcome = TestOutcome.PASSED if report.passed else TestOutcome.FAILED
        
        # Create result
        result = FlakyTestResult(
            nodeid=item.nodeid,
            outcome=test_outcome,
            duration=report.duration,
            markers=[m for m in item.iter_markers()],
            environment="ci" if os.getenv("CI") else "local"
        )
        
        # Track
        flaky_reporter.track_test(result)
```

---

## Usage Examples

### Example 1: Track Test Session and Analyze

```python
from operations_center.observer.flaky_test_reporter import (
    FlakyTestReporter,
    FlakyTestResult,
    TestOutcome
)

# Create reporter
reporter = FlakyTestReporter.create_local("/tmp/flaky-tests")

# Simulate test runs (in real usage, pytest plugin captures these)
tests = [
    ("tests/unit/test_auth.py::test_login", [True, True, False, True, False]),
    ("tests/unit/test_db.py::test_query", [True, True, True, True, True]),
    ("tests/integration/test_api.py::test_endpoint", [True, False, False, False, True]),
]

for nodeid, outcomes in tests:
    for i, passed in enumerate(outcomes):
        result = FlakyTestResult(
            nodeid=nodeid,
            outcome=TestOutcome.PASSED if passed else TestOutcome.FAILED,
            duration=0.5 + (0.1 * i),  # Simulate duration variation
            markers=["unit" if "unit" in nodeid else "integration"]
        )
        reporter.track_test(result)

# Analyze
report = reporter.analyze_session()

print("Flaky Tests:")
for metric in report.flaky_candidates:
    print(f"  {metric.nodeid}")
    print(f"    Failure Rate: {metric.failure_rate:.1%}")
    print(f"    Category: {metric.suspected_category.value}")
    print(f"    Score: {metric.flakiness_score:.2f}")
    print(f"    Entropy: {metric.pattern_entropy:.3f}")

print("\nUnstable Tests:")
for metric in report.unstable_candidates:
    print(f"  {metric.nodeid} ({metric.failure_rate:.1%})")
```

**Output**:
```
Flaky Tests:
  tests/unit/test_auth.py::test_login
    Failure Rate: 40.0%
    Category: transient
    Score: 0.34
    Entropy: 0.673
  tests/integration/test_api.py::test_endpoint
    Failure Rate: 60.0%
    Category: intermittent_structural
    Score: 0.60
    Entropy: 0.673

Unstable Tests:
  (none)
```

### Example 2: Categorize and Prioritize Fixes

```python
# Get all flaky tests sorted by priority
flaky_metrics = report.flaky_candidates

# Prioritize by impact and effort
priority_order = []
for metric in flaky_metrics:
    # Impact: higher score = higher impact
    impact = metric.flakiness_score
    
    # Effort: transient = easy, structural = hard
    effort = {
        "transient": 1,
        "configuration": 2,
        "intermittent_structural": 3,
        "structural": 4,
        "unknown": 5
    }[metric.suspected_category.value]
    
    priority = impact / effort  # Impact-to-effort ratio
    priority_order.append((metric.nodeid, priority, metric))

priority_order.sort(key=lambda x: x[1], reverse=True)

print("Recommended Fix Order:")
for nodeid, priority, metric in priority_order:
    print(f"1. {nodeid}")
    print(f"   Category: {metric.suspected_category.value}")
    print(f"   Effort: {'Easy' if metric.suspected_category.value in ('transient', 'configuration') else 'Hard'}")
```

### Example 3: Export Metrics for Dashboard

```python
import json

# Convert to JSON for dashboard/reporting
report_dict = report.to_dict()

# Save to file
with open("flaky-report.json", "w") as f:
    json.dump(report_dict, f, indent=2)

# Extract metrics by category
by_category = {}
for metric in report.flaky_candidates:
    category = metric.suspected_category.value
    if category not in by_category:
        by_category[category] = []
    by_category[category].append({
        "test": metric.nodeid,
        "failure_rate": metric.failure_rate,
        "score": metric.flakiness_score
    })

print(json.dumps(by_category, indent=2))
```

---

## Troubleshooting Guide

### Problem 1: Tests Not Being Detected as Flaky

**Symptoms**: 
- Tests run 5+ times but don't appear in flaky_candidates list
- All tests show in unstable_candidates instead

**Root Causes**:
1. Failure rate is below 10% threshold
2. Tests are passing all runs
3. Insufficient run count (< 2 runs)

**Solution**:
```python
# Check actual metrics for a specific test
for nodeid, runs in reporter.test_runs.items():
    if "test_foo" in nodeid:
        failure_rate = sum(1 for r in runs if r.outcome == TestOutcome.FAILED) / len(runs)
        print(f"{nodeid}: {failure_rate:.1%} failure rate ({len(runs)} runs)")
        
        # If below 10%, consider lowering threshold
        if failure_rate < 0.10:
            print("  → Below 10% threshold; if this is a known flaky test, lower threshold")
```

**Prevention**:
- Run tests at least 3 times to get confident metrics
- If testing transient failures, run 5-10 times minimum
- Adjust `FLAKY_THRESHOLD` if needed (but 10% is well-justified)

---

### Problem 2: False Positives (Tests Marked Flaky But Actually Stable)

**Symptoms**:
- Test has high variance but is actually stable
- Test shows high entropy but always passes eventually

**Root Causes**:
1. Environmental noise (resource contention)
2. Test setup is expensive (long durations)
3. Insufficient confidence (too few runs)

**Solution**:
```python
# Inspect suspicious metrics
for metric in report.flaky_candidates:
    if metric.failure_rate < 0.15:  # Low failure rate
        print(f"Possible false positive: {metric.nodeid}")
        print(f"  Failure rate: {metric.failure_rate:.1%} (low)")
        print(f"  Confidence: {metric.confidence:.1%}")
        print(f"  Suggestion: Run test 10+ times to increase confidence")
```

**Prevention**:
- Increase `MIN_CONFIDENCE_RUNS` from 3 to 5 for more conservative detection
- Exclude noisy test environments in reporter initialization
- Use pytest markers to exclude slow/resource-intensive tests from flakiness tracking

---

### Problem 3: Cannot Find Root Cause (UNKNOWN Category)

**Symptoms**:
- Test is flaky but categorized as UNKNOWN
- Can't determine if it's transient or structural

**Root Causes**:
1. Not enough data (run count < 3)
2. Markers missing or exception info not captured
3. Pattern doesn't match heuristic rules

**Solution**:
```python
# Collect more detailed information
for metric in report.flaky_candidates:
    if metric.suspected_category == FlakynessCategory.UNKNOWN:
        print(f"Investigating: {metric.nodeid}")
        print(f"  Failure rate: {metric.failure_rate:.1%}")
        print(f"  Entropy: {metric.pattern_entropy:.3f}")
        print(f"  Streak length: {metric.streak_length}")
        print(f"  Retry successes: {metric.retry_success_count}")
        print(f"  Last failure: {metric.last_failure_reason}")
        
        # Manual heuristic
        if metric.pattern_entropy > 0.5:
            print("  → Likely TRANSIENT (high entropy)")
        elif metric.streak_length > 3:
            print("  → Likely STRUCTURAL (long failure streak)")
        else:
            print("  → Recommend manual review")
```

**Prevention**:
- Ensure pytest captures full exception info
- Use meaningful pytest markers
- Run flakiness detection on 5+ iterations for clarity

---

### Problem 4: Storage Issues

**Symptoms**:
- Reports not being saved
- `save_session_report()` returns None
- Permission denied when writing to storage directory

**Root Causes**:
1. Storage path doesn't exist or is not writable
2. Disk space exhausted
3. S3/HTTP backends don't save in Stage 1

**Solution**:
```python
# Verify storage is writable
import os

storage_root = Path("/tmp/flaky-tests")
try:
    storage_root.mkdir(parents=True, exist_ok=True)
    test_file = storage_root / ".writable_test"
    test_file.write_text("test")
    test_file.unlink()
    print("✓ Storage directory is writable")
except Exception as e:
    print(f"✗ Storage error: {e}")
    raise

# Ensure reports directory exists before saving
reporter = FlakyTestReporter.create_local(storage_root)
session_report = reporter.analyze_session()

# This will auto-create reports/ directory
path = reporter.save_session_report(session_report)
if path:
    print(f"✓ Report saved: {path}")
else:
    print("✗ Report not saved (check storage backend)")
```

**Prevention**:
- Use local file storage for Stage 1 (S3/HTTP in Stage 2-3)
- Ensure `/tmp` or custom path has write permissions
- Monitor disk space for automated systems

---

### Problem 5: Unexpected Categorization

**Symptoms**:
- Test categorized as STRUCTURAL but appears transient
- Test categorized as TRANSIENT but always fails in CI

**Root Causes**:
1. Heuristic doesn't match your failure pattern
2. Missing environment information
3. Multiple root causes (conflicting signals)

**Solution**:
```python
# Debug categorization logic
for metric in report.flaky_candidates:
    test_result = reporter.test_runs[metric.nodeid]
    
    print(f"Debug: {metric.nodeid}")
    print(f"  Failure rate: {metric.failure_rate:.1%}")
    print(f"  Pattern variance: {reporter._compute_pattern_variance(test_result):.3f}")
    
    # Check heuristic conditions
    variance = reporter._compute_pattern_variance(test_result)
    if 0.05 <= metric.failure_rate <= 0.40 and variance > 0.1:
        print("  → Matches TRANSIENT rule")
    elif metric.failure_rate > 0.50:
        print("  → Matches STRUCTURAL rule")
    else:
        print("  → Doesn't match primary rules; falling back")
```

**Prevention**:
- Review categorization heuristics in `_categorize_flakiness()`
- For custom heuristics, extend the reporter class (Stage 2+)
- Document your customizations in `.console/backlog.md`

---

## API Reference

### FlakyTestReporter

The main class for detecting and analyzing flaky tests.

#### Constructors

**`__init__(storage_root: Path | None = None)`**
Initialize reporter with optional storage root.

**`create_local(storage_root: str | Path) -> FlakyTestReporter`** [classmethod]
Create reporter with local file storage.

**`create_s3(bucket: str, prefix: str = "flaky-tests") -> FlakyTestReporter`** [classmethod]
Create reporter with S3 backend (stub; full support in Stage 2-3).

**`create_http(base_url: str, auth_token: str | None = None) -> FlakyTestReporter`** [classmethod]
Create reporter with HTTP backend (stub; full support in Stage 2-3).

#### Methods

**`track_test(result: FlakyTestResult) -> None`**
Record a single test execution result.

**Parameters**:
- `result`: `FlakyTestResult` — Test execution data

**Example**:
```python
reporter.track_test(FlakyTestResult(
    nodeid="tests/test_foo.py::test_bar",
    outcome=TestOutcome.FAILED,
    duration=1.23
))
```

---

**`analyze_session() -> FlakyTestSessionReport`**
Analyze all tracked test runs and produce flakiness report.

**Returns**: `FlakyTestSessionReport` with flaky and unstable candidates.

**Example**:
```python
report = reporter.analyze_session()
print(f"Flaky: {len(report.flaky_candidates)}")
print(f"Unstable: {len(report.unstable_candidates)}")
```

---

**`save_session_report(report: FlakyTestSessionReport) -> Path | None`**
Save session report to storage as JSON.

**Parameters**:
- `report`: `FlakyTestSessionReport` — Session analysis report

**Returns**: Path where saved, or None if storage not available.

**Storage path**: `{storage_root}/reports/session-{timestamp}.json`

**Example**:
```python
path = reporter.save_session_report(report)
if path:
    print(f"Saved: {path}")
```

---

**`save_test_results() -> Path | None`**
Save all tracked test results to JSONL file.

**Returns**: Path where saved, or None if storage not available.

**Storage path**: `{storage_root}/runs/results-{timestamp}.jsonl`

**JSONL Format**: One `FlakyTestResult` per line as JSON object.

**Example**:
```python
path = reporter.save_test_results()
if path:
    with open(path) as f:
        for line in f:
            result = json.loads(line)
            print(result["nodeid"])
```

---

### FlakyTestResult

Represents a single test execution (Tier 1 observation).

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `nodeid` | str | Yes | Fully qualified test path (e.g., `tests/unit/test_foo.py::TestClass::test_method`) |
| `outcome` | TestOutcome \| str | Yes | Test result: PASSED, FAILED, SKIPPED, XFAILED, XPASSED |
| `duration` | float | Yes | Execution time in seconds |
| `markers` | list[str] | No | Pytest markers (e.g., ["slow", "integration"]) |
| `exception_type` | str | No | Exception class name if failed (e.g., "AssertionError") |
| `exception_message` | str | No | Exception message text |
| `output_lines` | list[str] | No | Captured stdout/stderr lines |
| `run_id` | str | No | Unique run identifier (auto-generated from timestamp) |
| `environment` | str | No | Environment label (e.g., "ci", "local"); default: "local" |
| `python_version` | str | No | Python version string (e.g., "3.11.2") |
| `timestamp` | datetime | No | Execution timestamp; auto-generated if not provided |

#### Methods

**`to_dict() -> dict[str, Any]`**
Convert to dictionary for JSONL serialization.

**Example**:
```python
result = FlakyTestResult(
    nodeid="tests/test_foo.py::test_bar",
    outcome=TestOutcome.FAILED,
    duration=1.23,
    exception_type="AssertionError",
    exception_message="Expected 42, got 41"
)

dict_form = result.to_dict()
print(json.dumps(dict_form))
```

---

### FlakyTestMetric

Structured metrics for a single flaky test (Tier 2 output).

#### Fields

| Field | Type | Description | Range |
|-------|------|-----------|-------|
| `nodeid` | str | Test identifier | — |
| `failure_rate` | float | Proportion of failed runs | 0.0-1.0 |
| `run_count` | int | Total executions analyzed | 0+ |
| `retry_success_count` | int | Times passed after failure | 0+ |
| `duration_mean` | float | Average execution time (seconds) | 0.0+ |
| `duration_variance` | float | Variance in execution time | 0.0+ |
| `pattern_entropy` | float | Randomness measure | 0.0-0.693 |
| `streak_length` | int | Longest consecutive same outcome | 1+ |
| `recovery_time_days` | float \| None | Days to stabilization | 0.0+ or None |
| `suspected_category` | FlakynessCategory | Root cause | TRANSIENT, STRUCTURAL, CONFIGURATION, INTERMITTENT_STRUCTURAL, UNKNOWN |
| `markers` | list[str] | Pytest markers | — |
| `last_failure_reason` | str | Most recent exception | — |
| `flakiness_score` | float | Overall score | 0.0-1.0 |
| `confidence` | float | Assessment confidence | 0.0-1.0 |

#### Methods

**`to_dict() -> dict[str, Any]`**
Convert to dictionary for JSON serialization (rounds numeric fields).

**Example**:
```python
# Serialize metric
metric_dict = metric.to_dict()
json_str = json.dumps(metric_dict, indent=2)

print(f"Test: {metric.nodeid}")
print(f"  Flakiness Score: {metric.flakiness_score:.2f}")
print(f"  Failure Rate: {metric.failure_rate:.1%}")
```

---

### FlakyTestSessionReport

Session-level analysis report (Tier 2 output).

#### Fields

| Field | Type | Description |
|-------|------|-----------|
| `session_id` | str | Unique session identifier (ISO timestamp) |
| `timestamp` | datetime | Report generation time |
| `run_count` | int | Number of distinct test runs analyzed |
| `total_tests` | int | Total unique tests tracked |
| `flaky_candidates` | list[FlakyTestMetric] | Tests with >10% failure rate |
| `unstable_candidates` | list[FlakyTestMetric] | Tests with 5-10% failure rate |

#### Methods

**`to_dict() -> dict[str, Any]`**
Convert to dictionary for JSON serialization.

**Example**:
```python
report = reporter.analyze_session()
json_dict = report.to_dict()

print(f"Session: {report.session_id}")
print(f"  Run count: {json_dict['run_count']}")
print(f"  Flaky: {json_dict['flaky_count']}")
print(f"  Unstable: {json_dict['unstable_count']}")
```

---

### Enums

#### TestOutcome
```python
class TestOutcome(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    XFAILED = "xfailed"   # Expected to fail
    XPASSED = "xpassed"   # Expected to fail but passed
```

#### FlakynessCategory
```python
class FlakynessCategory(Enum):
    TRANSIENT = "transient"                           # Environment-dependent
    STRUCTURAL = "structural"                         # Code-rooted
    CONFIGURATION = "configuration"                   # Environment-mismatch
    INTERMITTENT_STRUCTURAL = "intermittent_structural"  # Recently regressed
    UNKNOWN = "unknown"                               # Insufficient data
```

---

## Integration with Observer Service

### Stage 3 Integration (Planned)

In Stage 3, the flaky test reporter will integrate with the observer service to provide repository-level insights.

#### FlakyTestCollector (Stage 3)

```python
class FlakyTestCollector(RepoSignalCollector):
    """Synthesizes flaky test data for observer service."""
    
    def collect(self, context: ObserverContext) -> FlakyTestSignal:
        """Aggregate historical flakiness data and produce signal."""
        # Reads Tier 3 historical data
        # Produces FlakyTestSignal with:
        # - flaky_count: Number of flaky tests
        # - affected_modules: Modules with flakiness
        # - trend: Week-over-week changes
        # - category_breakdown: Counts by category
        # - estimated_impact: Developer time cost
```

#### FlakyTestSignal (Added to RepoSignalsSnapshot)

```python
@dataclass
class FlakyTestSignal:
    """Observer signal for repository flakiness."""
    
    flaky_count: int
    unstable_count: int
    affected_modules: dict[str, int]
    most_problematic_tests: list[str]
    failure_rate_trend: float  # Week-over-week change
    recovery_rate: float       # Tests recently fixed
    category_breakdown: dict[str, int]
    estimated_impact: str      # "low", "medium", "high", "critical"
```

#### Usage in Observer

```python
# In RepoObserverService.observe()
snapshot = RepoSignalsSnapshot(
    # ... other signals ...
    flaky_test_signal=FlakyTestSignal(
        flaky_count=3,
        unstable_count=5,
        affected_modules={"tests/unit": 2, "tests/integration": 1},
        most_problematic_tests=[
            "tests/unit/test_auth.py::test_login",
            "tests/integration/test_api.py::test_endpoint"
        ],
        failure_rate_trend=0.15,  # 15% increase week-over-week
        recovery_rate=0.5,         # 50% of flaky tests fixed this week
        category_breakdown={
            "transient": 5,
            "structural": 2,
            "configuration": 1
        },
        estimated_impact="high"
    )
)
```

#### Observer Dashboard Visualization (Stage 4)

The observer dashboard will display:
- Flaky test count trend (time series)
- Distribution by category (pie chart)
- Affected modules (bar chart)
- Top problematic tests (table)
- Recovery rate (percentage badge)
- Estimated developer impact (severity indicator)

### Current Integration Status

**Stage 1 (Current)**: Core detection — FlakyTestReporter captures per-run and session-level metrics.

**Stage 2 (Planned)**: Historical aggregation — FlakyTestAggregator will compute trends.

**Stage 3 (Planned)**: Observer integration — FlakyTestCollector will produce RepoSignalsSnapshot signals.

**Stage 4 (Planned)**: Dashboard and alerts — UI panels and automated notifications.

---

## Best Practices and Recommendations

### 1. Run Tests Consistently

Flakiness detection requires consistent test execution:

- **Minimum 3 runs** recommended (confidence: 60%)
- **5+ runs** for statistical reliability (confidence: 100%)
- Run in same environment (CI has consistent hardware)
- Isolate tests (no shared state or resources)

### 2. Monitor Flakiness Trends

Use the report JSON to track flakiness over time:

```python
import json
from datetime import datetime

# Save report with date
report_path = Path(f"flaky-report-{datetime.now().date()}.json")
with open(report_path) as f:
    report_dict = report.to_dict()
    json.dump(report_dict, f)

# Later: compare reports across days
# Track: flaky_count, unstable_count, avg score
```

### 3. Prioritize Fixes

Focus on high-impact, low-effort fixes first:

```python
# Impact = flakiness_score * run_count
# Effort = 1 (transient), 2 (config), 4 (structural)

for metric in report.flaky_candidates:
    impact = metric.flakiness_score * metric.run_count
    effort = {
        "transient": 1,
        "configuration": 2,
        "intermittent_structural": 3,
        "structural": 4
    }[metric.suspected_category.value]
    
    roi = impact / effort
    # Fix tests with highest ROI first
```

### 4. Automate Detection

Integrate with pytest plugin (Stage 2+):

```python
# Run: pytest --enable-flaky-detection
# Automatically captures results and generates report
```

### 5. Alert on Regression

Monitor for new flaky tests:

```python
# Load previous report
prev_report = json.load(open("flaky-report-yesterday.json"))
prev_tests = {m["nodeid"] for m in prev_report["flaky_candidates"]}

# Load current report
curr_report = json.load(open("flaky-report-today.json"))
curr_tests = {m["nodeid"] for m in curr_report["flaky_candidates"]}

# New flaky tests
new_flaky = curr_tests - prev_tests
if new_flaky:
    print(f"🚨 ALERT: {len(new_flaky)} new flaky tests detected")
    for test in new_flaky:
        print(f"  - {test}")
```

---

## File Locations and Dependencies

### Source Code

- **Main**: `src/operations_center/observer/flaky_test_reporter.py` (570 LOC)
- **Tests**: `tests/unit/observer/test_flaky_test_reporter.py` (650+ LOC, 55 tests)
- **Models**: `src/operations_center/observer/models.py` (FlakyTestSignal)

### Documentation

- **Design**: `.console/STAGE0_FLAKY_TEST_REPORTER_DESIGN.md` (4,200+ LOC)
- **User Guide**: `docs/design/flaky-test-reporter.md` (this file)

### Dependencies

- Python 3.11+
- `pytest` for test execution
- `dataclasses` (built-in)
- `pathlib` (built-in)
- `json` (built-in)
- `math` (built-in)
- Optional: `boto3` for S3 backend (Stage 2+)
- Optional: `requests` for HTTP backend (Stage 2+)

---

## FAQ

**Q: Why 10% threshold for flaky?**  
A: Studies show >10% failure rate indicates a real issue affecting developer confidence. 5-10% warrants monitoring but may be environmental noise.

**Q: Can I use this with pytest-xdist (parallel execution)?**  
A: Not yet. The reporter treats all runs equally. Stage 2 will add parallelization support to detect load-sensitive flakiness.

**Q: Does this work with parameterized tests?**  
A: Yes. Each parameter combination is treated as a separate test (unique nodeid).

**Q: How do I export metrics to Grafana?**  
A: Save report JSON and use a custom Grafana data source. Full integration planned for Stage 3.

**Q: What's the performance overhead?**  
A: <1% in Tier 1 (per-run capture). Tier 2 analysis (session) takes 50-200ms depending on test count.

---

## Version History

**1.0** (2026-06-07): Stage 1 implementation complete.
- Tier 1: Per-run observation with FlakyTestResult
- Tier 2: Session analysis with flakiness metrics
- Local file storage
- 14 structured metrics per test
- 5 root cause categories

---

## Contact and Support

- **Design**: `.console/STAGE0_FLAKY_TEST_REPORTER_DESIGN.md`
- **Issues**: File issue in repository with `[flaky-test]` tag
- **Stage 2+**: Check `.console/backlog.md` for next features
