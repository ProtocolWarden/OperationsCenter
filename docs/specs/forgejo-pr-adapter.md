# Spec — Forgejo PR adapter (the reviewer surface)

**Status:** draft, not started
**Companion to:** [Forgejo board adapter](forgejo-board-adapter.md) (implemented)

Written adversarially, like the board spec. The board adapter landed cleanly
because its spec found the hazards before any code existed. This surface is
larger, and the honest conclusion is less comfortable: **one open question
decides whether the fleet can merge at all, and it is not an implementation
detail.**

## Objective

Give the reviewer a Forgejo equivalent of `adapters/github_pr.GitHubPRClient`,
so council review moves to Forgejo PRs at cutover — the operator decision
recorded in the board spec ("Forgejo PRs at cutover").

## Why this is not "the board adapter again"

|                                  | board adapter          | PR adapter                          |
|----------------------------------|------------------------|-------------------------------------|
| operations to port               | 14                     | **32**                              |
| depends on branch protection     | no                     | **yes — to merge at all**           |
| depends on CI check results      | no                     | **yes — gates every merge**         |
| blast radius when wrong          | a task sits in the wrong column | **the fleet merges nothing, or merges unsafely** |
| fleet-drivable share             | ~85%                   | ~60%                                |

The board adapter could be subtly wrong and the fleet would limp. This one sits
directly on the merge decision.

## Surface inventory

`src/operations_center/adapters/github_pr.py` — 643 lines, 32 public methods,
consumed by `pr_review_watcher`, `ci_monitor`, and `post_merge_regression`.
Endpoint families in use:

| family | endpoints | Forgejo status |
|---|---|---|
| PR CRUD | `/pulls`, `/pulls/{n}`, `/pulls/{n}/files`, `/pulls/{n}/merge` | direct equivalents |
| review | `/pulls/{n}/comments`, `/pulls/{n}/reviews` | equivalents, shapes differ |
| issue comments | `/issues/{n}/comments`, `/issues/comments/{id}` | direct equivalents |
| reactions | `/issues/{n}/reactions`, `/issues/comments/{id}/reactions` | equivalents |
| commit status | `/statuses/{sha}` | direct equivalent |
| **checks** | `/commits/{sha}/check-runs` | **does not exist** (B1) |
| **branch protection** | `/branches/{b}/protection` | different path *and* model (B2) |
| refs / contents | `/branches/{b}`, `/git/refs/heads/{b}`, `/contents/{p}` | equivalents |

---

## Adversarial

### B1 — Forgejo has no Checks API

OC reads `GET /repos/{o}/{r}/commits/{sha}/check-runs` — the GitHub **Checks**
API. Gitea and Forgejo implement **commit statuses** only. There is no
`check-runs` endpoint to port to.

Three helpers are built on it, and they are the merge gate:

- `get_failed_checks` — 7 production call sites, including the merge decision
- `get_incomplete_checks` — separates "still running" from "failed"
- `get_completed_checks`

Statuses carry most of the signal but not all. A check-run has **two** fields —
`status` (queued / in_progress / completed) and `conclusion` (success / failure /
neutral / cancelled / skipped / timed_out). A commit status has **one** `state`
(pending / success / failure / error). Collapsing two fields into one loses:

- `skipped` and `neutral` become indistinguishable from `success`
- `queued` and `in_progress` both become `pending`
- `cancelled` and `timed_out` both become `failure` (acceptable) — but the
  reviewer's retry logic currently treats them differently

The first loss is the dangerous one. **"The check was skipped" and "the check
passed" are not the same claim**, and the merge queue already distinguishes them.
A naive translation makes a check that never ran look like one that succeeded.

Second-order cost: roughly 100 assertions across `tests/` stub these helpers with
GitHub-shaped return values. Changing what they return is not a three-file edit —
it changes the fixture vocabulary the whole reviewer test suite is written in.

### B2 — `enforce_admins` has no Forgejo equivalent, and the gate fails closed

This is the finding that decides the project.

`_branch_protection_ok` (`pr_review_watcher/main.py:1234`) refuses to merge unless
it can prove **both**:

