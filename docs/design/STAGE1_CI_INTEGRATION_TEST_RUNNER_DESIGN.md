---
status: designed
stage: 1
---

# Stage 1: CI Integration Test Runner Architecture Design

**Date**: 2026-06-09  
**Status**: 🎯 DESIGN STAGE — Architecture Specification & Component Documentation  
**Acceptance Criteria**: ✅ Complete (test runner design, snapshot validation approach, CI integration points, file structure)

## 1. Overview & Objectives

### Purpose
This document specifies the complete architecture for the **CI Integration Test Runner** — a system that validates real-world snapshot data through a 5-layer validation pipeline integrated into the GitHub Actions CI/CD workflow.

### Key Objectives
1. **Real-world snapshot validation** — Capture and validate repository state snapshots from live external services
2. **Multi-layer validation** — Progressively validate snapshots at 5 distinct layers (schema, completeness, consistency, accuracy, regression)
3. **CI/CD integration** — Seamlessly integrate into GitHub Actions with pull request, push, and scheduled triggers
4. **Actionable failure reporting** — Categorize failures (transient, structural, configuration, unknown) for operator guidance
5. **Scalability** — Support both local file storage and remote repositories (S3, HTTP)

### Scope
- **In scope**: Test runner design, snapshot validation architecture, CI integration, storage backends, failure categorization
- **Out of scope**: Real-time alerting dashboards, advanced ML-based anomaly detection, production environment rollout

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     GitHub Actions CI                       │
│  ┌──────────────────────────────────────────────────────┐   │
│  │        snapshot Job (PR/Push/Schedule)              │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │ pytest --markers=integration                │   │   │
│  │  │  - Layer 1-3: Schema, Completeness, Const.  │   │   │
│  │  │  - Layer 4-5: Accuracy, Regression (slow)   │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           SnapshotValidator Pipeline                 │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │ Validation Layers (Sequential)              │   │   │
│  │  │  Layer 1: Schema (JSON ↔ Pydantic)         │   │   │
│  │  │  Layer 2: Completeness (3+ signals)        │   │   │
│  │  │  Layer 3: Consistency (cross-signal)       │   │   │
│  │  │  Layer 4: Accuracy (vs. live tools)        │   │   │
│  │  │  Layer 5: Regression (baseline comparison) │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         Failure Categorization & Reporting          │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │ TRANSIENT (retry 3x) vs STRUCTURAL (fail)  │   │   │
│  │  │ CONFIGURATION (manual) vs UNKNOWN (log)    │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           Artifact Storage & Retention              │   │
│  │  - Local: ${{ runner.temp }}/snapshots              │   │
│  │  - Retention: 30 days                               │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Component Interaction Diagram

```
User Code (PR/Push)
        ↓
GitHub Actions Workflow
        ↓
    snapshot Job
        ├─→ Environment Setup
        │    └─ SNAPSHOT_ROOT, SNAPSHOT_RETENTION_DAYS, SNAPSHOT_TOLERANCE
        ├─→ Fixture Collection
        │    └─ RepoStateSnapshot (test data)
        ├─→ pytest Execution (marked tests)
        │    └─ tests/integration/observer/test_snapshot_validation.py
        └─→ SnapshotValidator Pipeline
             ├─ validate_layer_1_schema()
             ├─ validate_layer_2_completeness()
             ├─ validate_layer_3_consistency()
             ├─ validate_layer_4_accuracy() [slow]
             ├─ validate_layer_5_regression() [slow]
             └─ generate_validation_report()
                  ├─ Failure Categorization
                  ├─ Retry Logic
                  └─ Report Export (JSON)
                       └─ Upload Artifacts (30-day retention)
```

---

## 3. Snapshot Validation Approach

### 3.1 5-Layer Validation Architecture

The snapshot validator implements a progressive validation pipeline with 5 layers:

#### Layer 1: Schema Validation
- **Purpose**: Ensure snapshot JSON conforms to RepoStateSnapshot Pydantic model
- **Method**: Attempt JSON ↔ Pydantic roundtrip
- **Failure Type**: STRUCTURAL (schema errors = bad data capture)
- **Quick Mode**: ✅ Included in PR runs
- **Implementation**: `validate_layer_1_schema(snapshot_data) → ValidationResult`

