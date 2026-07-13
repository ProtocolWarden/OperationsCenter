---
status: spec
---

# Council verdict — keyless change control for guardrail surfaces

Status: SPEC (operator-directed, 2026-07-13)
Owner: loop supervision
Supersedes: nothing — coexists with Track C signing (`cl loop sign-config`),
which remains the stronger upgrade whenever the operator runs the ceremony.

## Operator decision

2026-07-13: the operator chose to stay in **loud-unsigned mode** (no ed25519
ceremony for now) and instead gate guardrail changes behind a **council of
agents**: batched change requests, multiple independent high-capability
reviewers with context and evidence, deliberation, recorded verdict.

This document pins what that buys, what it does not, and the mechanism.

## Threat framing — what the council covers vs. the signature

| Property | Loud-unsigned (today) | Council verdict (this spec) | Signed anchor (Track C) |
|---|---|---|---|
| Fleet quietly merges a guardrail change | possible (single reviewer LGTM) | **blocked** — cross-family unanimous quorum | blocked |
| Locally tampered config runs at launch | runs live, warns | **committed copy runs instead** (C2) | signed reference runs instead |
| Checker code itself patched locally | undetected | undetected (residual) | detected at deploy (signature fails) |
| GitHub account compromise | bypass | bypass (residual) | signature still refuses |
| Verdict verifiable later | n/a | audit-trail JSON (trust the log) | cryptographic, offline, forever |

The two residual rows are the honest gap. The council is *adjudicated change
control*; the key is *cryptographic tamper-proofing*. The operator can add the
key later without reworking anything here.

## Why cross-family, not seeds

Per-request seed control is not exposed by the claude/codex CLIs, and
same-model-different-seeds only decorrelates sampling noise — the members
would share training, failure modes, and any poisoned evidence identically.
Cross-family members (Claude sonnet, Claude opus, Codex gpt-5) plus distinct
review lenses give strictly stronger diversity than seeds would.

A council member is still an agent running on this infrastructure reading
attacker-influenceable text (the diff). Mitigations: members receive the diff
as data with injection-hardened prompts (INJ conventions), never execute PR
code, and unanimity means one resistant member blocks. This raises the bar;
it does not make the panel exogenous. See threat table.

## Mechanism

### G1 — guardrail path set

New reviewer setting `council.guardrail_paths` (repo-relative globs). A PR
whose diff touches any of these paths is a **guardrail PR** and takes the
council path instead of the single self-review:

```
.console/workers.yaml
.console/guidelines.md
tools/loop/oc_session_prompt.txt
.hooks/**
scripts/operations-center.sh
eval/**
src/operations_center/entrypoints/pr_review_watcher/**   # council guards its own code
src/operations_center/entrypoints/loop_bridge/**
config/operations_center.local.yaml                      # untracked, but pin anyway
docs/design/COUNCIL_VERDICT.md                           # this spec
```

Empty list ⇒ feature off (fail-open to current behavior, so the rollout PR
itself does not deadlock on the gate it introduces; the list is populated in
a follow-up PR that then rides through the council).

### C1 — council review in pr_review_watcher

For a guardrail PR:

1. **Panel**: K=3 members, one per backend family rung — claude/sonnet,
   claude/opus, codex/gpt-5 — reusing the existing worker-backend ladder and
   cooldown machinery. Each member gets an identical evidence bundle (diff,
   PR body, the guardrail file's role, last loop-cycle summary, heartbeats)
   and a distinct lens prompt: *correctness*, *security/capability-change*,
   *convergence/operational*.
2. **Verdict**: each member returns structured JSON
   `{"verdict": "LGTM"|"CONCERN", "reasons": [...]}` (same schema as the
   self-heal ladder's structured concerns).
3. **Quorum**: **unanimous LGTM** merges. Any CONCERN feeds the existing
   fix-strategy ladder (resolve the concern, never bypass it — the Self-Heal
   Ladder invariant applies unchanged); re-review after each fix round;
   exhaustion parks `needs_human`.
4. **Availability**: if a family is on cooldown (e.g. a claude session_5h
   limit), the council **waits** — guardrail PRs park like
   `reviewer_backend_unavailable` and auto-resume on the existing cooldown
   expiry path (#446). The gate fails CLOSED for guardrail changes; ordinary
   PRs are unaffected. Degrade-never-halt applies to the fleet's work, not to
   its own guardrail approvals.
5. **Record**: verdicts land in `state/council/<repo>-<pr>.json` and a PR
   comment summarizing all three members' reasons; the `reviewer-verdict`
   status context is set only on quorum.

### C2 — launch-time committed-truth check (ContextLifecycle)

Unsigned mode gains a keyless analogue of restore-by-consumption. At
`cl loop run` launch, when no signed reference exists:

- read the committed copy of the config from `origin/main`
  (`git show origin/main:<relpath>` after a bounded fetch; offline ⇒ skip
  with a loud note);
- live section == committed ⇒ run live (it IS the committed truth);
- drift ⇒ **run the committed copy** and flag loudly in the loop log and
  runtime state (`signed_status: "drift_unsigned"`); no YAML rewriting;
- new launcher flag `--require-committed` (parity with `--require-signed`,
  outside agent-reachable config): refuse to start on drift instead.

Signed reference present ⇒ Track C behavior wins unchanged.

### C3 — EVAL cross-family panel (separate work item)

The guide-gap audit flagged same-family generator↔evaluator as HIGH. Grading
panels get the same cross-family treatment as C1 (claude-generated work is
judged with codex on the panel and vice versa). Specced separately once C1's
panel plumbing exists to reuse.

## Rollout

1. **Phase 1 (CL)**: C2 committed-truth check + `--require-committed` +
   tests; OC/VF launchers pass the flag once verified.
2. **Phase 2 (OC)**: C1 council mode behind `council.guardrail_paths`
   (empty default), then a follow-up PR populating the path set — that PR is
   the council's first live case.
3. **Phase 3 (OC)**: C3 EVAL panel.

Each phase ships through the normal PR flow. Phase 2's reviewer changes are
themselves guardrail paths, so after the path set is populated, changes to
the council require the council.
