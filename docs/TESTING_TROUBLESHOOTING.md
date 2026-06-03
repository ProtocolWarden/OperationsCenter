# Testing Troubleshooting: Parallel Execution Issues

This guide helps diagnose and fix problems with parallel test execution using pytest-xdist.

## Quick Diagnosis

Use this flowchart to identify your issue:

```
Test behavior problem?
├─ Passes serially, fails in parallel → Shared state bug (see "Shared State Issues")
├─ Flaky results (sometimes passes/fails) → Race condition (see "Race Conditions")
├─ Worker crashes or hangs → Worker failure (see "Worker Crashes/Hangs")
├─ Import errors across workers → Module initialization (see "Import/Module Issues")
├─ Performance worse than baseline → System load (see "Performance Degradation")
└─ pytest-xdist not installed → Installation (see "Installation Issues")
```

## Installation Issues

### pytest-xdist not installed or not found

**Symptom**: `ERROR: usage: pytest [OPTIONS] [ITEM] ... error: unrecognized arguments: -n`

**Diagnosis**:
```bash
pytest --version  # Should show xdist
python -c "import xdist; print(xdist.__version__)"
```

**Solution**:
```bash
pip install -e ".[dev]"
python -m pytest --co -q | head -5  # Verify xdist loaded
```

### Wrong version installed

**Symptom**: `pytest-xdist >= 3.0 required` or xdist options don't work

**Diagnosis**:
```bash
pip show pytest-xdist
```

**Solution**:
```bash
pip install --upgrade "pytest-xdist>=3.0"
```

---

## Shared State Issues

### Problem: Passes serially (`-n 1`), fails in parallel (`-n auto`)

This is the classic sign of shared mutable state. Multiple workers are stepping on each other's data.

**Step 1: Identify the shared state**

The failing test usually has module-level or class-level state:

```python
# ❌ CULPRIT: Module-level _recorder
_recorder = None

def make_record():
    global _recorder
    if _recorder is None:
        _recorder = ExecutionRecorder()
    return _recorder
```

**Step 2: Reproduce the race**

```bash
pytest tests/path/to/failing_test.py -n 1  # Passes
pytest tests/path/to/failing_test.py -n 2  # Fails
```

**Step 3: Fix the shared state**

Option A: Move to a fixture (preferred):

```python
@pytest.fixture
def recorder():
    return ExecutionRecorder()

def test_something(recorder):
    result = recorder.record(...)
    assert result
```

Option B: Create on demand:

```python
def make_record():
    return ExecutionRecorder()  # New instance every time

def test_something():
    recorder = make_record()
    result = recorder.record(...)
    assert result
```

Option C: Use thread-local storage (advanced):

```python
import threading

_local = threading.local()

def make_record():
    if not hasattr(_local, 'recorder'):
        _local.recorder = ExecutionRecorder()
    return _local.recorder
```

### Problem: Fixture scope too broad

**Symptom**: Different tests in same module interfere; one test's state leaks into the next

**Diagnosis**:

Check your conftest.py for fixtures with `scope="module"` or `scope="session"`:

```bash
grep -n "scope=" tests/**/conftest.py
```

**Fix**:

Change to function scope (the default):

```python
# ❌ Bad: Module scope with mutable state
@pytest.fixture(scope="module")
def config():
    return Config()

# ✅ Good: Function scope (new instance per test)
@pytest.fixture
def config():
    return Config()
```

### Problem: Global state in conftest.py

**Symptom**: Test order affects results; tests fail when run in different order

**Diagnosis**:

Look for module-level code in conftest.py that runs on import:

```python
# conftest.py
os.environ["TEST_MODE"] = "1"  # Runs once per worker, might not be what you want
```

**Fix**:

Move initialization to fixtures:

```python
# conftest.py

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment once per worker."""
    original = os.environ.get("TEST_MODE")
    os.environ["TEST_MODE"] = "1"
    yield
    if original is None:
        del os.environ["TEST_MODE"]
    else:
        os.environ["TEST_MODE"] = original
```

---

## Race Conditions

### Problem: Flaky tests (pass ~70% of the time, fail ~30%)

