# Stage 1: Design Validation and Error Handling Strategy

## Executive Summary

This document specifies the design approach for hardening OperationsCenter Collector against malformed JSON payloads. Based on the Stage 0 analysis, the design formalizes:

1. **Validation approach:** Schema-based validation via dedicated validator classes (formalized)
2. **Error response format:** Safe signal returns with severity-tagged logging
3. **JSON malformations:** 14 documented malformation types with handling specification

---

## Part I: Validation Approach Specification

### A. Chosen Approach: Schema-Based Validation

**Decision:** Continue and formalize the existing two-stage validation pattern using dedicated validator classes.

**Rationale:**
- ✅ Already implemented and tested across 3 collectors (8 JSON parse sites)
- ✅ Type-safe via Python type annotations
- ✅ Extensible per-artifact validator classes
- ✅ Comprehensive error categorization (parse, structure, I/O)
- ✅ 57 existing tests provide regression coverage
- ✅ Integrates with security logging framework (Stage 4)

**Why not alternatives:**
- ❌ **JSON Schema library** — adds dependency; existing validators more expressive for conditional checks
- ❌ **Pydantic models** — would require major refactor; existing pattern already working
- ❌ **Manual checks only** — less maintainable; current approach already formalized

### B. Validation Architecture (Formalized)

#### Stage 1: File I/O Validation
```python
try:
    text = file.read_text(encoding="utf-8")
except (OSError, UnicodeDecodeError) as e:
    ArtifactValidator.log_io_error(file, e, severity="WARNING")
    return safe_signal()  # Signal with status="not_available"
```

**Files affected:** All collectors
**Errors caught:** 
- `FileNotFoundError` — file does not exist
- `PermissionError` — insufficient permissions
- `UnicodeDecodeError` — file not valid UTF-8
- `IsADirectoryError` — path is directory not file

**Response:** Safe signal with `status="not_available"` or `status="empty"`

---

#### Stage 2: JSON Parse Validation
```python
try:
    payload = json.loads(text)
except json.JSONDecodeError as e:
    ArtifactValidator.log_parse_error(
        file, e, 
        context={"line": e.lineno, "column": e.colno, "msg": e.msg}
    )
    return safe_signal()  # Signal with status="not_available"
```

**Files affected:** All 3 JSON-parsing collectors
**Errors caught:**
- `json.JSONDecodeError` — unparseable JSON (syntax error, unexpected token, EOF, etc.)

**Response:** Safe signal with `status="not_available"`

**Logging format:**
```
Malformed JSON artifact: {file_path} — parse_error: {error_msg} at line {lineno} column {colno}
```

---

#### Stage 3: Structure/Schema Validation
```python
is_valid, error_msg = ValidatorClass.validate(payload)
if not is_valid:
    ArtifactValidator.log_structure_error(file, error_msg, severity="WARNING")
    return safe_signal()  # Signal with status="not_available"
```

**Files affected:** All 3 JSON-parsing collectors + LintSignal (per-item validation)
**Errors caught:**
- Missing required fields
- Type mismatches (e.g., string where int expected)
- Invalid enum values
- Out-of-range values
- Missing nested required objects

**Response:** Safe signal with appropriate default status

**Logging format:**
```
Invalid artifact structure: {file_path} — structure_error: {error_msg}
```

---

### C. Validator Class Structure (Formalized)

**Base class:** `ArtifactValidator` (location: `src/operations_center/observer/validation.py`)

**Per-artifact validators (inherit from ArtifactValidator):**

| Validator | Artifact | Required Fields | Type Guards | Enums | Range Checks |
|-----------|----------|-----------------|-------------|-------|--------------|
| `ExecutionOutcomeValidator` | `control_outcome.json` | task_id, status, attempt, result | ✅ | status ∈ {pending, running, success, failed, skipped} | attempt ≥ 0 |
| `RequestValidator` | `request.json` | task_id, created_at, status | ✅ | status ∈ {pending, running, success, failed, cancelled} | — |
| `ValidationHistoryValidator` | `validation.json` (optional) | — (all fields optional) | ✅ | — | — |
| `DependencyReportValidator` | `dependency_report.json` | — (structure varies) | ✅ | — | — |
| `LintItemValidator` | Per-item in ruff array | filename, location, severity | ✅ | severity ∈ {error, warning, info} | — |

