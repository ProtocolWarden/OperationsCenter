# Backend Error Catalog — Codes, Meanings, and Recovery Strategies

**Document:** Per-backend error codes, failure modes, and recovery strategies  
**Scope:** Stage 1 of error handling documentation  
**Last Updated:** 2026-05-29

---

## Overview

This catalog documents the failure modes and error codes for each OperationsCenter backend:

- **Claude (claude-code)** — Primary LLM backend via Claude Code API
- **Codex** — Fallback backend for redundancy
- **team_executor** — Team-level task orchestration
- **dag_executor** — Directed acyclic graph workflow execution
- **demo_stub** — Testing/training stub (always succeeds)

Each section covers:
1. **Common error codes** with their meanings
2. **Root causes** (backend issue vs. OC issue)
3. **Detection methods** (how to identify in logs)
4. **Recovery strategies** (manual/automatic)
5. **Escalation criteria** (when to create a Plane task)

---

## 1. Claude Backend (`claude-code`)

### Error Code Reference

| Code | HTTP | Meaning | Root Cause | Recovery |
|------|------|---------|-----------|----------|
| `RATE_LIMIT` | 429 | API usage limit hit | Backend quota exhausted | Cool down; switch to Codex |
| `AUTH_FAILED` | 401 | Invalid API key | Misconfigured credentials | Verify CL session; check `ANTHROPIC_API_KEY` |
| `TIMEOUT` | 504 | Request exceeded 10-min deadline | Large request or slow backend | Reduce scope; retry |
| `UNAVAILABLE` | 503 | Backend overloaded | Traffic surge at backend | Fallback to Codex; retry after delay |
| `INVALID_REQUEST` | 400 | Malformed request body | OC serialization bug | Escalate; check request format |
| `CONTEXT_WINDOW_EXCEEDED` | 400 | Token count > max for model | Prompt + artifacts too large | Trim context; split into smaller tasks |
| `NETWORK_ERROR` | N/A | Connection lost before completion | Network partition | Retry with exponential backoff |
| `INTERNAL_ERROR` | 500 | Backend crashed or hung | Rare backend bug | Wait 30s; retry; if repeat, escalate |

### Detailed Failure Modes

#### RATE_LIMIT (429)

**Detection:**
```bash
grep -E "429|rate.*limit|Too many requests" <session-log>
```

**Root causes:**
- Daily usage quota consumed
- Burst limit exceeded (too many requests in short window)
- Shared quota with other services (check Claude Code UI usage)

**Recovery strategy:**
1. **Automatic (built-in):**
   - Watchdog parses reset time from response header: `retry-after`
   - Cools down Claude backend for that duration
   - Switches to Codex immediately

2. **Manual override (if immediate action needed):**
   ```bash
   export OC_BACKEND=demo_stub  # Use training backend
   # Or wait until reset time (typically 24 hours for daily limit)
   ```

3. **Monitor reset:**
   ```bash
   grep -i "claude.*reset\|cooldown_until" .console/log.md | tail -1
   ```

**Escalation:**
- Single limit per day: Normal; no escalation
- Multiple limits per day (>2 in 24h): Escalate with frequency data
- Quota insufficient for workload: Escalate request for quota increase

---

#### AUTH_FAILED (401)

**Detection:**
```bash
grep -E "401|unauthorized|invalid.*api.*key" <session-log>
```

**Root causes:**
- `ANTHROPIC_API_KEY` not set or revoked
- CL session anchor lost (credentials not hydrated)
- API key has expired permissions

**Recovery strategy:**

1. **Check credential state:**
   ```bash
   echo "API Key set: ${ANTHROPIC_API_KEY:+yes}"
   echo "CL_ANCHOR: ${CL_ANCHOR:+yes}"
   ```

2. **Refresh credentials:**
   ```bash
   # If running via CL (ContextLifecycle):
   eval $(cl session start PlatformManifest)
   # This re-hydrates CL_ANCHOR and env vars
   
   # If running standalone:
   export ANTHROPIC_API_KEY=<your-key>
   ```

3. **Verify via test dispatch:**
   ```bash
   operations-center execute \
     --request-json '{"backend": "claude", "adapter": "demo"}' \
     --config config/operations_center.example.yaml
   ```

**Escalation:**
- First occurrence: Check credential setup docs
- Repeated after credential refresh: Escalate — possible key revocation or CL integration issue

---

