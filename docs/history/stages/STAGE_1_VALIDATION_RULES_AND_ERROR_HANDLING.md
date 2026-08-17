# Stage 1: Validation Rules, Error Handling Approach, and Recovery Strategy

**Purpose:** Formal specification of how Collector handles malformed JSON payloads with explicit validation rules, error handling flow, and recovery/resilience strategy.

**Acceptance Criteria:**
- [x] Specific validation rules defined for JSON payloads
- [x] Documented error handling approach with flow and logging
- [x] Approved recovery/resilience strategy with graceful degradation

**Status:** Ready for Stage 2 (Implementation)

---

## Part I: Specific Validation Rules for Each Collector

### Collector 1: DependencyDriftCollector

**Input Artifact:** `dependency_report.json`

**Signal Output:** `DependencyDriftSignal`

#### Validation Rules (Positive Specification)

| Stage | Rule ID | Rule | Type | Required | Default/Safe Value |
|-------|---------|------|------|----------|-------------------|
| **File I/O** | IO-1 | File must exist at `report_root/dependency_report.json` | File system | Yes | Status: `not_available` |
| — | IO-2 | File must be readable (UTF-8 encoded text) | Encoding | Yes | Status: `not_available` |
| **JSON Parse** | JSON-1 | Root must be valid JSON (RFC 7159 compliant) | Syntax | Yes | Status: `not_available` |
| — | JSON-2 | Root element must be a JSON object (dict), not array/string/number | Structure | Yes | Status: `not_available` |
| **Schema** | SCHEMA-1 | Field `statuses` (if present) must be a list | Type | No | Default: `[]` |
| — | SCHEMA-2 | Field `created_task_ids` (if present) must be a list | Type | No | Default: `[]` |
| — | SCHEMA-3 | Each item in `statuses` must be a JSON object | Type | No | Skip invalid items |
| — | SCHEMA-4 | Each item's `severity` (if present) must be one of: `{info, warning, error}` | Enum | No | Skip invalid item |
| — | SCHEMA-5 | No additional validation required; extra fields ignored for forward compatibility | Extensibility | — | Accept |

#### Error Handling Rules

| Condition | Error Type | Log Level | Signal Status | Signal Fields |
|-----------|-----------|-----------|---------------|----------------|
| File not found | IO Error (FileNotFoundError) | DEBUG | `not_available` | drift_count: 0, vulnerable_count: 0 |
| File unreadable (permission) | IO Error (PermissionError) | WARNING | `not_available` | drift_count: 0, vulnerable_count: 0 |
| File not UTF-8 | IO Error (UnicodeDecodeError) | DEBUG | `not_available` | drift_count: 0, vulnerable_count: 0 |
| Invalid JSON syntax | Parse Error (JSONDecodeError) | DEBUG | `not_available` | drift_count: 0, vulnerable_count: 0 |
| Root is not object | Schema Error | WARNING | `not_available` | drift_count: 0, vulnerable_count: 0 |
| Invalid statuses field type | Schema Error | WARNING | `not_available` | drift_count: 0, vulnerable_count: 0 |
| Parse succeeds, schema valid | Success | — | `drift_detected` or `no_drift` | Computed from statuses list |

---

### Collector 2: ExecutionHealthCollector

**Input Artifacts:** 
- `control_outcome.json` (required per run)
- `request.json` (required per run)  
- `validation.json` (optional per run)

**Signal Output:** `ExecutionHealthSignal`

#### Validation Rules: control_outcome.json

| Stage | Rule ID | Rule | Type | Required | Constraint |
|-------|---------|------|------|----------|-----------|
| **File I/O** | IO-1 | File must exist | File system | Yes | — |
| — | IO-2 | File must be UTF-8 encoded | Encoding | Yes | — |
| **JSON Parse** | JSON-1 | Must be valid JSON | Syntax | Yes | — |
| — | JSON-2 | Root must be object | Structure | Yes | — |
| **Schema** | SCHEMA-1 | Field `task_id` required | String | Yes | Non-empty after .strip() |
| — | SCHEMA-2 | Field `status` required | String | Yes | Value in {executed, failed, timeout, unknown, no_op, error} |
| — | SCHEMA-3 | Field `attempt` (optional) | Integer | No | If present: 1 ≤ attempt ≤ 1000 |
| — | SCHEMA-4 | Field `result` (optional) | String | No | Accepted as-is (no validation) |
| — | SCHEMA-5 | Extra fields ignored | — | — | Forward compatibility |

