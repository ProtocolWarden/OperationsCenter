---
status: complete
title: Observer Snapshot Validation CLI — User Guide
description: Comprehensive user guide for snapshot validation CLI with commands, workflows, and troubleshooting
version: "1.0"
date: 2026-06-14
---

# Observer Snapshot Validation CLI — User Guide

**Document Status**: Complete user guide for snapshot validation CLI  
**Version**: 1.0  
**Date**: 2026-06-14

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation](#installation)
3. [Command Reference](#command-reference)
4. [Validation Workflows](#validation-workflows)
5. [Configuration](#configuration)
6. [Output Formats](#output-formats)
7. [Troubleshooting Guide](#troubleshooting-guide)
8. [CI/CD Integration](#cicd-integration)
9. [Examples](#examples)
10. [Help & Man Page](#help--man-page)

---

## Quick Start

### Basic Validation

Validate a snapshot file against the default validation layers (1, 2, 3):

```bash
operations-center-observer-snapshot validate /path/to/snapshot.json
```

### Full Validation (All Layers)

Include accuracy and regression validation:

```bash
operations-center-observer-snapshot validate /path/to/snapshot.json --layers 1,2,3,4,5
```

### Save Report

Save validation report to file:

```bash
operations-center-observer-snapshot validate snapshot.json --output report.json
```

### Check CLI Version

```bash
operations-center-observer-snapshot --version
# Output: operations-center-observer-snapshot 0.1.0
```

---

## Installation

The CLI is installed as an entry point via the operations-center package:

```bash
pip install -e .  # from the OperationsCenter repo root
```

This registers the `operations-center-observer-snapshot` command globally.

**Requirements**:
- Python 3.10+
- Typer (CLI framework)
- Pydantic (validation)
- PyYAML (snapshot format support)
- pytest (for accuracy validation)
- ruff (for lint validation)
- coverage.py (for coverage validation)

---

## Command Reference

### Global Options

Available for all commands:

```bash
--version           Show version and exit
--log-level LEVEL   Logging level: debug|info|warning|error
--debug             Enable debug mode (implies --log-level debug)
--help              Show help for command
```

**Environment Variable**: `OC_SNAPSHOT_LOG_LEVEL=debug`

---

### `validate` — Main Command

Validate a snapshot against configurable validation layers.

#### Syntax

```bash
operations-center-observer-snapshot validate SNAPSHOT_PATH [OPTIONS]
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `SNAPSHOT_PATH` | string | Path to snapshot JSON/YAML file or run_id for storage lookup |

#### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--layers` | — | string | `1,2,3` | Comma-separated layer numbers (1-5), e.g., `1,2,3,4,5` |
| `--baseline` | — | path | none | Path to baseline snapshot for layer 5 (regression detection) |
| `--repo-path` | — | path | cwd | Repository path for accuracy checks (layer 4) |
| `--tolerance` | — | float | `0.05` | Global tolerance as decimal (5% = 0.05) |
| `--coverage-tolerance` | — | float | — | Coverage-specific tolerance (overrides `--tolerance`) |
| `--test-count-tolerance` | — | float | — | Test count-specific tolerance (overrides `--tolerance`) |
| `--timeout` | — | int | `60` | Max seconds for layer 4 execution |
| `--format` | — | string | `table` | Output format: `table`, `json`, `markdown`, `text` |
| `--output` | `-o` | path | — | Save validation report to file |
| `--verbose` | `-v` | bool | false | Show detailed error information |
| `--quiet` | `-q` | bool | false | Minimal output |
| `--retry-transient` | — | bool | false | Auto-retry on transient errors |
| `--max-retries` | — | int | `3` | Max retry attempts for transient errors |

#### Environment Variables

Configuration via environment variables (format: `OC_SNAPSHOT_<SETTING>`):

| Env Variable | Type | Example |
|--------------|------|---------|
| `OC_SNAPSHOT_LAYERS` | string | `1,2,3,4,5` |
| `OC_SNAPSHOT_BASELINE` | path | `/path/to/baseline.json` |
| `OC_SNAPSHOT_REPO_PATH` | path | `/home/user/myrepo` |
| `OC_SNAPSHOT_TOLERANCE` | float | `0.05` |
| `OC_SNAPSHOT_COVERAGE_TOLERANCE` | float | `0.10` |
| `OC_SNAPSHOT_TEST_COUNT_TOLERANCE` | float | `0.02` |
| `OC_SNAPSHOT_TIMEOUT` | int | `120` |
| `OC_SNAPSHOT_LOG_LEVEL` | string | `debug` |

#### Exit Codes

| Code | Meaning | Description |
|------|---------|-------------|
| `0` | SUCCESS | All validation layers passed |
| `1` | VALIDATION_FAILED | One or more validation layers failed |
| `2` | NOT_FOUND | Snapshot file not found |
| `3` | LOAD_ERROR | Failed to load/parse snapshot |
| `4` | CONFIG_ERROR | Configuration error or invalid argument |
| `5` | FILE_MISSING | Required file missing |

---

### `list` — List Stored Snapshots

List snapshots stored in the default or custom storage directory.

#### Syntax

```bash
operations-center-observer-snapshot list [OPTIONS]
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--limit` | int | `10` | Maximum snapshots to list |
| `--order` | string | `recent` | Sort order: `recent`, `oldest`, `name` |
| `--filter` | string | — | Filter by: `valid`, `invalid` |
| `--format` | string | `table` | Output format: `table`, `json`, `csv` |
| `--backend` | string | `local` | Storage backend: `local`, `s3`, `http` |
| `--storage-root` | path | `tools/report/operations_center/observer` | Storage root directory |
| `--verbose` | `-v` | bool | false | Include file size, checksum, validation status |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Example

```bash
# List 20 most recent snapshots
operations-center-observer-snapshot list --limit 20 --order recent

# List and show as JSON
operations-center-observer-snapshot list --format json

# List with file sizes and checksums
operations-center-observer-snapshot list --verbose
```

---

### `show` — Display Snapshot Contents

Display the contents of a snapshot file.

#### Syntax

```bash
operations-center-observer-snapshot show SNAPSHOT_PATH [OPTIONS]
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `SNAPSHOT_PATH` | string | Path to snapshot or run_id |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--field` | string | — | Show specific field (e.g., `signals.test_signal`) |
| `--format` | string | `json` | Output format: `json`, `yaml`, `markdown` |
| `--pretty` | bool | false | Color-coded pretty print |
| `--backend` | string | `local` | Storage backend |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Example

```bash
# Show full snapshot
operations-center-observer-snapshot show snapshot.json

# Show specific field
operations-center-observer-snapshot show snapshot.json --field signals.test_signal

# Pretty-print as YAML
operations-center-observer-snapshot show snapshot.json --format yaml --pretty
```

---

### `compare` — Compare Two Snapshots

Compare two snapshot files to detect differences.

#### Syntax

```bash
operations-center-observer-snapshot compare SNAPSHOT1 SNAPSHOT2 [OPTIONS]
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `SNAPSHOT1` | string | First snapshot path/ID |
| `SNAPSHOT2` | string | Second snapshot path/ID |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--format` | string | `diff` | Output format: `diff`, `json`, `table` |
| `--signals` | string | — | Compare specific signals (comma-separated) |
| `--stats` | bool | false | Show change statistics |
| `--output` | path | — | Save comparison to file |
| `--backend` | string | `local` | Storage backend |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Status

**Note**: `compare` command is not yet implemented. Use `show` command to view snapshots for manual comparison.

---

### `export` — Export Snapshot to File

Export a snapshot to JSON, YAML, or JSONL format.

#### Syntax

```bash
operations-center-observer-snapshot export SNAPSHOT_ID OUTPUT_PATH [OPTIONS]
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `SNAPSHOT_ID` | string | run_id or path of snapshot to export |
| `OUTPUT_PATH` | path | Output file path |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--format` | string | auto-detect | Format: `json`, `yaml`, `jsonl` (auto-detected from extension if not set) |
| `--backend` | string | `local` | Storage backend |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Example

```bash
# Export to JSON (auto-detected from extension)
operations-center-observer-snapshot export snapshot-id export.json

# Export to YAML
operations-center-observer-snapshot export snapshot-id export.yaml --format yaml

# Export to JSONL
operations-center-observer-snapshot export snapshot-id export.jsonl
```

---

### `import` — Import Snapshot from File

Import a snapshot from a file into storage.

#### Syntax

```bash
operations-center-observer-snapshot import INPUT_PATH [OPTIONS]
```

#### Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `INPUT_PATH` | path | Input file path (JSON/YAML/JSONL) |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--format` | string | auto-detect | Format: `json`, `yaml` (auto-detected if not set) |
| `--backend` | string | `local` | Storage backend |
| `--output-dir` | path | `tools/report/operations_center/observer` | Where to store (local backend) |
| `--validate-after` | bool | true | Run validation after import |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Status

**Note**: `import` command is not yet implemented.

---

### `cleanup` — Remove Old Snapshots

Remove snapshots based on age and retention policy.

#### Syntax

```bash
operations-center-observer-snapshot cleanup [OPTIONS]
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--days` | int | `30` | Delete snapshots older than N days |
| `--keep-count` | int | `50` | Keep at least N most recent snapshots |
| `--dry-run` | bool | true | Preview changes without deleting (default) |
| `--backend` | string | `local` | Storage backend |
| `--storage-root` | path | `tools/report/operations_center/observer` | Storage root directory |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Example

```bash
# Preview: snapshots that would be deleted
operations-center-observer-snapshot cleanup --days 30 --keep-count 50

# Actually delete (not dry-run)
operations-center-observer-snapshot cleanup --days 30 --keep-count 50 --no-dry-run
```

#### Status

**Note**: `cleanup` command is not yet fully implemented.

---

### `observe-and-validate` — Collect and Validate Snapshot

Generate a new snapshot and validate it immediately.

#### Syntax

```bash
operations-center-observer-snapshot observe-and-validate [OPTIONS]
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--repo-path` | path | current dir | Repository path |
| `--output-dir` | path | `tools/report/operations_center/observer` | Where to save snapshot |
| `--format` | string | `json` | Snapshot format: `json`, `yaml` |
| `--layers` | string | `1,2,3` | Validation layers to run |
| `--full` | bool | false | Include slow layers (4,5) — takes 60-120s |
| `--skip-validation` | bool | false | Collect snapshot but skip validation |
| `--output` | path | — | Save validation report to file |
| `--verbose` | `-v` | bool | false | Detailed output |
| `--quiet` | `-q` | bool | false | Minimal output |

#### Status

**Note**: `observe-and-validate` command requires RepoObserver integration (not yet implemented).

---

## Validation Workflows

### Workflow 1: Quick Local Validation (Fast Path)

**Purpose**: Quick validation for local testing before pushing  
**Time**: ~100ms  
**Layers**: 1, 2, 3

```bash
operations-center-observer-snapshot validate snapshot.json
```

**What it checks**:
- ✅ Snapshot JSON structure is valid (Layer 1)
- ✅ All required signals are present (Layer 2)
- ✅ Signal values are internally consistent (Layer 3)
- ❌ Real-world tool accuracy not checked (fast path)
- ❌ Regression detection not included (fast path)

**When to use**: Before committing snapshot changes, local validation before CI run

---

### Workflow 2: Accuracy Validation (CI Validation)

**Purpose**: Validate snapshot accuracy against live tools  
**Time**: 5-30s depending on repo size  
**Layers**: 1, 2, 3, 4

```bash
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4 \
  --repo-path /path/to/repo \
  --timeout 60
```

**Configuration via environment**:

```bash
export OC_SNAPSHOT_LAYERS=1,2,3,4
export OC_SNAPSHOT_REPO_PATH=/path/to/repo
export OC_SNAPSHOT_TIMEOUT=60
operations-center-observer-snapshot validate snapshot.json
```

**What it checks**:
- ✅ Layers 1-3: Fast validation
- ✅ Layer 4: Real test count, coverage, lint violations match actual tools
- ❌ Regression not checked (use Workflow 3)

**When to use**: CI pipeline validation, manual validation of snapshot accuracy

---

### Workflow 3: Regression Detection (Baseline Comparison)

**Purpose**: Detect regressions between current and baseline snapshot  
**Time**: 5-30s (depending on repo size)  
**Layers**: 1, 2, 3, 4, 5

```bash
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --baseline baseline.json \
  --tolerance 0.05 \
  --coverage-tolerance 0.10
```

**Tolerance guidelines**:

| Metric | Conservative | Default | Aggressive |
|--------|--------------|---------|-----------|
| Test Count | 1% (0.01) | 5% (0.05) | 10% (0.10) |
| Coverage | 2% (0.02) | 5% (0.05) | 10% (0.10) |
| Lint Violations | 5% (0.05) | 10% (0.10) | 20% (0.20) |

**What it checks**:
- ✅ Layers 1-4: Full accuracy validation
- ✅ Layer 5: Coverage regression (>2pp drop), test count change (>5% variance)
- ✅ Custom tolerances per metric

**When to use**: Validating changes that might affect test/coverage metrics, baseline comparisons

---

### Workflow 4: Verbose Debugging

**Purpose**: Debug validation failures with detailed error information  
**Time**: ~100ms + layers 4-5 if enabled  
**Layers**: Configurable

```bash
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --verbose \
  --output report.json
```

**Output includes**:
- Detailed error context for each failing layer
- Field-by-field validation results
- Tool execution logs (for layers 4-5)
- Performance metrics per layer
- Saved to JSON file for archival

**When to use**: Troubleshooting validation failures, archiving validation history

---

### Workflow 5: Batch Validation

**Purpose**: Validate multiple snapshots  
**Time**: ~100ms per snapshot (fast path)  
**Layers**: 1, 2, 3

```bash
for snapshot in snapshots/*.json; do
  echo "Validating $snapshot..."
  operations-center-observer-snapshot validate "$snapshot" --quiet
  if [ $? -ne 0 ]; then
    echo "FAILED: $snapshot"
    operations-center-observer-snapshot validate "$snapshot" --verbose
  fi
done
```

**Or with parallel execution**:

```bash
find snapshots -name "*.json" | \
  xargs -P 4 -I {} \
  operations-center-observer-snapshot validate {} --quiet --output {}.report.json
```

**When to use**: Testing multiple CI runs, validating snapshots from different branches

---

## Configuration

### Configuration Precedence

1. **Command-line options** (highest priority)
2. **Environment variables** (OC_SNAPSHOT_*)
3. **Defaults** (lowest priority)

Example with precedence:

```bash
# Defaults to 0.05 (5%)
operations-center-observer-snapshot validate snapshot.json

# Overrides to 0.10 (10%) via environment
export OC_SNAPSHOT_TOLERANCE=0.10
operations-center-observer-snapshot validate snapshot.json  # Uses 0.10

# Command-line overrides environment
operations-center-observer-snapshot validate snapshot.json --tolerance 0.02  # Uses 0.02
```

### Configuration File (Future)

Create `.snapshot-config.yaml` in your repository:

```yaml
# Default validation layers
layers: [1, 2, 3, 4]

# Tolerance thresholds
tolerance:
  global: 0.05
  coverage: 0.10
  test_count: 0.02

# Paths
repo_path: .
baseline: tools/report/baseline.json

# Timeout for layer 4 (seconds)
timeout: 60

# Output preferences
output_format: table
verbose: false
```

**Status**: Configuration file support planned for future release.

---

## Output Formats

### `table` (Default)

Human-readable table format with colors:

```
Snapshot Validation Report
─────────────────────────────────────────────────────────────
Layer     │ Status  │ Duration │ Details
─────────────────────────────────────────────────────────────
1 Schema  │ PASSED  │ 45ms     │ All fields valid
2 Complete│ PASSED  │ 22ms     │ 6/6 signals present
3 Consist │ PASSED  │ 58ms     │ No semantic contradictions
4 Accuracy│ FAILED  │ 15200ms  │ Test count: -3.2% (within 5%)
5 Regression│ PASSED│ 8300ms   │ Coverage stable, tests +2%
─────────────────────────────────────────────────────────────
Overall: PASSED (5/5 layers)
Time: 23.6s
```

### `json` (Machine-readable)

```json
{
  "snapshot_id": "abc123",
  "report_timestamp": "2026-06-14T10:30:45.123Z",
  "overall_status": "passed",
  "layers": [
    {
      "layer": 1,
      "name": "schema",
      "status": "passed",
      "duration_ms": 45,
      "errors": []
    },
    {
      "layer": 4,
      "name": "accuracy",
      "status": "passed",
      "duration_ms": 15200,
      "metrics": {
        "test_count": {
          "expected": 500,
          "actual": 485,
          "error_percent": 3.0,
          "tolerance": 5.0,
          "passed": true
        }
      }
    }
  ],
  "performance": {
    "total_duration_ms": 23600,
    "layers_with_external_tools": ["layer_4", "layer_5"]
  }
}
```

### `markdown`

```markdown
# Snapshot Validation Report

**Status**: ✅ PASSED (5/5 layers)  
**Duration**: 23.6s  
**Timestamp**: 2026-06-14 10:30:45

## Layer Results

### 1. Schema Validation ✅
- **Status**: PASSED
- **Duration**: 45ms
- All fields valid and properly typed

### 2. Completeness ✅
- **Status**: PASSED
- **Duration**: 22ms
- 6/6 required signals present

### 4. Accuracy ✅
- **Status**: PASSED
- **Test Count**: 485/500 (97%, tolerance: 5%)
- **Coverage**: 85.2%/85.0% (100%, tolerance: 5%)

## Summary

All validation layers passed successfully.
```

### `text` (Plain text)

```
Snapshot Validation Report

Overall Status: PASSED
Duration: 23.6s

Layers:
  1. Schema:       PASSED (45ms)
  2. Completeness: PASSED (22ms)
  3. Consistency:  PASSED (58ms)
  4. Accuracy:     PASSED (15.2s)
  5. Regression:   PASSED (8.3s)

All layers passed.
```

---

## Troubleshooting Guide

### Error: "Snapshot file not found"

**Exit Code**: 2  
**Cause**: The specified snapshot path doesn't exist

**Solutions**:

1. **Check the path**:
   ```bash
   # Verify file exists
   ls -la /path/to/snapshot.json
   
   # Try absolute path
   operations-center-observer-snapshot validate /absolute/path/snapshot.json
   ```

2. **Check for typos**:
   ```bash
   # List available snapshots
   operations-center-observer-snapshot list
   ```

3. **Use storage lookup by run_id**:
   ```bash
   # If snapshot is stored in default location
   operations-center-observer-snapshot validate run-abc123
   ```

---

### Error: "Failed to load/parse snapshot"

**Exit Code**: 3  
**Cause**: Snapshot file is malformed (invalid JSON/YAML)

**Solutions**:

1. **Validate JSON syntax**:
   ```bash
   python -m json.tool snapshot.json > /dev/null
   # or
   jq empty snapshot.json
   ```

2. **Validate YAML syntax**:
   ```bash
   python -c "import yaml; yaml.safe_load(open('snapshot.yaml'))"
   ```

3. **Check file encoding**:
   ```bash
   file snapshot.json  # Should show UTF-8
   ```

4. **View raw content**:
   ```bash
   head -50 snapshot.json
   ```

---

### Error: "Validation failed"

**Exit Code**: 1  
**Cause**: One or more validation layers failed

**Solution: Get detailed error information**:

```bash
# Run with verbose output
operations-center-observer-snapshot validate snapshot.json --verbose

# Save detailed report to file
operations-center-observer-snapshot validate snapshot.json \
  --verbose \
  --output report.json
```

**Common failures by layer**:

#### Layer 1 (Schema)
- Malformed JSON/YAML
- Missing required fields
- Invalid field types

**Debug**:
```bash
operations-center-observer-snapshot validate snapshot.json --layers 1 --verbose
```

#### Layer 2 (Completeness)
- Missing required signals (test_signal, dependency_drift, lint_signal)
- Too many collector errors (>5)
- Less than 3 non-unavailable signals

**Debug**:
```bash
operations-center-observer-snapshot validate snapshot.json --layers 2 --verbose

# Show the snapshot signals
operations-center-observer-snapshot show snapshot.json --field signals
```

#### Layer 3 (Consistency)
- Semantic contradictions between signals
  - Test status "passing" but test_count = 0
  - Lint violations > 100 but status != "violations"
  - Coverage > 0 but no coverage data

**Debug**:
```bash
operations-center-observer-snapshot validate snapshot.json --layers 3 --verbose

# Show signal values
operations-center-observer-snapshot show snapshot.json --field signals.test_signal
```

#### Layer 4 (Accuracy)
- Snapshot values differ from actual tools beyond tolerance
- Tools not found (pytest, ruff, coverage.py not in PATH)
- Timeout while running tools

**Debug**:
```bash
# Increase timeout
operations-center-observer-snapshot validate snapshot.json \
  --layers 4 \
  --timeout 120 \
  --verbose

# Check tool availability
which pytest ruff coverage
pytest --version
```

#### Layer 5 (Regression)
- Coverage regression detected (>2pp drop from baseline)
- Test count variance (>5% from baseline)

**Debug**:
```bash
# View baseline
operations-center-observer-snapshot show baseline.json

# Show current snapshot test count
operations-center-observer-snapshot show snapshot.json --field signals.test_signal

# Adjust tolerance
operations-center-observer-snapshot validate snapshot.json \
  --layers 5 \
  --baseline baseline.json \
  --tolerance 0.10 \
  --verbose
```

---

### Error: "Configuration error or invalid argument"

**Exit Code**: 4  
**Cause**: Invalid CLI arguments or configuration

**Check**:

1. **Layer specification must be 1-5**:
   ```bash
   # Valid
   operations-center-observer-snapshot validate snapshot.json --layers 1,2,3,4,5
   
   # Invalid (0 is not valid)
   operations-center-observer-snapshot validate snapshot.json --layers 0,1,2
   # Error: Layer must be 1-5, got 0
   ```

2. **Tolerance must be valid float**:
   ```bash
   # Valid
   --tolerance 0.05      # 5%
   --tolerance 0.10      # 10%
   
   # Invalid
   --tolerance invalid    # Error: not a valid float
   ```

3. **Format must be valid**:
   ```bash
   # Valid formats
   --format table
   --format json
   --format markdown
   --format text
   
   # Invalid
   --format csv  # Error: unsupported format
   ```

4. **Log level must be valid**:
   ```bash
   # Valid
   --log-level debug
   --log-level info
   --log-level warning
   --log-level error
   
   # Invalid
   --log-level verbose  # Error: unsupported level
   ```

---

### Error: "Timeout while running layer 4"

**Cause**: Layer 4 (accuracy) took longer than timeout  
**Default timeout**: 60 seconds

**Solutions**:

1. **Increase timeout**:
   ```bash
   operations-center-observer-snapshot validate snapshot.json \
     --layers 1,2,3,4 \
     --timeout 120  # 2 minutes
   ```

2. **Skip layer 4 if not needed**:
   ```bash
   operations-center-observer-snapshot validate snapshot.json \
     --layers 1,2,3  # Skip layer 4
   ```

3. **Check tool performance**:
   ```bash
   # Directly time the tools
   time pytest --collect-only
   time ruff check .
   time coverage report
   ```

4. **Use parallel execution** (if repo is large):
   ```bash
   # Run layer 4 separately with more resources
   operations-center-observer-snapshot validate snapshot.json \
     --layers 4 \
     --timeout 300 \
     --verbose
   ```

---

### Warning: "Tool not found in PATH"

**Cause**: Layer 4 accuracy validation requires tools not installed

**Required tools for layer 4**:
- `pytest` — for test count accuracy
- `ruff` — for lint violation count
- `coverage` — for coverage accuracy

**Install**:

```bash
# Install all tools
pip install pytest ruff coverage

# Or from project dependencies
pip install -e .[test,lint,coverage]
```

**Verify installation**:

```bash
pytest --version
ruff --version
coverage --version
```

---

### Snapshot shows errors but validation passed

**Cause**: Snapshot contains collector errors but they're within tolerance (≤5)

**Explanation**:

Layer 2 (Completeness) checks that collector errors ≤ 5. If the snapshot has 3 collector errors:

```json
{
  "collector_errors": [
    {"collector": "test_signal", "error": "timeout"},
    {"collector": "coverage", "error": "file not found"},
    {"collector": "dependency_drift", "error": "network error"}
  ]
}
```

The validation passes because 3 ≤ 5, but you have degraded data.

**Recommendations**:

1. **Investigate the errors**:
   ```bash
   operations-center-observer-snapshot show snapshot.json --field collector_errors
   ```

2. **Fix the underlying issues**:
   - Check tool availability (pytest, ruff, coverage)
   - Check network connectivity
   - Check file permissions

3. **Re-run snapshot collection**:
   ```bash
   operations-center-observer-snapshot observe-and-validate \
     --repo-path /path/to/repo \
     --verbose
   ```

---

### Regression detected (Layer 5 failed)

**Cause**: Current snapshot shows significant regression vs. baseline

**Example**:

```
Layer 5 Regression: FAILED
  Coverage regression: 85.2% → 83.0% (2.2pp drop > 2pp threshold)
  Test count variance: 500 → 485 (3% variance < 5% threshold)
```

**Analysis**:

1. **View the baseline**:
   ```bash
   operations-center-observer-snapshot show baseline.json \
     --field signals.coverage
   ```

2. **View current snapshot**:
   ```bash
   operations-center-observer-snapshot show snapshot.json \
     --field signals.coverage
   ```

3. **Adjust tolerance if the change is acceptable**:
   ```bash
   operations-center-observer-snapshot validate snapshot.json \
     --layers 5 \
     --baseline baseline.json \
     --coverage-tolerance 0.03  # 3% instead of default 5%
   ```

4. **Update baseline if the change is intentional**:
   ```bash
   # Copy current snapshot as new baseline
   cp snapshot.json baseline.json
   
   # Re-run validation with new baseline
   operations-center-observer-snapshot validate snapshot.json \
     --layers 5 \
     --baseline baseline.json
   ```

---

## CI/CD Integration

### GitHub Actions

Add snapshot validation to your CI workflow:

#### Basic Validation (Fast Path)

```yaml
name: Snapshot Validation

on: [push, pull_request]

jobs:
  validate-snapshot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: pip install -e . pytest ruff coverage
      
      - name: Validate snapshot (fast path)
        run: |
          operations-center-observer-snapshot validate \
            tools/report/operations_center/observer/*/repo_state_snapshot.json \
            --format json \
            --output snapshot-report.json
      
      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: snapshot-report
          path: snapshot-report.json
```

#### Full Validation (Accuracy + Regression)

```yaml
name: Full Snapshot Validation

on: [push, pull_request]

jobs:
  validate-full:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: pip install -e . pytest ruff coverage
      
      - name: Collect snapshot (with observation)
        run: |
          operations-center-observer-snapshot observe-and-validate \
            --repo-path . \
            --full \
            --output snapshot-full-report.json \
            --verbose
      
      - name: Validate against baseline
        run: |
          operations-center-observer-snapshot validate \
            tools/report/operations_center/observer/*/repo_state_snapshot.json \
            --layers 1,2,3,4,5 \
            --baseline baseline.json \
            --tolerance 0.05 \
            --output validation-report.json
      
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: validation-reports
          path: |
            snapshot-full-report.json
            validation-report.json
```

#### With Baseline Update

```yaml
name: Update Snapshot Baseline

on:
  workflow_dispatch:  # Manual trigger
  schedule:
    - cron: '0 0 * * 0'  # Weekly

jobs:
  update-baseline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: pip install -e . pytest ruff coverage
      
      - name: Generate new snapshot
        run: |
          operations-center-observer-snapshot observe-and-validate \
            --repo-path . \
            --full
      
      - name: Copy to baseline
        run: |
          cp tools/report/operations_center/observer/*/repo_state_snapshot.json \
             baseline.json
      
      - name: Commit and push
        run: |
          git add baseline.json
          git commit -m "chore: update snapshot baseline"
          git push
```

### GitLab CI

```yaml
snapshot-validation:
  stage: test
  image: python:3.10
  script:
    - pip install -e . pytest ruff coverage
    - operations-center-observer-snapshot validate
        tools/report/operations_center/observer/*/repo_state_snapshot.json
        --format json
        --output snapshot-report.json
        --timeout 120
  artifacts:
    paths:
      - snapshot-report.json
    reports:
      dotenv: snapshot-report.json
  timeout: 10 minutes
```

### Jenkins

```groovy
pipeline {
    agent any
    stages {
        stage('Snapshot Validation') {
            steps {
                sh '''
                    pip install -e . pytest ruff coverage
                    operations-center-observer-snapshot validate \
                        tools/report/operations_center/observer/*/repo_state_snapshot.json \
                        --format json \
                        --output snapshot-report.json \
                        --verbose
                '''
            }
        }
    }
    post {
        always {
            archiveArtifacts artifacts: 'snapshot-report.json'
        }
    }
}
```

### Local Pre-commit Hook

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash

echo "Running snapshot validation..."

# Get list of changed snapshot files
snapshots=$(git diff --cached --name-only | grep 'repo_state_snapshot.json')

if [ -z "$snapshots" ]; then
    exit 0
fi

for snapshot in $snapshots; do
    echo "Validating $snapshot..."
    operations-center-observer-snapshot validate "$snapshot" --quiet
    
    if [ $? -ne 0 ]; then
        echo "❌ Snapshot validation failed: $snapshot"
        operations-center-observer-snapshot validate "$snapshot" --verbose
        exit 1
    fi
done

echo "✅ All snapshots valid"
exit 0
```

Make executable:

```bash
chmod +x .git/hooks/pre-commit
```

---

## Examples

### Example 1: Local Development Workflow

```bash
# 1. Make changes to your repo
vim src/main.py

# 2. Collect a new snapshot
python -m operations_center.observer observe

# 3. Quickly validate it (fast path ~100ms)
operations-center-observer-snapshot validate snapshot.json

# 4. If you want full validation before pushing
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --verbose

# 5. Push if validation passes
git add snapshot.json
git commit -m "feat: update snapshot"
git push
```

### Example 2: CI Validation Matrix

```bash
#!/bin/bash
# Validate snapshots from different Python versions

for python_version in 3.9 3.10 3.11; do
  echo "Testing Python $python_version..."
  
  snapshot="snapshots/snapshot-py${python_version}.json"
  
  if [ ! -f "$snapshot" ]; then
    echo "Snapshot not found: $snapshot"
    continue
  fi
  
  operations-center-observer-snapshot validate "$snapshot" \
    --layers 1,2,3,4 \
    --repo-path . \
    --output "report-py${python_version}.json"
  
  if [ $? -eq 0 ]; then
    echo "✅ Python $python_version: PASSED"
  else
    echo "❌ Python $python_version: FAILED"
  fi
done
```

### Example 3: Batch Regression Testing

```bash
#!/bin/bash
# Test current snapshot against multiple baselines

current="snapshot.json"
baselines=(
  "baseline-main.json"
  "baseline-v1.0.json"
  "baseline-v0.9.json"
)

for baseline in "${baselines[@]}"; do
  if [ ! -f "$baseline" ]; then
    continue
  fi
  
  echo "Comparing against $baseline..."
  
  operations-center-observer-snapshot validate "$current" \
    --layers 1,2,3,4,5 \
    --baseline "$baseline" \
    --coverage-tolerance 0.05 \
    --test-count-tolerance 0.05 \
    --format json \
    --output "report-vs-${baseline%.json}.json"
done
```

### Example 4: Debugging Failed Validation

```bash
#!/bin/bash

snapshot="snapshot.json"

# 1. Quick check
echo "=== Quick validation (fast path) ==="
operations-center-observer-snapshot validate "$snapshot" --layers 1,2,3

# 2. If passed, try accuracy
if [ $? -eq 0 ]; then
  echo ""
  echo "=== Accuracy validation (layer 4) ==="
  operations-center-observer-snapshot validate "$snapshot" \
    --layers 4 \
    --timeout 120 \
    --verbose
fi

# 3. If both passed, try regression
if [ $? -eq 0 ]; then
  echo ""
  echo "=== Regression detection (layer 5) ==="
  operations-center-observer-snapshot validate "$snapshot" \
    --layers 5 \
    --baseline baseline.json \
    --verbose
fi

# 4. Save complete report
echo ""
echo "=== Saving detailed report ==="
operations-center-observer-snapshot validate "$snapshot" \
  --layers 1,2,3,4,5 \
  --baseline baseline.json \
  --verbose \
  --output report.json

echo "Report saved to report.json"
```

---

## Help & Man Page

### Built-in Help

```bash
# Show help for main command
operations-center-observer-snapshot --help

# Show help for specific command
operations-center-observer-snapshot validate --help
operations-center-observer-snapshot list --help

# Show version
operations-center-observer-snapshot --version
```

### Detailed Command Help

```bash
# Help with examples
operations-center-observer-snapshot validate --help
# Shows: Arguments, Options, Environment Variables, Exit Codes
```

### Man Page

Generate man page (requires ronn):

```bash
# Install ronn (optional)
gem install ronn

# Generate man page
operations-center-observer-snapshot --help | ronn > operations-center-observer-snapshot.1

# View man page
man ./operations-center-observer-snapshot.1
```

Or view text version:

```bash
operations-center-observer-snapshot validate --help | less
```

---

## Additional Resources

- **Design Document**: [STAGE0_CLI_SPECIFICATION.md](../design/STAGE0_CLI_SPECIFICATION.md)
- **API Reference**: [Snapshot Validation Engine](../api/snapshot_validation_engine.md)
- **Testing Guide**: See `tests/unit/observer/test_snapshot_cli.py`

---

**Last Updated**: 2026-06-14  
**CLI Version**: 0.1.0  
**Status**: Complete
