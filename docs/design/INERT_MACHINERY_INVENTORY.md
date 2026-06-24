---
status: open
---

# Inert Machinery Inventory

**Status: OPEN backlog — triage, do not bulk-act.** A standing inventory of
*built-but-inert* machinery in OC: code that looks like coverage but is dead,
dormant, unwired, or fed a constant, so it enforces/does nothing on the live
path. Born from the Osprey/Praetorian arc finding that "the gap is never absence;
it's present-but-dead machinery that looks present." See the four resolved gap
specs: [Context Discipline](./CONTEXT_DISCIPLINE.md),
[Lineage Visualization](./LINEAGE_VISUALIZATION.md),
[Risk-Tiered Approval](./RISK_TIERED_APPROVAL.md),
[Runtime Capability Enforcement](./RUNTIME_CAPABILITY_ENFORCEMENT.md).

Each entry is classified:
- **ROTTED** — was meant to work, silently doesn't; an operator could believe a
  control is active when it is not. (Most dangerous.)
- **DEAD** — built + (often) tested, but no live caller; should be wired or deleted.
- **DORMANT-OK** — correct fail-safe opt-in; leave it, but make the dormancy
  observable so it can't rot into silence.

Most dispositions are **wire-or-delete operator decisions**, not unilateral
edits. Verify each "no caller / fed-constant / dropped" claim before acting (grep
returning only def + tests/serialization = candidate).

## Systemic theme — per-task constraints are largely theater

The execution boundary (`execution/handoff.py`) faithfully populates
`ExecutionRequest` with per-task constraints from the proposal — `allowed_paths`,
`max_changed_files`, `validation_commands`, `require_clean_validation`,
`timeout_seconds` — but the **live execution path ignores most of them** in favor
of static env/settings values. So an operator who tightens a per-task constraint
often gets no enforcement and no signal. This is one coherent decision, not five
bugs: **should per-task proposal constraints be authoritative over backend
settings?** Resolve it once; items 1, 6, 8, and Gap-1 (timeout) are its faces.

## Ranked backlog (most-dangerous first)

