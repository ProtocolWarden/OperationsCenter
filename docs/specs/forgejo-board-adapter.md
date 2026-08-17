# Spec — Forgejo board adapter

**Status:** draft, not started
**Author:** written adversarially — the goal is to find where this breaks before
building it, not to argue it will work.

## Objective

Replace Plane with self-hosted Forgejo as the fleet's board, behind the
`BoardClient` seam introduced in #503/#504, so that swapping backends is a change
to `make_board_client()` and one new adapter rather than a change to every
caller.

Forgejo is the chosen forge: it provides issues *and* pull requests, so it
replaces Plane and GitHub together. Git mirrors to GitHub per push (cheap DR,
public visibility); `forgejo dump` on a schedule for the data a git mirror does
not cover.

## What already exists

- `adapters/board` — `BoardClient` protocol (14 operations) and one factory.
- 13 of 37 callers migrated; **24 still import `PlaneClient` directly**, held by a
  ratchet in `tests/unit/adapters/test_board_seam.py`.
- `PlaneClient` satisfies the protocol structurally.

## The mapping, and where it is dishonest

| OC concept | Plane | Forgejo | Honest? |
|---|---|---|---|
| task id | UUID string | per-repo integer `index` | **no — see A3** |
| state | first-class, exclusive | *does not exist* | **no — see A1** |
| labels | label objects | label objects | yes |
| description | `description_stripped` + `_html` | `body` (markdown) | mostly |
| comments | comment objects | comment objects | yes |
| priority | field | *does not exist* | **no — see A2** |
| project | project id | repo `{owner}/{repo}` | yes |

---

## Adversarial: where this breaks

### A1 — States are exclusive. Labels are a set. This is the whole problem.

OC uses six states and treats them as mutually exclusive: `Ready for AI` (42
call sites), `Backlog` (17), `Done` (8), `Cancelled` (4), `Blocked` (3),
`Awaiting Input` (3). Plane enforced exclusivity *structurally* — an issue has
one state field.

Forgejo has `open`/`closed` and labels. Labels are an unordered set. **Nothing
prevents an issue carrying `state: Blocked` and `state: Ready for AI`
simultaneously.**

Every rule in `board_unblock.py` assumes exactly one state. A double-labelled
issue would satisfy two rules at once and could be transitioned twice in a single
cycle, or counted in two buckets by the queue-health scans.

This is not a theoretical risk: the adapter itself will create it. `transition_issue`
must remove the old state label and add the new one — two API calls, not one.
**If the process dies between them, the issue has zero or two state labels.**
Plane's single-field write was atomic; this is not.

Mitigations, none free:
- (a) Adapter-enforced invariant: `transition_issue` removes-then-adds and
  `to_board_task` treats "more than one state label" as a hard error rather than
  picking one. Fails loudly instead of behaving randomly.
- (b) A reconciliation task that finds and repairs multi-state issues each cycle
  (the fleet already has `board_unblock` as precedent).
- (c) Use `open`/`closed` for the terminal states (`Done`, `Cancelled`) and labels
  only for the active ones, reducing but not removing the window.

**Recommended: (a) + (b).** (a) alone converts silent corruption into a visible
failure; (b) repairs it. Neither makes the write atomic, and the spec should not
pretend otherwise.

### A2 — Priority has no home

`set_priority` was added in #504 precisely because callers were reaching around
the adapter for it. Forgejo has no priority field, so it becomes a label
(`priority: high`) with the same exclusivity problem as A1, at lower stakes —
priority only feeds rescoring, not dispatch.

### A3 — Task IDs change type, and they are persisted everywhere

Plane task ids are UUIDs. Forgejo issue ids are integers scoped **per repo**, so
`#42` in two repos are different tasks. OC's task ids are currently global.

Those ids are already written into places a cutover cannot rewrite:
- branch names (`goal/010834c9` — a Plane UUID prefix)
- PR bodies and titles
- labels (`original-task-id:`)
- `.console/` files and `logs/local/` state sidecars
- the `dc10_baseline` / audit ratchets that name paths, not ids (safe)

**A big-bang cutover breaks every in-flight reference.** Options: drain to zero
before cutover (simplest, costs a quiet period), or carry `plane-id: <uuid>` as a
label on migrated issues (keeps history greppable, adds a label the fleet must
ignore).

### A4 — Pagination will silently truncate the board

