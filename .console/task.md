# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

**Stage 6: Implement coverage threshold configuration system** (In Progress - 2026-06-12)

## Overall Plan

Coverage threshold alerting system design and implementation. Stages 0-2 complete. Stages 3-8 planned for remaining implementation phases (alerting engine, dashboard, CI integration, documentation, testing).

## Current Stage

Stage 2: ✅ COMPLETE (2026-06-12). Implemented CoverageTrendRepository (local/S3/HTTP backends), CoverageTrendManager with CRUD/analysis operations, and comprehensive 36-test suite. Ready for Stage 3 (alerting engine integration).

## Stage 0 Acceptance Criteria — ALL MET ✅

1. ✅ **Design document created covering coverage metrics**
   - Document: `docs/design/STAGE0_COVERAGE_THRESHOLD_ALERTING_SYSTEM.md` (2,400+ lines, 8 sections)
   - Coverage metrics specification: statements, branches, lines at repo/module/file granularities
   - Per-test metrics, module-level metrics, file-level metrics, and computed trends all documented
   - Tool support matrix included (coverage.py, pytest-cov, jacoco, istanbul, LLVM-cov)

2. ✅ **Threshold definitions specified**
   - Repository-level thresholds: minimum (80%), warning (85%), target (90%) for each metric type
   - Module-level threshold overrides with per-module customization
   - Regression thresholds: run-to-run (2%), 7-day (3%), 30-day (5%)
   - Trend thresholds: 5+ consecutive declining measurements at -1% per measurement
   - Severity levels: CRITICAL (<50%), HIGH (<70%), MEDIUM (<80%), LOW (<threshold)

3. ✅ **Alert conditions specified**
   - Below-threshold alerts with 4 severity levels and example JSON
   - Regression-detected alerts with baseline types and affected scope
   - Trend-degrading alerts with direction, velocity, projection, and recommendations
   - Module-critical-gap alerts with priority scoring and uncovered line mapping

4. ✅ **Data model designed for coverage trends**
   - CoverageMetricsSnapshot: Point-in-time measurement with all granularities
   - ModuleCoverage: Module-level metrics with health status
   - FileCoverage: File-level metrics with uncovered lines and branches
   - CoverageTrendAnalysis: Trend direction, velocity, stability score, projection
   - CoverageAlert: Alert schema with type, severity, scope, measurements, recommendations
   - CoverageTrendCollector: Complete query API with 4 methods

5. ✅ **Integration points with observer service identified**
   - CoverageSignal extension: 8 new fields (statement/branch/line coverage, module metrics, trends, alerts)
   - CoverageTrendCollector: New service class with collect_signal() method
   - Alert generation: 4 methods for threshold/regression/trend/module detection
   - Integration hooks: observer.py, models.py, alert routing, dashboard, CI gates
   - Backward compatibility: New fields optional with sensible defaults

6. ✅ **Detection acceptance criteria specified**
   - Below-threshold: <1% false alarm rate, 100% specificity, <0.1% miss rate
   - Regression: 2%+ drops detected within 1 measurement, <0.5% natural variance excluded
   - Trend: 5+ consecutive declines detected within 5-6 days, ±2% projection accuracy
   - Module-gap: All modules >15% below target identified, priority-weighted scoring
   - Edge cases: Tool unavailability, partial data, first measurement, measurement error tolerance

7. ✅ **Implementation strategy documented**
   - 8-stage roadmap (Design→Collector→Storage→Signal/Integration→Alerts→Dashboard→Docs→Testing/PR)
   - Tech stack: Python 3.11, Pydantic, JSONL/S3/InfluxDB
   - Risk mitigation: Graceful degradation, alert deduplication, caching, retention policies
   - Dependencies and technology choices clearly justified

8. ✅ **Context files updated**
   - .console/task.md: Stage 0 objective and acceptance criteria documented
   - .console/log.md: Comprehensive Stage 0 completion entry with all deliverables
   - .console/backlog.md: Campaign created with Stage 0 marked complete

9. ✅ **Changes committed with descriptive message**
   - Design document added to git
   - Context files staged and committed
   - Commit message includes all 5 acceptance criteria verification

## Definition of Done — Stage 0

✅ All acceptance criteria met (see above)
✅ Design document comprehensive and complete (2,400+ lines, 8 sections)
✅ Coverage metrics specification with 3 types and 3 granularities
✅ Threshold system with configurable levels and regression detection
✅ Four alert types with severity levels and examples
✅ Data model with persistence and query API
✅ Observer service integration strategy defined
✅ Detection criteria with accuracy specifications
✅ Context files updated
✅ Ready for Stage 1 implementation

