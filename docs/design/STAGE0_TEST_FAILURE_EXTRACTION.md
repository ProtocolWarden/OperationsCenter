# Stage 0: Test Failure Categorization Extraction — Investigation Report

**Date**: 2026-06-13  
**Objective**: Extend failure categorization to extract test names and assertion messages  
**Status**: ✅ Investigation Complete

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture](#current-architecture)
3. [Failure Categorization Systems](#failure-categorization-systems)
4. [Test Failure Capture Flow](#test-failure-capture-flow)
5. [Data Extraction Points](#data-extraction-points)
6. [Current Limitations](#current-limitations)
7. [Available Test Data](#available-test-data)
8. [Proposed Extraction Strategy](#proposed-extraction-strategy)
9. [Implementation Roadmap](#implementation-roadmap)

## Executive Summary

The OperationsCenter platform has a comprehensive failure categorization system spanning three layers:
1. **Execution/Recovery Layer**: Maps execution results to recovery-relevant failure kinds
2. **Contracts Layer**: Defines canonical failure reason categories
3. **Observer Layer**: Captures test outcomes and validates repository state

Currently, **test failures are captured at a high level** (pass/fail/skip/error counts and aggregated exception messages) but **lack fine-grained details** about individual test names and assertion messages.

**Key Finding**: Test failure details are available at the pytest layer but are not being fully extracted and propagated to the failure categorization system. The extraction points exist; they are underutilized.

## Current Architecture

### Three-Layer Failure Categorization System

```
┌─────────────────────────────────────────┐
│  Execution/Recovery Layer               │
│  (classifier.py, recovery_loop/)        │
│  Classifies ExecutionResult →           │
│  ExecutionFailureKind (8 kinds)         │
└─────────────────────────────────────────┘
                    ↑
                    │ uses
                    │
┌─────────────────────────────────────────┐
│  Contracts Layer                        │
│  (enums.py, execution.py)               │
│  Defines FailureReasonCategory (11),    │
│  ExecutionStatus, ArtifactType,         │
│  ValidationStatus                       │
└─────────────────────────────────────────┘
                    ↑
                    │ populated by
                    │
┌─────────────────────────────────────────┐
│  Observer/Test Layer                    │
│  (flaky_test_models.py, models.py,      │
│   pytest_flaky_plugin.py)               │
│  Captures FlakyTestResult,              │
│  TestSignal, ValidationError            │
└─────────────────────────────────────────┘
```

## Failure Categorization Systems

### System 1: Execution/Recovery Classifier

**File**: `src/operations_center/execution/recovery_loop/classifier.py`

**Component**: `DefaultFailureClassifier`

**Purpose**: Map `ExecutionResult` to `ExecutionFailureKind` for retry/recovery decisions

**Current Mapping Rules**:
1. `result.success == True` → `NONE`
2. `status == TIMED_OUT` or `failure_category == TIMEOUT` → `TIMEOUT`
3. `failure_category == POLICY_BLOCKED` → `CONFIGURATION`
4. `failure_category == VALIDATION_FAILED` → `CONTRACT_VIOLATION`
5. `failure_category == ROUTING_ERROR` → `CONFIGURATION`
6. Adapter error codes (RATE_LIMIT, BACKEND_UNAVAILABLE, AUTH_FAILED, CONTRACT_REJECTED, EXECUTOR_ERROR, TIMEOUT) → mapped kinds
7. Otherwise → `UNKNOWN`

**Failure Kinds** (8 total):
- `NONE` — success
- `TRANSIENT` — recoverable
- `TIMEOUT` — execution timeout
- `RATE_LIMIT` — API rate limiting
- `AUTH` — authentication failure
- `CONFIGURATION` — configuration/policy issue
- `CONTRACT_VIOLATION` — validation contract failure
- `BACKEND_UNAVAILABLE` — service unavailable
- `UNKNOWN` — uncategorized

**Current Inputs**:
- `ExecutionResult.status` (ExecutionStatus enum)
- `ExecutionResult.failure_category` (FailureReasonCategory enum)
- `ExecutionResult.failure_reason` (free-form string, not parsed)
- `ExecutionResult.artifacts` (optional adapter error code hints)

**Limitation**: Does NOT parse test failures. Treats all `VALIDATION_FAILED` the same regardless of whether it's a test assertion, contract validation, or other validation failure.

### System 2: Contracts Failure Categories

**File**: `src/operations_center/contracts/enums.py`

**Component**: `FailureReasonCategory` enum (11 categories)

```python
class FailureReasonCategory(str, Enum):
    VALIDATION_FAILED = "validation_failed"      # Generic validation failure
    BACKEND_ERROR = "backend_error"              # Backend service error
    UNSUPPORTED_REQUEST = "unsupported_request"  # Request not supported
    TIMEOUT = "timeout"                          # Execution timeout
    NO_CHANGES = "no_changes"                    # No changes produced
    CONFLICT = "conflict"                        # Merge/conflict issue
    POLICY_BLOCKED = "policy_blocked"            # Policy violation
    BUDGET_EXHAUSTED = "budget_exhausted"        # Token/resource limit
    ROUTING_ERROR = "routing_error"              # Routing/dispatch failure
    SCOPE_TOO_WIDE = "scope_too_wide"            # Scope violation
    UNKNOWN = "unknown"                          # Unknown failure
```

**Used By**:
- Adapters (set when producing `ExecutionResult`)
- Recovery loop classifier (matches to determine `ExecutionFailureKind`)
- Backend-specific classifiers (e.g., OpenClaw error categorization)

**Gap**: `VALIDATION_FAILED` is too broad — includes both test failures and contract validation failures. No sub-categorization by failure type.

### System 3: Validation Failure Categories

**File**: `src/operations_center/observer/snapshot_validator.py`

**Component**: `ValidationFailureCategory` enum (4 categories)

```python
class ValidationFailureCategory(Enum):
    TRANSIENT = "transient"           # Retryable (e.g., network timeout)
    STRUCTURAL = "structural"         # Non-retryable (e.g., missing signal)
    CONFIGURATION = "configuration"   # Configuration issue
    UNKNOWN = "unknown"               # Unknown
```

**Used By**:
- `SnapshotValidator` (5-layer validation: schema, completeness, consistency, accuracy, regression)
- Snapshot validation reports

**Note**: Focuses on snapshot validation, not test execution failures. Different concern than test failure categorization.

## Test Failure Capture Flow

### Current Data Flow

```
Pytest Execution
       ↓
pytest_flaky_plugin.py (pytest_runtest_makereport hook)
       ↓ extracts:
         - item.nodeid (e.g., "tests/unit/test_foo.py::TestClass::test_method")
         - call.duration
         - call.excinfo.value (as string repr)
       ↓
FlakyTestResult (dataclass)
       ↓ fields:
         - nodeid
         - outcome (PASSED, FAILED, SKIPPED, XFAILED, XPASSED)
         - duration
         - exception_type (parsed from exception string)
         - exception_message (parsed from exception string)
         - output_lines (free-form output)
         - markers, environment, python_version
       ↓
FlakyTestReporter (analysis engine)
       ↓ computes:
         - FlakyTestMetric (flakiness scores, patterns)
         - FlakyTestSessionReport (session-level analysis)
       ↓
Storage (local/S3/HTTP)
```

### Current Implementation Details

**Pytest Plugin Hook** (`pytest_flaky_plugin.py:54-80`):

```python
def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo):
    if call.when == "call":  # Main test execution only
        test_name = item.nodeid
        outcome = "passed" if call.excinfo is None else "failed"
        
        if test_name not in self.test_outcomes:
            self.test_outcomes[test_name] = {
                "test_name": test_name,
                "outcome": outcome,
                "duration": call.duration or 0,
                "exception": str(call.excinfo.value) if call.excinfo else None,
            }
```

**Issues**:
1. Exception extracted as string repr only: `str(call.excinfo.value)`
2. No parsing of exception message or type separately
3. No extraction of assertion message from pytest's rewritten assertions
4. No extraction of test function name separate from nodeid
5. No extraction of test class name separate from nodeid
6. No extraction of traceback or source location

**FlakyTestReporter Analysis** (`flaky_test_reporter.py:141-145`):

```python
last_failure_reason = ""
for r in reversed(runs):
    if r.outcome == TestOutcome.FAILED and r.exception_type:
        last_failure_reason = f"{r.exception_type}: {r.exception_message}"[:100]
        break
```

**Issues**:
1. Takes first (reversed, so last) failing run
2. Truncates to 100 characters
3. Discards assertion message details
4. No test name context in output

### Pytest Data Availability

Pytest provides rich failure information through multiple APIs:

**1. pytest.Item Object** (the test case)
- `nodeid: str` — full test path (e.g., "tests/unit/test_foo.py::TestClass::test_method")
- `function: Callable` — test function object
  - `function.__name__` — function name (e.g., "test_method")
- `cls: type | None` — test class (for class-based tests)
  - `cls.__name__` — class name (e.g., "TestClass")
- `fspath: Path` — file path where test is defined
- `module: ModuleType` — imported test module

**2. pytest.CallInfo Object** (call details)
- `when: str` — "setup", "call", or "teardown"
- `duration: float` — execution duration in seconds
- `excinfo: ExceptionInfo | None` — exception information if failed
- `outcome: str` — "passed", "failed", or "skipped"

**3. ExceptionInfo Object** (exception details)
- `type: type[BaseException]` — exception class
- `value: BaseException` — exception instance
- `traceback: Traceback` — traceback object
- `getrepr(): str` — formatted exception representation
- `traceback[0].source: Source` — source code context

**4. Assertion Rewriting** (pytest-specific)
- Pytest rewrites assert statements for better error messages
- Assertion message available in exception output
- ExceptionInfo.getrepr() includes rewritten assertion context
- Traceback includes source line with assertion

### Example: Pytest Provides

**Test Code**:
```python
def test_calculation():
    result = add(2, 3)
    assert result == 6, "Expected 5 but got something else"  # Wrong expected value
```

**Pytest ExceptionInfo Contains**:
- `type: AssertionError`
- `value: AssertionError("Expected 5 but got something else")`
- `traceback: <full traceback with source context>`
- `getrepr()`: Full formatted output with:
  - Source line: `assert result == 6`
  - Assertion rewriting context: `assert 5 == 6`
  - Exception message: `"Expected 5 but got something else"`

**Currently Extracted** (in pytest plugin):
- `nodeid: "tests/test_math.py::test_calculation"`
- `exception: "AssertionError('Expected 5 but got something else')"`

**Not Currently Extracted**:
- Function name: "test_calculation"
- Test file: "tests/test_math.py"
- Exception type: "AssertionError"
- Exception message: "Expected 5 but got something else"
- Source location: "tests/test_math.py:5"
- Assertion rewriting context: `5 == 6`

## Data Extraction Points

### Extraction Point 1: Pytest Hook (`pytest_runtest_makereport`)

**Current Extraction**:
```
item.nodeid → test_name
call.duration → duration
call.excinfo → exception (as string)
```

**Available to Extract**:
```
item.nodeid → nodeid (full path)
  └─ parseable to: file, class, function
item.function.__name__ → function_name
item.cls.__name__ (if exists) → class_name
item.fspath → test_file
call.when → phase (setup/call/teardown)
call.excinfo.type → exception_type (the class)
call.excinfo.value → exception_instance
call.excinfo.getrepr() → formatted_exception (full output with assertions)
call.excinfo.traceback[0].lineno → source_line_number
```

**Transformation Required**:
1. Extract structured exception details from `call.excinfo`
2. Parse nodeid to separate module/class/function
3. Extract assertion message from exception traceback

### Extraction Point 2: FlakyTestResult Model

**Current Fields**:
- `nodeid: str`
- `outcome: TestOutcome`
- `duration: float`
- `exception_type: str` (parsed, error-prone)
- `exception_message: str` (parsed, error-prone)
- `markers: list[str]`
- `environment: str`
- `python_version: str`
- `output_lines: list[str]`

**Missing Fields**:
- `test_file: str` (e.g., "tests/unit/test_foo.py")
- `test_class: str | None` (e.g., "TestClass" or None)
- `test_function: str` (e.g., "test_method")
- `assertion_message: str` (extracted assertion message)
- `exception_chain: list[str]` (full traceback)
- `source_location: str` (file:line)

### Extraction Point 3: FlakyTestMetric Analysis

**Current Usage**:
```python
last_failure_reason = f"{r.exception_type}: {r.exception_message}"[:100]
```

**Enhanced Usage** (proposed):
```python
# Include test name in context
last_failure_reason = f"{r.test_function} assertion: {r.assertion_message}"
# Or: f"In {r.test_class}.{r.test_function}: {r.assertion_message}"

# Track patterns by assertion type
assertion_patterns = {}
for r in runs:
    if r.assertion_message:
        key = normalize_assertion(r.assertion_message)
        assertion_patterns[key] = assertion_patterns.get(key, 0) + 1
```

### Extraction Point 4: TestSignal Model

**Current Fields** (`models.py:81-103`):
- High-level aggregations: status, test_count, passed_count, failed_count
- No individual test failure details
- One `summary: str` field (optional)

**Enhancement Opportunity**:
```python
class TestSignal(BaseModel):
    # ... existing fields ...
    
    # NEW: Individual test failure details (optional)
    failed_tests: list[TestFailureDetail] = Field(default_factory=list)
    
class TestFailureDetail(BaseModel):
    test_name: str  # e.g., "test_calculation"
    test_file: str  # e.g., "tests/test_foo.py"
    assertion_message: str | None = None
    exception_type: str
    exception_message: str
```

### Extraction Point 5: Artifact Attachment

**Current**: Artifacts are used for diffs, patches, logs, etc.

**Opportunity**: Create test failure detail artifacts

```python
class ExecutionArtifact:
    artifact_type: ArtifactType  # NEW: TEST_FAILURE_DETAIL
    label: str
    content: dict  # JSON with test name, assertion message, traceback
```

## Current Limitations

### Limitation 1: Nodeid Parsing

**Current State**:
- Full nodeid captured: `"tests/unit/test_foo.py::TestClass::test_method"`
- Parser exists: `module = test_name.split("::")[0]`
- But no robust parsing of class/function names

**Impact**:
- Can't reliably extract test class/function names
- Parsing different nodeid formats error-prone
- No separation of concerns between test identity and execution details

### Limitation 2: Exception Information Loss

**Current State**:
```python
exception: str(call.excinfo.value) if call.excinfo else None
```

**Impact**:
- Exception converted to string immediately
- Type information lost
- Assertion rewriting context lost
- Traceback discarded
- Can't distinguish between assertion errors and other exceptions

### Limitation 3: Assertion Message Extraction

**Current State**:
- Assertion messages embedded in exception_message
- No separate field for assertion content
- No context about what was being asserted

**Impact**:
- Can't analyze assertion patterns
- Can't correlate similar assertion failures
- Can't distinguish "expected X but got Y" from other failure reasons

### Limitation 4: Traceback Loss

**Current State**:
- Full traceback from pytest is available in ExceptionInfo
- Currently extracted as single-line summary
- No source location information

**Impact**:
- Can't identify which line the assertion failed on
- Can't show context (what code led to assertion)
- Can't correlate failures across different test variations

### Limitation 5: Aggregation Without Detail

**Current State**:
- TestSignal captures: passed_count, failed_count, error_count
- No individual test results
- Last failure reason is single line (100 chars max)

**Impact**:
- Can't ask "which tests are failing?"
- Can't correlate same assertion failures
- Can't distinguish patterns in failures
- Dashboard shows only statistics, not diagnostic details

## Available Test Data

### Data Available from Pytest

| Data | Source | Current Status | Usable? |
|------|--------|-----------------|---------|
| Test nodeid | `item.nodeid` | ✅ Captured | Yes |
| Test file path | `item.fspath` | ❌ Not captured | Yes |
| Test function name | `item.function.__name__` | ❌ Not captured | Yes |
| Test class name | `item.cls.__name__` | ❌ Not captured | Yes |
| Execution duration | `call.duration` | ✅ Captured | Yes |
| Exception type | `call.excinfo.type` | ⚠️ Converted to string | Yes (needs refactoring) |
| Exception value | `call.excinfo.value` | ⚠️ Converted to string | Yes (needs refactoring) |
| Exception traceback | `call.excinfo.traceback` | ❌ Not captured | Yes |
| Source line number | `call.excinfo.traceback[0].lineno` | ❌ Not captured | Yes |
| Full formatted exception | `call.excinfo.getrepr()` | ❌ Not captured | Yes |
| Test markers | `item.iter_markers()` | ✅ Partially captured | Yes |
| Test output (capsys) | Via fixture (not hook) | ⚠️ Partial capture | Yes (requires fixture) |

### Data Available from Models

| Model | Field | Current | Gap |
|-------|-------|---------|-----|
| FlakyTestResult | All fields | ✅ Available | Need test name components |
| TestSignal | status, counts | ✅ Available | Need individual failure details |
| ValidationError | message, details | ✅ Available | Could structure test failures |

## Proposed Extraction Strategy

### Strategy 1: Enhanced Pytest Plugin

**Goal**: Extract complete test failure information at the pytest layer

**Implementation**:
```python
def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo):
    if call.when == "call":
        # Extract test identity
        test_nodeid = item.nodeid
        test_file = str(item.fspath)
        test_function = item.function.__name__
        test_class = item.cls.__name__ if hasattr(item, 'cls') and item.cls else None
        
        # Extract exception details
        if call.excinfo:
            exception_type = call.excinfo.type.__name__
            exception_value = call.excinfo.value
            exception_message = str(exception_value)
            
            # Extract assertion message if available
            assertion_message = _extract_assertion_message(call.excinfo)
            source_line = call.excinfo.traceback[0].lineno
            source_location = f"{test_file}:{source_line}"
            exception_chain = _format_traceback(call.excinfo.traceback)
        else:
            # Test passed
            exception_type = None
            assertion_message = None
            source_location = None
            exception_chain = []
        
        # Create FlakyTestResult with all fields
        result = FlakyTestResult(
            nodeid=test_nodeid,
            outcome=outcome,
            duration=call.duration,
            exception_type=exception_type,
            exception_message=exception_message,
            assertion_message=assertion_message,
            source_location=source_location,
            test_file=test_file,
            test_class=test_class,
            test_function=test_function,
            exception_chain=exception_chain,
            # ... other fields
        )
```

**Key Functions Needed**:
1. `_extract_assertion_message(excinfo)`: Parse pytest's assertion rewriting to get clean assertion message
2. `_format_traceback(traceback)`: Format traceback as structured list of frames

### Strategy 2: Enhanced Data Models

**Goal**: Add fields to capture extracted test details

**FlakyTestResult** (flaky_test_models.py):
```python
@dataclass
class FlakyTestResult:
    nodeid: str
    outcome: TestOutcome | str
    duration: float
    
    # EXISTING
    markers: list[str] = field(default_factory=list)
    exception_type: str = ""
    exception_message: str = ""
    output_lines: list[str] = field(default_factory=list)
    run_id: str = ""
    environment: str = "local"
    python_version: str = ""
    
    # NEW: Test identity components
    test_file: str = ""
    test_class: str | None = None
    test_function: str = ""
    
    # NEW: Detailed exception information
    assertion_message: str | None = None
    exception_chain: list[str] = field(default_factory=list)
    source_location: str = ""  # file:line
```

**TestSignal** (models.py):
```python
class TestSignal(BaseModel):
    # ... existing fields ...
    
    # NEW: Individual test failure details (optional)
    failed_tests: list[TestFailureDetail] = Field(default_factory=list)

class TestFailureDetail(BaseModel):
    test_function: str
    test_class: str | None = None
    assertion_message: str | None = None
    exception_type: str
    source_location: str
```

### Strategy 3: Flakiness Analysis Enhancement

**Goal**: Use detailed test failure information for better flakiness categorization

**FlakyTestReporter** (flaky_test_reporter.py):
```python
def _categorize_flakiness(self, failure_rate, runs):
    """Categorize based on assertion patterns."""
    assertion_patterns = {}
    exception_types = {}
    
    for r in runs:
        if r.assertion_message:
            pattern_key = normalize_assertion(r.assertion_message)
            assertion_patterns[pattern_key] = assertion_patterns.get(pattern_key, 0) + 1
        
        if r.exception_type:
            exception_types[r.exception_type] = exception_types.get(r.exception_type, 0) + 1
    
    # Categorize based on patterns
    if "timeout" in str(exception_types).lower():
        return FlakynessCategory.INFRASTRUCTURE
    elif "random" in str(assertion_patterns).lower():
        return FlakynessCategory.INTERMITTENT
    # ... more patterns
    else:
        return FlakynessCategory.UNKNOWN
```

### Strategy 4: Artifact-Based Storage

**Goal**: Store detailed test failure information alongside execution results

**New Artifact Type**:
```python
class ArtifactType(str, Enum):
    # ... existing ...
    TEST_FAILURE_DETAIL = "test_failure_detail"

# In ExecutionResult artifact list:
ExecutionArtifact(
    artifact_type=ArtifactType.TEST_FAILURE_DETAIL,
    label="Test failure: test_calculation",
    content=json.dumps({
        "test_function": "test_calculation",
        "test_class": None,
        "assertion_message": "assert 5 == 6",
        "exception_type": "AssertionError",
        "source_location": "tests/test_foo.py:42",
        "exception_chain": [...],
    }),
)
```

## Implementation Roadmap

### Phase 1: Data Model Enhancement (Stages 1-2)

**Objectives**:
1. Extend FlakyTestResult with test name components
2. Add assertion message and traceback fields
3. Create TestFailureDetail model for TestSignal
4. Maintain backward compatibility

**Acceptance Criteria**:
- New fields added to FlakyTestResult
- to_dict() method updated
- TestFailureDetail model created
- All new fields optional with sensible defaults
- Existing tests still pass

### Phase 2: Pytest Plugin Enhancement (Stages 2-3)

**Objectives**:
1. Extract test function name from pytest.Item
2. Extract test class name (if applicable)
3. Parse nodeid to extract test_file
4. Extract assertion message from ExceptionInfo
5. Extract source location (file:line)

**Acceptance Criteria**:
- Plugin extracts all new fields
- Helper functions for assertion/traceback parsing
- Handles edge cases (module-level tests, non-assertion failures)
- Comprehensive unit tests for extraction logic

### Phase 3: Reporter Integration (Stages 3-4)

**Objectives**:
1. Update FlakyTestReporter to use new fields
2. Enhance flakiness categorization using assertion patterns
3. Include test name/assertion in failure reason
4. Build assertion pattern analysis

**Acceptance Criteria**:
- Flakiness categorization uses test details
- failure_reason includes test name + assertion
- Assertion pattern tracking implemented
- New metrics in FlakyTestMetric for assertion patterns

### Phase 4: Signal Integration & Visualization (Stages 4-5)

**Objectives**:
1. Update TestSignal to include failed_tests list
2. Create test failure detail artifacts
3. Add dashboard panels for assertion analysis
4. Documentation and examples

**Acceptance Criteria**:
- TestSignal carries individual test failure details
- Artifacts generated for each failed test
- Dashboard shows assertion context
- Integration documentation complete

## Specific Fields to Extract

### Test Identity Fields (Required)
- `test_file: str` — Path to test file (e.g., "tests/unit/test_foo.py")
- `test_class: str | None` — Name of test class if applicable (e.g., "TestClass")
- `test_function: str` — Test function name (e.g., "test_method")

### Exception/Assertion Fields (Required on Failure)
- `exception_type: str` — Exception class name (e.g., "AssertionError")
- `assertion_message: str | None` — Clean assertion message without traceback
- `exception_message: str` — Full exception message (may include assertion message)
- `source_location: str` — File and line where failure occurred (e.g., "tests/test_foo.py:42")

### Traceback Fields (Optional)
- `exception_chain: list[str]` — Full traceback as list of frames
- `exception_chain_simplified: str | None` — Single-line summary of chain

### Categorization Helpers
- `is_assertion_failure: bool` — True if exception is AssertionError
- `is_timeout_failure: bool` — True if timeout-related
- `is_error_failure: bool` — True if test errored (not failed assertion)

## Integration Points

### Downstream Systems

**1. Recovery Loop Classifier**
- Current: Maps ExecutionResult.failure_category → ExecutionFailureKind
- Enhanced: Can distinguish test assertion failures from other validation failures
- New category: `VALIDATION_FAILED_TEST_ASSERTION` (sub-category)

**2. Flakiness Detection**
- Current: Tracks failure rate per test
- Enhanced: Tracks failure patterns (same assertion always fails, different assertions fail)
- New metric: assertion_pattern_entropy

**3. Dashboard**
- Current: Shows pass/fail counts
- Enhanced: Shows specific failing assertions and test names
- New panels: Top failing assertions, assertion pattern trends

**4. Reporting**
- Current: "test_foo.py failed"
- Enhanced: "test_foo.py::TestClass::test_method: assertion 'result == 5' failed"

## Risk Assessment

### Technical Risks

**Risk 1: Pytest API Compatibility**
- Pytest internal APIs may change between versions
- **Mitigation**: Version-pin pytest, fallback to string parsing if APIs change

**Risk 2: Exception Parsing Complexity**
- Different exception types have different message formats
- Custom exceptions may not follow standard patterns
- **Mitigation**: Graceful degradation — if parsing fails, store raw exception

**Risk 3: Performance Impact**
- Extracting full tracebacks for every test may be expensive
- **Mitigation**: Extract only on failure, compress traceback summaries

### Data Quality Risks

**Risk 1: Assertion Message Accuracy**
- Pytest's assertion rewriting may not always produce clean messages
- **Mitigation**: Unit test extraction with real pytest output

**Risk 2: Encoding Issues**
- Exception messages may contain non-UTF8 characters
- **Mitigation**: Proper encoding/escaping in extraction functions

**Risk 3: Size Constraints**
- Storing full tracebacks for millions of test runs may consume storage
- **Mitigation**: Implement retention policies, compress old data

## Success Metrics

### Stage 0 Success Criteria

- ✅ All files containing failure categorization identified
- ✅ Current test failure capture mechanism documented
- ✅ Extraction points clearly identified
- ✅ Available pytest data enumerated
- ✅ Current limitations documented
- ✅ Technical plan created with 4 phases
- ✅ Specific fields to extract listed

### Overall Campaign Success Criteria (Future)

1. **Extraction Completeness**: 100% of failing tests capture function name, class name, assertion message
2. **Assertion Analysis**: Can group failures by assertion pattern with 90%+ accuracy
3. **Performance**: Test failure extraction adds <5% overhead to pytest runs
4. **Reliability**: Extraction succeeds for 99%+ of test failures across Python 3.8+
5. **Integration**: Test details flow through all layers (pytest → FlakyTestReporter → Dashboard)
6. **User Value**: Users can identify and categorize flaky tests by assertion pattern

## Conclusion

The OperationsCenter platform has a solid foundation for failure categorization across execution, recovery, and observer layers. **Test failures are currently captured at a high level but can be significantly enriched with fine-grained details about test names and assertion messages.**

The key insight is that **pytest provides all necessary data through its hooks and ExceptionInfo API**; the current implementation simply doesn't fully extract and propagate it.

**Recommended approach**: 
1. Enhance pytest plugin to extract full exception details
2. Extend FlakyTestResult data model with new fields
3. Update reporter analysis to use assertion patterns
4. Integrate with TestSignal for end-to-end visibility
5. Add dashboard panels for assertion-level debugging

This creates a clear data flow from pytest → FlakyTestResult → FlakyTestMetric → TestSignal → Dashboard, with detailed test failure information available at every step.
