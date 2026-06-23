# Execution Lineage & the Determinism Boundary

**Status:** Draft spec (adversarially reviewed)
**Date:** 2026-06-22
**Related:** [[HARNESS_TRUST_HARDENING.md]], [[SELF_HEAL_LADDER.md]], `oc-harness-guide-gap-audit` (memory)

---

## 0. Thesis (corrected)

The fleet should be **emergent in the interior, deterministic at the edges, and
observable in its audit trail.** A static execution DAG is the wrong target —
it cages the emergence that makes the fleet useful. The right target is
**bounded emergence**: lanes choose their own path, but every load-bearing
boundary is a deterministic edge, and the path they took is recorded as
footprints, not dictated as rails.

Two claims from the original framing did **not** survive adversarial review and
are corrected here:

1. **"There are four deterministic surfaces."** False — there are at least
   **ten** load-bearing edges. The original four (capability ownership, the
   code-computed verdict, the required gates, the audit trail) undercount, and
   two of the four are not actually synchronous edges (see §3).
2. **"Lineage is a read-model that adds no authority AND lanes plan from it."**
   These are mutually exclusive. The instant a lane plans from lineage, lineage
   *is* authority, and an attacker-controllable source becomes a persistent
   cross-cycle injection channel (see §2). The spec resolves this with a hard
   **typed-steering / display-only split**.

---

## 1. What already exists (grounding)

A projection over current emissions is a **small additive change, not a build.**
Evidence:

### 1.1 Lineage signals already emitted, correlate on stable keys

| Signal | Source | Correlating key | Persisted? |
|---|---|---|---|
| Task lifecycle | `board_worker/claim.py:67`, `outcomes.py:37-138`; `execution/usage_store.py:854-904` | `task_id` (UUID) | Plane + `usage.json` |
| Proposal→PR | `dispatch.py:318`, `outcomes.py:129-130` (`pr-url:` label) | `task_id` + PR # | Plane label + `state/pr_reviews/*` |
| Verdict→merge | `verdict.py:80-115` (`compute_verdict`), `pr_review_watcher/main.py:2865-2876` | PR # + repo_key + head SHA | `state/pr_reviews/{repo}-{n}.json` + GH status |
| Heartbeats | `entrypoints/heartbeat.py:35-92` | role | `logs/local/watch-all/heartbeat_{role}.json` |
| Run artifacts | `execution/artifact_writer.py:26-76` | `run_id`, `task_id` | `~/.console/operations_center/runs/{run_id}/` |
| CI lineage | `outcomes.py:674` (`state/ci_lineage.json`) | `lineage_id` | disk |

`task_id` threads cleanly across all of them. **The data exists; the work is to
read and join it — and to mark each edge's trust state (§4).**

### 1.2 RepoGraph already has the vocabulary

- `EntityKind.RUN / AUDIT / EVIDENCE` already exist
  (`repograph/ontology/enums.py:101-103`).
- A **projection plane** already generates derived read-models
  (`repograph/projection/rules.py:103-160`); `Source` enum marks provenance.
- Adding lineage nodes/edges = **small** (reuse existing kinds) to **medium**
  (one minor schema bump for 1–2 new edge kinds). No library build.

### 1.3 The two facts that break the naive design

- **No concurrency safety in RepoGraph** — no locks, no append-only log, no
  CRDT; multi-writer safety is delegated to git-rebase of manifest YAML, which
  orders *commits*, not *causal events*.
- **Source signals are not durable or immutable** — session/lineage capture is
  GC'd (~44-day retention), so a *rebuild from source* yields a **different,
  smaller** graph than the *incremental* one. "Derived ⇒ can't drift" is false
  once source lifetime < projection lifetime.

---

## 2. The load-bearing correction: typed-steering vs display-only

The projection has **two consumers with opposite trust requirements**:

- **Humans / auditors** — read it to understand *why* the fleet did something.
  Tolerant of gaps; needs honesty about what's missing.
- **Lanes** — read prior causality to choose a next branch (the speed lever).
  **This is a control input** and inherits the full injection/integrity threat
  model.

**Rule (binding):** A lineage edge is admissible as a *steering* input to a lane
**only if** it is `code-computed ∧ chained ∧ durable ∧ causally-ordered`
(all four trust dimensions in §4 green). Everything else is **display-only** and
MUST NOT enter any lane's planning prompt.

Rationale — this is not theoretical. We already shipped exactly this discipline
once: D-INJ-4 / VERDICT B rebuilt the next-pass goal from **structured
failing-check IDs, not the LLM's free-text summary**, precisely to kill a
self-sustaining steering channel across passes
(`pr_review_watcher/main.py:2583`). Benefit (d) — "lanes read 'failed because
Y'" — *re-opens that exact channel at fleet scale* if Y is free text. The
typed-only rule is how we keep the speed benefit without undoing a hardening
that already exists.

