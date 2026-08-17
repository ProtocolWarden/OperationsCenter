# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective — ✅ COMPLETE (2026-07-15)

Add a shared helper (e.g. `print_structured(console, output)`) for all Rich
console structured-output printing, so CLI commands stop hand-rolling the
JSON/table print path independently.

Prior objective (gaps/edge_cases CLI exposure) shipped in PR #374 and was
re-verified 2026-07-07/2026-07-13/2026-07-14; see log.md for that history.

## Overall Plan

0. Analyze current Rich `Console` usage across the codebase; define what
   "structured output" means for the helper and which files it should target.
1. Design the helper's exact signature, module location, and migration plan.
2. Implement `print_structured()` and migrate the identified call sites.
3. Test: unit tests for the helper (dict/BaseModel/dataclass inputs, JSON
   highlighting via a buffered `Console`) plus regression coverage on migrated
   commands' `--json` output.
4. Final verification: full suite + lint, no new failures.

## Current Stage

**Stage 4: Refactor existing code to use the new shared helper** ✅ COMPLETE
(2026-07-15)

- Independently re-verified Stage 2's migration against the "refactor
  existing code" acceptance bar: swept the full source tree for any
  remaining `typer.echo(json.dumps(...))` / `console.print(json.dumps(...))`
  bypass patterns outside `cli_output.py`'s own docstring — none found, so
  no target call site was missed.
- Walked every `json.dumps`/`console.print` occurrence still present in the
  9 migrated files (`observer/cli.py` has the most: lines ~335, ~348,
  ~579-596, ~702-715, ~903, ~1073) and confirmed each is legitimately out
  of `print_structured`'s scope: inline `[dim]` debug context inside a
  markup string, disk writes (`output.write_text`, no console involved),
  the deliberate `--pretty` vs. non-`--pretty` raw-string dual mode in
  `show`, the `ExtractionReportFormatter`-routed combined-output branch in
  `query-flaky-tests` (shares one `output` variable across json/markdown/
  table branches — restructuring just the json arm would break that
  shared path), and a serializability guard whose `json.dumps` result is
  discarded, not printed. None are missed migrations.
- Checked the one behavioral wrinkle in the diff: `artifact_index/cli.py`'s
  two migrated sites previously used `default=_path_default` (raises
  `TypeError` on anything but a `Path`) while `print_structured` uses
  `default=str` (stringifies anything). Confirmed both call sites' payload
  dicts (`_run_summary()` + the `skipped` list) already pre-stringify every
  `Path` before assembly, so the `default=` fallback was dead code at both
  sites pre-migration — no behavior change.
- Re-ran `ruff check` / `ruff format --check` on all 15 touched files
  (clean) and the full suite: 10298 passed, 6 failed, 21 skipped, 2
  xfailed — the same 6 pre-existing failures named in Stage 2/3 (sandbox
  race conditions + one unrelated `test_custodian_sweep.py` assertion),
  zero new failures.
- No source changes were needed this stage — Stage 2's migration already
  satisfied the "refactor existing code to use the shared helper"
  objective; this stage is the independent verification pass confirming
  that.

## Stage 4 Acceptance Criteria — ALL MET ✅

1. ✅ **Identified and updated all relevant callsites** — swept for
   remaining bypass patterns (none found); all 9 Stage-0/1 target files
   migrated; the 2 deliberately-excluded sites (`artifact_index/cli.py`
   truncated content dump, `observer/cli.py` formatter-routed combined
   output) re-confirmed genuinely out of scope, not oversights.
2. ✅ **Replaced redundant console output code with calls to
   print_structured** — 15 call sites across 9 files now route through
   the shared helper instead of hand-rolled `json.dumps`/`typer.echo`/
   pre-serialized `console.print`.
3. ✅ **Behavior remains identical to original implementation** —
   `default=str` vs. the old `default=_path_default` verified as a no-op
   difference (no raw `Path` ever reaches either call site's `json.dumps`
   pre-migration); `sort_keys` preserved per-site; soft-wrap and no-ANSI
   behavior verified in `test_cli_output.py`.
4. ✅ **All refactored calls use consistent patterns** — every migrated
   site now calls `print_structured(console, <payload>[, sort_keys=True])`
   with no per-file variation in how the payload is serialized or
   rendered.

Prior: Stage 3 ✅ COMPLETE (2026-07-15)