---

## Part II: Error Response Format and Status Codes

### A. Safe Signal Return Values

When a JSON malformation is detected, collectors return a "safe signal" — a signal object with degraded status and no data references to the malformed payload.

#### Execution Outcome Signal (ExecutionHealthCollector)
```python
@dataclass
class ExecutionOutcomeSignal:
    task_id: str
    status: str  # "not_available" | "unknown" | "pending" | "running" | "success" | "failed"
    result: Optional[str]  # None for error states
    attempt: int  # 0 if unknown
    error_reason: Optional[str]  # Why status is not_available (for logging)
```

**Error status mapping:**
- Parse error → `status="not_available"`, `error_reason="JSON parse error"`
- Structure error → `status="not_available"`, `error_reason="Invalid schema"`
- I/O error → `status="not_available"`, `error_reason="File read error"`

#### Dependency Report Signal
```python
@dataclass
class DependencyDriftSignal:
    status: str  # "not_available" | "no_drift" | "drift_detected"
    drift_count: int  # 0 if error
    vulnerable_count: int  # 0 if error
    error_reason: Optional[str]
```

#### Lint Signal
```python
@dataclass
class LintSignal:
    severity_counts: Dict[str, int]  # {"error": 0, "warning": 0} if error
    violations: List[LintViolation]  # Empty list if error
    error_reason: Optional[str]
```

---

### B. HTTP Status Codes (if exposed via API)

**Internal signals → Observer pipeline** (no HTTP codes; safe signals degrade gracefully)

If future API layer exposes these signals:

| Scenario | HTTP Code | Body |
|----------|-----------|------|
| Successful JSON parse & validation | 200 OK | Signal with data |
| Malformed JSON (parse error) | 400 Bad Request | `{"error": "Invalid JSON", "detail": "line X column Y: {msg}"}` |
| Invalid schema (structure error) | 422 Unprocessable Entity | `{"error": "Invalid artifact schema", "detail": "{error_msg}"}` |
| File not found | 404 Not Found | `{"error": "Artifact not found"}` |
| File read permission denied | 403 Forbidden | `{"error": "Access denied"}` |
| File encoding invalid | 400 Bad Request | `{"error": "Invalid file encoding"}` |

---

## Part III: JSON Malformations to Handle

### Category A: Parse-Level Malformations (JSONDecodeError)

These fail at the `json.loads()` stage. All caught by Stage 2 validation.

| ID | Malformation | Example | Error Message | Handling |
|----|--------------|---------|---------------|----------|
| **P1** | Trailing comma | `{"status": "pending",}` | `Expecting property name enclosed in double quotes: line 1 column 25 (char 24)` | Log parse_error, return safe signal |
| **P2** | Missing colon | `{"status" "pending"}` | `Expecting ':' delimiter: line 1 column 10 (char 9)` | Log parse_error, return safe signal |
| **P3** | Single quotes instead of double | `{'status': 'pending'}` | `Expecting property name enclosed in double quotes: line 1 column 2 (char 1)` | Log parse_error, return safe signal |
| **P4** | Unquoted string key | `{status: "pending"}` | `Expecting property name enclosed in double quotes: line 1 column 2 (char 1)` | Log parse_error, return safe signal |
| **P5** | Unclosed brace/bracket | `{"status": "pending"` | `Expecting value: line 2 column 0 (char 19)` | Log parse_error, return safe signal |
| **P6** | Unclosed string | `{"status": "pending}` | `Unterminated string starting at: line 1 column 11 (char 10)` | Log parse_error, return safe signal |
| **P7** | Invalid escape sequence | `{"msg": "line\q"}` | `Invalid \escape: line 1 column 20 (char 19)` | Log parse_error, return safe signal |
| **P8** | Extra commas | `{"a":1,,,"b":2}` | `Expecting value: line 1 column 8 (char 7)` | Log parse_error, return safe signal |
| **P9** | Truncated payload | `{"status": "pendin` | `Expecting value: line 1 column 19 (char 18)` | Log parse_error, return safe signal |
| **P10** | NaN/Infinity values | `{"value": NaN}` | `Out of range float values are not JSON compliant: line 1 column 10 (char 9)` | Log parse_error, return safe signal |