Concrete attack this closes: an attacker-controllable issue body
(`dispatch.py:63` literally documents bodies as attacker-controllable) is
engineered so that, cycles later, it materializes as a lineage edge reading
"already tried the real fix, provably blocked." A lane reads it and skips the
correct branch or takes the attacker's preferred one. The §2 rule makes that
edge display-only (text-derived ⇒ not steerable), defusing it.

---

## 3. The real determinism-surface inventory (10, not 4)

| # | Surface | Status today | Gap |
|---|---|---|---|
| 1 | Capability ownership | **Mis-classified.** Async Custodian lint (`.custodian/config.yaml:210`), no runtime lookup; **no-ops in OC CI** (no PM sibling). | Post-hoc cleanup ≠ synchronous edge. |
| 2 | Code-computed verdict | **Holds.** `compute_verdict` (`pr_review_watcher/main.py:566`); model cannot author it. | The one solid edge. |
| 3 | Required gates | **Partial.** `reviewer-verdict` status is **self-issued by the merging process** (`main.py:1004-1018`) immediately before an unconditional REST merge; `required_checks` defaults `[]` (`settings.py:362`), enforced only by out-of-repo GH branch protection. | Determinism lives in un-versioned GitHub config. |
| 4 | Audit trail / lineage | **Observation, not a gate.** Records, does not stop. | Needs trust-labeling (§4) before steering. |
| 5 | **Task admission / author trust** | **OMITTED + ungoverned.** Admission = state + labels + ≥40-char goal; **no author allowlist** (`claim.py:111-153`). Any issue with the right labels is dispatched verbatim. | Fence (#386) stops prompt-hijack, *not* unauthorized work. |
| 6 | **Global work ceiling** | **OMITTED + referenced-but-nonexistent.** `settings.py:334` comment claims "global budget applies"; no global budget object exists. ~15 filers each self-dedup; no aggregate brake. | Systemic fault → unbounded self-filed task fan-out. |
| 7 | **Task-creation gate** | **OMITTED.** Lane output (`outcomes.py:560-619`) writes `STATE_READY` follow-ups — immediately admissible — bounded only by per-lineage retry≤3; scope-split spawns ≤6 *fresh-lineage* children that can re-split. | The interior writes its own admissible work — emergence escapes the edges. |
| 8 | **Egress / token containment** | **OMITTED + fail-open.** `maybe_sandbox`/`maybe_netns` return cmd unwrapped on any degrade (`sandbox.py:346-370`, `netns.py:93-126`); off-by-default. #386 made degrade *observable*, not *enforced*. | Token-holding backend runs unconfined by default. |
| 9 | **Lineage integrity / authorship** | **MISSING.** `cl.capture` content is unsigned, unchained; `derive_lineage_id` proves ID well-formedness, not writer ownership. | A compromised lane can write false lineage about *another* lane's chain. |
| 10 | **Controller-tier liveness** | **PARTIAL.** `HeartbeatStallTask` monitors worker/reviewer lanes but **not the maintenance loop that hosts it** (`heartbeat_stall.py:43`). | If spec_hygiene crash-loops, the detector dies with it. |

---

## 4. Per-edge trust labeling (the minimum for "observable")

Every lineage edge in the projection carries four orthogonal trust flags. The
projection is honest about its own untrustworthiness instead of manufacturing
false confidence.

```
edge.trust = {
  provenance:   code-computed | text-derived,
  integrity:    chained       | unverified,
  completeness: durable       | expired,        # vs source-GC retention
  order:        causal         | host-relative,  # logical clock vs commit order
}
```

- **Steerable** iff `code-computed ∧ chained ∧ durable ∧ causal`.
- Display surfaces render the other states explicitly (greyed / flagged), so a
  human never mistakes a GC-truncated, reordered, or injected edge for ground
  truth.

---

## 5. Build items

Phased. Each item names its acceptance test. **Section A is the only thing the
"speed" goal needs; B–D are the determinism edges the audit exposed.** A and B
are independent and can land in parallel.

### Phase A — Lineage as a *display-only* projection (low risk, ships the read-model)
- **A1. Lineage inventory + join** — read the §1.1 signals, join on
  `task_id`/PR#/sha into a single lineage view. *Accept:* a CLI/endpoint that,
  given a `task_id`, returns the task→proposal→PR→verdict→merge chain from
  existing files only. No new writers.
- **A2. Trust labeling (§4)** — stamp every edge with the four flags. *Accept:*
  text-derived/expired/host-relative edges are visibly distinguished; unit test
  asserts a free-text-sourced edge is never labeled `code-computed`.
- **A3. RepoGraph projection binding** — express the view as RUN/AUDIT/EVIDENCE
  nodes via the existing projection plane. *Accept:* projection is regenerable
  and never authored directly; `Source` marks it derived.
- **A4. Rebuild-equals-incremental conformance gate** — CI test that rebuilds
  the projection from source and diffs against the incremental graph over a
  corpus that **includes a GC'd range**. *Accept:* it fails today (proving the
  drift), then passes once A5 lands.