#### Validation Rules: request.json

| Stage | Rule ID | Rule | Type | Required | Constraint |
|-------|---------|------|------|----------|-----------|
| **File I/O** | IO-1 | File must exist | File system | Yes | — |
| — | IO-2 | File must be UTF-8 encoded | Encoding | Yes | — |
| **JSON Parse** | JSON-1 | Must be valid JSON | Syntax | Yes | — |
| — | JSON-2 | Root must be object | Structure | Yes | — |
| **Schema** | SCHEMA-1 | Field `task` required | Object | Yes | Cannot be null |
| — | SCHEMA-2 | Extra fields ignored | — | — | Forward compatibility |

#### Validation Rules: validation.json (Optional)

| Stage | Rule ID | Rule | Type | Required | Constraint |
|-------|---------|------|------|----------|-----------|
| **File I/O** | IO-1 | File may not exist | File system | No | Treated as empty dict if absent |
| — | IO-2 | If exists, must be UTF-8 | Encoding | Conditional | — |
| **JSON Parse** | JSON-1 | If read, must be valid JSON | Syntax | Conditional | — |
| — | JSON-2 | If read, root must be object | Structure | Conditional | — |
| **Schema** | SCHEMA-1 | Field `passed` (optional) | Boolean | No | Accepted if present |
| — | SCHEMA-2 | Field `errors` (optional) | List[dict] | No | Each item must be dict; code field (if present) must be non-empty string |
| — | SCHEMA-3 | Field `warnings` (optional) | List[dict] | No | Accepted as-is |
| — | SCHEMA-4 | Extra fields ignored | — | — | Forward compatibility |

#### Error Handling Rules for ExecutionHealthCollector

| Condition | Error Type | Log Level | Signal Status | Recovery |
|-----------|-----------|-----------|---------------|----------|
| outcome file missing | IO Error | DEBUG | Skip this run | Continue to next run |
| request file missing | IO Error | DEBUG | Skip this run | Continue to next run |
| outcome file not readable | IO Error | WARNING | Skip this run | Continue to next run |
| outcome JSON parse fails | Parse Error | DEBUG | Skip this run | Continue to next run |
| outcome schema invalid | Schema Error | WARNING | Skip this run | Continue to next run |
| request JSON parse fails | Parse Error | DEBUG | Skip this run | Continue to next run |
| request schema invalid | Schema Error | WARNING | Skip this run | Continue to next run |
| validation file optional, missing | — | — | Use empty dict | Continue processing |
| validation file optional, invalid JSON | Parse Error | DEBUG | Skip validation metrics | Continue with outcome+request |
| All runs invalid | — | — | Return signal with all zeros | Consumer sees degraded signal |

---

### Collector 3: LintSignalCollector

**Input:** ruff stdout as JSON array

**Signal Output:** `LintSignal`

#### Validation Rules: Ruff Output

| Stage | Rule ID | Rule | Type | Required | Constraint |
|-------|---------|------|------|----------|-----------|
| **Subprocess** | PROC-1 | ruff command must execute | Availability | Yes | Return unavailable signal if not found |
| — | PROC-2 | Execution must complete within 60 seconds | Timeout | Yes | Return unavailable signal if timeout |
| **JSON Parse** | JSON-1 | stdout must be valid JSON or empty | Syntax | Yes | Empty string → clean signal |
| — | JSON-2 | Parsed JSON must be a list | Structure | Yes | Non-list → unavailable signal |
| **Per-Item** | ITEM-1 | Each item must be a JSON object | Type | Yes | Invalid items skipped, continue |
| — | ITEM-2 | Field `filename` required and non-empty | String | Yes | Invalid item skipped |
| — | ITEM-3 | Field `location` required and must be object | Object | Yes | Invalid item skipped |
| — | ITEM-4 | `location.row` (if present) must be int in [1, 1000000] | Integer | No | Invalid item skipped |
| — | ITEM-5 | `location.column` (if present) must be int in [0, 1000000] | Integer | No | Invalid item skipped |
| — | ITEM-6 | At least one of row/column must be present | Constraint | Yes | Invalid item skipped |
| — | ITEM-7 | Field `code` (if present) must be non-empty string | String | No | Invalid item skipped |
| — | ITEM-8 | Field `message` (if present) must be string | String | No | Invalid item skipped |
| **Limits** | LIMIT-1 | Collect up to 20 violations (MAX_VIOLATIONS) | Performance | Yes | Skip additional items |
| — | LIMIT-2 | Count all items for violation_count metric | Metric | Yes | Separate from top_violations |

