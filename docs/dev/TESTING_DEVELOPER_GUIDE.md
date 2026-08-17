# Developer Guide: Writing Parallel-Safe Tests

This guide explains how to write tests that work correctly when executed in parallel with pytest-xdist. Follow these patterns to ensure your tests are safe across all worker configurations.

## Core Principles

### 1. Avoid Shared State

✅ **Good**: Each test is independent.  
❌ **Bad**: Tests share module-level variables or global state.

```python
# ❌ Bad — module-level state persists across tests
_recorder = None

def make_record():
    global _recorder
    if _recorder is None:
        _recorder = ExecutionRecorder()
    return _recorder

# ✅ Good — each test gets its own instance
def make_record():
    return ExecutionRecorder()

# ✅ Better — use pytest fixtures
@pytest.fixture
def recorder():
    return ExecutionRecorder()

def test_something(recorder):
    result = recorder.record(...)
    assert result
```

### 2. Use pytest's Temporary Directories

✅ **Good**: Use `tmp_path` or `tmp_path_factory`.  
❌ **Bad**: Use `tempfile.mkdtemp()` manually.

```python
# ❌ Bad — manual directory creation isn't worker-aware
import tempfile

@pytest.fixture
def work_dir():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)

# ✅ Good — pytest handles cleanup per worker
@pytest.fixture
def work_dir(tmp_path):
    yield tmp_path
    # Automatic cleanup by pytest

# ✅ Also good — for cross-test fixtures
@pytest.fixture(scope="session")
def shared_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("shared")
```

**Why?** pytest's `tmp_path` and `tmp_path_factory` are worker-aware. Each worker gets its own isolated directory tree, preventing cross-worker interference.

### 3. Isolate Fixture Scope Correctly

Use the right fixture scope to prevent workers from stepping on each other:

```python
# ✅ function scope (default) — new instance per test
@pytest.fixture
def config():
    return Config()

# ✅ class scope — one instance per test class
@pytest.fixture(scope="class")
def db_connection():
    db = Database(":memory:")
    yield db
    db.close()

# ⚠️  module scope — safe if immutable
@pytest.fixture(scope="module")
def immutable_data():
    return {"a": 1, "b": 2}  # Immutable dict — safe

# ❌ session scope — risky in parallel (all workers share instance)
@pytest.fixture(scope="session")
def shared_service():
    # If this has mutable state, workers will interfere
    service = Service()
    yield service
    service.cleanup()
```

**Best practice**: Default to `scope="function"` (function-level scope). Only use broader scopes for read-only or immutable data.

### 4. Mock External Dependencies, Not Internal Code

✅ **Good**: Mock external services and subprocess calls.  
❌ **Bad**: Mock internal functions in ways that depend on import order.

```python
# ✅ Good — mock the boundary
from unittest.mock import patch, MagicMock

@patch("operations_center.backends.github_api.requests.post")
def test_github_integration(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    result = post_to_github(...)
    assert result.ok

# ❌ Fragile — mocks applied to imported function references
# This breaks when different workers import at different times
from operations_center.backends.github_api import post_to_github

@patch("operations_center.backends.github_api.post_to_github")
def test_something(mock_fn):
    # This mock might not apply to all imports
    ...
```

### 5. Avoid Monkeypatching Global State

✅ **Good**: Use mock/patch context managers.  
❌ **Bad**: Monkeypatch at module level or in conftest without cleanup.

```python
# ❌ Bad — global patch that affects all workers
import sys
sys.path.insert(0, "/some/path")

# ✅ Good — scoped patch
@pytest.fixture
def custom_path():
    import sys
    sys.path.insert(0, "/some/path")
    yield
    sys.path.pop(0)

# ✅ Better — use monkeypatch fixture (automatic cleanup)
def test_something(monkeypatch):
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setattr(obj, "attr", value)
```

### 6. Be Careful with Fixtures and Imports

When a conftest.py defines fixtures, each worker will import that module. If the module has side effects, they'll run on each worker:

```python
# conftest.py

# ❌ Bad — side effect on import
print("Initializing test environment")  # Prints 4 times (once per worker)
os.chdir("/some/path")  # Changes cwd on each worker independently

# ✅ Good — side effects only in fixtures
@pytest.fixture(scope="session")
def initialize_env():
    print("Initializing")
    os.chdir("/some/path")
    yield
    os.chdir(original_cwd)  # Cleanup

# Then in your test:
def test_something(initialize_env):
    # Now the environment is set up
    ...
```

## Fixture Patterns for Parallel Tests

### Function-Scope Fixtures (Safe Default)

```python
@pytest.fixture
def fresh_config():
    """Create a new config for each test."""
    return Config(defaults=True)

def test_config_merge(fresh_config):
    fresh_config.update({"key": "value"})
    assert fresh_config["key"] == "value"
```

Each test gets its own instance. No sharing = no race conditions. ✅

### Immutable Module-Scope Fixtures

```python
@pytest.fixture(scope="module")
def test_data():
    """Immutable test data shared across all tests in this module."""
    return {"read_only": True, "values": [1, 2, 3]}

def test_uses_data(test_data):
    # Safe because we never modify test_data
    assert test_data["read_only"] is True
```

Read-only data can be shared. Only use if tests never modify it. ✅

### Session-Scope with Mutation (Use Cautiously)