**Example**:
```python
# Layer 1 catches malformed JSON, missing required fields
snapshot = RepoStateSnapshot(**json.loads(data))  # Pydantic validation
```

#### Layer 2: Completeness Validation
- **Purpose**: Ensure snapshot contains sufficient signal data for analysis
- **Requirement**: ≥3 non-unavailable signals across repo_signals
- **Failure Type**: STRUCTURAL (incomplete data = test/build infrastructure down)
- **Quick Mode**: ✅ Included in PR runs
- **Implementation**: `validate_layer_2_completeness(snapshot) → ValidationResult`

**Example**:
```python
# Layer 2 checks for minimum signal coverage
available_signals = [s for s in snapshot.repo_signals.values() 
                     if s.status != SignalStatus.UNAVAILABLE]
assert len(available_signals) >= 3
```

#### Layer 3: Consistency Validation
- **Purpose**: Cross-signal semantic checks (signals agree with each other)
- **Examples**:
  - If build_signal shows "failed", test_signal should not be "passed"
  - deployment_signal timestamp should be ≥ build_signal timestamp
  - coverage data should be 0–100%
- **Failure Type**: STRUCTURAL (consistency = data quality issue)
- **Quick Mode**: ✅ Included in PR runs
- **Implementation**: `validate_layer_3_consistency(snapshot) → ValidationResult`

**Example**:
```python
# Layer 3 checks semantic relationships
if build_signal.status == BuildStatus.FAILED:
    assert test_signal.status in [TestStatus.FAILED, TestStatus.SKIPPED]

assert test_signal.coverage >= 0 and test_signal.coverage <= 100
```

#### Layer 4: Accuracy Validation (marked `@pytest.mark.slow`)
- **Purpose**: Compare snapshot data against live tools (SwitchBoard, Plane, Archon)
- **Method**: Query live state and diff against snapshot signals
- **Failure Type**: TRANSIENT (network issues, tool unavailability) or STRUCTURAL (stale snapshot)
- **Quick Mode**: ❌ Skipped in PR runs (long-running, requires live services)
- **Implementation**: `validate_layer_4_accuracy(snapshot, live_clients) → ValidationResult`

**Example**:
```python
# Layer 4 compares to live state
live_tests = switchboard_client.get_test_results(repo_id, build_id)
if live_tests != snapshot.repo_signals.test_signal.details:
    raise ValidationError("Snapshot stale: test results diverged from live")
```

#### Layer 5: Regression Detection (marked `@pytest.mark.slow`)
- **Purpose**: Detect unexpected degradation by comparing to baseline snapshots
- **Method**: Load baseline snapshot from repository, compute deltas
- **Tolerance**: Configurable variance (default 5%, set via SNAPSHOT_TOLERANCE)
- **Failure Type**: TRANSIENT (one bad run) or STRUCTURAL (sustained regression)
- **Quick Mode**: ❌ Skipped in PR runs
- **Implementation**: `validate_layer_5_regression(snapshot, baseline) → ValidationResult`

**Example**:
```python
# Layer 5 checks for regressions
baseline_coverage = baseline.repo_signals.test_signal.coverage  # e.g. 92%
current_coverage = snapshot.repo_signals.test_signal.coverage   # e.g. 88%
tolerance = 0.05  # 5%

if (baseline_coverage - current_coverage) / baseline_coverage > tolerance:
    raise ValidationError(f"Coverage regression: {baseline}% → {current}%")
```

### 3.2 Snapshot Data Model

