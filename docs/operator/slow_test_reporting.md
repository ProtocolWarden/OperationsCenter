# Slow Test Reporting Guide

## Overview

The slow-test reporting feature provides real-time visibility into test performance characteristics. It automatically identifies and reports tests that exceed a configurable duration threshold, helping teams detect performance regressions and maintain efficient CI/CD pipelines.

**Key capabilities:**
- Per-test duration tracking with millisecond precision
- Configurable threshold-based detection
- Support for pre-marked slow tests (`@pytest.mark.slow`)
- JSON export for CI integration and historical analysis
- Zero breaking changes to existing test infrastructure
- xdist compatibility with parallel execution

## Quick Start

### Basic Usage (Default 1.0s Threshold)

```bash
# Run tests with default 1.0 second threshold
pytest tests/

# Example output when slow tests are detected:
# SLOW TEST THRESHOLD WARNING (threshold: 1.0s)
# ⚠️  2 test(s) exceeded the threshold:
#     1.872s  tests/unit/backend/test_async.py::test_concurrent_operations
#     1.524s  tests/unit/storage/test_persistence.py::test_large_dataset_handling
# 📌 1 test(s) marked @pytest.mark.slow:
#     0.963s  tests/integration/test_e2e_workflow.py::test_full_pipeline
# Summary: 3/127 slow tests
#   Average duration: 0.145s
#   Max duration: 1.872s
```

### Custom Threshold (Detect Tests >100ms)

```bash
# Find tests exceeding 100 milliseconds
pytest tests/ --slow-threshold=0.1

# Output shows all tests > 0.1s:
# SLOW TEST THRESHOLD WARNING (threshold: 0.1s)
# ⚠️  14 test(s) exceeded the threshold:
#     0.963s  tests/unit/...
#     0.221s  tests/unit/...
#     ...
```

### Very Strict Threshold (Detect Tests >10ms)

```bash
# Identify even minor performance issues
pytest tests/ --slow-threshold=0.01

# Useful during performance optimization work
pytest tests/unit/core/ --slow-threshold=0.01
```

## Configuration Examples

### Example 1: Development Workflow

Use a relaxed threshold during development (focus on obvious bottlenecks):

```bash
# Check for seriously slow tests (>500ms) during development
pytest tests/ --slow-threshold=0.5
```

**Use case:** Local development where you want to catch only egregious slowdowns without noise.

### Example 2: CI/CD Pipeline

Integrate into your CI with a moderate threshold to catch regressions:

```yaml
# .github/workflows/test.yml
- name: Run tests with performance monitoring
  run: |
    pytest tests/ --slow-threshold=0.2 --slow-report=slow_tests.json
    
- name: Archive slow test report
  if: always()
  uses: actions/upload-artifact@v3
  with:
    name: slow_tests_report
    path: slow_tests.json
```

**Use case:** CI pipeline automatically generates reports for performance analysis without failing the build.

### Example 3: Performance Optimization Sprint

Use strict threshold during optimization work:

```bash
# Baseline: identify all tests >50ms
pytest tests/core/ --slow-threshold=0.05 --slow-report=baseline.json

# After optimization: compare results
pytest tests/core/ --slow-threshold=0.05 --slow-report=optimized.json

# Analyze improvement
jq '.slow_tests | length' baseline.json optimized.json
```

**Use case:** Track the impact of performance improvements with detailed JSON exports.

### Example 4: Pre-marked Slow Tests

Mark expected slow tests explicitly:

```python
# tests/integration/test_migration.py
import pytest

@pytest.mark.slow
def test_database_migration():
    """This test takes 2.5s but is expected to be slow."""
    # Large dataset processing...
    pass

@pytest.mark.slow
@pytest.mark.parametrize("size", [1000, 10000, 100000])
def test_scaling_behavior(size):
    """Test performance at different scales."""
    pass
```

Usage:

```bash
# Run full suite, clearly distinguishing marked tests
pytest tests/ --slow-threshold=0.2

# Output:
# ⚠️  3 test(s) exceeded the threshold:
#     0.963s  tests/unit/performance/test_sorting.py::test_quicksort_worst_case
#     0.215s  tests/unit/api/test_concurrent.py::test_race_condition_handling
#     0.105s  tests/integration/test_cache.py::test_cache_eviction
# 📌 5 test(s) marked @pytest.mark.slow:
#     2.512s  tests/integration/test_migration.py::test_database_migration
#     1.842s  tests/integration/test_migration.py::test_scaling_behavior[100000]
#     ...
```

