# Dependency Report Performance Tests

## Overview

Performance regression tests for `DependencyDriftCollector` live in
`tests/unit/observer/test_dependency_report_performance.py`. They are marked
`@pytest.mark.performance` and run in a dedicated CI job.

## What is tested

| Scenario | Deps | Notes | Bound |
|---|---|---|---|
| baseline | 7 | none | <50ms |
| large-simple | 20 | 10% actionable | <50ms |
| large-actionable | 10 | 80% actionable | <50ms |
| large-payload | 8 | ~80KB verbose notes | <50ms |
| extra-large | 50 | 50% actionable | <50ms |

All tests also assert correctness (dep count, actionable count, signal status).
Cross-scenario tests check linear scalability and error-path resilience.

## Measured baselines (Stage 3, 2026-05-30)

All scenarios: ~0.1–0.2ms wall-clock, ~0.003MB peak memory. The 50ms bound
provides >250× headroom — enough to catch architectural regressions (e.g.
accidental network I/O on the collection path) while ignoring CI noise.

## CI integration

The `performance` job in `.github/workflows/ci.yml` runs on every push and
pull request. A failure means collection time exceeded 50ms for at least one
scenario — a >250× regression from measured baseline.

To run locally:
```
pytest -q tests/unit -m "performance"
```

To run all performance + correctness tests:
```
pytest -q tests/unit/observer/test_dependency_report_performance.py
```