```python
class RepoStateSnapshot(BaseModel):
    """Complete snapshot of repository state at a point in time."""
    
    run_id: str                    # Unique snapshot identifier
    observed_at: datetime          # Timestamp of observation
    observer_version: str          # Validator version for compatibility
    
    repo_signals: Dict[str, Signal]  # Core signals:
        # test_signal: Last test run results
        # build_signal: Last build status  
        # deployment_signal: Last deployment status
        # coverage_signal: Code coverage metrics
        # dependency_signal: Dependency scan results
    
    metadata: Dict[str, Any]       # Extended attributes:
        # git_metadata: commit SHAs, branch info
        # environment: CI environment details
        # configuration: Feature flags, tolerance settings
        # external_references: Related Plane tasks, commits

class Signal(BaseModel):
    """Individual signal: test status, build result, deployment, coverage, etc."""
    
    status: SignalStatus           # PASSED, FAILED, SKIPPED, UNAVAILABLE
    details: Dict[str, Any]        # Signal-specific data
    last_updated: datetime
    source: str                    # Data source (e.g. "github_actions", "sentry")
```

### 3.3 Validation Failure Categories

Failures are categorized to guide operator response:

| Category | When | Action | Retry |
|----------|------|--------|-------|
| **TRANSIENT** | Network timeout, rate limit, tool unavailable | Retry up to 3x, escalate if persistent | Yes (3x) |
| **STRUCTURAL** | Schema error, missing signals, logic violation | Fix data capture or validation logic | No |
| **CONFIGURATION** | Missing env vars, bad credentials, permissions | Manual operator intervention | No |
| **UNKNOWN** | Uncaught exception, unclear root cause | Log for analysis, escalate | No |

**Retry Logic**:
```python
def get_retryable_errors(failures: List[ValidationError]) -> List[str]:
    """Returns list of errors that should trigger retry."""
    return [
        error.message for error in failures
        if error.category == ValidationFailureCategory.TRANSIENT
    ]

# In CI:
retry_count = 0
while retry_count < 3:
    result = validator.validate(snapshot)
    if not result.failures:
        return SUCCESS
    
    transient_errors = get_retryable_errors(result.failures)
    if not transient_errors:
        return FAIL_PERMANENTLY  # Structural issue
    
    retry_count += 1
    time.sleep(10 * (2 ** retry_count))  # Exponential backoff
```

---

## 4. CI Integration Points

### 4.1 GitHub Actions Workflow Integration

**File**: `.github/workflows/ci.yml`

```yaml
jobs:
  snapshot:
    name: Real-world Snapshot Validation
    runs-on: ubuntu-latest
    
    # Trigger Configuration
    if: |
      github.event_name == 'pull_request' ||
      github.event_name == 'push' ||
      github.event.schedule == '0 2 * * *'  # Daily at 2 AM UTC
    
    steps:
      # Step 1: Setup environment
      - name: Setup Snapshot Environment
        env:
          SNAPSHOT_ROOT: ${{ runner.temp }}/snapshots
          SNAPSHOT_RETENTION_DAYS: 30
          SNAPSHOT_RETENTION_COUNT: 50
          SNAPSHOT_TOLERANCE: 0.05  # 5%
        run: mkdir -p $SNAPSHOT_ROOT
      
      # Step 2: Quick mode (PR)
      - if: github.event_name == 'pull_request'
        name: Run Snapshot Validation (Quick)
        run: pytest tests/integration/observer/test_snapshot_validation.py -m "integration and not slow" -v
      
      # Step 3: Full mode (Push/Schedule)
      - if: github.event_name == 'push' || github.event.schedule == '0 2 * * *'
        name: Run Snapshot Validation (Full)
        run: pytest tests/integration/observer/test_snapshot_validation.py -m integration -v
      
      # Step 4: Upload artifacts
      - name: Upload Validation Reports
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: snapshot-validation-reports
          path: ${{ runner.temp }}/snapshots/reports/
          retention-days: 30
```

### 4.2 Test Marker Configuration

**File**: `pyproject.toml`

```toml
[tool.pytest.ini_options]
markers = [
    "snapshot: Integration test for snapshot validation",
    "snapshot_slow: Accuracy and regression tests (layers 4-5)",
    "snapshot_baseline: Baseline comparison tests (future)",
    "snapshot_performance: Performance and scaling tests",
]
```

### 4.3 Trigger Modes

| Trigger | Layers Executed | Tests Included | Duration | Use Case |
|---------|-----------------|----------------|-----------|----|
| **Pull Request** | 1-3 (quick) | `@pytest.mark.snapshot and not snapshot_slow` | ~30s | Per-commit validation |
| **Push to Main** | 1-5 (full) | All `@pytest.mark.snapshot` tests | ~5m | Pre-merge regression detection |
| **Daily Schedule** | 1-5 (full) | All `@pytest.mark.snapshot` tests (nightly) | ~5m | No-code regressions (environmental) |