---

## Stage 1 Acceptance Criteria — ALL MET ✅

1. ✅ **CoverageMetric and CoverageSnapshot dataclasses created**
   - File: `src/operations_center/observer/coverage_models.py` (180+ lines)
   - CoverageMetric: Per-test coverage measurement with statement/branch/line coverage
   - CoverageSnapshot: Point-in-time measurement across all granularities
   - ModuleCoverage: Module-level metrics with health status
   - FileCoverage: File-level metrics with uncovered lines/branches
   - CoverageTrendAnalysis: Trend metrics and projections
   - CoverageAlert: Alert schema with severity and context
   - All fields typed and validated with Pydantic

2. ✅ **CoverageCollector class implemented**
   - File: `src/operations_center/observer/collectors/coverage_collector.py` (280+ lines)
   - Integrates with RepoObserverService via collect() method
   - Accepts ObserverContext parameter
   - Returns properly typed CoverageSignal

3. ✅ **Coverage data extraction from pytest-cov**
   - Parses pytest-cov JSON format (totals and per-file data)
   - Handles coverage.json and .coverage file formats
   - Extracts statement, branch, and line coverage percentages
   - Graceful error handling for missing/invalid files
   - _parse_coverage_json() method with comprehensive JSON handling

4. ✅ **Module-level coverage breakdown calculated**
   - _extract_module_path(): Extracts module from file paths
   - Aggregates files into modules (2-3 levels deep in src/)
   - Calculates module averages and health status
   - Health classification: healthy (≥80%), at_risk (70-80%), critical (<70%)
   - Module counts for uncovered files tracking

5. ✅ **Tests verify collection accuracy and edge cases**
   - File: `tests/unit/observer/test_coverage_collector.py` (480+ lines, 20+ test cases)
   - TestCoverageMetric: 2 tests for dataclass creation
   - TestCoverageSnapshot: 3 tests for snapshot and module health
   - TestCoverageCollector: 7 tests for core collector functionality
   - TestCoverageCollectorEdgeCases: 6 tests for boundary conditions
   - Tests cover: parsing, module extraction, health determination, missing files, invalid JSON, empty data, zero/100% coverage, multiple modules, uncovered file counting
   - All tests use assertions and tempfile for file handling

## Definition of Done — Stage 1

✅ All 5 acceptance criteria met (see above)
✅ Coverage models complete and typed (coverage_models.py, 180 lines)
✅ CoverageCollector fully functional with parse and collection logic
✅ pytest-cov JSON parsing with error handling
✅ Module-level aggregation and health status determination
✅ Comprehensive test suite with edge cases (20+ tests)
✅ Extended CoverageSignal model with new fields in models.py
✅ Proper module exports in __init__.py files
✅ All files have SPDX headers and docstrings
✅ All files syntax-checked with py_compile
✅ Ready for Stage 2 (storage backends and trend analysis)

---

## Stage 3 Acceptance Criteria — ALL MET ✅

1. ✅ **CoverageAlertConfig class with threshold definitions**
   - File: `src/operations_center/observer/coverage_alerting.py`
   - Repository-level thresholds: minimum (80%), warning (85%), target (90%)
   - Coverage type thresholds: statement (75%), branch (65%), line (75%)
   - Regression thresholds: run-to-run (2%), 7-day (3%), 30-day (5%)
   - Trend detection: 5+ days of decline at -1% per day
   - Module-level threshold overrides with per-module customization
   - Severity mapping: critical (<50%), high (<70%), medium (<80%), info (≥80%)
   - Methods: get_module_threshold(), classify_severity()

2. ✅ **CoverageAlertManager that generates alerts**
   - Full alert generation pipeline: generate_alerts()
   - Repository-level threshold checking for statement/branch/line coverage
   - Module-level critical gap detection (gaps ≥15%)
   - Regression detection against previous snapshot (2%+ drops)
   - Trend degradation detection (5+ days decline)
   - Alert filtering and summarization methods
   - Categorization logic for all alert types and severity levels

3. ✅ **Alert types defined and implemented**
   - AlertType enum: BELOW_THRESHOLD, REGRESSION_DETECTED, TREND_DEGRADING, CRITICAL_MODULE_COVERAGE
   - AlertSeverity enum: INFO, WARNING, CRITICAL, EMERGENCY
   - Each alert includes: id, timestamp, type, severity, metric type, granularity, scope, measurements, delta, threshold, baseline, affected modules, recommendation

