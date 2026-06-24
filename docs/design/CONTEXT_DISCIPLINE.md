---
status: resolved
---

# Context Discipline

**Status: RESOLVED (2026-06-24).** WON'T-BUILD the subsystem (~80% already
exists). Investigation reshaped the one candidate "fix": `request.timeout_seconds`
defaults to 300 (proposal constraint), `openclaw` honors it, `dag` overrides with
the 3600 settings value — the backends disagree on authority, and forcing `dag` to
honor the request would *regress* live tasks 3600→300. So this shipped a
de-silencing comment in `dag_executor/adapter.py`, not a risky change. See
Resolution below. This maps Praetorian's "bounded context / controlled memory
transfer / context lifecycle" primitive onto OC. One of four open-gap specs from
the Osprey/Praetorian arc; see also
[Lineage Visualization](./LINEAGE_VISUALIZATION.md),
[Risk-Tiered Approval](./RISK_TIERED_APPROVAL.md),
[Runtime Capability Enforcement](./RUNTIME_CAPABILITY_ENFORCEMENT.md), and the
master [Execution Lineage & Determinism Boundary](./EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md).

**Headline verdict:** WON'T-BUILD as a subsystem. ~80% of "context discipline"
is already present under other names; the remainder is one tiny fail-closed fix
(Delta A) and one cross-repo turn-cap that is the operator's call (Delta B). Most
of what *looks* like a context-discipline gap is the SBX-off-by-default decision
in disguise.

## Current real state

How OC lanes acquire / bound / transfer context today:

- **Acquire.** `board_worker` does not build the LLM prompt. It claims one Plane
  issue, extracts a goal string (`board_worker/_text.py`), nonce-fences the
  untrusted part (`dispatch.py` → `injection.py` `wrap_untrusted_goal`), and
  appends trusted scaffolding (Definition-of-Done, output contract, ≤5 prior
  rejection hints). The real prompt is assembled downstream in the external
  **TeamExecutor** repo, whose `cwd` is a full clone of the branch tips — so the
  dominant "context" is the working tree the agent reads at will, not the prompt
  string.
- **Bound — weakly, on the wrong axis.**
  - `max_turns` is **vaporware in OC** (zero hits in `src/`); it is a field in
    TeamExecutor's `team.yaml` that is loaded but **never appended to the `claude`
    CLI argv**. The agentic loop has no turn cap.
  - The only enforced runtime bound is a **wall clock** (static 3600s).
    Host-level rlimit wall-cap (`OC_RLIMIT_WALL_SEC`) is off-by-default +
    fail-open.
  - `max_files` / `max_lines` bound the output **diff** post-hoc — a commit gate,
    not an input-context cap.
  - **No token budget anywhere** (`RunTelemetry.llm_input_tokens` is
    reporting-only).
  - The one real in-run context bound lives in TeamExecutor:
    `summarizer.py` `_TOKEN_THRESHOLD = 4000` summarizes a prior stage before
    carrying it forward.
- **Transfer.** Across tasks: effectively none (fresh temp dir + clone + process
  per issue; lineage is recorded after success as a display-only read-model,
  never fed back into a prompt). The `cl`/ContextLifecycle subsystem does
  anchored persistence + an advisory honor-system gate and contains **zero** size
  machinery; its hydrate path is skipped entirely for the claude/opus backends OC
  runs. (This confirms the prior "consolidation middle DORMANT" — it was never
  built; only field slots exist.)

**Verified for this spec:** `backends/team_executor/adapter.py` `_run_once` calls
`runner.run(goal_text=..., invocation_id=...)` and **drops
`request.timeout_seconds`** — the per-task timeout an operator sets is silently
ignored by the LLM executors.

## Steelman (the strongest case there IS a gap)

1. **Unbounded agentic loop = real cost/divergence exposure.** `max_turns`
   unwired + per-task timeout dead → a single hard/ambiguous task can burn the
   full 3600s; nothing caps turns or tokens. The textbook Praetorian
   "unbounded context → runaway."
2. **Context laundering past the fence.** The board nonce-fence is real, but
   untrusted content re-enters unfenced via (a) the agent reading arbitrary repo
   files (incl. any in-repo `CLAUDE.md`) and (b) TeamExecutor's cross-stage
   summary text concatenated into the next goal outside any fence. Fenced data →
   model output → unfenced carried context is a genuine laundering channel.
3. **The "load-bearing" control is off.** `injection.py` declares the fence
   non-load-bearing and names "the worker's sandbox / capability-reduction" as
   the real control — but sandbox, egress netns, and rlimits are all
   off-by-default + fail-open. So by default neither the fence nor its backstop
   constrains the agent.

