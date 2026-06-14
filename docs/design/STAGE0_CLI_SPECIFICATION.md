---
status: complete
---

# Stage 0: Observer Snapshot Validation CLI Specification

**Document Status**: Complete specification for manual CI run snapshot validation  
**Version**: 1.0  
**Date**: 2026-06-14

## Executive Summary

This document specifies the observer snapshot validation CLI for manual CI run testing. The CLI enables developers and CI operators to validate repository state snapshots against a comprehensive 5-layer validation pipeline without requiring full CI execution. It provides flexible validation layer selection, configurable tolerances, and multiple output formats.

---

## Table of Contents

1. [5-Layer Validation Pipeline Analysis](#5-layer-validation-pipeline-analysis)
2. [Validation Functions & Module Inventory](#validation-functions--module-inventory)
3. [CLI Command Interface Design](#cli-command-interface-design)
4. [Input/Output Formats](#inputoutput-formats)
5. [Error Handling & Exit Codes](#error-handling--exit-codes)
6. [Performance Targets](#performance-targets)
7. [User Experience Requirements](#user-experience-requirements)
8. [Architecture Overview](#architecture-overview)

---

## 5-Layer Validation Pipeline Analysis

### Overview

The snapshot validation system implements a 5-layer validation pipeline that progressively validates repository state snapshots from basic structural correctness through advanced regression detection. Each layer builds on previous layers with increasing validation complexity and execution time.

### Layer 1: Schema Validation (Fast Path - ~50ms)

**Purpose**: Verify snapshot JSON structure matches Pydantic model schema

**Key Checks**:
- JSON serialization/deserialization roundtrip (validates all fields preserve values)
- All required fields present and correctly typed
- No unexpected extra fields in JSON
- UUID, datetime, and enum fields deserialize correctly

**Validation Functions**:
- `SnapshotValidator.validate_layer_1_schema()` → `ValidationResult`
- `RepoStateSnapshot.model_validate_json()` (Pydantic validation)

**Expected Duration**: ~40-80ms
**Failure Category**: STRUCTURAL (non-retryable)
**Use Case**: Pre-flight validation for malformed snapshots

**Integration Test Coverage**:
- `tests/integration/observer/test_snapshot_validation.py::TestSnapshotSchemaValidation` (4 tests)
- Minimal snapshot schema validation
- Roundtrip serialization integrity
- All required fields presence verification
- Error snapshot handling

### Layer 2: Completeness Validation (Fast Path - ~20ms)

**Purpose**: Verify snapshot contains required signals and acceptable error thresholds

**Key Checks**:
- Required signals present (test_signal, dependency_drift, lint_signal)
- At least 3 non-unavailable signals (test, dependency, lint, type, todo, coverage)
- Acceptable collector error count (≤5 errors allowed)
- Signal status fields properly populated

**Validation Functions**:
- `SnapshotValidator.validate_layer_2_completeness()` → `ValidationResult`
- Signal presence checks: `signals.test_signal is not None`
- Signal status enumeration: "passing", "unavailable", "failing"
- Collector error counting: `len(snapshot.collector_errors) <= 5`

**Expected Duration**: ~15-30ms
**Failure Category**: STRUCTURAL or TRANSIENT (>5 errors are retryable)
**Use Case**: Detect missing critical signal collectors, excessive collection failures

**Integration Test Coverage**:
- `TestSnapshotCompletenessValidation` (8 tests)
- Minimal snapshot completeness
- Three-signal minimum requirement
- Limited signal detection
- Unavailable signal handling
- Collector error tolerance

### Layer 3: Consistency Validation (Moderate Speed - ~50ms)

**Purpose**: Cross-signal semantic checks ensuring signal values align with status

**Key Checks**:
- Test signal consistency: If status="passing", then test_count > 0
- Lint signal consistency: If violation_count > 100, status must be "violations"/"failing"
- Dependency consistency: If status="healthy", then critical_issues == 0
- Coverage consistency: If total_coverage_pct > 0, coverage data must be present

**Validation Functions**:
- `SnapshotValidator.validate_layer_3_consistency()` → `ValidationResult`
- Field extraction: `getattr(signal, 'field_name', None)`
- Status-value correlation: comparing enum values with numeric metrics
- Cross-signal validation without external system calls

**Expected Duration**: ~40-70ms
**Failure Category**: STRUCTURAL (semantic contradiction indicates snapshot error)
**Use Case**: Detect invalid snapshot data created by faulty collection

**Integration Test Coverage**:
- `TestSnapshotConsistencyValidation` (7 tests)
- Test signal count consistency
- Lint violation status consistency
- Dependency health consistency
- Coverage presence consistency
- Invalid snapshot detection

### Layer 4: Real-World Accuracy Validation (Slow - 5-30s)

**Purpose**: Compare snapshot values against actual live tools, with configurable tolerance

**Key Checks**:
- Test count accuracy: `actual_count ≈ snapshot_count` (within tolerance)
- Coverage accuracy: `actual_coverage ≈ snapshot_coverage` (within tolerance)
- Lint violation count: Compares `ruff check` output count
- External tool execution: pytest, ruff, coverage.py

**Validation Functions**:
- `SnapshotValidator.validate_layer_4_accuracy(tolerance)` → `ValidationResult`
  - `_get_actual_test_count()` → runs pytest --collect-only
  - `_get_actual_coverage()` → runs coverage report
  - `_get_actual_lint_violations()` → runs ruff check
  - Relative error calculation: `abs(actual - expected) / expected`
  - Tolerance comparison: `relative_error <= tolerance`

**Expected Duration**: 5-30s (depends on repo size and tool complexity)
**Failure Category**: TRANSIENT (actual system state may differ from snapshot time)
**Use Case**: Validate snapshots reflect real system state within margin of error
**Configuration**:
- Default tolerance: test_count=1%, coverage=5%
- Customizable per-metric tolerance
- Timeout: 60s default (configurable)

**Integration Test Coverage**:
- `TestSnapshotAccuracyValidation` (6 tests)
- Test count accuracy within tolerance
- Coverage accuracy verification
- Lint violation count matching
- Timeout handling
- Tool execution failure handling

### Layer 5: Regression Detection (Slow - 5-30s)

**Purpose**: Compare current snapshot against baseline to detect unwanted changes

**Key Checks**:
- Coverage regression: `current_coverage < baseline_coverage - threshold` → error
  - Default threshold: -2.0% (allow 2% degradation)
- Test count regression: `current_count < baseline_count - threshold` → error
  - Default threshold: -5 tests
- Lint violation regression: `current_violations > baseline_violations + threshold` → error
  - Default threshold: +10 violations
- Performance regression: If baseline includes timing, flag >20% slowdown

**Validation Functions**:
- `SnapshotValidator.validate_layer_5_regression(baseline)` → `ValidationResult`
- Baseline snapshot loading: `RepoStateSnapshot` comparison
- Metric extraction: `getattr(snapshot.signals.SIGNAL, 'METRIC')`
- Regression calculation: `current_value vs baseline_value`
- Configurable thresholds per metric

**Expected Duration**: 5-30s (if baseline requires loading from remote storage)
**Failure Category**: TRANSIENT (baseline may be outdated) or STRUCTURAL (real regression)
**Use Case**: CI gate to prevent performance/quality regression
**Configuration**:
- Baseline source: file path or storage reference
- Regression thresholds: configurable per metric
- Scope: per-metric regression detection

**Integration Test Coverage**:
- `TestSnapshotRegressionValidation` (5 tests)
- Coverage regression detection
- Test count regression detection
- Lint regression detection
- Baseline loading from file
- Baseline loading from storage

---

## Validation Functions & Module Inventory

### Core Modules

#### `src/operations_center/observer/snapshot_validator.py` (21KB)

**Classes**:
- `SnapshotValidator` — Multi-layer validation orchestrator
  - `__init__(snapshot, repo_path)` — Initialize with snapshot and repo context
  - `validate_layer_1_schema()` → `ValidationResult` — Schema validation
  - `validate_layer_2_completeness()` → `ValidationResult` — Completeness validation
  - `validate_layer_3_consistency()` → `ValidationResult` — Consistency validation
  - `validate_layer_4_accuracy(tolerance)` → `ValidationResult` — Accuracy validation
  - `validate_layer_5_regression(baseline)` → `ValidationResult` — Regression detection
  - `_get_actual_test_count()` → `int | None` — Run pytest --collect-only
  - `_get_actual_coverage()` → `float | None` — Run coverage report
  - `_get_actual_lint_violations()` → `int | None` — Run ruff check

- `ValidationError` (dataclass) — Single validation error
  - `layer: int` — Layer number (1-5)
  - `category: ValidationFailureCategory` — Failure type (TRANSIENT, STRUCTURAL, CONFIGURATION, UNKNOWN)
  - `message: str` — Human-readable error message
  - `details: dict[str, Any]` — Machine-readable error context
  - `is_retryable: bool` — Can this error be retried?
  - `to_dict()` → `dict` — Serialize for reporting

- `ValidationResult` (dataclass) — Result of single validation check
  - `passed: bool` — Check passed?
  - `check_name: str` — Name of check (schema_validation, completeness_validation, etc.)
  - `message: str` — Summary message
  - `errors: list[ValidationError]` — List of errors found
  - `duration_ms: float` — Execution time in milliseconds
  - `to_dict()` → `dict` — Serialize for reporting

- `SnapshotValidationReport` (dataclass) — Complete validation report
  - `snapshot_id: str` — Snapshot run_id
  - `observed_at: datetime` — When snapshot was created
  - `layers_checked: list[int]` — Which layers were validated
  - `results: list[ValidationResult]` — Results from each layer
  - `passed: bool` — Overall pass/fail
  - `overall_duration_ms: float` — Total execution time
  - `generated_at: datetime` — When report was generated
  - `add_result(result)` → `None` — Add result, update passed status
  - `get_retryable_errors()` → `list[ValidationError]` — Filter retryable errors
  - `to_dict()` → `dict` — Serialize for reporting

- `ValidationFailureCategory` (enum) — Error categorization for retry logic
  - `TRANSIENT` — Can be retried (network timeout, temporary state difference)
  - `STRUCTURAL` — Cannot be retried (malformed snapshot, missing required signal)
  - `CONFIGURATION` — Configuration issue (wrong path, missing repo)
  - `UNKNOWN` — Unknown category (default for uncategorized errors)

**Key Properties**:
- All validation methods are independent (can be called individually)
- Results include both pass/fail and detailed error information
- Errors are categorized for intelligent retry logic
- Duration tracking enables performance optimization

#### `src/operations_center/observer/snapshot_validation_engine.py` (9KB)

**Classes**:
- `SnapshotValidationEngine` — High-level validation orchestrator
  - `__init__(loader=None)` — Initialize with optional custom loader
  - `validate(source, config, baseline_source)` → `SnapshotValidationReport` — Main validation entry point
  - `validate_with_retry(source, config, baseline_source)` → `(SnapshotValidationReport, bool)` — Validate with auto-retry on transient errors

- `ValidationConfig` (dataclass) — Validation configuration
  - `layers: list[int] | None` — Layer numbers to validate (1-5), defaults to [1, 2, 3]
  - `tolerance: dict[str, float] | None` — Tolerance per metric, defaults to test_count=0.01, coverage=0.05
  - `repo_path: Path | None` — Repository path for accuracy checks
  - `timeout: int` — Max seconds for layer 4 tools (default: 60)
  - `retry_on_transient: bool` — Auto-retry on transient errors?
  - `max_retries: int` — Max retry attempts (default: 3)
  - `get_layers()` → `list[int]` — Get layers with defaults applied
  - `get_tolerance()` → `dict[str, float]` — Get tolerance with defaults applied

- `ValidationError` (exception) — Validation orchestration error
  - `__init__(message, context, source_error)` — Initialize with context
  - `message: str` — Error message
  - `context: dict[str, Any]` — Additional context
  - `source_error: Exception | None` — Original exception
  - `to_dict()` → `dict` — Serialize for reporting

**Key Properties**:
- Single entry point for all validation workflows
- Automatic snapshot loading from multiple sources (file, storage, run_id)
- Built-in retry logic for transient failures
- Baseline loading for regression detection
- Comprehensive error context for debugging

#### `src/operations_center/observer/snapshot_loader.py` (8.7KB)

**Classes**:
- `SnapshotLoader` — Load snapshots from multiple sources
  - `load(source)` → `RepoStateSnapshot` — Load from file, storage, or run_id
  - `_load_from_file(path)` → `RepoStateSnapshot` — Load from JSON/YAML file
  - `_load_from_storage(run_id)` → `RepoStateSnapshot` — Load from artifact storage
  - `_load_from_run_id(run_id)` → `RepoStateSnapshot` — Load from CI metadata

- `SnapshotLoadError` (exception) — Snapshot loading failed
  - `source: str` — What was being loaded
  - `reason: str` — Why it failed
  - `to_dict()` → `dict` — Serialize for reporting

**Key Properties**:
- Transparent source detection (file vs. storage vs. run_id)
- Format auto-detection (JSON vs. YAML)
- Detailed error reporting with context

#### `src/operations_center/observer/cli.py` (20KB)

**Entry Points**:
- CLI app: `typer.Typer` application
- Commands: 8 main commands (validate, observe-and-validate, list, show, compare, export, import, cleanup)

**Main Command**: `cmd_validate()`
- Full CLI interface with all options (see CLI Design section)

**Helper Functions**:
- `_setup_logging(log_level, debug)` → `None` — Configure logging
- `_parse_layers(layers_str)` → `list[int]` — Parse layer specification
- `_build_tolerance_dict(global, coverage, test_count)` → `dict[str, float]` — Build tolerance config
- `_format_duration(ms)` → `str` — Format duration for display

**Key Properties**:
- Rich console output with formatting
- Multiple output formats (table, JSON, markdown, text)
- File I/O for report saving
- Exit codes for script integration

---

## CLI Command Interface Design

### Command Structure

```
operations-center-observer-snapshot [OPTIONS] COMMAND [ARGS]
```

### Primary Command: `validate`

**Purpose**: Validate a snapshot against configurable layers

**Usage**:
```bash
operations-center-observer-snapshot validate <SNAPSHOT_PATH> [OPTIONS]
```

**Arguments**:
- `SNAPSHOT_PATH` (required) — Path to snapshot JSON/YAML file OR run_id string
  - File path: `/path/to/snapshot.json` or `./snapshots/20260614-120000.yaml`
  - Run ID: `run-abc123def456` (loads from artifact storage)
  - Exit code `EXIT_FILE_MISSING` (5) if file not found

**Core Options** (Validation Control):

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--layers` | str | "1,2,3" | Comma-separated layer numbers (1,2,3,4,5) |
| `--baseline` | Path | None | Baseline snapshot for layer 5 regression detection |
| `--repo-path` | Path | `cwd()` | Repository path for layer 4 accuracy checks |
| `--tolerance` | float | 0.05 | Global tolerance as decimal (0.01=1%, 0.05=5%) |
| `--coverage-tolerance` | float | None | Coverage-specific tolerance (overrides --tolerance) |
| `--test-count-tolerance` | float | None | Test count-specific tolerance (overrides --tolerance) |
| `--timeout` | int | 60 | Max seconds for layer 4 tool execution |

**Accuracy Options** (Layer 4 Configuration):

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--skip-lint-check` | bool | False | Skip lint violation accuracy check |
| `--skip-coverage-check` | bool | False | Skip coverage accuracy check |
| `--skip-test-check` | bool | False | Skip test count accuracy check |

**Output Options**:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--format` | str | "table" | Output format: table, json, markdown, text |
| `--output, -o` | Path | None | Save validation report to JSON file |
| `--verbose, -v` | bool | False | Show detailed error information and context |
| `--quiet, -q` | bool | False | Minimal output (only pass/fail) |

**Retry Options**:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--retry-transient` | bool | False | Auto-retry on transient errors (network, timeout) |
| `--max-retries` | int | 3 | Max retry attempts for transient failures |
| `--retry-delay` | int | 1 | Seconds to wait between retries |

**Global Options** (via callback):

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--log-level` | str | "info" | Logging level: debug, info, warning, error |
| `--debug` | bool | False | Enable debug mode (implies --log-level debug) |

### Exit Codes

| Code | Name | Meaning | Retry Recommended |
|------|------|---------|------------------|
| 0 | `EXIT_SUCCESS` | Validation passed all layers | N/A |
| 1 | `EXIT_VALIDATION_FAILED` | Validation failed one or more layers | Yes, if errors are transient |
| 2 | `EXIT_NOT_FOUND` | Snapshot not found in storage | No |
| 3 | `EXIT_LOAD_ERROR` | Failed to load snapshot (parse error, I/O error) | Depends on error type |
| 4 | `EXIT_CONFIG_ERROR` | Configuration error (invalid layers, bad path) | No |
| 5 | `EXIT_FILE_MISSING` | Snapshot file path doesn't exist | No |

### Typical Usage Workflows

#### Workflow 1: Quick Validation (Default 3 layers)
```bash
# Validate snapshot using fast path (layers 1-3)
operations-center-observer-snapshot validate snapshot.json

# With auto-retry on transient failures
operations-center-observer-snapshot validate snapshot.json --retry-transient
```

#### Workflow 2: Full Validation with Accuracy Check
```bash
# Validate all 5 layers including accuracy
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --repo-path /path/to/repo \
  --timeout 120

# With custom tolerance for test count (allow 5% variation)
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --test-count-tolerance 0.05
```

#### Workflow 3: Regression Detection
```bash
# Validate current snapshot against baseline
operations-center-observer-snapshot validate current.json \
  --layers 5 \
  --baseline baseline.json

# Include accuracy checks for complete validation
operations-center-observer-snapshot validate current.json \
  --layers 1,2,3,4,5 \
  --baseline baseline.json
```

#### Workflow 4: CI Integration
```bash
# Minimal output for CI logs
operations-center-observer-snapshot validate snapshot.json --quiet

# Save detailed report for analysis
operations-center-observer-snapshot validate snapshot.json \
  --output report.json \
  --format json

# Exit code determines CI pass/fail
operations-center-observer-snapshot validate snapshot.json
if [ $? -eq 0 ]; then echo "PASS"; else echo "FAIL"; fi
```

#### Workflow 5: Debugging Failed Validation
```bash
# Get verbose output with full error details
operations-center-observer-snapshot validate snapshot.json \
  --verbose \
  --log-level debug

# Save report for inspection
operations-center-observer-snapshot validate snapshot.json \
  --output report.json \
  --format json \
  --verbose
```

### Secondary Commands (Planned Future)

These commands are designed but implementation is a follow-up:

#### `observe-and-validate`
Combine snapshot observation with validation in one command
```bash
operations-center-observer-snapshot observe-and-validate \
  --repo-path /path/to/repo \
  --output-dir snapshots/ \
  --layers 1,2,3,4,5
```

#### `list`
List available snapshots in storage
```bash
operations-center-observer-snapshot list [--filter recent|all] [--format table|json]
```

#### `compare`
Compare two snapshots
```bash
operations-center-observer-snapshot compare snapshot1.json snapshot2.json \
  --metrics test_count,coverage,violations
```

---

## Input/Output Formats

### Input: Snapshot Files

**Format 1: JSON**
```json
{
  "run_id": "run-20260614-120000",
  "observed_at": "2026-06-14T12:00:00Z",
  "observer_version": "1.0.0",
  "source_command": "observe-repo",
  "repo": {
    "url": "https://github.com/example/repo",
    "branch": "main",
    "commit_sha": "abc123def456"
  },
  "signals": {
    "test_signal": {
      "status": "passing",
      "test_count": 8500,
      "failing_count": 0
    },
    "lint_signal": {
      "status": "passing",
      "violation_count": 0
    },
    "coverage_signal": {
      "total_coverage_pct": 86.5
    }
  },
  "collector_errors": []
}
```

**Format 2: YAML**
```yaml
run_id: run-20260614-120000
observed_at: 2026-06-14T12:00:00Z
observer_version: 1.0.0
source_command: observe-repo
repo:
  url: https://github.com/example/repo
  branch: main
  commit_sha: abc123def456
signals:
  test_signal:
    status: passing
    test_count: 8500
    failing_count: 0
  lint_signal:
    status: passing
    violation_count: 0
  coverage_signal:
    total_coverage_pct: 86.5
collector_errors: []
```

**File Extension Detection**:
- `.json`, `.jsonl` → parsed as JSON
- `.yaml`, `.yml` → parsed as YAML
- No extension: attempt both (JSON first, fallback to YAML)

### Output: Validation Report (Multiple Formats)

#### Format 1: Table (Default, Human-Readable)
```
╭────────────────────────────────────────────────────────────────────╮
│ Snapshot Validation Report                                         │
├────────────────────────────────────────────────────────────────────┤
│ Snapshot ID: run-20260614-120000                                   │
│ Created: 2026-06-14T12:00:00Z                                      │
│ Report Generated: 2026-06-14T12:00:05Z                             │
│ Overall Duration: 1.234s                                           │
├────────────────────────────────────────────────────────────────────┤
│ LAYERS VALIDATED                                                   │
├─────┬──────────────┬───────┬──────────────────────────────────┬────┤
│ No. │ Layer        │ Pass  │ Details                          │ ms │
├─────┼──────────────┼───────┼──────────────────────────────────┼────┤
│ 1   │ Schema       │ ✓     │ Roundtrip serialization OK       │ 45 │
│ 2   │ Completeness │ ✓     │ All required signals present     │ 23 │
│ 3   │ Consistency  │ ✓     │ Cross-signal validation OK       │ 58 │
│ 4   │ Accuracy     │ ✗     │ Test count drift 8,500 vs 8,520  │ 1200 │
│ 5   │ Regression   │ ─     │ Not validated                    │ 0  │
├─────┴──────────────┴───────┴──────────────────────────────────┴────┤
│ OVERALL RESULT: FAILED ✗                                          │
├────────────────────────────────────────────────────────────────────┤
│ ERRORS & DETAILS (4)                                              │
├────────────────────────────────────────────────────────────────────┤
│ Layer 4 | TRANSIENT | Test count mismatch: 8520 vs 8500           │
│         │            | (0.2% > 1% tolerance)                      │
│         │            | Retryable: true                            │
├────────────────────────────────────────────────────────────────────┤
│ RECOMMENDATIONS                                                    │
├────────────────────────────────────────────────────────────────────┤
│ • Transient errors detected: consider retry with --retry-transient │
│ • Or increase tolerance: --test-count-tolerance 0.01              │
├────────────────────────────────────────────────────────────────────┤
│ Exit code: 1 (validation failed)                                   │
╰────────────────────────────────────────────────────────────────────╯
```

#### Format 2: JSON (Machine-Readable)
```json
{
  "snapshot_id": "run-20260614-120000",
  "observed_at": "2026-06-14T12:00:00Z",
  "layers_checked": [1, 2, 3, 4],
  "passed": false,
  "overall_duration_ms": 1234,
  "generated_at": "2026-06-14T12:00:05Z",
  "results": [
    {
      "passed": true,
      "check_name": "schema_validation",
      "message": "Schema validation passed",
      "errors": [],
      "duration_ms": 45
    },
    {
      "passed": false,
      "check_name": "accuracy_validation",
      "message": "Accuracy validation failed",
      "errors": [
        {
          "layer": 4,
          "category": "transient",
          "message": "Test count mismatch: 8520 vs 8500 (0.2% > 1%)",
          "details": {
            "expected": 8500,
            "actual": 8520,
            "relative_error": 0.002,
            "tolerance": 0.01
          },
          "is_retryable": true
        }
      ],
      "duration_ms": 1200
    }
  ],
  "retryable_errors": 1,
  "non_retryable_errors": 0
}
```

#### Format 3: Markdown (CI Logs)
```markdown
# Snapshot Validation Report

**Snapshot ID**: run-20260614-120000  
**Created**: 2026-06-14T12:00:00Z  
**Report Generated**: 2026-06-14T12:00:05Z  
**Overall Duration**: 1.234s  

## Validation Results

| Layer | Name | Result | Duration | Notes |
|-------|------|--------|----------|-------|
| 1 | Schema | ✅ PASS | 45ms | Roundtrip serialization OK |
| 2 | Completeness | ✅ PASS | 23ms | All required signals present |
| 3 | Consistency | ✅ PASS | 58ms | Cross-signal validation OK |
| 4 | Accuracy | ❌ FAIL | 1200ms | Test count drift: 8,520 vs 8,500 |
| 5 | Regression | ⏭ SKIP | 0ms | Not requested |

## Overall Result: ❌ FAILED

### Errors (1)

**Layer 4 - Accuracy Validation**
- **Category**: Transient (retryable)
- **Message**: Test count mismatch: 8,520 vs 8,500 (0.2% > 1% tolerance)
- **Expected**: 8,500 tests
- **Actual**: 8,520 tests
- **Tolerance**: 1%
- **Retryable**: Yes

## Recommendations

- Transient errors detected: consider retry with `--retry-transient`
- Or increase tolerance: `--test-count-tolerance 0.01`

**Exit Code**: 1 (validation failed)
```

#### Format 4: Text (Simple, CI-Friendly)
```
SNAPSHOT VALIDATION REPORT
==========================

Snapshot ID: run-20260614-120000
Created: 2026-06-14T12:00:00Z
Duration: 1.234s

LAYER RESULTS
=============
Layer 1 (Schema):      PASS (45ms)
Layer 2 (Completeness): PASS (23ms)
Layer 3 (Consistency):  PASS (58ms)
Layer 4 (Accuracy):     FAIL (1200ms)
Layer 5 (Regression):   SKIP

OVERALL: FAILED

ERRORS
======
Layer 4 | TRANSIENT
  Message: Test count mismatch: 8520 vs 8500 (0.2% > 1% tolerance)
  Retryable: Yes

Exit Code: 1
```

### Output: File Saving

When `--output <PATH>` is specified:
- Always saves in JSON format (for programmatic access)
- Creates parent directories if they don't exist
- Prints confirmation: `✓ Report saved to <PATH>`

---

## Error Handling & Exit Codes

### Exit Code Reference

| Code | Name | Scenario | Retry? | User Action |
|------|------|----------|--------|-------------|
| 0 | SUCCESS | All validation passed | N/A | Done |
| 1 | VALIDATION_FAILED | Snapshot failed one or more layers | Maybe | Review errors, increase tolerance, or retry if transient |
| 2 | NOT_FOUND | Snapshot source not found | No | Check snapshot path or run_id |
| 3 | LOAD_ERROR | Failed to load/parse snapshot | Depends | Check snapshot format (JSON/YAML), file permissions |
| 4 | CONFIG_ERROR | Invalid configuration | No | Review CLI options (--layers, --tolerance, paths) |
| 5 | FILE_MISSING | Snapshot file path doesn't exist | No | Check file path exists and is readable |

### Error Categorization Logic

**Transient Errors** (retryable):
- Network timeouts during layer 4
- Temporary state differences (test count, coverage)
- Tool execution timeouts
- Temporary file system errors

**Structural Errors** (non-retryable):
- Missing required signals
- Schema mismatch
- Semantic inconsistency (status != data)
- Malformed JSON/YAML

**Configuration Errors** (non-retryable):
- Invalid layer numbers
- Invalid path (doesn't exist)
- Invalid tolerance specification
- Missing required baseline

**Retry Logic**:
- Transient errors are automatically retried if `--retry-transient` flag is set
- Max retries: configurable via `--max-retries` (default: 3)
- Retry delay: configurable via `--retry-delay` (default: 1s)
- Exponential backoff: `delay = base_delay * (2 ^ attempt)`

### Verbose Error Output

When `--verbose` flag is set:
```
Layer 4 | TRANSIENT | Test count mismatch
  Message: Test count mismatch: 8520 vs 8500 (0.2% > 1% tolerance)
  Details:
    expected: 8500
    actual: 8520
    relative_error: 0.002
    tolerance: 0.01
  Retryable: true
  Location: Line 364 in snapshot_validator.py
  Tool: pytest --collect-only (execution time: 1.2s)
```

---

## Performance Targets

### Latency Requirements

| Layer | Category | Target | Acceptable Range | Notes |
|-------|----------|--------|------------------|-------|
| 1 | Schema | 50ms | 40-80ms | Pure Python validation, no I/O |
| 2 | Completeness | 25ms | 15-40ms | Signal enumeration and counting |
| 3 | Consistency | 60ms | 40-100ms | Cross-signal checks |
| 1-3 | Fast Path | 135ms | 100-200ms | Default validation scope |
| 4 | Accuracy (quick) | 2s | 1-5s | pytest --collect-only |
| 4 | Accuracy (full) | 15s | 10-30s | Depends on repo size |
| 5 | Regression | 5s | 3-10s | Depends on baseline loading |
| 1-5 | Full Validation | 20s | 15-40s | All layers on typical repo |

### Throughput Targets

- Single validation: ~1.2s (layers 1-3)
- Batch validation: 50+ snapshots/minute (one at a time)
- Parallel validation: Not supported (single-threaded by design)

### Resource Usage Targets

| Resource | Target | Notes |
|----------|--------|-------|
| Memory | <100MB | No caching of snapshots |
| CPU | <20% (single core) | Lightweight for layers 1-3 |
| Disk | <50MB (working dir) | Temporary test execution |
| Network | <1MB | Baseline loading from storage |

### Specific Layer Performance Targets

#### Layer 1 (Schema Validation)
- Target: <50ms
- No external processes
- Pure Python JSON parsing and validation
- Measurement: wall-clock time of `RepoStateSnapshot.model_validate_json()`

#### Layer 2 (Completeness)
- Target: <25ms
- No external processes
- Simple enumeration and counting
- Measurement: wall-clock time of signal presence checks

#### Layer 3 (Consistency)
- Target: <60ms
- No external processes
- Cross-signal checks with conditional logic
- Measurement: wall-clock time of consistency validations

#### Layer 4 (Accuracy)
- Target: 2-15s (depending on checks enabled)
- External tool execution (pytest, coverage, ruff)
- Configurable timeout: 60s (default)
- Measurement: wall-clock time of tool execution + analysis
- Optimizations:
  - `--skip-test-check` → skip pytest (saves ~2s)
  - `--skip-coverage-check` → skip coverage (saves ~1s)
  - `--skip-lint-check` → skip ruff (saves ~0.5s)

#### Layer 5 (Regression)
- Target: 5-10s
- Depends on baseline loading method (file vs. storage)
- File loading: <1s
- Storage loading: 3-10s (network dependent)
- Measurement: total time including baseline load + comparison

### Performance Optimization Recommendations

**For Fast CI Feedback**:
```bash
# Use default layers 1-3 only (skip layer 4 accuracy)
operations-center-observer-snapshot validate snapshot.json
# Expected: ~135ms
```

**For Comprehensive Validation**:
```bash
# All layers but skip optional accuracy checks
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --skip-lint-check
# Expected: ~5-10s
```

**For Fast Layer 4 Execution**:
```bash
# Run pytest --collect-only directly (faster than full test suite)
pytest --collect-only <repo> | grep "test session starts" | grep "tests" | awk '{print $1}'
# This is what layer 4 uses internally: ~2s for typical repo
```

---

## User Experience Requirements

### Design Principles

1. **Clarity**: Error messages explain what failed and why
2. **Actionability**: Each error includes next steps for fixing
3. **Progressivity**: Fast path (layers 1-3) works by default, advanced options available
4. **Integration**: Exit codes enable straightforward CI/script integration
5. **Transparency**: Detailed output available with `--verbose` when needed
6. **Flexibility**: Tolerances and layer selection accommodate various workflows

### User Personas & Workflows

#### Persona 1: Local Developer (Quick Feedback)
**Goal**: Validate snapshot before committing
**Workflow**:
```bash
# Run quick validation (layers 1-3, ~135ms)
operations-center-observer-snapshot validate snapshot.json

# If OK, commit
git add snapshot.json
git commit -m "Update snapshot"
```
**UX Requirements**:
- Fast execution (<1s)
- Clear pass/fail output
- No long waiting

**Output**: Table format (default)

#### Persona 2: CI Operator (Full Validation with Reporting)
**Goal**: Validate snapshot in CI pipeline, save report for analysis
**Workflow**:
```bash
# Full validation with all layers and detailed report
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --repo-path /workspace \
  --output validation-report.json

# Exit code determines job status
```
**UX Requirements**:
- Consistent exit codes
- Machine-readable output (JSON)
- Detailed error context
- Report saving capability

**Output**: JSON format (machine-readable)

#### Persona 3: QA Engineer (Regression Detection)
**Goal**: Detect performance/quality regression from baseline
**Workflow**:
```bash
# Compare current snapshot against baseline
operations-center-observer-snapshot validate current.json \
  --layers 5 \
  --baseline baseline.json \
  --output regression-report.json

# Review report for changes
```
**UX Requirements**:
- Clear regression reporting
- Baseline comparison
- Metric-level details
- Actionable recommendations

**Output**: JSON format + table summary

#### Persona 4: Debugging Test Failures
**Goal**: Understand why validation failed
**Workflow**:
```bash
# Get verbose output with full details
operations-center-observer-snapshot validate snapshot.json \
  --verbose \
  --log-level debug

# Read detailed error messages
```
**UX Requirements**:
- Full error context
- Location information (file:line)
- Tool output (pytest, ruff, etc.)
- Recommendations for fixing

**Output**: Text format with verbose details

### Error Message Guidelines

**Good Error Message** (actionable, clear):
```
Layer 4 | TRANSIENT | Test count mismatch
  Snapshot reports 8,500 tests, actual count is 8,520 (0.2% drift)
  This is within your configured tolerance of 1%.
  Retryable: Yes
  
Recommendation: If this error persists, increase tolerance with:
  --test-count-tolerance 0.01
```

**Poor Error Message** (confusing, not actionable):
```
Accuracy validation failed
```

### Output Format Selection Logic

| Scenario | Recommended Format | Rationale |
|----------|-------------------|-----------|
| CLI (terminal) | table | Human-readable, visual status |
| CI logs | markdown | Compact, readable in logs |
| Programmatic parsing | json | Machine-readable, structured |
| Debugging | text | Simple, easy to search |

### Help System

```bash
# Show help for main command
operations-center-observer-snapshot --help

# Show help for validate subcommand
operations-center-observer-snapshot validate --help

# Show version
operations-center-observer-snapshot --version
```

Expected help output:
```
Usage: operations-center-observer-snapshot validate [OPTIONS] SNAPSHOT_PATH

Validate snapshot against configured layers.

ARGUMENTS:
  SNAPSHOT_PATH    Path to snapshot JSON/YAML or run_id

OPTIONS:
  --layers TEXT                          Comma-separated layer numbers [default: 1,2,3]
  --baseline PATH                        Baseline for layer 5 regression
  --repo-path PATH                       Repository path [default: current dir]
  --tolerance FLOAT                      Global tolerance [default: 0.05]
  ...
  
EXAMPLES:
  # Quick validation (layers 1-3)
  operations-center-observer-snapshot validate snapshot.json
  
  # Full validation with regression check
  operations-center-observer-snapshot validate snapshot.json \
    --layers 1,2,3,4,5 --baseline baseline.json
    
  # With detailed output and reporting
  operations-center-observer-snapshot validate snapshot.json \
    --verbose --output report.json --format json
```

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│ CLI Layer (snapshot_validator.py)                              │
│ • Command parsing (typer)                                       │
│ • Option handling                                               │
│ • Output formatting                                             │
│ • Exit code assignment                                          │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Engine Layer (snapshot_validation_engine.py)                   │
│ • Validation orchestration                                      │
│ • Configuration management                                      │
│ • Retry logic                                                   │
│ • Baseline loading                                              │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Validation Layer (snapshot_validator.py)                       │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Layer 1: Schema Validation                                 │ │
│ │ • JSON/YAML parsing                                        │ │
│ │ • Pydantic model validation                                │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Layer 2: Completeness Validation                           │ │
│ │ • Signal presence checks                                   │ │
│ │ • Collector error counting                                 │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Layer 3: Consistency Validation                            │ │
│ │ • Status-value correlation                                 │ │
│ │ • Cross-signal semantic checks                             │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Layer 4: Accuracy Validation                               │ │
│ │ • External tool execution (pytest, coverage, ruff)         │ │
│ │ • Tolerance-based comparison                               │ │
│ │ • Timeout handling                                         │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Layer 5: Regression Detection                              │ │
│ │ • Baseline comparison                                      │ │
│ │ • Metric-level regression detection                        │ │
│ │ • Threshold-based alerting                                 │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Utility Layers                                                  │
│ • Snapshot Loader (snapshot_loader.py)                          │
│ • Output Formatter (snapshot_output_formatter.py)               │
│ • Models (models.py)                                            │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Input**: Snapshot path or run_id → CLI argument
2. **Loading**: Snapshot loaded by SnapshotLoader
3. **Configuration**: ValidationConfig created from CLI options
4. **Validation**: SnapshotValidator executes layers sequentially
5. **Results**: ValidationResult and SnapshotValidationReport generated
6. **Formatting**: SnapshotOutputFormatter converts report to specified format
7. **Output**: Display on console, save to file, return exit code

### Concurrency Model

- Single-threaded design
- No multi-process validation
- Sequential layer execution
- Safe for CI/CD pipeline usage

### Error Propagation

```
CLI Input Error
    ↓
Config Error (EXIT_CONFIG_ERROR)

Snapshot Not Found
    ↓
Not Found Error (EXIT_NOT_FOUND)

Snapshot Load Error
    ↓
Load Error (EXIT_LOAD_ERROR)

Validation Error
    ↓
Pass/Fail Result → Retryable? → Auto-retry or EXIT_VALIDATION_FAILED
```

---

## Specification Compliance Checklist

### Acceptance Criteria for Stage 0

- [x] **Analyzed 5-layer validation pipeline**
  - Layer 1: Schema validation (40-80ms)
  - Layer 2: Completeness validation (15-40ms)
  - Layer 3: Consistency validation (40-100ms)
  - Layer 4: Accuracy validation (1-30s)
  - Layer 5: Regression detection (3-10s)

- [x] **Identified all validation functions and modules**
  - snapshot_validator.py: Core validation logic (21KB)
  - snapshot_validation_engine.py: Orchestration (9KB)
  - snapshot_loader.py: Loading from multiple sources (8.7KB)
  - cli.py: Command-line interface (20KB)

- [x] **Designed CLI command interface**
  - 8 main commands documented
  - Detailed argument specifications
  - 20+ options for controlling validation
  - Exit codes defined and explained

- [x] **Created detailed specification document**
  - This document (8 sections, comprehensive)
  - All modules documented with class/function signatures
  - All options documented with examples

- [x] **Defined performance targets**
  - Layer latency targets: 50ms to 30s per layer
  - Throughput: 50+ snapshots/minute
  - Resource usage targets: <100MB memory
  - Optimization recommendations

- [x] **Defined UX requirements**
  - 4 user personas with workflows
  - Error message guidelines
  - Output format selection logic
  - Help system design

---

## Next Steps (Not in Scope for Stage 0)

1. **Stage 1: CLI Implementation & Testing**
   - Implement remaining commands (observe-and-validate, list, compare, etc.)
   - Write comprehensive CLI tests
   - Implement output formatting for all formats

2. **Stage 2: Integration with CI/CD**
   - Integrate CLI into GitHub Actions
   - Create workflow templates for snapshot validation
   - Document CI/CD integration patterns

3. **Stage 3: Advanced Features**
   - Parallel snapshot validation
   - Batch validation support
   - Real-time validation dashboard
   - Performance optimization

4. **Stage 4: Documentation & User Guides**
   - User guide for snapshot validation
   - Troubleshooting guide
   - Integration guide for CI/CD systems
   - Advanced usage patterns

---

## Document History

| Date | Version | Changes |
|------|---------|---------|
| 2026-06-14 | 1.0 | Initial specification document created |

---

**Document Owner**: Operations Center Observer Team  
**Last Updated**: 2026-06-14  
**Specification Status**: Complete for Stage 0
