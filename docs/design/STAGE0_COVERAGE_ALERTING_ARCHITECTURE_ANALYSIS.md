# Stage 0: Coverage Threshold Alerting System — Architecture Analysis

**Document Version**: 1.0  
**Date**: 2026-06-13  
**Status**: Complete Analysis  
**SPDX-License-Identifier**: AGPL-3.0-or-later

---

## Executive Summary

This document provides a comprehensive architectural analysis of the Coverage Threshold Alerting System, documenting the structure, key classes, public APIs, and dependencies across the four critical modules:

1. **coverage_models.py** — Core data models for coverage metrics and trend analysis
2. **coverage_trend_manager.py** — High-level API for trend storage, retrieval, and analysis
3. **coverage_trend_repository.py** — Abstract repository interface with three backend implementations
4. **dashboard.py** — Dashboard data providers for visualization

The system is designed with clean layering: models → manager (business logic) → repository (persistence) → dashboard (presentation).

---

## 1. coverage_models.py — Data Models

**File Location**: `src/operations_center/observer/coverage_models.py`  
**Lines of Code**: 441  
**Purpose**: Defines core data structures for coverage metrics, trends, and alerts

### 1.1 Core Data Classes

#### CoverageMetric
Represents a single coverage measurement for a specific scope (repository, module, or file).

**Key Attributes**:
- `scope: str` — Identifier for what is being measured (repo name, module path, or file path)
- `scope_type: Literal["repository", "module", "file"]` — Granularity level
- `timestamp: datetime` — When measurement was taken
- `source: str` — Tool that produced the metric (e.g., "coverage.py", "pytest-cov")

**Coverage Metrics** (three types):
- `statement_coverage_pct: float` — % of executable statements executed
- `branch_coverage_pct: float` — % of conditional branches taken
- `line_coverage_pct: float` — % of source lines executed

**Execution Counts**:
- `statement_count, branch_count, line_count: int` — Total counts
- `executed_statements, executed_branches, executed_lines: int` — Executed counts

**Test Context**:
- `test_execution_time_ms: Optional[int]` — Test suite execution time
- `test_count: Optional[int]` — Number of tests executed

**Key Methods**:
```python
def get_coverage_by_type(coverage_type: Literal["statement", "branch", "line"]) -> float
    # Returns coverage percentage for specified type

def get_execution_count(count_type: Literal["statement", "branch", "line"]) -> int
    # Returns execution count for specified type
```

---

#### ModuleCoverage
Coverage metrics aggregated at the module/package level.

**Key Attributes**:
- `module_path: str` — Module identifier (e.g., "src/operations_center/observer")
- Coverage percentages and counts (statement, branch, line) — Same as CoverageMetric
- `health_status: Literal["healthy", "at_risk", "critical"]` — Derived health classification

**Key Methods**:
```python
def is_healthy() -> bool                        # Check if health_status == "healthy"
def is_at_risk() -> bool                        # Check if health_status == "at_risk"
def is_critical() -> bool                       # Check if health_status == "critical"
def get_average_coverage() -> float             # Average of three coverage types
```

**Health Status Determination**:
- **Healthy**: Coverage ≥ configured threshold (typically 80%+)
- **At-Risk**: Coverage between 70% and threshold
- **Critical**: Coverage < 70%

---

#### FileCoverage
Coverage metrics at the individual source file level.

**Key Attributes**:
- `file_path: str` — Path to source file (e.g., "src/observer.py")
- Coverage percentages (statement, branch, line)
- `uncovered_lines: list[tuple[int, int]]` — Line ranges not executed, e.g., [(10, 15), (20, 25)]
- `uncovered_branches: list[str]` — Descriptions of conditions not fully covered

**Key Methods**:
```python
def get_uncovered_line_count() -> int              # Sum of uncovered line ranges
def is_below_threshold(threshold: float) -> bool   # Check against line_coverage_pct
```

---

#### CoverageSnapshot
A point-in-time collection of coverage measurements across all granularities.

**Key Attributes**:
- `timestamp: datetime` — When snapshot was captured
- `run_id: str` — Unique identifier (often git commit SHA or test run ID)
- `source: str` — Tool that generated metrics
- Repository-level aggregates: `overall_statement_coverage_pct`, `overall_branch_coverage_pct`, `overall_line_coverage_pct`
- `module_coverages: list[ModuleCoverage]` — Per-module breakdown
- `file_coverages: list[FileCoverage]` — Per-file details (optional)
- Test context: `test_execution_time_ms`, `test_count`, `uncovered_file_count`