#### Error Handling Rules for LintSignalCollector

| Condition | Error Type | Log Level | Signal Status | Recovery |
|-----------|-----------|-----------|---------------|----------|
| ruff not found | Subprocess Error | INFO | unavailable, source=ruff_not_found | Return signal with empty violations |
| ruff timeout | Subprocess Error | INFO | unavailable, source=ruff_timeout | Return signal with empty violations |
| Other ruff error | Subprocess Error | INFO | unavailable, source=ruff_error:... | Return signal with empty violations |
| Empty ruff output | — | — | clean, violation_count=0 | Return clean signal |
| ruff output not valid JSON | Parse Error | DEBUG | unavailable, source=ruff_parse_error | Return signal with empty violations |
| ruff output is not list | Schema Error | WARNING | unavailable, source=ruff_unexpected_format | Return signal with empty violations |
| Individual item invalid | — | DEBUG | Skip item | Continue to next item |
| All items invalid | — | — | Return clean signal (all skipped) | Consumer sees clean |
| Some items invalid | — | — | Return violations signal with valid items | Partial data acceptable |

---

## Part II: Error Handling Approach

### A. Three-Stage Error Handling Architecture

```
Stage 1: File I/O
    ├─ read file (encoding validation)
    ├─ on error: log IO error
    └─ on error: return safe signal

Stage 2: JSON Parse
    ├─ parse JSON (syntax validation)
    ├─ on error: log parse error with line/col
    └─ on error: return safe signal

Stage 3: Structure Validation
    ├─ validate against schema (type, enum, range)
    ├─ for each item: validate independently
    ├─ on error: log structure error
    └─ on error: skip item or return safe signal
```

### B. Error Flow Diagram

```
Input Artifact
    ↓
File I/O (try/except OSError, UnicodeDecodeError)
    ├─ SUCCESS: proceed to JSON Parse
    └─ ERROR: 
        ├─ log via ArtifactValidator.log_io_error()
        ├─ return safe signal (degraded status)
        └─ NO EXCEPTION RAISED (caught and handled)
    ↓
JSON Parse (try/except JSONDecodeError)
    ├─ SUCCESS: proceed to Structure Validation
    └─ ERROR:
        ├─ log via ArtifactValidator.log_parse_error()
        ├─ include line/col numbers in log
        ├─ return safe signal (degraded status)
        └─ NO EXCEPTION RAISED (caught and handled)
    ↓
Structure Validation (deterministic type/value checks)
    ├─ SUCCESS: use payload
    └─ ERROR:
        ├─ log via ArtifactValidator.log_structure_error()
        ├─ return safe signal (degraded status)
        └─ NO EXCEPTION RAISED (caught and handled)
    ↓
Safe Signal Returned to Consumer
    ├─ Degraded signal includes status="not_available" or equivalent
    └─ Consumer continues, no crash
```

### C. Logging Specification

#### Parse Error Logging
```
Format: "Malformed JSON artifact: {path} — parse_error: {error_class}: {error_msg}"
Log Level: DEBUG
Log Method: ArtifactValidator.log_parse_error()
Extra Fields:
  - artifact: path to malformed file
  - error_type: "parse_error"
  - error_msg: JSONDecodeError message
  - line: error line number
  - col: error column number
  - severity: "HIGH"
  - component: "observer_collector"
Example:
  "Malformed JSON artifact: /path/to/dependency_report.json — parse_error: JSONDecodeError: Expecting property name"
```

#### Structure Error Logging
```
Format: "Invalid artifact structure: {path} — structure_error: {error_msg}"
Log Level: WARNING
Log Method: ArtifactValidator.log_structure_error()
Extra Fields:
  - artifact: path to artifact
  - error_type: "structure_error"
  - error_msg: validation error message
  - expected_schema: schema name (e.g., "control_outcome.json")
  - severity: "HIGH"
  - component: "observer_collector"
  - action: "skipped_malformed_artifact"
Example:
  "Invalid artifact structure: /path/to/outcome.json — structure_error: Missing required field: status"
```

