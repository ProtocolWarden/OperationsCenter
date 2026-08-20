# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

Get OperationsCenter fully self-hosted on Forgejo — board, PRs, and CI — with
no dependency on GitHub or Plane for anything the fleet needs to operate, and
leave the deployment reproducible on a different machine.

GitHub remains a **mirror only**: a place to read the history, not a place
anything gates on.

## Overall Plan

1. Extract a `PRClient` seam so the forge is a swappable backend. ✅
2. Build and verify `ForgejoPRClient` against the live instance. ✅
3. Stand up Forgejo + a registered runner; port the workflows to `.forgejo/`. ✅
4. Flip `board_backend` and `pr_backend` to `forgejo`; restart the fleet. ✅
5. Prove it end to end on a real PR: fleet reviews it, `audit` posts, it merges.
6. Delete `.github/workflows/`, push GitHub main as a mirror.
7. Leave the deployment reproducible from the repo alone. ✅

## Definition of Done

1. `board_backend: forgejo` and `pr_backend: forgejo` live, fleet running. ✅
2. CI runs on Forgejo Actions from `.forgejo/workflows/`, `.github/workflows/`
   deleted, branch protection requiring `custodian-audit / audit
   (pull_request)` + `reviewer-verdict` with `apply_to_admins`. ✅
3. A real PR opened, reviewed by the fleet, and merged end-to-end on Forgejo.
4. GitHub main updated to mirror Forgejo.
5. The deployment reproducible from the repo alone on a new machine. ✅

## Current Stage

**Landing the CI cutover.** Two PRs open on the Forgejo instance:

* **PR #2** `e2e/forgejo-cutover-proof` — the CI cutover, plus the four bugs
  that surfaced once the gates ran on slower hardware than GitHub's runners.
* **PR #3** `chore/rotate-console-log` — log rotation. **Cannot go green until
  #2 merges**: it is failing on exactly the bugs #2 fixes, and inherits them on
  rebase. Merge order is not a preference.

Remaining: merge #2, rebase and merge #3, push the mirror (which requires
dropping GitHub's required status checks — `audit` can never post there again,
so pushes to a protected `main` would be rejected; operator approved
2026-08-19, keeping force-push and deletion protection).

## Constraints that are easy to get wrong

* **Never hand-post `reviewer-verdict`.** It is a required context; posting it
  manually defeats the gate it exists to be.
* `capacity: 1` on the runner is deliberate — jobs share the host network
  namespace and would collide on fixed ports.
* `FORGEJO__server__ROOT_URL` and `container.network: host` are a pair. Change
  one without the other and every checkout dies at `git exit 128`.
* This box has **4 cores**. Do not run stress tests beside live CI; it starves
  the runner and manufactures failures that look like findings.
