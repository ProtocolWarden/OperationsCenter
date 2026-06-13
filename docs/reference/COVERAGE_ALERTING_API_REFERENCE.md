<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Copyright (C) 2026 ProtocolWarden -->

# Coverage Threshold Alerting System — API Reference

**Version**: 1.0  
**Last Updated**: 2026-06-12

## Table of Contents

1. [Core Data Models](#core-data-models)
2. [CoverageCollector](#coveragecollector)
3. [CoverageTrendRepository](#coveragetrendrepository)
4. [CoverageTrendManager](#coveragetrendmanager)
5. [CoverageAlertManager](#coveragealertmanager)
6. [CoverageAlertConfig](#coveragealertconfig)
7. [CoverageAlertRouter](#coveragealertrouter)
8. [Integration Points](#integration-points)

---

## Core Data Models

### CoverageSnapshot

Point-in-time measurement of code coverage.

```python
class CoverageSnapshot(BaseModel):
    """A single point-in-time coverage measurement."""
    
    timestamp: datetime
        # When measurement was taken (UTC)
    
    run_id: str
        # Unique identifier: Git commit SHA or test run ID
    
    source: str
        # Tool that produced measurement: "coverage.py", "jacoco", etc.
    
    # Repository-level metrics
    overall_statement_coverage_pct: float
        # Percentage of statements executed (0-100)
    
    overall_branch_coverage_pct: float
        # Percentage of branches taken (0-100)
    
    overall_line_coverage_pct: float
        # Percentage of lines executed (0-100)
    
    # Module-level breakdown
    module_coverages: list[ModuleCoverage]
        # Coverage for each module/package
        # Default: empty list
    
    # File-level details (optional)
    file_coverages: list[FileCoverage]
        # Coverage for each source file (for detailed diagnostics)
        # Default: empty list
    
    # Metadata
    test_execution_time_ms: int | None = None
        # Total test suite execution time in milliseconds
    
    test_count: int | None = None
        # Total number of tests executed
    
    uncovered_file_count: int = 0
        # Number of files with coverage < 80%
```

**Usage Example**:
```python
snapshot = CoverageSnapshot(
    timestamp=datetime.now(timezone.utc),
    run_id="abc123def456",
    source="coverage.py",
    overall_statement_coverage_pct=85.2,
    overall_branch_coverage_pct=72.5,
    overall_line_coverage_pct=85.2,
    module_coverages=[...],
    test_execution_time_ms=12500,
    test_count=342
)
```

---

### ModuleCoverage

Coverage metrics for a specific module/package.

```python
class ModuleCoverage(BaseModel):
    """Coverage metrics for a specific module/package."""
    
    module_path: str
        # Module identifier: "src/operations_center/observer"
    
    statement_coverage_pct: float
        # Statement coverage percentage (0-100)
    
    branch_coverage_pct: float
        # Branch coverage percentage (0-100)
    
    line_coverage_pct: float
        # Line coverage percentage (0-100)
    
    # Counts for detailed analysis
    statement_count: int
        # Total executable statements in module
    
    branch_count: int
        # Total branches in module
    
    line_count: int
        # Total executable lines in module
    
    # Derived status
    health_status: str
        # One of: "healthy" (≥threshold), "at_risk" (70-threshold), "critical" (<70)
```

**Health Status Rules**:
- `healthy`: Module coverage >= configured threshold for metric type
- `at_risk`: Module coverage between 70% and threshold
- `critical`: Module coverage < 70%

---

### FileCoverage

Coverage metrics for a specific source file.

```python
class FileCoverage(BaseModel):
    """Coverage metrics for a specific source file."""
    
    file_path: str
        # Source file path: "src/operations_center/observer/models.py"
    
    statement_coverage_pct: float
        # Statement coverage percentage (0-100)
    
    branch_coverage_pct: float
        # Branch coverage percentage (0-100)
    
    line_coverage_pct: float
        # Line coverage percentage (0-100)
    
    # Granular details
    uncovered_lines: list[tuple[int, int]]
        # Line ranges not executed: [(5, 10), (25, 30)]
        # Each tuple is (start_line, end_line) inclusive
    
    uncovered_branches: list[str]
        # Branch descriptions not covered: ["line 42 condition else branch"]
```

---

### CoverageTrendAnalysis

Computed trend metrics over a time window.

```python
class CoverageTrendAnalysis(BaseModel):
    """Computed trend metrics over a time window."""
    
    metric_type: str
        # One of: "statement", "branch", "line"
    
    granularity: str
        # One of: "repository", "module", "file"
    
    scope_id: str
        # For repository: "" (empty)
        # For module: "src/operations_center/observer"
        # For file: "src/operations_center/observer/models.py"
    
    # Time window
    window_start: datetime
        # Start of analysis window (UTC)
    
    window_end: datetime
        # End of analysis window (UTC)
    
    # Historical values
    measurements: list[tuple[datetime, float]]
        # List of (timestamp, coverage_pct) pairs, sorted by date
    
    # Computed metrics
    current_value: float
        # Most recent measurement
    
    average_value: float
        # Arithmetic mean of all measurements
    
    min_value: float
        # Minimum value in window
    
    max_value: float
        # Maximum value in window
    
    # Trend analysis
    trend_direction: str
        # One of: "improving", "stable", "degrading"
    
    trend_pct: float
        # Percentage change per day (slope)
        # Positive = improving, Negative = degrading
    
    regression_count: int
        # Number of day-to-day drops >= threshold
    
    # Stability
    standard_deviation: float
        # Standard deviation of measurements
    
    stability_score: float
        # 0-1 score: 1.0 = stable, 0.0 = highly volatile
    
    # Velocity and projection
    days_of_decline: int
        # Number of consecutive days with decline
    
    projected_value_7days: float | None = None
        # Estimated value 7 days from now (or None if unavailable)
```

---

### CoverageAlert

A generated coverage alert.

```python
class CoverageAlert(BaseModel):
    """A generated coverage alert."""
    
    alert_id: str
        # Unique identifier: e.g., "coverage_below_20260612_001"
    
    timestamp: datetime
        # When alert was generated (UTC)
    
    alert_type: str
        # One of: "below_threshold", "regression_detected", 
        # "trend_degrading", "module_critical_gap"
    
    severity: str
        # One of: "info", "warning", "critical", "emergency"
    
    # What triggered the alert
    metric_type: str
        # One of: "statement", "branch", "line"
    
    granularity: str
        # One of: "repository", "module", "file"
    
    scope_id: str
        # "" (repository), module path, or file path
    
    # Measurements
    current_value: float
        # Current measurement value
    
    threshold_or_baseline: float | None = None
        # Threshold or baseline value for comparison
    
    delta_pct: float
        # Change from baseline: current - baseline
    
    # Context
    baseline_type: str
        # One of: "minimum_threshold", "previous_run", 
        # "7day_avg", "30day_avg"
    
    # Remediation
    affected_modules: list[str]
        # Modules with coverage issues
    
    affected_files: list[str]
        # Source files with coverage issues
    
    recommendation: str | None = None
        # Actionable remediation suggestion
    
    # Status tracking
    acknowledged: bool = False
        # Whether alert has been reviewed
    
    acknowledged_by: str | None = None
        # User who acknowledged alert
    
    acknowledged_at: datetime | None = None
        # When alert was acknowledged
    
    dismissal_reason: str | None = None
        # Reason for dismissing alert (if applicable)
```

---

## CoverageCollector

Gathers coverage metrics from test execution output.

```python
class CoverageCollector:
    """Collects coverage metrics from test execution."""
    
    def collect(context: ObserverContext) -> CoverageSignal:
        """
        Collect coverage metrics and return signal.
        
        Parameters:
            context: ObserverContext with access to coverage data
        
        Returns:
            CoverageSignal with metrics and status
        
        Raises:
            CoverageCollectionError: If collection fails
        
        Implementation:
        1. Locate coverage output file (.coverage, coverage.json, etc.)
        2. Parse coverage tool output (coverage.py, jacoco, etc.)
        3. Extract metrics at repository/module/file granularities
        4. Handle errors gracefully (status="partial" or "unavailable")
        5. Return structured CoverageSignal
        
        Example:
            signal = collector.collect(context)
            # signal.status: "measured"
            # signal.total_coverage_pct: 85.2
            # signal.module_coverages: [ModuleCoverage(...), ...]
        """
```

---

## CoverageTrendRepository

Abstract base class for historical coverage data storage.

```python
class CoverageTrendRepository(ABC):
    """Abstract base for coverage trend storage backends."""
    
    @abstractmethod
    def save_snapshot(self, snapshot: CoverageSnapshot) -> None:
        """
        Store a coverage metrics snapshot.
        
        Parameters:
            snapshot: CoverageSnapshot to persist
        
        Implementation:
        - Serialize to JSON/JSONL
        - Write to storage backend (filesystem, S3, etc.)
        - Ensure idempotency (same run_id → same storage)
        """
    
    @abstractmethod
    def get_snapshot(self, run_id: str) -> CoverageSnapshot | None:
        """
        Retrieve a snapshot by run ID.
        
        Returns: CoverageSnapshot or None if not found
        """
    
    @abstractmethod
    def get_historical_data(
        self,
        metric_type: str,  # "statement", "branch", "line"
        granularity: str,  # "repository", "module", "file"
        scope_id: str | None = None,  # "" for repo, module path, file path
        start_date: datetime = None,
        end_date: datetime = None
    ) -> list[tuple[datetime, float]]:
        """
        Query coverage values over time.
        
        Returns:
            List of (timestamp, coverage_pct) tuples, sorted by date
        
        Example:
            data = repo.get_historical_data(
                metric_type="line",
                granularity="repository",
                start_date=datetime(2026, 6, 1),
                end_date=datetime(2026, 6, 12)
            )
            # Returns: [(2026-06-01 10:00, 85.2), (2026-06-02 10:00, 85.1), ...]
        """
    
    @abstractmethod
    def cleanup_old_snapshots(self, retention_days: int = 90) -> int:
        """
        Remove snapshots older than retention period.
        
        Returns: Number of snapshots deleted
        """
```

### LocalCoverageTrendRepository

File-based storage using JSONL format.

```python
class LocalCoverageTrendRepository(CoverageTrendRepository):
    """JSONL-based storage on local filesystem."""
    
    def __init__(self, base_path: str = ".coverage_data"):
        """
        Parameters:
            base_path: Root directory for coverage data
            Structure: .coverage_data/YYYY-MM-DD/run_id.jsonl
        """
    
    # Implements all abstract methods from CoverageTrendRepository
    # Stores one snapshot per JSONL file
    # Daily directory organization for easy retention cleanup
```

### S3CoverageTrendRepository

Cloud storage using AWS S3.

```python
class S3CoverageTrendRepository(CoverageTrendRepository):
    """S3-based storage for coverage trends."""
    
    def __init__(
        self,
        bucket: str,
        prefix: str = "coverage-trends",
        region: str = "us-west-2"
    ):
        """
        Parameters:
            bucket: S3 bucket name
            prefix: S3 key prefix (e.g., "coverage-trends")
            region: AWS region
        
        Key structure: {prefix}/snapshots/{repo}/{run_id}.json
        """
```

---

## CoverageTrendManager

High-level API for trend analysis and querying.

```python
class CoverageTrendManager:
    """Analyze coverage trends and compute metrics."""
    
    @staticmethod
    def create_local(base_path: str = ".coverage_data") -> "CoverageTrendManager":
        """Create manager with local JSONL storage."""
    
    @staticmethod
    def create_s3(bucket: str, prefix: str, region: str) -> "CoverageTrendManager":
        """Create manager with S3 storage."""
    
    def save_snapshot(self, snapshot: CoverageMetricsSnapshot) -> None:
        """
        Persist a coverage metrics snapshot.
        
        Parameters:
            snapshot: CoverageMetricsSnapshot to store
        """
    
    def get_latest_snapshot(self) -> CoverageSnapshot:
        """
        Retrieve most recent coverage measurement.
        
        Returns: Latest CoverageSnapshot
        Raises: CoverageTrendError if no snapshots exist
        """
    
    def compute_trend_analysis(
        self,
        metric_type: str,  # "statement", "branch", "line"
        granularity: str,  # "repository", "module", "file"
        scope_id: str | None = None,
        window_days: int = 7
    ) -> CoverageTrendAnalysis:
        """
        Compute trend metrics for a metric and scope.
        
        Parameters:
            metric_type: Type of metric
            granularity: Level of aggregation
            scope_id: Module/file path (empty for repository)
            window_days: Analysis window in days
        
        Returns: CoverageTrendAnalysis with trend direction, slope, projection
        
        Example:
            trend = manager.compute_trend_analysis(
                metric_type="line",
                granularity="repository",
                window_days=7
            )
            print(f"Trend: {trend.trend_direction} ({trend.trend_pct}% per day)")
        """
    
    def detect_regression(
        self,
        current: CoverageSnapshot,
        baseline: str = "previous_run"  # or "7day_avg", "30day_avg"
    ) -> bool:
        """
        Detect if coverage has regressed.
        
        Parameters:
            current: Current coverage snapshot
            baseline: Baseline type for comparison
        
        Returns: True if regression detected
        """
    
    def calculate_trend_slope(
        self,
        measurements: list[tuple[datetime, float]]
    ) -> float:
        """
        Calculate linear trend slope (% change per day).
        
        Parameters:
            measurements: List of (timestamp, value) tuples
        
        Returns: Slope in percentage per day
        
        Example:
            slope = -0.8  # Coverage declining 0.8% per day
        """
    
    def calculate_volatility_score(
        self,
        measurements: list[tuple[datetime, float]]
    ) -> float:
        """
        Calculate stability score (0-1, higher = more stable).
        
        Parameters:
            measurements: List of (timestamp, value) tuples
        
        Returns: 0-1 stability score
        """
```

---

## CoverageAlertManager

Generates coverage alerts based on thresholds and trends.

```python
class CoverageAlertManager:
    """Generate coverage alerts from snapshots and trends."""
    
    def generate_alerts(
        self,
        snapshot: CoverageSnapshot,
        config: "CoverageAlertConfig",
        history: CoverageTrendAnalysis | None = None
    ) -> list[CoverageAlert]:
        """
        Generate all active alerts for current state.
        
        Parameters:
            snapshot: Current coverage snapshot
            config: Alert configuration with thresholds
            history: Optional trend analysis for trend alerts
        
        Returns: List of CoverageAlert objects
        
        Checks:
        1. Below-threshold: Each metric against repository/module threshold
        2. Regression: Current vs. previous measurement
        3. Trend degrading: History with 5+ declining measurements
        4. Module critical gap: Module coverage vs. target
        
        Example:
            alerts = manager.generate_alerts(snapshot, config, history)
            for alert in alerts:
                if alert.severity in ["critical", "emergency"]:
                    # Route to immediate channels
                    route(alert, channels=["slack", "email"])
        """
    
    def compute_alert_severity(
        self,
        alert_type: str,
        gap: float,
        trend_velocity: float = 0.0
    ) -> str:
        """
        Map numeric metrics to severity level.
        
        Parameters:
            alert_type: Type of alert
            gap: Coverage gap from threshold (negative for shortfall)
            trend_velocity: Rate of change (% per day)
        
        Returns: Severity level: "info", "warning", "critical", "emergency"
        
        Severity Mapping:
            below_threshold:
              gap < -30%: "emergency"
              gap < -20%: "critical"
              gap < -10%: "warning"
              gap >= -10%: "info"
            
            trend_degrading:
              velocity < -2.0%/day: "critical"
              velocity < -1.0%/day: "warning"
              otherwise: "info"
        """
```

---

## CoverageAlertConfig

Configuration for coverage thresholds and alert rules.

```python
class CoverageAlertConfig(BaseModel):
    """Configuration for coverage alerting system."""
    
    # Repository-level defaults
    minimum_threshold_pct: float = 80.0
        # Minimum acceptable coverage
    
    warning_threshold_pct: float = 85.0
        # Threshold triggering warning
    
    target_threshold_pct: float = 90.0
        # Goal coverage level
    
    # Coverage-type specific thresholds
    statement_minimum: float = 75.0
        # Minimum statement coverage
    
    branch_minimum: float = 65.0
        # Minimum branch coverage (stricter)
    
    line_minimum: float = 75.0
        # Minimum line coverage
    
    # Regression detection
    run_to_run_threshold_pct: float = 2.0
        # Drop threshold from previous run
    
    window_7day_threshold_pct: float = 3.0
        # Drop threshold from 7-day average
    
    window_30day_threshold_pct: float = 5.0
        # Drop threshold from 30-day average
    
    # Trend detection
    min_consecutive_declining_runs: int = 5
        # Minimum consecutive declines to trigger alert
    
    min_trend_pct_per_day: float = -1.0
        # Minimum decline rate to trigger alert
    
    # Module overrides
    module_thresholds: dict[str, dict[str, float]] = Field(default_factory=dict)
        # {module_path: {metric_type: threshold}}
        # Example:
        #   {
        #     "src/operations_center/observer": {
        #       "statement": 85.0,
        #       "branch": 75.0
        #     }
        #   }
    
    # Alert routing
    alert_routes: list["AlertChannelRoute"] = Field(default_factory=list)
        # Routes for alert delivery
    
    default_channels: list[str] = Field(default_factory=lambda: ["operator"])
        # Fallback channels if no routes match
    
    # Methods
    def get_threshold(self, metric_type: str, granularity: str, scope_id: str = "") -> float:
        """
        Get applicable threshold for metric.
        
        Resolution order:
        1. Module-specific override (if granularity="module")
        2. Coverage-type specific (e.g., branch_minimum)
        3. Repository default (minimum_threshold_pct)
        """
    
    def get_routes_for_alert(self, alert: CoverageAlert) -> list[str]:
        """
        Get channels where alert should be routed.
        
        Parameters:
            alert: CoverageAlert to route
        
        Returns: List of channel names ["slack", "email", ...]
        """
```

---

## CoverageAlertRouter

Routes alerts to notification channels.

```python
class CoverageAlertRouter:
    """Route coverage alerts to notification channels."""
    
    def route_alert(
        self,
        alert: CoverageAlert,
        config: CoverageAlertConfig
    ) -> list["AlertChannelResult"]:
        """
        Route alert to applicable channels.
        
        Parameters:
            alert: CoverageAlert to route
            config: Alert configuration with routes
        
        Returns: List of AlertChannelResult objects (one per channel)
        
        Process:
        1. Find all routes matching alert criteria
        2. Format alert for each channel
        3. Deliver to channel (Slack, email, etc.)
        4. Collect results (success/failure for each channel)
        
        Example:
            results = router.route_alert(alert, config)
            for result in results:
                if result.success:
                    print(f"Delivered to {result.channel_name}")
                else:
                    print(f"Failed: {result.error_message}")
        """
```

---

## Integration Points

### RepoObserverService Integration

```python
class RepoObserverService:
    
    async def observe(self, context: ObserverContext) -> RepoSignalsSnapshot:
        """
        Existing observer method, enhanced with coverage alerts.
        """
        
        # Existing coverage collection
        coverage_signal = await self._collect_coverage_signal(context)
        
        # NEW: Compute trends and generate alerts
        if coverage_signal.status == "measured":
            trends = self.coverage_trend_manager.compute_trend_analysis(
                metric_type="line",
                granularity="repository"
            )
            alerts = self.coverage_alert_manager.generate_alerts(
                snapshot=coverage_signal,
                config=self.coverage_config,
                history=trends
            )
            coverage_signal.active_alerts = alerts
        
        # Route alerts to channels
        if coverage_signal.active_alerts:
            for alert in coverage_signal.active_alerts:
                self.coverage_alert_router.route_alert(alert, self.coverage_config)
        
        # Include in snapshot
        return RepoSignalsSnapshot(
            coverage_signal=coverage_signal,
            # ... other signals
        )
```

---

**API Reference Version**: 1.0  
**For comprehensive design documentation, see STAGE0_COVERAGE_THRESHOLD_ALERTING_SYSTEM.md**
