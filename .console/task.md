# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Overall Plan

Expose sample gaps and edge_cases lists in CLI for operator inspection.

## Current Stage

**Stage 1: Design CLI output format for gaps and edge_cases exposure** ✅ COMPLETE

## Stage 1 Acceptance Criteria — ALL MET ✅

1. ✅ **Define output format for sample gaps list**
   - Format: `list[str]` — flat array of pytest node ID strings
   - JSON key: `"gaps"` inside the existing ExtractionHealth JSON object
   - Example: `"gaps": ["test_module::test_missing_both", "tests/unit/foo.py::TestBar::test_baz"]`
   - Cap: up to 10 samples; full count already carried by `no_extraction`

2. ✅ **Define output format for sample edge_cases list**
   - Format: `list[dict]` — each entry has `test_id` (string) and `issue` (string)
   - JSON key: `"edge_cases"` inside the existing ExtractionHealth JSON object
   - Example: `"edge_cases": [{"test_id": "test_module::test_foo", "issue": "truncated_message"}]`
   - Cap: up to 10 entries; a test with 2 issues produces 2 entries
   - Full counts per issue type already carried by `edge_case_summary`

3. ✅ **Determine what fields to include per gap**
   - Single field: `test_id` (the pytest node ID string, e.g. `"test_module::test_missing_both"`)
   - No per-item `reason` field needed — all gaps share the same reason (both `test_name`
     and `assertion_message` are None); reason is implicit from being in the `gaps` array

4. ✅ **Determine what fields to include per edge_case**
   - `test_id`: string — full pytest node ID
   - `issue`: string — one of `"truncated_message"`, `"special_chars"`, `"malformed_exception"`
     (singular form; maps to the corresponding `edge_case_summary` counter)

5. ✅ **Plan integration with existing extraction-health command**
   - **JSON mode** (`--format json`): zero CLI changes needed — `asdict(health)` auto-includes
     new dataclass fields. New output adds `"gaps"` and `"edge_cases"` keys alongside
     `"success_rate"`, `"no_extraction"`, etc.
   - **Table mode** (`--format table`): one additional branch in `cli.py:1049-1055` to print
     gap and edge_case sample lines below the summary line when either list is non-empty:
     ```
     extraction success_rate=80.0%  complete=4  partial=0  none=1
     gaps (1 test, showing 1):
       test_module::test_missing_both
     edge_cases (2 issues, showing 2):
       test_module::test_truncated  [truncated_message]
       test_module::test_special    [special_chars]
     ```
   - **Baseline**: existing tests (26/26) confirmed passing before any code change

## Full JSON Output Shape (after Stage 2 implementation)

```json
{
  "success_rate": 80.0,
  "complete_extraction": 4,
  "partial_extraction": 0,
  "no_extraction": 1,
  "edge_case_summary": {
    "truncated_messages": 2,
    "special_chars": 1,
    "malformed_exceptions": 0
  },
  "gaps": [
    "test_module::test_missing_both"
  ],
  "edge_cases": [
    {"test_id": "test_module::test_complete_extraction", "issue": "truncated_message"},
    {"test_id": "test_module::test_complete_extraction", "issue": "special_chars"}
  ],
  "history": { ... }
}
```

## Implementation Path (Stage 2)

1. **`query_flaky.py:98-117`** — `ExtractionHealth` dataclass: add two fields:
   ```python
   gaps: list[str] = dataclass_field(default_factory=list)
   edge_cases: list[dict] = dataclass_field(default_factory=list)
   ```

2. **`query_flaky.py:358-395`** — `get_extraction_health()` loop: collect samples while
   iterating (first 10 of each); append to local lists before `return ExtractionHealth(...)`.

3. **`cli.py:1049-1055`** — table branch: after the summary line, print gap and edge_case
   sample sections when either list is non-empty.

4. **Tests**:
   - `tests/unit/observer/test_extraction_health_queries.py` — add `TestExtractionHealthGaps`
     and `TestExtractionHealthEdgeCases` classes + update `TestExtractionHealthDataclass`
   - `tests/unit/observer/test_cli_extraction_health.py` — add JSON-shape assertions for
     `gaps`/`edge_cases` keys and table-format section tests
