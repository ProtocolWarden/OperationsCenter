# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Signal models for repository observation and analysis.

## Timestamp Strategy: Signal-Level vs Snapshot-Level

Signals in this module may have two timestamp sources:
1. **Signal-level `observed_at`**: Timestamp when the external tool ran (optional, may be None)
2. **Snapshot-level `observed_at`**: Timestamp when the snapshot was captured (always present in RepoStateSnapshot)

### When to Use Each Timestamp

**Signal-level observed_at is populated when:**
- The signal comes from out-of-process analysis (security scanner, benchmark tool, linter)
- The external tool provides its own timestamp (invocation time)
- The tool ran at a different time than the snapshot was captured

**Signal-level observed_at is None when:**
- The signal is computed locally without external tools
- The tool does not provide timing information
- The snapshot was taken but the external tool never ran

### Usage Pattern in Derivers

When using signals with optional observed_at in derivers, follow this pattern:

    # Prefer signal-level if available, fall back to snapshot-level
    observed_at = signal.observed_at or snapshots[0].observed_at

This ensures:
- More accurate timestamps when external tools provide them
- No null timestamps (snapshot-level is guaranteed non-None)
- Consistent timestamp semantics across all derivers

### Signals with Optional observed_at

These 6 signals perform out-of-process analysis and have optional observed_at:
- CheckSignal (test execution)
- DependencyDriftSignal (dependency analysis)
- ArchitectureSignal (module structure analysis)
- BenchmarkSignal (performance metrics)
- SecuritySignal (vulnerability scanning)
- CoverageSignal (code coverage analysis)

All other signals (TodoSignal, ExecutionHealthSignal, etc.) are computed locally
and use snapshot-level observed_at only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from operations_center.observer.validation import ParseErrorMetadata

OBSERVER_VERSION = 1


class RepoContextSnapshot(BaseModel):
    name: str
    path: Path
    current_branch: str
    base_branch: str | None = None
    is_dirty: bool


class CommitMetadata(BaseModel):
    sha_short: str
    author: str
    timestamp: datetime
    subject: str


class FileHotspot(BaseModel):
    path: str
    touch_count: int


class TestSignal(BaseModel):
    """Test execution results with breakdown metrics and coverage integration.

    Granular test counts, execution performance, failure categorization, and
    code coverage. Fields are self-documenting via their annotations below;
    `status` is one of passing/failing/flaky/partial/unavailable and
    `failure_category` is the primary failure type (assertion, timeout, …).
    """

    status: str
    test_count: int | None = None
    passed_count: int = 0
    failed_count: int = 0
    skip_count: int = 0
    xfailed_count: int = 0
    error_count: int = 0
    execution_time_ms: int | None = None
    coverage_percent: float | None = None
    failure_category: str | None = None
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None


# Backwards compatibility alias
CheckSignal = TestSignal


class DependencyDriftSignal(BaseModel):
    """Dependency manifest analysis and drift detection.

    Represents the analysis results from dependency manifest scanning tools
    (e.g., SBOM generators, dependency checkers, package auditors).

    Attributes:
        status: Overall dependency status ("healthy", "drift", "missing", "unavailable", etc.)
        source: Name of the tool that analyzed dependencies (e.g., "pip-audit", "cargo-audit", "yarn audit")
        observed_at: Timestamp when the dependency analysis was performed. Optional because:
            - The tool may not record execution timestamps
            - Analysis may be deferred or cached
            - Results may be imported from an external system
            If None, use snapshot.observed_at as fallback: `signal.observed_at or snapshot.observed_at`
        summary: Human-readable summary of dependency health
        parse_errors: Metadata about any parsing errors during collection

    When observed_at is used in derivers, prefer the signal-level value over snapshot-level:

        # In derivers that access dependency_drift
        observed_at = signal.dependency_drift.observed_at or snapshots[0].observed_at
    """

    status: str
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None
    parse_errors: ParseErrorMetadata = Field(default_factory=ParseErrorMetadata)


class TodoFileCount(BaseModel):
    path: str
    count: int


class TodoSignal(BaseModel):
    todo_count: int = 0
    fixme_count: int = 0
    top_files: list[TodoFileCount] = Field(default_factory=list)


class ExecutionRunRecord(BaseModel):
    run_id: str
    task_id: str
    worker_role: str
    outcome_status: str  # executed, no_op, skipped, or other
    outcome_reason: str | None = None
    validation_passed: bool | None = None


