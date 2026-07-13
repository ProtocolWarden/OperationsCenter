---
status: design
---

# Harness Trust-Hardening — Adversarially-Hardened Completion Spec

Status: **DESIGN / adversarially verified** · Owner: operator · Date: 2026-06-18

This spec closes the three primitives the harness audit found missing against the
runtime-harness reference model: **injection defense (INJ)**, **process/runtime
isolation (SBX)**, and **agent-quality evaluation (EVAL)**. The orchestration and
governance harness is otherwise complete; these three are the *trust* axis — OC
currently trusts its inputs, its runtime, and its own quality, all unverified.

Each design below was drafted, then attacked by an independent adversarial
reviewer assuming a competent attacker who has read this exact document. The
designs recorded here are the **post-attack** versions. Original draft layers that
did not survive attack are retained only as outer defense-in-depth, never as the
load-bearing control. Decision verdicts cite the attack each option survives or
fails.

---

## 0. Unifying thesis

All three red-teams converged on one finding:

> **Structural capability-reduction beats probabilistic detection/measurement.**
> Detection (injection signatures), perimeter (network deny), and measurement
> (accuracy metrics) are each a filter the adversary tunes against. The control
> that holds against an attacker who has read the design is the one that *removes
> the capability being abused* — typed verdicts computed by code, minimized
> ambient authority, human-adjudicated ground truth behind operator-only approval.

Consequences that ripple through every phase:

- **INJ:** the merge-affecting verdict must not be free text the model authors —
  it is a typed schema the model fills and **code** computes the verdict from.
  Injection can at most flip a re-checkable field; it cannot author "merge me."
- **SBX:** the goal is not "detect a bad agent" but "make a successful injection
  *non-catastrophic*" — minimized env, no ambient secrets, non-executing
  patch-applier, confined filesystem.
- **EVAL:** auto-derived outcome data is *downstream-contaminated*; it is a
  *flagger* of disagreements for human adjudication, never a printed
  precision/recall *metric*. And the eval must sit outside the write surface of
  the fleet it grades.

The three compose: SBX is what makes an INJ bypass survivable; the INJ adversarial
cases seed the EVAL corpus; the EVAL gate is what catches a reviewer regression
that re-opens the INJ verdict hole. None is complete alone.

---

## 0.1 Binding invariant — self-healing

> **The system must always be able to judge and correct itself, with no human in
> the per-correction loop.** No control in this spec may create a state where the
> autonomous fleet cannot continue to review / fix / merge without a human
> unblocking it. The operator may encode a judgment **once** (offline, anchored);
> the system must reconfirm and correct **forever** after.

(While the operator's signing ceremony is deferred, guardrail-surface changes are
gated by cross-family council review instead — see
[COUNCIL_VERDICT.md](COUNCIL_VERDICT.md) for the mechanism and its honest residual
gaps vs. the signature.)

This is a hard constraint on every capability, not a feature of EVAL alone. It has
three operational tests every design here must pass:

1. **No hard halt — degrade, never stop.** Every protective control has a
   degraded-but-alive fallback. Security controls may fail *closed*, but only if
   the closed window is bounded to seconds by automatic recovery; the fleet itself
   never loses a runnable path. (INJ already obeys this: detection *degrades to a
   stricter deterministic path*, it does not fail-closed-to-human — which would be
   a one-line remote fleet-halt.)
2. **No bootstrap deadlock — the healer's path is never gated by the thing it
   heals.** A change that breaks the sandbox/proxy/eval must remain mergeable by a
   path that does not depend on the broken component. The controller tier (outside
   the sandbox) and the always-available local lane are the bootstrap floor.
3. **No human in the correction loop.** Human anchoring is permitted only at the
   irreducible root (encode-once, offline, signed) and that root must be
   *provably disjoint* from every routine correction. Reconfirmation, drift
   detection, regression fixes, and freshness all self-heal automatically.

The invariant *reframes* the decisions that the prior draft had marked "operator
sign-off": the question is not "which option does the operator prefer" but "which
option keeps the system able to correct itself." Resolved under that test in §1.

---

## 1. Resolved decisions (adversarially verified under the self-healing invariant)

The three forks the prior draft deferred to operator discretion were attacked
specifically against the self-healing invariant and are now **resolved**. The
operator's role collapses to a single irreducible touchpoint (D-OP-3: signing a
new ground-truth case, once, offline) that sits *outside* every correction loop.

