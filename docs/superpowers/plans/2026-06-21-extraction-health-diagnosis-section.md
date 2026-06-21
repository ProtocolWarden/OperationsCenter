# Extraction Health Diagnosis Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revise `docs/operator/diagnostics.md` "Extraction Health Diagnosis" section so it meets all acceptance criteria: 5–8 distinct commands with example outputs, a dedicated diagnostic workflow subsection, and a unified metrics-and-thresholds reference table.

**Architecture:** Pure documentation edit to a single file. No code changes. Three insertions/replacements to the existing section at lines 563–740.

**Tech Stack:** Markdown, existing CLI commands already verified in research stage.

## Global Constraints

- File to modify: `docs/operator/diagnostics.md`
- Section to revise: lines 563–740 (`## Extraction Health Diagnosis`)
- Do NOT alter any other section
- All command invocations must match the CLI as documented by Stage 0 research
- Example outputs must be consistent (same numbers across all commands showing the same hypothetical state)
- Follow the H3 subsection pattern used by other diagnostics sections

---

### Task 1: Add Extraction Metrics Reference Table

**Files:**
- Modify: `docs/operator/diagnostics.md:567-580` (after the formula block, before `### CLI Commands`)

**Why:** The acceptance criteria requires a reference table for extraction metrics and thresholds. Currently the formula and the alert table are separate; a single reference table that combines metric semantics + alert thresholds per metric is the missing artifact.

**Interfaces:**
- Produces: `### Extraction Metrics Reference` subsection with a table containing columns: Metric, What it counts, Healthy, Threshold (WARNING <80%), Threshold (CRITICAL <50%), Threshold (EMERGENCY <10%)

- [ ] **Step 1: Replace the existing `### Metric: extraction_success_rate` block**

Current text (lines 567–580):
```markdown
### Metric: extraction_success_rate

**Formula** (`src/operations_center/observer/query_flaky.py:387`):

```
success_rate = (complete_extraction + partial_extraction) / total_flaky_tests × 100
```

- **complete_extraction** — test has both `test_name` and `assertion_message`
- **partial_extraction** — test has one of the two fields
- **no_extraction** — test has neither field (a blind spot)
- `success_rate` treats partial as a success; only `no_extraction` counts against it

The rate is recorded in `FlakyTestSignal.extraction_success_rate` (`models.py:460`) on every observer snapshot.
```

Replace with:

```markdown
### Extraction Metrics Reference

**Formula** (`src/operations_center/observer/query_flaky.py:387`):

```
extraction_success_rate = (complete_extraction + partial_extraction) / total_flaky_tests × 100
```

`partial_extraction` counts as a success; only `no_extraction` (neither field present) counts against the rate. The rate is recorded in `FlakyTestSignal.extraction_success_rate` (`models.py:460`) on every observer snapshot.

| Metric | What it counts | Healthy | WARNING | CRITICAL | EMERGENCY |
|--------|----------------|---------|---------|----------|-----------|
| `extraction_success_rate` | % of tests with name and/or message | ≥ 80% | < 80% | < 50% | < 10% |
| `complete_extraction` | Tests with both `test_name` and `assertion_message` | high | — | — | — |
| `partial_extraction` | Tests with one field only | low | — | — | — |
| `no_extraction` | Tests with neither field (blind spots) | 0 | any | many | majority |
| `truncated_messages` | Assertion messages cut at 200 chars | 0 | > 0 | — | — |
| `special_chars` | Messages with non-ASCII or control chars | 0 | > 0 | — | — |
| `malformed_exceptions` | Non-standard exception chains yielding no message | 0 | > 0 | — | — |

Alert thresholds are defined in `FlakyTestAlertConfig` (`flaky_test_alert_config.py:122–128`). The `extraction_success_rate` column maps directly to the `EXTRACTION_SUCCESS_RATE_LOW` alert severity.
```

