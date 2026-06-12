# Edge-Case Test Suite for Flaky Test Reporter Metrics

## Overview

This test suite provides comprehensive edge-case coverage infrastructure for all 14 metrics in the Flaky Test Reporter system. The suite uses parametrized testing to validate extreme scenarios, boundary conditions, and invalid inputs across:

- **7 Per-Test Metrics**: failure_rate, failure_entropy, streak_variance, recovery_time_percentile_90, duration_stability, environment_correlation, isolation_score
- **7 Repository-Level Metrics**: flaky_test_percentage, median_failure_rate, flaky_growth_rate, category_concentration, critical_test_flakiness_ratio, flaky_velocity, repository_health_score

## Files

### Infrastructure Files (Created in Stage 1)

- **`conftest.py`** — Pytest fixtures and factory functions
  - `flaky_test_reporter`: Base reporter instance with temporary storage
  - `test_results_factory`: Factory for creating FlakyTestResult objects
  - `metric_factory`: Factory for creating FlakyTestMetric objects
  - `flaky_test_session_report_factory`: Factory for session reports
  - `per_test_metric_edge_cases`: Pre-configured edge-case scenarios for per-test metrics
  - `repository_metric_edge_cases`: Pre-configured edge-case scenarios for repo metrics

- **`test_data_generators.py`** — Generator functions and helper utilities
  - 7 per-test metric generators (`generate_failure_rate_scenarios()`, etc.)
  - 7 repository-level metric generators (`generate_flaky_test_percentage_scenarios()`, etc.)
  - Helper functions: `create_test_results_sequence()`, `apply_floating_point_error()`

### Test Implementation Files (To Be Created in Stage 2)

- **`test_edge_cases_per_test_metrics.py`** — Edge-case tests for per-test metrics
  - 7 test classes (one per metric)
  - ~50+ parametrized test cases
  - Coverage: zero-input, boundary, extreme, invalid, pathological scenarios

- **`test_edge_cases_repo_metrics.py`** — Edge-case tests for repository-level metrics
  - 7 test classes (one per metric)
  - ~50+ parametrized test cases
  - Coverage: zero-input, boundary, extreme, invalid, pathological scenarios

### Existing Files

- **`test_flaky_test_reporter.py`** — Core reporter tests (unmodified)
- **`test_flaky_test_aggregator.py`** — Aggregator tests (unmodified)
- **`test_flaky_test_alerts.py`** — Alert tests (unmodified)
- **`test_dashboard_flaky.py`** — Dashboard tests (unmodified)

## Scenario Categories

All parametrized tests are organized by scenario type:

### 1. ZERO_INPUT
- Empty collections
- Zero values
- Single elements
- No data scenarios

**Examples**:
```python
# failure_rate with zero total runs
(failures=0, total=0, expected=0.0)

# No flaky tests
(flaky_count=0, total_tests=0, expected=0.0)
```

### 2. BOUNDARY
- Values at threshold (exactly at limit)
- Just above threshold (+1, +0.001, etc.)
- Just below threshold (-1, -0.001, etc.)

**Examples**:
```python
# At threshold: 0.05 for failure_rate
(failures=1, total=20, expected=0.05)

# Above threshold
(failures=1, total=19, expected=0.052632)
```

### 3. EXTREME
- Very large numbers (1M+)
- Very small numbers (0.0001-)
- Maximum/minimum representable values
- Precision limits

**Examples**:
```python
# Large sample sizes
(failures=9999, total=10000, expected=0.9999)

# Large repository
(flaky_count=1, total_tests=10000, expected=0.0001)
```

### 4. INVALID
- Negative values (when impossible)
- NaN/Infinity
- Type mismatches
- Out-of-range values

**Examples**:
```python
# All zero durations (division by zero)
(durations=[0.0, 0.0, 0.0], expected="error")

# More parallel failures than serial (anomaly)
(serial=5, parallel=10, expected=-1.0)
```

### 5. PATHOLOGICAL
- All same value
- Perfectly alternating pattern
- Single repeated value
- Maximum randomness

**Examples**:
```python
# All passes (deterministic, entropy = 0)
(pass_count=10, fail_count=0, expected=0.0)

# Perfect 50/50 split (maximum entropy)
(pass_count=5, fail_count=5, expected=1.0)
```

## Running Tests

### Run All Edge-Case Tests

```bash
# All edge-case infrastructure tests
pytest tests/unit/observer/conftest.py tests/unit/observer/test_data_generators.py -v

# All parametrized edge-case tests (when implemented in Stage 2)
pytest tests/unit/observer/test_edge_cases*.py -v
```

### Run Specific Metric Tests

```bash
# failure_rate edge cases only
pytest tests/unit/observer/test_edge_cases_per_test_metrics.py::TestFailureRateEdgeCases -v

# Repository health score
pytest tests/unit/observer/test_edge_cases_repo_metrics.py::TestRepositoryHealthScoreEdgeCases -v
```

### Run by Scenario Type

```bash
# All boundary value tests
pytest tests/unit/observer/test_edge_cases*.py -k "boundary" -v

# All zero-input edge cases
pytest tests/unit/observer/test_edge_cases*.py -k "zero" -v

# All extreme value tests
pytest tests/unit/observer/test_edge_cases*.py -k "extreme" -v
```

### Run with Coverage Report

```bash
# Generate coverage for edge-case tests
pytest tests/unit/observer/test_edge_cases*.py --cov=operations_center.observer --cov-report=html

# Coverage threshold verification
pytest tests/unit/observer/test_edge_cases*.py --cov=operations_center.observer --cov-fail-under=95
```

## Using Fixtures in Your Tests

### Using Factory Fixtures