**D-OP-1 — sandboxed cloud backends vs ollama-local-only. RESOLVED: HYBRID
(local-as-floor, cloud-as-preference).** Pure local silently degrades reviewer/fix
quality and can relocate #334-style non-convergence into a capability ceiling;
pure cloud makes the key-proxy a halt. Resolution: ollama-local is the
always-available bootstrap floor (the bwrap launcher **fails open to local**, which
needs no proxy); cloud-via-proxy is a *preference* gated on an
in-sandbox→proxy→cloud **liveness probe** (extends `worker_backend_probe`). A dead
proxy presents as a `worker_backend` **cooldown** so `select_worker_backend`
auto-routes to local — never "no runnable backend." The proxy runs as its own
`systemd --user` unit (`Restart=on-failure`, ordered before `oc-fleet.service`);
the machine restarts it, not a human. Cloud-unavailability emits a status signal
and the #335 escalation budget bounds the local-only fix loop, so degradation is
observable and convergent, never silent.

**D-OP-2 — network isolation mechanism. RESOLVED: B+ (L7/SNI allowlist proxy,
fail-CLOSED but made non-load-bearing).** A bare egress proxy is a single
un-supervised process in the self-heal critical path with a silent-rot failure
mode and a bootstrap deadlock. Resolution: keep the L7/SNI allowlist (security),
but (a) run it as `oc-egress-proxy.service`, `systemd --user`, `Restart=always`,
linger on, ordered before `oc-fleet.service` — so "proxy down" self-recovers in
seconds without touching the model endpoint; (b) an **active synthetic probe**
through the proxy to the model endpoint + github.com runs at **controller tier
(outside the sandbox)** and classifies *DENY-on-allowlisted-destination* as rot vs
policy, auto-opening an Observer/Custodian fix task; (c) the allowlist is a
capability-plane-owned node so drift has an accountable owner; (d) the proxy's own
repair executes at controller tier on the **unsandboxed host egress path** — the
healer of the network path is never itself behind that path.

**D-OP-3 — does EVAL become a blocking gate, and how is it guarded. RESOLVED:
ACCEPT, superseding the prior "operator-only CODEOWNERS forever."** That prior
answer *violated* the self-healing invariant: a human in every eval correction.
Resolution splits eval trust into an irreducible **human-anchored answer key** and
a fully **self-healing body** (full design in §4.2). The operator signs a new
ground-truth case **once, offline**; everything checkable against that key
self-heals forever with no human in the correction loop. The fleet may *append
candidate cases* and *auto-fix its own graded behavior*, but can never edit/delete
a signed label or lower the **monotonic baseline floor** — enforced structurally
by a tiny operator-anchored *constitution* (3 files that change ~never) that is
provably disjoint from the per-correction loop.

All capability-level decisions below (D-INJ-*, D-SBX-*, D-EVAL-*) also carry an
adversarial verdict and are resolved.

---

## 2. Capability INJ — injection defense

### 2.1 Surface (grounded)

Untrusted external text enters an LLM goal at:

- `entrypoints/board_worker/dispatch.py:63,123` — issue title+body → `goal_text` →
  `python -m worker.main --goal <goal_text>`.
- `entrypoints/board_worker/spec_author.py:440,452,74` — issue YAML payload →
  spec-author goal.
- `entrypoints/pr_review_watcher/main.py:2098,2132,2148-2172,2188` — PR title +
  raw diff + campaign spec + **custodian findings** → review `goal_text` →
  `_run_direct_review`.
- `pr_review_watcher/main.py:2583` — **circular**: the LLM verdict `summary`
  becomes the goal for the next fix pass.
- `pr_review_watcher/main.py:2509` — LLM `summary` reflected back to GitHub as a
  comment (injection-to-output).

The merge action is already structurally gated by two required checks (`audit`,
`reviewer-verdict`). Forging "LGTM" *text* still needs CI+audit green; the live
risk is **suppressing a CONCERNS**, steering a fix-pass/worker to exfiltrate or
write malicious code, and **second-order injection through trusted channels**.

### 2.2 Hardened design

**Load-bearing (capability-reduction):**

1. **Typed verdict (root fix).** `_run_direct_review` returns a typed schema —
   `{check_id, status: pass|fail, evidence_span}` per enumerated review check —
   not a free-text verdict. The LGTM/CONCERNS decision is **computed by code**
   from the schema, outside the model. Injection can flip a field (each
   independently re-checkable) but cannot author the merge decision.
2. **Typed circular hand-off.** The fix-pass goal is built from the **structured
   failing-check IDs**, not the `summary` text. A typed channel cannot transport
   "you are now in maintenance mode."
3. **Fenced/typed findings.** Custodian findings enter the goal as
   `{detector_id, count}`, **never raw `samples`** (which are attacker-authored
   repo content laundered through a *trusted* channel — the single strongest
   attack found). Re-derive needed context *inside* the untrusted fence, do not
   trust the sample string.
4. **Output sanitization.** The `summary` reflected to GitHub is escaped/typed so
   it cannot `@`-ping, embed steering markdown, or feed the next reader/pass.

**Outer defense-in-depth (kept, never load-bearing):**

