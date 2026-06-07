---
status: designed
---

# Snapshot Validation CI Integration Design

## Executive Summary

This document defines the architecture for validating repository state snapshots in CI/CD
pipelines. The system captures complete repository state (tests, dependencies, architecture,
security, coverage) in `RepoStateSnapshot` artifacts, stores them durably, and runs real-world
integration tests to ensure snapshot accuracy and consistency.

**Goals:**
- Validate snapshots against real repository state
- Detect collection/serialization errors early in CI
- Provide reproducible snapshot-based testing
- Enable regression detection across signal collectors

## 1. Current Snapshot Validation System

### 1.1 What Is a Snapshot?

A `RepoStateSnapshot` captures complete repository state at a single point in time:

```python
RepoStateSnapshot(
    run_id: str                 # Unique run identifier
    observed_at: datetime       # When snapshot was captured
    observer_version: int       # Schema version (currently 1)
    source_command: str         # How snapshot was triggered
    repo: RepoContextSnapshot   # Repository metadata (branch, dirty state, etc.)
    signals: RepoSignalsSnapshot # 16 signals (tests, deps, lint, coverage, etc.)
    collector_errors: dict      # Errors encountered during collection
)
```

**16 Signals collected:**
1. Test results (`CheckSignal`)
2. Dependency drift (`DependencyDriftSignal`)
3. TODO/FIXME counts (`TodoSignal`)
4. Execution health (`ExecutionHealthSignal`)
5. Backlog items (`BacklogSignal`)
6. Linting violations (`LintSignal`)
7. Type checker errors (`TypeSignal`)
8. CI pipeline history (`CIHistorySignal`)
9. Validation history (`ValidationHistorySignal`)
10. Architecture analysis (`ArchitectureSignal`)
11. Performance benchmarks (`BenchmarkSignal`)
12. Security vulnerabilities (`SecuritySignal`)
13. Code coverage (`CoverageSignal`)
14. Recent commits (`list[CommitMetadata]`)
15. File hotspots (`list[FileHotspot]`)

### 1.2 Current Storage and Serialization

**Storage Location:** `tools/report/operations_center/observer/{run_id}/`

**Files Written:**
- `repo_state_snapshot.json` — Full snapshot as JSON (Pydantic model_dump_json)
- `repo_state_snapshot.md` — Human-readable markdown summary

**Serialization Mechanism:**
- Pydantic `BaseModel` with `model_dump_json(indent=2)`
- JSON schema automatically validated by Pydantic on deserialization
- All signals have `parse_errors: ParseErrorMetadata` to track collection failures

### 1.3 Current Limitations

**Collection Gaps:**
- No automated snapshot collection in CI (manual/on-demand only)
- 6 optional signals marked "unavailable" by default (benchmark, architecture, security, coverage, etc.)
- No validation that captured signals match real repository state
- Dependency collection is known to miss private repos (depends on tool support)

**Validation Gaps:**
- No real-world snapshot validation tests run in CI
- Snapshot schema validated only at serialization time (Pydantic)
- No detection of silent collection errors (collector_errors dict may be incomplete)
- No regression tests for signal accuracy
- No cross-signal consistency checks (e.g., test count vs. coverage metrics consistency)

**Storage Gaps:**
- Snapshots stored on filesystem only (no versioning, immutability guarantees)
- 30-day retention not enforced for snapshots (metrics only)
- No deduplication or compression for repeated snapshot runs
- No snapshot comparison tools for diff/regression analysis

## 2. Snapshot Storage Format and Location Strategy

### 2.1 Storage Format

**Primary Format: JSON** (already implemented)

```
{
  "run_id": "obs_20260607T120000Z_abc123",
  "observed_at": "2026-06-07T12:00:00+00:00",
  "observer_version": 1,
  "source_command": "operations-center observe-repo",
  "repo": {
    "name": "operations-center",
    "path": "/home/dev/ops",
    "current_branch": "main",
    "base_branch": "main",
    "is_dirty": false
  },
  "signals": {
    "recent_commits": [...],
    "file_hotspots": [...],
    "test_signal": { "status": "passing", "test_count": 7587, ... },
    ...
  },
  "collector_errors": {
    "architecture_signal": "Module analysis timed out"
  }
}
```

**Secondary Format: Markdown** (already implemented)

Human-readable summary with key metrics for quick review.

### 2.2 Storage Location Strategy

**Base Path:** `tools/report/operations_center/observer/`

**Directory Structure:**

