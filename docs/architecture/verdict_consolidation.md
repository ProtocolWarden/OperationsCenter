# Verdict Consolidation State Machine

## Overview

The Verdict Consolidation module implements the logic for consolidating verdicts from multiple review lanes and determining merge decisions. It evaluates PR review results and decides whether to merge, block, retry, or escalate.

## Responsibilities

- **Verdict Aggregation**: Consolidate verdicts from all parallel review lanes
- **Decision Logic**: Apply rules to determine merge-decision outcomes (merge/blocked/retry/escalate)
- **Structured Logging**: Record all decisions with latency and reason for dashboard integration
- **Anomaly Tracking**: Monitor retry and escalation rates for operational health

## Decision Outcomes

| Outcome | Meaning | Trigger |
|---------|---------|---------|
| **merge** | PR approved and ready to merge | All lanes pass and no blocking issues |
| **blocked** | PR closed due to unresolved concerns | Max fix attempts exhausted with concerns remaining |
| **retry** | Mixed verdicts; attempting another review pass | Some lanes pass, others have concerns |
| **escalate** | Decision escalated to human reviewer | Backend unavailable or unhandled edge case |

## Latency Baseline

All merge decisions should complete within 500 milliseconds from:
- Verdict consolidation start
- CI gate checks
- Metadata loading

Slower decisions typically indicate CI API delays, GitHub rate-limiting, or review backend timeouts.

## Anomaly Detection Thresholds

- **Retry Rate**: More than 20 percent of decisions are retries (indicates review instability)
- **Escalation Rate**: More than 5 percent of decisions escalate (indicates backend issues)
- **CI Delay**: More than 5 consecutive CI wait cycles (indicates pipeline slowness)

## Structured Logging Format

All decisions are logged as structured JSON:

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
- `decision`: Outcome type (merge/blocked/retry/escalate)
- `latency_ms`: Time from consolidation start to decision
- `reason`: Decision rationale (e.g., "self_review_lgtm", "fix_attempts_exhausted")
- `lanes`: Number of parallel verdict lanes processed
- `timestamp`: Unix timestamp when decision was recorded

## Integration with Instrumentation

The `MergeDecisionInstrumenter` class tracks all decisions and provides:
- Real-time metrics summary via `get_metrics_summary()`
- JSON export via `export_metrics_json()`
- Latency histograms (min, max, average, percentiles)
- Outcome distribution and rate analysis

## Health Status Indicators

| Metric | Healthy | Degraded | Critical |
|--------|---------|----------|----------|
| Average latency | Less than 300 milliseconds | 300-500 ms | More than 500 milliseconds |
| Retry rate | Less than 10 percent | 10-20 percent | More than 20 percent |
| Escalation rate | Less than 3 percent | 3-5 percent | More than 5 percent |

## Implementation Location

`src/operations_center/reviewer/instrumentation.py` — Core `MergeDecisionInstrumenter` class and verdict consolidation logic.