- Stage 2 already added `tests/unit/test_cli_output.py` (15 tests, 100%
  line/branch coverage). This stage audited that suite against the
  helper's documented contract (docstring + Stage 1 design doc §4) and
  found five behaviors it described but didn't yet exercise: `str` inputs
  (proves the "callers must pass data, not `model_dump_json()`" contract —
  a `str` is rendered as a JSON string scalar, not parsed), `bool`/`int`/
  `float` primitive passthrough, a `dict` *subclass* (`OrderedDict`, to pin
  that it takes the `else` passthrough branch rather than the non-`dict`
  `Mapping` branch — both would produce correct output, but only one is
  the intended dispatch path), non-ASCII/unicode preservation
  (`ensure_ascii=False`), and pretty-print indentation on nested payloads.
- Added 7 new tests (22 total): `TestMappingInput.test_dict_subclass_
  passthrough_not_routed_through_mapping_branch`; `TestOtherJsonNativeInputs.
  test_bool_renders_as_json_literal` / `test_int_renders_as_json_number` /
  `test_float_renders_as_json_number` /
  `test_str_input_is_not_parsed_as_json_but_rendered_as_a_string_scalar`;
  new `TestUnicodeAndFormatting` class with
  `test_non_ascii_characters_are_not_escaped` and
  `test_nested_payload_is_pretty_printed_with_indent`.
- No production code changed — `src/operations_center/cli_output.py` was
  already correct and already at 100% coverage; this stage strengthens the
  proof, it doesn't fix a gap.
- Verification: `ruff check`/`ruff format --check` clean on the test file.
  `pytest --cov=operations_center.cli_output`: 22 passed, 100.00% line
  coverage, 100.00% branch coverage (15/15 stmts, 6/6 branches). Full
  suite: 10298 passed, 6 failed, 21 skipped, 2 xfailed — the 6 failures are
  the identical named tests that failed on Stage 2's pre-change baseline
  (`test_race_condition_guards.py` ×2, `test_check_signal_collector.py`,
  `test_custodian_sweep.py`, `test_dependency_drift_collector.py`,
  `test_snapshot_edge_cases.py`), zero new failures introduced.

## Stage 3 Acceptance Criteria — ALL MET ✅

1. ✅ **Unit tests cover normal cases and edge cases** — 22 tests spanning
   `dict`/`BaseModel`/`dataclass` (incl. nested)/non-`dict` `Mapping`/`dict`
   subclass/`list`/`None`/empty dict & list/`bool`/`int`/`float`/`str`/
   `sort_keys` both ways/`default=str` fallback/soft-wrap regression/no-ANSI
   on non-tty/unicode preservation/indent formatting.
2. ✅ **Tests verify output format and structure** — every test parses the
   rendered output back with `json.loads` and asserts structural equality
   (or, for scalars/formatting, asserts the exact rendered text); dedicated
   checks for indentation (multi-line, 2-space) and absence of `\uXXXX`
   escapes / ANSI codes.
3. ✅ **Tests pass with 100% coverage of helper code** — `pytest --cov`
   reports 100.00% line and 100.00% branch coverage on
   `src/operations_center/cli_output.py` (15/15 statements, 6/6 branches).
4. ✅ **Test file placed in appropriate test directory** —
   `tests/unit/test_cli_output.py`, matching the flat-module-under-`tests/
   unit/` convention already used for the other Stage-2-migrated modules.

Prior: Stage 2 ✅ COMPLETE (2026-07-15)

- Created `src/operations_center/cli_output.py` with `print_structured()`
  exactly per the Stage 1 design (`console: Console, output: Any, *,
  sort_keys: bool = False`), full docstrings, type hints.
- Migrated all 9 target files (15 call sites — 13 from the design doc's
  table plus 2 discovered while implementing: a `list-active --json` branch
  in `entrypoints/audit/main.py` that bypassed `Console` via
  `typer.echo(_json.dumps(...))`, not previously catalogued, and
  `observer/cli.py`'s `show --pretty --format json` branch which now routes
  through `print_structured` instead of `console.print_json(json_string)`).
- Deleted the stale soft-wrap comment at `observer/cli.py` (the
  `typer.echo` call it justified no longer exists).
- Left `artifact_index/cli.py`'s `get-artifact --print-content` call site
  (json.dumps + `_path_default`) **unmigrated** — Stage 1's design doc
  mischaracterized it as a "read-json command"; it's actually a raw
  content dump (JSON *or* text, chosen by `content_type`) with
  `--max-bytes` truncation logic that `print_structured` has no equivalent
  for. Migrating it would silently drop truncation. `_path_default` and
  the `json` import both stay, since this is their only remaining caller.
  `observer/cli.py`'s `query-flaky-tests --format json` combined-output
  branch (lines ~886-899) was also left alone — it goes through
  `ExtractionReportFormatter`, a different pre-existing formatting
  abstraction, not a naked `json.dumps` bypass, and wasn't in Stage 1's
  scoped table.