**Key Methods**:
```python
def get_critical_modules() -> list[ModuleCoverage]
    # Returns all modules with health_status == "critical"

def get_at_risk_modules() -> list[ModuleCoverage]
    # Returns all modules with health_status == "at_risk"

def get_files_below_threshold(threshold: float) -> list[FileCoverage]
    # Returns files with coverage below specified threshold
```

---

#### CoverageTrendAnalysis
Computed trend metrics over a time window (7 days, 30 days, etc.).

**Key Attributes**:
- `metric_type: Literal["statement", "branch", "line"]` — Which metric to analyze
- `granularity: Literal["repository", "module", "file"]` — Scope of analysis
- `scope_id: str` — Identifier (empty for repo, module path for module, file path for file)
- Time window: `window_start: datetime`, `window_end: datetime`
- `measurements: list[tuple[datetime, float]]` — Historical data points

**Computed Statistics**:
- Current, average, min, max coverage values
- `trend_direction: Literal["improving", "stable", "degrading"]` — Direction classification
- `trend_pct: float` — Percentage change from average
- `regression_count: int` — Number of drops from previous measurement
- `standard_deviation: float` — Variability of measurements
- `stability_score: float` — 0-1 score (1 = very stable, 0 = volatile)
- `days_of_decline: int` — Count of consecutive declining measurements
- `projected_value_7days: Optional[float]` — Extrapolated value 7 days forward

**Key Methods**:
```python
def is_improving() -> bool                   # Check trend_direction == "improving"
def is_degrading() -> bool                   # Check trend_direction == "degrading"
def is_stable() -> bool                      # Check trend_direction == "stable"
def get_total_change() -> float              # Difference from first to last measurement
```

---

#### CoverageAlert
A generated alert for coverage threshold violations, regressions, or trends.

**Key Attributes**:
- `alert_id: str` — Unique alert identifier
- `timestamp: datetime` — When alert was generated
- `alert_type: Literal["below_threshold", "regression_detected", "trend_degrading", "critical_module_coverage"]` — Alert category
- `severity: Literal["info", "warning", "critical", "emergency"]` — Severity level
- Scope information: `metric_type`, `granularity`, `scope_id`
- Coverage data: `current_value`, `threshold_or_baseline`, `delta_pct`
- `baseline_type: Literal["minimum_threshold", "previous_run", "7day_avg", "30day_avg", "trend"]` — What was compared

**Alert Context**:
- `affected_modules: list[str]` — Modules impacted
- `affected_files: list[str]` — Files impacted
- `recommendation: Optional[str]` — Suggested action

**Acknowledgment**:
- `acknowledged: bool` — Whether alert has been reviewed
- `acknowledged_by: Optional[str]` — Who acknowledged it
- `acknowledged_at: Optional[datetime]` — When acknowledged
- `dismissal_reason: Optional[str]` — Reason if dismissed

**Key Methods**:
```python
def is_critical() -> bool                    # severity in ("critical", "emergency")
def is_acknowledged() -> bool                # Check acknowledged flag
def is_dismissed() -> bool                   # Check dismissal_reason is set
def get_severity_level() -> int              # 0=info, 1=warning, 2=critical, 3=emergency
def exceeds_threshold() -> bool              # current_value > threshold_or_baseline
def is_below_target(target_pct: float = 90.0) -> bool
def get_alert_emoji() -> str                 # "ℹ️", "⚠️", "🚨", "🚨🚨"
def get_alert_type_label() -> str            # Human-readable type description
```

---

### 1.2 Module-Level Functions

#### compare_snapshots()
```python
def compare_snapshots(current: CoverageSnapshot, previous: CoverageSnapshot) -> dict[str, float]
    # Returns deltas: statement_delta, branch_delta, line_delta
```

#### is_snapshot_valid()
```python
def is_snapshot_valid(snapshot: CoverageSnapshot) -> bool
    # Validates coverage percentages (0-100), timestamp, and source
```

#### get_baseline_coverage()
```python
def get_baseline_coverage(baseline_type: str, current_value: float, threshold: float) -> float
    # Returns baseline value for threshold comparison
```

---

## 2. coverage_trend_manager.py — High-Level API

**File Location**: `src/operations_center/observer/coverage_trend_manager.py`  
**Lines of Code**: 399  
**Purpose**: High-level API for coverage trend storage, retrieval, and trend analysis

### 2.1 Main Class: CoverageTrendManager