class ExecutionHealthSignal(BaseModel):
    total_runs: int = 0
    executed_count: int = 0
    no_op_count: int = 0
    unknown_count: int = 0
    error_count: int = 0
    validation_failed_count: int = 0
    recent_runs: list[ExecutionRunRecord] = Field(default_factory=list)
    parse_errors: ParseErrorMetadata = Field(default_factory=ParseErrorMetadata)


class BacklogItem(BaseModel):
    title: str
    item_type: str  # maintenance, feature, enhancement, arch, redesign, etc.
    description: str = ""


class BacklogSignal(BaseModel):
    items: list[BacklogItem] = Field(default_factory=list)


class LintViolation(BaseModel):
    path: str
    line: int
    col: int
    code: str
    message: str


class LintSignal(BaseModel):
    status: str  # "clean", "violations", "unavailable"
    violation_count: int = 0
    distinct_file_count: int = 0
    top_violations: list[LintViolation] = Field(default_factory=list)
    source: str | None = None
    parse_errors: ParseErrorMetadata = Field(default_factory=ParseErrorMetadata)


class TypeError(BaseModel):
    path: str
    line: int
    col: int
    code: str
    message: str


class TypeSignal(BaseModel):
    status: str  # "clean", "errors", "unavailable"
    error_count: int = 0
    distinct_file_count: int = 0
    top_errors: list[TypeError] = Field(default_factory=list)
    source: str | None = None
    parse_errors: ParseErrorMetadata = Field(default_factory=ParseErrorMetadata)


class ValidationFailureRecord(BaseModel):
    task_id: str
    worker_role: str
    total_runs: int
    validation_failure_count: int
    failure_rate: float = 0.0


class ValidationHistorySignal(BaseModel):
    status: str  # "nominal", "patterns_detected", "unavailable"
    tasks_analyzed: int = 0
    tasks_with_repeated_failures: list[ValidationFailureRecord] = Field(default_factory=list)
    overall_failure_rate: float = 0.0
    source: str | None = None
    parse_errors: ParseErrorMetadata = Field(default_factory=ParseErrorMetadata)


class CICheckRunRecord(BaseModel):
    name: str
    sha: str
    conclusion: str  # success, failure, timed_out, cancelled, skipped, neutral, etc.


class CIHistorySignal(BaseModel):
    status: str  # "nominal", "flaky", "failing", "unavailable"
    runs_checked: int = 0
    failure_rate: float = 0.0
    flaky_checks: list[str] = Field(default_factory=list)
    failing_checks: list[str] = Field(default_factory=list)
    recent_runs: list[CICheckRunRecord] = Field(default_factory=list)
    source: str | None = None


class ArchitectureSignal(BaseModel):
    """Code architecture and module dependency analysis.

    Represents the results of static architecture analysis tools that examine
    module structure, import relationships, and coupling.

    Attributes:
        status: Overall architecture health ("healthy", "warnings", "unavailable", etc.)
        source: Name of the analysis tool (e.g., "depcheck", "import-sort", "pydeps")
        observed_at: Timestamp when the architecture analysis was performed. Optional because:
            - Architecture analysis tools may not provide execution timestamps
            - Analysis results may be cached or imported
            - Analysis is expensive and may run less frequently than snapshots
            If None, use snapshot.observed_at as fallback: `signal.observed_at or snapshot.observed_at`
        max_import_depth: Maximum import depth in the module graph
        circular_dependencies: List of module pairs with circular import relationships
        coupling_score: Quantitative measure of module coupling (0.0-1.0, higher = worse)
        summary: Human-readable summary of architectural health

    When observed_at is used in derivers, prefer the signal-level value over snapshot-level:

        # In derivers that access architecture_signal
        observed_at = signal.architecture_signal.observed_at or snapshots[0].observed_at
    """

    status: str  # "healthy", "warnings", "unavailable"
    source: str | None = None
    observed_at: datetime | None = None
    max_import_depth: int | None = None
    circular_dependencies: list[str] = Field(default_factory=list)
    coupling_score: float | None = None
    summary: str | None = None


