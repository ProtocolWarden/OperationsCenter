---
status: active
---
# Work Order 0009 — Execution Hygiene

_Operator-authored. Addresses accumulated execution quality problems observed in
production operation. Priority order top-to-bottom._

---

## P1 — Stop polluting `.console/` truth files

**Problem:** `.console/log.md` is 11,000+ lines. Every watchdog cycle dumps its
full report directly into `log.md`, `task.md`, and `backlog.md`. These files are
the operator truth surface — not a running log. The result:

- Every PR rebase generates merge conflicts in `.console/` (the single biggest
  source of rebase pain today)
- `log.md` has become unreadable and unsearchable
- `task.md` accumulates stale goal descriptions that no longer reflect reality

**Required changes:**

1. Watchdog cycle summaries must go to a dedicated rotating file (e.g.
   `logs/local/watchdog_cycles/YYYYMMDD_cycleN.md`) — NOT into `log.md`.
2. `log.md` entries must be operator-authored or milestone-authored only:
   significant decisions, stop points, architectural changes. One entry per
   meaningful event, not one per cycle.
3. `task.md` must reflect the current live objective only. When a goal completes
   it gets archived to `log.md` (one line), not accumulated in task.md.
4. `backlog.md` must not be written by the watchdog at all — it is operator-curated.
5. Gate: if a watchdog session touches `.console/log.md` or `.console/task.md`
   without a meaningful event (new PR opened, goal completed, blocker hit), that
   is a policy violation.

---

## P2 — Delete STAGE_N.md scratchpad files, never commit them

**Problem:** 16+ `STAGE_N.md` / `DERIVER_AUDIT_STAGE*.md` files are committed to
the repo root. These are goal-execution scratchpad artifacts — working notes that
have no value after the goal closes. They are cluttering the repo and confusing
future readers about what is canonical.

**Required changes:**

1. All existing `STAGE_*.md`, `DERIVER_AUDIT_*.md`, `LOOP_START.md` files in the
   repo root must be deleted and the deletion committed.
2. Goal workers must write scratchpad artifacts to `tmp/` or a path covered by
   `.gitignore`, never to tracked locations.
3. Add `STAGE_*.md`, `*_AUDIT_*.md` patterns to `.gitignore` as a hard guard.

---

## P3 — One open PR at a time (merge-before-promote)

**Problem:** The controller opened 4 PRs simultaneously, all against the same base,
all conflicting. This multiplied the operator rescue effort by 4x today.

**Required changes:**

1. Before promoting any goal task from Backlog → Ready for AI, check open PR count.
   If `open_prs >= 1`, do not promote. Block with `OPEN_PR_GATE` label.
2. When a PR merges, the gate lifts automatically and the next backlog item can promote.
3. This is enforced in the board-worker's pre-promotion check, not just in the
   watchdog prompt.

---

## P4 — Single clean commit per goal (no stage-by-stage spam)

**Problem:** Goal workers produce 5–8 commits per goal: "Stage 0", "Stage 1",
"Stage 2 complete", "Stage 3 verification", etc. This pollutes `git log` and makes
rebase chains unnecessarily long.

**Required changes:**

1. Goal workers must squash all stage commits before opening a PR. One implementation
   commit + one test commit is acceptable. Eight stage narrative commits is not.
2. The PR description can describe the stages — the git log should not.

---

## P5 — Watchdog must not commit `.console/` changes on every cycle

**Problem:** Each watchdog cycle commits to whatever branch it is on, writing the
cycle summary to `.console/log.md`. This means:
- Every branch accumulates `.console/` commits that conflict with main
- `git log` is full of `chore(watchdog): cycle N` entries
- The watchdog pollutes branches it should be observing, not modifying

**Required changes:**

1. Watchdog cycle summaries are written to `logs/local/watchdog_cycles/` (from P1)
   and are NOT committed.
2. The only time a watchdog session commits is when it takes a meaningful action
   (deploys a fix, opens/closes a PR, promotes a task). Observation-only cycles
   produce zero commits.
3. The `chore(watchdog): cycle N` commit pattern is retired.

---

## P6 — Handle CI tool version drift proactively

**Problem:** `ty` updated from 0.0.37 → 0.0.40 in CI, surfacing new errors the
controller didn't introduce but couldn't fix. All 4 open PRs were blocked until
operator intervention.

**Required changes:**

1. The watchdog's preflight must run `ty check src/` and `ruff check .` locally
   before dispatching a goal worker. If pre-existing failures exist, fix them on
   main before opening new work.
2. Pin dev tool versions in `pyproject.toml` (`ty==0.0.40`, `ruff==x.y.z`) to
   eliminate silent CI drift.

---

## Summary / execution order

| Phase | Item | Owner |
|-------|------|-------|
| P1 | Redirect watchdog output away from `.console/` truth files | goal worker + watchdog prompt |
| P2 | Delete STAGE_*.md root files + gitignore guard | immediate cleanup commit |
| P3 | Open-PR gate in board-worker pre-promotion | board-worker entrypoint |
| P4 | Squash stage commits before PR | goal worker PR policy |
| P5 | Watchdog commits only on meaningful actions | watchdog session prompt |
| P6 | Pin tool versions + preflight CI check | pyproject.toml + watchdog step 0 |

P2 can be done immediately as a cleanup commit. P1 and P5 are the same root cause
and should be tackled together. P3 is a small code change with high leverage. P4
and P6 are policy changes in the session prompts.
