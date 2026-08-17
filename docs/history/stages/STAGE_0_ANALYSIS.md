# Stage 0: Collector JSON Handling Analysis

## Executive Summary

This document provides the baseline analysis for hardening OperationsCenter Collector against malformed JSON status payloads. The current implementation (as of commit fa79142) includes validation and error handling infrastructure, but vulnerabilities exist in:

1. **Parse-phase error handling** — inconsistent JSON decoding
2. **Structure validation** — some collectors lack schema validation
3. **Error recovery** — incomplete graceful degradation in edge cases
4. **Observability** — limited visibility into error frequency and patterns

---

## Part I: Code Paths Parsing JSON

### A. Current Implementation Status

The observer module has been **partially hardened** with a validation framework. Below are all entry points that parse JSON:

#### 1. **DependencyDriftCollector** (`collectors/dependency_drift.py`)
- **Files parsed:** `dependency_report.json`
- **Parse method:** `json.loads(text)`
- **Validation:** `DependencyReportValidator` (schema validation present)
- **Error handling:**
  - ✅ I/O errors caught (line 28-31)
  - ✅ JSONDecodeError caught (line 34-40)
  - ✅ Structure validation applied (line 42-50)
  - ✅ Logs security events via `ArtifactValidator.log_*` methods
  - ✅ Returns safe signal on all failure paths

#### 2. **ExecutionArtifactCollector** (`collectors/execution_health.py`)
- **Files parsed:** 
  - `control_outcome.json` (multiple per run_dir)
  - `request.json` (multiple per run_dir)
  - `validation.json` (optional per run_dir)
- **Parse method:** `json.loads(text)`
- **Validation:** 
  - `ExecutionOutcomeValidator` (outcome files)
  - `RequestValidator` (request files)
  - `ValidationHistoryValidator` (validation files)
- **Error handling:**
  - ✅ I/O errors caught (lines 59-62, 84-86, 115-119)
  - ✅ JSONDecodeError caught (lines 64-68, 88-92, 121-126)
  - ✅ Structure validation applied to outcome and request (lines 70-102)
  - ✅ Optional validation.json skips validation errors without crashing (lines 127-139)
  - ✅ Type coercion guards (lines 141-147)
  - ✅ Logs security events

#### 3. **ValidationHistoryCollector** (`collectors/validation_history.py`)
- **Files parsed:**
  - `control_outcome.json` (per run_dir)
  - `request.json` (per run_dir)
  - `validation.json` (optional per run_dir)
- **Parse method:** `json.loads(text)`
- **Validation:**
  - `ExecutionOutcomeValidator` (outcome files)
  - `RequestValidator` (request files)
  - `ValidationHistoryValidator` (validation files)
- **Error handling:**
  - ✅ I/O errors caught (lines 76-82, 104-110, 152-157)
  - ✅ JSONDecodeError caught (lines 83-89, 111-117, 158-163)
  - ✅ Structure validation applied
  - ✅ Optional validation.json handled gracefully (lines 164-176)
  - ✅ Logs security events

#### 4. **LintSignalCollector** (`collectors/lint_signal.py`)
- **Files parsed:** ruff stdout (JSON array of lint violations)
- **Parse method:** `json.loads(raw)` (line 45)
- **Validation:** `LintItemValidator` (per-item validation)
- **Error handling:**
  - ✅ JSONDecodeError caught (line 46-51)
  - ✅ Type validation for list/dict (lines 53-58)
  - ✅ Per-item validation with graceful skip (line 63-66)
  - ✅ Per-violation construction failure caught (lines 79-81)
  - ⚠️ Limited logging (only `logger.debug`)

#### 5. **CheckSignalCollector** (`collectors/check_signal.py`)
- **Files parsed:** Test log files (text parsing, NOT JSON)
- **Parse method:** `read_text(encoding="utf-8", errors="replace")`
- **No JSON parsing** — safe from JSON vulnerabilities
- ✅ Graceful unknown/missing file handling

### B. JSON Parsing Code Paths Summary