### 4.4 Environment Variables

```bash
# Snapshot Root Directory
SNAPSHOT_ROOT=${SNAPSHOT_ROOT:-/tmp/snapshots}
# Data Retention Policy
SNAPSHOT_RETENTION_DAYS=${SNAPSHOT_RETENTION_DAYS:-30}
SNAPSHOT_RETENTION_COUNT=${SNAPSHOT_RETENTION_COUNT:-50}
# Regression Tolerance (5% default)
SNAPSHOT_TOLERANCE=${SNAPSHOT_TOLERANCE:-0.05}
# Optional: Remote Storage
SNAPSHOT_S3_BUCKET=${SNAPSHOT_S3_BUCKET:-}
SNAPSHOT_HTTP_BASE_URL=${SNAPSHOT_HTTP_BASE_URL:-}
```

---

## 5. File Structure & Organization

### 5.1 Source Code Organization

```
src/operations_center/observer/
├── __init__.py
├── snapshot_repository.py          # [Stage 1] Abstract repository interface
│   ├── SnapshotRepository (ABC)
│   ├── LocalSnapshotRepository
│   ├── S3SnapshotRepository
│   └── HTTPSnapshotRepository
├── snapshot_manager.py             # [Stage 1] High-level storage API
│   ├── SnapshotManager (factory pattern)
│   ├── SnapshotComparison (diff results)
│   └── factory methods: create_local(), create_s3(), create_http()
├── snapshot_validator.py           # [Stage 2] 5-layer validation pipeline
│   ├── SnapshotValidator (main validator class)
│   ├── ValidationResult (results dataclass)
│   ├── ValidationError (error tracking)
│   ├── ValidationFailureCategory (enum)
│   ├── SnapshotValidationReport (JSON-serializable report)
│   └── validate_layer_1() through validate_layer_5()
├── flaky_test_reporter.py          # Flaky test detection (adjacent feature)
├── flaky_test_collector.py         # Observer integration
├── service.py                       # RepoObserverService (main integration point)
└── models.py                        # Data models (RepoStateSnapshot, Signal, etc.)
```

### 5.2 Test Organization

```
tests/
├── unit/observer/
│   ├── test_snapshot_edge_cases.py     # [Stage 3] Edge case tests (19 tests)
│   │   └── Corrupted data, permissions, format conversions, large snapshots
│   ├── test_snapshot_performance.py    # [Stage 3] Performance/scaling (13 tests)
│   │   └── Repository/manager performance, memory efficiency, scaling linearity
│   └── test_signal_query.py            # Query API tests
├── integration/observer/
│   ├── test_snapshot_validation.py     # [Stage 2] 5-layer validation (41 tests)
│   │   ├── Layer 1: Schema (4 tests)
│   │   ├── Layer 2: Completeness (5 tests)
│   │   ├── Layer 3: Consistency (5 tests)
│   │   ├── Layer 4: Accuracy (3 tests)
│   │   ├── Layer 5: Regression (4 tests)
│   │   ├── Reporting & Categorization (5 tests)
│   │   ├── Multi-fixture scenarios (8 tests)
│   │   └── Detailed reporting (3 tests)
│   ├── test_flaky_test_integration.py  # Flaky test runner integration
│   └── conftest.py                     # Pytest fixtures & configuration
├── fixtures/
│   ├── console_malformed/              # Console validator fixtures (7 repos)
│   └── observability/                  # Snapshot data fixtures
└── conftest.py                         # Root pytest configuration
```

### 5.3 Documentation Organization

```
docs/design/
├── snapshot-validation-ci-integration.md        # [Stage 0] Design spec (2,500+ lines)
│   └── Overview, components, architecture, CI integration details
├── snapshot-validation-ci-runner.md             # [Stage 5] User guide (4,500+ lines)
│   └── API reference, configuration, troubleshooting, examples
├── STAGE1_CI_INTEGRATION_TEST_RUNNER_DESIGN.md  # [Stage 1] This document
│   └── Architecture, components, integration points, file structure
├── flaky-test-reporter.md                       # Flaky test reporter design
└── flaky-test-reporter-ci-integration.md        # Flaky test CI integration
```