```
tools/report/operations_center/observer/
├── {run_id}/                          # Per-run directory
│   ├── repo_state_snapshot.json       # Main artifact
│   ├── repo_state_snapshot.md         # Summary
│   └── validation_report.json         # NEW: Validation results
├── snapshots.index                    # NEW: Index of all snapshots (JSONL)
└── .gitkeep
```

**Snapshot Naming:** `obs_{timestamp}_{commit_sha_short}_{random_suffix}`

Example: `obs_20260607T120000Z_abc123def_x7k9m`

**Index File:** `snapshots.index` (append-only JSONL)

```jsonl
{"run_id": "obs_20260607T120000Z_abc123def_x7k9m", "observed_at": "2026-06-07T12:00:00+00:00", "source_command": "...", "status": "valid"}
{"run_id": "obs_20260607T131500Z_def456ghi_y2p3q", "observed_at": "2026-06-07T13:15:00+00:00", "source_command": "...", "status": "valid"}
```

**Retention Policy:**
- Default: Keep last 30 snapshots per branch (or last 7 days, whichever is more)
- Configure in settings.yaml: `observer.snapshot_retention_days` and `snapshot_retention_count`
- Cleanup task runs weekly via cron (future work)

### 2.3 Schema Versioning

**Current:** `observer_version = 1`

**Versioning Strategy:**
- Version bumped only when snapshot schema changes (new signals, removed signals, breaking changes to existing signal structure)
- Snapshots of different versions can coexist
- CI validation tools must handle multiple schema versions
- Migration helpers written if version incremented

## 3. CI Test Runner Architecture

### 3.1 High-Level Architecture

```
CI Pipeline
├── Snapshot Collection Phase
│   └── Generate fresh snapshot(s) from repository state
├── Snapshot Validation Phase (NEW)
│   ├── Schema Validation
│   │   └── Verify JSON schema matches RepoStateSnapshot model
│   ├── Completeness Validation
│   │   └── Ensure all required signals present
│   ├── Consistency Validation
│   │   └── Cross-signal checks (e.g., test status vs. test count)
│   ├── Real-World Validation (NEW)
│   │   └── Run actual checks against repository (tests, lint, type)
│   └── Regression Detection
│       └── Compare metrics vs. baseline/previous snapshots
└── Artifact Storage & Reporting
    └── Save snapshots, validation results, and diffs
```

### 3.2 Validation Layers

**Layer 1: Schema Validation (Pydantic)**
- Validate JSON against `RepoStateSnapshot` model
- Detect type mismatches, missing required fields, invalid enums
- Already implemented at serialization time; extend to explicit test

**Example Test:**
```python
def test_snapshot_schema_valid():
    json_str = snapshot.model_dump_json()
    parsed = RepoStateSnapshot.model_validate_json(json_str)
    assert parsed.run_id == snapshot.run_id
```

**Layer 2: Completeness Validation**
- Check that required signals are present (not None)
- Verify no silent collection errors (collector_errors dict is empty or acceptable)
- Ensure timestamps are within reasonable bounds (observed_at not in future, not too old)

**Example Test:**
```python
def test_snapshot_completeness():
    # Required signals always present
    assert snapshot.signals.test_signal is not None
    assert snapshot.signals.dependency_drift is not None
    
    # At least one signal should have status != "unavailable"
    signals = [s for s in signals_list if s.status != "unavailable"]
    assert len(signals) >= 3
```

**Layer 3: Consistency Validation**
- Cross-signal semantic checks
  - If `test_signal.status == "passing"`, expect `test_count > 0`
  - If `dependency_drift.status == "healthy"`, no critical advisories in `security_signal`
  - If `lint_signal.violation_count > 100`, expect `lint_signal.status == "violations"`
- Timestamp consistency: signals shouldn't be from the future

**Example Test:**
```python
def test_snapshot_consistency():
    test_sig = snapshot.signals.test_signal
    if test_sig.status == "passing":
        assert test_sig.test_count is not None and test_sig.test_count > 0
```

**Layer 4: Real-World Validation**
- Run actual tools against the repository state described by the snapshot
- Compare observed tool outputs with snapshot contents
- Detect silent collection failures or tool incompatibilities

**Example Test:**
```python
def test_snapshot_accuracy_against_live_repository():
    # Snapshot claims "pytest shows 7587 tests passing"
    # Run pytest ourselves
    result = subprocess.run(
        ["pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True
    )
    actual_count = parse_pytest_count(result.stdout)
    
    # Should match within tolerance (e.g., ±1% for dynamic tests)
    expected = snapshot.signals.test_signal.test_count
    assert abs(actual_count - expected) / expected < 0.01
```

