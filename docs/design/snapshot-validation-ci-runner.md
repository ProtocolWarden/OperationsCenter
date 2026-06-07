---
status: implemented
stage: 5
---

# Snapshot Validation CI Runner — Complete Guide

## Overview

This document provides a comprehensive guide to the Snapshot Validation CI Runner implementation, covering the architecture, snapshot format specification, versioning strategy, runbook for operations, and configuration guide for extending the system.

**Related Documentation:**
- [snapshot-validation-ci-integration.md](./snapshot-validation-ci-integration.md) — High-level architecture and design decisions

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Snapshot Format Specification](#2-snapshot-format-specification)
3. [Snapshot Versioning Strategy](#3-snapshot-versioning-strategy)
4. [Runbook: Collection, Update, and Troubleshooting](#4-runbook)
5. [Snapshot Structure Examples](#5-snapshot-structure-examples)
6. [Validation Logic Examples](#6-validation-logic-examples)
7. [Configuration Guide for New Test Scenarios](#7-configuration-guide)
8. [API Reference](#8-api-reference)

---

## 1. Architecture Overview

### 1.1 High-Level System Design

```
Repository State
        ↓
    Collector Phase (src/operations_center/observer/collectors/)
        ↓
    RepoStateSnapshot (models.py)
        ↓
    Serialization (JSON/YAML/JSONL formats)
        ↓
    SnapshotRepository (snapshot_repository.py)
        ├─ LocalSnapshotRepository (filesystem)
        ├─ S3SnapshotRepository (AWS S3)
        └─ HTTPSnapshotRepository (generic HTTP/REST)
        ↓
    SnapshotManager (snapshot_manager.py)
        ├─ save_snapshot()
        ├─ get_snapshot()
        ├─ list_snapshots()
        ├─ compare_snapshots()
        └─ delete_snapshot()
        ↓
    SnapshotValidator (snapshot_validator.py)
        ├─ Layer 1: Schema Validation
        ├─ Layer 2: Completeness Validation
        ├─ Layer 3: Consistency Validation
        ├─ Layer 4: Real-World Accuracy Validation
        └─ Layer 5: Regression Detection
        ↓
    CI Job (.github/workflows/ci.yml)
        ├─ Pull Requests: Layers 1-3 (quick)
        ├─ Push: Layers 1-5 (full)
        └─ Scheduled: Layers 1-5 (nightly regression detection)
```

### 1.2 Core Components

**SnapshotRepository (snapshot_repository.py)**
- Abstract base class defining the repository interface
- Implementations: LocalSnapshotRepository, S3SnapshotRepository, HTTPSnapshotRepository
- Handles storage, retrieval, comparison, and cleanup of snapshots
- Supports multiple formats: JSON, YAML, JSONL

**SnapshotManager (snapshot_manager.py)**
- High-level API for snapshot operations
- Factory methods for creating managers with different backends
- Provides CRUD operations and snapshot comparison
- Handles snapshot retention and cleanup

**SnapshotValidator (snapshot_validator.py)**
- Validates snapshots against repository state
- Implements 5-layer validation architecture
- Returns detailed SnapshotValidationReport with categorized errors
- Supports retry logic for transient failures

**RepoStateSnapshot (models.py)**
- Pydantic model representing complete repository state
- Contains 16 signals capturing test results, dependencies, lint, coverage, etc.
- Tracks collector errors for each signal
- Includes metadata: run_id, observed_at, observer_version, source_command

### 1.3 Execution Flow in CI

```
┌─── PR Trigger ───┐
│                  │
├─ Generate Snapshot (existing: ObserverService.observe_repo())
├─ Validate Layer 1: Schema (JSON ↔ Pydantic)
├─ Validate Layer 2: Completeness (required signals present)
├─ Validate Layer 3: Consistency (cross-signal checks)
├─ Return result (PASS/FAIL)
│
└──► Exit Code 0 (pass) or 1 (fail)

┌─── Push Trigger ───┐
│                    │
├─ Generate Snapshot
├─ Validate Layers 1-5 (all layers, including slow tests)
├─ Compare vs baseline (Layer 5: regression detection)
└──► Exit Code 0 or 1

┌─── Schedule Trigger (nightly) ───┐
│                                   │
├─ Load latest snapshot from storage
├─ Validate Layers 1-5
├─ Detect regressions without code changes
└──► Alert on failures (eventual feature)
```

---

## 2. Snapshot Format Specification

### 2.1 Primary Format: JSON

**File Name:** `repo_state_snapshot.json`

**Structure:**

```json
{
  "run_id": "obs_20260607T120000Z_abc123_x7k9m",
  "observed_at": "2026-06-07T12:00:00+00:00",
  "observer_version": 1,
  "source_command": "operations-center observe-repo",
  "repo": {
    "name": "operations-center",
    "path": "/home/dev/ops",
    "current_branch": "main",
    "base_branch": "main",
    "is_dirty": false,
    "head_commit": "abc123def456",
    "head_commit_message": "fix: Resolve snapshot validation issue"
  },
  "signals": {
    "test_signal": {
      "status": "passing",
      "test_count": 7587,
      "passed_count": 7587,
      "failed_count": 0,
      "skipped_count": 0,
      "execution_time_seconds": 28.5,
      "parse_errors": {
        "status": "success",
        "error_count": 0,
        "message": null
      }
    },
    "dependency_drift": {
      "status": "healthy",
      "total_dependencies": 145,
      "outdated_count": 8,
      "critical_advisories": 0,
      "parse_errors": {
        "status": "success",
        "error_count": 0,
        "message": null
      }
    },
    "lint_signal": {
      "status": "clean",
      "violation_count": 0,
      "warning_count": 0,
      "parse_errors": {
        "status": "success",
        "error_count": 0,
        "message": null
      }
    },
    "type_signal": {
      "status": "clean",
      "error_count": 0,
      "parse_errors": {
        "status": "success",
        "error_count": 0,
        "message": null
      }
    },
    "todo_signal": {
      "status": "passing",
      "todo_count": 12,
      "fixme_count": 3,
      "parse_errors": {
        "status": "success",
        "error_count": 0,
        "message": null
      }
    },
    "coverage_signal": {
      "status": "healthy",
      "total_coverage_pct": 75.3,
      "line_coverage_pct": 78.5,
      "branch_coverage_pct": 68.2,
      "parse_errors": {
        "status": "success",
        "error_count": 0,
        "message": null
      }
    },
    "recent_commits": [
      {
        "hash": "abc123def456",
        "author": "Alice <alice@example.com>",
        "message": "fix: Resolve snapshot validation issue",
        "timestamp": "2026-06-07T10:30:00+00:00"
      }
    ],
    "file_hotspots": [
      {
        "path": "src/operations_center/observer/snapshot_validator.py",
        "change_count": 15,
        "last_modified": "2026-06-07T10:30:00+00:00"
      }
    ]
  },
  "collector_errors": {
    "architecture_signal": "Module analysis timed out (exceeded 30s limit)",
    "security_signal": "Security scanner not available"
  }
}
```

### 2.2 Secondary Format: YAML

**File Name:** `repo_state_snapshot.yaml`

Same structure as JSON, but in YAML format. Useful for:
- Manual inspection and editing
- Version control diffs
- Configuration files

```yaml
run_id: obs_20260607T120000Z_abc123_x7k9m
observed_at: '2026-06-07T12:00:00+00:00'
observer_version: 1
source_command: operations-center observe-repo
repo:
  name: operations-center
  path: /home/dev/ops
  current_branch: main
  base_branch: main
  is_dirty: false
signals:
  test_signal:
    status: passing
    test_count: 7587
    passed_count: 7587
    parse_errors:
      status: success
```

### 2.3 Append-Only Format: JSONL

**File Name:** `snapshots.index`

Used for snapshot metadata tracking and bulk operations:

```jsonl
{"run_id": "obs_20260607T120000Z_abc123_x7k9m", "observed_at": "2026-06-07T12:00:00+00:00", "source_command": "observe-repo", "status": "valid"}
{"run_id": "obs_20260607T131500Z_def456_y2p3q", "observed_at": "2026-06-07T13:15:00+00:00", "source_command": "observe-repo", "status": "valid"}
```

### 2.4 Snapshot Metadata

**Stored alongside snapshot:**

```json
{
  "run_id": "obs_20260607T120000Z_abc123_x7k9m",
  "observed_at": "2026-06-07T12:00:00+00:00",
  "format": "json",
  "version": 1,
  "path": "tools/report/operations_center/observer/obs_20260607T120000Z_abc123_x7k9m/repo_state_snapshot.json",
  "checksum": "sha256:abc123def456..."
}
```

### 2.5 Storage Locations

**Local Storage:**
```
$SNAPSHOT_ROOT/
├── obs_20260607T120000Z_abc123_x7k9m/
│   ├── repo_state_snapshot.json
│   ├── repo_state_snapshot.yaml
│   └── validation_report.json
├── obs_20260607T131500Z_def456_y2p3q/
│   └── repo_state_snapshot.json
├── snapshots.index
└── .gitkeep
```

**S3 Storage:**
```
s3://{bucket_name}/{prefix}/
├── obs_20260607T120000Z_abc123_x7k9m/repo_state_snapshot.json
├── obs_20260607T131500Z_def456_y2p3q/repo_state_snapshot.json
└── snapshots.index
```

**HTTP/REST Storage:**
```
{base_url}/snapshots/
├── obs_20260607T120000Z_abc123_x7k9m
├── obs_20260607T131500Z_def456_y2p3q
└── snapshots.index
```

---

## 3. Snapshot Versioning Strategy

### 3.1 Version Numbering

**Current Version:** `observer_version = 1`

**Versioning Scheme:**
- Semver-inspired: `observer_version` is an integer
- Bumped ONLY when snapshot schema changes (new signals, breaking changes)
- NOT bumped for: bug fixes, documentation changes, workflow changes

### 3.2 When to Bump Version

**Increment on:**
- New signal added to `RepoSignalsSnapshot`
- Existing signal structure changed (field removed, field type changed)
- Required field becomes optional or vice versa
- Breaking change to collector behavior

**Do NOT increment on:**
- New optional signals with "unavailable" status
- Bug fixes that don't affect schema
- Addition of collector_errors entries
- Changes to validation layers or CI workflow

### 3.3 Migration Strategy

When `observer_version` is incremented:

1. **Old version still supported** (backward compatibility)
2. **Migration helpers written:**
   ```python
   def migrate_v1_to_v2(snapshot_v1: dict) -> dict:
       """Migrate RepoStateSnapshot from v1 to v2 schema."""
       snapshot_v2 = snapshot_v1.copy()
       snapshot_v2['observer_version'] = 2
       # ... add new fields with defaults
       return snapshot_v2
   ```

3. **CI validation tool handles multiple versions:**
   ```python
   def load_snapshot(run_id: str) -> RepoStateSnapshot:
       data = repository.load_raw(run_id)
       version = data.get('observer_version', 1)
       if version == 1:
           return RepoStateSnapshot.model_validate(data)
       elif version == 2:
           migrated = migrate_v1_to_v2(data)
           return RepoStateSnapshot.model_validate(migrated)
       else:
           raise ValueError(f"Unsupported version: {version}")
   ```

### 3.4 Baseline Snapshots

**Baseline = "source of truth" snapshot for a branch**

- One baseline per branch (main, develop, feature-*, etc.)
- Created manually or by promotion workflow
- Stored with special naming: `baseline_{branch_name}.json`
- Used for regression detection (Layer 5)

**Baseline Operations:**
```bash
# Promote current snapshot to baseline
./scripts/snapshot-promote-baseline.sh main

# Load baseline for regression detection
BASELINE=$(find $SNAPSHOT_ROOT -name "baseline_main.json" -type f)
```

---

## 4. Runbook: Collection, Update, and Troubleshooting

### 4.1 Snapshot Collection

#### Automatic Collection (CI)

Triggered by:
- Pull requests (to main, develop)
- Pushes (to any branch)
- Scheduled runs (nightly at 2 AM UTC)

**Workflow:**

```yaml
# In .github/workflows/ci.yml
- name: Generate snapshot
  run: |
    python -m operations_center observe-repo \
      --output-dir $SNAPSHOT_ROOT \
      --format json \
      --include-slow-signals
```

**Environment Variables:**
```bash
SNAPSHOT_ROOT=${RUNNER_TEMP}/snapshots  # CI runner temp directory
SNAPSHOT_RETENTION_DAYS=30               # Keep snapshots for 30 days
SNAPSHOT_RETENTION_COUNT=50              # Keep last 50 snapshots
```

#### Manual Collection (Local)

```bash
# Generate snapshot in current repository
python -m operations_center observe-repo \
  --output-dir ./snapshots \
  --format json

# List generated snapshots
ls -lh ./snapshots/

# Inspect snapshot structure
python -m json.tool ./snapshots/obs_*/repo_state_snapshot.json | head -50
```

#### Collection with Remote Storage

```bash
# S3 backend
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

python -c "
from operations_center.observer.snapshot_manager import SnapshotManager
from operations_center.observer.service import ObserverService

# Generate snapshot
observer = ObserverService()
snapshot = observer.observe_repo()

# Save to S3
manager = SnapshotManager.create_s3(
    bucket_name='my-org-snapshots',
    prefix='operations-center'
)
manager.save_snapshot(snapshot)
"

# HTTP backend
python -c "
manager = SnapshotManager.create_http(
    base_url='https://snapshot-server.example.com',
    auth_token='bearer_token_here'
)
manager.save_snapshot(snapshot)
"
```

### 4.2 Snapshot Updates

#### When to Update Baseline

Update baseline snapshots when:
- Major refactoring introduces expected metric changes
- Performance improvements validated and should be new baseline
- New signals added to repository
- Threshold adjustments require approval

**Promotion Workflow:**

```bash
#!/bin/bash
# ./scripts/snapshot-promote-baseline.sh

BRANCH=${1:-main}
SNAPSHOT_ROOT=${SNAPSHOT_ROOT:-./snapshots}

# Find latest snapshot
LATEST=$(ls -t1 "$SNAPSHOT_ROOT"/obs_*/repo_state_snapshot.json | head -1)

if [ -z "$LATEST" ]; then
    echo "No snapshots found in $SNAPSHOT_ROOT"
    exit 1
fi

# Promote to baseline
BASELINE="$SNAPSHOT_ROOT/baseline_${BRANCH}.json"
cp "$LATEST" "$BASELINE"

echo "Promoted $LATEST to $BASELINE"
echo "Don't forget to commit: git add $BASELINE"
```

**Usage:**
```bash
./scripts/snapshot-promote-baseline.sh main
git add snapshots/baseline_main.json
git commit -m "chore: Update snapshot baseline for main branch"
```

#### Updating Tolerance Thresholds

For regression detection (Layer 5), update tolerance in environment:

```bash
# In .github/workflows/ci.yml
env:
  SNAPSHOT_TOLERANCE: 0.05  # 5% variance allowed for dynamic metrics
  SNAPSHOT_COVERAGE_TOLERANCE: 0.02  # 2% for coverage specifically
```

Or programmatically:

```python
from operations_center.observer.snapshot_validator import SnapshotValidator

validator = SnapshotValidator(
    snapshot,
    repo_path=Path.cwd(),
    tolerance=0.05,  # 5%
    coverage_tolerance=0.02  # 2%
)
```

### 4.3 Troubleshooting Guide

#### Issue: Snapshot Validation Fails on PR

**Symptom:**
```
FAILED tests/integration/observer/test_snapshot_validation.py::TestSnapshotSchemaValidation::test_schema_validation_minimal_snapshot
```

**Root Causes and Solutions:**

1. **JSON Schema Mismatch**
   ```bash
   # Check: Is the JSON valid?
   python -m json.tool $SNAPSHOT_ROOT/obs_*/repo_state_snapshot.json
   
   # Check: Does it have required fields?
   python -c "
   import json
   with open('obs_*/repo_state_snapshot.json') as f:
       s = json.load(f)
   required = ['run_id', 'observed_at', 'observer_version', 'signals']
   for field in required:
       print(f'{field}: {field in s}')
   "
   ```

2. **Type Mismatch in Signals**
   ```python
   # Debug: Print snapshot structure
   from operations_center.observer.models import RepoStateSnapshot
   import json
   
   with open('repo_state_snapshot.json') as f:
       data = json.load(f)
   
   # Attempt to parse
   try:
       snapshot = RepoStateSnapshot.model_validate(data)
   except Exception as e:
       print(f"Validation error: {e}")
       print(f"Data keys: {data.keys()}")
   ```

3. **Collector Error**
   Check `collector_errors` in snapshot:
   ```bash
   python -c "
   import json
   with open('repo_state_snapshot.json') as f:
       s = json.load(f)
   print('Collector errors:')
   for collector, error in s.get('collector_errors', {}).items():
       print(f'  {collector}: {error}')
   "
   ```

#### Issue: Completeness Validation Fails

**Symptom:**
```
FAILED: Completeness validation failed: Minimum 3 non-unavailable signals required, found 2
```

**Solutions:**

1. **Check which signals are missing:**
   ```python
   validator = SnapshotValidator(snapshot)
   result = validator.validate_layer_2_completeness()
   for error in result.errors:
       print(f"Error: {error.message}")
       print(f"Category: {error.category}")
   ```

2. **Ensure signal collectors are running:**
   ```bash
   # Run with verbose output
   python -m operations_center observe-repo \
     --verbose \
     --include-slow-signals
   
   # Check logs for collector status
   grep -E "(test_signal|dependency|lint|type)" logs/observer.log
   ```

3. **Allow additional signals to be unavailable (temporarily):**
   ```python
   # In snapshot model or conftest.py
   snapshot.signals.coverage_signal.status = "unavailable"
   snapshot.signals.architecture_signal.status = "unavailable"
   ```

#### Issue: Consistency Validation Fails

**Symptom:**
```
FAILED: Consistency validation failed: Test signal passing but test_count is 0
```

**Solutions:**

1. **Verify test signal data:**
   ```bash
   python -c "
   import json
   with open('repo_state_snapshot.json') as f:
       s = json.load(f)
   sig = s['signals']['test_signal']
   print(f'Status: {sig[\"status\"]}')
   print(f'Test count: {sig.get(\"test_count\", \"N/A\")}')
   print(f'Passed: {sig.get(\"passed_count\", \"N/A\")}')
   "
   ```

2. **Re-run tests to collect accurate data:**
   ```bash
   pytest --collect-only -q 2>&1 | tail -5
   # Expected output shows actual test count
   
   # Update snapshot with correct value
   python -c "
   from operations_center.observer.service import ObserverService
   observer = ObserverService()
   snapshot = observer.observe_repo()
   # Should now have correct test_count
   "
   ```

#### Issue: Regression Detection False Positive

**Symptom:**
```
FAILED: Coverage regressed by 3.5pp (75.2% → 71.7%)
```

**Solutions:**

1. **Check if regression is real:**
   ```bash
   # Run coverage locally
   pytest --cov=src --cov-report=term-missing
   
   # Compare to baseline
   grep "total_coverage_pct" snapshots/baseline_main.json
   ```

2. **If expected, update baseline:**
   ```bash
   ./scripts/snapshot-promote-baseline.sh main
   git add snapshots/baseline_main.json
   git commit -m "chore: Update coverage baseline after expected regression"
   ```

3. **Adjust tolerance if too strict:**
   ```yaml
   # .github/workflows/ci.yml
   env:
     SNAPSHOT_COVERAGE_TOLERANCE: 0.05  # Increase from 0.02 to 0.05
   ```

#### Issue: Snapshot Storage Inaccessible

**Symptom:**
```
ERROR: Failed to store snapshot: Permission denied: /tmp/snapshots
ERROR: Failed to load snapshot: S3 credentials invalid
```

**Solutions:**

1. **Local Storage (CI):**
   ```bash
   # Verify runner has write access
   ls -ld $RUNNER_TEMP
   touch $RUNNER_TEMP/test.txt && rm $RUNNER_TEMP/test.txt
   
   # Create SNAPSHOT_ROOT directory
   mkdir -p $SNAPSHOT_ROOT
   ```

2. **S3 Storage:**
   ```bash
   # Verify AWS credentials
   aws s3 ls s3://my-bucket/snapshots/
   
   # Check IAM policy includes s3:PutObject, s3:GetObject
   # Set environment variables
   export AWS_ACCESS_KEY_ID=...
   export AWS_SECRET_ACCESS_KEY=...
   export AWS_REGION=us-east-1
   ```

3. **HTTP Storage:**
   ```bash
   # Verify endpoint is reachable
   curl -H "Authorization: Bearer $AUTH_TOKEN" https://snapshot-server.example.com/health
   
   # Test upload
   curl -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
     -d @repo_state_snapshot.json \
     https://snapshot-server.example.com/snapshots/test
   ```

#### Issue: Retry Loop (TRANSIENT Failures)

**Symptom:**
```
Validation failed (transient): Network timeout connecting to lint tool
Retry 1/3...
Retry 2/3...
FAILED after 3 retries
```

**Solutions:**

1. **For timeout failures:**
   ```bash
   # Increase timeout
   export SNAPSHOT_ACCURACY_TIMEOUT=120  # seconds
   
   # Or run accuracy validation locally only
   pytest tests/integration/observer -k "Accuracy" -m "snapshot_slow"
   ```

2. **For network failures:**
   ```bash
   # Check external tool availability
   pytest --version
   coverage --version
   git --version
   ruff check --version
   
   # If missing, install
   pip install pytest coverage ruff
   ```

3. **For flaky tests:**
   ```bash
   # Run just the flaky test multiple times
   for i in {1..5}; do
     pytest tests/integration/observer::TestSnapshotAccuracyValidation::test_snapshot_accuracy_test_count -v
   done
   ```

### 4.4 Maintenance Tasks

#### Weekly: Check Snapshot Size

```bash
# Local storage
du -sh $SNAPSHOT_ROOT
du -sh $SNAPSHOT_ROOT/obs_*/ | sort -rh | head -10

# S3 storage
aws s3 ls s3://my-bucket/snapshots/ --recursive --summarize
```

#### Monthly: Cleanup Old Snapshots

```bash
# Automatic cleanup (runs via retention policy)
python -c "
from operations_center.observer.snapshot_manager import SnapshotManager
manager = SnapshotManager.create_local()
deleted = manager.cleanup_old_snapshots()
print(f'Deleted {len(deleted)} old snapshots')
"

# Manual cleanup (if needed)
find $SNAPSHOT_ROOT -type d -name 'obs_*' -mtime +30 -exec rm -rf {} \;
```

#### Quarterly: Review Baseline Accuracy

```bash
# Compare multiple baselines
for branch in main develop; do
  echo "=== $branch ==="
  head -20 snapshots/baseline_$branch.json | grep -E '(test_count|coverage|dependency)'
done

# Regenerate if outdated
./scripts/snapshot-promote-baseline.sh main develop
```

---

## 5. Snapshot Structure Examples

### 5.1 Minimal Snapshot (All Required Fields)

```python
from datetime import datetime
from operations_center.observer.models import (
    RepoStateSnapshot, RepoContextSnapshot, RepoSignalsSnapshot,
    CheckSignal, DependencyDriftSignal, ParseErrorMetadata
)

snapshot = RepoStateSnapshot(
    run_id="obs_20260607T120000Z_abc123_test",
    observed_at=datetime.fromisoformat("2026-06-07T12:00:00+00:00"),
    observer_version=1,
    source_command="operations-center observe-repo",
    repo=RepoContextSnapshot(
        name="operations-center",
        path="/home/dev/ops",
        current_branch="main",
        base_branch="main",
        is_dirty=False,
    ),
    signals=RepoSignalsSnapshot(
        test_signal=CheckSignal(
            status="passing",
            test_count=7587,
            passed_count=7587,
            failed_count=0,
            skipped_count=0,
            execution_time_seconds=28.5,
            parse_errors=ParseErrorMetadata(status="success"),
        ),
        dependency_drift=DependencyDriftSignal(
            status="healthy",
            total_dependencies=145,
            outdated_count=8,
            critical_advisories=0,
            parse_errors=ParseErrorMetadata(status="success"),
        ),
        # ... other required signals
    ),
    collector_errors={},
)
```

### 5.2 Snapshot with Errors (Collector Failures)

```python
snapshot.collector_errors = {
    "architecture_signal": "Module analysis timed out (exceeded 30s)",
    "security_signal": "Security scanner not available in test environment",
    "benchmark_signal": "Benchmark runner requires hardware setup",
}

# These errors are acceptable as long as we have 3+ non-unavailable signals
```

### 5.3 Snapshot with Inconsistent Signals (For Testing)

```python
# Test case: test signal claims passing but test_count is 0
snapshot.signals.test_signal.status = "passing"
snapshot.signals.test_signal.test_count = 0
snapshot.signals.test_signal.passed_count = 0
# This would fail consistency validation (Layer 3)
```

### 5.4 Large Snapshot (With Full Signal Details)

```json
{
  "run_id": "obs_20260607T120000Z_abc123_production",
  "observed_at": "2026-06-07T12:00:00+00:00",
  "observer_version": 1,
  "source_command": "operations-center observe-repo --include-slow",
  "repo": {
    "name": "operations-center",
    "path": "/home/dev/ops",
    "current_branch": "main",
    "base_branch": "main",
    "is_dirty": false,
    "head_commit": "abc123def456...",
    "head_commit_message": "feat: Add new feature"
  },
  "signals": {
    "test_signal": { "status": "passing", "test_count": 7587, ... },
    "dependency_drift": { "status": "healthy", "total_dependencies": 145, ... },
    "lint_signal": { "status": "clean", "violation_count": 0, ... },
    "type_signal": { "status": "clean", "error_count": 0, ... },
    "todo_signal": { "status": "passing", "todo_count": 12, ... },
    "coverage_signal": { "status": "healthy", "total_coverage_pct": 75.3, ... },
    "ci_history_signal": { "status": "healthy", "recent_failures": 0, ... },
    "execution_health_signal": { "status": "healthy", "worker_utilization": 65, ... },
    "recent_commits": [
      { "hash": "abc123...", "author": "Alice", "message": "...", "timestamp": "2026-06-07T10:30:00+00:00" },
      { "hash": "def456...", "author": "Bob", "message": "...", "timestamp": "2026-06-07T09:15:00+00:00" }
    ],
    "file_hotspots": [
      { "path": "src/observer/snapshot_validator.py", "change_count": 15, "last_modified": "2026-06-07T10:30:00+00:00" },
      { "path": "tests/integration/observer/test_snapshot_validation.py", "change_count": 12, "last_modified": "2026-06-07T10:30:00+00:00" }
    ]
  },
  "collector_errors": {}
}
```

---

## 6. Validation Logic Examples

### 6.1 Layer 1: Schema Validation

```python
from operations_center.observer.models import RepoStateSnapshot
import json

def validate_schema(snapshot_json: str) -> bool:
    """Verify JSON can be parsed as RepoStateSnapshot."""
    try:
        data = json.loads(snapshot_json)
        snapshot = RepoStateSnapshot.model_validate(data)
        return True
    except Exception as e:
        print(f"Schema validation failed: {e}")
        return False

# Usage
with open("repo_state_snapshot.json") as f:
    json_str = f.read()

if validate_schema(json_str):
    print("✓ Schema is valid")
else:
    print("✗ Schema is invalid")
```

### 6.2 Layer 2: Completeness Validation

```python
from operations_center.observer.models import RepoStateSnapshot

def validate_completeness(snapshot: RepoStateSnapshot) -> bool:
    """Verify snapshot has required signals and acceptable errors."""
    # Require at least 3 non-unavailable signals
    signals = [
        snapshot.signals.test_signal,
        snapshot.signals.dependency_drift,
        snapshot.signals.lint_signal,
        snapshot.signals.type_signal,
        snapshot.signals.coverage_signal,
        snapshot.signals.ci_history_signal,
    ]
    non_unavailable = [s for s in signals if s and s.status != "unavailable"]
    
    if len(non_unavailable) < 3:
        print(f"✗ Only {len(non_unavailable)} non-unavailable signals found (minimum 3)")
        return False
    
    # Allow up to 5 collector errors
    if len(snapshot.collector_errors) > 5:
        print(f"✗ Too many collector errors: {len(snapshot.collector_errors)}")
        return False
    
    print(f"✓ Completeness check passed ({len(non_unavailable)} signals, {len(snapshot.collector_errors)} errors)")
    return True

# Usage
snapshot = RepoStateSnapshot.model_validate_json(json_str)
if validate_completeness(snapshot):
    print("✓ Snapshot is complete")
else:
    print("✗ Snapshot is incomplete")
```

### 6.3 Layer 3: Consistency Validation

```python
from operations_center.observer.models import RepoStateSnapshot

def validate_consistency(snapshot: RepoStateSnapshot) -> list[str]:
    """Cross-signal semantic checks. Return list of errors (empty if valid)."""
    errors = []
    
    # Check 1: Test signal status matches test count
    test_sig = snapshot.signals.test_signal
    if test_sig:
        if test_sig.status == "passing" and (test_sig.test_count or 0) == 0:
            errors.append("Test signal claims passing but test_count is 0")
        if test_sig.test_count == 0 and test_sig.status != "failing":
            errors.append(f"Test count is 0 but status is {test_sig.status}")
    
    # Check 2: Dependency signal consistency
    dep_sig = snapshot.signals.dependency_drift
    if dep_sig:
        if dep_sig.status == "healthy" and (dep_sig.critical_advisories or 0) > 0:
            errors.append(f"Dependency signal claims healthy but has {dep_sig.critical_advisories} critical advisories")
    
    # Check 3: Lint signal consistency
    lint_sig = snapshot.signals.lint_signal
    if lint_sig:
        if lint_sig.status == "clean" and (lint_sig.violation_count or 0) > 0:
            errors.append(f"Lint signal claims clean but has {lint_sig.violation_count} violations")
    
    if errors:
        print("✗ Consistency validation failed:")
        for error in errors:
            print(f"  - {error}")
        return errors
    
    print("✓ Consistency validation passed")
    return []

# Usage
snapshot = RepoStateSnapshot.model_validate_json(json_str)
errors = validate_consistency(snapshot)
if not errors:
    print("✓ Snapshot is consistent")
else:
    print(f"✗ Found {len(errors)} consistency issues")
```

### 6.4 Layer 4: Real-World Accuracy Validation

```python
import subprocess
from pathlib import Path
from operations_center.observer.models import RepoStateSnapshot

def validate_accuracy(snapshot: RepoStateSnapshot, repo_path: Path) -> bool:
    """Verify snapshot metrics match actual repository state."""
    errors = []
    
    # Check 1: Test count accuracy
    result = subprocess.run(
        ["pytest", "--collect-only", "-q"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    
    actual_test_count = len([l for l in result.stdout.split("\n") if l.endswith("test")])
    expected_test_count = snapshot.signals.test_signal.test_count or 0
    
    if actual_test_count > 0:
        tolerance = 0.01  # 1%
        relative_error = abs(actual_test_count - expected_test_count) / expected_test_count
        if relative_error > tolerance:
            errors.append(
                f"Test count mismatch: {actual_test_count} vs {expected_test_count} "
                f"(error: {relative_error*100:.1f}%)"
            )
    
    # Check 2: Lint accuracy
    result = subprocess.run(
        ["ruff", "check", "."],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    
    actual_violations = len([l for l in result.stdout.split("\n") if "error" in l])
    expected_violations = snapshot.signals.lint_signal.violation_count or 0
    
    if abs(actual_violations - expected_violations) > 5:
        errors.append(
            f"Lint violations mismatch: {actual_violations} vs {expected_violations}"
        )
    
    if errors:
        print("✗ Accuracy validation failed:")
        for error in errors:
            print(f"  - {error}")
        return False
    
    print("✓ Accuracy validation passed")
    return True

# Usage
snapshot = RepoStateSnapshot.model_validate_json(json_str)
if validate_accuracy(snapshot, Path.cwd()):
    print("✓ Snapshot accuracy verified")
else:
    print("✗ Snapshot has accuracy issues")
```

### 6.5 Layer 5: Regression Detection

```python
from operations_center.observer.models import RepoStateSnapshot

def validate_regression(
    current: RepoStateSnapshot,
    baseline: RepoStateSnapshot,
    tolerance: float = 0.02,  # 2% default
) -> bool:
    """Compare current snapshot vs baseline. Return True if within tolerance."""
    errors = []
    
    # Check 1: Coverage regression
    current_coverage = current.signals.coverage_signal.total_coverage_pct or 0
    baseline_coverage = baseline.signals.coverage_signal.total_coverage_pct or 0
    
    coverage_change = baseline_coverage - current_coverage
    if coverage_change > tolerance * 100:
        errors.append(
            f"Coverage regressed by {coverage_change:.1f}pp "
            f"({baseline_coverage:.1f}% → {current_coverage:.1f}%)"
        )
    
    # Check 2: Test count regression
    current_tests = current.signals.test_signal.test_count or 0
    baseline_tests = baseline.signals.test_signal.test_count or 0
    
    test_loss = baseline_tests - current_tests
    if test_loss > 10:  # Absolute threshold: lose more than 10 tests
        errors.append(
            f"Test count regressed: {current_tests} vs {baseline_tests} baseline "
            f"(lost {test_loss} tests)"
        )
    
    # Check 3: New lint violations
    current_violations = current.signals.lint_signal.violation_count or 0
    baseline_violations = baseline.signals.lint_signal.violation_count or 0
    
    new_violations = current_violations - baseline_violations
    if new_violations > 0:
        errors.append(
            f"Lint violations increased: {current_violations} vs {baseline_violations} "
            f"(+{new_violations} new)"
        )
    
    if errors:
        print("✗ Regression detected:")
        for error in errors:
            print(f"  - {error}")
        return False
    
    print("✓ No regressions detected")
    return True

# Usage
current = RepoStateSnapshot.model_validate_json(current_json)
baseline = RepoStateSnapshot.model_validate_json(baseline_json)

if validate_regression(current, baseline, tolerance=0.05):
    print("✓ Snapshot passes regression check")
else:
    print("✗ Snapshot has regressions")
```

---

## 7. Configuration Guide for New Test Scenarios

### 7.1 Adding a New Signal Validator

**Step 1: Create validator in SnapshotValidator**

```python
# In src/operations_center/observer/snapshot_validator.py

def validate_new_signal_consistency(self) -> ValidationResult:
    """Validate consistency for new custom signal."""
    errors = []
    
    # Your custom validation logic
    if self.snapshot.signals.custom_signal:
        if self.snapshot.signals.custom_signal.status == "passing":
            if self.snapshot.signals.custom_signal.count <= 0:
                errors.append(
                    ValidationError(
                        message="Custom signal claims passing but count is 0",
                        category=ValidationFailureCategory.STRUCTURAL,
                    )
                )
    
    return ValidationResult(
        check_name="custom_signal_consistency",
        passed=len(errors) == 0,
        errors=errors,
    )
```

**Step 2: Add test for new validator**

```python
# In tests/integration/observer/test_snapshot_validation.py

class TestCustomSignalValidation:
    """Validation for new custom signal."""

    def test_custom_signal_consistency(self, minimal_snapshot: RepoStateSnapshot):
        """Verify custom signal consistency check works."""
        validator = SnapshotValidator(minimal_snapshot)
        result = validator.validate_new_signal_consistency()
        
        # Should pass for well-formed snapshot
        assert result.passed
        assert result.check_name == "custom_signal_consistency"
```

### 7.2 Configuring Snapshot for New Repository

**Step 1: Create custom SnapshotBuilder**

```python
# In tests/integration/observer/conftest.py

from operations_center.observer.snapshot_builder import SnapshotBuilder

@pytest.fixture
def my_repo_snapshot(repo_path: Path) -> RepoStateSnapshot:
    """Snapshot for my-repo with custom signals."""
    builder = SnapshotBuilder(repo_path=repo_path)
    
    # Override defaults
    builder.with_test_signal(test_count=5000, passed_count=5000)
    builder.with_dependency_signal(total_dependencies=200, outdated_count=15)
    builder.with_coverage_signal(total_coverage_pct=82.5)
    
    return builder.build()
```

**Step 2: Use in tests**

```python
def test_my_repo_snapshot(my_repo_snapshot: RepoStateSnapshot):
    """Test snapshot for my-repo."""
    validator = SnapshotValidator(my_repo_snapshot)
    result = validator.validate_layer_1_schema()
    assert result.passed
```

### 7.3 Adding Custom Tolerance for Specific Signals

**In CI Workflow:**

```yaml
# .github/workflows/ci.yml
- name: Validate snapshots
  env:
    SNAPSHOT_TOLERANCE: 0.05
    SNAPSHOT_COVERAGE_TOLERANCE: 0.02
    SNAPSHOT_TEST_COUNT_TOLERANCE: 0.01
    CUSTOM_SIGNAL_TOLERANCE: 0.10
  run: pytest tests/integration/observer -m snapshot
```

**In Code:**

```python
def validate_custom_tolerance(snapshot: RepoStateSnapshot, baseline: RepoStateSnapshot):
    """Validate with custom tolerances per signal."""
    tolerances = {
        'coverage': 0.02,      # 2% for coverage
        'test_count': 0.01,    # 1% for tests
        'custom_signal': 0.10, # 10% for custom signal
    }
    
    # Apply tolerances
    for signal_name, tolerance in tolerances.items():
        if signal_name == 'coverage':
            # Coverage validation with 2% tolerance
            pass
        elif signal_name == 'test_count':
            # Test count validation with 1% tolerance
            pass
```

### 7.4 Conditional Snapshot Validation

**Mark specific tests for different scenarios:**

```python
# Skip accuracy validation for certain branches
@pytest.mark.snapshot
@pytest.mark.skipif(
    os.getenv("CI_BRANCH") == "develop",
    reason="Accuracy validation skipped on develop branch"
)
def test_snapshot_accuracy_main_only(snapshot_validator: SnapshotValidator):
    """Accuracy validation only on main branch."""
    result = snapshot_validator.validate_layer_4_accuracy()
    assert result.passed
```

**In workflow:**

```yaml
- name: Run snapshot validation
  run: |
    if [[ "${{ github.ref }}" == "refs/heads/main" ]]; then
      pytest tests/integration/observer -m snapshot
    else
      pytest tests/integration/observer -m "snapshot and not snapshot_slow"
    fi
```

### 7.5 Custom Baseline Management

**Create branch-specific baselines:**

```bash
#!/bin/bash
# scripts/snapshot-manage-baselines.sh

BRANCH=$(git rev-parse --abbrev-ref HEAD)
BASELINE_DIR="snapshots/baselines"
mkdir -p "$BASELINE_DIR"

case "$BRANCH" in
  main)
    # Strict tolerances for main
    export SNAPSHOT_TOLERANCE=0.01
    ;;
  develop)
    # Looser tolerances for develop
    export SNAPSHOT_TOLERANCE=0.05
    ;;
  feature/*)
    # Very loose for feature branches
    export SNAPSHOT_TOLERANCE=0.10
    ;;
esac

# Run validation
pytest tests/integration/observer -m snapshot
```

---

## 8. API Reference

### 8.1 SnapshotManager

```python
from operations_center.observer.snapshot_manager import SnapshotManager

# Create manager with local storage
manager = SnapshotManager.create_local(
    root=Path("./snapshots"),
    retention_days=30,
    retention_count=50,
)

# Save snapshot
metadata = manager.save_snapshot(snapshot, format="json")
# Returns: {"run_id": "...", "observed_at": "...", "format": "json", ...}

# Load snapshot
snapshot = manager.get_snapshot("obs_20260607T120000Z_abc123_x7k9m")

# Get latest snapshot
latest = manager.get_latest_snapshot()

# List all snapshots
all_snapshots = manager.get_snapshots(limit=10)

# Compare two snapshots
diff = manager.compare_snapshots("run_id_1", "run_id_2")

# Delete snapshot
deleted = manager.delete_snapshot("run_id")

# Cleanup old snapshots
deleted_ids = manager.cleanup_old_snapshots()
```

### 8.2 SnapshotValidator

```python
from operations_center.observer.snapshot_validator import SnapshotValidator

validator = SnapshotValidator(
    snapshot=snapshot,
    repo_path=Path.cwd(),
    tolerance=0.05,  # 5% default tolerance
    coverage_tolerance=0.02,  # 2% for coverage
)

# Run individual layers
result1 = validator.validate_layer_1_schema()
result2 = validator.validate_layer_2_completeness()
result3 = validator.validate_layer_3_consistency()
result4 = validator.validate_layer_4_accuracy()  # Slow
result5 = validator.validate_layer_5_regression(baseline_snapshot)  # Slow

# Run all layers
all_results = validator.validate_all_layers(baseline_snapshot=baseline)

# Get detailed report
report = validator.generate_report()
```

### 8.3 SnapshotRepository (Abstract)

```python
from operations_center.observer.snapshot_repository import (
    SnapshotRepository,
    LocalSnapshotRepository,
    S3SnapshotRepository,
    HTTPSnapshotRepository,
    SnapshotFormat,
)

# Local implementation
repo = LocalSnapshotRepository(
    root=Path("./snapshots"),
    retention_days=30,
    retention_count=50,
)

# S3 implementation
repo = S3SnapshotRepository(
    bucket_name="my-org-snapshots",
    prefix="operations-center",
    region_name="us-east-1",
)

# HTTP implementation
repo = HTTPSnapshotRepository(
    base_url="https://snapshot-server.example.com",
    auth_token="bearer_token",
    timeout=30,
)

# Common operations (all backends)
metadata = repo.store(snapshot, format=SnapshotFormat.JSON)
snapshot = repo.load("run_id")
snapshots = repo.list_snapshots(limit=10)
deleted = repo.delete("run_id")
diff = repo.compare("run_id_1", "run_id_2")
deleted_ids = repo.cleanup()  # Uses retention policy
```

### 8.4 ValidationResult

```python
from operations_center.observer.snapshot_validator import ValidationResult, ValidationError

result = ValidationResult(
    check_name="schema_validation",
    passed=True,
    message="Snapshot schema is valid",
    errors=[],  # List[ValidationError]
)

# Check results
if result.passed:
    print("✓ Validation passed")
else:
    print(f"✗ {result.message}")
    for error in result.errors:
        print(f"  - {error.message} ({error.category})")
```

### 8.5 SnapshotComparison

```python
from operations_center.observer.snapshot_manager import SnapshotComparison

comparison = manager.compare_snapshots("run_id_1", "run_id_2")

# Access diffs
for signal_name, diff in comparison.items():
    print(f"{signal_name}:")
    for key, (old, new) in diff.items():
        print(f"  {key}: {old} → {new}")
```

---

## Summary

This guide provides:

1. **Architecture Overview** — System design and component relationships
2. **Format Specification** — JSON, YAML, JSONL structures and storage locations
3. **Versioning Strategy** — How to handle schema changes and baselines
4. **Runbook** — Step-by-step procedures for collection, updates, and troubleshooting
5. **Examples** — Real snapshot structures and validation logic
6. **Configuration Guide** — How to add new signals and customize validation
7. **API Reference** — Complete API for managers, validators, and repositories

For implementation details, refer to:
- `src/operations_center/observer/snapshot_manager.py`
- `src/operations_center/observer/snapshot_validator.py`
- `src/operations_center/observer/snapshot_repository.py`
- `tests/integration/observer/test_snapshot_validation.py`

---

**Last Updated:** 2026-06-07 (Stage 5 Implementation)
