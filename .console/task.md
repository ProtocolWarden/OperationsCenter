# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

Update the "Test Failure Extraction and Analysis" README section to accurately reflect
all extraction capabilities shipped since the section was first written: the
`extraction-health` CLI command, `message_quality_rate`, `gaps`, `edge_cases`,
`low_quality_messages`, alert integration, and the troubleshooting / interpretation guide.

## Current Stage

**Stage 1: Research and design README section content** ✅ COMPLETE

## Stage 1 Acceptance Criteria — ALL MET ✅

1. ✅ **Identified what extraction capabilities exist** (Stage 0 summary, confirmed via source
   reading this session)

2. ✅ **Determined section scope, structure, and audience** — see design below.

3. ✅ **Created outline with subsections** — see outline below.

---

## Stage 1 Design

### Audience

| Reader | Goal |
|--------|------|
| Operator | Run `extraction-health` to check whether the pipeline is producing usable data |
| Developer | Query `query-flaky-tests` to understand which tests fail and why |
| Engineer | Understand the data-flow and extend the extraction system |

Out of scope for README (detail already in `docs/reference/EXTRACTION_FIDELITY_METRIC.md`):
- Quality gate internals (bare exception type name list)
- JSONL storage schema with all fields
- Extension guide (adding new quality gates, promoting to signal)

### Gap Analysis: what the current README is missing

The section at `README.md:1038–1189` was written for the original extraction feature
(Stage 7, 2026-06-14) before `extraction-health` was added. The following are absent:

| Missing | Implemented in |
|---------|----------------|
| `extraction-health` command | `cli.py` |
| `success_rate` + `message_quality_rate` metrics | `query_flaky.py`, `ExtractionHealth` |
| `gaps` sample list | `query_flaky.py:141`, `cli.py:1088` |
| `edge_cases` sample list | `query_flaky.py:142`, `cli.py:1095` |
| `low_quality_messages` sample list | `query_flaky.py:144`, `cli.py:1102` |
| Alert thresholds and channel routing | `flaky_test_alerts.py`, `flaky_test_alert_config.py` |
| Troubleshooting / interpretation guide | `docs/reference/EXTRACTION_FIDELITY_METRIC.md` |

What the current section covers well and should be kept:
- Overview of what gets extracted (test names, assertion messages)
- `query-flaky-tests` CLI with `--format` and `--include-assertions` options
- Python API (`get_failing_test_names`, `get_failing_assertion_messages`, `filter_by_test_name`)
- Data flow diagram (Pytest Execution → Test Outcomes JSON → … → Query & Reporting)
- Example output for `query-flaky-tests` (table + JSON)

### Proposed Section Outline

```
### Test Failure Extraction and Analysis      (existing h3, keep)

  Overview                                    (update: add two-axis health model intro)

  #### Querying Failing Test Data             (rename from "Query Extracted Test Failure Data")
    - CLI: query-flaky-tests examples         (keep)
    - Python API                              (keep)
    - Example output: table + JSON            (keep)

  #### Monitoring Extraction Health           (NEW subsection)
    - What it measures: success_rate vs message_quality_rate (two-axis model)
    - CLI: extraction-health --format json / --format table
    - Full JSON output field reference table
    - Table output example (with gaps, edge_cases, low_quality_messages)

  #### Gaps and Edge Cases                    (NEW subsection)
    - Gaps: both test_name and assertion_message are None
    - Edge cases: truncated_message / special_chars / malformed_exception
    - Low-quality messages: empty / too_short / bare_exception_type
    - Reading sample lists in CLI output

  #### Alert Integration                      (NEW subsection)
    - Threshold table (INFO/WARNING/CRITICAL/EMERGENCY for both metrics)
    - Channel routing table
    - Programmatic check example

  #### Extraction Process                     (keep; move below health monitoring)
    - 4-step pipeline description
    - Data flow diagram

  #### Troubleshooting                        (NEW subsection)
    - message_quality_rate interpretation table (None / 0-49% / 50-79% / 80-94% / 95-100%)
    - "sudden drop without success_rate drop" diagnostic
    - Cross-link to full reference doc

  #### Inline Documentation                   (slim down to a brief note; remove docstring list)
```

### Key Content Decisions

1. **Two-axis model** belongs in the overview — operators need to know upfront that
   `success_rate` ≠ `message_quality_rate`. A healthy pipeline needs both > 95%.

2. **`extraction-health` before `query-flaky-tests`** — operators check health first;
   querying individual failures is secondary. Current section ordering is reversed.
   Reorder: health monitoring first, then querying failures.

3. **Gaps/edge_cases/low_quality_messages** get their own subsection because all three
   are sample lists, and an operator needs to understand how to read all of them together
   before looking at any one in isolation.

4. **Alert Integration** section is brief: just the two threshold tables and a one-liner
   programmatic example. Full config docs are in the reference doc.

5. **Inline Documentation** subsection should be reduced to a single sentence pointing
   to the relevant source files. Enumerating individual function docstrings in the README
   is maintenance burden that doesn't help operators.

6. **Cross-links**: add a "See also: `docs/reference/EXTRACTION_FIDELITY_METRIC.md`" callout
   at the top of the Monitoring section to direct engineers who need the full reference.

---

## Implementation Path (Stage 2)

File: `README.md`, section `### Test Failure Extraction and Analysis` (lines 1038–1189).

Approach: rewrite the section in-place. Length will grow from ~150 lines to ~280 lines to
accommodate three new subsections. Content from the current section is preserved and
reorganized; nothing is deleted that is still accurate.

Stage 2 acceptance criteria:
1. All subsections from the outline above are present and accurate
2. `extraction-health` CLI examples match actual command output shape in `cli.py`
3. JSON field reference table matches `ExtractionHealth` dataclass fields
4. Alert threshold tables match `FlakyTestAlertConfig` configured values
5. No broken cross-links
6. Test suite and linter pass (README changes don't break anything)