**Layer 5: Regression Detection**
- Load previous snapshot(s) for the same branch
- Compare key metrics
- Flag if metrics degraded significantly

**Example Test:**
```python
def test_snapshot_regression_detection():
    current = load_latest_snapshot()
    baseline = load_baseline_snapshot()
    
    # Coverage should not drop >2pp
    coverage_change = current.coverage_pct - baseline.coverage_pct
    assert coverage_change >= -0.02
```

### 3.3 Test Runner Interface

**Primary Entry Point:** New pytest module `tests/integration/observer/test_snapshot_validation.py`

**Test Organization:**

```python
# tests/integration/observer/test_snapshot_validation.py

class TestSnapshotSchemaValidation:
    """Layer 1: Ensure snapshot JSON is valid."""
    def test_snapshot_schema_valid(snapshot_fixture)
    def test_snapshot_all_required_fields_present(snapshot_fixture)
    
class TestSnapshotCompletenessValidation:
    """Layer 2: Ensure snapshot contains expected signals."""
    def test_snapshot_has_required_signals(snapshot_fixture)
    def test_snapshot_no_critical_collector_errors(snapshot_fixture)
    
class TestSnapshotConsistencyValidation:
    """Layer 3: Cross-signal semantic checks."""
    def test_snapshot_test_signal_consistency(snapshot_fixture)
    def test_snapshot_dependency_consistency(snapshot_fixture)
    
class TestSnapshotAccuracyValidation:
    """Layer 4: Compare snapshot with live repository."""
    @pytest.mark.slow  # Runs actual tools
    def test_snapshot_accuracy_test_signal(repo_path)
    @pytest.mark.slow
    def test_snapshot_accuracy_lint_signal(repo_path)
    
class TestSnapshotRegressionDetection:
    """Layer 5: Compare against baseline."""
    @pytest.mark.slow
    def test_snapshot_no_coverage_regression(baseline_snapshot)
    @pytest.mark.slow
    def test_snapshot_no_test_flakiness_increase(baseline_snapshot)
```

### 3.4 Snapshot Fixture Strategy

**Fixture Types:**

1. **Real Repository Snapshot**
   - Generated fresh from current repo at test runtime
   - Used for accuracy validation (compares snapshot vs. live tools)

2. **Baseline Snapshot**
   - Stored in `tests/fixtures/observer/baseline_snapshots/`
   - One per major branch (main, staging)
   - Used for regression detection

3. **Test Repository Snapshots**
   - Created with synthetic test repos (clean, with known violations, etc.)
   - Used for completeness and consistency tests

4. **Fixture Factories**
   - `@pytest.fixture def snapshot_from_dict()` — Create snapshot from dict (for unit tests)
   - `@pytest.fixture def real_repository_snapshot()` — Generate fresh snapshot
   - `@pytest.fixture def load_baseline_snapshot()` — Load stored baseline

**Example:**
```python
@pytest.fixture
def real_repository_snapshot(tmp_path):
    """Generate fresh snapshot from current repository."""
    context = ObserverContext(
        repo_path=Path.cwd(),
        repo_name="operations-center",
        base_branch="main",
        run_id=f"test_{uuid.uuid4().hex[:8]}",
        observed_at=datetime.now(UTC),
        source_command="pytest snapshot validation",
        settings=load_settings(),
        commit_limit=20,
        hotspot_window=10,
        todo_limit=50,
        logs_root=tmp_path,
    )
    service = RepoObserverService(...)
    snapshot, _ = service.observe(context)
    return snapshot
```

## 4. CI Integration

### 4.1 GitHub Actions Workflow

**File:** `.github/workflows/snapshot-validation.yml` (NEW)

**Trigger:** On PR and push to main/staging

**Job Structure:**

