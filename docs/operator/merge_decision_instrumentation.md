# Merge-Decision Instrumentation Guide

## Overview

The merge-decision instrumentation system provides structured metrics and logging for operator monitoring of the PR review automation. This document covers baseline metrics, anomaly detection triggers, and debugging procedures.

## Metrics Collection

### Decision outcomes

The instrumentation tracks three merge decision outcomes:

| Outcome | Meaning |
|---------|---------|
| **approved** | PR approved and merged by reviewer |
| **blocked** | PR closed due to unresolved concerns after max fix attempts |
| **retry** | Mixed verdicts; dispatching fix attempt on PR branch |

### Latency baseline

**Acceptable baseline:** Less than 500 milliseconds

The merge decision should complete within 500 milliseconds from:
- Start of verdict consolidation
- CI gate checks
- All metadata loading

Latencies exceeding 500 milliseconds typically indicate:
- Slow CI status API responses
- GitHub API rate-limiting
- Review pipeline backend unavailability (more than 2 consecutive timeouts)

### Latency histogram

Tracked latencies across all decisions enable:
- Min/max/average latency trending
- Percentile analysis (p50, p95, p99)
- Baseline drift detection

## Structured logging

All merge decisions are logged with structured format for dashboard integration:

```json
{
  "decision": "approved",
  "latency_ms": 245.5,
  "reason": "self_review_lgtm",
  "lanes": 1,
  "timestamp": 1717426800.5
}
```

**Key fields:**
- `decision`: Decision type (approved/blocked/retry)
- `reason`: Decision rationale (e.g., "self_review_lgtm", "fix_attempts_exhausted")
- `latency_ms`: Decision latency in milliseconds
- `lanes`: Number of parallel verdict lanes processed
- `timestamp`: Unix timestamp of decision recording

**Note:** PR number and repository key are logged separately via structured logger context (not in the structured_logs dict). Latency SLA compliance (latency less than 500 milliseconds) can be derived from the `latency_ms` field by comparison with the baseline threshold.

## Anomaly detection

### Retry rate anomaly

**Threshold: more than 20 percent of decisions are retries**

High retry rates indicate:
- Review verdicts unstable (varying across runs)
- PR diffs changing frequently during review
- Review backend flakiness (inconsistent verdicts)

**Action:** Investigate review backend stability and verdict consolidation logic.

### CI green delay

**Threshold: CI wait cycles exceeding 5 consecutive cycles**

Indicates:
- CI pipeline slow or flaky (not recovering to green)
- Required check status not updating
- GitHub Actions / CI provider issues

**Action:** Check CI logs; validate required checks; investigate backend issues if exceeding 10 cycles.

## Debugging decision failures

### Decision latency exceeds 500 milliseconds

**Steps:**
1. Check latency histogram — is this isolated or trend?
2. Review structured logs for this PR number
3. Check GitHub API rate limit status
4. Inspect review backend logs for slow pipeline execution
5. Look for CI status API delays in logs

**Quick wins:**
- Increase GitHub token rate limit quota
- Add retry logic to CI status checks
- Parallelize verdict consolidation if possible

### Unusually high retry rate

**Steps:**
1. Sample 5-10 recent retry decisions
2. Check if same repo/lane combination retrying
3. Inspect the PR diffs for patterns (size, file types)
4. Review verdict consolidation logic for instability

**Common causes:**
- Large diffs causing review backend timeout
- Flaky verdict pipeline (transient errors)
- Review backend version mismatch

## Metrics export

Metrics are aggregated in memory and accessible via the `get_instrumenter()` function and `MergeDecisionInstrumenter.get_metrics_summary()` method (defined in `src/operations_center/reviewer/instrumentation.py`):

```python
from operations_center.reviewer.instrumentation import get_instrumenter

# Get current metrics snapshot (get_instrumenter defined at src/operations_center/reviewer/instrumentation.py:284)
summary = get_instrumenter().get_metrics_summary()

# Response structure:
{
  "total_decisions": 1234,
  "outcomes": {
    "approved": 945,
    "blocked": 67,
    "retry": 198
  },
  "avg_latency_ms": 245.3,
  "max_latency_ms": 1823.5,
  "min_latency_ms": 45.2,
  "decision_log": [...]
}
```

Metrics can be exported as JSON via `MergeDecisionInstrumenter.export_metrics_json()` method (defined in `src/operations_center/reviewer/instrumentation.py:274`) for external dashboards.

## Health status indicators

| Metric | Healthy | Degraded | Critical |
|--------|---------|----------|----------|
| Avg latency | less than 300 milliseconds | 300-500 milliseconds | exceeding 500 milliseconds |
| Retry rate | less than 10 percent | 10-20 percent | exceeding 20 percent |
| Recent decisions (1h) | exceeding 10 | 5-10 | less than 5 |

## Integration with dashboards

Structured logs integrate with Grafana/observability platforms:

1. **Metric query:** `merge_decision_outcome{repo_key="repo"}`
2. **Alert rule:** Latency exceeding 1000 milliseconds for 5 minutes
3. **Dashboard panels:**
   - Decision outcome pie chart
   - Latency trend (line graph with 500 milliseconds baseline)
   - Retry rate trend

## Related Documentation

- [PR Review Watcher Architecture](../architecture/pr_review_watcher.md)
- [Verdict Consolidation State Machine](../architecture/verdict_consolidation.md)
- [Review Backend Troubleshooting](../troubleshooting/review_backend.md)