class BenchmarkSignal(BaseModel):
    """Performance benchmark results and regression detection.

    Represents the output of performance measurement tools that track metrics
    like execution time, memory usage, throughput, or latency over time.

    Attributes:
        status: Performance status ("nominal", "regression", "improvement", "unavailable", etc.)
        source: Name of the benchmark tool (e.g., "criterion", "JMH", "wrk", "hyperfine")
        observed_at: Timestamp when the benchmarks were executed. Optional because:
            - Benchmark tools may not record invocation timestamps
            - Benchmarks are computationally expensive and may not run on every snapshot
            - Results may be imported from external CI/performance tracking systems
            If None, use snapshot.observed_at as fallback: `signal.observed_at or snapshot.observed_at`
        benchmark_count: Number of benchmarks executed
        regressions: List of benchmarks that regressed (slowed down) compared to baseline
        summary: Human-readable summary of performance changes

    When observed_at is used in derivers, prefer the signal-level value over snapshot-level:

        # In derivers that access benchmark_signal
        observed_at = signal.benchmark_signal.observed_at or snapshots[0].observed_at
    """

    status: str  # "nominal", "regression", "unavailable"
    source: str | None = None
    observed_at: datetime | None = None
    benchmark_count: int = 0
    regressions: list[str] = Field(default_factory=list)
    summary: str | None = None


class SecuritySignal(BaseModel):
    """Security vulnerability and advisory scanning results.

    Represents the output of security analysis tools that detect vulnerabilities,
    outdated dependencies, and compliance issues.

    Attributes:
        status: Security status ("clean", "advisories", "critical", "unavailable", etc.)
        source: Name of the security scanner (e.g., "trivy", "snyk", "bandit", "semgrep")
        observed_at: Timestamp when the security scan was performed. Optional because:
            - Security scanners may not provide execution timestamps
            - Scans may be expensive and run less frequently than snapshots
            - Results may be imported from external security platforms
            If None, use snapshot.observed_at as fallback: `signal.observed_at or snapshot.observed_at`
        advisory_count: Total number of vulnerabilities found
        critical_count: Number of critical-severity vulnerabilities
        high_count: Number of high-severity vulnerabilities
        summary: Human-readable summary of security findings

    When observed_at is used in derivers, prefer the signal-level value over snapshot-level:

        # In derivers that access security_signal
        observed_at = signal.security_signal.observed_at or snapshots[0].observed_at
    """

    status: str  # "clean", "advisories", "unavailable"
    source: str | None = None
    observed_at: datetime | None = None
    advisory_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    summary: str | None = None


class UncoveredFile(BaseModel):
    path: str
    coverage_pct: float


class CoverageSignal(BaseModel):
    """Code coverage analysis results.

    Represents the output of code coverage measurement tools that track
    what fraction of the codebase is exercised by tests.

    Attributes:
        status: Coverage measurement status ("measured", "partial", "unavailable", etc.)
        total_coverage_pct: Overall code coverage percentage (0-100)
        uncovered_file_count: Number of files below the uncovered_threshold_pct
        uncovered_threshold_pct: Threshold for marking files as under-covered (default 80%)
        top_uncovered: List of files with lowest coverage, for focused improvement effort
        source: Name of the coverage tool (e.g., "coverage.py", "jacoco", "nyc", "llvm-cov")
        observed_at: Timestamp when coverage was measured. Optional because:
            - Coverage tools may not record measurement timestamps
            - Coverage analysis is computationally expensive and may not run on every snapshot
            - Results may be imported from external CI/coverage services
            If None, use snapshot.observed_at as fallback: `signal.observed_at or snapshot.observed_at`
        summary: Human-readable summary of coverage status and trends

    When observed_at is used in derivers, prefer the signal-level value over snapshot-level:

        # In derivers that access coverage_signal
        observed_at = signal.coverage_signal.observed_at or snapshots[0].observed_at
    """

    status: str  # "measured", "partial", "unavailable"
    total_coverage_pct: float | None = None
    uncovered_file_count: int = 0
    uncovered_threshold_pct: float = 80.0
    top_uncovered: list[UncoveredFile] = Field(default_factory=list)
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None