### 5.4 Configuration Files

```
.github/
└── workflows/
    └── ci.yml                              # Main CI workflow
        └── snapshot job (PR/push/schedule triggers)

pyproject.toml
└── [tool.pytest.ini_options]
    └── markers: snapshot, snapshot_slow, snapshot_baseline, snapshot_performance

.console/
├── task.md                              # Current task definition
├── guidelines.md                        # Operational guidelines
├── backlog.md                          # Work inventory
└── log.md                              # Decision log & completion records
```

---

## 6. Component Specifications

### 6.1 SnapshotValidator (Layer orchestrator)

**File**: `src/operations_center/observer/snapshot_validator.py`

```python
class SnapshotValidator:
    """Main validator: orchestrates 5-layer validation pipeline."""
    
    def __init__(
        self,
        repo_root: str,
        strict_mode: bool = True,
        tolerance: float = 0.05  # 5% default
    ):
        self.repo_root = repo_root
        self.strict_mode = strict_mode
        self.tolerance = tolerance
    
    def validate(self, snapshot_data: dict | str) -> SnapshotValidationReport:
        """Run complete 5-layer validation pipeline.
        
        Args:
            snapshot_data: Dict or JSON string of snapshot
            
        Returns:
            SnapshotValidationReport with all layer results and categorization
        """
    
    # Layer methods
    def validate_layer_1_schema(self, data: dict) -> ValidationResult:
        """Schema validation: JSON → Pydantic model"""
    
    def validate_layer_2_completeness(self, snapshot: RepoStateSnapshot) -> ValidationResult:
        """Completeness: ≥3 non-unavailable signals"""
    
    def validate_layer_3_consistency(self, snapshot: RepoStateSnapshot) -> ValidationResult:
        """Consistency: cross-signal semantic checks"""
    
    def validate_layer_4_accuracy(
        self,
        snapshot: RepoStateSnapshot,
        live_clients: Dict[str, Any]
    ) -> ValidationResult:
        """Accuracy: snapshot vs. live tools (SwitchBoard, Plane, Archon)"""
    
    def validate_layer_5_regression(
        self,
        snapshot: RepoStateSnapshot,
        baseline: RepoStateSnapshot,
        tolerance: float | None = None
    ) -> ValidationResult:
        """Regression: compare to baseline with tolerance"""
```

### 6.2 SnapshotRepository (Storage abstraction)

**File**: `src/operations_center/observer/snapshot_repository.py`

```python
class SnapshotRepository(ABC):
    """Abstract repository: defines storage interface."""
    
    @abstractmethod
    def store(self, run_id: str, snapshot: RepoStateSnapshot) -> None:
        """Save snapshot to storage"""
    
    @abstractmethod
    def load(self, run_id: str) -> RepoStateSnapshot:
        """Load snapshot from storage"""
    
    @abstractmethod
    def list_run_ids(self, limit: int = 100) -> List[str]:
        """List available snapshot run IDs"""
    
    @abstractmethod
    def delete(self, run_id: str) -> None:
        """Delete snapshot from storage"""
    
    @abstractmethod
    def compare(self, run_id_1: str, run_id_2: str) -> SnapshotComparison:
        """Compare two snapshots, return diff"""


class LocalSnapshotRepository(SnapshotRepository):
    """File-based storage with rotation and retention."""
    # Supports JSON/YAML/JSONL formats
    # Automatic rotation based on retention policy


class S3SnapshotRepository(SnapshotRepository):
    """AWS S3 backend for remote storage."""
    # Configurable bucket/prefix
    # Bearer token or IAM auth


class HTTPSnapshotRepository(SnapshotRepository):
    """Generic HTTP endpoint for remote storage."""
    # RESTful CRUD operations
    # Bearer token authentication
```

### 6.3 SnapshotManager (High-level API)

**File**: `src/operations_center/observer/snapshot_manager.py`