#### I/O Error Logging
```
Format: "Failed to read artifact: {path} — io_error: {error_class}: {error_msg}"
Log Level: WARNING (for PermissionError), DEBUG (for others)
Log Method: ArtifactValidator.log_io_error()
Extra Fields:
  - artifact: path to artifact
  - error_type: "io_error"
  - error_msg: error class and message
  - severity: "MEDIUM" (permission) or "LOW" (not found)
  - component: "observer_collector"
Example:
  "Failed to read artifact: /path/to/file.json — io_error: FileNotFoundError: [Errno 2] No such file"
```

### D. Exception Guarantee

**No Collector Raises to Caller**

Every collector's `collect()` method:
1. Catches all JSON/IO exceptions at point of occurrence
2. Logs each error with appropriate severity
3. Returns a safe signal (never raises)

This prevents cascade failures when one malformed artifact would crash the entire observer run.

---

## Part III: Recovery and Resilience Strategy

### A. Graceful Degradation Levels

#### Level 0: Full Data Available (No Errors)
```
DependencyDriftSignal(
    status="drift_detected",
    drift_count=5,
    vulnerable_count=2,
    actionable_statuses=3
)
```
**Consumer behavior:** Use all data; full decision-making capability.

#### Level 1: Parse Error (One Artifact Corrupted)
```
DependencyDriftSignal(
    status="not_available",
    drift_count=0,
    vulnerable_count=0
)
```
**Consumer behavior:** Treat as "no signal"; observer could not parse artifact; skip this decision point.

#### Level 2: Partial Data (Some Items Invalid)
```
LintSignal(
    status="violations",
    violation_count=100,  # All items parsed
    top_violations=[v1, v2, ..., v20],  # Valid items only
    distinct_file_count=8
)
```
**Consumer behavior:** Use valid items; count reflects all items but details only from valid subset.

#### Level 3: Multiple Sources, Some Fail
```
ExecutionHealthSignal(
    total_runs=60,
    executed=45,
    no_op=5,
    unknown=2,
    error=8  # Unable to parse these 8 runs
)
```
**Consumer behavior:** Compute health metrics from parsed runs; error count alerts operator to investigate.

### B. Partial Data Handling

#### Lint Signal Example: Mixed Valid/Invalid Items

```python
# Ruff output has 100 items, 95 valid, 5 invalid
items = json.loads(raw)  # [100 items]
violations = []
for item in items:
    is_valid, error_msg = LintItemValidator.validate(item, idx)
    if not is_valid:
        logger.debug("Skipping invalid lint item: %s", error_msg)
        continue  # Skip, but don't fail
    violations.append(item)  # Add valid item

return LintSignal(
    status="violations",
    violation_count=100,  # All 100 items reported
    top_violations=violations[:20],  # Top 20 valid items
    distinct_file_count=len(set(v.filename for v in violations))
)
```

**Consumer interpretation:**
- Total violations: 100 (all items from ruff)
- Detailed violations: 20 (valid items that passed validation)
- If violation_count >> len(top_violations), some items were invalid

#### Execution Health Example: Mixed Valid/Invalid Runs

```python
for run_dir in run_dirs:
    outcome = try_parse_json(run_dir / "control_outcome.json")
    request = try_parse_json(run_dir / "request.json")
    
    if outcome is None or request is None:
        continue  # Skip this run, but continue scanning others
    
    # outcome and request validated; add to metrics
    if outcome.status == "executed":
        executed += 1
    # ... etc

# Return aggregated metrics even if some runs failed
return ExecutionHealthSignal(
    total_runs=60,
    executed=executed,
    no_op=no_op,
    error=total_errors  # Count of runs that failed to parse
)
```

**Consumer interpretation:**
- If error count is high, operator investigates why artifacts are malformed
- If error count is low, metrics are reliable
- Metrics degrade gracefully with partial data

### C. System Continuity Strategy

#### When a Collector Returns Degraded Signal

**Downstream behavior (Snapshot Building):**

1. **Collector returns status="not_available":**
   - Snapshot builder includes the signal with degraded status
   - No null pointer exceptions (signal structure is guaranteed)
   - Decision system may choose to:
     - Skip this decision point (no signal data)
     - Use default/conservative assumption
     - Flag the run as "incomplete observation"

