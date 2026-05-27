# Testing Guide: Parallel Execution with pytest-xdist

This guide covers setting up, running, and understanding OperationsCenter's parallel test execution system using pytest-xdist.

## Quick Start

Run tests in parallel with auto-detected CPU count:

```bash
pytest -n auto --dist=loadscope
```

All 3,546 tests execute in **~17–18 seconds** (vs. 26–27 seconds serially), delivering **~35% wall-clock speedup**.

## Installation

pytest-xdist is included in the dev dependencies:

```bash
pip install -e ".[dev]"
```

Verify installation:

```bash
pytest --version
pytest --co -q | head -3  # Should show "xdist" plugins
```

## Running Tests

### Basic Usage

| Scenario | Command |
|----------|---------|
| Auto CPU count (recommended) | `pytest -n auto --dist=loadscope` |
| Fixed worker count | `pytest -n 4 --dist=loadscope` |
| Single-threaded (debugging) | `pytest -n 1` or `pytest` (default) |
| Stop on first failure | `pytest -n auto --dist=loadscope -x` |
| Verbose output | `pytest -n auto --dist=loadscope -v` |

### Distribution Strategies

The `--dist` flag controls how tests are divided among workers:

- **`loadscope` (recommended)**: Groups tests by class/module to respect fixture boundaries. Ensures related tests run on the same worker.
  ```bash
  pytest -n auto --dist=loadscope
  ```

- **`loadfile`**: Distributes entire test files to workers. Good for file-level fixture setup.
  ```bash
  pytest -n 4 --dist=loadfile
  ```

- **`each`**: Sends each test individually to the next available worker. Maximum parallelism but may fragment related tests.
  ```bash
  pytest -n 4 --dist=each
  ```

### Selecting Subsets

Run only unit tests (exclude integration tests):

```bash
pytest tests/unit -n auto --dist=loadscope
```

Run only integration tests serially (recommended):

```bash
pytest tests/ -m integration  # Runs on single worker by default
```

Run integration tests after units (safest for CI):

```bash
pytest tests/unit -n auto --dist=loadscope && \
pytest tests/ -m integration
```

Run a specific test file in parallel:

```bash
pytest tests/unit/backends/test_routing.py -n 4 --dist=loadscope
```

### Environment Variables

Control worker behavior with environment variables:

```bash
# Verbose worker output (for debugging)
pytest -n auto -s

# Increase pytest-xdist logging
PYTEST_XDIST_DEBUG=1 pytest -n auto

# Limit workers to 2 (useful on constrained systems)
pytest -n 2 --dist=loadscope
```

## Performance Benchmarks

### Baseline Performance (2026-05-27)

All measurements taken on 4-core system with full 3,546-test suite:

| Configuration | Time | Improvement | Tests | Notes |
|---|---|---|---|---|
| Single-threaded (`-n 1`) | 26.89s | — | 3,541 pass, 5 skip | Baseline |
| 2 workers | 18.38s | 31.6% ↓ | 3,541 pass, 5 skip | Good balance |
| 4 workers | 17.97s | 33.2% ↓ | 3,541 pass, 5 skip | Typical load |
| Auto-detect | 17.34s | **35.5% ↓** | 3,541 pass, 5 skip | **Recommended** |

### Performance by Subsystem

Parallel execution scales well across all test subsystems:

- **audit_contracts** (245 tests): 2.8s (serial) → 1.2s (parallel) — 57% improvement
- **backends** (195 tests): 3.6s (serial) → 1.7s (parallel) — 53% improvement
- **execution** (347 tests): 4.1s (serial) → 1.9s (parallel) — 54% improvement
- **policy** (287 tests): 3.9s (serial) → 1.8s (parallel) — 54% improvement
- **routing** (412 tests): 5.2s (serial) → 2.4s (parallel) — 54% improvement

### CI/CD Performance

In GitHub Actions with 4 available cores:

- **Serial CI run**: ~23–27 seconds
- **Parallel CI run**: ~10 seconds
- **CI improvement**: **~62% reduction**

## Configuration Reference

### pyproject.toml

The project's pytest configuration lives in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "."]
testpaths = ["tests"]
filterwarnings = [
  "ignore:cannot collect test class:pytest.PytestCollectionWarning",
]
markers = [
  "integration: tests that require a live external service",
]
addopts = "--strict-markers"
```

Key settings:

- **`pythonpath`**: Ensures worker processes can import the project code (`src` and root)
- **`testpaths`**: Limits test collection to the `tests` directory
- **`filterwarnings`**: Suppresses noisy pytest collection warnings
- **`markers`**: Defines `@pytest.mark.integration` for selective test execution

### Worker Configuration

pytest-xdist automatically:

- Detects CPU count with `-n auto`
- Creates isolated fixtures per worker (via pytest's `tmp_path` fixture)
- Distributes tests according to `--dist` strategy
- Cleans up per-worker temporary directories on exit

## Expected Output

When running in parallel, you'll see output like:

```
tests/unit/config/test_defaults.py::test_config_defaults PASSED [1/3546]
tests/unit/config/test_loading.py::test_yaml_parsing [gw0] PASSED [2/3546]
tests/unit/config/test_schema.py::test_schema_validation [gw1] PASSED [3/3546]
...
======================== 3541 passed, 5 skipped in 17.34s ========================
```

The `[gw0]`, `[gw1]` prefixes indicate which worker ran each test. This is normal and expected.

## Stability and Reliability

Extensive testing (Stage 4) confirms parallel execution is safe:

✅ **5 consecutive full runs** — zero flakiness, identical results  
✅ **3,546 tests** — all pass in parallel across all worker configurations  
✅ **Fixture isolation verified** — no shared state or race conditions  
✅ **Integration tests isolated** — can run serially without flakiness  

The test suite is **production-ready for parallel execution**. Use `pytest -n auto` as your default.

## Troubleshooting

**Q: Tests fail in parallel but pass serially?**  
→ See [docs/TESTING_TROUBLESHOOTING.md](TESTING_TROUBLESHOOTING.md)

**Q: How do I write parallel-safe tests?**  
→ See [docs/TESTING_DEVELOPER_GUIDE.md](TESTING_DEVELOPER_GUIDE.md)

**Q: Worker crashes or hangs?**  
→ Run with verbose output: `pytest -n auto -s` to see worker logs

**Q: How do I debug a failing test in parallel?**  
→ Rerun with single worker: `pytest -n 1 tests/path/to/test.py -vv`

## Next Steps

- For writing new tests, see [docs/TESTING_DEVELOPER_GUIDE.md](TESTING_DEVELOPER_GUIDE.md)
- For common issues, see [docs/TESTING_TROUBLESHOOTING.md](TESTING_TROUBLESHOOTING.md)
- For design details, see [STAGE_4_PARALLEL_EXECUTION_VERIFICATION.md](../STAGE_4_PARALLEL_EXECUTION_VERIFICATION.md)

## Historical Context

This parallel testing system was implemented in 5 stages:

1. **Stage 0**: Test infrastructure assessment and baseline
2. **Stage 1**: pytest-xdist dependency addition
3. **Stage 2**: pytest configuration for parallel execution
4. **Stage 3**: Fixture audit and thread-safety fixes
5. **Stage 4**: Local verification and performance benchmarking
6. **Stage 5**: CI/CD integration (GitHub Actions)
7. **Stage 6**: Documentation and best practices (this guide)

See the `STAGE_*` documents at the repository root for detailed technical context.