#### TIMEOUT (504)

**Detection:**
```bash
grep -i "timeout\|504\|deadline.*exceeded" <session-log>
```

**Root causes:**
- Request took > 10 minutes to complete
- Complex code generation or analysis task
- Network latency delaying response

**Recovery strategy:**

1. **Scope reduction:**
   - Split task into smaller subtasks
   - Example: 10-file refactor → 5-file per PR
   - Reduce context window (trim logs, examples)

2. **Automatic retry:**
   - Recovery engine will retry with `TIMEOUT` classification
   - Backoff: 5s → 10s → 30s (max 3 attempts)

3. **Manual increase of deadline (if safe):**
   - In `execution/recovery_loop/engine.py`, adjust retry policy
   - Not recommended without understanding task complexity

**Escalation:**
- Consistent timeouts on same task type: Escalate for task analysis
- Suggests: Task is too complex for single execution window

---

#### CONTEXT_WINDOW_EXCEEDED (400)

**Detection:**
```bash
grep -E "context.*window|token.*limit|message too long" <session-log>
```

**Root causes:**
- Request (code + prompt + context) exceeds token limit
- Accumulated artifacts make request too large
- Request includes large test results or logs

**Recovery strategy:**

1. **Trim unnecessary context:**
   - Remove verbose logs/test output
   - Shorten prompt; use key examples only
   - Limit codebase context to relevant files

2. **Split into stages:**
   - Phase 1: Analyze (no execution)
   - Phase 2: Implement (smaller scope)
   - Phase 3: Verify

3. **Use Sonnet model (if available):**
   - Larger context window (200K tokens)
   - Switch via: `export OC_MODEL=claude-sonnet`

**Escalation:**
- Systematic window issues: Escalate for task decomposition strategy
- Suggests: Workflow needs automated context management

---

#### INVALID_REQUEST (400)

**Detection:**
```bash
grep -E "400|bad.*request|invalid.*request|schema" <session-log>
```

**Root causes:**
- OC serialization bug (malformed JSON request)
- Request doesn't match backend API schema
- ExecutionRequest validation passed but adapter-level check failed

**Recovery strategy:**

1. **Inspect the request:**
   ```bash
   operations-center-run-show <run_id> --json | jq '.request' | head -50
   ```

2. **Check for malformed fields:**
   - Non-string values in string fields
   - Missing required fields
   - Invalid enum values

3. **Escalate to engineering:**
   - File GitHub issue with request JSON
   - Tag: `bug/serialization` or `bug/request-validation`

**Escalation:**
- Any occurrence of INVALID_REQUEST: Escalate
- This indicates a bug in OC, not a transient issue

---

#### NETWORK_ERROR

**Detection:**
```bash
grep -E "connection.*refused|broken.*pipe|network.*error|timeout" <session-log>
```

**Root causes:**
- Firewall blocking Claude Code API calls
- DNS resolution failure
- Network partition (temporary)
- Client-side connection reset

**Recovery strategy:**

1. **Verify network connectivity:**
   ```bash
   curl -I https://api.anthropic.com/
   # Should return 403 (auth required) or 200, not connection error
   ```

2. **Check DNS:**
   ```bash
   nslookup api.anthropic.com
   # Should resolve without error
   ```

3. **Automatic recovery:**
   - Recovery engine retries with exponential backoff
   - Max 3 attempts with 5s → 10s → 30s delays

4. **Check firewall rules:**
   - If in corporate environment: verify HTTPS to `api.anthropic.com` is allowed

**Escalation:**
- Persistent network errors (>3 in 1 hour): Escalate for network diagnosis
- May indicate infrastructure issue

---

#### INTERNAL_ERROR (500)

**Detection:**
```bash
grep -E "500|internal.*error|backend.*crash" <session-log>
```

**Root causes:**
- Claude backend crash or hang
- Rare unhandled exception in backend
- Backend database/cache failure

**Recovery strategy:**

1. **Wait and retry:**
   - Backend failures are usually transient (< 5 minutes)
   - Recovery engine automatically retries with backoff

2. **Check backend status page:**
   - https://status.anthropic.com/ (if available)
   - Look for ongoing incidents

3. **If persists:**
   - Try Codex fallback (if available)
   - Or wait 15 minutes and retry

**Escalation:**
- Repeated 500s (>3 in 1 hour): Escalate to Anthropic support
- Include trace artifact and timing