Acts as a facade over the repository abstraction, providing business logic for trend analysis and pattern detection.

**Constructor**:
```python
def __init__(self, repository: CoverageTrendRepository) -> None
    # Initialize with a specific storage backend
```

**Factory Methods** (class methods for convenience):
```python
@classmethod
def create_local(root: Path | None = None, retention_days: int = 30) -> CoverageTrendManager
    # Create manager with local filesystem storage

@classmethod
def create_s3(bucket: str, prefix: str = "coverage-trends", access_key: str | None = None, 
              secret_key: str | None = None, region: str = "us-east-1") -> CoverageTrendManager
    # Create manager with S3 storage

@classmethod
def create_http(base_url: str, token: str | None = None) -> CoverageTrendManager
    # Create manager with HTTP API storage
```

---

### 2.2 Snapshot Operations

```python
def save_snapshot(snapshot: CoverageSnapshot) -> None
    # Store a coverage metrics snapshot

def get_snapshot(run_id: str) -> CoverageSnapshot | None
    # Retrieve a snapshot by run_id; returns None if not found

def list_snapshots(limit: int | None = None, start_date: datetime | None = None, 
                   end_date: datetime | None = None) -> list[CoverageSnapshot]
    # List snapshots within optional date range and limit

def delete_snapshot(run_id: str) -> bool
    # Delete a snapshot; returns True if deleted, False if not found
```

---

### 2.3 Trend Analysis Operations

```python
def save_trend_analysis(analysis: CoverageTrendAnalysis) -> None
    # Persist trend analysis to storage

def get_trend_analysis(metric_type: str, granularity: str, scope_id: str | None = None) 
                      -> CoverageTrendAnalysis | None
    # Retrieve previously computed trend analysis
```

---

### 2.4 Alert Operations

```python
def save_alert(alert: CoverageAlert) -> None
    # Store a coverage alert

def list_alerts(limit: int | None = None, severity: str | None = None) -> list[CoverageAlert]
    # List recent alerts, optionally filtered by severity ("info", "warning", "critical", "emergency")
```

---

### 2.5 Trend Computation & Analysis

#### compute_trend_analysis()
```python
def compute_trend_analysis(metric_type: Literal["statement", "branch", "line"],
                          granularity: Literal["repository", "module", "file"],
                          scope_id: str | None = None,
                          window_days: int = 7) -> CoverageTrendAnalysis
    # Analyze coverage trend over a time window
    # 1. Retrieves snapshots within window
    # 2. Extracts specified metric for scope
    # 3. Computes statistics (mean, stddev, min, max)
    # 4. Determines trend direction (improving/stable/degrading)
    # 5. Calculates stability score and 7-day projection
    # Returns: Fully populated CoverageTrendAnalysis
```

**Algorithm** (simplified):
1. Collect all measurements in time window
2. Sort by timestamp
3. Calculate statistics: mean, stddev, min, max
4. Determine trend: delta from first to last measurement
5. Calculate stability: 1 - (stddev / mean)
6. Project forward: slope × days

---

#### detect_regression()
```python
def detect_regression(current_snapshot: CoverageSnapshot,
                     metric_type: Literal["statement", "branch", "line"],
                     threshold_pct: float = 2.0) -> bool
    # Detect if coverage dropped more than threshold_pct since previous measurement
    # Compares current run against immediately previous run
```

---

#### calculate_trend_slope()
```python
def calculate_trend_slope(metric_type: Literal["statement", "branch", "line"],
                         granularity: Literal["repository", "module", "file"],
                         scope_id: str | None = None,
                         window_days: int = 7) -> float
    # Calculate slope of trend: % change per day
    # Used for velocity-based projections
```

---

#### calculate_volatility_score()
```python
def calculate_volatility_score(metric_type: Literal["statement", "branch", "line"],
                              granularity: Literal["repository", "module", "file"],
                              scope_id: str | None = None,
                              window_days: int = 7) -> float
    # Calculate volatility: 0-1 score where higher = more volatile
    # Formula: coefficient_of_variation / 100 (clamped to [0, 1])
```

---

#### get_historical_data()
```python
def get_historical_data(metric_type: Literal["statement", "branch", "line"],
                       granularity: Literal["repository", "module", "file"],
                       scope_id: str | None = None,
                       start_date: datetime | None = None,
                       end_date: datetime | None = None) -> list[tuple[datetime, float]]
    # Retrieve raw historical coverage data for a metric
    # Returns: Sorted list of (timestamp, coverage_value) tuples
```