## Adversarial round 1

Bounded-context here is a **quality/cost concern, not a determinism/safety
primitive**, and the safety slices already have owners:

- **Unbounded loop / cost** is not a subsystem problem; it is one dead field. The
  fix (wire `max_turns` into the `claude` argv) lives in **TeamExecutor**, not
  OC. Building an OC-side context-budget manager to fix a missing CLI flag in
  another repo is wrong-layer + over-engineering. The 3600s wall-clock already
  bounds the runaway, crudely.
- **Laundering.** The board fence is *explicitly* defense-in-depth, never
  load-bearing. Re-fencing the cross-stage summary adds a second non-load-bearing
  layer — defeated by the same instruction-via-data. Per "capability-reduction
  beats detection," more fencing is the pattern the priors rank *below* reducing
  capability. The correct control is the sandbox + egress containment (SBX axis),
  already designed and wired, just defaulted off.
- **Sandbox off-by-default** is the genuinely load-bearing fact — but it is the
  *known* SBX posture decision, not a context gap. Re-litigating it under a
  "context discipline" banner is mislabeling an owned, deliberately-deferred
  decision.

## Adversarial round 2

What survives is narrow and tiny:

- The unwired turn cap is a real defect, but the OC-side move is
  **deletion/honesty**, not addition — stop carrying a dead `max_turns` /
  per-task `timeout_seconds` that lie about being enforced. The actual fix is
  cross-repo (TeamExecutor argv).
- The laundering channel is real, but a measured **size** budget does nothing to
  close it — a 4000-token summary of a malicious instruction still carries the
  instruction. Bounding size is orthogonal to bounding trust; the only capability
  reduction is the sandbox (off by default). So this collapses back into the SBX
  deferral and gives **zero** independent justification for a context-budget
  mechanism.
- **One thing genuinely survives:** OC ships a per-task `timeout_seconds` the LLM
  executors silently ignore — a **fail-open lie**. An operator who sets a tight
  bound on a suspect run gets no enforcement and no signal. This is OC's own
  layer (it owns the work-order → adapter handoff) and is covered by nothing
  else.

## Minimal real delta

- **Delta A — BUILD-minimal (~3-5 lines).** In
  `backends/team_executor/adapter.py`, thread `request.timeout_seconds` (when
  set) into `TeamExecutorRunner.run()`, falling back to the static setting only
  when unset (mirror the dag/critique adapter pattern). Turns an existing
  fail-open (operator sets a bound, nothing enforces it) into a real per-task
  wall-clock bound. If the external Runner signature lacks the param, the honest
  alternative is to **delete the dead `timeout_seconds` plumbing** so the
  contract stops promising an unenforced bound.
- **Delta B — NEEDS-OPERATOR-DECISION (cross-repo).** Wire `max_turns` into
  TeamExecutor's `claude` argv (`--max-turns <role.max_turns>`). The genuine
  bounded-context win, but it lives in TeamExecutor and crosses the cross-repo /
  code-failure line — the operator's call to file there.

Explicitly **not** built: no context-budget manager, no token-ceiling enforcer,
no eviction/retention policy, no re-fencing of summaries or repo reads, no new CL
"consolidation middle."

## Disposition

| Item | Disposition | Why |
|---|---|---|
| Greenfield context-discipline subsystem | **WON'T-BUILD** | ~80% already exists; remainder is wrong-layer or redundant-with-SBX. |
| Per-task `timeout_seconds` ignored by LLM executors | **BUILD-minimal (Delta A)** | A real OC-layer fail-open; ~3-5 lines. |
| `max_turns` unwired into the agent CLI | **NEEDS-OPERATOR-DECISION (Delta B)** | The real win, but lives in TeamExecutor; crosses the cross-repo line. |
| Cross-stage summary / repo-file injection laundering | **DEFER → fold into SBX** | Bounding size ≠ bounding trust; the real fix is the (owned, deferred) sandbox/egress. |

## Resolution (2026-06-24)

- **WON'T-BUILD** the context-discipline subsystem — confirmed.
- **Shipped:** a de-silencing comment in `dag_executor/adapter.py` documenting why
  it uses the settings timeout (not `request.timeout_seconds`) and that the
  authority question is open. No live behavior change.
- **Operator decision (recorded):** are per-task proposal constraints authoritative
  over backend settings? This one question governs timeout AND the dropped
  `allowed_paths` / `max_changed_files` / `validation_commands` — tracked as the
  systemic theme in [Inert Machinery Inventory](./INERT_MACHINERY_INVENTORY.md).
- **Cross-repo (recorded):** wire `max_turns` into TeamExecutor's `claude` argv
  (team_executor is not even installed in this env).