`list_issues()` means "the whole board". Forgejo paginates at 20 by default and
caps `limit`. A naive port returns page one and **looks successful**.

This is the most dangerous failure in the list, because the fleet's rules reason
about absence: `board_unblock` promotes when a queue looks empty,
`detect_convergence_stall` fires when nothing is progressing. A truncated board
does not error — it produces confident wrong decisions.

The adapter must paginate to exhaustion, and the test suite must include a
multi-page fixture. A single-page fixture would pass while the bug ships.

### A5 — Description round-trip is lossy

`to_board_task` reads `description_stripped` and `description_html`;
`_render_text_html` writes both. Forgejo has one `body` field in markdown.
Writing is fine (drop the HTML). Reading is fine. But any code that expects the
*stripped* variant to differ from the HTML one will silently get the same string.
Grep for `description_html` before assuming this is free.

### A6 — Comment parsing is load-bearing

`detect_convergence_stall` and the reviewer parse comment bodies. Forgejo comment
objects differ in shape from Plane's. The bot marker convention
(`<!-- operations-center:bot -->`) survives, but field names do not.

### A7 — The fleet cannot do this alone

Self-drive assessment, honestly: **the fleet can write and test the adapter; it
cannot complete the migration.**

| Step | Fleet can do it | Why not |
|---|---|---|
| Write `ForgejoClient` | **yes** | pure code, testable against a fake transport |
| Test against fixtures | **yes** | no server needed |
| Migrate the 24 remaining callers | **yes** | mechanical, ratchet-guarded |
| Stand up Forgejo | **no** | host, DNS, TLS, disk |
| Create the API token | **no** | credential the fleet must not mint for itself |
| Decide cutover timing | **no** | operator judgement |
| Verify against a live instance | **no** | needs the instance |

A spec that claims full autonomy here would be wrong, and the fleet would burn
its self-heal ladder discovering that.

---

## Correctness criteria (testable, no live server)

1. `ForgejoClient` satisfies `BoardClient` — the existing seam test covers this
   the moment the class exists.
2. **Pagination**: given a 3-page fixture, `list_issues()` returns every issue.
   A single-page fixture is not sufficient evidence.
3. **State exclusivity**: `transition_issue` results in exactly one state label;
   `to_board_task` raises on an issue carrying two.
4. **Round-trip**: `create_issue` → `fetch_issue` → `to_board_task` preserves
   title, description, labels, and state.
5. **State vocabulary**: all six states map, and an unknown state name fails
   loudly rather than defaulting.
6. **Auth**: `Authorization: token …`, not Plane's `X-API-Key`.
7. Every existing `BoardClient` test passes against both adapters — the protocol
   is the contract, so the suite should be parameterised over implementations.

## Completion criteria

Done means all of:

- [ ] `ForgejoClient` implements all 14 operations, with the pagination and
      exclusivity tests above.
- [ ] `make_board_client()` selects the backend from settings; Plane remains
      selectable until cutover.
- [ ] The seam ratchet reaches **0** — all 24 remaining callers migrated.
- [ ] A live smoke test against a real instance (operator-run).
- [ ] Cutover decision recorded: drain-to-zero or `plane-id` labels (A3).
- [ ] Mirror configured: per-push git to GitHub, scheduled `forgejo dump`.
- [ ] GitHub demoted to read-only. **Two writable forges will diverge**, and the
      divergence will be discovered by a fleet acting on stale state.

Explicitly *not* done when the adapter merely exists and passes unit tests. That
is step one of seven.

## Phasing

1. **`ForgejoClient` + tests** — no server, no risk, fully fleet-drivable.
2. **Backend selection in the factory** — Plane stays default.
3. **Finish the ratchet** (24 files) — mechanical, independent of Forgejo.
4. **Operator: stand up Forgejo, mint a token.**
5. **Smoke against live, on a throwaway repo.**
6. **Cutover** (A3 decision), mirrors configured.
7. **Local CI** — currently blocked on GitHub Actions producing the required
   `audit` status; moving to Forgejo Actions changes that constraint, so this
   belongs after cutover, not before.

## Open questions for the operator

- Drain-to-zero, or migrate in-flight tasks with `plane-id` labels?
- One repo per project (matching today's repos), or a single board repo? Forgejo
  issue ids are per-repo, which makes the second option meaningfully simpler.
- Does the council review flow move to Forgejo PRs at cutover, or stay on GitHub
  until local CI exists? Splitting them means two review surfaces at once.