### Example 5: Parallel Execution

Works seamlessly with pytest-xdist:

```bash
# Run with 4 parallel workers + slow test tracking
pytest tests/ -n 4 --slow-threshold=0.1

# Output aggregates results across all workers:
# SLOW TEST THRESHOLD WARNING (threshold: 0.1s)
# ⚠️  8 test(s) exceeded the threshold:
#     0.563s  tests/unit/worker/test_async.py::test_parallel_task_scheduling
#     ...
```

**Note:** Duration tracking is accurate even with parallel execution; xdist workers report back to the main process at session end.

## JSON Export Integration

Export results for programmatic analysis and CI integration:

```bash
# Generate JSON report
pytest tests/ --slow-threshold=0.1 --slow-report=slow_tests.json
```

### JSON Schema (v1.0)

```json
{
  "version": "1.0",
  "threshold_seconds": 0.1,
  "total_tests": 127,
  "slow_tests": [
    {
      "nodeid": "tests/unit/backend/test_async.py::test_concurrent_operations",
      "duration_seconds": 0.963,
      "is_marked": false,
      "category": "threshold_exceeded"
    },
    {
      "nodeid": "tests/integration/test_e2e.py::test_full_pipeline",
      "duration_seconds": 1.524,
      "is_marked": true,
      "category": "marked_slow"
    }
  ],
  "statistics": {
    "slow_test_count": 12,
    "average_duration": 0.156,
    "max_duration": 1.872,
    "min_duration": 0.101
  }
}
```

### Processing JSON Reports

Extract specific information:

```bash
# Count slow tests
jq '.statistics.slow_test_count' slow_tests.json

# Find slowest test
jq '.slow_tests | max_by(.duration_seconds) | {nodeid, duration_seconds}' slow_tests.json

# Export just marked tests
jq '.slow_tests[] | select(.is_marked == true) | .nodeid' slow_tests.json

# Calculate average slow test duration
jq '.slow_tests | map(.duration_seconds) | add / length' slow_tests.json
```

## Performance Regression Detection

Track performance over time by comparing JSON reports:

```bash
#!/bin/bash
# save_and_compare_slow_tests.sh

# Run baseline
pytest tests/ --slow-threshold=0.1 --slow-report=reports/baseline.json

# Run after changes
pytest tests/ --slow-threshold=0.1 --slow-report=reports/current.json

# Compare
echo "Baseline slow tests: $(jq '.statistics.slow_test_count' reports/baseline.json)"
echo "Current slow tests:  $(jq '.statistics.slow_test_count' reports/current.json)"

# Flag regressions
BASELINE=$(jq '.statistics.max_duration' reports/baseline.json)
CURRENT=$(jq '.statistics.max_duration' reports/current.json)

if (( $(echo "$CURRENT > $BASELINE * 1.5" | bc -l) )); then
    echo "⚠️  REGRESSION: Slowest test increased from ${BASELINE}s to ${CURRENT}s"
    exit 1
fi
```

## Threshold Selection Guide

### Development (Local Testing)

| Threshold | Use Case | Example |
|-----------|----------|---------|
| 1.0s | Default catch obvious issues | `pytest tests/` |
| 0.5s | Find problematic tests | `pytest tests/unit/` |
| 0.1s | Focus on unit test performance | `pytest tests/unit/core/` |

### CI/CD Pipeline

| Threshold | Use Case | Example |
|-----------|----------|---------|
| 0.2s | Catch regressions | Standard CI run |
| 0.1s | Strict performance gate | Performance-critical modules |
| 0.05s | Aggressive optimization tracking | Performance sprint |

### Investigation & Optimization

| Threshold | Use Case | Example |
|-----------|----------|---------|
| 0.01s | Fine-grained analysis | Profiling specific module |
| 0.001s | Microsecond-level tracking | Benchmark optimization |

## Troubleshooting

### Issue: Too Many Slow Tests Reported

**Symptom:** Threshold too low for current test performance

**Solution:** Adjust threshold upward
```bash
# Current environment might have slower hardware
pytest tests/ --slow-threshold=0.5  # More relaxed threshold
```

### Issue: No Slow Tests Detected

**Symptom:** Tests run quickly, no warnings