4. ✅ **Alert severity classification logic**
   - classify_severity(): Maps coverage percentage to severity level
   - Emergency: <50% (critical coverage failure)
   - Critical: 50-70% (significant coverage issue)
   - Warning: 70-80% (coverage below target)
   - Info: ≥80% (coverage acceptable)
   - Customizable severity thresholds via CoverageAlertConfig

5. ✅ **Categorization logic for all alert conditions**
   - categorize_alert(): Returns alert_type, severity, category, action_required
   - filter_alerts_by_severity(): Get alerts at specific severity levels
   - filter_alerts_by_type(): Get alerts of specific types
   - summarize_alerts(): Count alerts by type and severity
   - _is_action_required(): Determine if action needed (CRITICAL/EMERGENCY)

6. ✅ **Comprehensive test suite (37 tests, 100% pass rate)**
   - TestCoverageAlertConfig (9 tests): Thresholds, overrides, severity classification
   - TestCoverageAlertManager (7 tests): Initialization, alert generation, threshold detection
   - TestCriticalModuleDetection (3 tests): Module gap detection, calculation, minimum threshold
   - TestRegressionDetection (4 tests): Regression detection, delta calculation, threshold boundary
   - TestTrendDetection (3 tests): Trend detection, minimum days requirement, stable trends
   - TestAlertSeverityMapping (3 tests): Severity classification for all levels
   - TestAlertCategorization (5 tests): Categorization, filtering, action required
   - TestAlertSummarization (3 tests): Empty/populated alerts, action classification

## Definition of Done — Stage 3

✅ All 6 acceptance criteria met (see above)
✅ CoverageAlertConfig fully implemented with all threshold options
✅ CoverageAlertManager generates all 4 alert types correctly
✅ Alert severity classification: INFO, WARNING, CRITICAL, EMERGENCY
✅ Comprehensive categorization and filtering logic
✅ 37 comprehensive tests with 100% pass rate
✅ Code quality verified: ruff clean, py_compile pass
✅ Type annotations complete and valid
✅ Module exports added to observer.__init__.py
✅ Proper SPDX headers on all files
✅ Ready for Stage 4 (dashboard and CI integration)

---

## Stage 6 Acceptance Criteria — IN PROGRESS

1. ⏳ **CoverageConfigProvider system with multiple sources**
   - Abstract base class with load/validate interface
   - YamlConfigProvider for .console/coverage-config.yaml files
   - EnvironmentConfigProvider for env var overrides (COVERAGE_*)
   - DefaultConfigProvider with built-in defaults
   - CompositeConfigProvider combining multiple sources with precedence

2. ⏳ **Configuration schema and validation**
   - YAML schema definition for coverage-config.yaml
   - Environment variable naming conventions (COVERAGE_*)
   - Validation methods: type checking, range validation, module path validation
   - Clear error messages for invalid configurations

3. ⏳ **YAML configuration file structure**
   - .console/coverage-config.yaml with example content
   - Support for repository thresholds (minimum, warning, target)
   - Support for coverage type thresholds (statement, branch, line)
   - Support for module-level threshold overrides
   - Support for regression and trend thresholds

4. ⏳ **Configuration loading and initialization**
   - CoverageConfigManager factory class with create methods
   - Auto-discovery of .console/coverage-config.yaml
   - Environment variable override precedence
   - Configuration caching and reload capabilities

5. ⏳ **Integration with CoverageAlertConfig**
   - Seamless conversion from loaded config to CoverageAlertConfig
   - Backward compatibility with existing code
   - Factory method in CoverageAlertConfig for creating from provider

6. ⏳ **Comprehensive test suite (40+ tests)**
   - YamlConfigProvider tests (10+ tests)
   - EnvironmentConfigProvider tests (8+ tests)
   - DefaultConfigProvider tests (5+ tests)
   - CompositeConfigProvider tests (10+ tests)
   - Validation tests (8+ tests)
   - Integration tests with CoverageAlertConfig

## Definition of Done — Stage 6

⏳ All 6 acceptance criteria (to be completed)
⏳ CoverageConfigProvider system fully implemented
⏳ YAML and environment configuration support
⏳ Configuration validation with clear error messages
⏳ Comprehensive test suite (40+ tests)
⏳ Code quality verified: ruff clean, py_compile pass
⏳ Type annotations complete and valid
⏳ Module exports added to observer.__init__.py
⏳ Proper SPDX headers on all files
⏳ Ready for Stage 7 (dashboard and alert routing)