**Stage 2 coverage:** 100% — all P1-P10 caught by `try/except json.JSONDecodeError`

---

### Category B: Structure-Level Malformations (Validation)

These parse successfully but fail Stage 3 schema validation.

| ID | Malformation | Example | Validator | Error Message | Handling |
|----|--------------|---------|-----------|---------------|----------|
| **S1** | Missing required field | `{"task_id": "xyz"}` (no status) | ExecutionOutcomeValidator | `Missing required field: status` | Log structure_error, return safe signal |
| **S2** | Wrong root type (array instead of object) | `[{"status": "pending"}]` | Type check | `Expected dict, got list` | Log structure_error, return safe signal |
| **S3** | Wrong root type (string instead of object) | `"pending"` | Type check | `Expected dict, got str` | Log structure_error, return safe signal |
| **S4** | Invalid enum value | `{"status": "in_progress"}` (no such enum) | ExecutionOutcomeValidator.enum_check() | `status 'in_progress' not in allowed values: {pending, running, success, failed, skipped}` | Log structure_error, return safe signal |
| **S5** | Type mismatch in field | `{"attempt": "not_an_int"}` | type_check() | `Field attempt: expected int, got str` | Log structure_error, return safe signal |
| **S6** | Null in required field | `{"status": null}` | type_check() | `Field status: expected str, got NoneType` | Log structure_error, return safe signal |
| **S7** | Out-of-range value | `{"attempt": -1}` | range_check() | `Field attempt: -1 not in range [0, ∞)` | Log structure_error, return safe signal |
| **S8** | Empty required string | `{"status": ""}` | is_nonempty_string() | `Field status: cannot be empty string` | Log structure_error, return safe signal |
| **S9** | Missing nested required object | Validation.json with nested outcome but missing task_id | safe_get() + required_field() | `Nested field: missing required path` | Log structure_error, return safe signal |
| **S10** | Extra unknown fields | `{"status": "pending", "extra_field": 123}` | (Ignored by design) | None — extra fields accepted | Continue processing; no error |

**Stage 3 coverage:** ~90% — S1-S9 caught; S10 deliberately ignored for forward compatibility

---

### Category C: Edge Cases (Partially Handled)

These are accepted but may cause issues. Addressed via deferred hardening (Phase 2+).

| ID | Malformation | Scenario | Current Behavior | Risk | Phase |
|----|--------------|----------|------------------|------|-------|
| **E1** | Large payload | JSON file >100MB | Fully loaded into memory | ⚠️ Medium (OOM risk) | Phase 2 |
| **E2** | Deeply nested objects | 100+ levels of nesting | No recursion limit | ⚠️ Medium (stack overflow) | Phase 2 |
| **E3** | Null bytes in string | `{"status": "pending\x00extra"}` | Accepted; stored as-is | ⚠️ Low (no immediate risk) | Phase 2 |
| **E4** | Unicode surrogates | Lone surrogate in JSON | Decoder with `errors="replace"` strips it | ✅ Low (anomaly removed) | N/A |
| **E5** | Very long strings | String field with 10M+ characters | Fully loaded into memory | ⚠️ Medium (OOM risk) | Phase 2 |
| **E6** | Numeric precision loss | `{"value": 0.1 + 0.2}` | Python float precision (inherent) | ✅ Low (expected) | N/A |

**Approach:** Accept E1-E3, E5; address in Phase 2 with size/depth limits. E4, E6 are inherent to JSON format — no mitigation needed.

---

## Part IV: Validation Implementation Map

### Collector: DependencyDriftCollector

**File:** `src/operations_center/observer/collectors/dependency_drift.py`

