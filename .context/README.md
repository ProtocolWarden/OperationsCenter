# .context/ — OperationsCenter

Runtime-neutral durable cognition surface for OperationsCenter.

Implements the [ContextLifecycle](https://github.com/ProtocolWarden/ContextLifecycle).

OC is the orchestration and execution boundary. Its watchdog loops and worker dispatches must not accumulate unbounded conversational history. This surface enables checkpoint-driven resumable operation.

---

## OC Lifecycle

```
orchestrator wakes
→ reads .context/checkpoints/<latest>.yaml
→ reads active capsule refs
→ classifies state
→ dispatches bounded worker if needed (via WorkerHandoff)
→ writes updated checkpoint
→ writes .console/log.md summary
→ terminates or compacts
```

OC state lives in artifacts. Not in the conversation.

---

## Directory Layout

| Path            | Purpose                                              |
| --------------- | ---------------------------------------------------- |
| `schemas/`      | OC-specific schema extensions (imports from CLP)     |
| `templates/`    | OC-specific starter templates                        |
| `examples/`     | OC watchdog and worker examples                      |
| `active/`       | Active investigation capsules                        |
| `archive/`      | Resolved capsules                                    |
| `checkpoints/`  | Watchdog loop checkpoints (one per cycle)            |
| `capsules/`     | Finalized investigation capsules                     |
| `leases/`       | Worker lease definitions                             |
| `handoffs/`     | Worker handoff records                               |
| `tmp/`          | Scratch state — gitignored                           |

---

## Config

See `config.yaml` in this directory.

## CLP Reference

Schemas: https://github.com/ProtocolWarden/ContextLifecycle/tree/main/.context/schemas/