---

### 2.6 Internal Methods

#### _extract_metric_value()
```python
def _extract_metric_value(snapshot: CoverageSnapshot,
                         metric_type: Literal["statement", "branch", "line"],
                         granularity: Literal["repository", "module", "file"],
                         scope_id: str | None = None) -> float | None
    # Extract a specific metric from a snapshot
    # Routes to correct location based on granularity:
    # - repository: reads overall_*_coverage_pct
    # - module: searches module_coverages, returns matching module's metric
    # - file: searches file_coverages, returns matching file's metric
```

---

## 3. coverage_trend_repository.py — Persistence Layer

**File Location**: `src/operations_center/observer/coverage_trend_repository.py`  
**Lines of Code**: 877  
**Purpose**: Abstract repository interface and three concrete backend implementations

### 3.1 Abstract Base: CoverageTrendRepository

Defines the contract that all storage backends must implement.

#### Abstract Methods

```python
@abstractmethod
def store_snapshot(snapshot: CoverageSnapshot) -> dict[str, str | int]
    # Store snapshot and return metadata dict with: run_id, observed_at, version, path, checksum

@abstractmethod
def load_snapshot(run_id: str) -> CoverageSnapshot
    # Load snapshot by run_id; raises FileNotFoundError if not found

@abstractmethod
def list_snapshots(limit: int | None = None, start_date: datetime | None = None, 
                  end_date: datetime | None = None) -> list[dict[str, str | int]]
    # List snapshot metadata, optionally filtered by date range and limit

@abstractmethod
def delete_snapshot(run_id: str) -> bool
    # Delete snapshot; return True if deleted, False if not found

@abstractmethod
def store_trend_analysis(analysis: CoverageTrendAnalysis) -> dict[str, str | int]
    # Store trend analysis and return metadata

@abstractmethod
def load_trend_analysis(metric_type: str, granularity: str, scope_id: str | None = None) 
                       -> CoverageTrendAnalysis | None
    # Load trend analysis; return None if not found

@abstractmethod
def store_alert(alert: CoverageAlert) -> dict[str, str | int]
    # Store alert and return metadata

@abstractmethod
def list_alerts(limit: int | None = None, severity: str | None = None) -> list[CoverageAlert]
    # List recent alerts, optionally filtered by severity

@abstractmethod
def cleanup(retention_days: int = 30) -> list[str]
    # Delete old data; return list of deleted run_ids
```

---

### 3.2 Enum: CoverageTrendFormat

```python
class CoverageTrendFormat(str, Enum):
    JSON = "json"      # Single JSON file per record
    JSONL = "jsonl"    # JSONL (one JSON per line) for streaming
```

---

### 3.3 Helper Functions

These functions are used by all three backends to ensure consistency:

```python
def _generate_checksum(content: str) -> str
    # Generate SHA-256 checksum for content integrity verification

def _create_snapshot_metadata(snapshot: CoverageSnapshot, path: str, 
                             checksum: str | None = None) -> dict[str, str | int]
    # Create metadata dict: {run_id, observed_at, version, path, checksum}

def _create_trend_metadata(analysis: CoverageTrendAnalysis, path: str, 
                          checksum: str | None = None) -> dict[str, str | int]
    # Create metadata dict for trend analysis

def _create_alert_metadata(alert: CoverageAlert, path: str, 
                          checksum: str | None = None) -> dict[str, str | int]
    # Create metadata dict for alerts
```

---

### 3.4 Backend 1: LocalCoverageTrendRepository

Filesystem-based storage with index and retention.

**Constructor**:
```python
def __init__(self, root: Path | None = None, retention_days: int = 30,
            default_format: CoverageTrendFormat = CoverageTrendFormat.JSONL) -> None
    # root: Base directory for storage (default: .coverage_data)
    # retention_days: Days to keep data before cleanup
    # default_format: JSONL or JSON
```

**Directory Structure**:
```
.coverage_data/
├── index.json              # Metadata index
├── snapshots/
│   ├── {run_id}/
│   │   ├── snapshot.json
│   │   ├── metadata.json
│   ├── {run_id}/
│   └── ...
├── trends/
│   ├── {metric_type}_{granularity}_{scope_id}.json
│   └── ...
├── alerts/
│   ├── alert_1.json
│   ├── alert_2.json
│   └── ...
```

**Key Methods** (in addition to abstract interface):
```python
def _load_index() -> dict[str, dict[str, Any]]
    # Load and cache the index.json file

def _save_index() -> None
    # Persist the in-memory index to index.json
```

