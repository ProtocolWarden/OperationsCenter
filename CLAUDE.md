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

OC uses [ContextLifecycle](https://github.com/ProtocolWarden/ContextLifecycle) for bounded, resumable agent sessions. **Cognition is hosted by the anchoring manifest** — OC carries no `.context/` of its own. Per P3 of `PlatformDeployment/docs/architecture/adr/0002-work-order-manifest-cognition.md`, every Claude Code session targeting OC must first run `eval $(cl session start PlatformManifest)` (or `PrivateManifest` for private work). All capsules, checkpoints, and handoffs land under the anchor's `.context/sessions/<CL_SESSION_ID>/` subtree.

| Surface                                | Purpose                                                              |
|----------------------------------------|----------------------------------------------------------------------|
| `.console/`                            | Operational truth — task, guidelines, backlog, log                   |
| `.console/workers.yaml`                | OC worker/watcher definitions (replaces old `.context/config.yaml`)  |
| `.console/loop_schedule.json`          | Runtime watchdog state (cycle delay) — local runtime, not cognition  |
| `<anchor>/.context/sessions/<sid>/`    | Durable cognition (capsules, checkpoints, handoffs) on the manifest  |
| `.claude/`                             | Claude Code adapter — ContextGuard hooks (CL shim per ADR 0002 P5)   |

**Orchestrator lifecycle:**

```
wake → read <anchor>/.context/sessions/<sid>/checkpoints/<latest>.yaml
     → read <anchor>/.context/sessions/<sid>/active/ capsule refs
     → classify state
     → dispatch bounded worker if needed
     → write updated checkpoint to <anchor>/.context/sessions/<sid>/checkpoints/
     → update .console/log.md
     → terminate or compact
```

**On session start:** verify `CL_ANCHOR` is set. Check `<anchor>/.context/sessions/<CL_SESSION_ID>/active/` for any active capsules; check `checkpoints/` for the latest checkpoint.
**On session end:** write a LoopCheckpoint to `checkpoints/`. Update any active capsule's `handoff_notes` and `next_actions`. `cl session end` archives the session subdir.
**Templates:** `<anchor>/.context/templates/`.
**Config:** `<anchor>/.context/config.yaml` (CL guard flags) and `.console/workers.yaml` (OC operational config).
