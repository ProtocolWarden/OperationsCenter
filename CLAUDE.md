<!-- console-context -->
## OperatorConsole Context

At the start of each session, read the compiled context before acting:

- `.console/.context` — compiled startup context (generated fresh each launch)

The context file contains your current task, guidelines, backlog, log, and runtime context.

**Source files** (editable truth — update these, not the context file):

| File | Role |
|------|------|
| `.console/task.md` | Current objective and definition of done |
| `.console/guidelines.md` | Repo policy, branch rules, operating constraints |
| `.console/backlog.md` | Work inventory — in-progress, up-next, done |
| `.console/log.md` | Recent decisions, stop points, what changed and why |

After meaningful progress, update `.console/backlog.md` and `.console/log.md`.
Do not edit `.console/.context` directly — it is regenerated at each launch.
<!-- /console-context -->

## Cognition Lifecycle

OC uses [ContextLifecycle](https://github.com/ProtocolWarden/ContextLifecycle) for bounded, resumable agent sessions.

| Surface       | Purpose                                                      |
|---------------|--------------------------------------------------------------|
| `.console/`   | Operational truth — task, guidelines, backlog, log           |
| `.context/`   | Durable cognition — capsules, checkpoints, handoffs, leases  |
| `.claude/`    | Claude Code adapter — ContextGuard hooks                     |

**Orchestrator lifecycle:**

```
wake → read .context/checkpoints/<latest>.yaml
     → read active capsule refs
     → classify state
     → dispatch bounded worker if needed
     → write updated checkpoint
     → update .console/log.md
     → terminate or compact
```

**On session start:** Check `.context/active/` for any active capsules. Check `.context/checkpoints/` for the latest checkpoint.
**On session end:** Write a LoopCheckpoint. Update any active capsule's `handoff_notes` and `next_actions`.
**Templates:** `.context/templates/`
**Config:** `.context/config.yaml`