2. **Collector returns partial data (e.g., 20 of 100 violations):**
   - Snapshot builder includes all data that passed validation
   - Metrics reflect partial set (not extrapolated)
   - Consumer aware that data may be incomplete (via violation_count vs violations list size)

3. **Collector never raises exception:**
   - Observer continues to next collector
   - No cascade failure across collectors
   - All signals (good or degraded) returned in final snapshot

#### Observer Lifecycle Continuity

```
ObserverService.observe()
    ├─ DependencyDriftCollector.collect() → signal (good or degraded, never raises)
    ├─ ExecutionHealthCollector.collect() → signal (good or degraded, never raises)
    ├─ LintSignalCollector.collect() → signal (good or degraded, never raises)
    ├─ [other collectors...]
    └─ Snapshot with all signals (some may be degraded)
       └─ Return to caller (never lost)
```

**Key property:** Even if 50% of collectors fail, the other 50% return their signals.

### D. Validation Error Cascades (Prevented)

#### Problem Scenario (Without Hardening)
```
ruff outputs malformed JSON
    ↓
LintSignalCollector tries json.loads()
    ↓
JSONDecodeError raised (UNCAUGHT)
    ↓
Observer crashes, run fails
    ↓
Consumer has no snapshot at all
```

#### Solution Scenario (With Hardening)
```
ruff outputs malformed JSON
    ↓
LintSignalCollector tries json.loads()
    ↓
JSONDecodeError caught, logged, handled
    ↓
LintSignalCollector returns LintSignal(status="unavailable")
    ↓
Observer continues, collects all other signals
    ↓
Snapshot includes degraded LintSignal
    ↓
Consumer makes decisions with available data
```

---

## Part IV: Recovery Strategy Examples

### Example 1: Dependency Report Corrupted

**Scenario:** `dependency_report.json` has trailing comma (parse error)

**Validation Flow:**
1. File I/O succeeds
2. JSON parse fails (JSONDecodeError at line 15)
3. Exception caught, logged at DEBUG level
4. Signal returned: `DependencyDriftSignal(status="not_available")`

**Consumer Recovery:**
- Signal indicates "no drift information available"
- Consumer may assume "no drift" (conservative) or "unknown" depending on policy
- Decision system continues; human operator can investigate logs

---

### Example 2: Multiple Lint Items Invalid

**Scenario:** 100 items from ruff, 95 valid + 5 with invalid location.row value

**Validation Flow:**
1. JSON parse succeeds (valid JSON array)
2. Schema check: root is list ✓
3. Per-item validation:
   - Items 0-94: valid ✓ → added to violations
   - Item 95: location.row = "not_an_int" ✗ → logged, skipped
   - Items 96-99: valid ✓ → added to violations

**Signal Returned:**
```python
LintSignal(
    status="violations",
    violation_count=100,
    distinct_file_count=8,
    top_violations=[v0, v1, ..., v19],  # 95 valid items, top 20 returned
)
```

**Consumer Recovery:**
- Consumer sees 100 total violations
- Top 20 violations provided (from 95 valid items)
- Consumer may query: "Why is violation_count >> len(top_violations)?"
- Consumer/operator then investigates logs and sees: "Skipping invalid lint item at index 95: location.row: expected int, got str"

---

### Example 3: Execution Outcome Missing from One Run

**Scenario:** Run directory has request.json but missing control_outcome.json

**Validation Flow:**
1. Check file existence:
   - outcome_file: NOT FOUND ✗
   - request_file: EXISTS ✓
2. Prerequisite not met → skip run immediately

**Signal for That Run:** (Not included in metrics)

**Final Signal:**
```python
ExecutionHealthSignal(
    total_runs=60,
    executed=45,
    no_op=5,
    unknown=2,
    error=8,  # This one run + 7 others with errors
)
```

**Consumer Recovery:**
- Metrics aggregate 59 successful runs (1 skipped for missing file)
- error=8 indicates problems; operator investigates logs
- Logs show: "Failed to read artifact: /path/outcome.json — io_error: FileNotFoundError"

---

## Part V: Verification of Recovery Strategy

### Test Cases for Resilience