**Symptom**: `pytest tests/path -n 4` sometimes passes, sometimes fails

**Root cause**: Multiple workers accessing the same resource without synchronization.

**Step 1: Reproduce consistently**

Run 10 times and count failures:

```bash
for i in {1..10}; do pytest tests/path/to/flaky_test.py -n 4 -q; done
```

If you see failures, you have a race condition.

**Step 2: Check for file collisions**

Tests writing to shared files:

```python
# ❌ Bad: Two workers write to same file
with open("/tmp/test_results.json", "w") as f:
    json.dump(result, f)

# ✅ Good: Use pytest's tmp_path
def test_something(tmp_path):
    output = tmp_path / "results.json"
    with open(output, "w") as f:
        json.dump(result, f)
```

**Step 3: Check for port collisions**

If tests spawn servers:

```python
# ❌ Bad: All workers try to use port 8000
proc = subprocess.Popen(["server", "--port", "8000"])

# ✅ Good: Use random/dynamic ports
import socket
sock = socket.socket()
sock.bind(('', 0))
port = sock.getsockname()[1]
sock.close()
proc = subprocess.Popen(["server", "--port", str(port)])
```

**Step 4: Check for database locks**

If tests access a database:

```python
# ❌ Bad: SQLite in non-WAL mode locks on writes
db = sqlite3.connect(":memory:")

# ✅ Good: Enable WAL mode for concurrent access
db = sqlite3.connect(":memory:")
db.execute("PRAGMA journal_mode=WAL")
```

### Problem: Tests pass but assertions are wrong

**Symptom**: Test passes, but the assertion is somehow satisfied incorrectly

**Diagnosis**: You're testing wrong thing due to shared state:

```python
_results = []

def test_first():
    _results.append("first")
    assert "first" in _results  # Passes

def test_second():
    _results.append("second")
    assert "second" in _results  # Passes because "first" is still there!
```

**Fix**: Isolate state:

```python
@pytest.fixture
def results():
    return []

def test_first(results):
    results.append("first")
    assert results == ["first"]  # Now correctly fails if state is shared

def test_second(results):
    results.append("second")
    assert results == ["second"]  # Passes only with isolated state
```

---

## Worker Crashes/Hangs

### Problem: Worker process crashes with no output

**Symptom**: `pytest -n auto` hangs or kills a worker silently

**Step 1: Get worker output**

```bash
pytest -n auto -s --tb=long 2>&1 | tee test_output.log
```

**Step 2: Check for deadlocks**

Look for tests that:

- Create locks and never release them
- Wait for resources that are also locked by other workers
- Call `sys.exit()` or `os._exit()`

```python
# ❌ Bad: Deadlock potential
import threading
lock = threading.Lock()

def test_first():
    lock.acquire()  # Never released!

# ✅ Good: Use context managers
def test_first():
    with lock:
        # Do work
        pass
```

**Step 3: Check for resource leaks**

```bash
# Run with verbose worker output
pytest -n auto --capture=no -s
```

Look for warnings about unclosed files or connections.

**Step 4: Run with single worker to isolate**

```bash
pytest -n 1 tests/path/to/problematic_test.py -vv
```

If it passes with `-n 1` but hangs with `-n auto`, the problem is multi-worker specific.

### Problem: Worker timeout

**Symptom**: `ERROR: Worker excvars did not receive heartbeat`

The worker is taking too long or hanging.

**Solution**:

Increase timeout (xdist default is 60 seconds):

```bash
pytest -n auto --timeout=120 tests/
```

Or identify the slow test:

```bash
pytest -n auto --durations=10  # Show 10 slowest tests
```

Then optimize it or mark it for serial execution (see "Slow Tests" below).

### Problem: SIGKILL or segfault in worker

**Symptom**: Worker dies with segfault or is killed by OS

**Diagnosis**: Usually a C extension or system library issue.

```bash
pytest -n auto -s --tb=native 2>&1
```

**Solution**:

Run that specific test serially:

```bash
pytest -n 1 tests/path/to/segfaulting_test.py
```

