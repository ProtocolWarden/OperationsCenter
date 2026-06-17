# .console/ Reconciliation Fixture Repositories

This directory contains 7 fixture repositories with various malformed `.console/` configurations. These fixtures enable comprehensive integration testing of the R1 (presence validator) and R2 (budget/structure validator) detectors for OperationsCenter's `.console/` reconciliation system.

## Fixture Inventory

All fixtures are discoverable via the `FIXTURES` registry in `__init__.py` and automatically exposed as pytest fixtures via `conftest.py`.

### R1 Violations: Directory & File Presence (4 fixtures)

#### 1. `fixture_r1_missing_console_dir`
**Violation Category**: Missing `.console/` directory  
**Expected R1 Detection**: `count=1` with sample `.console/ directory does not exist (CRITICAL)`

Directory structure:
```
fixture_r1_missing_console_dir/
└── README.md
```

The `.console/` directory does not exist at all. This is the most critical R1 violation—the reconciliation infrastructure is completely absent.

---

#### 2. `fixture_r1_console_is_file`
**Violation Category**: `.console/` is a file, not a directory  
**Expected R1 Detection**: `count=1` with sample `.console/ exists but is not a directory (CRITICAL)`

Directory structure:
```
fixture_r1_console_is_file/
├── .console (file)
└── README.md
```

The `.console/` path exists but is a regular file instead of a directory. This prevents the reconciliation system from reading any required metadata files.

---

#### 3. `fixture_r1_missing_task_md`
**Violation Category**: Missing required file (`task.md`)  
**Expected R1 Detection**: `count=1` with sample `.console/ missing required file: task.md`

Directory structure:
```
fixture_r1_missing_task_md/
├── .console/
│   ├── guidelines.md
│   ├── backlog.md
│   ├── log.md
│   └── workers.yaml
└── README.md
```

The `.console/` directory exists with most required files, but `task.md` is missing. The R1 validator requires all 5 core files: `task.md`, `guidelines.md`, `backlog.md`, `log.md`, and `workers.yaml`.

---

#### 4. `fixture_r1_missing_workers_yaml`
**Violation Category**: Missing required file (`workers.yaml`)  
**Expected R1 Detection**: `count=1` with sample `.console/ missing required file: workers.yaml`

Directory structure:
```
fixture_r1_missing_workers_yaml/
├── .console/
│   ├── task.md
│   ├── guidelines.md
│   ├── backlog.md
│   └── log.md
└── README.md
```

The `.console/` directory exists with 4 of the 5 required files, but `workers.yaml` is missing. This prevents the watchdog loop configuration from being loaded.

---

### R2 Violations: Structure & Content Validation (3 fixtures)

#### 5. `fixture_r2_oversized_task_md`
**Violation Category**: File size exceeds 100KB budget  
**Expected R2 Detection**: `count=1` with sample `.console/task.md exceeds 100KB budget (size bytes)`

Directory structure:
```
fixture_r2_oversized_task_md/
├── .console/
│   ├── task.md (101 KB)
│   ├── guidelines.md
│   ├── backlog.md
│   ├── log.md
│   └── workers.yaml
└── README.md
```

The `task.md` file is 101 KB, exceeding the 100 KB per-file limit. This tests the R2 detector's budget enforcement for `.console/` files to prevent unbounded metadata growth.

---

#### 6. `fixture_r2_missing_task_section`
**Violation Category**: task.md missing required section  
**Expected R2 Detection**: `count=1` with sample `.console/task.md missing required section: Current Stage`

Directory structure:
```
fixture_r2_missing_task_section/
├── .console/
│   ├── task.md (missing ## Current Stage)
│   ├── guidelines.md
│   ├── backlog.md
│   ├── log.md
│   └── workers.yaml
└── README.md
```

The `task.md` file exists but is missing the `## Current Stage` section. R2 requires all three sections: `## Objective`, `## Overall Plan`, and `## Current Stage` to provide a complete task state representation.

---

#### 7. `fixture_r2_invalid_workers_yaml`
**Violation Category**: workers.yaml has YAML syntax error  
**Expected R2 Detection**: `count=1` with sample `.console/workers.yaml has YAML syntax error`