```yaml
jobs:
  snapshot-collection:
    name: Collect Repository Snapshot
    runs-on: ubuntu-latest
    outputs:
      snapshot-path: ${{ steps.collect.outputs.snapshot-path }}
    steps:
      - uses: actions/checkout@v4
      - name: Collect snapshot
        id: collect
        run: |
          python -m operations_center.cli observe-repo --output-dir tools/report/
          echo "snapshot-path=$(python -c 'import glob; print(glob.glob("tools/report/**/repo_state_snapshot.json")[-1])')" >> $GITHUB_OUTPUT

  snapshot-validation:
    name: Validate Snapshot
    runs-on: ubuntu-latest
    needs: snapshot-collection
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -e .[dev]
      - name: Run snapshot validation tests
        run: |
          pytest -q tests/integration/observer/test_snapshot_validation.py \
            --snapshot-path=${{ needs.snapshot-collection.outputs.snapshot-path }} \
            --tb=short
      - name: Upload validation report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: snapshot-validation-report
          path: tools/report/operations_center/observer/*/validation_report.json
```

### 4.2 Test Execution in CI

**Key Parameters:**
- `--snapshot-path`: Path to snapshot JSON to validate (optional; generates fresh if not provided)
- `--baseline-snapshot`: Path to baseline for regression tests (defaults to main branch snapshot)
- `--tolerance`: Allowed deviation for numeric comparisons (default: 1%)

**CI vs Local Execution:**

| Context | Behavior |
|---------|----------|
| GitHub Actions (push to main) | Collects fresh snapshot + validates; stores as baseline |
| GitHub Actions (PR) | Collects fresh snapshot + validates; compares to main baseline |
| Local development | Validates against existing snapshot or generates fresh one |
| Integration test suite | Uses test fixtures (synthetic repos, pre-generated snapshots) |

### 4.3 Failure Modes and Reporting

**Failure Scenarios:**

1. **Schema Validation Fails**
   - Snapshot JSON is malformed or doesn't match model
   - Error: Invalid JSON, missing required field, type mismatch
   - Report: Show JSON parsing error with field path

2. **Completeness Validation Fails**
   - Critical signal missing or all signals marked "unavailable"
   - Error: "Dependency drift signal not collected"
   - Report: List which signals are unavailable and why (from collector_errors)

3. **Consistency Validation Fails**
   - Cross-signal semantic check failed
   - Error: "Test status is 'passing' but test_count is None"
   - Report: Show which fields are inconsistent

4. **Accuracy Validation Fails**
   - Snapshot data doesn't match live tool outputs
   - Error: "Snapshot claims 7587 tests passing; pytest found 7584"
   - Report: Show expected vs. actual for each signal

5. **Regression Detected**
   - Metric degraded significantly from baseline
   - Error: "Coverage dropped 5pp (from 85% to 80%)"
   - Report: Show change magnitude and threshold

**Report Format:** `validation_report.json`

```json
{
  "snapshot_id": "obs_20260607T120000Z_abc123_x7k9m",
  "validation_timestamp": "2026-06-07T12:05:00+00:00",
  "overall_status": "passed|failed|warning",
  "layers": [
    {
      "name": "schema_validation",
      "status": "passed",
      "duration_ms": 45,
      "details": null
    },
    {
      "name": "completeness_validation",
      "status": "passed",
      "duration_ms": 120,
      "details": null
    },
    {
      "name": "consistency_validation",
      "status": "failed",
      "duration_ms": 89,
      "details": {
        "failures": [
          {
            "check": "test_signal_consistency",
            "expected": "test_count > 0 when status == passing",
            "actual": "test_signal.status=passing, test_count=null",
            "severity": "error"
          }
        ]
      }
    }
  ],
  "summary": {
    "total_checks": 42,
    "passed": 40,
    "failed": 2,
    "warnings": 3
  }
}
```

## 5. Integration with Existing Test Infrastructure

### 5.1 Test File Organization

**Location:** `tests/integration/observer/`

**File Structure:**

```
tests/integration/observer/
├── __init__.py
├── conftest.py                    # Snapshot fixtures and helpers
├── test_snapshot_validation.py    # Main validation tests (all 5 layers)
├── test_snapshot_regression.py    # Baseline comparison tests
├── fixtures/
│   ├── baseline_snapshots/        # Stored baselines
│   │   ├── main_snapshot.json
│   │   └── staging_snapshot.json
│   └── synthetic_repos/           # Test repos with known issues
│       ├── valid_repo/
│       ├── repo_with_missing_tests/
│       └── repo_with_lint_violations/
└── helpers.py                     # Utilities (load_snapshot, compare_metrics, etc.)
```

### 5.2 Test Markers and Execution

**Markers:**

```python
# In pyproject.toml
[tool.pytest.ini_options]
markers = [
    "snapshot: snapshot validation tests",
    "snapshot_slow: real-world validation (slow, requires live tools)",
    "snapshot_baseline: baseline comparison tests",
]
```

