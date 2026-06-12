# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

**Stage 1: Fix Scope Creep in PR #271 Review Resolution**

Resolve scope ambiguity from Stage 0 investigation. Remove unrelated WO-1/WO-6 workflow items from task.md that crept into the PR #271 revert scope. Task.md restructuring was pre-existing (on main) and should not be part of the edge-case test revert.

## Acceptance Criteria

1. ✅ task.md revised to focus exclusively on PR #269 test revert and review concern resolution
   - WO-1 through WO-6 items removed (pre-existing on main, not part of revert scope)
   - Current objective clearly stated
   - Acceptance criteria for this stage documented

2. ⏳ All 5 review concerns from PR #271 addressed:
   - ✅ Concern #1: Scope Ambiguity — 6 unimplemented metrics deferred to Phase 2 (documented in investigation report)
   - ✅ Concern #2: Task.md restructuring out of scope — verified no changes in revert
   - ✅ Concern #3: Expected value precision discrepancy — documented in investigation report
   - ✅ Concern #4: Root-cause documentation — architectural decision clarified
   - ✅ Concern #5: CI restoration — test removal verified, production code unchanged

3. ⏳ Code changes complete and verified:
   - Tests removed via revert (stage 0)
   - Production code untouched (verified)
   - No hanging stubs or deferred metrics left incomplete in src/

4. ⏳ Tests pass and linters clean:
   - Run full test suite: `pytest`
   - Run ruff linter: `ruff check .`
   - Zero failures, zero linter violations

5. ⏳ Changes committed and pushed:
   - Stage 1 commit created: "fix(pr-271): Remove scope creep from task.md"
   - All changes pushed to `fix/revert-269-green-main` branch
   - Existing PR updated in place

## Investigation Summary

**Stage 0 Complete**: Comprehensive analysis identified:
- 6 unimplemented metrics from PR #269 tests (deferred to Phase 2)
- Expected value precision issue in failure_entropy test (0.081296 vs 0.080789)
- Task.md workflow items (WO-1/WO-6) are pre-existing on main, not introduced by revert
- All production code remains unchanged

See: `INVESTIGATION_REPORT_REVIEW_CONCERNS.md` (committed to branch)