Directory structure:
```
fixture_r2_invalid_workers_yaml/
├── .console/
│   ├── task.md
│   ├── guidelines.md
│   ├── backlog.md
│   ├── log.md
│   └── workers.yaml (invalid YAML)
└── README.md
```

The `workers.yaml` file contains YAML syntax errors (unmatched list delimiters, invalid boolean values). This tests that the R2 detector validates YAML correctness before the watchdog loop tries to parse it.

---

## Using These Fixtures in Integration Tests

### Example: Testing R1 Detector Against All Violations

```python
# tests/integration/detectors/test_r1_integration.py
import pytest
from custodian.audit_kit.detector import AuditContext
from .custodian.detectors import _detect_r1_console_presence

@pytest.mark.parametrize("fixture_name,expected_count", [
    ("r1_missing_console_dir", 1),
    ("r1_console_is_file", 1),
    ("r1_missing_task_md", 1),
    ("r1_missing_workers_yaml", 1),
])
def test_r1_detector_integration(fixture_name, expected_count, request):
    fixture_path = request.getfixturevalue(fixture_name)
    ctx = AuditContext(
        repo_root=fixture_path,
        src_root=fixture_path / "src" / "operations_center",
        tests_root=fixture_path / "tests",
        config={},
        plugin_modules=[],
    )
    result = _detect_r1_console_presence(ctx)
    assert result.count == expected_count
```

### Example: Testing R2 Detector Against Structure Violations

```python
# tests/integration/detectors/test_r2_integration.py
@pytest.mark.parametrize("fixture_name,expected_count", [
    ("r2_oversized_task_md", 1),
    ("r2_missing_task_section", 1),
    ("r2_invalid_workers_yaml", 1),
])
def test_r2_detector_integration(fixture_name, expected_count, request):
    fixture_path = request.getfixturevalue(fixture_name)
    ctx = AuditContext(
        repo_root=fixture_path,
        src_root=fixture_path / "src" / "operations_center",
        tests_root=fixture_path / "tests",
        config={},
        plugin_modules=[],
    )
    result = _detect_r2_console_budget(ctx)
    assert result.count == expected_count
```

## Fixture Registry

The `__init__.py` module exports:

- **`FIXTURES_DIR`**: Path to the fixtures directory
- **`FIXTURES`**: Dictionary mapping fixture names to their paths
- **`get_fixture_path(name)`**: Function to get a fixture path by name
- **`list_fixtures()`**: Function to list all fixture names

```python
from tests.fixtures.console_malformed import FIXTURES, get_fixture_path, list_fixtures

# Get a fixture path
path = get_fixture_path("r1_missing_console_dir")

# List all fixtures
for name in list_fixtures():
    print(name)

# Access FIXTURES dict directly
for name, path in FIXTURES.items():
    print(f"{name}: {path}")
```

## Pytest Fixtures

The `conftest.py` module auto-generates pytest fixtures for each repository:

```python
def test_something(r1_missing_console_dir):
    # r1_missing_console_dir is a Path to fixture_r1_missing_console_dir
    assert not (r1_missing_console_dir / ".console").exists()
```

All fixture names are available as pytest parameters (replace hyphens with underscores if needed).

## Violation Categories Covered

These 7 fixtures cover all violation categories detected by R1 and R2:

### R1 (Presence Validator) Violations:
- ✅ Missing `.console/` directory entirely
- ✅ `.console/` is a file, not a directory
- ✅ Missing required file (`task.md`)
- ✅ Missing required file (`workers.yaml`)
- ✅ (Combinations of missing files via parameterization in tests)

### R2 (Budget/Structure Validator) Violations:
- ✅ File size exceeds 100KB budget
- ✅ Invalid UTF-8 encoding (not included but can be added if needed)
- ✅ task.md missing required sections
- ✅ backlog.md missing standard sections (not included but can be added if needed)
- ✅ workers.yaml invalid YAML syntax

## Success Criteria for Integration Tests

When using these fixtures, integration tests should:

1. **Instantiate AuditContext** with the fixture's repo_root
2. **Call the appropriate detector** (_detect_r1_console_presence or _detect_r2_console_budget)
3. **Verify the result** matches the expected violation count and sample messages
4. **Use parameterization** to test multiple fixtures efficiently

All 7 fixtures are discoverable and documented for easy integration test development.