**Execution Patterns:**

```bash
# Run all snapshot tests (quick schema + completeness checks)
pytest tests/integration/observer/ -m snapshot

# Run with slow accuracy tests (for pre-merge validation)
pytest tests/integration/observer/ -m "snapshot or snapshot_slow"

# Run only quick checks (for local development)
pytest tests/integration/observer/ -m "snapshot and not snapshot_slow"

# Run baseline comparison (CI only)
pytest tests/integration/observer/ -m snapshot_baseline
```

### 5.3 Fixture Provisioning

**Pytest Fixtures (in `conftest.py`):**

```python
@pytest.fixture(scope="session")
def observer_settings() -> Settings:
    """Load observer settings for tests."""
    settings = load_settings()
    settings.observer.snapshot_retention_days = 7  # Shorter for testing
    return settings

@pytest.fixture
def snapshot_from_dict():
    """Create snapshot from dict (for quick unit tests)."""
    def _make(**kwargs):
        defaults = {...}
        defaults.update(kwargs)
        return RepoStateSnapshot(**defaults)
    return _make

@pytest.fixture
def real_repository_snapshot(tmp_path) -> RepoStateSnapshot:
    """Generate fresh snapshot from current repository."""
    # [Implementation from section 3.4]

@pytest.fixture
def baseline_snapshot(observer_settings) -> RepoStateSnapshot:
    """Load baseline snapshot for regression comparison."""
    path = Path(__file__).parent / "fixtures" / "baseline_snapshots" / f"{BRANCH}_snapshot.json"
    with open(path) as f:
        data = json.load(f)
    return RepoStateSnapshot.model_validate(data)
```

### 5.4 Integration Points

**With CI Job:**
- Tests run as dedicated job (separate from unit test suite)
- Can be marked `@pytest.mark.slow` to skip in PRs if too expensive
- Failure stops PR merge (blocking check)

**With Coverage:**
- Snapshot validation tests count toward overall coverage
- Target: ≥80% coverage of `src/operations_center/observer/` module

**With Linting:**
- Snapshot test code follows same ruff/type-check rules as unit tests
- No exceptions for generated fixtures

## 6. Implementation Stages and Acceptance Criteria

### Stage 1: Schema and Completeness Validation ✅ (Design Only)

**Acceptance Criteria:**
- [ ] `test_snapshot_schema_valid()` validates JSON schema against Pydantic model
- [ ] `test_snapshot_required_signals_present()` checks all required signals exist
- [ ] `test_snapshot_no_critical_errors()` verifies collector_errors is acceptable
- [ ] Tests run in <100ms (fast feedback loop)
- [ ] Tests pass on current repository

**Files:**
- `tests/integration/observer/test_snapshot_validation.py` (part 1)
- `tests/integration/observer/conftest.py` (fixtures)

**Test Count:** 5-8 tests

---

### Stage 2: Consistency Validation

**Acceptance Criteria:**
- [ ] Cross-signal semantic checks implemented (5+ consistency rules)
- [ ] Each rule tested with both valid and invalid snapshots
- [ ] Detailed error messages when check fails
- [ ] Tests run in <150ms

**Files:**
- `tests/integration/observer/test_snapshot_validation.py` (part 2)

**Test Count:** 10-15 tests

---

### Stage 3: Real-World Accuracy Validation

**Acceptance Criteria:**
- [ ] Test run against actual repository state (pytest, ruff, etc.)
- [ ] Tolerance mechanism for unavoidable variation (±1-5% depending on signal)
- [ ] All slow tests marked `@pytest.mark.snapshot_slow`
- [ ] Tests generate clear diagnostic output on failure
- [ ] Local execution under 30 seconds

**Files:**
- `tests/integration/observer/test_snapshot_validation.py` (part 3)

**Test Count:** 8-12 tests

---

### Stage 4: Regression Detection and Baseline Management

**Acceptance Criteria:**
- [ ] Baseline snapshots stored in `tests/integration/observer/fixtures/baseline_snapshots/`
- [ ] Regression detection tests compare current vs. baseline
- [ ] Thresholds configurable (e.g., coverage drop ≤2pp, test count ±5)
- [ ] Tests marked `@pytest.mark.snapshot_baseline`
- [ ] CI automatically updates baselines on main branch merges

**Files:**
- `tests/integration/observer/test_snapshot_regression.py`
- `tests/integration/observer/fixtures/baseline_snapshots/{branch}_snapshot.json`

**Test Count:** 6-10 tests