5. **Nonce-fenced envelope.** Every untrusted span wrapped in a per-run
   *randomized* sentinel (`<<UNTRUSTED:{nonce}>> … <</UNTRUSTED:{nonce}>>`) with a
   system preamble. Static sentinels are trivially closed by the attacker; the
   nonce is the only version worth shipping. Still defeated by instruction-via-
   data — hence it is outer-only.
6. **Custodian `INJ1` detector.** Deterministic scan of ingested text for
   injection signatures (imperative directives, role markers, zero-width/homoglyph
   chars, split-across-hunk fragments, encoded blobs). Mirrors the boundary-detector
   shape (`detect_inj1(context) -> DetectorResult`; `build_injection_detectors()`).
   On hit → **degrade to the stricter deterministic path** (refuse-LGTM, more
   required checks), **never fail-closed-to-human** (which is a one-line remote
   fleet-halt: paste the signature list into any PR).

### 2.3 Decisions

- **D-INJ-1: verdict channel — free-text (A) vs typed schema → code-computed (B).**
  **VERDICT B.** Free text lets the model author the gated decision, so every
  injection contends directly for the merge; B removes the authority from the
  text. Survives paraphrase/semantic injection, which detection cannot.
- **D-INJ-2: detection policy — fail-closed-to-human (A) vs degrade-to-stricter,
  no halt (B).** **VERDICT B** (given B above). A is a remote kill switch.
- **D-INJ-3: custodian `samples` into goals — raw (A) vs `{detector_id,count}`
  (B).** **VERDICT B.** Raw samples bypass the untrusted fence via a trusted
  channel — the biggest blind spot.
- **D-INJ-4: circular hand-off — summary text (A) vs structured check IDs (B).**
  **VERDICT B.** A is a self-sustaining steering channel across passes.
- **D-INJ-5: worker capability — ambient env + detection (A) vs least-capability
  sandbox (B).** **VERDICT B.** Determines the *blast radius* of any bypass; this
  is the SBX capability and the reason INJ and SBX ship interleaved.

### 2.4 Acceptance criteria

- A corpus injection case that previously could suppress CONCERNS cannot flip the
  **code-computed** verdict (proven by replay, §4).
- No raw custodian `samples` string reaches any LLM goal (grep + test).
- Fix-pass goals contain no free-text verdict summary (test on the dispatch path).
- An `INJ1` hit forces the conservative path with **zero** human escalations
  generated (DoS-resistance test: 50 signature-laden PRs → 0 halts).

---

## 3. Capability SBX — runtime isolation

### 3.1 Surface (grounded)

Zero process isolation today. `_subprocess.py:28-31` passes `env=dict(os.environ)`
— the worker inherits **every** secret (GitHub token, Plane token, all per-repo
tokens, host secrets). `policy/defaults.py:124` declares `network_mode="allowed"`
but it is **not OS-enforced**. The agent runs shell (`shell=True` bootstrap),
pushes anywhere (`git/client.py:216` force-with-lease), reads `.git/config`
embedded creds, writes the whole `/tmp` workspace. The `.ssh/.gnupg`/`.env` path
policy (`policy/defaults.py:41-101`) is checked **after** execution — advisory,
not a control. "sandbox" in-repo = `sandbox_base_branch` (a git branch), not
isolation.

### 3.2 Hardened design

**Layer 0 — env + credential minimization (lands first, no container):**

- Replace `dict(os.environ)` with an **allowlist** env: only the *one* target
  repo's token (if needed in-sandbox), model creds, pinned `PATH`/`PYTHONPATH`
  (fixed values, not inherited — PATH/PYTHONPATH inheritance re-leaks).