1. `reviewer-verdict` appears in `required_status_checks.contexts` (line 1269), and
2. `enforce_admins.enabled` is true (line 1274).

The reasoning is in the code: the check exists so the fleet's self-issued verdict
is not the only thing standing between an attacker-pushed head and `main`. The
fleet issues its own verdict; admin enforcement is what stops that verdict from
being self-certifying.

Forgejo's branch protection lives at
`/repos/{o}/{r}/branch_protections/{name}` with a different model.
`enable_status_check` + `status_check_contexts` map onto (1) cleanly.
**There is no `enforce_admins`.** Forgejo constrains pushes through allowlists
and `block_on_*` toggles; repository admins are not bounded the same way.

Three options, all with costs:

- **(a) Report `enforce_admins: false`.** Honest. The gate fails closed. **The
  fleet can never self-merge on Forgejo.** Every PR waits for a human.
- **(b) Report `true` because protection exists.** The fleet merges on a claim
  the adapter cannot substantiate. This removes the control *and hides that it
  removed it* — strictly worse than deleting the check outright, because the
  logs still say it passed.
- **(c) Reconstruct the property from Forgejo primitives.** `enforce_admins` was
  a proxy for *"the fleet's verdict alone is insufficient"*. On Forgejo the
  nearest real guarantee is an empty push allowlist on the protected branch
  (nobody, admins included, pushes directly) plus required status contexts. Map
  to that and report precisely what it does and does not cover.

**(c) is the only defensible answer**, and it is not a translation — it is a
redesign of a security control against different primitives. That makes it an
operator decision, and it belongs before any code.

### B3 — Fail-closed means there is no soft landing

`require_branch_protection` defaults to `True` (`config/settings.py:417`), and an
unverifiable protection state refuses the merge. That behaviour is correct and
was observed working during the 2026-08-17 GitHub outage: the reviewer declined
to merge when it could not read protection.

The consequence for this migration: **a partially-correct adapter does not
degrade, it halts.** There is no intermediate state where the fleet limps along
merging the easy PRs. Either protection maps faithfully or nothing merges. Plan
the rollout accordingly — this cannot be shipped half-done and iterated on in
production.

### B4 — The required `audit` status is produced by GitHub Actions

Branch protection requires `audit`, and that status is posted by a GitHub Actions
workflow. Move the forge and the producer disappears. Forgejo Actions is
workflow-compatible, so this is portable — but it is a second migration riding on
the first, and the required-context name must match **exactly**, or every PR
blocks forever on a status nobody will ever post.

This is the same coupling that has blocked "replace GitHub Actions with local
CI", arriving from the other direction. Worth noting that solving it here also
unblocks that.

### B5 — Issues and PRs share one number space

In Gitea/Forgejo, issues and pull requests draw from the **same** per-repo
counter. The board spec put every task in one board repo; PRs live in the code
repos, so nothing collides today.

But it forecloses an option: the board repo can never also host PRs, or task #42
and PR #42 become the same number in different contexts. State it now, before
someone proposes consolidating further.

### B6 — Diff retrieval is GitHub-shaped

`get_pr_diff` requests `Accept: application/vnd.github.v3.diff` (line 484) and
`_pr_diff_too_large_summary` (line 498) handles GitHub's truncation behaviour. Forgejo
serves diffs at a different path with different limits.

The reviewer feeds these diffs to council members under a token budget. A diff
that arrives truncated differently — or silently complete where it used to be
summarised — **changes what three reviewers see, and therefore what they
approve**, with no error anywhere. Verify against real large PRs, not fixtures.

### B7 — There is no `PRClient` protocol yet

The board migration worked because `BoardClient` existed as a Protocol and a
ratchet test drove concrete-client imports from 37 to 2. The PR side has no
protocol at all.

The good news, on inspection: the reviewer already funnels construction through
two small factories (`main.py:383-400`) and names `GitHubPRClient` in only four
places. The import-site ratchet is trivial compared with the board's 37 — nothing
like the same sprawl.

The real work is declaring the protocol over a 32-method surface and making the
reviewer's *call sites* type-check against it. That is mechanical and
fleet-drivable, and worth doing regardless of whether cutover happens: it is what
makes the decision reversible.