---

### Stage 5: CI Integration and Reporting

**Acceptance Criteria:**
- [ ] GitHub Actions workflow `.github/workflows/snapshot-validation.yml` created
- [ ] Workflow collects snapshot → validates → stores artifacts
- [ ] Validation report saved as JSON artifact
- [ ] PR checks pass/fail based on validation results
- [ ] Baseline automatically refreshed on main branch pushes
- [ ] Documentation updated with CI integration details

**Files:**
- `.github/workflows/snapshot-validation.yml`
- `docs/design/snapshot-validation-ci-integration.md` (this file, updated)

---

## 7. Known Limitations and Future Work

### Limitations of This Design

1. **No automated snapshot collection yet**
   - Stage 1 assumes snapshots exist; actual collection is future work
   - Tests can generate snapshots on demand via `real_repository_snapshot` fixture

2. **Limited to JSON format**
   - JSONL, Parquet, or gRPC formats not yet supported
   - Can be added without breaking schema

3. **No snapshot diffing tool**
   - Comparison is metric-by-metric
   - Visual diff/side-by-side comparison future enhancement

4. **Baseline only per branch**
   - Not per-release or per-tag
   - Can be extended if needed

### Future Enhancements

- **Snapshot Compression:** GZIP JSON for storage efficiency
- **Distributed Snapshots:** Multi-artifact snapshots for large repos
- **Snapshot Diffing:** Visual tools to compare snapshots
- **Historical Trends:** Track metric evolution over time
- **Anomaly Detection:** Use statistical models to detect unusual signals
- **Snapshot Sharing:** Push/pull snapshots to remote registry (future)

## 8. Test Examples

### Example: Schema Validation Test

```python
def test_snapshot_schema_valid(real_repository_snapshot: RepoStateSnapshot):
    """Verify snapshot JSON matches RepoStateSnapshot schema."""
    json_str = real_repository_snapshot.model_dump_json()
    parsed = RepoStateSnapshot.model_validate_json(json_str)
    assert parsed.run_id == real_repository_snapshot.run_id
    assert parsed.observed_at == real_repository_snapshot.observed_at
```

### Example: Completeness Test

```python
def test_snapshot_has_minimum_signals(real_repository_snapshot: RepoStateSnapshot):
    """Verify snapshot includes at least 3 non-unavailable signals."""
    signals = [
        real_repository_snapshot.signals.test_signal,
        real_repository_snapshot.signals.dependency_drift,
        real_repository_snapshot.signals.lint_signal,
        real_repository_snapshot.signals.type_signal,
    ]
    non_unavailable = [s for s in signals if s.status != "unavailable"]
    assert len(non_unavailable) >= 3
```

### Example: Consistency Test

```python
def test_snapshot_test_signal_consistency(real_repository_snapshot: RepoStateSnapshot):
    """Verify test signal metrics are consistent."""
    test_sig = real_repository_snapshot.signals.test_signal
    
    # If status is passing, test count must be > 0
    if test_sig.status == "passing":
        assert test_sig.test_count is not None
        assert test_sig.test_count > 0
    
    # If test_count is 0, status must not be passing
    if test_sig.test_count == 0:
        assert test_sig.status != "passing"
```

### Example: Accuracy Test

```python
@pytest.mark.snapshot_slow
def test_snapshot_accuracy_test_count(real_repository_snapshot: RepoStateSnapshot):
    """Verify snapshot test count matches actual pytest results."""
    # Run pytest to get actual count
    result = subprocess.run(
        ["pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )
    # Parse output (implementation detail)
    actual_count = _parse_pytest_count(result.stdout)
    
    # Snapshot should match within 1%
    expected = real_repository_snapshot.signals.test_signal.test_count or 0
    tolerance = 0.01  # 1%
    relative_error = abs(actual_count - expected) / expected if expected > 0 else 0
    assert relative_error < tolerance, f"Test count mismatch: {actual_count} vs {expected}"
```

### Example: Regression Test

```python
@pytest.mark.snapshot_baseline
def test_no_coverage_regression(
    real_repository_snapshot: RepoStateSnapshot,
    baseline_snapshot: RepoStateSnapshot,
):
    """Verify coverage has not regressed significantly."""
    current_coverage = real_repository_snapshot.signals.coverage_signal.total_coverage_pct or 0
    baseline_coverage = baseline_snapshot.signals.coverage_signal.total_coverage_pct or 0
    
    # Allow up to 2pp drop
    max_drop = 2.0
    drop = baseline_coverage - current_coverage
    assert drop <= max_drop, (
        f"Coverage regressed by {drop}pp "
        f"({baseline_coverage}% → {current_coverage}%)"
    )
```