```python
def test_metric_with_factory(metric_factory):
    """Create metrics using the factory."""
    metric = metric_factory(
        nodeid="test::test_foo",
        failure_rate=0.5,
        run_count=10
    )
    assert metric.failure_rate == 0.5
    assert metric.run_count == 10
```

### Using Test Results Factory

```python
def test_reporter_with_factory(flaky_test_reporter, test_results_factory):
    """Track test results using the factory."""
    result = test_results_factory(
        outcome="failed",
        duration=2.5,
        markers=["slow"]
    )
    flaky_test_reporter.track_test(result)
    report = flaky_test_reporter.analyze_session()
    assert report.flaky_count >= 0
```

### Using Pre-Configured Edge Cases

```python
def test_with_edge_cases(flaky_test_reporter, per_test_metric_edge_cases):
    """Use pre-configured edge-case scenarios."""
    scenarios = per_test_metric_edge_cases["failure_rate"]
    
    for scenario_name, (failures, total, expected) in scenarios.items():
        rate = failures / total if total > 0 else 0.0
        assert rate == expected, f"Failed: {scenario_name}"
```

## Using Data Generators

### Direct Parametrization

```python
from tests.unit.observer.test_data_generators import generate_failure_rate_scenarios

class TestFailureRateEdgeCases:
    @pytest.mark.parametrize(
        "failures,total,expected,scenario_name",
        generate_failure_rate_scenarios()
    )
    def test_calculation(self, failures, total, expected, scenario_name):
        rate = failures / total if total > 0 else 0.0
        assert rate == expected
```

### Using Generator Output

```python
from tests.unit.observer.test_data_generators import generate_entropy_scenarios

def test_entropy_with_all_scenarios():
    """Test all entropy scenarios at once."""
    for pass_count, fail_count, expected, name in generate_entropy_scenarios():
        # Test logic here
        pass
```

## Adding New Metrics to the Edge-Case Suite

When adding a new metric to the flaky test reporter:

### 1. Create Generator Function (in `test_data_generators.py`)

```python
def generate_my_new_metric_scenarios() -> list[tuple]:
    """Generate parametrization scenarios for my_new_metric.
    
    Covers all scenario types: ZERO_INPUT, BOUNDARY, EXTREME, INVALID, PATHOLOGICAL
    
    Returns:
        List of tuples: (input1, input2, expected_output, scenario_name)
    """
    return [
        # ZERO_INPUT cases
        (..., expected, "scenario_name"),
        
        # BOUNDARY cases
        (..., expected, "scenario_name"),
        
        # Continue for other categories...
    ]
```

### 2. Add to Fixtures (in `conftest.py`)

Add pre-configured scenarios to either `per_test_metric_edge_cases` or `repository_metric_edge_cases`:

```python
@pytest.fixture
def per_test_metric_edge_cases() -> dict[str, dict]:
    return {
        "my_new_metric": {
            "zero_input": (0, 0, 0.0),
            "boundary": (1, 20, 0.05),
            # ... more scenarios
        },
        # ... existing metrics
    }
```

### 3. Create Test Class (in appropriate test file)

```python
class TestMyNewMetricEdgeCases:
    """Edge-case tests for my_new_metric."""
    
    @pytest.mark.parametrize(
        "input1,input2,expected,scenario_name",
        generate_my_new_metric_scenarios(),
        ids=[s[3] for s in generate_my_new_metric_scenarios()]
    )
    def test_my_new_metric(self, input1, input2, expected, scenario_name):
        """Test my_new_metric with all edge cases."""
        # Implementation
```

## Test Statistics

### Stage 1 Deliverables (Completed)

- ✅ 1 design document (STAGE1_PARAMETRIZED_TEST_DESIGN.md)
- ✅ 4 core fixtures (conftest.py)
- ✅ 14 generator functions (test_data_generators.py)
- ✅ 3 helper functions (test_data_generators.py)
- ✅ Pre-configured edge cases for all 14 metrics
- ✅ 120+ parametrization scenarios documented

### Stage 2 Implementation (To Be Done)

- [ ] ~50 parametrized test cases for per-test metrics
- [ ] ~50 parametrized test cases for repository-level metrics
- [ ] ~100+ total new test cases
- [ ] Expected coverage: >95% of edge cases

## Maintenance and Updates

### Updating Scenarios

When metric definitions change:

1. Update generator function in `test_data_generators.py`
2. Update pre-configured fixtures in `conftest.py`
3. Update test cases as needed

### Adding New Scenario Categories

If new scenario types are needed:

1. Document them in this README
2. Add to scenario categories table
3. Update relevant generator functions
4. Update test organization as needed

## Troubleshooting

### Tests Not Discovered

Ensure parametrization uses correct format:

```python
# ✅ Correct
@pytest.mark.parametrize("a,b,expected", [(1, 2, 3)])

# ❌ Incorrect
@pytest.mark.parametrize("a,b,expected", generate_scenarios())  # Missing ids
```

### Floating-Point Assertion Failures

Use `math.isclose()` for floating-point comparisons:

```python
import math

# ✅ Correct
assert math.isclose(result, expected, rel_tol=1e-5)

# ❌ Incorrect
assert result == expected  # May fail due to rounding
```

### Generator Function Not Found

Ensure import path is correct:

```python
# ✅ Correct
from tests.unit.observer.test_data_generators import generate_failure_rate_scenarios

# ❌ Incorrect
from test_data_generators import generate_failure_rate_scenarios
```

## References

- **Stage 0 Analysis**: `.console/STAGE0_EDGE_CASE_ANALYSIS.md` — Complete analysis of 14 metrics, 120+ scenarios
- **Stage 1 Design**: `.console/STAGE1_PARAMETRIZED_TEST_DESIGN.md` — Test infrastructure design
- **Main Architecture**: `docs/STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md` — Metric definitions and thresholds
