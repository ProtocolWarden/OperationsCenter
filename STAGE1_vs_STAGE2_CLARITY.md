# Stage 1 vs Stage 2: Clear Separation

**Purpose**: Explicitly distinguish Stage 1 (documentation) from Stage 2 (implementation) to prevent conflation.

---

## What Is Stage 1: Documentation

### ✅ Stage 1 Includes

**Location**: Single file: `src/operations_center/observer/models.py`

**Type**: Docstrings only (pure text documentation)

**Examples of Stage 1 deliverables**:
```python
class CheckSignal(BaseModel):
    """Test execution results and status.

    Represents the outcome of test runs...

    Attributes:
        status: Overall test status...
        test_count: Total number of tests...
        source: Name of the tool/framework...
        observed_at: Timestamp when the test execution completed. Optional because:
            - Test runs may not provide timing information
            - Tests may not have run yet (deferred)
            - Status may be inferred from snapshot state
            If None, use snapshot.observed_at as fallback: `signal.observed_at or snapshot.observed_at`
        summary: Human-readable summary...

    When observed_at is used in derivers, prefer the signal-level value:
        # In derivers that access test_signal
        observed_at = signal.test_signal.observed_at or snapshots[0].observed_at
    """
    status: str
    test_count: int | None = None
    source: str | None = None
    observed_at: datetime | None = None  # ← FIELD DEFINITION UNCHANGED
    summary: str | None = None
```

**Stage 1 deliverables**:
- ✅ Module-level docstring explaining timestamp strategy
- ✅ 6 signal class docstrings with "observed_at: Timestamp when..." sections
- ✅ "Why optional:" rationale in each signal
- ✅ Code example showing fallback pattern: `signal.X.observed_at or snapshots[0].observed_at`
- ✅ RepoStateSnapshot docstring with "Timestamp Semantics" section
- ✅ Safe fallback pattern documentation

**Stage 1 does NOT include**:
- ❌ Any changes to field definitions
- ❌ Any code that USES the fallback pattern
- ❌ Any deriver modifications
- ❌ Any test additions
- ❌ Any logic changes

---

## What Is Stage 2: Implementation

### ❌ Stage 2 Implementation (NOT PART OF STAGE 1)

**Location**: Multiple files under `src/operations_center/insights/derivers/`

**Type**: Actual code that implements the fallback pattern

**Example of Stage 2 implementation** (NOT IN STAGE 1):
```python
# File: src/operations_center/insights/derivers/architecture_drift.py
# This code SHOULD NOT be in Stage 1

class ArchitectureDriftDeriver:
    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        arch = snapshots[0].signals.architecture_signal
        if arch.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []
        
        # ← This line is Stage 2 implementation, NOT Stage 1 documentation
        # ← Stage 1 documentation SPECIFIES this pattern
        # ← Stage 2 IMPLEMENTS this pattern in actual code
        observed_at = arch.observed_at or snapshots[0].observed_at
        
        # Rest of derivation logic...
```

**Stage 2 deliverables** (future work):
- ❌ Implementation of fallback pattern in architecture_drift.py
- ❌ Implementation in benchmark_regression.py
- ❌ Implementation in security_vuln.py
- ❌ Implementation in coverage_gap.py
- ❌ Implementation in dependency_drift.py
- ❌ Implementation in observation_coverage.py
- ❌ Tests for None observed_at scenarios
- ❌ Full test suite validation

---

## Critical Distinction: Documentation vs Implementation

### Stage 1 Document vs Stage 2 Code

**Stage 1: "Here's what you should do"**
```
(In models.py docstring)
"When using signals with optional observed_at in derivers, follow this pattern:
    observed_at = signal.observed_at or snapshots[0].observed_at
This ensures:
- More accurate timestamps when external tools provide them
- No null timestamps (snapshot-level is guaranteed non-None)
- Consistent timestamp semantics across all derivers"
```

**Stage 2: "Here's the code that does it"**
```python
# In architecture_drift.py
observed_at = arch.observed_at or snapshots[0].observed_at
```

**Key point**: Stage 1 SPECIFIES the pattern in documentation. Stage 2 IMPLEMENTS it in code.

---

## Verification: Stage 1 Is NOT Stage 2

### File Count Check
- **Stage 1 modifies**: 1 file (models.py)
- **Stage 2 would modify**: 6+ files (derivers)
- **Current state**: Only 1 file modified → ✅ This is Stage 1, not Stage 2

### Change Type Check
- **Stage 1 changes**: Docstrings only
- **Stage 2 changes**: Actual code logic
- **Current state**: Only docstrings → ✅ This is Stage 1, not Stage 2

### Deriver Code Check
```bash
$ grep -n "observed_at = " src/operations_center/insights/derivers/*.py
# Result: No output (no matches)
```
**Interpretation**: No deriver has the fallback pattern yet → ✅ Stage 2 not implemented

### Signal Field Check
```bash
$ grep "observed_at: datetime | None = None" src/operations_center/observer/models.py
# Result: 6 matches (unchanged)
```
**Interpretation**: Field definitions are unchanged → ✅ Not behavioral changes

---

## Documentation-Only Proof

### What changed in models.py

**Before Stage 1** (hypothetically):
```python
from __future__ import annotations

class CheckSignal(BaseModel):
    status: str
    test_count: int | None = None
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None
```

**After Stage 1** (current):
```python
"""Signal models for repository observation and analysis.

## Timestamp Strategy: Signal-Level vs Snapshot-Level
[41 lines of module-level docstring explaining strategy]
"""
from __future__ import annotations

class CheckSignal(BaseModel):
    """Test execution results and status.
    
    [25 lines of comprehensive docstring explaining when/why observed_at is optional]
    """
    status: str
    test_count: int | None = None
    source: str | None = None
    observed_at: datetime | None = None  # ← UNCHANGED
    summary: str | None = None
```

**What changed**: Docstrings added (pure text documentation)  
**What stayed the same**: All field definitions, all types, all logic  
**Impact**: Zero runtime behavior change

---

## Deployment Readiness

### Stage 1 Can Deploy Immediately
- ✅ Pure documentation (no code compilation required)
- ✅ No runtime behavior changes
- ✅ No test modifications
- ✅ Fully backward compatible
- ✅ No integration issues

### Stage 2 Cannot Deploy Until After Stage 1 Complete
- ⏳ Depends on Stage 1 specification
- ⏳ Uses documentation as implementation guide
- ⏳ Requires tests for new behavior
- ⏳ Needs full validation

---

## Summary Table

| Aspect | Stage 1 | Stage 2 |
|--------|---------|---------|
| **Purpose** | Document semantics | Implement pattern |
| **Type** | Docstrings | Code logic |
| **Files modified** | 1 (models.py) | 6+ (derivers) |
| **Field changes** | None | None |
| **Code logic changes** | None | Yes (fallback pattern) |
| **Test changes** | None | Yes (None observed_at tests) |
| **Runtime behavior change** | None | Yes |
| **Can deploy independently** | ✅ Yes | ❌ No (depends on Stage 1) |
| **Conflicts with Stage 2** | ❌ No | ✅ No (different files) |

---

## Conclusion

**Stage 1 (Current)**: Pure documentation, zero implementation, zero behavior changes  
**Stage 2 (Future)**: Implementation using Stage 1 specification as guide  

**They are completely separate and non-conflicting.**

Current status: ✅ Stage 1 complete and ready for immediate merge.