- **Strip the token from `.git/config` and reflog** — clone via a credential
  helper that never persists the token, or rewrite to a tokenless remote before
  handing the workspace to the worker. Env-minimization alone misses this (the
  red-team's #1 "Layer 0 still leaks" finding).

**Pre-push enforcement (lands with Layer 0, no container) — the patch-not-push
pivot's required guard:**

- The worker **emits a patch**; the trusted host-side `WorkspaceManager`
  applies+pushes. Moving push out of the sandbox is only safe if the parent is a
  **strict, non-executing, path-allowlisting applier**:
  1. `git apply --check` then apply **confined to the workspace path**; reject any
     diff touching `..`, absolute paths, or symlinks. **Never `--unsafe-paths`.**
  2. **Path allowlist** — deny or human-gate `.github/`, `.git/`, `*hooks*`,
     `.husky/`, `package.json` scripts, `setup.py`/`pyproject` build hooks,
     `conftest.py`, `Makefile`, CI configs, lockfile-only changes. This is the
     **enforced promotion of the dead advisory policy.** `.github/workflows`
     poisoning is the highest-value pivot (sandbox → CI → real repo secrets).
  3. **Parent never executes the patched code** (no install/test/format on the
     host post-apply — `conftest.py`/`postinstall`/Makefile = host RCE). Quality
     re-runs go in a *second* sandbox.
  4. Push to a non-default branch → PR (existing gate) only after 1–3 pass.

**Layer 1 — bwrap filesystem confinement:**

- `bwrap` (rootless, no daemon) with: rw-bind workspace only; ro-bind a
  **host-pre-built** toolchain/venv; tmpfs `/tmp` and a seeded tmpfs `$HOME` per
  backend; **no bind** of `~/.ssh ~/.gnupg ~/.config ~/.aws gh-auth`.
- **`--unshare-pid` + fresh `/proc` are mandatory** — without them the sandbox
  reads `/proc/<parent>/environ` (the parent still holds full `os.environ`),
  defeating Layer 0 entirely.
- In-sandbox bootstrap is forbidden (toolchain is ro); any backend that
  pip/npx-installs at runtime must be pre-provisioned (inventory first).

**Layer 2 — network (Phase 3, D-OP-2 = B+):**

- `--share-net` + an **L7/SNI-aware egress proxy** allowing only the model
  endpoint + github.com; DNS pinned to a resolver answering only allowlisted
  names (UDP/53 otherwise blocked — DNS tunnels exfil). Honest framing: the two
  sanctioned channels (github push, model endpoint) **are** exfil paths; Layer 2
  *raises cost*, it does not *close* exfil.
- **STRUCTURAL EGRESS — B1 IMPLEMENTED (2026-06-22, opt-in).** The audit found the
  `--share-net` proxy is *honor-system* (an agent can `unset HTTPS_PROXY` / raw-socket
  out); the cheap kernel fix (`systemd-run --user -p IPAddressDeny`) was empirically
  shown NOT to enforce under a rootless `--user` manager. `board_worker/netns.py`
  (`maybe_netns`, gated `OC_EGRESS_NETNS=1`, fail-open) now closes it rootless: **pasta**
  runs the executor in a netns that maps host loopback in (proxy/ollama reachable at the
  same `127.0.0.1:port` via `-T`), an in-netns **iptables OUTPUT DROP** (allow only `lo`
  + established) kernel-blocks all other egress, and **caps are dropped** before exec so
  the agent can't flush it. Validated end-to-end: proxy reachable, raw internet socket
  `ENETUNREACH`, firewall un-flushable. Reverses the D-SBX-2 "`--unshare-net` can't reach
  host loopback" objection (pasta's loopback map solves it). Needs `passt` installed.
- **Non-load-bearing under the self-healing invariant (§0.1):** the proxy runs as
  `oc-egress-proxy.service` (`systemd --user`, `Restart=always`, linger on,
  ordered before `oc-fleet.service`) so its death self-recovers in seconds without
  touching the model endpoint. An **active synthetic probe at controller tier**
  (outside the sandbox) asserts allowlisted-destination success and classifies
  *DENY-on-allowlisted* as rot vs policy → auto-opens a fix task. The allowlist is
  a capability-plane-owned node. The proxy's own repair runs at controller tier on
  the unsandboxed host egress path — no bootstrap deadlock.

**Layer 3 — resource limits:** rlimits/cgroup CPU + mem + pids + wall-timeout.

**Cloud-key containment (Phase 3, D-OP-1 = HYBRID):** a localhost key-injecting
proxy so the cloud model key never enters the sandbox env. **The fleet never halts
on it:** the bwrap launcher *fails open to the ollama-local floor* (no proxy
needed); cloud-via-proxy is a preference gated on an in-sandbox→proxy→cloud
liveness probe; a dead/missing proxy presents as a `worker_backend` cooldown so
`select_worker_backend` auto-routes to local. ollama-local is the only config
where "secrets removed" is fully true and is the always-available bootstrap floor.

### 3.3 Decisions

- **D-SBX-1: cloud model key — in-sandbox (A) vs key-proxy required, cloud gated
  (B).** **VERDICT B** (= OP-1). A leaves the crown-jewel key reachable by a
  compromised model.
- **D-SBX-2: net — `--unshare-net`+forwarder (A) vs `--share-net`+L7 proxy (B).**
  **VERDICT B** (= OP-2). `--unshare-net` cannot reach host ollama / localhost
  proxy without a forwarder anyway; an L7 proxy at least logs+constrains the
  deliberate-exfil case that dominates.
- **D-SBX-3: patch application — non-executing path-allowlist applier (A) vs
  parent re-runs tests/format for quality (B).** **VERDICT A, hard.** The parent
  must not execute patched code on the trusted host.
- **D-SBX-4: CI/build-config changes in a diff — auto-apply (A) vs mandatory
  human review (B).** **VERDICT B.** `.github/workflows` poison is the top pivot.
- **D-SBX-5: ship order — Layer 0 alone (A) vs Layer 0 + enforced pre-push gate
  together (B).** **VERDICT B.** Layer 0 alone (push still in-sandbox, `.git`
  creds + model key reachable) is reduced-blast-radius, not containment.

### 3.4 Acceptance criteria

- Worker env contains **no** Plane token, no other-repo tokens, no host secrets
  (env-diff test); `.git/config` carries no token post-clone.
- A test patch touching `.github/workflows/ci.yml` is **blocked** pre-push.
- Inside bwrap: `cat /proc/<parent-pid>/environ` fails; `~/.ssh` unreadable;
  rlimits enforced; existing local backends still run green.
- (Phase 3) A novel-domain egress attempt is blocked+logged; a cloud backend runs
  with the API key **absent** from the sandbox env.

---

## 4. Capability EVAL — agent-quality evaluation

### 4.1 Surface (grounded)

Nothing measures agent quality. All reviewer unit tests
(`tests/test_pr_review_watcher.py`) **mock** the verdict — they test the state
machine, not verdict quality. Reusable signals exist but measure *infrastructure /
target-repo CI*, not the agents: `post_merge_regression.py` (CI fails on main
after a merge), `reviewer/instrumentation.py` (decision outcome+latency, **not**
correctness), insights derivers (execution/proposal outcomes), Observer signals.
Recent reviewer bugs an eval should have caught pre-merge: **#313** (verdict
bypass, merged broken), **#334** (non-convergence loop), **#337** (doc over-flag).