| Collector | Files | Validators | Parse Errors | Structure Errors | IO Errors | Logs |
|-----------|-------|-----------|--------------|-----------------|-----------|------|
| DependencyDrift | 1 JSON | ✅ Yes | ✅ | ✅ | ✅ | ✅ |
| ExecutionHealth | 3 JSON | ✅ Yes | ✅ | ✅ | ✅ | ✅ |
| ValidationHistory | 3 JSON | ✅ Yes | ✅ | ✅ | ✅ | ✅ |
| LintSignal | 1 JSON | ✅ Yes | ✅ | ✅ | ❌ | ⚠️ |
| CheckSignal | Text logs | — | — | — | ✅ | ✅ |

**Total JSON parse sites:** 8 (`json.loads()` calls across 3 collectors)

---

## Part II: Current Error Handling Approach

### A. Validation Framework Architecture

**Location:** `src/operations_center/observer/validation.py`

#### Core Components:

1. **ArtifactValidator (base class)**
   - `type_check()` — validates Python type matches expected
   - `enum_check()` — validates string is in allowed set
   - `range_check()` — validates int in [min, max]
   - `safe_get()` — nested dict access with type guards
   - `required_field()` — checks field existence and type
   - `is_nonempty_string()` — validates non-empty strings
   - **Logging methods:**
     - `log_parse_error()` — JSONDecodeError with line/col
     - `log_structure_error()` — schema validation failures
     - `log_io_error()` — file read failures

2. **Per-Artifact Validators (inherit from ArtifactValidator)**
   - `ExecutionOutcomeValidator` — validates `control_outcome.json`
   - `RequestValidator` — validates `request.json`
   - `ValidationHistoryValidator` — validates `validation.json`
   - `DependencyReportValidator` — validates `dependency_report.json`
   - `LintItemValidator` — validates individual ruff lint items

#### Validation Pattern (Two-Stage):

```python
# Stage 1: File I/O
try:
    text = file.read_text(encoding="utf-8")
except (OSError, UnicodeDecodeError) as e:
    ArtifactValidator.log_io_error(file, e)
    return safe_signal()

# Stage 2: JSON parsing
try:
    payload = json.loads(text)
except json.JSONDecodeError as e:
    ArtifactValidator.log_parse_error(file, e)
    return safe_signal()

# Stage 3: Structure validation
is_valid, error_msg = ValidatorClass.validate(payload)
if not is_valid:
    ArtifactValidator.log_structure_error(file, error_msg)
    return safe_signal()
```

### B. Security Logging (Stage 4 Addition)

**Location:** `src/operations_center/observer/security_logging.py`

- `ErrorSeverity` enum: LOW, MEDIUM, HIGH
- `ErrorCategory` enum: parse_error, structure_error, io_error
- `SecurityLogEntry` — structured log format with timestamp, artifact, error details
- `AlertCondition` — defines alert thresholds (4 conditions defined)
- `MalformedPayloadMetrics` — tracks error counts per category

**Alert Thresholds (defined but not integrated):**
1. `parse_error_spike` — 10 parse errors in 5 minutes → HIGH severity
2. `structure_error_surge` — 5 structure errors in 5 minutes → HIGH severity
3. `permission_denied_pattern` — 3 permission errors in 10 minutes → MEDIUM severity
4. `collector_health_degradation` — 5 total errors in 5 minutes → HIGH severity

---

## Part III: Potential Failure Modes

### Category A: Graceful Failures (Currently Handled)

These will not crash; they degrade to safe signal states:

| Mode | Scenario | Current Behavior | Risk |
|------|----------|------------------|------|
| **Missing file** | JSON file does not exist | Return unavailable/empty signal | ✅ Low |
| **Corrupt JSON syntax** | Unparseable JSON (e.g., trailing comma) | JSONDecodeError caught, logged, safe signal returned | ✅ Low |
| **Wrong root type** | Root is `[...]` or `"string"` instead of `{...}` | Type guard catches, logged, safe signal returned | ✅ Low |
| **Missing required field** | `"status"` or `"task_id"` absent | Validator catches, logged, safe signal returned | ✅ Low |
| **Invalid enum value** | `"status": "invalid_enum"` | Enum check catches, logged, safe signal returned | ✅ Low |
| **Type mismatch in field** | `"attempt": "not_an_int"` | Type check catches, logged, safe signal returned | ✅ Low |
| **File permission denied** | OSError on read | Logged as PermissionError (WARNING level), safe signal | ✅ Low |
| **Encoding error** | Non-UTF-8 bytes in file | UnicodeDecodeError caught, logged, safe signal | ✅ Low |

