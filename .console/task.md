# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

Add a regression test suite that execs the *live* STEP 3 snippet (from
`.console/haiku_collector_prompt.md`) against the OUTPUT of the
`extraction-health` CLI it targets — proving the markdown snippet's parsing/
mapping logic actually matches the command's real output shape and the
collector's documented OUTPUT SCHEMA, instead of relying on a hand-copied
reimplementation (the class of bug that shipped once already, see PR #313 /
log.md "Stage 5 — integration").

Prior objective (`print_structured()` shared helper) shipped and was verified
complete across Stages 0-4 on 2026-07-15; see log.md for that history.

## Overall Plan

0. Investigate: locate the STEP 3 snippet, disambiguate "the OUTPUT", review
   existing test patterns, document requirements/scope.
1. Design: exact extraction mechanism (how the test pulls the live code block
   out of the markdown file) and execution mechanism (subprocess vs.
   in-process exec), plus the OUTPUT fixtures to run it against.
2. Implement: the extraction/execution harness + regression tests.
3. Verify: full suite + lint, no new failures.

## Current Stage

**Stage 3: Finalize and prepare for merge** ✅ COMPLETE (2026-07-16)

Final merge-readiness pass, from a clean tree at `f302b75` (no code changes
since Stage 2's own commit):
- Re-ran the new suite in isolation: 12/12 passed.
- `ruff check .`: 0 violations. `ruff format --check` on both touched
  non-`.console/log`-doc files (the test file + the prompt markdown) clean —
  the markdown "error" is ruff declining to format `.md` outside preview
  mode, not a real finding.
- Documentation check: the test module's docstring already states purpose
  (why this suite exists, the PR #313 drift class it guards against) and what
  it validates (extracts+execs the real snippet, asserts against the OUTPUT
  SCHEMA contract); "how to run" is standard `pytest` discovery, consistent
  with every other test file in the repo — no per-file "how to run" section
  exists elsewhere in `tests/unit/`, and the repo's `README.md` "Test Suites
  Overview" table documents suites by category (`tests/unit/`), not per-file,
  so no README edit was needed for consistency with existing convention.
- `git status`: clean. Branch has 2 commits ahead of `main`
  (`0a2aad5`, `f302b75`), no upstream configured, no existing PR.
- Not pushed and no PR opened yet — pushing/opening a PR is a visible,
  externally-observable action this stage defers to explicit operator
  request per repo guidelines ("Do not push to remote branches without the
  operator's explicit request").

**Stage 3 rework (2026-07-16, post-rejection): "how to run" documentation**

The above pass's "standard pytest discovery, no per-file convention exists"
claim was wrong — `tests/integration/test_execution_boundary.py`'s module
docstring *does* carry a `Run from the OperationsCenter repo:\n\n    pytest
tests/integration/test_execution_boundary.py -v` block, so this repo does
have a (module-docstring-based) per-file "how to run" convention; it just
isn't universal across every test file. Fixed by adding the same pattern to
`test_step3_snippet_regression.py`'s module docstring: an explicit `pytest
tests/unit/observer/test_step3_snippet_regression.py -v` run command, plus a
short pointer to what each of the two test classes covers. Re-verified:
12/12 passed in isolation, `ruff check`/`ruff format --check` clean.

**Stage 2: Verify tests pass and check for regressions** ✅ COMPLETE (2026-07-16)

Independent re-verification of Stage 1's claims, rerun from a clean tree
(`git status` clean, `0a2aad5` HEAD):
- `tests/unit/observer/test_step3_snippet_regression.py`: 12/12 passed in
  isolation.
- Full suite: 10348 passed, 6 failed, 21 skipped, 2 xfailed — the failures are
  the same pre-existing sandbox/timing set as every prior stage's baseline
  (`test_race_condition_guards.py` ×2, `test_check_signal_collector.py`,
  `test_custodian_sweep.py`, `test_dependency_drift_collector.py`,
  `test_snapshot_edge_cases.py`). Zero new failures.
- `ruff check .`: 0 violations.
- `ruff format --check .`: 73 pre-existing drifted files repo-wide, none of
  which this branch touches (confirmed via `git diff a8bfe75 HEAD --stat` —
  only `.console/*` docs + the new test file changed); the new test file
  itself is ruff-format clean.
- No source/test changes were needed this stage — Stage 1's implementation
  and fix held up under independent re-run.

**Stage 1: Implement regression test suite for STEP 3 snippet execution** ✅
COMPLETE (2026-07-16)

New file: `tests/unit/observer/test_step3_snippet_regression.py` (12 tests).

- **Extraction mechanism**: `extract_step3_python_source()` locates the `##
  STEP 3` heading, takes its second fenced ` ```bash ` block (the CLI call is
  the first), and matches the `python3 -c "..."` wrapper to pull the literal
  python source — no hand-retyping. 4 tests (`TestStep3SnippetExtraction`)
  pin this fails loudly (clear `AssertionError`) if the heading, fence count,
  or `python3 -c` shape ever drifts.
- **Execution mechanism**: `run_step3_snippet()` re-extracts the snippet on
  every call and runs it via `subprocess.run([sys.executable, "-c", source])`
  — real execution, not eval-in-process — substituting only the hardcoded
  `/tmp/oc_extraction_health.json` path with a per-test `tmp_path` so
  parallel runs never collide.
- **Real OUTPUT**: `_cli_json_for()` builds the CLI JSON via the same
  `CliRunner` pattern as `test_cli_extraction_health.py` (mocking
  `TestSignalQuery`/`ExtractionHistoryCollector`, not the mapping logic),
  covering typical/all-defaults/multi-key-edge_case_summary/rounding fixtures,
  plus malformed-JSON and missing-file cases hitting the snippet's own
  `parse_error` except-branch.
- **OUTPUT SCHEMA contract assertion**: `test_mapped_output_matches_output_schema_extraction_contract`
  asserts the mapped result's key set and field types exactly match the
  `extraction` sub-object of `## OUTPUT SCHEMA`.
- **Bug found and fixed during this stage** (exactly the class of drift this
  ticket exists to catch): STEP 3's python mapper never emitted a `gaps` key
  at all, and its `edge_cases` key held the raw `edge_case_summary` counts
  dict rather than the `ExtractionHealth.edge_cases` sample list
  (`test_id`/`issue` dicts) — even though `extraction-health`'s real JSON
  output has carried both `gaps: list[str]` and `edge_cases: list[dict]`
  sample fields since the 2026-06-21 CLI work. Confirmed via `git stash`: the
  new suite goes 6 failed/6 passed against the pre-fix snippet, 12/12 passed
  after. Fixed `.console/haiku_collector_prompt.md`: STEP 3's python block now
  passes through `h.get('gaps', [])` / `h.get('edge_cases', [])`; the
  `parse_error` fallback branch now also includes empty `gaps`/`edge_cases`
  keys so the fallback shape matches the success shape; and `## OUTPUT
  SCHEMA`'s `extraction.gaps` type annotation was corrected from
  `[{"test_id": "<id>"}]` to `["<test_id>"]` to match the actual `list[str]`
  shape (already documented correctly in `docs/reference/EXTRACTION_FIDELITY_METRIC.md`).
- **Verification**: `ruff check`/`ruff format --check` clean on the new file.
  Full suite: 10348 passed, 6 failed (pre-existing sandbox/timing failures —
  `test_race_condition_guards.py` ×2, `test_check_signal_collector.py`,
  `test_custodian_sweep.py`, `test_dependency_drift_collector.py`,
  `test_snapshot_edge_cases.py` — identical to every prior stage's baseline),
  21 skipped, 2 xfailed. Zero new failures.

## Next Stage

None — objective complete and branch is merge-ready. Regression suite execs
the live STEP 3 snippet against real CLI OUTPUT and the OUTPUT SCHEMA
contract; the one latent drift bug it surfaced is fixed; full suite and lint
are green modulo the pre-existing baseline failures; Stage 2 independently
re-confirmed all of this from a clean tree; Stage 3 re-confirmed merge
readiness, and its rework pass added an explicit "how to run" section to the
test module's docstring (matching the `test_execution_boundary.py`
precedent) to fully satisfy the "test suite documented" acceptance
criterion. Awaiting operator go-ahead to push `goal/64e71078` and open a PR.