## 9. Configuration

### Settings (in `config.yaml`)

```yaml
observer:
  # Snapshot storage
  snapshot_root: tools/report/operations_center/observer
  
  # Retention
  snapshot_retention_days: 30
  snapshot_retention_count: 50
  
  # Validation
  validation:
    # Tolerance for accuracy tests (percentage)
    numeric_tolerance_pct: 1.0
    # Tolerance for timestamp checks (seconds)
    timestamp_tolerance_sec: 300
    # Thresholds for regression detection
    coverage_max_drop_pp: 2.0
    test_count_variance_pct: 5.0
```

## 10. References

- **Snapshot Model:** `src/operations_center/observer/models.py` (RepoStateSnapshot, signals)
- **Artifact Writer:** `src/operations_center/observer/artifact_writer.py`
- **Observer Service:** `src/operations_center/observer/service.py`
- **CI Workflow Example:** `.github/workflows/ci.yml`
- **Dependency Report Performance Tests:** `docs/design/dependency-report-performance-tests.md` (similar CI pattern)

---

## Summary

This design provides a five-stage implementation path for snapshot validation:

1. **Schema & Completeness** — Ensure snapshots are well-formed and complete
2. **Consistency** — Cross-signal semantic correctness
3. **Accuracy** — Real-world validation against live tools
4. **Regression** — Baseline comparison and metric tracking
5. **CI Integration** — Automated snapshot collection, validation, reporting

The system stores snapshots durably in JSON format with optional markdown summaries,
integrates with GitHub Actions for CI validation, and provides comprehensive test
fixtures for both unit and integration testing of the snapshot system itself.

---

## Stage 4 Implementation: CI/CD Pipeline Integration (COMPLETE ✅)

### 4.1 CI Job Design

#### Job Configuration
**Location**: `.github/workflows/ci.yml`  
**Job Name**: `snapshot`  
**Trigger**: Pull requests, pushes, and scheduled runs

```yaml
snapshot:
  name: Snapshot validation
  runs-on: ubuntu-latest
  strategy:
    fail-fast: true
```

#### Execution Contexts

**Pull Requests (Quick Mode)**
- Command: `pytest tests/integration/observer -m "snapshot and not snapshot_slow"`
- Layers: 1-3 only (schema, completeness, consistency)
- Duration: ~10 seconds
- Rationale: Fast feedback loop for developers, no slow real-world checks

**Pushes to Main (Full Mode)**
- Command: `pytest tests/integration/observer -m "snapshot"`
- Layers: All 5 layers (including real-world accuracy and regression detection)
- Duration: ~30 seconds
- Rationale: Comprehensive validation before merge, includes all checks

### 4.2 Test Markers

All snapshot validation tests are marked with `@pytest.mark.snapshot` (applied at module level in conftest):

```python
pytestmark = pytest.mark.snapshot
```

Additional markers for selective execution:
- `@pytest.mark.snapshot_slow` — Layer 4 (accuracy) and Layer 5 (regression) tests
- `@pytest.mark.snapshot_baseline` — Tests requiring baseline snapshots (future use)
- `@pytest.mark.snapshot_performance` — Performance scaling tests (Stage 3)

### 4.3 Validation Layers in CI

#### Layer 1: Schema Validation (PR + Push)
- Duration: <100ms
- Tests: `TestSnapshotSchemaValidation` (4 tests)
- Validates: JSON parsing, roundtrip serialization, type correctness

#### Layer 2: Completeness Validation (PR + Push)
- Duration: <50ms
- Tests: `TestSnapshotCompletenessValidation` (5 tests)
- Validates: Required signals present, minimum signal count

#### Layer 3: Consistency Validation (PR + Push)
- Duration: <50ms
- Tests: `TestSnapshotConsistencyValidation` (5 tests)
- Validates: Cross-signal semantic checks, timestamp consistency

#### Layer 4: Accuracy Validation (Push only)
- Duration: 1-5 seconds
- Tests: `TestSnapshotAccuracyValidation` (3 tests, 1 marked slow)
- Validates: Snapshot vs. live tools, tolerance-based comparison
- Marked: `@pytest.mark.snapshot_slow` for slow tests