- Added `tests/unit/test_cli_output.py` (15 tests): dict / `BaseModel`
  (verifies `mode="json"` datetime/Path conversion) / dataclass (incl.
  nested) / non-`dict` `Mapping` / list / `None` / empty dict/list /
  `sort_keys` True vs False / `default=str` fallback / soft-wrap
  regression (220-char value stays on one line at `width=80`) / no ANSI
  codes on non-tty output.
- Updated 4 existing CLI tests that asserted on the *old* serialization
  mechanism (`model_dump_json()`/`typer.echo` mocks) in
  `tests/unit/entrypoints/audit/test_main_cov.py` (3 tests) and
  `tests/unit/entrypoints/calibration/test_main_cov.py` (3 tests) and
  `tests/unit/entrypoints/governance/test_main_cov.py` (1 test) — the
  test doubles are `SimpleNamespace`/bare `MagicMock` fakes, not real
  `BaseModel`/dataclass instances, so they no longer match
  `print_structured`'s type-dispatch; rewrote these to assert the CLI
  calls `print_structured(console, <object>)` with the right object,
  rather than re-asserting on `print_structured`'s own serialization
  (already covered by `test_cli_output.py`).
- Verification: `ruff check .` — 0 violations. `ruff format --check` on
  all touched files — clean (repo-wide pre-existing drift in 68 unrelated
  files, confirmed unrelated by name and by reproducing on unmodified
  branch). Full suite: 10291 passed, 21 skipped, 2 xfailed, 6 failed — all
  6 failures reproduce identically on the branch tip *before* this
  stage's changes (sandbox race conditions in
  `test_race_condition_guards.py`/`test_check_signal_collector.py`/
  `test_dependency_drift_collector.py`/`test_snapshot_edge_cases.py`, plus
  one unrelated `test_custodian_sweep.py` assertion) — zero new failures
  introduced.

Full design: `.console/STAGE1_PRINT_STRUCTURED_DESIGN.md`.

Prior: Stage 1 ✅ COMPLETE (2026-07-15) — `.console/STAGE1_PRINT_STRUCTURED_DESIGN.md`.
Stage 0 ✅ COMPLETE (2026-07-15) — `.console/STAGE0_RICH_CONSOLE_HELPER_ANALYSIS.md`.

## Stage 0 Acceptance Criteria — ALL MET ✅