```python
@pytest.fixture(scope="session")
def database():
    """Session-scoped DB connection.
    
    Safe because:
    - We use pytest-xdist's built-in database locking
    - Tests use transactions that auto-rollback
    - No persistent state between test runs
    """
    db = Database(":memory:")
    yield db
    db.close()

def test_query(database):
    # Each test runs in a transaction
    with database.transaction():
        database.insert("users", {"id": 1, "name": "Alice"})
        result = database.query("users")
        assert len(result) == 1
    # Transaction auto-rolls back
```

Only safe if mutations are isolated per test (e.g., via transactions). ⚠️

### Factory Fixtures for Repeated Creation

```python
@pytest.fixture
def config_factory():
    """Factory for creating fresh configs on demand."""
    def _make_config(**overrides):
        defaults = {"timeout": 30, "retries": 3}
        defaults.update(overrides)
        return Config(**defaults)
    return _make_config

def test_timeout_config(config_factory):
    cfg = config_factory(timeout=60)
    assert cfg.timeout == 60

def test_retries_config(config_factory):
    cfg = config_factory(retries=5)
    assert cfg.retries == 5
```

Each test creates only what it needs. Minimal sharing = safe. ✅

## Common Pitfalls

### Pitfall 1: File System Collisions

❌ **Bad**: Hardcoding paths

```python
def test_writes_file():
    with open("/tmp/test_output.json", "w") as f:
        f.write(json.dumps({"test": "data"}))
    # Other workers may overwrite this file!
```

✅ **Good**: Using pytest's tmp_path

```python
def test_writes_file(tmp_path):
    output = tmp_path / "output.json"
    with open(output, "w") as f:
        f.write(json.dumps({"test": "data"}))
    assert output.read_text()  # Only this test sees this file
```

### Pitfall 2: Mock Side Effects

❌ **Bad**: Mocks that affect subsequent tests

```python
@patch("os.environ", {"DEBUG": "1"})
def test_debug_mode():
    # ❌ Other workers share os.environ changes
    assert os.environ["DEBUG"] == "1"
```

✅ **Good**: Using monkeypatch or context managers

```python
def test_debug_mode(monkeypatch):
    monkeypatch.setenv("DEBUG", "1")
    assert os.environ["DEBUG"] == "1"
    # Auto-cleanup: other tests won't see this change
```

### Pitfall 3: Process Spawning

❌ **Bad**: Spawning subprocesses with shared names

```python
def test_spawns_server():
    # ❌ All workers try to use port 8000!
    proc = subprocess.Popen(["python", "server.py", "--port", "8000"])
    assert requests.get("http://localhost:8000").ok
```

✅ **Good**: Using dynamic ports or fixtures

```python
@pytest.fixture
def unused_port():
    """Return an unused network port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def test_spawns_server(unused_port, tmp_path):
    proc = subprocess.Popen(
        ["python", "server.py", "--port", str(unused_port)],
        cwd=tmp_path
    )
    try:
        assert requests.get(f"http://localhost:{unused_port}").ok
    finally:
        proc.terminate()
```

### Pitfall 4: Object State in conftest

❌ **Bad**: Storing mutable state

```python
# conftest.py
_test_results = []  # ❌ Shared across workers

@pytest.fixture
def collector():
    def collect(value):
        _test_results.append(value)
    return collect
```

✅ **Good**: Using per-test fixtures

```python
# conftest.py
@pytest.fixture
def results():
    """Per-test results list."""
    return []

@pytest.fixture
def collector(results):
    def collect(value):
        results.append(value)
    return collect
```

## Testing Your Tests for Parallelization

### Check Your Test for Parallel Safety

1. **Run serially first**: `pytest tests/path/to/test.py -n 1`
2. **Run in parallel**: `pytest tests/path/to/test.py -n 4`
3. **Run multiple times**: `for i in {1..5}; do pytest -n 4 --tb=short; done`

If results differ between serial and parallel, you have a shared-state bug.

### Audit Your Fixtures

```bash
# Find all fixture definitions
grep -r "@pytest.fixture" tests/

# Check for mutable default fixtures
grep -r "@pytest.fixture" tests/ | grep -v "scope="  # These are function-scoped

# Find conftest files (places where shared fixtures live)
find tests/ -name "conftest.py"
```

## Reference: Fixture Scope Decisions

| Scope | Sharing | When to Use | Risk |
|-------|---------|-----------|------|
| **function** (default) | Per test | Always; safe default | None — each test isolated |
| **class** | Per class | Class-level setup/teardown | Low — still per test class |
| **module** | Per file | Read-only data | Low if immutable |
| **package** | Per directory | Rare; heavy resources | Medium — large scope |
| **session** | All tests, all workers | Heavy resources, read-only | High — shared across workers |

## Running Tests in Your PR

When submitting code:

1. **Run locally in parallel**: `pytest -n auto --dist=loadscope`
2. **Run locally serially**: `pytest` (ensures no hidden dependencies on test order)
3. **CI runs automatically**: GitHub Actions runs parallel tests in `pytest tests/unit -n auto --dist=loadscope`

Both serial and parallel should pass. If only one passes, you have a race condition.

## Getting Help

- **Flaky test in parallel?** → See [docs/TESTING_TROUBLESHOOTING.md](TESTING_TROUBLESHOOTING.md)
- **Not sure if fixture is safe?** → Default to function scope
- **Need to share state?** → Use explicit fixtures, not module globals
