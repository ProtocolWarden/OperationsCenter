---
status: resolved
---

# Runtime Capability Enforcement (C2)

**Status: RESOLVED (2026-06-24).** DEFER — dormant-by-environment is correct.
SHIPPED the anti-rot guard (the test no longer cements "None forever") + a
probe-target note. See Resolution below. One of four open-gap specs from the Osprey/Praetorian arc; see also
[Context Discipline](./CONTEXT_DISCIPLINE.md),
[Lineage Visualization](./LINEAGE_VISUALIZATION.md),
[Risk-Tiered Approval](./RISK_TIERED_APPROVAL.md), and the master
[Execution Lineage & Determinism Boundary](./EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md).

**Headline verdict:** DEFER — keep it dormant. Dormant-by-environment is the
*correct* intended state. The only real risk is **silent rot**: the probe targets
the wrong module for the live load path, and a test locks in "None forever." Land
a tiny observability + anti-rot slice so the dormancy is loud; escalate the
probe-target question.

## Current real state

- **The mechanism exists.** `capability_ownership.py`: `resolve_owner` mirrors
  RepoGraph's build-time invariant (count `owns` edges, raise on ≠1);
  `verify_owner_or_degrade` is the gate — no-op when `required=False`, DEGRADE
  and proceed when the registry is None, REFUSE only on a *loaded* registry with
  ambiguous/mismatched owner. One call site: `board_unblock_task.py`, guarded by
  `settings.require_capability_owner` (default `False`) with
  `expected_owner=self_repo_key`.
- **Why it is dormant — a version-pin fact (verified).** `load_capability_registry`
  does `importlib.import_module("repograph")` then
  `getattr(mod, "load_capability_registry", None)`. In OC's venv `repograph` is
  installed (v0.2.0, transitively via `platform-manifest@v1.0.0`) but **has no
  `load_capability_registry`** → returns None. The capabilities plane
  (`repograph/capabilities/{compiler,validation,…}.py`) lives on RepoGraph **HEAD
  only**, on no released tag.
- **The probe targets the wrong module for the live path (verified).**
  `capability_ownership.py` imports **bare `repograph`** and looks for
  `repograph.load_capability_registry`. But the real consumer-facing API is
  `platform_manifest.capabilities.load_capabilities()`, which returns a
  `CapabilityRegistry` with `.edges` and is what a live OC would call. If the
  plane ships only through PM's wrapper, OC's probe stays None **forever**.
- **A graph-level gate already enforces — a different thing than assumed.** Two
  distinct mechanisms: (1) **exactly-one-owns** is enforced at *build/compile*
  time in `repograph/capabilities/validation.py` (raises whenever the registry is
  compiled — a duplicate-owner registry cannot even load); (2) **Custodian CAP1**
  (`capability_refs.py`) does **not** check owner-count at all — it checks that an
  owned capability's `invocation.ref` resolves to existing code (rot detection).
  OC subscribes to CAP1 (`.custodian/config.yaml`); the registry-side CAP1 runs
  in PM CI.

## Steelman

The async gates are temporally wrong for *prevention*. Build-time validation runs
when the registry is authored/compiled in PM; CAP1 runs in CI. Both are "code
acts first; the sweep flags drift later." A maintenance lane (`board_unblock`, a
fleet-mutating capability) executes between sweeps. If the registry were ever in
an ambiguous state at the moment it fires, a **synchronous** check refuses *that
specific privileged mutation* before it touches the fleet — preventive, not
detective, on the highest-value action.

## Adversarial round 1

- **The threat model is empty in this fleet.** Single operator, operator-authored
  board, operator-authored registry. For runtime enforcement to *prevent*
  something the registry must reach ambiguity *while the fleet runs* and
  `board_unblock` must fire against it. But a duplicate `owns` edge **cannot be
  loaded** (validation raises at compile), so the REFUSE branch is **unreachable
  against any registry that successfully loaded**. The only reachable REFUSE is an
  `expected_owner` mismatch — which fires only if the operator intentionally
  reassigns ownership in the registry and forgets to update OC's `self_repo_key`:
  a typo in two operator-controlled files, caught at the next CAP1 sweep, on a
  task that degrades safely. Not an adversary.
- **The graph gate already covers the real risk.** The real capability-integrity
  risk here is **registry rot** (a capability pointing at moved/deleted code) =
  exactly CAP1, which OC already enforces and PM CI runs cross-repo.
  Exactly-one-owns is enforced at compile. Runtime adds no integrity check the
  graph layer doesn't already make structurally true or detect.
- **"Dormant until the plane ships" is the correct, intended state.** Textbook
  fail-open opt-in: present + tested, default off, degrades to proceed when input
  absent, activates when its input ships. It honors the self-healing invariant
  precisely — a missing registry must never halt `board_unblock`, and it doesn't.