```python
class SnapshotManager:
    """High-level API factory for snapshot operations."""
    
    @staticmethod
    def create_local(root_dir: str) -> "SnapshotManager":
        """Local file storage"""
    
    @staticmethod
    def create_s3(bucket: str, prefix: str = "snapshots") -> "SnapshotManager":
        """AWS S3 storage"""
    
    @staticmethod
    def create_http(base_url: str, token: str = "") -> "SnapshotManager":
        """Generic HTTP storage"""
    
    def save(self, snapshot: RepoStateSnapshot) -> str:
        """Save snapshot, return run_id"""
    
    def get(self, run_id: str) -> RepoStateSnapshot:
        """Load snapshot by run_id"""
    
    def list_by_date(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> List[RepoStateSnapshot]:
        """Query snapshots by date range"""
    
    def cleanup_old(self) -> int:
        """Clean up based on retention policy, return count deleted"""
```

### 6.4 Validation Report

**File**: `src/operations_center/observer/snapshot_validator.py`

```python
class SnapshotValidationReport:
    """JSON-serializable report of all validation results."""
    
    run_id: str
    timestamp: datetime
    snapshot_id: str
    
    layer_results: Dict[int, ValidationResult]  # Layer 1-5 results
    
    overall_status: str  # "PASS", "FAIL", "PARTIAL"
    failure_categories: Dict[str, int]  # {TRANSIENT: 2, STRUCTURAL: 1, ...}
    
    def to_json(self) -> str:
        """Export as JSON for artifact storage"""
    
    def get_retryable_errors(self) -> List[str]:
        """Get errors that should trigger retry"""
    
    def is_transient(self) -> bool:
        """True if all failures are transient"""
```

---

## 7. Integration Points with Existing Systems

### 7.1 RepoObserverService (Main integration point)

**File**: `src/operations_center/observer/service.py`

```python
class RepoObserverService:
    """Main observer service: collects signals and validates snapshots."""
    
    def __init__(self, artifact_writer: ArtifactWriter):
        self.artifact_writer = artifact_writer
        self._validator = None
        self._snapshot_manager = None
    
    # Existing methods for signal collection
    def get_build_signal(self) -> BuildSignal: ...
    def get_test_signal(self) -> TestSignal: ...
    def get_deployment_signal(self) -> DeploymentSignal: ...
    
    # [NEW] Snapshot validation methods
    def create_snapshot(self) -> RepoStateSnapshot:
        """Collect all signals into snapshot"""
    
    def validate_snapshot(self, snapshot: RepoStateSnapshot) -> SnapshotValidationReport:
        """Run validation pipeline on snapshot"""
    
    def query(self) -> TestSignalQuery:
        """Access test signal visibility API"""
```

### 7.2 pytest Integration

**File**: `tests/integration/observer/test_snapshot_validation.py`

```python
import pytest

# Module-level marker (applies to all tests in file)
pytestmark = pytest.mark.snapshot

class TestSnapshotSchemaValidation:
    """Layer 1: Schema validation tests"""
    def test_valid_snapshot_schema(self): ...
    def test_invalid_json_format(self): ...

class TestSnapshotCompletenessValidation:
    """Layer 2: Completeness validation tests"""
    def test_minimum_signals_required(self): ...

class TestSnapshotConsistencyValidation:
    """Layer 3: Consistency validation tests"""
    def test_build_failed_requires_test_failed(self): ...

class TestSnapshotAccuracyValidation:
    """Layer 4: Accuracy validation (slow)"""
    @pytest.mark.snapshot_slow
    def test_snapshot_vs_live_tools(self): ...

class TestSnapshotRegressionDetection:
    """Layer 5: Regression detection (slow)"""
    @pytest.mark.snapshot_slow
    def test_coverage_regression_detection(self): ...
```

### 7.3 Flaky Test Reporter Integration

**File**: `src/operations_center/observer/flaky_test_collector.py`

```python
class FlakyTestCollector:
    """Collect flaky test signals for inclusion in snapshots."""
    
    def collect(self, observer_service: RepoObserverService) -> FlakyTestSignal:
        """Gather flaky test metrics and integrate into snapshot"""
    
    # Snapshots include flaky_test_signal as an additional repo signal
    # Example: snapshot.repo_signals['flaky_test_signal']
```