- **A5. Durable lineage tier** — persist steerable edges to a git-tracked tier
  with retention ≥ projection retention; mark anything beyond source retention
  `completeness: expired`. *Accept:* A4 passes; expired edges are display-only.

### Phase B — Close the omitted determinism edges (surfaces 5–8)
- **B1. Task admission author-trust (surface 5)** — add a creator/author
  allowlist to the admission path in `claim.py`; un-allowlisted authors require
  operator promotion before `STATE_READY`. *Accept:* an issue from a
  non-allowlisted author with valid labels is **not** dispatched; test proves it.
- **B2. Global work ceiling (surface 6)** — a single fleet-wide counter of open
  fleet-created tasks; filers consult it and refuse past a threshold. Replace
  the phantom "global budget applies" comment with a real object. *Accept:*
  simulate all filers firing in one cycle → total created ≤ ceiling.
- **B3. Task-creation gate (surface 7)** — bound *aggregate* follow-up/scope-split
  creation per lineage-root (not just per-lineage retry); cap recursive splits.
  *Accept:* a runaway scope-split fans out to ≤ N total descendants, not
  unbounded.
- **B4. Containment fail-closed option (surface 8)** — add
  `OC_SANDBOX_REQUIRED` / `OC_EGRESS_REQUIRED` that turn fail-open into
  fail-closed (refuse to dispatch a token-holding backend un-contained).
  Default stays degrade-never-halt per §0.1, but operators can opt into hard
  enforcement. *Accept:* with the flag set + bwrap absent, dispatch refuses and
  files a fault instead of running unconfined.

### Phase C — Fix the mis-classified edges (surfaces 1, 3)
- **C1. Make required-gate self-contained (surface 3)** — verify branch
  protection state from code before merge (don't rely on un-versioned GH
  config); refuse self-merge if `required_checks` is empty or `enforce_admins`
  off. *Accept:* a repo with no branch protection cannot be self-merged; test
  proves the guard is non-vacuous.
- **C2. Runtime capability-ownership check (surface 1)** — at the capability
  invocation point, resolve owner synchronously instead of trusting the async
  Custodian lint; fail-closed on ambiguous ownership. *Accept:* invoking a
  capability with ≠1 owner is rejected at runtime, not just flagged later.

### Phase D — Lineage integrity + controller liveness (surfaces 9, 10)
- **D1. Lineage authorship binding + hash chain (surface 9)** — a `cl.capture`
  may only mutate lineages the writing lane owns; per-lineage hash chain (mirror
  the verdict ledger's model). Cross-lineage writes quarantined. *Accept:* a
  lane writing another lane's `lineage_id` is rejected; tampered chain detected.
  **This is a hard prerequisite for any steerable edge.**
- **D2. Self-monitoring controller heartbeat (surface 10)** — an external check
  (watchdog, not spec_hygiene) monitors `heartbeat_spec_hygiene`; if the
  maintenance loop is live-but-not-succeeding, alert out-of-band. *Accept:*
  kill spec_hygiene's success path → fault raised by something that isn't
  spec_hygiene.

---

## 6. Sequencing & dependencies

```
A1 → A2 → A3 → A4 → A5        (display-only read-model; safe to ship alone)
                  └── D1 ──┐
                          ▼
   (steerable edges unlocked only after A5 + D1 + §2 typed-only rule)

B1, B2, B3, B4   parallel, independent of A
C1, C2           parallel, independent of A
D2               independent
```

**Do not enable lane-steering from lineage (benefit d) until A5 + D1 ship and
the §2 typed-only rule is enforced.** Until then, lineage is humans-only.

---

## 7. Explicitly NOT building (resist these)

- ❌ A greenfield `OperationalGraphRuntime` / canonical runtime-state authority —
  highest-entropy move; contradicts the operational-comprehensibility constraint
  and duplicates RepoGraph + the plane + heartbeats as a fourth source of truth.
- ❌ Converting lanes to a static execution DAG — that is the cage; the graph is
  footprints, lanes still choose the path.
- ❌ Adopting Osprey / Praetorian wholesale — the ecosystem is already broader;
  borrow the primitives (capability narrowing, explicit-but-adaptive graphs,
  deterministic edges around nondeterministic cognition), not the framework.

---

## 8. The one-sentence invariant

> **Emergent in decision-making; deterministic at every load-bearing edge;
> observable in an audit trail that is honest about its own trust state — and a
> lane may plan from that trail only where it is typed, signed, durable, and
> ordered.**
