# Merge-Decision Instrumentation Guide

## Overview

The merge-decision instrumentation system provides structured metrics and logging for operator monitoring of the PR review automation. This document covers baseline metrics, anomaly detection triggers, and debugging procedures.

## Metrics Collection

### Decision Outcomes

The instrumentation tracks four merge decision outcomes:

| Outcome | Meaning | Typical Baseline |
|---------|---------|-----------------|
| **merge** | PR approved and merged by reviewer | < 500ms latency |
| **blocked** | PR closed due to unresolved concerns after max fix attempts | < 500ms latency |
| **retry** | Mixed verdicts; dispatching fix attempt on PR branch | < 500ms latency |
| **escalate** | Decision escalated to human (cannot auto-resolve) | < 500ms latency |

### Latency Baseline

**Acceptable baseline: < 500ms**

The merge decision should complete within 500ms from:
- Start of verdict consolidation
- CI gate checks
- All metadata loading

Latencies exceeding 500ms typically indicate:
- Slow CI status API responses
- GitHub API rate-limiting
- Review pipeline backend unavailability (> 2 consecutive timeouts)

### Latency Histogram

Tracked latencies across all decisions enable:
- Min/max/average latency trending
- Percentile analysis (p50, p95, p99)
- Baseline drift detection

## Structured Logging

All merge decisions are logged with structured format for dashboard integration:

```json
{
  "decision": "merge",
  "latency_ms": 245.5,
  "reason": "self_review_lgtm",
  "lanes": 1,
  "timestamp": 1717426800.5
}
```

**Key fields:**
- `decision`: Decision type (merge/blocked/retry/escalate)
- `reason`: Decision rationale (e.g., "self_review_lgtm", "fix_attempts_exhausted")
- `latency_ms`: Decision latency in milliseconds
- `lanes`: Number of parallel verdict lanes processed
- `timestamp`: Unix timestamp of decision recording

**Note:** PR number and repository key are logged separately via structured logger context (not in the structured_logs dict). Baseline compliance (latency < 500ms) can be derived from the `latency_ms` field by comparison with the baseline threshold.

## Anomaly Detection

### Retry Rate Anomaly

**Threshold: > 20% of decisions are retries**

High retry rates indicate:
- Review verdicts unstable (varying across runs)
- PR diffs changing frequently during review
- Review backend flakiness (inconsistent verdicts)

**Action:** Investigate review backend stability and verdict consolidation logic.

### CI-Green Delay

**Threshold: CI wait cycles > 5 consecutive cycles**

Indicates:
- CI pipeline slow or flaky (not recovering to green)
- Required check status not updating
- GitHub Actions / CI provider issues

**Action:** Check CI logs; validate required checks; consider temporary escalation if >10 cycles.

### Escalation Rate

**Monitor threshold: > 5% of decisions escalate**

Escalations indicate:
- Backend unavailability (review pipeline crashes)
- Unhandled edge cases (missing PR metadata, malformed diffs)
- Rate-limiting or transient errors

**Action:** Check reviewer backend logs; validate API rate limits; escalate to SRE if persistent.

## Debugging Decision Failures

### Decision took > 500ms

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

### Escalation spike

**Steps:**
1. Check recent commits to reviewer code
2. Check review backend deployment status
3. Look for rate-limit errors in backend logs
4. Verify GitHub API connectivity

## Metrics Export

Metrics are aggregated in memory and accessible via the `MergeDecisionInstrumenter.get_metrics_summary()` method:

```python
from operations_center.reviewer.instrumentation import get_instrumenter

# Get current metrics snapshot
summary = get_instrumenter().get_metrics_summary()

# Response structure:
{
  "total_decisions": 1234,
  "outcomes": {
    "merge": 945,
    "blocked": 67,
    "retry": 198,
    "escalate": 24
  },
  "avg_latency_ms": 245.3,
  "max_latency_ms": 1823.5,
  "min_latency_ms": 45.2,
  "decision_log": [...]
}
```

Metrics can be exported as JSON via `export_metrics_json()` for external dashboards.

## Health Status Indicators

| Metric | Healthy | Degraded | Critical |
|--------|---------|----------|----------|
| Avg latency | < 300ms | 300-500ms | > 500ms |
| Retry rate | < 10% | 10-20% | > 20% |
| Escalation rate | < 3% | 3-5% | > 5% |
| Recent decisions (1h) | > 10 | 5-10 | < 5 |

## Integration with Dashboards

Structured logs integrate with Grafana/observability platforms:

1. **Metric query:** `merge_decision_outcome{repo_key="repo"}`
2. **Alert rule:** Latency > 1000ms for 5min
3. **Dashboard panels:**
   - Decision outcome pie chart
   - Latency trend (line graph with 500ms baseline)
   - Retry rate trend
   - Escalation spike detector

## Related Documentation

- [PR Review Watcher Architecture](../architecture/pr_review_watcher.md)
- [Verdict Consolidation State Machine](../architecture/verdict_consolidation.md)
- [Review Backend Troubleshooting](../troubleshooting/review_backend.md)