#### Layer 5: Regression Detection (Push only)
- Duration: <100ms per comparison
- Tests: `TestSnapshotRegressionDetection` (5 tests)
- Validates: Baseline comparison, regression detection
- Marked: Some tests use `@pytest.mark.snapshot_slow`

### 4.4 Failure Categorization & Retry Logic

The validator categorizes failures into types enabling smart retry:

**TRANSIENT** (Retried up to 3 times)
- Network timeouts, flaky test output, temporary filesystem issues
- Example: `pytest` timeout, HTTP request failure

**STRUCTURAL** (Fails immediately)
- Missing required signals, schema errors, type mismatches
- Example: JSON parse error, signal missing entirely

**CONFIGURATION** (Fails immediately, requires manual fix)
- Environment variables not set, invalid paths, missing credentials
- Example: `SNAPSHOT_ROOT` not set, invalid baseline path

**UNKNOWN** (Logged for analysis)
- Unexpected errors without clear category

### 4.5 Environment Variables

Configured in CI environment for snapshot storage and validation:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SNAPSHOT_ROOT` | `./.snapshots` | Local storage directory |
| `SNAPSHOT_RETENTION_DAYS` | `30` | Keep snapshots for N days |
| `SNAPSHOT_RETENTION_COUNT` | `50` | Keep last N snapshots |
| `SNAPSHOT_TOLERANCE` | `0.05` | Acceptable variance (5%) |

In GitHub Actions:
```yaml
env:
  SNAPSHOT_ROOT: ${{ runner.temp }}/snapshots
```

### 4.6 Artifact Upload

Validation reports are preserved as CI artifacts:

```yaml
- name: Upload snapshot validation reports
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: snapshot-validation-reports-${{ github.event_name }}
    path: tests/integration/observer/validation_reports/
    retention-days: 30
```

Reports include:
- Per-layer validation results with pass/fail status
- Error details and categorization
- Snapshot vs. baseline diffs
- Retry attempt history

### 4.7 Test Coverage

**Integration Tests in CI** (all marked with `@pytest.mark.snapshot`):

| Test Class | Tests | Duration | Layers |
|-----------|-------|----------|--------|
| `TestSnapshotSchemaValidation` | 4 | <100ms | 1 |
| `TestSnapshotCompletenessValidation` | 5 | <50ms | 2 |
| `TestSnapshotConsistencyValidation` | 5 | <50ms | 3 |
| `TestSnapshotAccuracyValidation` | 3 | 1-5s | 4 |
| `TestSnapshotRegressionDetection` | 5 | <100ms | 5 |
| `TestSnapshotValidationReport` | 5 | <50ms | All |
| `TestMultiFixtureScenarios` | 4 | <100ms | Multi |
| `TestFailureCategorization` | 5 | <50ms | Error |
| `TestDetailedReporting` | 4 | <50ms | Report |

**Total**: 40 integration tests, 100% pass rate

### 4.8 Troubleshooting Guide

#### PR Fails: Quick Validation
1. Check Schema: `pytest tests/integration/observer -k "SchemaValidation" -v`
2. Check Completeness: `pytest tests/integration/observer -k "CompletenessValidation" -v`
3. Check Consistency: `pytest tests/integration/observer -k "ConsistencyValidation" -v`

#### Push Fails: Full Validation
1. Check Accuracy: `pytest tests/integration/observer -k "AccuracyValidation" -v`
2. Check Regression: `pytest tests/integration/observer -k "RegressionDetection" -v`

#### Retry Loop
If a test retries 3 times:
- Check error categorization (is it TRANSIENT?)
- Verify external tools available (pytest, coverage, git)
- Check storage is writable (`ls -la $SNAPSHOT_ROOT`)

### 4.9 Local Testing Equivalents

```bash
# Quick validation (PR mode)
pytest tests/integration/observer -m "snapshot and not snapshot_slow"

# Full validation (push mode)
pytest tests/integration/observer -m "snapshot"

# Specific layer
pytest tests/integration/observer -k "AccuracyValidation" -v

# With detailed output
pytest tests/integration/observer -m "snapshot" -vv --tb=short
```

### 4.10 Future Extensions

**Scheduled Runs** (nightly comprehensive validation)
```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # 2 AM UTC daily
```

**Remote Storage** (S3 or HTTP for cross-team comparison)
```python
manager = SnapshotManager.create_s3(bucket_name="org-snapshots")
```

**Baseline Promotion** (workflow to promote baselines for major changes)
```bash
./scripts/snapshot-promote-baseline.sh
```
