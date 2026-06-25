<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Copyright (C) 2026 ProtocolWarden -->

# Extraction Fidelity Metric — Reference

**Implemented**: 2026-06-25  
**Spec**: [`docs/specs/STAGE1_EXTRACTION_FIDELITY_METRIC.md`](../specs/STAGE1_EXTRACTION_FIDELITY_METRIC.md)

---

## Overview

The extraction-health system tracks two complementary axes:

| Metric | Question answered | Field |
|---|---|---|
| `success_rate` | Does extracted data *exist* for this test? | `ExtractionHealth.success_rate` |
| `message_quality_rate` | Is the extracted assertion message *useful for diagnosis*? | `ExtractionHealth.message_quality_rate` |

`success_rate` measures **presence** — whether `test_name` and/or `assertion_message` are non-`None`.
`message_quality_rate` measures **quality** — whether each `assertion_message` that does exist contains
enough content for an operator to understand the failure.

A healthy extraction pipeline produces both metrics above 95%.  A pipeline with low `message_quality_rate`
but high `success_rate` is emitting placeholder messages (e.g., bare `"TimeoutError"` strings) that look
complete but carry no diagnostic value.

---

## CLI Usage

### extraction-health command

```
operations-center observer extraction-health [OPTIONS]

Options:
  --hours INT          Look back N hours (default: 24)
  --format TEXT        Output format: json|table (default: json)
  --trend-days INT     Days of history to summarize in the trend section (default: 7)
  --storage-root PATH  Override snapshot storage root
  --quiet / -q         Minimal output
```

### JSON output

```
operations-center observer extraction-health --format json --hours 24
```

Full output shape (values are illustrative):

```json
{
  "success_rate": 87.5,
  "complete_extraction": 14,
  "partial_extraction": 0,
  "no_extraction": 2,
  "edge_case_summary": {
    "truncated_messages": 1,
    "special_chars": 0,
    "malformed_exceptions": 0
  },
  "gaps": [
    "tests/unit/auth/test_token_refresh.py::test_expired_token",
    "tests/unit/db/test_pool.py::TestPool::test_timeout"
  ],
  "edge_cases": [
    {
      "test_id": "tests/unit/queue/test_drain.py::test_drain_under_load",
      "issue": "truncated_message"
    }
  ],
  "message_quality_rate": 92.3,
  "low_quality_messages": [
    {
      "test_id": "tests/unit/net/test_connector.py::test_connect_timeout",
      "reason": "bare_exception_type"
    }
  ],
  "history": {
    "window_days": 7,
    "trend": { "direction": "stable", "delta": 0.5 },
    "slope": 0.1,
    "anomalies": [],
    "observations": 12,
    "recent": [...]
  }
}
```

**Key fields:**

| Field | Type | Meaning |
|---|---|---|
| `success_rate` | `float` | `(complete + partial) / total × 100` |
| `message_quality_rate` | `float \| null` | `informative / with_assertion × 100`; `null` when no tests have an assertion message |
| `gaps` | `list[str]` | Up to 10 pytest node IDs where both fields are missing |
| `edge_cases` | `list[{test_id, issue}]` | Up to 10 per-test quality issues (`truncated_message`, `special_chars`, `malformed_exception`) |
| `low_quality_messages` | `list[{test_id, reason}]` | Up to 10 messages below the quality threshold |

### Table output

```
operations-center observer extraction-health --format table
```

Example output when all metrics are present:

```
extraction success_rate=87.5%  complete=14  partial=0  none=2
message_quality_rate=92.3%
gaps (2 tests, showing 2):
  tests/unit/auth/test_token_refresh.py::test_expired_token
  tests/unit/db/test_pool.py::TestPool::test_timeout
edge_cases (1 issues, showing 1):
  tests/unit/queue/test_drain.py::test_drain_under_load  [truncated_message]
low_quality_messages (showing 1):
  tests/unit/net/test_connector.py::test_connect_timeout  [bare_exception_type]
```

`message_quality_rate` is omitted from table output when `None` (no assertion messages in the
snapshot).  `gaps`, `edge_cases`, and `low_quality_messages` sections are omitted when empty.

---

## Quality Gates

`message_quality_rate` is computed over all tests that have a non-`None` `assertion_message`.
Each message is first cleaned by `clean_assertion_message()` (whitespace collapse, `assert ` prefix
removal, 200-char cap) and then evaluated against the following gates in order:

| Gate | Condition | `reason` label |
|---|---|---|
| Empty | Cleaned message is `""` | `"empty"` |
| Bare exception type | Cleaned message is in `_BARE_EXCEPTION_TYPE_NAMES` | `"bare_exception_type"` |
| Too short | `len(cleaned) < 10` | `"too_short"` |

A message that passes all three gates is **informative** and counted toward `informative_count`.
One that fails any gate is **low-quality** and (if the sample cap has not been reached) added to
`low_quality_messages`.

### Bare exception type names

The `_BARE_EXCEPTION_TYPE_NAMES` constant (defined in `query_flaky.py`) currently contains:

```
TimeoutError, ConnectionError, OSError
```

These are produced by `parse_non_assertion_exception()` when the exception carries no `args`.
The set is intentionally small: only names that the extractor emits as bare placeholders are
included.  Names like `"ValueError"` are excluded because they frequently appear embedded in
real, informative assertion messages.

### Minimum length threshold

`_MESSAGE_QUALITY_MIN_LENGTH = 10`.  Messages shorter than 10 characters after cleaning are
unlikely to contain a complete assertion expression.  Examples below the threshold:
`"False"`, `"None"`, `"0 != 1"`.  Examples above: `"not found"`, `"assert x > 0"`.