**Characteristics**:
- ✅ Zero external dependencies
- ✅ Good for single-machine/CI environments
- ✅ Full history preserved (with rotation)
- ❌ Not suitable for distributed systems
- ❌ Filesystem concurrency limitations

---

### 3.5 Backend 2: S3CoverageTrendRepository

AWS S3-based cloud storage.

**Constructor**:
```python
def __init__(self, bucket: str, prefix: str = "coverage-trends",
            access_key: str | None = None, secret_key: str | None = None,
            region: str = "us-east-1") -> None
    # bucket: S3 bucket name
    # prefix: S3 prefix/folder
    # access_key, secret_key: AWS credentials (uses env if not provided)
    # region: AWS region
```

**S3 Structure**:
```
s3://{bucket}/{prefix}/
├── snapshots/
│   ├── {run_id}/snapshot.json
│   ├── {run_id}/metadata.json
│   └── ...
├── trends/
│   ├── {metric_type}_{granularity}_{scope_id}.json
│   └── ...
├── alerts/
│   ├── alert_1.json
│   └── ...
├── index.json
```

**Characteristics**:
- ✅ Scalable, cloud-native storage
- ✅ Built-in versioning and durability
- ✅ Handles distributed/concurrent access
- ✅ Pagination support for large datasets (1000+ objects)
- ❌ Requires boto3 and AWS credentials
- ❌ Potential latency and API costs

---

### 3.6 Backend 3: HTTPCoverageTrendRepository

HTTP API-based remote storage (e.g., custom server or managed service).

**Constructor**:
```python
def __init__(self, base_url: str, token: str | None = None) -> None
    # base_url: Base URL of API (e.g., https://coverage-api.example.com)
    # token: Bearer token for authentication
```

**API Endpoints** (expected by implementation):
```
POST   /snapshots              - Store snapshot
GET    /snapshots/{run_id}     - Load snapshot
GET    /snapshots              - List snapshots (with query filters)
DELETE /snapshots/{run_id}     - Delete snapshot

POST   /trends                 - Store trend analysis
GET    /trends                 - Load trend analysis (with query params)

POST   /alerts                 - Store alert
GET    /alerts                 - List alerts (with severity filter)

POST   /cleanup                - Trigger cleanup
```

**Characteristics**:
- ✅ Works with any HTTP API
- ✅ Can use existing backend infrastructure
- ✅ Bearer token authentication
- ❌ Requires external service
- ❌ Network-dependent reliability
- ❌ Latency variability

---

### 3.7 Shared Implementation Details

All three backends handle:

**Snapshot Storage**:
- Serialize CoverageSnapshot to JSON
- Generate checksum for integrity
- Store with metadata
- Maintain index/catalog

**Trend Analysis Storage**:
- Store with metric_type, granularity, scope_id as key
- Enable retrieval of latest analysis for a scope

**Alert Storage**:
- Store alerts with alert_id as key
- Enable filtering by severity
- Track acknowledgment status

**Retention/Cleanup**:
- Delete snapshots older than retention_days
- Preserve index consistency

---

## 4. dashboard.py — Presentation Layer

**File Location**: `src/operations_center/observer/dashboard.py`  
**Lines of Code**: 927  
**Purpose**: Format coverage and health data for dashboard visualization

### 4.1 Data Classes (Dataclasses)

#### DashboardMetric
Represents a single metric for dashboard display.

**Attributes**:
- `name: str` — Metric display name
- `value: float | int | str` — Current value
- `unit: str` — Unit of measurement (e.g., "count", "%", "ms")
- `status: str` — Status classification ("HEALTHY", "WARNING", "CRITICAL")
- `threshold_warning: Optional[float]` — Warning threshold
- `threshold_critical: Optional[float]` — Critical threshold
- `timestamp: datetime` — When metric was measured

**Methods**:
```python
def to_dict(self) -> dict
    # Convert to JSON-serializable dict
```

---

#### DashboardPanel
A group of related metrics for dashboard display.

**Attributes**:
- `title: str` — Panel title
- `description: str` — Panel description
- `metrics: list[DashboardMetric]` — Metrics in this panel
- `timestamp: datetime` — Panel generation time

**Methods**:
```python
def to_dict(self) -> dict
    # Convert to JSON-serializable dict with nested metric dicts
```

---

#### DashboardSnapshot
Complete dashboard snapshot with all panels and alerts.

