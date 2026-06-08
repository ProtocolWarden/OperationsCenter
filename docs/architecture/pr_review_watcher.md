# PR Review Watcher Architecture

## Overview

The PR Review Watcher is a long-running process that manages the automated review and merge workflow for pull requests created by the Operations Center. It implements a two-phase state machine for handling PR verdicts: self-review (automated) and human review (escalated).

## Responsibilities

- **PR State Management**: Maintains persistent state for each open PR in `state/pr_reviews/`
- **Self-Review Phase**: Polls for PR verdicts and decides between merge, revision, or escalation
- **Human Review Phase**: Monitors PR comments and reactions to determine human approval or request revisions
- **Revision Loop Control**: Enforces maximum loop counts to prevent indefinite revision cycles
- **Timeout Fallback**: Auto-merges PRs that exceed the human review timeout threshold (1 day)

## Key Components

### State File Schema

State for each PR is stored at `state/pr_reviews/<owner>/<repo>/<pr-number>.json`:

```json
{
  "task_id": "...",
  "pr_number": 42,
  "repo_key": "OperationsCenter",
  "phase": "self_review",
  "self_review_loops": 0,
  "human_review_loops": 0,
  "created_at": "2026-04-27T00:00:00Z",
  "updated_at": "2026-04-27T00:00:00Z"
}
```

### Phase 1: Self-Review (Automated)

1. Executor reads PR diff and produces verdict: `LGTM` or `CONCERNS`
2. **LGTM** → PR is merged automatically
3. **CONCERNS** → Run revision pass (up to `max_self_review_loops` times)
4. If unresolved → Escalate to Phase 2

### Phase 2: Human Review (Escalated)

1. Post escalation comment with summary of concerns
2. Monitor for human approval (emoji reaction or `/lgtm` comment)
3. Dispatch revision passes on human request (up to `max_human_review_loops` times)
4. Auto-merge if no action after 1 day timeout

## Integration Points

- **Executor**: Calls `worker.main` + `execute.main` pipeline for verdict decisions
- **GitHub API**: Monitors PR state, posts comments, triggers merges
- **Plane**: Updates task status to Done when PR is resolved
- **State Persistence**: Reads/writes state files to track PR lifecycle

## Safety Invariants

- Never processes comments marked with `<!-- operations-center:bot -->`
- Always filters `bot_logins` to prevent loops
- State file is the single source of truth for PR progress
- GitHub merge happens before Plane task is marked Done

## Entry Point

`src/operations_center/entrypoints/pr_review_watcher/main.py` — CLI accepts `--config`, `--watch`, `--poll-interval-seconds`, `--status-dir`.