---

### Claude Backend Monitoring

**Healthy indicators:**
- Dispatch succeeds within 2 minutes
- No 429 errors
- Session completion: 15–40 minutes (depending on task)

**Degraded indicators:**
- Frequent 504 (timeouts)
- Intermittent 503 (service unavailable)
- Dispatch latency > 5 minutes

**Critical indicators:**
- Repeated 401 (auth failure)
- Repeated 500 (backend crashes)
- All dispatches failing with same error code

---

## 2. Codex Backend

### Error Code Reference

| Code | Meaning | Root Cause | Recovery |
|------|---------|-----------|----------|
| `RATE_LIMIT` | Codex quota exhausted | Backend usage limit | Cool down; retry after reset |
| `SERVICE_UNAVAILABLE` | Codex API down | Maintenance or incident | Switch to Claude; wait for recovery |
| `INVALID_MODEL` | Model doesn't exist | Configuration error | Check model name in settings |
| `ADAPTER_TIMEOUT` | Codex HTTP request timed out | Slow backend or network | Retry; if repeat, escalate |

### Codex Deployment Context

**Important:** Codex is a fallback-only backend. It should be used when:
- Claude is rate-limited
- Claude is unavailable
- Training/testing with deterministic backend

Codex typically has:
- Smaller rate limits than Claude
- Longer response times
- Less robust error recovery

### Fallback Behavior

Watchdog implements automatic fallback:
```
Try Claude
├─ Success → Done
├─ Rate limit 429 → Cool down; switch to Codex
├─ Unavailable 503 → Switch to Codex
└─ Auth error 401 → Stop (credentials invalid)
```

Codex is never the primary; Claude always recovers when rate limit resets.

---

## 3. team_executor Backend

### Error Code Reference

| Code | Meaning | Root Cause | Recovery |
|------|---------|-----------|----------|
| `PLAN_FAILED` | Plan stage validation error | Invalid input or constraints | Review plan output; fix input |
| `EXECUTION_FAILED` | Executor task failed | Code execution error or timeout | Check execution logs |
| `VERIFICATION_FAILED` | Verification stage detected issues | Task output doesn't match spec | Retry with refined task |
| `TEAM_UNAVAILABLE` | Team executor not reachable | Service down or misconfigured | Check team_executor service; restart if needed |
| `QUEUE_FULL` | Team task queue at capacity | Too many concurrent tasks | Wait for in-flight tasks; retry |

### team_executor Failure Modes

#### PLAN_FAILED

**When it occurs:**
- Task input doesn't meet team_executor requirements
- Constraints (max files, max lines) violated
- Task scope ambiguous or impossible

**Recovery:**
1. Review the plan rejection reason
2. Refine task specification
3. Retry with clearer constraints

---

#### EXECUTION_FAILED

**When it occurs:**
- Code execution in worker timed out
- Worker ran out of memory
- File system operations failed

**Recovery:**
1. Check execution logs for specific error
2. Reduce task scope
3. Retry with smaller dataset

---

#### team_executor Service Down

**Detection:**
```bash
curl http://team_executor:8080/health
```

**Recovery:**
1. Restart service:
   ```bash
   docker-compose restart team_executor
   ```
2. Verify it's healthy:
   ```bash
   curl http://team_executor:8080/health
   # Should return: {"status": "healthy"}
   ```
3. Retry the execution

---

## 4. dag_executor Backend

### Error Code Reference

| Code | Meaning | Root Cause | Recovery |
|------|---------|-----------|----------|
| `DAG_VALIDATION_FAILED` | Workflow DAG is invalid | Cycles or missing deps | Fix DAG spec; validate with `dag_executor validate` |
| `TASK_FAILED` | DAG task (vertex) failed | Code error in task | Check task logs |
| `TIMEOUT` | DAG execution exceeded deadline | Complex workflow; slow tasks | Reduce parallelism; increase deadline |
| `RESOURCE_EXHAUSTED` | DAG execution ran out of resources | Too many parallel tasks | Reduce concurrency limit |

### dag_executor Failure Modes

#### DAG_VALIDATION_FAILED

**Common issues:**
- Circular dependencies (A → B → A)
- Missing input tasks (D requires output from C, but C not in DAG)
- Invalid task format

**Recovery:**
```bash
# Validate DAG before execution
operations-center execute \
  --request-json '{"dag": <your-spec>}' \
  --validate-only
```