### B8 — Self-drive assessment

| step | fleet can drive? |
|---|---|
| extract `PRClient` protocol, migrate reviewer onto it | **yes** |
| implement the 29 mechanical operations | **yes** |
| write status→check translation + loss tests | **yes** |
| decide the B2 security-control mapping | **no — operator** |
| verify protection semantics against a live instance | **no — needs the instance** |
| migrate `audit` to Forgejo Actions | **partly** — needs a live runner |

~60% drivable, and the non-drivable part is a security decision at the *front*,
not a verification step at the end. That inverts the board adapter's shape, where
the operator questions could be answered in a sentence each and the rest was
mechanical.

---

## Live findings — Forgejo 13, 2026-08-18

Probed against the running instance (scratch repo `bp-probe`, kept for
negative-case verification). Three findings revise the adversarial sections:

1. **B2 mostly dissolves: `apply_to_admins` exists.** The branch-protection
   rule carries a first-class `apply_to_admins` field — GitHub's
   `enforce_admins` under Forgejo's name: the rule binds repository admins
   too. With `enable_status_check` + `status_check_contexts` (which accepted
   `["audit", "reviewer-verdict"]` verbatim), both paths the fail-closed gate
   reads map 1:1 and truthfully. What remains of B2 is a cutover checklist
   item, not a design question: **set `apply_to_admins: true` when protecting
   the Forgejo repos.** Option (c)'s push-allowlist reconstruction is not
   needed.
2. **The status model, precisely.** A status carries its value in a field
   named `status` (not `state`), and there are **five** values:
   `pending / success / failure / error / warning` — `warning` has no GitHub
   equivalent. The adapter's translation (`STATUS_TO_CHECK`): `pending` →
   in_progress, `success` → success, `failure` and `error` → failure (the
   original word survives in `output.title`), `warning` → **neutral** —
   completed, not passed, not failed, which is Forgejo's own semantics. The
   statuses endpoint returns full posting *history*; ids increase
   monotonically, so the GitHub-style latest-per-context dedupe carries over.
3. **`check-runs` is a confirmed 404**, and `GET /pulls/{index}.diff` serves
   the raw diff.

## B4 resolved — `audit` on Forgejo Actions, 2026-08-19

Actions enabled on the live instance, `forgejo-runner` 6.3.1 registered, and a
probe workflow run end to end. B4 said the `audit` status "must be reproduced
under an identical context name, or every PR blocks forever". **The identical
name is not achievable**, and the reason matters more than the inconvenience.

### The context format is not the job name

GitHub uses the job id, so OC's required context is the bare string `audit`.
Forgejo composes it:

```
<workflow name:> / <run-name> (<event>)
```

With no `run-name:`, the middle segment is **the commit message**. The probe's
first run produced:

```
audit.yml / ci: probe the status context name (push)
```

A required status check must be a fixed string. A context containing the commit
message changes on every push, so under the default configuration branch
protection can never be satisfied — not "blocks until you rename it", but
unsatisfiable in principle.

### Correction: the middle segment is the JOB, not `run-name`

An earlier revision of this section claimed `run-name:` pins the middle
segment. **That was wrong**, and generalised from a failure case.

A two-job workflow with `run-name: probe` produces two contexts, and `probe`
appears in neither:

```
multi / alpha (pull_request)
multi / beta  (pull_request)
```

A job carrying `name: Pretty Job Name` produces:

```
naming / Pretty Job Name (pull_request)
```

So the format is:

```
<workflow name:> / <job name:, or the job id when absent> (<event>)
```

It is **stable by default** — no `run-name:` needed, and each job gets its own
context exactly as on GitHub, just prefixed by the workflow name.

The commit-message form in the first probe
(`audit.yml / ci: probe the status context name (push)`) appears only when a run
fails *before any job starts*: with no job to name, Forgejo falls back to the
file name and the run title. Reading a fallback as the rule is what produced the
wrong conclusion.

### Both events fire on a PR head

`push` and `pull_request` each produce their own context, so the GitHub
workflow's dual trigger yields **two** runs and two contexts per PR head:

```
custodian-audit / audit (pull_request)  -> success
custodian-audit / audit (push)          -> pending
```

Two consequences: the work is done twice, and a gate that requires "nothing
incomplete" sees the slower run as outstanding. The Forgejo workflow should
trigger on `pull_request` only.

### Cutover configuration

* workflow: `name: custodian-audit`, `on: pull_request` (no `run-name:` needed)
* branch protection `status_check_contexts`: `custodian-audit / audit (pull_request)`
* `apply_to_admins: true` (per the earlier live finding)

The required-context string is therefore **Forgejo's format, not a copy of
GitHub's**. Any cutover checklist that says "use the same name" is wrong.

### Live validation of the adapter

The same probe confirmed `ForgejoPRClient` reads real status data correctly —
not fixtures:

```
failed:     []
incomplete: ['custodian-audit / audit (push)']
completed:  ['custodian-audit / audit (pull_request)']
```

The statuses endpoint returned the full posting history (pending, pending,
success) for one job, and the latest-per-context dedupe resolved it as designed.

## Correctness criteria

1. Status→check translation is explicit about what it loses. A test asserts a
   **skipped check is distinguishable from a successful one** — this is the
   single most likely silent-wrong outcome.
2. `get_incomplete_checks` still separates "running" from "failed". Collapsing
   them makes the queue merge mid-CI, which is exactly the defect that let #503
   through on GitHub.
3. `get_branch_protection` returns something the self-merge gate can evaluate
   **without asserting a guarantee Forgejo does not make.** Whatever B2 resolves
   to is documented at the call site, not just in the adapter.
4. Pagination reads to exhaustion on every list endpoint — same class of silent
   truncation the board adapter hit.
5. Every method the reviewer calls exists. A missing one is an `AttributeError`
   mid-merge, discovered in production.
6. The negative path is tested: a wrongly-protected branch must cause a **refusal
   to merge**, not a merge.

## Completion criteria

- [x] **B2 decided** — `apply_to_admins` IS `enforce_admins` (live finding 1); the operator flips it on at cutover
- [x] `PRClient` protocol extracted; all 17 callers migrated (#512–#515)
- [x] `ForgejoPRClient` implementing the protocol (`adapters/forgejo/pr_client.py`)
- [x] Status→check translation with the losses tested (`STATUS_TO_CHECK`; warning→neutral, error→failure with its own name)
- [x] `audit` produced on Forgejo — under Forgejo's context format, which *cannot* be identical (see B4 resolved)
- [x] Live verification against a real instance, negative cases first (#517, and the Actions probe above)
- [ ] Cutover, GitHub demoted to read-only mirror

## Phasing

1. **Operator decides B2.** Nothing downstream is worth building first — it
   determines whether the gate can ever pass.
2. Extract `PRClient`, migrate the reviewer. Mechanical, fleet-drivable, and
   valuable whether or not cutover proceeds.
3. Implement the 29 mechanical operations against fakes.
4. Status→check translation with explicit loss tests.
5. `audit` on Forgejo Actions.
6. Live verification, negative cases first.
7. Cutover.

## Recommendation

**Do not start step 3 before step 1.** The board adapter landed cleanly because
its three questions were answered first. Here the open question is a security
control that decides whether the fleet can merge at all — building the client
first risks discovering at integration that the gate can never pass, with 29
methods of sunk work behind it.

There is also a sequencing alternative worth weighing honestly: **do step 2 now,
then stop.** Extracting the seam makes the reviewer forge-agnostic and is useful
on its own merits. Keep review on GitHub through board cutover, and move it only
once B2 is settled and Forgejo Actions is producing `audit`. That defers the
"one review surface" goal, but it trades a clean principle for a reversible
migration — and it is the board move, already done, that actually removes Plane.

## Open questions — for the operator

1. **B2:** what property replaces `enforce_admins`? (Recommended: option (c),
   empty push allowlist on the protected branch, documented explicitly.)
2. Does review move at board cutover, or later once `audit` runs on Forgejo?
3. Is a period of GitHub-review + Forgejo-board acceptable, or must they move
   together?