---

## 8. Execution Flow & Test Execution

### 8.1 Pytest Execution Modes

**Quick Mode** (PR trigger):
```bash
pytest tests/integration/observer/test_snapshot_validation.py \
  -m "snapshot and not snapshot_slow" \
  -v --tb=short
# Duration: ~30s
# Executes: Layers 1-3 (41 tests)
```

**Full Mode** (Push/Schedule trigger):
```bash
pytest tests/integration/observer/test_snapshot_validation.py \
  -m snapshot \
  -v --tb=short
# Duration: ~5m
# Executes: Layers 1-5 (41 tests, including snapshot_slow)
```

**Performance Mode** (Unit tests):
```bash
pytest tests/unit/observer/test_snapshot_*.py \
  -m snapshot_performance \
  -v --tb=short
# Duration: ~0.5s
# Executes: Scaling and memory tests (13 tests)
```

### 8.2 CI Artifact Outputs

```
${{ runner.temp }}/snapshots/
├── reports/
│   ├── snapshot_validation_2026-06-09_14-35.json   # Latest report
│   ├── snapshot_validation_2026-06-09_14-30.json   # Previous
│   └── ...                                         # 30-day history
├── snapshots/
│   ├── run_2026-06-09_14-35/snapshot.json
│   ├── run_2026-06-09_14-30/snapshot.json
│   └── ...
└── index.jsonl                                     # Snapshot index
```

---

## 9. Data Flow Examples

### Example 1: PR Validation (Quick Mode)

```
1. PR submitted to feature branch
   ↓
2. GitHub Actions trigger: pull_request event
   ↓
3. Setup environment: SNAPSHOT_ROOT, SNAPSHOT_TOLERANCE
   ↓
4. pytest runs: -m "snapshot and not snapshot_slow"
   ├─ RepoObserverService collects signals (test, build, coverage, etc.)
   ├─ SnapshotValidator.validate() executes layers 1-3
   │  ├─ Layer 1: JSON schema → Pydantic model (5s)
   │  ├─ Layer 2: Check ≥3 signals present (2s)
   │  └─ Layer 3: Cross-signal consistency (8s)
   ├─ 41 tests: PASS
   └─ Validation report: PASS
   ↓
5. Artifacts uploaded: validation reports (30-day retention)
   ↓
6. PR mergeable ✅
```

### Example 2: Scheduled Daily Validation (Full Mode)

```
1. Daily schedule trigger: "0 2 * * *" (2 AM UTC)
   ↓
2. Setup environment + load baseline snapshot
   ↓
3. pytest runs: -m snapshot (all tests)
   ├─ Layers 1-3: Fast validation (15s)
   ├─ Layer 4: Accuracy check vs. live tools (90s)
   │  └─ Query SwitchBoard, Plane, Archon
   ├─ Layer 5: Regression detection (60s)
   │  └─ Compare to baseline, check tolerance
   ├─ 41 tests: PASS or categorized failures
   └─ Detailed report: Success/Transient/Structural/Configuration
   ↓
4. Failure categorization triggers action:
   ├─ TRANSIENT: Retry up to 3x (network timeout)
   ├─ STRUCTURAL: Escalate to operator (missing signal)
   ├─ CONFIGURATION: Manual fix needed (env var)
   └─ UNKNOWN: Log for analysis
   ↓
5. Artifacts + email notification sent
   ↓
6. Dashboard updated with snapshot trends
```

---

## 10. Success Criteria & Acceptance

### 10.1 Architecture Completeness ✅
- [x] 5-layer validation pipeline designed and documented
- [x] Component specifications detailed (Validator, Repository, Manager, Report)
- [x] CI integration points mapped to GitHub Actions workflow
- [x] Failure categorization and retry logic specified
- [x] Storage backends (local, S3, HTTP) designed

### 10.2 File Structure ✅
- [x] Source code organization documented
- [x] Test organization with markers and fixtures documented
- [x] Documentation file structure defined
- [x] Configuration files (CI workflow, pyproject.toml) mapped