### Category B: Edge Cases (Partially Handled)

These have coverage but edge cases remain:

| Mode | Scenario | Current Behavior | Risk |
|------|----------|------------------|------|
| **Deeply nested missing path** | `safe_get(obj, ["a", "b", "c"])` where only "a" exists | Returns default (None) safely | ✅ Low |
| **Mixed valid/invalid items** | JSON array where 1/3 items invalid | Invalid items skipped via validation, valid items processed | ✅ Low |
| **Optional nested structure** | `validation.json` optional; when present but malformed | Handled with else clause after json.loads (line 127) | ✅ Low |
| **Large payloads** | Multi-MB JSON files | No size limit checks; could impact memory | ⚠️ Medium |
| **Deeply nested objects** | JSON with 100+ levels | No recursion depth limit; could stack overflow | ⚠️ Medium |
| **Unicode edge cases** | Null bytes, lone surrogates, BOM prefixes | `utf-8` decoder with `errors="replace"` strips anomalies | ⚠️ Medium |

### Category C: Not Currently Handled

These scenarios are not protected:

| Mode | Scenario | Current Behavior | Risk | Acceptance? |
|------|----------|------------------|------|-------------|
| **Unbounded memory growth** | JSON array with 1M+ items | All loaded into memory; could OOM | 🔴 High | Defer to Phase 2 |
| **JSON bomb (billion laughs)** | Compressed ZIP with decompressing to 10GB | No defense; depends on gzip limit | 🔴 High | Defer to Phase 2 |
| **Circular structure** | JSON reference cycle (not valid JSON, but if it were) | Python json module doesn't support; not a risk | ✅ Low | N/A |
| **Numeric overflow** | `"attempt": 999999999999999999999999999999` | Python int handles arbitrary precision; safe | ✅ Low | N/A |
| **Null byte injection** | `"field": "value\x00hidden"` | No sanitization; stored as-is | ⚠️ Medium | Defer to Phase 2 |
| **Timing side-channel on valid/invalid** | Attacker measures parse time to infer schema | JSONDecodeError timing differs from validator timing | ⚠️ Low | Defer to Phase 3 |

---

## Part IV: Current State Scorecard

### Strengths ✅

1. **Two-stage validation** — all collectors parse then validate
2. **Error type segregation** — I/O, parse, and structure errors handled separately
3. **Graceful degradation** — every error path returns safe signal
4. **Security logging** — all errors logged with severity/category
5. **Type guards** — comprehensive validators for all artifact types
6. **Optional field handling** — validation.json treated as optional
7. **Logging framework** — structured security logs ready (Stage 4)

### Gaps ⚠️

1. **Resource limits** — no size/depth checks on JSON payloads
2. **Alert integration** — alerting definitions exist but not wired to monitoring
3. **Lint collector logging** — uses `logger.debug` only, no security event structure
4. **Observability metrics** — alert conditions defined but metrics not collected
5. **Performance** — no timeouts on json.loads(); could hang on malicious input
6. **Unicode normalization** — accepts any UTF-8 (including anomalies like lone surrogates)

### Deferred (Out of Scope) 🔄

Per the backlog, Stage 4 (security logging) is complete. The following are **not** in scope for hardening:
- Rate limiting at source (per ADR guidance: clients responsible)
- Signature verification (assumes artifact source is trusted)
- Compression bomb detection (depends on gzip limits upstream)

---

## Part V: Integration Points Requiring Attention

### Code Dependencies

```
validators.py (base)
  ├── dependency_drift.py (uses DependencyReportValidator)
  ├── execution_health.py (uses ExecutionOutcomeValidator, RequestValidator, ValidationHistoryValidator)
  ├── validation_history.py (uses ExecutionOutcomeValidator, RequestValidator, ValidationHistoryValidator)
  └── lint_signal.py (uses LintItemValidator)
  
security_logging.py
  └── [Not yet integrated into collectors]
```

### Test Coverage

Tests are located in `tests/observer/test_collectors_hardening/`:
- `test_validation_helpers.py` — 22 tests for validators
- `test_dependency_drift.py` — 16 tests covering crash fix
- `test_execution_health.py` — 19 tests for malformed artifacts

**Gap:** LintSignalCollector tests do not exercise JSON parse error paths.