- [ ] **Step 2: Verify the replacement reads correctly**

Read the modified lines and confirm the table renders correctly in GitHub-flavored Markdown (pipe-delimited columns, correct alignment).

- [ ] **Step 3: Commit**

```bash
git add docs/operator/diagnostics.md
git commit -m "docs(diagnostics): add extraction metrics reference table"
```

---

### Task 2: Expand CLI Commands to 5–8 Clearly Distinct Items

**Files:**
- Modify: `docs/operator/diagnostics.md` — `### CLI Commands` subsection

**Why:** The current subsection groups many flag variants under two headings. The acceptance criteria requires 5–8 commands each with example output. We need to split into individually numbered items.

**Interfaces:**
- Produces: `### CLI Commands` subsection listing exactly 8 commands, each with a sentence description and a fenced example output block

The 8 commands are:

| # | Command | Purpose |
|---|---------|---------|
| 1 | `extraction-health` | Aggregate health — JSON (default, machine-readable) |
| 2 | `extraction-health --format table` | Aggregate health — table (quick human visual) |
| 3 | `extraction-health --trend-days 14` | Aggregate health + 14-day trend section |
| 4 | `query-flaky-tests` | Per-test records (table, no assertion messages) |
| 5 | `query-flaky-tests --include-assertions --hours 6` | Per-test records with assertion messages |
| 6 | `tail -5 extraction_health_history.jsonl \| python3 -m json.tool` | Raw JSONL history (last 5 entries) |
| 7 | `grep "EXTRACTION_SUCCESS_RATE_LOW\|extraction_success_rate" logs/local/watch-all/*.log \| tail -20` | Alert events in watcher log |
| 8 | Observer snapshot rate lookup (python3 one-liner) | Current rate embedded in latest snapshot |

- [ ] **Step 1: Replace the `### CLI Commands` block**

Replace existing `### CLI Commands` subsection with individual numbered subsections:

```markdown
### CLI Commands

Eight commands cover the full diagnostic surface from aggregate trend down to individual alert events.

**1. Aggregate health (JSON)**

```bash
operations-center observer extraction-health
```

Machine-readable output; the format consumed by the watchdog collector. Add `--hours 6` to restrict the look-back window or `--storage-root /path` to override the snapshot root.

Example output:

```json
{
  "success_rate": 91.3,
  "complete_extraction": 63,
  "partial_extraction": 21,
  "no_extraction": 8,
  "edge_case_summary": {
    "truncated_messages": 4,
    "special_chars": 2,
    "malformed_exceptions": 1
  }
}
```

**2. Aggregate health (table)**

```bash
operations-center observer extraction-health --format table
```

Quick visual inspection. Same data as command 1 in columnar form.

Example output:

```
Extraction Health
  success_rate          91.3 %
  complete_extraction   63
  partial_extraction    21
  no_extraction         8
  edge cases
    truncated_messages    4
    special_chars         2
    malformed_exceptions  1