| # | What | Where | Class | Disposition |
|---|------|-------|-------|-------------|
| 1 | **Per-task write allowlist `allowed_paths` is never enforced at the patch gate** — operator sets a scope allowlist, gets only the static danger-blocklist (`_BLOCKED_BASENAMES`). The one allowlist checker `ChangedFilePolicyChecker` has zero non-test callers; the live adapter drops `request.allowed_paths`. *(spot-verified: set at handoff.py:93, no enforcement reader.)* | `execution/workspace.py:498` → `adapters/workspace/patch_applier.py:272` (validate() takes no allowed_paths); `application/scope_policy.py` (uncalled) | ROTTED | Pass `request.allowed_paths` into the pre-commit validate (or wire `ChangedFilePolicyChecker`); else delete the per-task allowlist plumbing. **Highest priority — a security-shaped control that silently does nothing.** |
| 2 | **`RuntimeBindingPolicy` (per-task model-tier selection) is dead** — coordinator defaults it to None; the advertised activation method from_defaults_with_runtime_policy() **does not exist** (the only reference is a comment). So `request.runtime_binding` stays None and tier is always the static team default. | `policy/runtime_binding_policy.py`; `execution/coordinator.py:101,116` | DEAD | Add the missing constructor + pass it in `execute/main.py`, or delete module + `config/runtime_binding_policy.yaml` and stop advertising opus/sonnet/haiku tiers. |
| 3 | **Recovery loop is permanently single-attempt** — `RecoveryPolicy()` defaults `max_attempts=1`, no config wires it up, so retry/backoff/RATE_LIMIT-`retry_after`/budget-checker code is unreachable. (`policy.py:84` even shapes `_ALWAYS_REFUSE` to dodge a hollow-return static check — a "looks-implemented" tell.) | `execution/coordinator.py:121`; `execution/recovery_loop/*` | DEAD | Wire `settings.recovery.*` → `RecoveryPolicy` in `execute/main.py`, or drop the unused checkers + document the loop as single-shot. |
| 4 | **`QueueHealingEngine` (5-rule blocked-queue healer) is never run** — its only consumer is the `operations-center-triage-scan` verb, which is NOT registered in `spec_hygiene` maintenance and invoked by no systemd unit / watchdog. | `queue_healing/engine.py:12`; `maintenance/triage_scan.py:303` | DEAD | Register a QueueHealingTask wrapper in the maintenance loop, or delete `queue_healing/` + the verb. |
| 5 | **`recovery/` (parked-state machine, store, 21-field telemetry) + `recovery_policies/` budget tracker — built, test-covered, never wired** — full reference set is the packages + a single isolation test. | `recovery/parked.py`, `recovery/telemetry.py`, `recovery_policies/budget.py` | DEAD | Build the intended parking/oversight consumer (a maintenance task), or delete both packages + the test. |
| 6 | **Per-request `validation_commands` / `require_clean_validation` dropped** — live validation keys off `repo_cfg.validation_commands`; `request.require_clean_validation` has no execution-path reader. | `execution/handoff.py:97` set; `execution/workspace.py:694` uses repo_cfg | ROTTED | Prefer `request.validation_commands` when present, or remove the per-request fields. (Face of the systemic theme.) |
| 7 | **`effective_validation_profile` ("standard/elevated/critical") gates nothing** — computed + stored, only reader is a display string in `explain.py`. | `policy/engine.py:589`; `policy/models.py:253` | ROTTED | Map profile→commands at the enforcement site, or delete `required_profile`. |
| 8 | **Per-task `max_changed_files` ignored** — only the global env caps `OPS_CENTER_MAX_FILES/_LINES` are live; `spec_author` even passes `--max-changed-files` into a field nothing reads. | `execution/handoff.py:94` set; `execution/workspace.py:563` uses `self._max_files` | DEAD | Prefer `request.max_changed_files` when set, or drop the field. (Face of the systemic theme.) |
| 9 | **`policy/validate.py:validate_config` has no production caller** — it IS exercised in `tests/unit/policy/test_defaults.py:149`, but nothing calls it at config-load time, so a misconfigured live PolicyConfig (bad access_mode, duplicate repo_key) is never caught. | `policy/validate.py:25` | ROTTED | Call it where PolicyConfig is loaded and fail-closed; else accept it as a test-only invariant and document that. |
| 10 | **`key_proxy/` (cloud-key-injecting reverse proxy) orphaned** — superseded by the SNI egress proxy (subscription auth); no script/unit/spawn references it. | `entrypoints/key_proxy/main.py:119` | ROTTED | Delete the package, or document why retained. |
| 11 | **`maintenance/audit_close_receipts.py:main` fully orphaned** — unlike every sibling maintenance module, no `[project.scripts]` verb, no runbook, no spawn. | `entrypoints/maintenance/audit_close_receipts.py:79` | DEAD | Add the verb if wanted, else delete. |
| 12 | **`proposal.priority` fed-a-constant and unread** — dispatch never passes `--priority` (defaults "normal"); no executor-path consumer alters routing on it. | `worker/main.py:50`; `proposal_builder.py:54` | DEAD | Wire priority into queue ordering, or drop it from the proposal. |

## Lower-confidence / observability notes

- **`limit_classifier.models_affected`** (`backends/limit_classifier.py:115`): a no-op ternary (both branches identical) with zero callers — dead + dead-ternary.
- **`EgressProbeTask`** (`maintenance/egress_probe.py:118`) is registered but `run_once` returns `skipped` whenever `OC_EGRESS_PROXY` is unset — the *correct* DORMANT-OK fail-open, but **verify the env var is set on the live fleet**; if not, the egress-boundary assertion is permanently silent. Emit an observable "probe skipped — proxy unconfigured" signal so dormancy isn't silent. Same observability caveat for `OutcomeFlaggerTask`/`DriftMonitorTask` injection seams (`outcome_source`/`extractor` wired by no production code).
- **`escalation` adapter** (`adapters/escalation.py:post_escalation`): only live caller is the manual `autonomy-cycle` verb, so escalation never fires autonomously — reachable, not strictly inert.

## How to work this backlog

1. **Resolve the systemic theme first** (1, 6, 8, Gap-1 timeout): one operator
   decision — per-task constraints authoritative, or not? — then make the
   adapters consistent in one change instead of four.
2. **Item 1 (`allowed_paths`) is the priority regardless** — it is the only one
   shaped like a *security* control that silently enforces nothing. Spec it as a
   dedicated, opt-in fail-closed gate (mirror the sensitive-path ack gate's
   shape), not a bulk wire-up.
3. **The DEAD subsystems (2, 4, 5, 10, 11)** are each a clean *wire-or-delete*
   operator call. Defaulting to delete is healthy: a tested-but-unwired subsystem
   is a maintenance liability and a "looks-covered" trap, not an asset.
4. Whatever is kept dormant, make it **loud** (an observable skipped/degraded
   signal), per the [Runtime Capability Enforcement](./RUNTIME_CAPABILITY_ENFORCEMENT.md)
   resolution — silent dormancy is how this list grew.