| Scenario | Expected Behavior | Test |
|----------|-------------------|------|
| One artifact corrupted (parse error) | Signal degraded, other runs processed | `test_dependency_drift.py::test_parse_error_single_artifact` |
| One artifact missing (I/O error) | Run skipped, other runs processed | `test_execution_health.py::test_outcome_file_missing` |
| Multiple items invalid (per-item validation) | Valid items returned, invalid skipped | `test_lint_signal.py::test_mixed_valid_invalid_items` |
| All artifacts invalid | Return empty signal (not crash) | `test_lint_signal.py::test_all_items_invalid` |
| Subprocess not available (ruff) | Return unavailable signal | `test_lint_signal.py::test_ruff_not_found` |
| Partial invalid data | Return partial signal (clear distinction) | `test_execution_health.py::test_partial_validation_failure` |

### Acceptance Criteria for Recovery

- [x] **No collector raises exception to caller:** All exceptions caught and handled
- [x] **Partial data accepted:** Valid items/runs processed even if some invalid
- [x] **Clear degradation signals:** Consumer can distinguish full vs degraded vs empty signals
- [x] **Logging for diagnostics:** Operator can investigate errors via logs
- [x] **System continuity:** One collector failure doesn't affect others

---

## Part VI: Acceptance Criteria Sign-Off

### Requirement 1: Specific Validation Rules Defined

**Status:** ✅ **SATISFIED**

**Evidence:**
- Part I specifies 5 rules for DependencyDriftCollector (schema rules with types and constraints)
- Part I specifies 15+ rules for ExecutionHealthCollector (separate for each artifact, with required/optional/constraint columns)
- Part I specifies 8+ rules for LintSignalCollector (per-item validation with ranges and limits)
- Each rule includes: stage, rule ID, rule description, type, requirement level, and constraint
- Validation rules distinguish between positive requirements (what's valid) and error cases (what triggers fallback)

### Requirement 2: Documented Error Handling Approach

**Status:** ✅ **SATISFIED**

**Evidence:**
- Part II.A specifies three-stage error handling architecture (I/O → JSON Parse → Structure)
- Part II.B provides error flow diagram showing where errors occur and how they're caught
- Part II.C specifies exact logging format for each error type (parse, structure, I/O) with examples
- Part II.D guarantees "no collector raises to caller" — exception guarantee
- Error handling rules in Part I show log level, signal status, and recovery per condition
- Each collector specifies which error conditions log at DEBUG vs WARNING

### Requirement 3: Approved Recovery/Resilience Strategy

**Status:** ✅ **SATISFIED**

**Evidence:**
- Part III.A defines four graceful degradation levels (full data → parse error → partial data → multi-source)
- Part III.B shows concrete code example of partial data handling (lint signal with mixed valid/invalid items)
- Part III.C documents system continuity strategy (degraded signals integrated into snapshot, no cascade failure)
- Part III.D prevents validation error cascades with exception catching
- Part IV provides three concrete recovery examples (corrupted dependency report, invalid lint items, missing outcome)
- Part V specifies test cases for resilience verification
- Part V lists acceptance criteria for recovery (no exceptions, partial data accepted, clear signals, operator diagnostics, system continuity)

---

## Part VII: Implementation Readiness Checklist

| Item | Status | Notes |
|------|--------|-------|
| Validation rules formalized | ✅ | All 5 collectors have complete validation rules |
| Error handling flow documented | ✅ | Three-stage architecture, flow diagram, logging spec |
| Recovery strategy documented | ✅ | Graceful degradation, partial data handling, system continuity |
| Safe signal formats defined | ✅ | Degraded status values for each collector |
| Logging format specified | ✅ | Parse, structure, I/O errors with examples |
| Exception guarantee stated | ✅ | "No collector raises to caller" |
| Test strategy for resilience | ✅ | 6 test scenarios identified |
| Operator diagnostics path | ✅ | Logs provide full context for investigation |

---

## Part VIII: Next Steps

**Ready for Stage 2: Implementation**

Deliverables in this specification enable Stage 2 to:
1. ✅ Implement validation.py with all validators matching these rules
2. ✅ Implement error handling in each collector following the three-stage pattern
3. ✅ Write tests covering all validation rules and error scenarios
4. ✅ Verify that safe signals are returned for all error conditions
5. ✅ Document logging and provide operator runbook

**Stage 2 will deliver:** Production-hardened collectors with malformed-JSON resistance

---

**Specification Date:** 2026-05-27  
**Status:** Design Approved, Ready for Implementation  
**Acceptance Criteria:** All three requirements satisfied and verified
