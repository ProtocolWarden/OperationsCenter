---
status: open
---

# Risk-Tiered Approval Ladder

**Status: OPEN — specced adversarially, decision pending. Do NOT implement
unprompted.** One of four open-gap specs from the Osprey/Praetorian arc; see also
[Context Discipline](./CONTEXT_DISCIPLINE.md),
[Lineage Visualization](./LINEAGE_VISUALIZATION.md),
[Runtime Capability Enforcement](./RUNTIME_CAPABILITY_ENFORCEMENT.md), and the
master [Execution Lineage & Determinism Boundary](./EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md).

**Headline verdict:** WON'T-BUILD the general ladder — it is an abstraction with
one concrete instance on an operator-authored single-operator board, it
duplicates a **dead-but-existing** policy engine, and its "high→human-approve"
rung re-introduces the per-correction human the self-healing invariant forbids.
BUILD-minimal the single survivor instead: one code-computed sensitive-path
scope check.

## Current real state

The premise "identified-but-unbuilt" is **false for the abstraction, true only
for the wiring**. OC already contains a complete low/medium/high risk-tier engine
— it is just dead in the live fleet.

- **A full risk-tier policy engine exists but is unwired (verified).**
  `policy/engine.py` (22 KB) `PolicyEngine.evaluate()` runs ordered guardrail
  checks producing `ALLOW / ALLOW_WITH_WARNINGS / REQUIRE_REVIEW / BLOCK`.
  `policy/defaults.py` is literally a risk ladder (high→strict-validation +
  block-if-unavailable; medium→standard; low→no-block;
  `require_review_for_risk_levels=["high"]`) plus path-based blast-radius tiering
  (`.github/workflows/**`, `**/migrations/**`, `*.env`, `**/secrets/**` →
  review_required; `**/.ssh/**` → block). It is invoked only on the
  `execute`/`ExecutionCoordinator` plane.
- **The decisive finding: `risk_level` is hardcoded LOW in the entire live path,
  so the engine is a no-op tier-of-one (verified).** The live fleet runs
  `board_worker → dispatch.py → worker.main → execute.main`. `dispatch.py`'s
  `plan_cmd` passes goal/task-type/execution-mode/repo-key but **never
  `--risk-level`**; `worker/main.py:49` defaults `--risk-level` to `low`. No
  classifier, goal-heuristic, or label→risk mapping exists anywhere in the live
  path. Net: the coordinator evaluates **every live task as low**; the high/medium
  rungs are unreachable (exercised only by demo + unit tests).
- **What IS live and tiered (the real graduated handling):** the
  [Self-Heal Ladder](./SELF_HEAL_LADDER.md) (L0 standard fix → L1 enriched
  context → L2 decompose-per-concern → L3 rescope, bounded by
  `max_fix_strategy_level`) graduates *resolving effort*, not action-risk; the
  code-computed reviewer verdict (`pr_review_watcher/verdict.py` `compute_verdict`,
  fail-safe to CONCERNS, LGTM-only merge) is uniform-strict (correct); plus the
  self-merge / branch-protection gate, the admission allowlist, thin-goal block,
  OPEN_PR_GATE, and per-repo `max_daily_executions`.

So graduated **effort** is live; graduated **action-risk routing** exists as
built-but-dead code with no risk signal feeding it.

## Steelman

Not "build a new abstraction" — "you already paid for one, and a genuinely
high-blast-radius action rides the same uniform gate as a one-line typo fix."
A fleet PR that rewrites `.github/workflows/**` (the CI that gates every *other*
merge), or `**/migrations/**` (irreversible), or OC's own egress/sandbox
`config/`, gets exactly the same scrutiny as a docstring fix: one LGTM from the
same four generic checks (`spec_compliance`, `custodian_findings`,
`code_quality`, `no_tooling_artifacts`), none of which is blast-radius-aware.
`policy/defaults.py` already enumerates exactly these sensitive paths as
review-worthy — the policy plane "knows" they are special; the live plane that
merges them is blind to that knowledge.

## Adversarial round 1

- **Already-covered, on the axis that matters.** The live graduation is the
  Self-Heal Ladder (effort) + uniform-strict verdict (every PR clears the same
  bar). "Build a risk-tier ladder" is either re-deriving existing code or wiring
  up dead code — and the latter is a much smaller task than "spec a general
  system."