---

## Alert Integration

Both extraction metrics fire alerts when they fall below configured thresholds.  Alerts
are delivered via `FlakyTestAlertManager` and dispatched through `AlertChannelFactory`.

### Thresholds (inverted semantics — lower is worse)

| Metric | INFO | WARNING | CRITICAL | EMERGENCY |
|---|---|---|---|---|
| `extraction_success_rate` | 95 % | 80 % | 50 % | 10 % |
| `message_quality_rate` | 95 % | 80 % | 50 % | 10 % |

### Channel routing

| Alert type | INFO | WARNING | CRITICAL | EMERGENCY |
|---|---|---|---|---|
| `EXTRACTION_SUCCESS_RATE_LOW` | operator_log | operator_log, slack | operator_log, slack, email | operator_log, slack, email, pagerduty |
| `MESSAGE_QUALITY_RATE_LOW` | operator_log | operator_log, slack | operator_log, slack, email | operator_log, slack, email, pagerduty |

### Programmatic alert check

```python
from operations_center.observer.flaky_test_alerts import FlakyTestAlertManager
from operations_center.observer.flaky_test_alert_config import FlakyTestAlertConfig

config = FlakyTestAlertConfig()
alerts = FlakyTestAlertManager.check_message_quality_rate(rate=72.5, config=config)
for alert in alerts:
    print(f"[{alert.severity.value.upper()}] {alert.description}")
    # [WARNING] Message quality rate 72.5% is below warning threshold of 80.0%
```

`check_message_quality_rate()` returns an empty list when `rate` is `None` (no assertion messages),
so callers do not need to guard separately.

---

## Storage and Time-Series

Every run of `extraction-health` appends a snapshot to the extraction-history JSONL:

**Storage path:** `<storage-root>/extraction_history/extraction_health_history.jsonl`

**Schema per record** (newline-delimited JSON):

```json
{
  "observed_at": "2026-06-25T14:32:00+00:00",
  "success_rate": 87.5,
  "complete_extraction": 14,
  "partial_extraction": 0,
  "no_extraction": 2,
  "total_flaky_tests": 16,
  "extracted_count": 14,
  "edge_case_summary": {
    "truncated_messages": 1,
    "special_chars": 0,
    "malformed_exceptions": 0
  },
  "message_quality_rate": 92.3,
  "snapshot_id": null,
  "collection_run_id": null
}
```

**Backwards compatibility:** records written before `message_quality_rate` was added load with
`message_quality_rate=None` — no migration required.

**Accessing history programmatically:**

```python
from pathlib import Path
from operations_center.observer.extraction_health_history import (
    ExtractionHistoryStorage,
    ExtractionHistoryQuery,
)

storage = ExtractionHistoryStorage.create_local(
    Path("tools/report/operations_center/observer/extraction_history")
)
query = ExtractionHistoryQuery(storage)

# 7-day weekly aggregation
trend = query.get_success_rate_trend(days=7, granularity="weekly")

# Five most-recent snapshots
recent = query.get_recent_snapshots(count=5)
for snap in recent:
    print(f"{snap.observed_at.date()}  success={snap.success_rate:.1f}%  quality={snap.message_quality_rate}")
```

---

## Integration Points

### Where `message_quality_rate` is computed

`FlakyTestQueryMixin.get_extraction_health()` in `query_flaky.py:380–460`.

The method iterates `FlakyTest` records from the most recent snapshot (or a time-range of
snapshots), applies quality gates to each `assertion_message`, and returns an `ExtractionHealth`
dataclass.  No additional I/O or data-structure change is required to extend the quality check.

### Extending the quality gates

To add a new quality gate (e.g., "message is a stack trace header"):

1. Add the classification logic in the per-test loop at `query_flaky.py:428–440`.
2. Assign a new `quality_reason` string (e.g., `"stack_trace_header"`).
3. If you want the reason to appear in sample output, the `low_quality_messages` list
   already collects it automatically — no CLI changes needed.

### Extending `_BARE_EXCEPTION_TYPE_NAMES`

Edit the `frozenset` constant at `query_flaky.py:30–33`.  Add only names that the extraction
pipeline emits as bare placeholders (i.e., type-name-only output from
`parse_non_assertion_exception()` when `exc.args` is empty).

### Adding `message_quality_rate` to the signal snapshot

`message_quality_rate` is currently query-only — it is not stored in `FlakyTestSignal`.
When alert thresholds are stable enough to warrant trending, promote it by:

1. Adding `message_quality_rate: float | None = None` to `FlakyTestSignal` in `models.py`.
2. Populating it in the watchdog collection path that creates `FlakyTestSignal`.
3. Adding an `extraction_health_quality_rate` entry to the time-series collector.

---

## Interpretation Guide

| `message_quality_rate` | Likely cause | Recommended action |
|---|---|---|
| `None` | No flaky tests with assertion messages | No action; metric becomes meaningful when tests start failing |
| 0–49 % | Extraction pipeline producing placeholder output for most failures | Check `low_quality_messages` sample; inspect `parse_non_assertion_exception()` paths |
| 50–79 % | Mixed quality; worth investigating | Review `low_quality_messages`; verify exception chains are traversed correctly |
| 80–94 % | Healthy; a minority of edge cases expected | No action unless trending downward |
| 95–100 % | Excellent | No action |

A **sudden drop** in `message_quality_rate` without a corresponding drop in `success_rate`
usually means the extraction code is running but falling back to placeholder output — check
for changes to `parse_non_assertion_exception()` or `clean_assertion_message()`.