```

**3. Aggregate health with trend**

```bash
operations-center observer extraction-health --trend-days 14
```

Appends a `history` section to the JSON output with: daily trend, weekly trend, regression slope, anomalies list, and the five most-recent snapshot readings. Use this when a sudden drop is suspected.

Example `history` section (excerpt):

```json
{
  "success_rate": 91.3,
  "history": {
    "daily_trend": -1.2,
    "weekly_trend": -0.4,
    "regression_slope": -0.09,
    "anomalies": [
      {"recorded_at": "2026-06-18T08:00:00Z", "success_rate": 64.0}
    ],
    "recent": [
      {"recorded_at": "2026-06-21T14:03:12Z", "success_rate": 91.3},
      {"recorded_at": "2026-06-20T14:01:55Z", "success_rate": 89.7}
    ]
  }
}
```

**4. Per-test records (table)**

```bash
operations-center observer query-flaky-tests
```

Lists each failing test with its last-seen timestamp, occurrence count, and whether `test_name` and `assertion_message` were extracted. Add `--hours N` to restrict the look-back or `--limit N` to cap the row count.

Example output:

```
test_name                                    last_seen             count  name  msg
tests/test_auth.py::test_token_refresh       2026-06-21 14:03      5      yes   no
tests/test_cache.py::test_invalidation       2026-06-21 11:42      3      yes   yes
tests/test_webhook.py::test_timeout          2026-06-21 09:17      2      yes   yes
tests/test_payment.py::test_retry            2026-06-20 22:11      1      no    no
```

**5. Per-test records with assertion messages**

```bash
operations-center observer query-flaky-tests --include-assertions --hours 6
```

Adds the `assertion_message` column. Use this to identify which tests have a populated message vs. `(no message extracted)`.

Example output:

```
test_name                assertion_message
test_token_refresh       (no message extracted)
test_cache_invalidation  Expected status 200, got 503 after 3 retries
test_webhook_timeout     assert elapsed <= 30, got 47.2
```

**6. Raw JSONL history**

```bash
tail -5 tools/report/operations_center/observer/extraction_history/extraction_health_history.jsonl \
  | python3 -m json.tool
```

Inspects the last five entries in the time-series file directly. Useful when the CLI is unavailable or you need the raw `recorded_at` timestamps.

Example output (one entry):

```json
{
  "recorded_at": "2026-06-21T14:03:12Z",
  "success_rate": 91.3,
  "complete_extraction": 63,
  "partial_extraction": 21,
  "no_extraction": 8
}
```

**7. Alert events in watcher log**

```bash
grep "EXTRACTION_SUCCESS_RATE_LOW\|extraction_success_rate" logs/local/watch-all/*.log | tail -20
```

Shows when and at what severity the `EXTRACTION_SUCCESS_RATE_LOW` alert fired. Look for `"severity": "WARNING"`, `"severity": "CRITICAL"`, or `"severity": "EMERGENCY"` entries.

Example output:

```
watch-all/goal.log:{"event": "EXTRACTION_SUCCESS_RATE_LOW", "severity": "WARNING", "success_rate": 74.1, "ts": "2026-06-18T08:03:22Z"}
```

**8. Extraction rate in observer snapshot**

```bash
python3 -c "
import json, glob
f = sorted(glob.glob('tools/report/operations_center/observer/*/repo_state_snapshot.json'))[-1]
d = json.load(open(f))
print('rate:', d.get('extraction_success_rate', 'not present'))
"
```

Reads `FlakyTestSignal.extraction_success_rate` from the latest observer snapshot. Useful to confirm what rate the proposer/observer pipeline saw during the last `observe-repo` run, independent of the extraction-health collector.

Example output:

```
rate: 91.3
```
```

- [ ] **Step 2: Verify command count**

Manually count the numbered commands in the revised subsection — there must be exactly 8 before committing.

- [ ] **Step 3: Commit**

```bash
git add docs/operator/diagnostics.md
git commit -m "docs(diagnostics): expand CLI commands to 8 distinct items with examples"
```

---

### Task 3: Add Dedicated Diagnostic Workflow Subsection

**Files:**
- Modify: `docs/operator/diagnostics.md` — add `### Diagnostic Workflow` before `### Common Failure Modes`

**Why:** The acceptance criteria requires a planned diagnostic workflow. The current section only has a brief numbered list embedded in the failure modes table as a footnote. It needs to be a first-class subsection.

**Interfaces:**
- Produces: `### Diagnostic Workflow` subsection with a step-by-step troubleshooting flow keyed to the commands above

- [ ] **Step 1: Add the Diagnostic Workflow subsection**

Insert before `### Common Failure Modes`:

```markdown
### Diagnostic Workflow

Use this sequence when `extraction_success_rate` is below 80% or an `EXTRACTION_SUCCESS_RATE_LOW` alert fires.

**Step 1 — Confirm current rate and severity**

```bash
operations-center observer extraction-health --format table
```

Check `success_rate`. Cross-reference against the metrics reference table to classify severity (WARNING / CRITICAL / EMERGENCY).

**Step 2 — Check recent trend**

```bash
operations-center observer extraction-health --trend-days 14
```

A negative `regression_slope` and anomaly entries indicate a regression event rather than a stable low baseline. Note the `recorded_at` timestamp of the earliest anomaly.

**Step 3 — Identify which tests are failing extraction**

```bash
operations-center observer query-flaky-tests --hours 6
```

Look for rows where the `name` or `msg` column shows `no`. Tests with `name=no` are completely invisible — they contribute to `no_extraction`.

**Step 4 — Inspect assertion messages for extractable tests**

```bash
operations-center observer query-flaky-tests --include-assertions --hours 6
```

`(no message extracted)` means the exception chain is not being handled. Note the test names; these are candidates for `assertion_extractor.py` extension.

**Step 5 — Check edge-case counters**

In the `edge_case_summary` block from command 1:
- `truncated_messages > 0` — messages are being cut at 200 chars; context lost downstream
- `special_chars > 0` — non-ASCII or control characters in assertion text; may affect log parsing
- `malformed_exceptions > 0` — non-standard exception chains; `_extract_from_exception_chain` returned nothing useful

**Step 6 — Correlate with alert history**

```bash
grep "EXTRACTION_SUCCESS_RATE_LOW" logs/local/watch-all/*.log | tail -20
```

Confirm alert severity matches what you observed in step 1. If no alert is logged but the rate is below 80%, check that `FlakyTestAlertManager` is running (look for `"event": "extraction_health_check"` near the timestamp).

**Step 7 — Verify observer snapshot rate**

```bash
python3 -c "
import json, glob
f = sorted(glob.glob('tools/report/operations_center/observer/*/repo_state_snapshot.json'))[-1]
d = json.load(open(f))
print('rate:', d.get('extraction_success_rate', 'not present'))
"
```

If the snapshot rate differs significantly from the `extraction-health` output, the snapshot is stale — re-run `observe-repo` to refresh.

**Step 8 — Remediate**

| Finding | Action |
|---------|--------|
| `no_extraction == total` | pytest extraction plugin not active; check `conftest.py` or plugin install |
| Specific exception types not extracted | Extend `assertion_extractor.py` with a handler |
| `truncated_messages` spiking | Assertions are verbose; consider truncating at the source or raising the 200-char limit |
| Sudden drop correlated to a date | Check git log for pytest version bump or new test framework introduced |
| Rate stagnant at 0 with no flaky tests | `status == "unavailable"`; not an extraction failure — observer window has no flaky data |
```

- [ ] **Step 2: Remove the now-redundant "Diagnostic sequence" block from Common Failure Modes**

The existing "Diagnostic sequence when rate is below 80%:" numbered list at the end of `### Common Failure Modes` should be deleted since it is now superseded by the dedicated workflow section.

- [ ] **Step 3: Verify the section reads logically end-to-end**

Skim from `## Extraction Health Diagnosis` to the next `##` heading. Confirm order:
1. `### Extraction Metrics Reference` (table)
2. `### CLI Commands` (8 items)
3. `### Diagnostic Workflow` (8 steps)
4. `### Alert System`
5. `### Data Storage`
6. `### Common Failure Modes` (table only, no embedded diagnostic list)

- [ ] **Step 4: Run test suite to verify nothing broken**

```bash
cd /tmp/oc-goal-jkppppx6/workspace && python -m pytest --tb=short -q 2>&1 | tail -5
```

Expected: all tests pass, 0 failures.

- [ ] **Step 5: Final commit**

```bash
git add docs/operator/diagnostics.md
git commit -m "docs(diagnostics): add diagnostic workflow subsection, clean up Common Failure Modes"
```
