# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

Execute Work Order 0009 — Execution Hygiene.

See: `docs/architecture/adr/0009-work-order-execution-hygiene.md`

## Priority order

1. **P2 (immediate)** — Delete all STAGE_*.md / DERIVER_AUDIT_*.md / LOOP_START.md
   files from the repo root. Add gitignore guard. Commit directly to main.

2. **P1 + P5 (same root cause)** — Stop writing watchdog cycle summaries to
   `.console/log.md`. Redirect to `logs/local/watchdog_cycles/`. Watchdog commits
   only on meaningful actions, not every cycle.

3. **P3** — Add open-PR gate in board-worker pre-promotion check. Zero new goal
   promotions while any PR is open.

4. **P4** — Goal workers squash stage commits before opening a PR.

5. **P6** — Pin `ty` and `ruff` versions in pyproject.toml. Add preflight CI check
   to watchdog step 0.

## Definition of Done

- [ ] Repo root has no STAGE_*.md files; .gitignore blocks future ones
- [ ] `.console/log.md` no longer receives watchdog cycle dumps
- [ ] `git log` on main shows no `chore(watchdog): cycle N` commits after this point
- [ ] Board-worker refuses to promote goals when open PRs exist
- [ ] Goal workers produce ≤2 commits per goal before opening PR
- [ ] Tool versions pinned; CI matches local