---

## Part VI: Failure Mode Examples

### Example 1: Truncated JSON (Parse Error)

**Input:**
```json
{"statuses": [{"severity": "error"}, {"sev
```

**Handling:**
```python
try:
    payload = json.loads(text)  # Raises JSONDecodeError: Expecting value: line 1 column 63
except json.JSONDecodeError as e:
    ArtifactValidator.log_parse_error(
        candidate, e, context={"collector": "DependencyDriftCollector"}
    )
    # Logs: "Malformed JSON artifact: ... — parse_error: Expecting value: line 1 column 63"
    return DependencyDriftSignal(status="not_available")  # ✅ Safe return
```

**Result:** ✅ Gracefully degraded; logged with line/col info

### Example 2: Wrong Status Enum (Structure Error)

**Input:**
```json
{"task_id": "xyz", "status": "pending"}  // "pending" not in allowed set
```

**Handling:**
```python
payload = json.loads(text)  # ✅ Parses fine
is_valid, error_msg = ExecutionOutcomeValidator.validate(payload)
# is_valid = False, error_msg = "status 'pending' not in allowed values: ..."
if not is_valid:
    ArtifactValidator.log_structure_error(outcome_file, error_msg)
    # Logs: "Invalid artifact structure: ... — structure_error: status 'pending' not in ..."
    return signal(...)  # ✅ Safe return
```

**Result:** ✅ Gracefully degraded; logged with schema mismatch detail

### Example 3: Mixed Valid/Invalid Lint Items

**Input:**
```json
[
  {"filename": "a.py", "location": {"row": 1}, ...},  // ✅ Valid
  {"filename": "b.py"},  // ❌ Missing "location"
  {"filename": "c.py", "location": {"row": 2}, ...}   // ✅ Valid
]
```

**Handling:**
```python
items = json.loads(raw)  # ✅ Parses (valid JSON array)
violations = []
for idx, item in enumerate(items):
    is_valid, error_msg = LintItemValidator.validate(item, idx)
    if not is_valid:
        logger.debug("Skipping invalid lint item: %s", error_msg)  # ⚠️ Only debug level
        continue  # Skip this item
    # Process valid items
```

**Result:** ⚠️ Valid items are processed, invalid skipped, but only debug-level logging

---

## Part VII: Recommendations for Next Phases

### Phase 2 (Resource Limits)
1. Add `max_json_size` parameter (default 10MB)
2. Add `max_nesting_depth` parameter (default 20)
3. Validate before full parse: `if len(text) > max_size: raise PayloadTooLargeError`

### Phase 3 (Observability Integration)
1. Wire alert conditions from `security_logging.py` to metrics collector
2. Implement sliding-window time aggregation for alert evaluation
3. Post alerts to observability backend (Prometheus/Grafana)

### Phase 4 (Performance Hardening)
1. Add timeout to `json.loads()` (not natively supported; use `signal.alarm()` or async)
2. Add streaming JSON parser for large arrays
3. Benchmark parse time on representative payloads

---

## Appendix: File Locations

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| Base validator | `validation.py` | 1-561 | ✅ Implemented |
| Security logging defs | `security_logging.py` | 1-100+ | ✅ Implemented |
| DependencyDrift | `collectors/dependency_drift.py` | 20-73 | ✅ Hardened |
| ExecutionHealth | `collectors/execution_health.py` | 24-180 | ✅ Hardened |
| ValidationHistory | `collectors/validation_history.py` | 38-213 | ✅ Hardened |
| LintSignal | `collectors/lint_signal.py` | 21-89 | ⚠️ Partial |
| CheckSignal | `collectors/check_signal.py` | 23-119 | ✅ N/A (no JSON) |
| Tests | `tests/observer/test_collectors_hardening/` | — | ✅ 57 tests |

---

## Sign-Off

**Analysis completed:** 2026-05-27
**Stage 0 acceptance criteria:**
- ✅ All code paths that parse JSON identified (8 sites across 3 collectors)
- ✅ Current error handling documented (two-stage validation + security logging)
- ✅ Potential failure modes listed (graceful: 8, edge cases: 5, unhandled: 6)
- ✅ Integration points noted and scorecard provided

**Next step:** Stage 1 (Design phase) — define specification for resource limits and alert thresholds.