**Attributes**:
- `timestamp: datetime` — Snapshot time
- `system_status: str` — Overall system status
- `panels: list[DashboardPanel]` — All panels to display
- `alerts: list[str]` — Active alert messages

**Methods**:
```python
def to_dict(self) -> dict
    # Convert to JSON-serializable dict
```

---

### 4.2 Main Class: DashboardProvider

Orchestrates data collection and formatting for dashboard display.

**Constructor**:
```python
def __init__(self, metrics_collector: MetricsCollector,
            health_checker: HealthChecker,
            log_reader: Optional[StructuredLogReader] = None,
            flaky_test_signal: Optional[FlakyTestSignal] = None,
            coverage_snapshot: Optional[CoverageSnapshot] = None,
            coverage_trends: Optional[CoverageTrendAnalysis] = None,
            coverage_signal: Optional[CoverageSignal] = None) -> None
    # Initialize with dependencies (some optional for partial dashboards)
```

---

### 4.3 Main Public Method

#### generate_snapshot()
```python
def generate_snapshot(self) -> DashboardSnapshot
    # Generate complete dashboard snapshot
    # 1. Collect all available panels (system, health, coverage, flaky tests)
    # 2. Run health checks
    # 3. Format metrics and status
    # 4. Compile alerts from health report
    # Returns: DashboardSnapshot ready for JSON serialization
```

---

### 4.4 Panel Generation Methods

```python
def _panel_system_overview() -> DashboardPanel
    # System status: total collectors, healthy, degraded, critical

def _panel_error_rates() -> DashboardPanel
    # Error rate metrics: P50, P95, P99 error rates

def _panel_latency() -> DashboardPanel
    # Latency metrics: P50, P95, P99 request latency

def _panel_collector_health() -> DashboardPanel
    # Collector-specific health: collector status, message counts

def _panel_recent_errors() -> DashboardPanel
    # Recent error log entries with timestamps and severity

def _panel_flaky_test_summary() -> DashboardPanel
    # Flaky test statistics: count, failure rate, trend

def _panel_flaky_test_categories() -> DashboardPanel
    # Flaky tests categorized by type (network, timeout, concurrency, etc.)

def _panel_most_problematic_tests() -> DashboardPanel
    # Top flaky tests ranked by failure frequency

def _panel_coverage_summary() -> DashboardPanel
    # Repository-level coverage: statement, branch, line percentages

def _panel_coverage_by_module() -> DashboardPanel
    # Module-level coverage: top 10 modules by coverage or risk

def _panel_coverage_trend() -> DashboardPanel
    # Coverage trends: 7-day trend, direction, projection

def _panel_coverage_alerts() -> DashboardPanel
    # Active coverage alerts: threshold violations, regressions
```

---

## 5. Dependencies & Module Interactions

### 5.1 Dependency Hierarchy

```
Dashboard Layer (Presentation)
    ↓
Manager Layer (Business Logic)
    ↓
Repository Layer (Persistence)
    ↓
Model Layer (Data Structures)
```

---

### 5.2 Detailed Interactions

#### coverage_models.py (Foundation)
- **No dependencies** on other coverage modules
- Imported by: Manager, Repository, Dashboard
- Provides: Data structures (CoverageSnapshot, CoverageMetric, etc.)

---

#### coverage_trend_repository.py (Persistence)
- **Imports from**: coverage_models (data classes)
- **Imported by**: coverage_trend_manager
- **External dependencies**: boto3 (optional, for S3), requests (optional, for HTTP)
- **Provides**: 
  - Abstract CoverageTrendRepository interface
  - Three concrete implementations (Local, S3, HTTP)
  - Helper functions for metadata/checksum management

---

#### coverage_trend_manager.py (Business Logic)
- **Imports from**: coverage_models, coverage_trend_repository
- **Imported by**: Coverage alerting system, dashboard
- **Provides**:
  - Factory methods for creating managers with different backends
  - Trend analysis computation (statistics, projections)
  - Regression and volatility detection
  - High-level API for snapshot/trend/alert operations

**Key Operations**:
```
Input: CoverageSnapshot → Manager → Repository → Storage
       (one-time measurement)

Analysis: List snapshots → Manager computes statistics → CoverageTrendAnalysis
          (historical analysis)

Detection: Current snapshot → Manager.detect_regression() → Boolean result
           (regression detection)
```

---