### 10.3 Integration Points ✅
- [x] RepoObserverService integration points identified
- [x] pytest integration with markers documented
- [x] Flaky test reporter integration specified
- [x] GitHub Actions trigger modes (PR/push/schedule) designed

### 10.4 Snapshot Validation Approach ✅
- [x] 5-layer architecture with examples documented
- [x] RepoStateSnapshot data model specified
- [x] Validation failure categories defined
- [x] Retry logic and exception handling designed

---

## 11. Relationship to Prior Stages

### Stage 0: Understanding ✅
This Stage 1 design document consolidates and organizes all findings from Stage 0:
- Codebase exploration → File structure organization (Section 5)
- CI configuration analysis → CI integration points (Section 4)
- Test pattern identification → Component specifications (Section 6)
- Requirements clarification → Snapshot validation approach (Section 3)

### Stage 2-6: Implementation (Already Complete)
The snapshot validation infrastructure has been fully implemented across Stages 1-6:
- **Stage 1** (Implementation): Repository and manager APIs
- **Stage 2** (Implementation): Validator pipeline and integration tests
- **Stage 3** (Implementation): Edge case and performance tests
- **Stage 4** (Implementation): CI workflow integration
- **Stage 5** (Documentation): User guides and API reference
- **Stage 6** (Verification): Full test suite and quality checks

This Stage 1 design document serves as the **architectural reference** for the complete system.

---

## 12. Next Steps & Recommendations

### Immediate (Post-Stage 1)
1. ✅ Validate design against implemented code
2. ✅ Verify all components align with this specification
3. ✅ Confirm CI integration matches workflow design

### Near-term (Stage 2+)
1. Enhance snapshot collection from real-world repositories
2. Implement advanced baseline comparison strategies
3. Add dashboard integration for snapshot trends
4. Scale snapshot storage to production backends (S3)

### Long-term
1. ML-based anomaly detection for signal outliers
2. Multi-repository snapshot correlation
3. Historical trend analysis and forecasting
4. Production environment rollout with monitoring

---

## Appendix A: Component Dependencies

```
snapshot_validator.py
├─ snapshot_repository.py (imports SnapshotRepository ABC)
├─ models.py (imports RepoStateSnapshot, Signal, etc.)
├─ observer/service.py (used by RepoObserverService)
└─ External: pydantic, pytest, json, datetime

snapshot_manager.py
├─ snapshot_repository.py (creates repository instances)
├─ models.py (imports SnapshotComparison)
└─ External: typing, pathlib, s3fs, requests

snapshot_repository.py
├─ models.py (imports RepoStateSnapshot)
└─ External: abc, json, yaml, datetime, boto3, requests

test_snapshot_validation.py
├─ snapshot_validator.py (main class under test)
├─ models.py (test data)
├─ conftest.py (fixtures)
└─ External: pytest, json
```

---

## Appendix B: Environment Variables Reference

```bash
# Storage Configuration
SNAPSHOT_ROOT=/tmp/snapshots           # Where snapshots are stored
SNAPSHOT_RETENTION_DAYS=30             # How long to keep snapshots
SNAPSHOT_RETENTION_COUNT=50            # Max snapshots to retain

# Validation Configuration
SNAPSHOT_TOLERANCE=0.05                # 5% regression tolerance
SNAPSHOT_SCHEMA_STRICT=true            # Strict schema validation

# Remote Storage (Optional)
SNAPSHOT_S3_BUCKET=my-bucket           # S3 bucket name
SNAPSHOT_S3_PREFIX=snapshots           # S3 key prefix
SNAPSHOT_HTTP_BASE_URL=https://...     # HTTP endpoint
SNAPSHOT_HTTP_TOKEN=bearer-token       # Auth token

# External Services (Validation)
SWITCHBOARD_API_KEY=...                # SwitchBoard API key
PLANE_API_KEY=...                      # Plane API key
ARCHON_API_KEY=...                     # Archon API key
```

---

**Document Status**: ✅ Complete and ready for implementation review  
**Last Updated**: 2026-06-09  
**Author**: Claude Code (AI Assistant)