- **Operator-authored board → which tasks even have differentiated risk?** The
  human who knows the blast radius is the one writing the task and can already set
  per-task risk by **label** (the engine reads `"review_required" in labels`). A
  general *classifier* solves a problem the operator-authored board doesn't have.
- **The smuggled human gate (and it's worse than it looks).** A "high→human-
  approve" rung is exactly the per-correction human the self-healing invariant
  forbids. And the trap: the existing engine's `REQUIRE_REVIEW` does **not**
  implement a human handoff — `_policy_blocked_result` returns
  `status=SKIPPED, success=False`, i.e. it silently **drops the task**. So
  adopting the ladder as-is gives neither autonomy nor a review queue — it gives
  silent task-skips on the highest-value work, which self-heal then retries into
  the same wall. Both versions lose.
- **Abstraction without a second instance.** There is one plausible high-risk
  action class (sensitive-path diffs). A general classifier + router + per-tier
  table for one instance is abstraction-for-its-own-sake; the Self-Heal Ladder
  earned its generality, a risk-router has not.

## Adversarial round 2

Exactly one thing survives: **sensitive-path diffs merge through the same
blast-radius-blind gate as trivial diffs.** Attack the survivor — is the fix "add
a tier system" or "add ONE gate"? It is one gate, and it must obey "resolve the
concern, never bypass it":

- The ungated action is specific and enumerable — a diff touching the paths
  `policy/defaults.py` already lists. You don't need a risk *classifier*
  (goal-text inference, a new injection surface + a misclassify-high-as-low
  failure mode); you need a **deterministic diff-path check**, model-free and
  injection-proof — the same shape as the existing `no_tooling_artifacts` check.
- It fits the existing verdict mechanism with zero new abstraction: one more
  fail-closed condition in `compute_verdict`, not a router.
- Crucially it does **not** add a human-in-the-loop tier: "touches a sensitive
  path → require a stricter deterministic check passes, or an operator
  `risk-reviewed:` ack label set **once at task-authoring**" keeps the human at
  the encode-once anchored root. The fleet still self-heals to clear it; it just
  clears a scope-aware bar on those paths. No `REQUIRE_REVIEW`-skip, no approval
  queue.

## Minimal real delta

**Specific ungated high-risk action:** a fleet-authored PR whose diff modifies
blast-radius-controlling paths — `.github/workflows/**`, `**/migrations/**`,
`**/secrets/**` / `*.env`, and OC's own sandbox/egress `config/` — merges on the
same generic four-check LGTM as a docstring change.

**Smallest concrete change — one deterministic check, one file:** in
`pr_review_watcher/verdict.py`, add a **code-side** condition to `compute_verdict`:
if any changed file matches the sensitive-path globs **and** the PR/task carries
no operator `risk-reviewed:<area>` ack label, append a synthetic failing check
(e.g. sensitive-path-unacked) → forces CONCERNS. Reuse the glob set from
`policy/defaults.py` (import it; single source of truth). Same fail-closed,
code-computed shape as the existing verdict; uninfluenced by injection; LGTM-only
merge preserved; the only escape is the encode-once operator ack.

Do **not** wire the dead `policy/` + `ExecutionCoordinator` review-gate into the
live path for this — its `REQUIRE_REVIEW` silently SKIPs (drops), giving silent
task-loss, not stricter review. Wrong layer.

## Disposition

| Item | Disposition | Why |
|---|---|---|
| General risk-tier approval ladder | **WON'T-BUILD** | Abstraction with one instance; duplicates the dead `policy/` engine; "high→approve" re-introduces the forbidden per-correction human (and the existing tier silently SKIPs). |
| Sensitive-path diffs merge through a blast-radius-blind gate | **BUILD-minimal** | One code-computed, fail-closed sensitive-path scope check in `verdict.py`; adds scrutiny without a human gate or router. |
| The fully-built-but-dead `policy/` engine (fed `risk_level=low`; `REQUIRE_REVIEW` SKIPs) | **NEEDS-OPERATOR-DECISION** | Wire it (real signal + non-skip handler) or mark it dead; today it is neither a gate nor honest dead code. |

**Left open** pending the operator's bigger-picture decision on this arc.
