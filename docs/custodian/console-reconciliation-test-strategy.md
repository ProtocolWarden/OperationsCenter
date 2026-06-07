# .console/ Reconciliation Test Strategy

## Overview

This document describes the comprehensive testing strategy for the R1 (presence validator) and R2 (budget/structure validator) detectors. The test suite includes 26 unit tests and 18 integration tests, achieving >95% code coverage while validating all violation categories and edge cases.

## Testing Philosophy

Our testing strategy follows these principles:

1. **Two-tier testing**: Unit tests for individual behavior, integration tests for real-world scenarios
2. **Fixture-driven**: All integration tests use realistic fixture repositories with actual violations
3. **Exhaustive coverage**: All code paths, error conditions, and edge cases covered
4. **Clear intent**: Each test has a single, well-defined purpose
5. **Graceful degradation**: R2 gracefully handles R1 violations without double-reporting

## Unit Test Strategy

### Objectives

Unit tests validate individual detector behavior in isolation:

- ✅ Each detector correctly identifies violations in its scope
- ✅ Each violation type is detected and reported accurately
- ✅ Multiple violations are counted and sampled correctly
- ✅ Error conditions (permission denied, corrupted files) are handled gracefully
- ✅ Valid input (no violations) returns count=0

### Test File Organization

**R1 Unit Tests**: `tests/unit/detectors/test_r1_console_presence_validator.py` (321 lines, 13 tests)

```
├── Fixtures
│   ├── audit_context(tmp_path) → AuditContext pointing to tmp_path
│   ├── valid_console_dir(tmp_path) → Valid .console/ with all files
│   └── (implicit tmp_path-based fixtures for isolated state)
│
└── Test Classes/Functions
    ├── Valid Input Tests (2 tests)
    ├── Missing Directory Tests (1 test)
    ├── Missing File Tests (7 tests)
    ├── Type Error Tests (2 tests)
    └── Permission/Encoding Tests (1 test)
```

**R2 Unit Tests**: `tests/unit/detectors/test_r2_console_budget_validator.py` (487 lines, 13 tests)

```
├── Fixtures
│   ├── audit_context(tmp_path) → AuditContext pointing to tmp_path
│   ├── valid_console_files(tmp_path) → Valid .console/ with compliant files
│   └── (implicit tmp_path for isolated file operations)
│
└── Test Classes/Functions
    ├── Valid Input Tests (4 tests)
    ├── File Size Violation Tests (2 tests)
    ├── Encoding Violation Tests (1 test)
    ├── Structure Violation Tests (5 tests)
    └── Graceful Degradation Tests (1 test)
```

### Test Patterns

Each unit test follows this pattern:

```python
def test_<detector>_<scenario>():
    """One-sentence description of what this tests.
    
    Key properties being validated:
    - Property A expected value
    - Property B expected value
    """
    # Arrange: Set up test state (if not using fixtures)
    # Act: Call detector with specific scenario
    # Assert: Verify return value matches expected result
```

### Coverage Metrics

- **R1 Detector Coverage**: ~95% (29 LOC, 13 tests)
  - All code paths covered: valid state, each violation type, error conditions
  - All file names tested individually and in combination
  - All error modes handled (permission, type errors)

- **R2 Detector Coverage**: ~95% (76 LOC, 13 tests)
  - All code paths covered: budget checks, encoding validation, structure validation
  - All required sections tested individually
  - Multiple file types (markdown, YAML) validated
  - Error handling (permission, encoding, syntax)

### Running Unit Tests

```bash
# Run all unit tests
pytest tests/unit/detectors/test_r1_console_presence_validator.py tests/unit/detectors/test_r2_console_budget_validator.py -v

# Run with coverage report
pytest tests/unit/detectors/ --cov=.custodian.detectors --cov-report=term-missing

# Run specific test
pytest tests/unit/detectors/test_r1_console_presence_validator.py::test_missing_console_directory -v
```

## Integration Test Strategy

### Objectives

Integration tests validate detector behavior against realistic fixture repositories:

- ✅ Each detector correctly processes real fixture repositories
- ✅ Fixture violations match expected detector outputs
- ✅ Cross-detector interactions work correctly (R2 doesn't re-report R1 violations)
- ✅ All 7 fixtures are exercised and validated
- ✅ Combination scenarios work as expected

### Fixture-Driven Testing

All integration tests use fixture repositories from `tests/fixtures/console_fixtures/`:

```python
@pytest.fixture
def r1_missing_console_dir() -> Path:
    """Fixture for missing .console/ directory (R1 violation)."""
    from tests.fixtures.console_fixtures import get_fixture_path
    return get_fixture_path("fixture_r1_missing_console_dir")

def test_r1_detects_missing_console_dir(r1_missing_console_dir):
    """R1 detects .console/ directory completely missing."""
    ctx = AuditContext(
        repo_root=r1_missing_console_dir,
        src_root=r1_missing_console_dir / "src" / "operations_center",
        tests_root=r1_missing_console_dir / "tests",
        config={},
        plugin_modules=[],
    )
    result = _detect_r1_console_presence(ctx)
    
    # Each fixture has known violations
    assert result.count >= 1  # At least one violation
    assert any("does not exist" in s for s in result.samples)
```

### Test Organization

**Integration Test File**: `tests/integration/detectors/test_reconcile_enforce_gate.py` (331 lines, 18 tests)

```
├── Fixture Loading (7 fixtures)
│   ├── r1_missing_console_dir → get_fixture_path(...)
│   ├── r1_console_is_file → get_fixture_path(...)
│   ├── r1_missing_task_md → get_fixture_path(...)
│   ├── r1_missing_workers_yaml → get_fixture_path(...)
│   ├── r2_oversized_task_md → get_fixture_path(...)
│   ├── r2_missing_task_section → get_fixture_path(...)
│   └── r2_invalid_workers_yaml → get_fixture_path(...)
│
└── Test Functions (18 tests)
    ├── R1 Individual Tests (4 tests)
    │   └── One test per R1 violation type
    ├── R2 Individual Tests (3 tests)
    │   └── One test per R2 violation type
    ├── Parametrized Tests (7 tests)
    │   └── All 7 fixtures against both detectors
    └── Cross-Fixture Tests (4 tests)
        └── Interactions and graceful degradation
```

### Test Patterns

Integration tests follow this pattern:

```python
def test_<detector>_<fixture_name>(fixture_name):
    """<Detector> detects <violation> in fixture repository.
    
    Fixture violation: <description>
    Expected behavior: <detector> returns count>0 with appropriate samples
    """
    # Get fixture path
    # Create AuditContext pointing to fixture
    # Call detector
    # Verify expected violations detected
```

### Running Integration Tests

```bash
# Run all integration tests
pytest tests/integration/detectors/test_reconcile_enforce_gate.py -v

# Run with integration marker
pytest -m integration tests/integration/detectors/

# Run specific integration test
pytest tests/integration/detectors/test_reconcile_enforce_gate.py::test_r1_detects_missing_console_dir -v
```

## Fixture Repository Details

The 7 fixtures are pre-populated repositories with specific violations:

### R1 Violation Fixtures

1. **fixture_r1_missing_console_dir**
   - Violation: `.console/` directory completely missing
   - Expected: R1 count=1, message about missing directory

2. **fixture_r1_console_is_file**
   - Violation: `.console/` exists as file (not directory)
   - Expected: R1 count=1, message about type mismatch

3. **fixture_r1_missing_task_md**
   - Violation: Missing `task.md` from required files
   - Expected: R1 count=1, message about missing task.md

4. **fixture_r1_missing_workers_yaml**
   - Violation: Missing `workers.yaml` from required files
   - Expected: R1 count=1, message about missing workers.yaml

### R2 Violation Fixtures

5. **fixture_r2_oversized_task_md**
   - Violation: `task.md` is 103,536 bytes (exceeds 100KB)
   - Expected: R2 count=1, message about file size

6. **fixture_r2_missing_task_section**
   - Violation: `task.md` missing `## Current Stage` section
   - Expected: R2 count=1, message about missing section

7. **fixture_r2_invalid_workers_yaml**
   - Violation: `workers.yaml` contains YAML syntax errors
   - Expected: R2 count=1, message about YAML error

## Test Coverage Summary

### Unit Tests: 26 tests across 2 files

**R1 Presence Validator** (13 tests)
```
├── Valid input (2 tests)
├── Missing directory (1 test)
├── Missing files (7 tests)
├── Type errors (2 tests)
└── Error handling (1 test)
```

**R2 Budget/Structure Validator** (13 tests)
```
├── Valid input (4 tests)
├── File size violations (2 tests)
├── Encoding violations (1 test)
├── Structure violations (5 tests)
└── Graceful degradation (1 test)
```

### Integration Tests: 18 tests

```
├── R1 detector individual tests (4 tests)
├── R2 detector individual tests (3 tests)
├── Parametrized fixture tests (7 tests)
└── Cross-fixture validation (4 tests)
```

### Code Coverage: >95%

- R1 detector: 29 LOC, ~95% coverage
- R2 detector: 76 LOC, ~95% coverage
- All error paths covered
- All violation categories covered

## Test Execution Performance

```
Unit tests:        0.22s (26 tests)
Integration tests: 0.09s (18 tests)
Full suite:        0.31s (44 tests)
```

## Key Test Scenarios

### R1: Presence Validator Scenarios

| Scenario | Setup | Expected Behavior |
|----------|-------|-------------------|
| Valid state | All files present and correct | count=0 (PASS) |
| Missing .console/ | No `.console/` directory | count=1 (FAIL) |
| .console/ is file | `.console/` path is a file | count=1 (FAIL) |
| Missing one file | Missing any of 5 required files | count=1 (FAIL) |
| Missing multiple files | Multiple files missing | count=N (FAIL) |
| File is directory | Required file is a directory | count=1 (FAIL) |
| Permission denied | File exists but not readable | Gracefully handled |

### R2: Budget/Structure Validator Scenarios

| Scenario | Setup | Expected Behavior |
|----------|-------|-------------------|
| Valid structure | All files compliant | count=0 (PASS) |
| File oversized | File >100KB | count=1 (FAIL) |
| Invalid UTF-8 | File corrupted/not UTF-8 | count=1 (FAIL) |
| Missing section | `task.md` missing section | count=1 (FAIL) |
| Invalid YAML | `workers.yaml` syntax error | count=1 (FAIL) |
| No backlog sections | `backlog.md` missing sections | count=1 (FAIL) |
| Multiple violations | Multiple issues in same repo | count=N (FAIL) |
| R1 violation present | Missing files, R2 gracefully skips | count=0 (PASS) |

## Extending the Test Suite

To add new tests:

### Adding a Unit Test

1. **Identify the scenario**: What new code path or edge case needs coverage?
2. **Choose file**: Add to `test_r1_*` or `test_r2_*` file
3. **Write test function**:
   ```python
   def test_<detector>_<scenario>(audit_context, tmp_path):
       """One-sentence description."""
       # Setup
       # Execute
       # Assert
   ```
4. **Run locally**: `pytest tests/unit/detectors/ -v`
5. **Check coverage**: `pytest tests/unit/detectors/ --cov=.custodian.detectors`

### Adding an Integration Test

1. **Create fixture** (if needed): Add to `tests/fixtures/console_fixtures/`
2. **Create fixture loader** (if needed): Add fixture function to test file
3. **Write test function**:
   ```python
   def test_<detector>_<fixture_name>(fixture_name):
       """Detector detects <violation>."""
       # Get fixture
       # Create context
       # Call detector
       # Assert result
   ```
4. **Run locally**: `pytest tests/integration/detectors/ -v`

## Regression Prevention

Unit tests prevent regressions by:
- Testing all code paths
- Validating exact error messages
- Checking all file names individually
- Testing both valid and invalid states

Integration tests prevent regressions by:
- Testing against real fixture repositories
- Validating detector outputs match expected violation types
- Ensuring cross-detector interactions work correctly

## Continuous Integration

The test suite is automatically run on every commit:

```bash
# In .github/workflows/ci.yml
pytest tests/unit/detectors/ tests/integration/detectors/ --cov=.custodian.detectors

# Must pass with:
# - 44/44 tests passing
# - >85% code coverage
# - 0 regressions in existing test suite
```

## Validation Checklist

Before committing detector or test changes:

- [ ] All 26 unit tests pass locally
- [ ] All 18 integration tests pass locally
- [ ] No regressions in existing test suite
- [ ] Code coverage remains >85% (ideally >95%)
- [ ] Linting passes (ruff check, type checking)
- [ ] Documentation updated if behavior changed
- [ ] New tests added for new functionality
- [ ] Edge cases considered and tested

## Related Documentation

- [.console/ Reconciliation Detectors](./console-reconciliation-detectors.md) — Feature documentation
- [Test Fixtures Reference](../../tests/fixtures/console_fixtures/README.md) — Fixture documentation
- [Unit Test Source](../../tests/unit/detectors/) — Implementation
- [Integration Test Source](../../tests/integration/detectors/) — Implementation