**Possible causes:**
1. Tests are genuinely fast (good!)
2. Threshold is too high
3. Tests were skipped

**Solution:** Lower threshold to verify feature is working
```bash
pytest tests/ --slow-threshold=0.01 -v  # Very low threshold to verify
```

### Issue: Missing JSON Report

**Symptom:** `--slow-report` flag doesn't create file

**Solution:** Ensure path is writable
```bash
# Create directory if needed
mkdir -p reports/
pytest tests/ --slow-report=reports/slow_tests.json

# Verify file created
ls -lh reports/slow_tests.json
```

## Integration with Development Workflows

### Pre-commit Hook

Catch slow tests before pushing:

```bash
#!/bin/bash
# .git/hooks/pre-commit

echo "Checking for unexpectedly slow tests..."
pytest tests/unit/ --slow-threshold=0.3 || exit 1
```

### GitHub Actions Workflow

```yaml
name: Test Performance Monitoring

on: [pull_request]

jobs:
  slow-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Run tests with slow-test tracking
        run: |
          pytest tests/ \
            --slow-threshold=0.1 \
            --slow-report=slow_tests.json \
            --tb=short
      
      - name: Comment on PR with slow tests
        if: always()
        uses: actions/github-script@v6
        with:
          script: |
            const fs = require('fs');
            const report = JSON.parse(fs.readFileSync('slow_tests.json', 'utf8'));
            
            const slowCount = report.statistics.slow_test_count;
            const maxDuration = report.statistics.max_duration;
            
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: `⚠️ **Slow Test Report** (threshold: ${report.threshold_seconds}s)\n\n` +
                    `Slow tests detected: **${slowCount}/${report.total_tests}**\n` +
                    `Slowest: **${maxDuration.toFixed(3)}s**`
            });
```

### Local Development Helper Script

```bash
#!/bin/bash
# scripts/test-performance.sh

THRESHOLD=${1:-0.1}
REPORT=${2:-slow_tests.json}

echo "🚀 Running tests with slow-test tracking..."
echo "   Threshold: ${THRESHOLD}s"
echo "   Report: ${REPORT}"

pytest tests/ \
  --slow-threshold=$THRESHOLD \
  --slow-report=$REPORT \
  -q

if [ -f "$REPORT" ]; then
    echo ""
    echo "📊 Performance Summary:"
    jq '{
      total: .total_tests,
      slow: .statistics.slow_test_count,
      max_duration: .statistics.max_duration,
      avg_duration: .statistics.average_duration
    }' "$REPORT"
fi
```

Usage:
```bash
./scripts/test-performance.sh 0.05 reports/baseline.json
```

## Best Practices

1. **Start with Default Threshold**: Use `--slow-threshold=1.0` initially to identify obvious bottlenecks.

2. **Mark Expected Slow Tests**: Use `@pytest.mark.slow` for integration/E2E tests that are inherently slower.

3. **Track Trends**: Export JSON reports and compare over time to detect regressions.

4. **Integrate Early**: Add to CI pipeline before performance degrades significantly.

5. **Tune Threshold Periodically**: Adjust threshold as your test suite evolves.

6. **Combine with Profiling**: Use slow test detection + pytest profilers for deeper analysis:
   ```bash
   pytest tests/ --slow-threshold=0.1 --durations=10
   ```

## FAQ

**Q: Will slow test tracking slow down my tests?**
A: No, the overhead is negligible (<0.1% impact). The feature just records timing from pytest hooks.

**Q: How do I ignore specific slow tests?**
A: Mark them explicitly with `@pytest.mark.slow` so they appear in a separate category that won't trigger threshold warnings. Or adjust the threshold to match your actual performance baseline.

**Q: Can I use this with pytest-xdist?**
A: Yes, the feature is fully compatible with parallel execution via `pytest -n <num_workers>`.

**Q: How are durations captured?**
A: Durations come directly from pytest's internal timing via the `pytest_runtest_logreport` hook, accurate to 3 decimal places (millisecond precision).

**Q: Where does the slow-test report file go?**
A: It goes to the path specified in `--slow-report=<path>`. The directory must exist beforehand.

## Related Documentation

- [Pytest Markers](https://docs.pytest.org/en/latest/how-to/mark.html) - Official pytest mark documentation
- [Pytest xdist](https://pytest-xdist.readthedocs.io/) - Parallel execution plugin
- [Performance Optimization Guide](./tuning.md) - Tips for improving test performance