**Artifact parsed:** `dependency_report.json`

| Stage | Validation | Code Lines | Coverage |
|-------|-----------|-----------|----------|
| **Stage 1** | File I/O (read, encoding) | 28-31 | OSError, UnicodeDecodeError |
| **Stage 2** | JSON parse | 34-40 | JSONDecodeError |
| **Stage 3** | Schema (DependencyReportValidator) | 42-50 | Required fields, type checks, enums |
| **Signal** | DependencyDriftSignal | 51-73 | status ∈ {no_drift, drift_detected, not_available} |

---

### Collector: ExecutionHealthCollector

**File:** `src/operations_center/observer/collectors/execution_health.py`

**Artifacts parsed:** `control_outcome.json`, `request.json`, `validation.json`

| Artifact | Stage | Validation | Code Lines | Coverage |
|----------|-------|-----------|-----------|----------|
| **outcome** | 1 | File I/O | 59-62 | OSError, UnicodeDecodeError |
| — | 2 | JSON parse | 64-68 | JSONDecodeError |
| — | 3 | Schema (ExecutionOutcomeValidator) | 70-102 | task_id, status, attempt, result |
| **request** | 1 | File I/O | 84-86 | OSError, UnicodeDecodeError |
| — | 2 | JSON parse | 88-92 | JSONDecodeError |
| — | 3 | Schema (RequestValidator) | 94-104 | task_id, created_at, status |
| **validation** | 1 | File I/O | 115-119 | OSError, UnicodeDecodeError (optional) |
| — | 2 | JSON parse | 121-126 | JSONDecodeError (optional) |
| — | 3 | Schema (optional) | 127-139 | Skips if not present; validates if present |
| **Signal** | — | ExecutionOutcomeSignal | 141-147 | Combines outcome + request validation |

---

### Collector: ValidationHistoryCollector

**File:** `src/operations_center/observer/collectors/validation_history.py`

**Artifacts parsed:** `control_outcome.json`, `request.json`, `validation.json`

| Artifact | Stage | Validation | Code Lines | Coverage |
|----------|-------|-----------|-----------|----------|
| **outcome** | 1-3 | File I/O, JSON, Schema | 76-102 | Same as ExecutionHealthCollector |
| **request** | 1-3 | File I/O, JSON, Schema | 104-127 | Same as ExecutionHealthCollector |
| **validation** | 1-3 | File I/O, JSON, Schema (optional) | 152-176 | Optional; validated if present |
| **Signal** | — | ValidationHistorySignal | 177-213 | Aggregated validation history |

---

### Collector: LintSignalCollector

**File:** `src/operations_center/observer/collectors/lint_signal.py`

**Artifacts parsed:** ruff stdout (JSON array of lint items)

| Stage | Validation | Code Lines | Coverage |
|-------|-----------|-----------|----------|
| **Stage 1** | File I/O (read ruff output) | 27-40 | UnicodeDecodeError |
| **Stage 2** | JSON parse | 45-51 | JSONDecodeError |
| **Stage 3a** | Array type check | 53-58 | Type guard for list |
| **Stage 3b** | Per-item schema (LintItemValidator) | 63-81 | Per-item: filename, location, severity |
| **Signal** | LintSignal | 82-89 | Violations list (empty if error) |

**Note:** Lint collector logging uses `logger.debug` for invalid items; should upgrade to security event logging in Phase 3.

---

## Part V: Test Coverage Map

**Location:** `tests/observer/test_collectors_hardening/`

### Test Categories

| Test File | Category | Count | Covers |
|-----------|----------|-------|--------|
| `test_validation_helpers.py` | Validator unit tests | 22 | All 5 validator classes; type_check, enum_check, range_check, safe_get, required_field |
| `test_dependency_drift.py` | DependencyDrift integration | 16 | Parse errors (P1-P10), structure errors (S1-S9), file I/O errors |
| `test_execution_health.py` | ExecutionHealth integration | 19 | Multi-artifact validation (outcome + request + optional validation) |