If validation passes locally but fails in dag_executor:
- Escalate to dag_executor team
- May indicate versioning mismatch

---

#### TIMEOUT

**When it occurs:**
- DAG execution took > deadline (default: 30 minutes)
- Individual task timed out

**Recovery:**
1. Increase deadline in request
2. Reduce workflow parallelism
3. Profile longest-running tasks; optimize them

---

## 5. demo_stub Backend

### Behavior

**Used for:** Training, testing, CI verification  
**Always succeeds** with synthetic output  
**Error codes:** None (stub never fails)

**When to use:**
- Validating OC workflow logic (not running real tasks)
- Testing on limited compute (CI pipelines)
- Training/documentation

**Limitations:**
- Output is synthetic; never suitable for production use
- Always completes in ~5 seconds
- Not a real executor

---

## Cross-Backend Error Handling

### ErrorClassifier Logic

OC's RecoveryEngine classifies errors using this hierarchy:

```
Error → Classify as:
├─ TRANSIENT (retry eligible)
│  ├─ NETWORK_ERROR
│  ├─ TIMEOUT
│  ├─ SERVICE_UNAVAILABLE (503)
│  └─ INTERNAL_ERROR (500)
│
├─ RATE_LIMIT (special handling)
│  └─ Cool down; switch backend; resume
│
├─ NON_RETRYABLE (stop immediately)
│  ├─ AUTH_FAILED
│  ├─ INVALID_REQUEST
│  ├─ CONTEXT_WINDOW_EXCEEDED
│  └─ UNRECOVERABLE
│
└─ UNKNOWN (depends on policy)
   └─ [policy.retry_unknowns] → retry or stop
```

### Retry Budget

Each error kind has limits:

| Kind | Max Retries | Max Cost | Backoff |
|------|---|---|---|
| TRANSIENT | 5 | 300s | 5s → 10s → 30s → 60s → 120s |
| RATE_LIMIT | ∞ (until reset) | N/A | Wait until reset_time |
| TIMEOUT | 3 | 120s | 10s → 20s → 40s |
| NETWORK_ERROR | 5 | 180s | 5s → 10s → 20s → 40s → 80s |

---

## Monitoring and Alerting

### Metrics to Track

**Per backend:**
- `requests_total` — Total requests sent
- `requests_success` — Successful responses
- `error_rate` — Percentage of errors
- `p99_latency` — 99th percentile response time
- `rate_limit_count` — Frequency of rate limits

**Platform-wide:**
- `backend_fallback_rate` — % of requests using fallback (should be < 5%)
- `retry_success_rate` — % of retries that succeed (should be > 80%)

### Alert Thresholds

| Alert | Threshold | Action |
|-------|-----------|--------|
| Any backend error rate > 10% | Over 5 minutes | Page oncall |
| Backend unavailable | > 15 minutes | Page oncall |
| Rate limit reset time > 24h | When detected | Escalate quota request |
| CONTEXT_WINDOW_EXCEEDED rate | > 5% of requests | Task decomposition needed |

### Health Check Commands

```bash
# Check Claude backend
operations-center health check --backend claude

# Check Codex backend
operations-center health check --backend codex

# Check team_executor
curl http://team_executor:8080/health

# Check dag_executor
curl http://dag_executor:8080/health
```

---

## Troubleshooting Decision Tree

```
Backend error encountered?
│
├─ Is it a RATE_LIMIT?
│  └─ YES → See "Rate Limit" section above
│          No manual action; automatic recovery
│
├─ Is it TIMEOUT or NETWORK_ERROR?
│  └─ YES → Transient; retry will handle
│          Monitor for patterns
│
├─ Is it AUTH_FAILED?
│  └─ YES → Credentials issue
│          Check API key setup
│          Refresh CL session
│
├─ Is it INVALID_REQUEST?
│  └─ YES → OC bug; escalate to engineering
│          Include request JSON in bug report
│
└─ Is it SERVICE_UNAVAILABLE or INTERNAL_ERROR?
   └─ YES → Backend issue
           Switch to alternate backend
           Wait 15 minutes; retry
```

---

## References

- `src/operations_center/backends/` — Backend implementations
- `src/operations_center/execution/recovery_loop/` — Recovery engine
- `tools/loop/controller.py` — Backend selection and fallback logic
- `.console/recovery_policy.md` — Policy rules for error handling