class FlakyTestSignal(BaseModel):
    """Flaky test detection and analysis results.

    Summarizes test flakiness patterns and trends detected across multiple test runs.
    This signal synthesizes Tier 1-3 flakiness observations into actionable metrics.

    Attributes:
        status: Flakiness measurement status ("measured", "partial", "unavailable")
        flaky_test_count: Number of tests with failure_rate > 10%
        unstable_test_count: Number of tests with 5-10% failure rate
        affected_modules: List of modules/packages containing flaky tests
        most_problematic_tests: Top N (up to 5) flakiest tests with metrics
        failure_rate_trend: Week-over-week change in overall failure rate (%)
        recovery_rate: Percentage of previously flaky tests now stable
        category_breakdown: Count of tests by flakiness category (transient, structural, etc.)
        estimated_impact: Estimated impact metrics (CI slowdown %, dev time hours)
        source: Name of the flakiness detection system (always "flaky-test-reporter")
        observed_at: Timestamp when flakiness analysis was performed
        summary: Human-readable summary of flakiness status
    """

    status: str = "unavailable"
    flaky_test_count: int = 0
    unstable_test_count: int = 0
    affected_modules: list[str] = Field(default_factory=list)
    most_problematic_tests: list[dict] = Field(default_factory=list)
    failure_rate_trend: float = 0.0
    recovery_rate: float = 0.0
    category_breakdown: dict[str, int] = Field(default_factory=dict)
    estimated_impact: dict[str, float] = Field(default_factory=dict)
    source: str = "flaky-test-reporter"
    observed_at: datetime | None = None
    summary: str | None = None


class RepoSignalsSnapshot(BaseModel):
    recent_commits: list[CommitMetadata] = Field(default_factory=list)
    file_hotspots: list[FileHotspot] = Field(default_factory=list)
    test_signal: CheckSignal
    dependency_drift: DependencyDriftSignal
    todo_signal: TodoSignal
    execution_health: ExecutionHealthSignal = Field(default_factory=ExecutionHealthSignal)
    backlog: BacklogSignal = Field(default_factory=BacklogSignal)
    lint_signal: LintSignal = Field(default_factory=lambda: LintSignal(status="unavailable"))
    type_signal: TypeSignal = Field(default_factory=lambda: TypeSignal(status="unavailable"))
    ci_history: CIHistorySignal = Field(
        default_factory=lambda: CIHistorySignal(status="unavailable")
    )
    validation_history: ValidationHistorySignal = Field(
        default_factory=lambda: ValidationHistorySignal(status="unavailable")
    )
    architecture_signal: ArchitectureSignal = Field(
        default_factory=lambda: ArchitectureSignal(status="unavailable")
    )
    benchmark_signal: BenchmarkSignal = Field(
        default_factory=lambda: BenchmarkSignal(status="unavailable")
    )
    security_signal: SecuritySignal = Field(
        default_factory=lambda: SecuritySignal(status="unavailable")
    )
    coverage_signal: CoverageSignal = Field(
        default_factory=lambda: CoverageSignal(status="unavailable")
    )
    flaky_test_signal: FlakyTestSignal = Field(
        default_factory=lambda: FlakyTestSignal(status="unavailable")
    )


class RepoStateSnapshot(BaseModel):
    """A complete snapshot of repository state at a point in time.

    Captures all signals (test results, dependencies, architecture, etc.) along with
    repository metadata at a single moment.

    Attributes:
        run_id: Unique identifier for this snapshot run
        observed_at: Timestamp when this snapshot was captured. This is a required field
            that serves as the fallback timestamp for signals with optional observed_at.
            When a signal's observed_at is None, use: `signal.observed_at or snapshot.observed_at`
        observer_version: Version of the observer that created this snapshot (for compatibility)
        source_command: The command/trigger that created this snapshot
        repo: Repository context metadata (name, branch, dirty status, etc.)
        signals: Collection of all signals captured in this snapshot
        collector_errors: Map of signal types to error messages if collection failed

    ## Timestamp Semantics

    The snapshot's observed_at represents when the snapshot collection completed.
    Individual signals may have their own observed_at timestamps that differ:
    - Earlier: If the signal was collected from a cache or external system
    - Later: Unlikely, but possible if async collection delayed snapshot finalization
    - None: If the signal tool didn't provide timing or hasn't run yet

    For derivers: always prefer signal-level observed_at over snapshot-level when available:

        # Safe fallback pattern used by all derivers
        observed_at = signal.observed_at or snapshot.observed_at
    """

    run_id: str
    observed_at: datetime
    observer_version: int = OBSERVER_VERSION
    source_command: str
    repo: RepoContextSnapshot
    signals: RepoSignalsSnapshot
    collector_errors: dict[str, str] = Field(default_factory=dict)