1. ✅ **Identified all files using Rich console in the codebase**
   - 16 production files import and use `rich.console.Console` (full list in
     the analysis doc, Part 1). A broader `grep -rli rich` match of 51 files
     was mostly false positives (the substring "rich" inside *enrich*/*richer*
     in docstrings/comments) — confirmed by checking each for an actual
     `rich` import.
   - 2 test files construct `Console` directly for output capture/markup
     assertions (not shared-helper consumers).

2. ✅ **Documented current output patterns and use cases**
   - Status/severity markup convention (red=error, yellow=warning,
     green=success, dim=muted) — consistent in meaning but re-implemented
     inline in every file, including a `status_color` ternary duplicated
     4 times across `regression/main.py` and `replay/main.py`.
   - Dual-mode (human vs. JSON) output — implemented 4 different,
     inconsistent ways across files; only `observer/cli.py` uses
     `console.print_json()`, 7 files bypass `Console` entirely via
     `typer.echo(json.dumps(...))`.
   - Table rendering — one-off `rich.table.Table` construction per command;
     too heterogeneous to generalize, left out of scope.
   - Panel/dashboard composition (`observer/extraction_health_dashboard.py`)
     and interactive wizard (`entrypoints/setup/main.py`) — distinct
     sub-patterns, secondary/non-fit for this helper.
   - See analysis doc Part 2 for full detail and code references.

3. ✅ **Clarified what "structured output" means for this helper**
   - Defined as the machine-readable JSON payload path of a dual-mode CLI
     command (the current `--json`/`--format json` branch), not the
     human-readable table/text branch.
   - `print_structured(console, output)` normalizes `dict` / Pydantic
     `BaseModel` / dataclass-derived payloads to one JSON string
     (`indent=2, ensure_ascii=False, default=str`) and always renders via
     `console.print_json(...)` so structured output never again bypasses the
     caller's `Console`. Full contract in analysis doc Part 3.

4. ✅ **Listed files that would benefit from the shared helper**
   - High priority (bypasses `Console` today): `entrypoints/audit/main.py`,
     `entrypoints/calibration/main.py`, `entrypoints/run_show/main.py`,
     `entrypoints/worker_backend_status/main.py`,
     `entrypoints/worker_backend_probe/main.py`, `run_memory/cli.py`.
   - Medium priority (routes through `Console` but unhighlighted):
     `artifact_index/cli.py`, `entrypoints/governance/main.py`,
     `observer/cli.py`.
   - Low priority / partial fit: `observer/extraction_health_dashboard.py`.
   - Not applicable (no JSON branch / table-only / interactive-only):
     `entrypoints/artifacts/main.py`, `entrypoints/fixtures/main.py`,
     `entrypoints/regression/main.py`, `entrypoints/replay/main.py`,
     `entrypoints/setup/main.py`, `entrypoints/setup/providers.py`.
   - Full ranked list with rationale in analysis doc Part 4.

## Stage 1 Acceptance Criteria — ALL MET ✅

1. ✅ **Defined function signature with parameter types and return value**
   - `print_structured(console: Console, output: Any, *, sort_keys: bool = False) -> None`
     in new module `src/operations_center/cli_output.py`. The `sort_keys`
     keyword was discovered (not assumed) by reading all 9 target files' actual
     `json.dumps` calls — 4 of them pass `sort_keys=True` for deterministic,
     automation-consumed output and would silently regress without it.

2. ✅ **Decided on module/file location for the shared helper**
   - New flat top-level module `src/operations_center/cli_output.py`, sibling
     to existing single-file cross-cutting modules (`capability_ownership.py`,
     `close_invariants.py`, `impact_analysis.py`, etc.) rather than nested
     under `entrypoints/`, because 3 of the 9 target files
     (`observer/cli.py`, `artifact_index/cli.py`, `run_memory/cli.py`) are
     top-level packages themselves, not `entrypoints/` submodules. Full
     rationale in the design doc §2.

3. ✅ **Documented expected behavior, output format(s), and edge cases**
   - Normalization rules for `BaseModel`/`dataclass`/`Mapping`/other (§3);
     verified empirically against the installed `rich==15.0.0` that
     `console.print_json` never soft-wraps (hardcoded `soft_wrap=True`
     internally) and emits no ANSI codes on non-tty output — this directly
     resolves the soft-wrap concern behind the `typer.echo` comment at
     `observer/cli.py:1075-1076`, meaning that call site (and the other 5
     `typer.echo` sites) are safe to migrate. Also documented: pre-serialized
     JSON strings are NOT auto-parsed (callers must pass data, not a
     `model_dump_json()` string), `None`/empty-collection behavior, circular
     references, and the dual console+disk-write case in `governance/main.py`.
     Full detail in design doc §4.

4. ✅ **Verified design aligns with existing Rich console patterns in codebase**
   - Always renders via `console.print_json(...)`, the one already-correct
     existing pattern (`observer/cli.py:589`); takes the caller's own
     `Console` instance (no new global); matches existing `ensure_ascii=False`/
     `indent=2`/`default=str` conventions. Concrete per-file migration table
     (13 call sites across 9 files) in design doc §5, including a correction
     to Stage 0's file-level categorization of `artifact_index/cli.py` (2 of
     its 3 JSON call sites bypass `Console` via `typer.echo`, not just the one
     Stage 0's summary implied).

Full design: `.console/STAGE1_PRINT_STRUCTURED_DESIGN.md`.

## Stage 2 Acceptance Criteria — ALL MET ✅

1. ✅ **Helper function implemented with full type hints and docstrings** —
   `src/operations_center/cli_output.py`.
2. ✅ **Handles various structured output scenarios** — `dict`, `BaseModel`,
   `dataclass` (incl. nested), non-`dict` `Mapping`, list, `None`, empty
   collections, `sort_keys` True/False, `default=str` fallback; all covered
   by `tests/unit/test_cli_output.py` (15 tests).
3. ✅ **Integrates properly with Rich console** — always renders via
   `console.print_json(data=...)`; verified no soft-wrap (regression test)
   and no ANSI codes on non-tty output.
4. ✅ **Code follows project style and conventions** — SPDX header, flat
   module placement matching existing single-file utilities, `ruff check`/
   `ruff format` clean.

Migration status: all 9 Stage-1 target files migrated (15 call sites — 13
from the design table plus 2 found during implementation); one call site
(`artifact_index/cli.py` `get-artifact --print-content`) deliberately left
unmigrated (truncation semantics `print_structured` doesn't support — see
Current Stage notes above for full rationale). Full verification results
(tests/lint) also in Current Stage above.

## Next Stage

None queued. This objective (`print_structured()` helper + migration) is
now complete across all 5 stages (0-4): helper implemented, all in-scope
call sites migrated and independently re-verified against the "refactor
existing code" acceptance bar, tests comprehensive (22, 100% coverage),
full-suite/lint re-verified twice with zero new failures. Remaining open
items are explicitly out of scope for this ticket (see Stage 1 design doc
§6): a companion status-message helper for the red/yellow/green/dim
severity convention, and `artifact_index/cli.py`'s truncated
raw-content-dump path. Objective can be marked DONE.