#### dashboard.py (Presentation)
- **Imports from**: coverage_models, health_checks, metrics, models
- **Optionally uses**: CoverageTrendManager (via injected dependencies)
- **Depends on**: MetricsCollector, HealthChecker
- **Provides**: DashboardSnapshot with formatted panels and metrics
- **Used by**: API endpoints, UI serialization

**Flow**:
```
Coverage Data → DashboardProvider → DashboardSnapshot → JSON → UI
(models)         (formatting)      (structured)        (serialization)
```

---

### 5.3 Data Flow Example: End-to-End

**Scenario**: Store coverage snapshot and generate trend analysis

```python
# 1. Create data model
snapshot = CoverageSnapshot(
    timestamp=datetime.now(timezone.utc),
    run_id="abc123def456",
    source="coverage.py",
    overall_statement_coverage_pct=85.5,
    overall_branch_coverage_pct=78.2,
    overall_line_coverage_pct=82.1,
    module_coverages=[...],
    file_coverages=[...]
)

# 2. Store via manager
manager = CoverageTrendManager.create_local()
manager.save_snapshot(snapshot)

# 3. Compute trend (manager loads historical data via repository)
trend = manager.compute_trend_analysis(
    metric_type="line",
    granularity="repository",
    window_days=7
)

# 4. Detect regression
is_regression = manager.detect_regression(snapshot, metric_type="line")

# 5. Dashboard display (optional)
dashboard = DashboardProvider(...)
dashboard_snapshot = dashboard.generate_snapshot()  # Includes coverage panel
```

---

### 5.4 Configuration Integration

**coverage_config.py** (referenced but separate):
- Provides configuration for thresholds and retention
- Loaded by: CoverageTrendManager and monitoring systems
- Not directly imported by models/manager/repository
- Passed as parameters or environment variables

---

### 5.5 Deployment Backends

**Local Filesystem** (typical for CI/CD):
```
CoverageTrendManager ← LocalCoverageTrendRepository ← .coverage_data/
```
- Good for: single-machine, CI pipelines
- Storage: rotating JSON files on disk

---

**S3** (typical for production observability):
```
CoverageTrendManager ← S3CoverageTrendRepository ← S3 bucket
```
- Good for: distributed systems, long-term archival
- Requirements: AWS credentials, boto3
- Pagination: handles 1000+ objects per list

---

**HTTP API** (typical for managed services):
```
CoverageTrendManager ← HTTPCoverageTrendRepository ← External API
```
- Good for: integration with existing platforms
- Flexibility: works with any HTTP API
- Authentication: bearer tokens

---

## 6. Key Design Patterns

### 6.1 Strategy Pattern
**Repository abstraction** — CoverageTrendRepository defines interface, three concrete strategies (Local, S3, HTTP) provide different storage mechanisms.

### 6.2 Factory Pattern
**Manager class methods** — create_local(), create_s3(), create_http() provide convenient factory methods for backend selection.

### 6.3 Facade Pattern
**CoverageTrendManager** — Simplifies complex repository operations into easy-to-use high-level API.

### 6.4 Template Method Pattern
**All repositories** — Share common cleanup logic and metadata creation (via helper functions).

---

## 7. Thread Safety & Concurrency

### LocalCoverageTrendRepository
- ❌ **NOT fully thread-safe** — In-memory index can have races
- ✅ **OK for CI/CD** — Sequential test runs
- ❌ **NOT suitable** for concurrent writes from multiple processes

### S3CoverageTrendRepository
- ✅ **Thread-safe** — S3 provides atomic operations
- ✅ **Distributed** — Handles concurrent access
- ✅ **Recommended** for multi-service deployments

### HTTPCoverageTrendRepository
- **Depends on server** — Backend API's concurrency handling
- **Typically safe** — HTTP API should handle concurrent requests

---

## 8. Error Handling

### Common Exceptions

```python
FileNotFoundError           # Snapshot/trend/alert not found
json.JSONDecodeError        # Corrupted JSON file (local backend)
boto3.exceptions.S3Error    # S3 connectivity/permission errors
requests.exceptions.RequestException  # HTTP API errors
```

### Resilience Patterns

- **Manager methods** return `None` for missing items (graceful degradation)
- **Repository methods** raise exceptions for storage errors (explicit failure)
- **Local backend** has error recovery for corrupted files
- **All backends** validate JSON structure before deserialization

---

## 9. Configuration & Customization

### Local Backend
```python
manager = CoverageTrendManager.create_local(
    root=Path("/var/coverage-data"),
    retention_days=60
)
```