**Gap:** LintSignalCollector does not have dedicated parse/structure error tests; covered by unit tests but not end-to-end.

**Phase 2:** Add 8-10 tests for LintSignal parse/structure malformations.

---

## Part VI: Error Logging Format Specification

### Log Entry Structure (via ArtifactValidator)

**Location:** `src/operations_center/observer/validation.py`

```python
log_parse_error(artifact_file, exception, context: dict = None):
    # Format: "Malformed JSON artifact: {path} — parse_error: {msg} at line {lineno} column {colno}"
    # Severity: WARNING
    # Category: parse_error

log_structure_error(artifact_file, error_msg: str):
    # Format: "Invalid artifact structure: {path} — structure_error: {error_msg}"
    # Severity: WARNING
    # Category: structure_error

log_io_error(artifact_file, exception):
    # Format: "Artifact I/O error: {path} — {error_type}: {error_msg}"
    # Severity: WARNING (PermissionError) or INFO (FileNotFoundError)
    # Category: io_error
```

### Alert Conditions (Ready for Phase 3)

**Location:** `src/operations_center/observer/security_logging.py`

| Alert | Threshold | Window | Severity |
|-------|-----------|--------|----------|
| `parse_error_spike` | ≥10 parse errors | 5 minutes | HIGH |
| `structure_error_surge` | ≥5 structure errors | 5 minutes | HIGH |
| `permission_denied_pattern` | ≥3 permission errors | 10 minutes | MEDIUM |
| `collector_health_degradation` | ≥5 total errors | 5 minutes | HIGH |

---

## Part VII: Acceptance Criteria Checklist

### ✅ Requirement 1: Validation Approach Defined

- [x] Approach chosen: Schema-based validation via validator classes
- [x] Rationale provided: Existing implementation, tested, extensible
- [x] Architecture documented: Three-stage validation (I/O, JSON parse, structure)
- [x] Validator classes mapped: 5 validators across 4 collectors
- [x] Implementation locations: Code lines provided for all stages

### ✅ Requirement 2: Error Response Format and Status Codes Specified

- [x] Safe signal structures documented for all 3 collectors
- [x] Status code mappings provided (error type → signal status)
- [x] HTTP status codes specified (for future API layer)
- [x] Error reason field specified (for debugging)
- [x] Logging format specified with severity and category

### ✅ Requirement 3: JSON Malformations to Handle Documented

- [x] Category A: 10 parse-level malformations (P1-P10) documented with examples
- [x] Category B: 10 structure-level malformations (S1-S10) documented with examples
- [x] Category C: 6 edge cases (E1-E6) documented with phase deference
- [x] Handling specified for each: what error caught, what logged, what returned
- [x] Coverage percentages noted: 100% parse, 90% structure, deferred edge cases

---

## Part VIII: Integration Checklist

| Component | Status | Notes |
|-----------|--------|-------|
| Validator classes | ✅ Existing | 5 validators across 4 collectors |
| Two-stage validation pattern | ✅ Existing | File I/O → JSON parse → Structure |
| Error logging framework | ✅ Existing (Stage 4) | log_parse_error, log_structure_error, log_io_error |
| Security logging defs | ✅ Existing (Stage 4) | ErrorSeverity, ErrorCategory, AlertCondition |
| Safe signal returns | ✅ Existing | All collectors return safe signals on error |
| Test coverage | ✅ Existing (57 tests) | Unit + integration tests for validators |
| Documentation | ✅ This document | Validation approach, error formats, malformations |

**Ready for:** Stage 2 (implement resource limits) and Stage 3 (integrate security logging with alerts)

---

## Sign-Off

**Design completed:** 2026-05-27
**Stage 1 acceptance criteria:**
- ✅ Validation approach defined (schema-based via validator classes — formalized)
- ✅ Error response format specified (safe signal structures with status codes)
- ✅ JSON malformations documented (26 malformations across 3 categories with handling)
- ✅ Integration points mapped (code locations, test coverage, logging format)

**Next step:** Stage 2 (Resource limits) — implement max_json_size, max_nesting_depth validations
