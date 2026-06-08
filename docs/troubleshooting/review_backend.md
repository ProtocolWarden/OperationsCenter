# Review Backend Troubleshooting Guide

## Overview

This guide covers diagnosis and recovery procedures for common issues in the PR review backend, including verdict generation failures, state machine stalls, and integration problems.

## Common Issues and Solutions

### PR Verdict Generation Timeout

**Symptoms**: Review backend takes longer than 2 seconds to produce a verdict.

**Diagnosis**:
1. Check executor logs for review lane execution time
2. Verify GitHub API rate limit status (quota remaining)
3. Inspect review pipeline logs for slow stages

**Solutions**:
- Increase GitHub token rate limit quota
- Check for network latency to GitHub API
- Review backend may need scaling — contact SRE

### Self-Review Loop Not Progressing

**Symptoms**: PR stuck in self-review phase with no progression to revision or escalation.

**Diagnosis**:
1. Check `state/pr_reviews/<pr-number>.json` for `self_review_loops` count
2. Verify executor is being called (check logs for `execute.main` invocations)
3. Look for verdict file creation failures

**Solutions**:
- Verify executor permissions for writing verdict files
- Check workspace disk space
- Restart pr_review_watcher if stuck on specific PR

### Human Escalation Comment Not Posting

**Symptoms**: PR escalated to Phase 2 but no bot comment appears on GitHub PR.

**Diagnosis**:
1. Check GitHub API token validity and rate limits
2. Verify PR visibility (not archived/deleted)
3. Review pr_review_watcher logs for API call failures

**Solutions**:
- Verify GitHub token has permission to comment on PRs
- Check token has not expired
- Ensure bot account is not rate-limited

### High Retry Rate (more than 20 percent)

**Symptoms**: Metrics show frequent retries in verdict consolidation.

**Diagnosis**:
1. Sample recent retry decisions from structured logs
2. Check if same repository/branch patterns are retrying
3. Inspect PR diffs for patterns (size, file counts)

**Common Causes**:
- Large diffs causing review backend timeout
- Flaky verdict pipeline (transient errors)
- Incompatible file types triggering review errors

**Solutions**:
- Consider splitting large PRs into smaller changes
- Check for recent commits to review backend code
- Monitor review backend resource usage (CPU, memory)

### Escalation Spike (more than 5 percent escalating)

**Symptoms**: Sudden increase in escalated decisions without obvious cause.

**Diagnosis**:
1. Check recent commits to reviewer code
2. Review review backend deployment status
3. Look for rate-limit errors in backend logs
4. Verify GitHub API connectivity

**Solutions**:
- Revert recent changes if correlation found
- Check review backend service health
- Verify network connectivity to GitHub
- Contact SRE if backend service is down

### PR Merged Manually Before Bot Action

**Status**: Safe operation. No intervention needed.

The review watcher checks merge status before each action. If a PR is already merged, it automatically transitions the task to Done and cleans up the state file.

### Human Comment Not Triggering Revision

**Symptoms**: Human posts comment on PR but no revision pass is triggered.

**Diagnosis**:
1. Verify human's GitHub login is in `allowed_reviewer_logins` if configured
2. Check human commented on PR itself (not on commit or Plane task)
3. Confirm comment does not carry bot marker `<!-- operations-center:bot -->`

**Solutions**:
- Add human login to `allowed_reviewer_logins` config
- Ask human to comment directly on PR (not commit)
- Check for bot markers in comment HTML

## Health Checks

Run these commands to verify review backend health:

```bash
# Check PR state file integrity
cat state/pr_reviews/*/*/pr-*.json | python3 -m json.tool

# Verify executor can run
python -m operations_center.execution.execute --dry-run

# Check GitHub connectivity
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit
```

## Metrics Dashboard

Monitor these metrics for early warning signs:

- **Latency trend**: Watch for sustained increases above 500 milliseconds
- **Retry rate**: Alert if exceeds 20 percent threshold
- **Escalation rate**: Alert if exceeds 5 percent threshold
- **PR cycle time**: Track from open to merge completion

## Related Documentation

- [PR Review Watcher Architecture](../architecture/pr_review_watcher.md)
- [Verdict Consolidation State Machine](../architecture/verdict_consolidation.md)
- [Merge Decision Instrumentation Guide](../operator/merge_decision_instrumentation.md)