### S3 Backend
```python
manager = CoverageTrendManager.create_s3(
    bucket="my-coverage-bucket",
    prefix="prod/coverage",
    region="us-west-2"
)
```

### HTTP Backend
```python
manager = CoverageTrendManager.create_http(
    base_url="https://coverage-api.internal",
    token="sk_live_..."
)
```

---

## 10. Performance Considerations

### Time Complexity

| Operation | Local | S3 | HTTP |
|-----------|-------|----|----|
| store_snapshot | O(1) | O(1) | O(1) |
| load_snapshot | O(1) | O(1) | O(1) |
| list_snapshots (N items) | O(N) | O(N/1000) paginated | O(N) |
| compute_trend_analysis | O(N) | O(N) | O(N) |
| detect_regression | O(1) | O(1) | O(1) |
| cleanup | O(N log N) | O(N) | O(N) |

### Space Complexity
- Each snapshot: ~10-50 KB (depending on module count)
- 30-day history: ~300 KB - 1.5 MB
- S3: Minimal local memory, unlimited cloud storage
- Local: Filesystem storage

---

## 11. Testing Strategy

### Unit Test Modules
- `test_coverage_models.py` (1,186 lines) — Model validation and calculations
- `test_coverage_trend_manager.py` (1,007 lines) — Manager logic and analysis
- `test_coverage_trend_repository.py` (1,681 lines) — All three backends

### Test Coverage
- ✅ CRUD operations (store, load, list, delete)
- ✅ Trend computation (statistics, projections)
- ✅ Regression detection
- ✅ Edge cases (empty data, outliers, corrupted files)
- ✅ Concurrent access patterns
- ✅ Cleanup/retention logic
- ✅ All three backend implementations

---

## 12. Acceptance Criteria Status

| Criterion | Status | Evidence |
|-----------|--------|----------|
| coverage_models.py structure and key classes documented | ✅ Complete | Section 1 — 6 classes, 3 functions documented |
| coverage_trend_manager.py structure and key classes documented | ✅ Complete | Section 2 — 1 main class with 23 methods |
| coverage_trend_repository.py structure and key classes documented | ✅ Complete | Section 3 — Abstract interface + 3 implementations |
| dashboard.py structure and key classes documented | ✅ Complete | Section 4 — 4 dataclasses + DashboardProvider |
| Dependencies and interactions identified | ✅ Complete | Section 5 — Detailed dependency hierarchy and data flows |

---

## Appendix A: Import Graph

```
coverage_models.py (no imports from coverage modules)
    ↑ ↑ ↑
    │ │ │
    ├─ coverage_trend_repository.py ─────→ (boto3, requests optional)
    │   ↑
    │   └── coverage_trend_manager.py
    │       ↑
    │       └── coverage_alerting.py
    │       └── dashboard.py ←── (health_checks, metrics, models)
    │
    └─ (any external consumer of models)
```

---

## Appendix B: Quick Reference

### Create a Manager
```python
# Local filesystem
manager = CoverageTrendManager.create_local(root=Path(".coverage"))

# S3
manager = CoverageTrendManager.create_s3(bucket="coverage-bucket")

# HTTP API
manager = CoverageTrendManager.create_http(base_url="https://api.example.com")
```

### Store and Retrieve
```python
# Store
manager.save_snapshot(snapshot)
manager.save_trend_analysis(analysis)
manager.save_alert(alert)

# Retrieve
snapshot = manager.get_snapshot(run_id)
trend = manager.get_trend_analysis(metric_type, granularity, scope_id)
alerts = manager.list_alerts(severity="critical")
```

### Analyze Trends
```python
# Compute trend over 7 days
trend = manager.compute_trend_analysis("line", "repository", window_days=7)
print(f"Current: {trend.current_value}%, Trend: {trend.trend_direction}")

# Detect regression
if manager.detect_regression(snapshot, "statement", threshold_pct=2.0):
    print("Regression detected!")

# Calculate slope and volatility
slope = manager.calculate_trend_slope("line", "repository")
volatility = manager.calculate_volatility_score("line", "repository")
```

### Dashboard Display
```python
# Generate dashboard
provider = DashboardProvider(metrics_collector, health_checker, 
                            coverage_snapshot=snapshot,
                            coverage_trends=trend)
dashboard = provider.generate_snapshot()
print(dashboard.to_dict())  # JSON-serializable
```

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-13 | Initial comprehensive architecture analysis |

---

**SPDX-License-Identifier**: AGPL-3.0-or-later  
**Copyright**: Copyright (C) 2026 ProtocolWarden