If it passes, the bug is in the C extension's thread safety. If it fails, the extension itself is broken.

---

## Import/Module Issues

### Problem: ImportError in parallel workers

**Symptom**: `ModuleNotFoundError` with `-n auto`, works with `-n 1`

**Root cause**: Workers don't have correct `PYTHONPATH`.

**Diagnosis**:

```bash
pytest --collect-only -q 2>&1 | grep -i error
```

**Solution**:

Verify `pyproject.toml` has correct pythonpath:

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "."]  # ✅ Both needed for multi-worker
```

If missing, add it:

```bash
# Edit pyproject.toml and add above block
```

Verify it works:

```bash
pytest tests/some_test.py -n auto
```

### Problem: Fixture not found in worker

**Symptom**: `fixture '<name>' not found` error in workers only

**Root cause**: conftest.py not imported by all workers.

**Diagnosis**:

Check conftest.py locations:

```bash
find tests/ -name "conftest.py"
```

**Solution**:

Each test directory needs conftest.py or access to parent conftest. pytest automatically discovers conftest files up the directory tree.

If a specific fixture is missing, check:

1. conftest.py is in the right directory
2. conftest.py defines the fixture
3. conftest.py is in `testpaths` scope

---

## Performance Degradation

### Problem: Parallel is slower than serial

**Symptom**: `pytest -n auto` takes **longer** than `pytest -n 1`

**Causes**:

1. **Overhead exceeds benefit**: Project too small
2. **I/O bottleneck**: All workers competing for disk
3. **Resource exhaustion**: System out of memory/CPU
4. **Too many workers**: Thrashing with context switches

**Step 1: Check system load**

```bash
# Before running tests
top -b -n 1 | head -20

# During test run (in another terminal)
watch -n 0.1 'top -b -n 1 | head -20'
```

Look for:
- Memory near capacity (>80%)
- CPU maxed out (>100% per core)
- High context switch rate

**Step 2: Use fewer workers**

```bash
pytest -n 2 --dist=loadscope  # Instead of auto
```

**Step 3: Run without verbose output**

```bash
pytest -n auto -q  # Instead of pytest -n auto -v
```

Printing slows things down significantly in parallel.

### Problem: Specific test is slow in parallel

**Symptom**: One test takes 30s in parallel but 5s serially

**Diagnosis**: Likely I/O contention or incorrect test classification.

**Check:**

```bash
pytest tests/path/to/slow_test.py -n 1 --durations=1
pytest tests/path/to/slow_test.py -n 4 --durations=1
```

If parallelized run is slower, run serially:

```python
# Mark this test to always run serially
@pytest.mark.integration  # or create custom marker
def test_slow_operation():
    ...
```

Then run:

```bash
pytest tests/ -n 4 -m "not integration"  # Fast in parallel
pytest tests/ -m "integration"            # Slow tests serially
```

---

## Common Solutions by Issue Type

| Issue | Quick Fix | Long-term Fix |
|-------|-----------|---------------|
| **Fails parallel, passes serial** | Check for `global` variables | Use fixtures, avoid module state |
| **Flaky (random pass/fail)** | Run 10x: `for i in {1..10}; do pytest -n 4; done` | Fix race conditions, use locks properly |
| **Worker crashes** | Run with `-n 1` to isolate | Check for deadlocks, resource leaks |
| **ImportError in parallel** | Check `pythonpath` in pyproject.toml | Ensure conftest.py in right locations |
| **Slower than serial** | Use `-n 2` instead of auto | Profile with `--durations=10` |

---

## Getting More Help

### Enable verbose worker logging

```bash
PYTEST_XDIST_DEBUG=1 pytest -n auto -s
```

### Run with maximum verbosity

```bash
pytest -n auto -vv --tb=long -s 2>&1 | tee debug.log
```

### Check pytest-xdist docs

https://pytest-xdist.readthedocs.io/

### Report a new issue

If you find a pattern not covered here:

1. Isolate to minimal reproducible test
2. Include output from: `pytest --version` and `pytest --co -q`
3. Run with `-n 1` and `-n auto` and compare
4. Open an issue with reproduction steps
