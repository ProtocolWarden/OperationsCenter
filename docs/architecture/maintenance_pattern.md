# Maintenance-task registration pattern

_Status: shipped 2026-05-22 — ADR 0007 follow-up D_

## Why

Before this pattern, every hygiene/maintenance operation (spec archival,
campaign recovery, backlog promotion, projection rebuilds, etc.) was
hardcoded as a sequence of steps inside the host watcher's `run_once()`.
Adding a new maintenance step meant editing the watcher in-place, and
operators had no uniform observability surface — each step logged in its
own ad-hoc shape, intervals were implicit, and disabling one step required
a code change.

ADR 0007 explicitly noted this gap under _Considered alternatives_:

> watchdog has no registration mechanism; adding hardcoded steps conflates
> spec concerns with general health

…and parked it under _Out of scope_:

> Generic watcher-registration mechanism for the watchdog (would be useful
> but is its own work).

Follow-up D lands that mechanism.

## Surface

`operations_center.maintenance` exports four names:

```python
from operations_center.maintenance import (
    MaintenanceTask,     # Protocol any maintenance op implements
    MaintenanceContext,  # per-cycle context handed to run_once
    MaintenanceResult,   # uniform structured outcome
    MaintenanceRegistry, # registers tasks, runs the ones whose interval elapsed
)
```

### `MaintenanceTask` (Protocol, `@runtime_checkable`)

```python
class MaintenanceTask(Protocol):
    name: str                # e.g. "spec_hygiene", "ghost_audit"
    interval_seconds: int    # how often this task wants to run (advisory)
    enabled: bool            # config-driven on/off

    def run_once(self, ctx: MaintenanceContext) -> MaintenanceResult: ...
```

### `MaintenanceContext`

Carries `cycle_id` (UUID per watchdog cycle so results correlate),
`now: datetime` (UTC), and a free-form `resources` dict so the host can
hand in pre-constructed clients (a `PlaneClient`, `settings`, …). Tasks
pull what they need; if a resource is absent the task is responsible for
constructing its own.

### `MaintenanceResult`

```python
@dataclass
class MaintenanceResult:
    name: str
    status: Literal["ok", "skipped", "failed"]
    duration_seconds: float
    details: dict[str, Any]   # free-form, task-specific summary
    error: str | None         # populated only when status == "failed"
```

### `MaintenanceRegistry`

```python
registry = MaintenanceRegistry(state_path=Path(".console/maintenance_state.json"))
registry.register(SpecHygieneTask(settings, plane_client))

# Each cycle:
ctx = MaintenanceContext(cycle_id=str(uuid4()), now=datetime.now(UTC), resources={...})
for result in registry.run_due(ctx):
    log_uniformly(result)
```

`run_due` rules:

- A task runs only if `enabled` and `interval_seconds` has elapsed since
  its last run (or it has never run).
- Tasks not due are skipped silently (no result emitted).
- A failing task yields a `status='failed'` result with an `error`
  string; the registry continues to the next task — one failure does not
  block others.
- Last-run timestamps persist to `.console/maintenance_state.json` so
  intervals survive watchdog restarts. The path is gitignored via the
  existing `.console/*` rule.

## State file

Default path: `.console/maintenance_state.json`. Shape:

```json
{
  "last_run": { "spec_hygiene": 1747929600.0 },
  "written_at": "2026-05-22T12:00:00+00:00"
}
```

The registry tolerates a missing or malformed state file (logs a warning
and starts fresh).

## Proof of concept

`SpecHygieneTask` (in `src/operations_center/entrypoints/spec_hygiene/main.py`)
is the first migration. The original `run_once(settings, client)` function
still exists — it now returns a summary dict — and `SpecHygieneTask.run_once`
wraps it, measuring duration and translating the summary into a
`MaintenanceResult`. The standalone `operations-center-spec-hygiene` CLI
drives the same registry/task wiring, so there is one source of truth and
two execution surfaces:

1. Standalone watcher loop (existing pattern — backward-compatible).
2. Hosted under a `MaintenanceRegistry` cycle (new pattern — any future
   maintenance op can register here without touching the spec-hygiene
   code).

## Adding a new MaintenanceTask

1. Implement the protocol — any class with `name`, `interval_seconds`,
   `enabled`, and `run_once(ctx) -> MaintenanceResult` is structurally a
   `MaintenanceTask`. No base class to inherit.
2. `registry.register(YourTask(...))`.
3. Done. The registry handles scheduling, failure isolation, persistence,
   and uniform observability.

## Out of scope for this follow-up

- Migrating other watchers (custodian-sweep, ghost-audit, flow-audit) —
  ADR 0007 follow-up D landed only the pattern + the spec-hygiene proof
  of concept. Subsequent migrations are their own units of work.
- Cross-host scheduling / leader election. The registry is intentionally
  per-process; if multiple OC processes need to coordinate maintenance
  ownership that's a separate (and significantly larger) problem.