- **Un-dorming is costly and asymmetric.** To go live you must put a
  registry-exposing dependency in OC's runtime: either bump `platform-manifest`
  to a commit whose transitive `repograph` is HEAD-with-plane (**no such release
  exists**) or vendor RepoGraph HEAD. That adds version coupling (OC blocks on
  RepoGraph cutting a plane release), supply-chain surface (an unreleased dep on
  `main`), and a new failure mode — `load_capabilities()` runs the full registry
  compiler in OC's hot maintenance loop. You add coupling to the most
  safety-critical self-heal lane to buy prevention of a non-threat.

## Adversarial round 2

What survives is not "make it live" — it is the single concrete defect: **silent
rot of the dormant mechanism.** Attacked skeptically:

- **The probe can never activate via the live path.** Even on the day RepoGraph
  ships a plane release and OC bumps it, the probe imports `repograph` and looks
  for `repograph.load_capability_registry`; the live fleet path is
  `platform_manifest.capabilities.load_capabilities`. If the plane ships only
  through PM's wrapper, the guard stays None forever while the operator believes
  it "activates on input." The test `test_load_returns_none_in_this_environment`
  **locks in** the dormant result and keeps passing for the wrong reason after the
  plane ships.
- **Counter-attack:** the wrong-module risk is conditional on *how* RepoGraph
  ships the symbol (if it re-exports `load_capability_registry` at top level, bare
  `import repograph` would find it) — RepoGraph's call, not OC's. That weakens
  "fix the probe now" to "the probe's assumption is unverifiable until the dep
  ships." Which lands on: the only defensible delta is making the **dormancy
  observable**, not changing the probe to chase an unreleased shape.
- **Does the mechanism deserve to exist?** It is redundant-by-construction
  (compiler guarantees ≠ambiguous; CAP1 covers rot); load-bearing value ≈ zero.
  But it is cheap, tested, fail-open, and on the right action; deleting it
  re-opens the "surface has no synchronous layer" audit finding for no gain. Its
  presence is fine; its **invisibility** is the liability.

## Minimal real delta

Do **not** "make it live now" (needs an unreleased RepoGraph release or vendoring
HEAD; adds coupling + a new failure mode to the most critical self-heal lane, to
prevent a non-threat). The real delta makes dormancy explicit and rot-proof:

- **Primary — BUILD-minimal (~10 lines, observability).** When `required=True`
  and the registry is None, surface a one-shot `capability_owner_unverifiable`
  WARN / heartbeat field to the operator console (heartbeat plumbing already
  pipes OC status out). Goal: an operator who *sets* `require_capability_owner=True`
  believing it enforces SEES that it is silently degrading. File:
  `board_unblock_task.py` (emit into existing details/heartbeat) or a `settings.py`
  validator.
- **Anti-rot test pin (higher-value half).** Replace/augment
  `test_load_returns_none_in_this_environment` with a test asserting the
  **activation contract** — iff a module exposing `load_capability_registry` is
  importable, the guard transitions out of degrade. As written, the test cements
  "None forever" and passes for the wrong reason post-ship.
- **Probe-target — NEEDS-OPERATOR-DECISION (not an unprompted edit).** Record in
  the docstring that the live registry ships via
  `platform_manifest.capabilities.load_capabilities`, and that the bare-`repograph`
  probe is intentional-pending-confirmation of RepoGraph's export shape. Changing
  the probe target binds OC to a different dependency surface — an operator call.

## Disposition

| Item | Disposition | Why |
|---|---|---|
| Make runtime capability enforcement live | **DEFER (keep dormant)** | Dormant-by-environment is correct; redundant with build-time exactly-one-owns + CAP1; no live threat; un-dorming adds coupling to the critical self-heal lane. |
| Silent rot (probe can't activate via live path; test locks in "None forever") | **BUILD-minimal** | ~10-line observability + an activation-contract test, so dormancy is loud not silent. |
| Probe target (`repograph` vs `platform_manifest.capabilities`) | **NEEDS-OPERATOR-DECISION** | Binds OC to a dependency surface; decide before any go-live. |

## Resolution (2026-06-24)

- **DEFER (keep dormant)** — confirmed correct: redundant with build-time
  exactly-one-owns + Custodian CAP1, no live threat, un-dorming would add coupling
  to the critical self-heal lane.
- **Shipped:** replaced the rot-trap test (`test_load_returns_none_in_this_environment`,
  which cemented "None forever") with activation-contract tests (loader importable
  → the guard uses it; absent → None; module absent → None). Added a PROBE TARGET
  docstring note in `capability_ownership.py`. The existing degrade WARNING already
  surfaces `capability_owner_unverifiable`.
- **Operator decision (recorded):** the probe targets bare `repograph` but the live
  registry ships via `platform_manifest.capabilities` — decide the binding surface
  before any go-live. Tracked in [Inert Machinery Inventory](./INERT_MACHINERY_INVENTORY.md).