### 4.2 Hardened design

**Component 1 — golden corpus + replay (human-adjudicated).** ~15–30
*operator-adjudicated* cases, each a self-contained `(diff + context snapshot +
committed counterfactual verdict + written rationale)` fixture. Source: the
divergent cases where outcome and correct-verdict disagree (the ones that *break*
auto-labeling) + the INJ adversarial cases + #313/#334/#337 scenarios. Labels
encoded **once** via the operator ledger `[check: …]` idiom, reconfirmed forever.

**Component 2 — outcome correlation as a FLAGGER, not a metric.** Auto-correlation
(LGTM→main-regressed-in-N-h? CONCERNS→requeued-to-death?) emits **disagreement
tickets** for operator adjudication. It must **never** print a precision/recall
number — outcome data is downstream-contaminated (CI flakiness, sibling PRs, infra;
requeue-to-death conflates reviewer over-flag #337 with *worker* non-convergence
#334). A precision/recall derived from it is confidently wrong in exactly the
LGTM-happy-regression failure mode (#313) you most need to catch — and its naive
over-flag penalty creates a gradient *toward* that failure.

**Component 3 — CI gate (Phase 4, see OP-3).** When a PR touches
`pr_review_watcher/main.py` or `board_worker/dispatch.py`:
- **Blocking gate:** exact-match of the **code-computed verdict** (§2.2) against
  committed corpus verdicts, using a **pinned/cached model artifact** for
  determinism. Low-temp ≠ deterministic; a flaky gate becomes `continue-on-error`
  wallpaper.
- **Drift monitor (non-blocking):** real-model N-of-M replay, separate. N-of-M
  voting smooths away onset-of-regression variance, so it must not gate.

**The meta-guard, self-healing-compatible (load-bearing — D-OP-3).** The agent
under evaluation writes and reviews the PRs that modify its own eval, so the eval
must resist self-dealing *without* a human in the per-correction loop (§0.1). The
resolution is to split eval trust into two disjoint objects:

- **The answer key (human-anchored, encode-once).** Each *graded* corpus case is
  `(input, ground-truth verdict, operator signature)`, stored in an **append-only,
  hash-chained ledger** (the `operator-interventions-ledger` `[check: ref]`
  idiom). Editing or deleting a signed entry breaks the hash chain → required
  check red. The fleet **may append *unsigned candidate* cases** (scored, but not
  counted toward the gate until an operator signs them once). It can grow the exam;
  it can never rewrite the answer key.
- **The self-healing body (fully automated, no human).** Corpus freshness, drift
  detection, `[check:]` reconfirmation, the replay harness, scoring code, and
  regression **auto-fix pointed *only* at worker/reviewer behavior** — all
  self-heal. Auto-fix is *structurally forbidden* from mutating signed labels or
  the baseline floor (enforced by the constitution below, not by good behavior).

**The constitution (the irreducible root — 3 files, operator-anchored, change
~never).** The signature-verification workflow, the required-checks /
branch-protection config, and the **monotonic baseline floor** (the bar may only
*rise* automatically, never fall). These are the only human-gated surface and they
are *provably disjoint* from every routine correction — corpus growth, drift fixes,
and reconfirmation all happen below this line, automatically. The signing key is
operator-held and offline; its **only** use is converting an appended candidate
into a graded case. It is never in any correction, reconfirmation, or fix path.

**Independence of the judge.** Replay scoring runs in an **out-of-band critic lane
on a different model family** than the implementer (N copies of one model is N=1 —
a same-weights clone shares blindspots and collusion is structural). The critic
only ever *reads* signed labels. Tamper-evidence (hash/sig mismatch) is wired to a
**required, non-bypassable** check that also auto-opens an operator-review task —
the alarm has teeth because its consequence lives in the constitution the fleet
cannot disarm.

### 4.3 Decisions

- **D-EVAL-1: outcome correlation — metric (A) vs flagger (B).** **VERDICT B.**
  Downstream-contaminated; valid as an anomaly detector, invalid as a grader.
- **D-EVAL-2: corpus/gate location — in-repo fleet-editable (A) vs operator-only
  CODEOWNERS + tamper alarm (B).** **VERDICT B.** Non-negotiable precondition for
  blocking.
- **D-EVAL-3: graduation report-only→blocking — time-based (A) vs numeric
  precondition (B).** **VERDICT B** (= OP-3). Report-only is ignored by default;
  graduation must be earned by a demonstrated catch.
- **D-EVAL-4: over-flag penalty — requeue-to-death = reviewer over-flag (A) vs
  attribute by agent: requeue-to-death → *worker* non-convergence eval (B).**
  **VERDICT B.** A charges the reviewer for the worker's failure → LGTM-happy
  gradient.
- **D-EVAL-5: blocking gate engine — real-model N-of-M (A) vs pinned-artifact
  exact-match, N-of-M as separate drift monitor (B).** **VERDICT B.**

### 4.4 Acceptance criteria

- Corpus of ≥15 operator-adjudicated cases committed behind CODEOWNERS; a
  fleet-authored diff to a corpus file trips the Custodian tamper alarm.
- Component 2 produces **tickets**, emits **no** precision/recall number anywhere.
- A seeded reviewer regression (re-introduce the #313 retraction) is caught by the
  shadow gate before merge.
- Blocking turns on only when the §1/OP-3 numeric precondition is met.

---

## 5. Phased roadmap to completion

Sequenced by **leverage × dependency**: cheapest blast-radius cuts first, the INJ
root fix next (it needs the typed verdict before EVAL can score one), containment,
then network + cloud-key, then the eval guard last (it grades a now-stable
reviewer). Each phase is a few PRs through the existing required gates; each has an
exit gate that must pass before the next begins.

### Phase 0 — Foundations (no containers, no model change) · highest leverage/cost
- SBX Layer 0: allowlist env; strip `.git/config`/reflog token. *(D-SBX-1 partial,
  D-SBX-5)*
- SBX pre-push: promote advisory path policy → **enforced** non-executing
  path-allowlisting applier. *(D-SBX-3, D-SBX-4)*
- INJ outer: nonce-fenced untrusted envelope at all ingestion points. *(D-INJ-1
  outer layer)*
- EVAL bootstrap: stand up the **append-only hash-chained signed corpus** + the
  3-file **constitution** (signature-verify workflow, branch-protection config,
  monotonic baseline floor); seed the first operator-signed ground-truth cases;
  wire the tamper-evidence required check. *(D-EVAL-2, D-OP-3)*
- **Exit gate:** env-diff shows minimized env; a poisoned-`.github` test patch is
  blocked pre-push; the signed corpus + constitution are committed; editing a
  signed label breaks the hash chain and reds the tamper check; appending an
  *unsigned candidate* case is allowed and does not.

### Phase 1 — INJ structural verdict & hand-offs (the root fix)
- Typed verdict schema; code-computed LGTM/CONCERNS. *(D-INJ-1)*
- Typed circular hand-off (failing-check IDs, not summary). *(D-INJ-4)*
- `{detector_id,count}` findings, no raw samples; output sanitization. *(D-INJ-3)*
- Custodian `INJ1` detector → degrade-to-stricter (no human halt). *(D-INJ-2)*
- **Exit gate:** corpus injection case cannot flip the code-computed verdict;
  50 signature-laden PRs → 0 halts; no raw sample reaches any goal.

### Phase 2 — SBX process containment
- bwrap Layer 1 (`--unshare-pid` + fresh `/proc`, no `~/.ssh|.gnupg|.config`
  bind, host-pre-built ro toolchain, seeded tmpfs HOME per backend).
- Layer 3 rlimits/cgroup.
- **Exit gate:** `/proc/<parent>/environ` unreadable in-sandbox; `~/.ssh`
  unreadable; limits enforced; local backends green.

### Phase 3 — SBX network + cloud-key containment *(D-OP-1, D-OP-2)*
- **WIRED (2026-06-20):** the bwrap launcher now injects `HTTPS_PROXY`/`NO_PROXY`
  into the sandbox env, gated on `OC_EGRESS_PROXY` and **fail-open** on an
  unreachable proxy (via `board_worker/sandbox.py:maybe_sandbox`);
  localhost (ollama floor + key-proxy) always bypasses. Egress + key proxies and
  the supervised `oc-egress-proxy.service` were merged in #352/#353; the service
  ExecStart was corrected to the venv python (system python3 cannot import OC).
  Remaining: enable-and-observe (start the service + set `OC_EGRESS_PROXY`) and
  the controller-tier synthetic probe.
- **PROBE WIRED (2026-06-21, #367):** `EgressProbeTask` runs at controller tier
  in `register_maintenance_tasks`, probing an allowlisted host (tunnels) + a
  denied host (refused) each cycle; classifies allowlisted-DENIED as *rot* and
  denied-ALLOWED as *breach*, auto-opening a deduplicated board fix task;
  fail-open (`skipped`) when the proxy is unreachable or unconfigured. *(D-OP-2)*
- **DNS pinning — SATISFIED-BY-EQUIVALENT (2026-06-21):** under `--share-net`
  (D-SBX-2) the sandbox shares the host resolver, so a dedicated pinned resolver
  is not the binding control. The L7/**SNI allowlist at the proxy** is: all
  sandbox egress is forced through `HTTPS_PROXY`, and the proxy re-validates the
  TLS SNI host against the allowlist **regardless of what DNS resolves to** — a
  poisoned/attacker-chosen A-record cannot reach a non-allowlisted name because
  the CONNECT host is checked, not the IP. UDP/53 DNS-tunnel exfil is the residual
  (named in Layer 2's honest framing); closing it fully needs `--unshare-net`,
  rejected by D-SBX-2 as a bootstrap-deadlock/over-cost trade. Pinning therefore
  reduces to "best-effort, subsumed by SNI enforcement" — no separate resolver
  shipped. *(D-SBX-2)*
- Layer 2 `--share-net` + L7/SNI egress proxy; DNS pinned. *(D-SBX-2)*
- **Supervise the proxy** as `oc-egress-proxy.service` (`Restart=always`, linger,
  ordered before `oc-fleet.service`); controller-tier synthetic probe →
  rot-vs-policy classification → auto-fix task. *(D-OP-2)*
- localhost key-injecting proxy; cloud-via-proxy gated on the liveness probe;
  bwrap launcher **fails open to ollama-local**; dead proxy → `worker_backend`
  cooldown → auto-route to local. *(D-OP-1, D-SBX-1)*
- **Cloud-key proxy — N/A / SATISFIED-BY-EQUIVALENT (2026-06-21):** the live
  cloud auth is a **subscription token, not an API key** (`~/.claude/.credentials.json`),
  so there is no standalone key to strip into a separate injecting proxy. It is
  contained by the same two mechanisms the key-proxy would have provided:
  (1) **ro-bind only `.credentials.json`/`settings.json`** into a tmpfs `~/.claude`
  (the credential is never writable, never copied into the workspace, and the rest
  of the 1.8 GB `~/.claude` is excluded); (2) the **egress allowlist** confines
  where that token can be used to the model endpoint + github. A compromised model
  can *use* the session it is already running under but cannot exfiltrate the token
  to a novel destination. The D-OP-1 HYBRID fail-open-to-ollama-local floor still
  holds for the no-cloud case. *(D-OP-1, D-SBX-1)*
- **Exit gate:** novel-domain egress blocked+logged; cloud backend runs with key
  absent from sandbox env; killing the proxy degrades the fleet to local
  (still reviewing/fixing/merging) and `oc-egress-proxy.service` self-restarts —
  **no halt, no human**.

### Phase 4 — EVAL as a self-healing guard *(D-OP-3)*
- **SCAFFOLDING DONE (2026-06-21, #369 + #370):** the load-bearing machinery is
  merged and live in `src/operations_center/eval/` + repo-root `eval/`, exercised
  by 41 unit tests and a required CI check (`EVAL corpus integrity`). What shipped:
  - `corpus.py` — append-only **hash-chained** case ledger (`entry_hash =
    sha256(prev_hash ‖ canonical(payload))`); editing/deleting any past entry
    breaks the chain → required check red (tamper-evidence). *(D-EVAL-2)*
  - `signing.py` + `sign.py` — **Ed25519** operator answer-key signatures; a case
    is *graded* only if its signature verifies against the constitution pubkey.
    Asymmetric so no compute inside the trust boundary can forge a label; the
    private key is operator-held, offline (`sign keygen`/`sign sign` CLI). *(D-OP-3)*
  - `replay.py` — Component 1/3 **deterministic blocking gate**: replays the typed
    checks through the pure code-computed verdict (`pr_review_watcher.verdict`,
    no model → zero flakiness) and exact-matches the signed answer. Only graded
    cases gate; candidates are reported only. *(D-EVAL-5)*
  - `critic.py` — non-blocking out-of-band **different-model-family** N-of-M drift
    monitor (the model extractor is an injected seam). *(D-EVAL-5)*
  - `constitution.py` + `verify.py` + `.github/workflows/eval-corpus-integrity.yml`
    — monotonic baseline floor (may only rise) + report-only→blocking graduation
    (D-EVAL-3); the required check ties chain + signatures + floor together.
    CODEOWNERS pins corpus + constitution + workflow to the operator. *(D-EVAL-2)*
  - Seeded **7 unsigned candidate cases** (#313 verdict-bypass/injection + #337
    over-flag classes); all pass replay; gate correctly report-only (0/15 signed).
  Verified end-to-end with a throwaway ephemeral key: report-only → sign → gate
  graduates to **blocking**, and an in-place edit of a signed answer is caught.
- **PRODUCTION SEAMS WIRED (2026-06-21, #372 + #374):** the Component 2 flagger
  (D-EVAL-1, D-EVAL-4 attribution) is registered and runs on a real source —
  `eval/outcome_sources.py:GitHubOutcomeSource` turns post-merge regressions on
  merged PRs into LGTM-miss tickets (a merged PR necessarily passed
  `reviewer-verdict`=LGTM), opt-in via `OC_EVAL_OUTCOME_SOURCE=github`, fail-safe
  to skipped. The drift-monitor model adapter is built —
  `eval/check_extractors.py:BackendCheckExtractor` (prompts a different-family
  backend with the verdict schema, parses `checks`); it awaits an *extraction-kind*
  corpus layer (diff→checks cases) + a configured non-implementer backend to run
  live.
- **REMAINING (deferred, gated on the operator):** the one encode-once human step
  — generate the Ed25519 key offline, commit the pubkey, sign ≥`min_graded_cases`
  seed cases — which flips the gate from report-only to blocking.
- Self-healing body (once seeded): drift detection, `[check:]` reconfirmation,
  corpus growth via *unsigned candidate* append, regression auto-fix on
  worker/reviewer behavior only. *(D-OP-3)*
- Graduate report-only→blocking on the numeric precondition. *(D-EVAL-3)*
- **Exit gate:** a seeded reviewer regression (re-introduce the #313 retraction)
  is caught in shadow pre-merge; the fleet **auto-fixes the regression with no
  human**, yet a fleet PR that edits a signed label or lowers the baseline floor
  is structurally blocked (hash-chain / constitution check red).

### Completion definition

Done when: (1) the worker env carries no ambient secrets and runs under bwrap with
an enforced pre-push applier; (2) the merge-affecting verdict is code-computed from
a typed schema with no untrusted free-text authority; (3) a reviewer regression is
caught by a self-healing, self-deal-proof eval gate before it can merge; and
(4) **the self-healing invariant (§0.1) holds end-to-end** — killing the proxy,
tripping the injection detector, or regressing the reviewer each degrades-and-
auto-recovers with **no human in the correction loop**, while the only human
touchpoint (signing a new ground-truth case) stays provably outside it. At that
point the harness is complete on the trust axis, not only the orchestration axis.

---

## Appendix — adversarial provenance

Each capability design was attacked by an independent reviewer instructed to break
it assuming knowledge of this document. The attacks that reshaped the design:
INJ — instruction-via-data defeats any fence; unfenced custodian `samples` are a
trusted-channel injection; fail-closed is a remote fleet-halt. SBX — `--unshare-pid`
omission re-leaks the parent env; `.git/config` creds survive env-minimization;
patch-not-push is a confused-deputy upgrade unless the applier is non-executing and
path-allowlisting; the two allowed network channels are themselves exfil paths.
EVAL — outcome data is downstream-contaminated and anti-correlated with truth in
the LGTM-happy failure mode; the over-flag penalty gradients toward that failure;
the fleet can edit its own exam. All recorded verdicts reflect the option that
survived attack.

A second adversarial pass attacked the three deferred decisions specifically
against the **self-healing invariant** (§0.1, §1): it found that "local-only" is a
hidden quality-halt and the key-proxy a SPOF (→ D-OP-1 HYBRID, fail-open-to-local
floor); that a bare egress proxy is an un-supervised SPOF with silent-rot and a
bootstrap deadlock (→ D-OP-2 B+, systemd-supervised, controller-tier probe); and
that the prior "operator-only CODEOWNERS forever" *was itself a self-healing
violation* — a human in every correction loop (→ D-OP-3, split into a tiny
human-anchored signed answer-key + a fully self-healing body). Each resolution was
re-attacked for its residual hole (same-weights critic collusion; in-band anchor
edits; tamper-evidence-without-enforcement; auto-fix pointed at its own scorecard)
and hardened: different-model-family critic, append-only hash-chain, required
non-bypassable constitution check, and auto-fix structurally forbidden from
touching signed labels or the baseline floor.

## See also

The trust-axis work above hardened the *reviewer* path. A follow-on adversarial
audit (2026-06-22) extended the lens to the *worker* axis and the running fleet,
and the resulting design — execution lineage as a trust-labeled read-model plus
the corrected ten-surface determinism boundary — is specified in
[Execution Lineage & the Determinism Boundary](./EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md).
