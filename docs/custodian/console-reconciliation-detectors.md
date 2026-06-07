# .console/ Reconciliation Detectors (R1 & R2)

## Overview

This document describes the R1 (presence validator) and R2 (budget/structure validator) detectors for OperationsCenter's `.console/` reconciliation system. These detectors are Custodian plugins that validate the operational truth files in repositories, ensuring complete and well-formed `.console/` infrastructure.

## Architecture

The `.console/` directory is OperationsCenter's operational truth layer — containing the canonical task definition, guidelines, backlog, execution log, and worker configuration. Incomplete or malformed `.console/` directories can cause silent failures in the watchdog loop, inconsistent operational state, and loss of task context.

### Two-Tier Validation Strategy

We use a two-tier validation strategy to catch problems early and provide clear remediation guidance:

**R1 (Presence Validator)** — `_detect_r1_console_presence()`
- **Scope**: Directory and file presence
- **Checks**:
  - `.console/` directory exists
  - `.console/` is a directory (not a file)
  - All 5 required core files exist and are files (not directories):
    - `task.md` — Current task/objective definition
    - `guidelines.md` — Repository policy and constraints
    - `backlog.md` — Work inventory (in progress, up next, done)
    - `log.md` — Recent decisions and stop points
    - `workers.yaml` — Watchdog loop worker definitions
- **Severity**: MEDIUM (operational infrastructure missing)
- **Remediation**: Create missing files or fix directory/file type issues

**R2 (Budget/Structure Validator)** — `_detect_r2_console_budget()`
- **Scope**: File structure, content, and metadata
- **Checks**:
  - File size budget (each file <100KB) — prevents unbounded growth
  - UTF-8 encoding validity — detects corruption
  - `task.md` required sections: Objective, Overall Plan, Current Stage
  - `backlog.md` standard sections: In Progress, Up Next, Done
  - `workers.yaml` valid YAML syntax
- **Severity**: MEDIUM (operational state incomplete)
- **Remediation**: Fix file sizes, add missing sections, correct YAML syntax

### Design Rationale

**Two separate detectors** instead of one combined detector:

1. **Progressive remediation**: Developers fix presence issues (R1) first, then structure issues (R2). This reduces cognitive load and cognitive friction.
2. **Clear error messages**: R1 failures are always about missing infrastructure; R2 failures are always about malformed content.
3. **Distinct test scenarios**: R1 tests focus on filesystem operations; R2 tests focus on file format validation.
4. **Backwards compatibility**: R2 gracefully handles R1 violations (skips structure checks if files don't exist).

## Implementation Details

### R1: Presence Validator

```python
def _detect_r1_console_presence(ctx: AuditContext) -> DetectorResult:
    """Validate .console/ directory and required core files exist."""
```

**Check sequence**:
1. Check if `.console/` exists
2. Check if `.console/` is a directory (not a file)
3. Check if all 5 required files exist and are files (not directories)

**Return format**: `DetectorResult(count=violations, samples=[...])`

**Example outputs**:
- `count=0`: All checks passed
- `count=1, samples=['.console/ directory does not exist (CRITICAL)']`: No `.console/`
- `count=1, samples=['.console/ exists but is not a directory (CRITICAL)']`: `.console/` is a file
- `count=2, samples=['.console/ missing required file: task.md', '.console/ missing required file: log.md']`: Missing files

### R2: Budget/Structure Validator

```python
def _detect_r2_console_budget(ctx: AuditContext) -> DetectorResult:
    """Validate .console/ files have proper structure, size, and content."""
```

**Check sequence**:
1. Early return if `.console/` directory doesn't exist or isn't a directory (R1 issue)
2. Check file sizes (max 100KB per file)
3. Validate UTF-8 encoding for all markdown files
4. Validate `task.md` has required sections
5. Validate `backlog.md` has standard sections
6. Validate `workers.yaml` is valid YAML

**Return format**: `DetectorResult(count=violations, samples=[...])` (up to 10 samples)

**Example outputs**:
- `count=0`: All checks passed
- `count=1, samples=['.console/task.md exceeds 100KB budget (105024 bytes)']`: File too large
- `count=1, samples=['.console/workers.yaml has YAML syntax error']`: Invalid YAML
- `count=3, samples=['.console/task.md missing required section: ## Current Stage', ...]`: Missing sections

## Test Coverage

### Unit Tests (26 tests)

Unit tests focus on individual detector behavior with complete isolation.

**R1 Presence Validator Tests** (`tests/unit/detectors/test_r1_console_presence_validator.py`)

13 tests covering:

1. **Valid input** (2 tests)
   - `test_valid_console_directory_all_files_present` — All 5 files present → count=0
   - `test_valid_console_directory_count_zero` — Valid state has no violations

2. **Missing directory** (1 test)
   - `test_missing_console_directory` — `.console/` absent → count=1

3. **Missing files** (7 tests, one per file)
   - `test_missing_task_md` — Missing `task.md` → count=1
   - `test_missing_guidelines_md` — Missing `guidelines.md` → count=1
   - `test_missing_backlog_md` — Missing `backlog.md` → count=1
   - `test_missing_log_md` — Missing `log.md` → count=1
   - `test_missing_workers_yaml` — Missing `workers.yaml` → count=1
   - `test_multiple_missing_files` — Missing multiple files → count=N
   - `test_file_exists_but_is_directory` — File is a directory → count=1

4. **Type errors** (2 tests)
   - `test_console_is_a_file_not_directory` — `.console/` is a file → count=1
   - `test_permission_denied_graceful_handling` — Permission error → handled gracefully

**R2 Budget/Structure Validator Tests** (`tests/unit/detectors/test_r2_console_budget_validator.py`)

13 tests covering:

1. **Valid input** (4 tests)
   - `test_valid_console_structure_all_files_compliant` — All files valid → count=0
   - `test_valid_task_md_with_all_sections` — `task.md` has all sections
   - `test_valid_backlog_md_with_standard_sections` — `backlog.md` has standard sections
   - `test_valid_workers_yaml_valid_syntax` — `workers.yaml` parses correctly

2. **File size violations** (2 tests)
   - `test_task_md_exceeds_100kb_budget` — File >100KB → count=1
   - `test_multiple_files_exceed_budget` — Multiple files oversized → count=N

3. **Encoding violations** (1 test)
   - `test_invalid_utf8_encoding_detected` — Corrupted file → count=1

4. **Structure violations** (5 tests)
   - `test_task_md_missing_objective_section` — Missing "Objective" → count=1
   - `test_task_md_missing_overall_plan_section` — Missing "Overall Plan" → count=1
   - `test_task_md_missing_current_stage_section` — Missing "Current Stage" → count=1
   - `test_backlog_md_no_standard_sections` — No standard sections → count=1
   - `test_workers_yaml_invalid_syntax` — Invalid YAML → count=1

5. **Graceful degradation** (1 test)
   - `test_r2_gracefully_skips_missing_r1_files` — R2 doesn't report R1 violations

### Integration Tests (18 tests)

Integration tests validate detectors against real fixture repositories with all violation combinations.

**Location**: `tests/integration/detectors/test_reconcile_enforce_gate.py`

18 tests covering:

1. **R1 Detector Individual Tests** (4 tests)
   - `test_r1_detects_missing_console_dir` — Fixture: missing directory
   - `test_r1_detects_console_is_file` — Fixture: `.console/` is a file
   - `test_r1_detects_missing_task_md` — Fixture: missing `task.md`
   - `test_r1_detects_missing_workers_yaml` — Fixture: missing `workers.yaml`

2. **R2 Detector Individual Tests** (3 tests)
   - `test_r2_detects_oversized_task_md` — Fixture: file exceeds 100KB
   - `test_r2_detects_missing_task_section` — Fixture: missing section
   - `test_r2_detects_invalid_workers_yaml` — Fixture: YAML syntax error

3. **Parametrized Gate Enforcement** (7 tests)
   - Tests all 7 fixtures against both R1 and R2 detectors
   - Validates fixture classification matches expected violations

4. **Cross-Fixture Validation** (4 tests)
   - R2 gracefully skips R1 violations
   - Combined R1+R2 violation handling
   - Multiple violations detected correctly

## Fixture Repositories

Seven fixture repositories enable comprehensive integration testing:

### R1 Violations (4 fixtures)

1. **`fixture_r1_missing_console_dir`**
   - Missing `.console/` directory entirely
   - Expected: `count=1` with message about missing directory

2. **`fixture_r1_console_is_file`**
   - `.console/` exists as a file (not directory)
   - Expected: `count=1` with message about type mismatch

3. **`fixture_r1_missing_task_md`**
   - `.console/` exists with 4 of 5 required files
   - Missing: `task.md`
   - Expected: `count=1` with message about missing file

4. **`fixture_r1_missing_workers_yaml`**
   - `.console/` exists with 4 of 5 required files
   - Missing: `workers.yaml`
   - Expected: `count=1` with message about missing file

### R2 Violations (3 fixtures)

5. **`fixture_r2_oversized_task_md`**
   - `task.md` is 103,536 bytes (exceeds 100KB budget)
   - Expected: `count=1` with message about file size

6. **`fixture_r2_missing_task_section`**
   - `task.md` missing `## Current Stage` section
   - Has `## Objective` and `## Overall Plan`
   - Expected: `count=1` with message about missing section

7. **`fixture_r2_invalid_workers_yaml`**
   - `workers.yaml` contains YAML syntax errors
   - Unclosed list and invalid boolean values
   - Expected: `count=1` with message about YAML error

## Usage Guide

### Running Tests

```bash
# Run all 26 unit tests (R1 + R2)
pytest tests/unit/detectors/test_r1_console_presence_validator.py tests/unit/detectors/test_r2_console_budget_validator.py

# Run all 18 integration tests
pytest tests/integration/detectors/test_reconcile_enforce_gate.py -m integration

# Run specific detector tests
pytest tests/unit/detectors/test_r1_console_presence_validator.py -v  # Show test names
pytest tests/unit/detectors/test_r2_console_budget_validator.py -v
```

### Using Fixtures in Your Own Tests

Fixtures are discoverable via the fixture registry:

```python
from tests.fixtures.console_fixtures import FIXTURES, get_fixture_path, list_fixtures

# Get a specific fixture path
r1_fixture = get_fixture_path("fixture_r1_missing_console_dir")

# List all fixtures
for name in list_fixtures():
    print(name)

# Access as pytest fixture (auto-generated from conftest.py)
def test_something(fixture_r1_missing_console_dir):
    assert not (fixture_r1_missing_console_dir / ".console").exists()
```

### Integrating Into CI

The detectors are automatically registered in `custodian.audit_kit` via `build_oc_detectors()`. To include them in your Custodian audit:

```yaml
# In .custodian/config.yaml
audit:
  enabled: true
  detectors:
    - R1  # .console/ presence validator
    - R2  # .console/ budget/structure validator
```

Run:
```bash
custodian audit --enable-oc-detectors
```

## Success Criteria

A repository passes R1/R2 validation when:

✅ **R1 (Presence)**: All required files exist
- `.console/` directory exists and is a directory
- All 5 core files exist and are files (not directories)

✅ **R2 (Structure)**: Files have proper content
- All files <100KB (reasonable growth budget)
- All files are valid UTF-8 (no corruption)
- `task.md` has Objective, Overall Plan, Current Stage sections
- `backlog.md` has at least one standard section (In Progress/Up Next/Done)
- `workers.yaml` is valid YAML

## Common Issues & Solutions

### Issue: ".console/ directory does not exist"
**Cause**: Repository missing `.console/` directory (likely new repo)
**Solution**: 
```bash
mkdir -p .console
cp .console/task.md.example .console/task.md  # Or create from template
```

### Issue: ".console/ exists but is not a directory"
**Cause**: `.console/` is a file instead of directory (usually from checkout conflict)
**Solution**:
```bash
rm .console
mkdir .console
git checkout .console/  # Restore from git
```

### Issue: ".console/task.md exceeds 100KB budget"
**Cause**: Task file has grown too large (usually old logs mixed in)
**Solution**:
1. Move old log entries to `.console/log.md` history section
2. Archive completed backlog items to `.console/backlog.md` archive
3. Verify file is now <100KB

### Issue: ".console/workers.yaml has YAML syntax error"
**Cause**: Invalid YAML syntax in worker definitions
**Solution**:
```bash
python -m yaml .console/workers.yaml  # Find syntax error
# Fix and re-validate
```

## Related Documentation

- [.console/ Reconciliation Fixture Repositories](../fixtures/console_fixtures/README.md) — Detailed fixture documentation
- [Unit Test Reference](../../tests/unit/detectors/) — Test source code
- [Integration Test Reference](../../tests/integration/detectors/) — Integration test source code
- [Custodian Detector Protocol](https://github.com/ProtocolWarden/custodian) — Custodian framework documentation
