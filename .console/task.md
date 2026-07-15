# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Overall Plan

Audit `docs/operator/diagnostics.md` § "Extraction Health Diagnosis" — verify every source
reference, CLI command, numeric threshold, and storage path claim against the current
source code.

## Current Stage

**Stage 5: Test all documented CLI commands and validate outputs match documentation** ✅ COMPLETE (4 discrepancies found and fixed)
**Stage 2: Verify CLI commands and their options against actual implementation** ✅ COMPLETE (1 discrepancy found and fixed)
**Stage 4: Verify data storage paths and retention policies** ✅ COMPLETE
**Stage 3: Verify alert system configuration against FlakyTestAlertConfig** ✅ COMPLETE
**Stage 1: Verify formula accuracy and metric definitions against source code** ✅ COMPLETE
(Stage 0: Audit Extraction Health Diagnosis documentation and identify all verifiable claims — ✅ COMPLETE)

## Objective

Extract every verifiable claim from the section and check it against source:
1. Source code references (file paths, line numbers, functions)
2. All 8 documented CLI commands
3. Numeric thresholds and alert severity mappings
4. Data storage paths and retention policies
5. A checklist of claims verified against source code

## Stage 0 Acceptance Criteria — ALL MET ✅

1. ✅ **Source code references extracted and verified** — 10 references
   (`query_flaky.py:387`, `models.py:460`, `flaky_test_alert_config.py:122-128`,
   `flaky_test_alerts.py`, `assertion_extractor.py`, extraction-history JSONL path,
   `query.py`/`artifact_writer.py` snapshot path pattern, storage-root default),
   all confirmed exact against current source.
2. ✅ **All 8 CLI commands listed** with routing (`scripts/operations-center.sh` →
   `operations-center-observer-snapshot` → `cli.py` `extraction-health` /
   `query-flaky-tests` commands) and flags verified against `cli.py` signatures.
3. ✅ **Numeric thresholds and severity mappings documented**: WARNING < 80%,
   CRITICAL < 50%, EMERGENCY < 10% (`FlakyTestAlertConfig`); channel routing per
   severity (`operator_log` → `+slack` → `+email` → `+pagerduty`); 200-char
   truncation cutoff; >5% anomaly delta over a 5-point moving average; 365-day
   retention.
4. ✅ **Data storage paths and retention documented**: extraction-history JSONL
   at `tools/report/operations_center/observer/extraction_history/extraction_health_history.jsonl`;
   snapshot pattern `<root>/<run_id>/repo_state_snapshot.json`; 365-day retention,
   pruned automatically each `extraction-health` run.
5. ✅ **Checklist of claims created** — 18-item verification checklist, all items
   confirmed against source, 0 discrepancies found.

## Result

**Deliverable**: `.console/STAGE0_EXTRACTION_HEALTH_AUDIT.md` — full claim-by-claim
audit table (source refs, CLI commands, thresholds, storage paths) plus the
verification checklist.

**Finding**: 0 discrepancies. The section already matches source exactly — this
confirms commit `9590e1a` ("fix(observer): correct Extraction Health Diagnosis doc
against source code") fully resolved the prior inaccuracies. No documentation
changes were needed in this pass.

## Stage 4 — Verify data storage paths and retention policies

Independent re-verification of the "Data Storage" claims (a narrower re-check of
Stage 0's item 4), against current source:

1. ✅ Extraction history JSONL path — `tools/report/operations_center/observer/extraction_history/extraction_health_history.jsonl`.
   Confirmed via `cli.py:473/865/958` (`root = storage_root or Path("tools/report/operations_center/observer")`),
   `query_extraction_history.py:62` (`ExtractionHistoryStorage(self.root / "extraction_history")`),
   and `extraction_health_history.py:219` (`self.snapshots_file = self.base_path / "extraction_health_history.jsonl"`).
2. ✅ Observer snapshot path pattern — `tools/report/operations_center/observer/<run_id>/repo_state_snapshot.json`.
   Confirmed via `query.py:522` (`json_path = self.root / run_id / "repo_state_snapshot.json"`)
   and `artifact_writer.py:15-18` (`run_dir = self.root / snapshot.run_id`; `json_path = run_dir / "repo_state_snapshot.json"`).
3. ✅ 365-day retention implemented — `extraction_health_history.py:210` (`retention_days: int = 365` default,
   not overridden at the `ExtractionHistoryStorage` call site) and enforced in `cleanup_old_snapshots()`
   (`extraction_health_history.py:283-285`, cutoff = `now - timedelta(days=self.retention_days)`), invoked every
   `extraction-health` run via `cli.py:1037` → `query.cleanup_old_extraction_history()` →
   `query_extraction_history.py:166-172` → `storage.cleanup_old_snapshots()`, surfaced as `snapshots_pruned`.
4. ✅ All path references in diagnostic commands validated — doc lines 415 (raw `tail` command),
   457 & 560 (Python glob one-liners), 495-497 (Data Storage table), and 605 (source reference list)
   all use the identical path strings verified in 1-2 above; no drift between the table and the
   command examples.

**Result**: 0 discrepancies. All four Stage 4 acceptance criteria confirmed directly against
current source — no documentation changes required.

## Stage 1 — Verify formula accuracy and metric definitions against source code

Independent re-verification of the "Extraction Metrics Reference" formula and
metric-definition claims (a narrower re-check of Stage 0's item 1), against
current source, done without relying on the Stage 0 summary:

1. ✅ Formula matches exactly — `query_flaky.py:387`:
   `success_rate = ((complete + partial) / total * 100.0) if total > 0 else 0.0`,
   equivalent to the doc's `(complete_extraction + partial_extraction) /
   total_flaky_tests × 100`.
2. ✅ `FlakyTestSignal.extraction_success_rate` field confirmed at `models.py:460`:
   `extraction_success_rate: float = 0.0`.
3. ✅ Metric definitions confirmed against the `ExtractionHealth` dataclass and
   `get_extraction_health()` logic (`query_flaky.py:99-117`, `350-392`):
   `complete_extraction` = both `test_name` and `assertion_message` present;
   `partial_extraction` = exactly one of the two present; `no_extraction` =
   neither present. `malformed_exceptions` confirmed initialized to `0` and
   never incremented anywhere in the module — matches the doc's "reserved
   counter, detection not yet implemented, always 0" claim.
4. ✅ Thresholds confirmed at `flaky_test_alert_config.py:122-128`:
   `warning_threshold=80.0, critical_threshold=50.0, emergency_threshold=10.0`.
   `should_alert_on_extraction_success_rate()` (lines 213-230) uses strict `<`
   comparisons, so a rate of exactly 80/50/10 does not alert — matching the
   doc's `≥ 80%` = healthy boundary and `< 80% / < 50% / < 10%` severity rows.

**Check that proves correctness**: ran the existing test suites that lock in
these values — `test_flaky_test_alert_config.py`, `test_extraction_health_queries.py`,
`test_models_test_signal.py`, `test_flaky_test_alerts.py` → **90 passed, 0
failures**.

**Result**: 0 discrepancies. All four Stage 1 acceptance criteria (formula,
field, metric definitions, thresholds) confirmed directly against current
source via an independent read (not by trusting the Stage 0 summary). No
documentation or source changes were needed.

## Stage 3 — Verify alert system configuration against FlakyTestAlertConfig

Independent re-verification of the "Alert System" claims (a narrower re-check of
Stage 0's item 3), against current source:

1. ✅ Thresholds match `FlakyTestAlertConfig.thresholds["extraction_success_rate"]`
   (`flaky_test_alert_config.py:122-128`): warning=80.0, critical=50.0,
   emergency=10.0. `should_alert_on_extraction_success_rate()` (lines 213-230)
   applies them with inverted (lower-is-worse) semantics exactly as the doc's
   table states: `< 10%` EMERGENCY, `< 50%` CRITICAL, `< 80%` WARNING, `≥ 80%`
   no alert.
2. ✅ Channel routing matches `channel_routes["EXTRACTION_SUCCESS_RATE_LOW"]`
   (`flaky_test_alert_config.py:89-95`): warning=[operator_log, slack],
   critical=[operator_log, slack, email], emergency=[operator_log, slack,
   email, pagerduty] — identical to the doc's Thresholds table.
3. ✅ `FlakyTestAlertManager.check_extraction_success_rate()` exists
   (`flaky_test_alerts.py:287-332`), is a `@staticmethod` taking a
   `FlakyTestSignal` and optional `FlakyTestAlertConfig`, returns 0 or 1
   `FlakyTestAlert`, and skips entirely when `signal.status == "unavailable"`
   — matches the doc's description exactly.
4. ✅ End-to-end dispatch confirmed in `cli.py:966-999`: the `extraction-health`
   command builds a `FlakyTestSignal`, calls `check_extraction_success_rate()`,
   resolves channels via `alert_cfg.get_channels_for_alert(...)`, and notifies
   each channel via `AlertChannelFactory.create_channels_from_config(...)`,
   all wrapped in a `try/except Exception` that only logs at `debug` level —
   confirms the doc's "best-effort — a channel failure never prevents the
   command from emitting its JSON payload" claim.
5. ✅ `OperatorLogChannel.notify()` (`alert_channels.py:63-118`) uses logger
   name `operations_center.alerts` and message format
   `f"ALERT [{condition}] — {error_count}/{threshold} errors in {window}min
   (collector={collector}, severity={severity})"` — byte-for-byte match with
   the doc's example log line.
6. ✅ `AlertChannelFactory.create_channel()` (`alert_channels.py:658-702`)
   confirms `email`, `github`, and `pagerduty` are all real, instantiable
   channels (not just config-only names) — the doc's channel table is
   accurate to what actually dispatches, not just what's configured. (Note:
   the separate, unrelated `alert_config.py` module has a narrower
   `valid_channels` set with no `email` — that module is not used by the
   flaky-test extraction alert path and doesn't contradict this doc section.)
7. ✅ Existing test coverage is already comprehensive: 16 tests in
   `test_flaky_test_alert_config.py::TestExtractionSuccessRateConfig` (all
   threshold/severity boundaries) and 21 tests in
   `test_flaky_test_alerts.py::TestCheckExtractionSuccessRate` (severity
   paths, unavailable-status skip, alert content, custom-config overrides),
   plus `test_cli_extraction_health.py::test_alert_log_format_includes_float_threshold`
   for the dispatch format. No new tests were needed — the acceptance
   criteria were already fully covered before this stage.

**Result**: 0 discrepancies. All four Stage 3 acceptance criteria (thresholds,
severities, channel routing, `check_extraction_success_rate()` existence/behavior)
confirmed directly against current source — no documentation or test changes
required.

**Test run**: `pytest tests/unit/observer/test_flaky_test_alert_config.py
tests/unit/observer/test_flaky_test_alerts.py
tests/unit/observer/test_cli_extraction_health.py` → 71 passed, 1 pre-existing
failure (`test_anomaly_structure_uses_timestamp_not_recorded_at` — anomaly
detection in history trends, unrelated to alert configuration; out of scope
per this task's definition of done).

## Stage 2 — Verify CLI commands and their options against actual implementation

Verified all 8 documented CLI commands and their options
(`--hours`, `--format`, `--trend-days`, `--include-assertions`, `--limit`) by
reading `cli.py` directly and by *running* both underlying typer commands
(`extraction-health`, `query-flaky-tests`) against fixture snapshot data
(`PYTHONPATH`-injected against this workspace's `src/`, not the stale `.venv`
install — see note below):

1. ✅ Routing confirmed for all 8: commands 1-5 route through
   `./scripts/operations-center.sh observer <subcommand>` → `operations-center.sh:909-913`
   → `operations-center-observer-snapshot` binstub → `cli.py`'s `extraction-health` /
   `query-flaky-tests` typer commands. Commands 6-8 are standalone shell one-liners
   (`tail`/`json.tool`, `grep`, `python3 -c`) that intentionally bypass the CLI — the
   doc never claims otherwise.
2. ✅ `--hours`, `--format` (json/table), `--trend-days`, `--include-assertions` all
   confirmed wired up correctly by live run: `extraction-health --format table` output
   matches the doc's exact multi-line block structure; `--format json --trend-days N`
   produces a `history` object with every key the doc's reference table lists
   (`window_days`, `trend`, `weekly_trend`, `slope` with `confidence` enum values,
   `anomalies`, `observations`, `recent`, `snapshots_pruned`); the
   `EXTRACTION_SUCCESS_RATE_LOW` alert log line format matches byte-for-byte.
3. ❌→✅ **`--limit` on `query-flaky-tests` was declared (`cli.py:836-840`, default 10,
   help text "Max tests to display (0 = all)") but never applied to the result —
   confirmed empirically: `--limit 3` against a 15-test fixture printed all 15 rows,
   and the undecorated default also printed all 15 (no 10-row cap). **Fixed** by
   slicing `test_names`/`assertions` to `limit` entries in `cmd_query_flaky_tests`
   (`cli.py:880-883`) when `limit > 0`. Added 4 new tests to
   `test_cli_query_flaky_tests.py` covering: capping to N, default-10 capping,
   `--limit 0` (all), and assertions capped alongside test names.
4. ✅ Example table output for `query-flaky-tests` (command 4/5) confirmed to match
   `ExtractionReportFormatter` byte-for-byte (`│`-separated columns, same header/rule
   layout) via live run against fixture data.

**Environment note**: this workspace's `.venv` was built from
`/home/dev/Documents/GitHub/OperationsCenter/.venv/bin/python` and its installed
`operations-center` package resolves to that *separate* checkout's `src/`, not this
workspace's `src/`. Running `.venv/bin/operations-center-observer-snapshot` directly
executes different (newer/unrelated) code. All verification in this stage was done
with `PYTHONPATH=<this-workspace>/src` prepended so the correct source was exercised.

**Test run**: `pytest tests/unit/observer/ -q` → 1540 passed, 3 pre-existing failures
unrelated to this change (`test_anomaly_structure_uses_timestamp_not_recorded_at`,
`test_store_with_read_only_directory`, `test_cleanup_with_zero_retention` — confirmed
via `git stash` to fail identically before this change; out of scope per this task's
definition of done). `ruff check` clean on both touched files.

**Result**: 1 confirmed discrepancy (dead `--limit` option), fixed in source with new
test coverage. No doc changes needed — the doc's description of `--limit` was correct;
the implementation was incomplete.

## Stage 5 — Test all documented CLI commands and validate outputs match documentation

Unlike Stages 0-4 (static/targeted source re-reads), this stage executed all 8
documented commands end-to-end against real, hand-built `RepoStateSnapshot` and
extraction-history fixture data (not mocks), then diff'd the actual output
structure against the doc's JSON/table examples line by line.

**Setup**: reused Stage 2's `env -u PYTHONPATH` workaround for the stray
`/home/dev/Documents/GitHub/OperationsCenter` install, and additionally ran
`pip install -e . --no-build-isolation --no-deps` (no network required —
setuptools/wheel were already present in `.venv`) so the editable install
itself points at this workspace, not just the ad-hoc `PYTHONPATH` override.

**Acceptance criteria — all met**:

1. ✅ **All 8 commands executed without errors** against fixture data sized to match
   the doc's own example numbers (91.3% success rate; 63/21/8 complete/partial/no
   split; 4 truncated + 2 special-char edge cases; 11 query-flaky-tests occurrences
   across 4 test names, 5 with assertion messages).
2. ✅ **Output structure compared to documented examples** — found and fixed 4 gaps
   (below); everything else (command 1/2 shapes, alert log line, `IndexError`
   caveat, anomaly dict shape) matched exactly.
3. ✅ **Example realism verified** — the qualified-test-name and always-populated
   `extraction_success_rate` examples were shown to be impossible in practice; fixed.
4. ✅ **Documented options exercised**: `--hours`, `--format table/json`, `--trend-days`,
   `--include-assertions`, and `--limit` (Stage 2's fix) all behaved as documented
   under live execution.

**4 discrepancies found and fixed in `docs/operator/diagnostics.md`**:

1. Command 6's `tail -5 ... | python3 -m json.tool` fails (`Extra data`) whenever more
   than one JSONL line is piped in — the normal case. Replaced with a working
   `python3 -c` snippet; added an explanatory note.
2. Command 8 / diagnostic Step 7: `FlakyTestSignal.extraction_success_rate` on a real
   observer snapshot always reads `0.0` — `FlakyTestCollector.collect()`
   (`collectors/flaky_test_collector.py`) never sets `extraction_success_rate`,
   `extracted_count`, or `extraction_gaps`, corroborated by `snapshot_validator.py`'s
   own dedicated "missing extraction visibility" structural-validation error. The
   doc's "re-run observe-repo to refresh" remediation was wrong (re-running doesn't
   populate the field). Added a caveat, corrected the example to `0.0`, rewrote Step 7.
3. Commands 4/5 example tables showed qualified pytest node IDs
   (`tests/test_auth.py::test_token_refresh`); real output only ever has the bare
   `test_name` (`test_token_refresh`) since `get_failing_test_names()` reads
   `TestSignal.test_name`, which never carries a module path. Fixed both examples.
4. Command 3's `trend`/`weekly_trend` example and key-reference table omitted real
   fields present in live output: `success_rate_std_dev`,
   `complete_extraction_mean`/`partial_extraction_mean`/`no_extraction_mean`,
   `edge_case_trends`, and a nested `trend.anomalies` list distinct from the
   top-level `anomalies`. Added the missing rows and expanded the example.

**Test run**: `pytest tests/unit/observer/ -q` → 1540 passed, 3 failed — all 3
pre-existing/environmental and unrelated (confirmed by inspection, matching Stage
2's finding): two fail because the sandbox runs tests as root (permission checks
bypassed); one (`test_anomaly_structure_uses_timestamp_not_recorded_at`) hardcodes
absolute dates that have since aged out of its own 30-day query window now that the
system date is 2026-07-14. This stage touched no Python source, only
`docs/operator/diagnostics.md`. `ruff check` on the pre-existing uncommitted
`cli.py`/test-file changes (Stage 2's `--limit` fix): clean.

**Fixture cleanup**: all synthetic snapshot run directories and the JSONL history
file created for live verification were deleted afterward (gitignored, not part of
the deliverable) so the workspace is left clean.

**Result**: 4 confirmed documentation discrepancies, all fixed. No source changes
were required this stage (Stage 2 already fixed the one real source bug, `--limit`).

## Stage 6 — Compile findings and identify all discrepancies between docs and code

Pure compilation stage: synthesized Stages 0–5 into `.console/STAGE6_COMPILED_FINDINGS.md`,
organized by the task's 5 acceptance criteria. No new source/doc auditing was
performed — Stages 0–5 already exhaustively covered the section (general claims,
formulas, CLI commands/options, alert system, storage/retention, and live
execution of all 8 commands against real fixture data).

**Compiled total: 5 discrepancies found across all stages, all fixed, 0 open:**

1. **Non-existent/dead CLI option** (Stage 2): `query-flaky-tests --limit N` was
   documented and declared but never applied to output. Fixed in source
   (`cli.py:880-883`), covered by 4 new tests.
2. **Example output mismatch** (Stage 5): command 6's `json.tool` pipe fails on
   multi-line JSONL input. Doc fixed with a working snippet.
3. **Example output mismatch** (Stage 5): command 8 / Step 7's
   `extraction_success_rate` always reads `0.0` in real snapshots (collector
   never sets it), and the doc's remediation advice was wrong. Doc fixed with
   caveat + corrected example + rewritten step.
4. **Example output mismatch** (Stage 5): commands 4/5 tables showed
   unreachable qualified pytest node IDs instead of the real bare test names.
   Doc fixed.
5. **Example output mismatch** (Stage 5): command 3's `trend`/`weekly_trend`
   example omitted several real fields. Doc fixed with the missing fields added.

**Zero discrepancies** in: file paths/line numbers (Stage 0), formulas/threshold
values (Stage 1), alert channels (Stage 3), storage paths/retention (Stage 4) —
each independently re-verified against current source with its own test run.

No new code or doc changes were needed this stage; all fixes above were already
applied to the working tree by their originating stage and remain uncommitted.

## Stage 7 — Update diagnostics.md to correct all discrepancies found

Final verification stage: re-confirmed (independently, not by trusting prior-stage
summaries) that every one of Stage 6's 5 compiled discrepancies is actually present
and correct in the current `docs/operator/diagnostics.md` working-tree diff, and
that no discrepancy was missed.

**Independently re-checked against current source:**
- `cli.py:880-883` `--limit` truncation fix is present and matches the doc.
- `FlakyTestCollector.collect()` (`flaky_test_collector.py`) confirmed to never set
  `extraction_success_rate`/`extracted_count`/`extraction_gaps` — corroborates the
  command 8 / Step 7 caveat text.
- `get_failing_test_names()` (`query.py:438-459`) confirmed to key its dict by the
  bare `signal.test_name` only — corroborates the commands 4/5 bare-name fix.
- `ExtractionHealthTrend` dataclass (`extraction_health_history.py:118-138`)
  confirmed to declare `success_rate_std_dev`, `complete_extraction_mean`,
  `partial_extraction_mean`, `no_extraction_mean`, `edge_case_trends`, and
  `anomalies` — corroborates the command 3 `trend`/`weekly_trend` field additions.
  `models.py:460-462` confirmed `extracted_count`/`extraction_gaps` exist as
  `FlakyTestSignal` fields (support fields for the command 8 caveat's phrasing).
  `snapshot_validator.py:384` confirmed the "missing extraction visibility"
  validation message cited in the doc's command 8 caveat.
- Full re-read of the rendered section (lines 214-613) end to end — no
  inconsistency, dangling reference, or unapplied fix found.

**Test/lint verification:** `pytest tests/unit/observer/ -q` → 1540 passed, 3
pre-existing failures (`test_anomaly_structure_uses_timestamp_not_recorded_at`,
`test_store_with_read_only_directory`, `test_cleanup_with_zero_retention`) —
reproduced identically via `git stash` against the base commit, confirming they
predate and are unrelated to this change. `ruff check` on `cli.py` and the query
test file: clean.

**Result:** 0 additional discrepancies found. All 5 from Stage 6 remain correctly
applied in `docs/operator/diagnostics.md`. No further doc edits were needed.

## Stage 8 — Validate updated documentation and commit changes

Final validation before commit. Re-ran the observer test suite (1540 passed, 3
pre-existing failures — reproduced identically via `git stash` against base
commit `9590e1a`, confirmed unrelated/out of scope) and `ruff check` (clean).
Live-executed the documented commands against this workspace's actual source
(`extraction-health`, `--format table`, `--trend-days 14`, `query-flaky-tests`,
`--include-assertions --hours 6`, the command-6 JSONL pretty-print snippet, and
the command-8 snapshot-rate one-liner) and confirmed via direct source read
that `FlakyTestCollector.collect()` still never sets `extraction_success_rate`
/`extracted_count`/`extraction_gaps`, corroborating the Stage 5 caveat.

**New finding:** the "Related Sections" row for
[Observer Snapshot Staleness](#observer-snapshot-staleness) still claimed
re-running `observe-repo` refreshes a stale `extraction_success_rate` —
contradicting the Stage 5 caveat that this field is never populated by
`observe-repo` regardless of staleness. Fixed to point at the command 8
caveat instead.

Confirmed no TODO/FIXME/placeholder text anywhere in the diff.

**Result:** doc is coherent end-to-end, all CLI commands verified working,
0 new test/lint failures. Committed the full diff (doc + `cli.py` `--limit`
fix + new tests + `.console/*`) with a message referencing source-code
accuracy, and pushed the branch — per this stage's explicit acceptance
criteria (unlike Stages 0–7, which were verification-only and left changes
uncommitted).

## Next Steps

None required — all 8 stages (0 through 8) are complete and the branch has
been committed and pushed. Stage 0 (full-section audit), Stage 1
(formula/metric-definition focus), Stage 2 (CLI commands/options focus — 1
source fix applied), Stage 3 (alert system focus), Stage 4 (storage/retention
focus), Stage 5 (live execution of all 8 commands against real fixture data —
4 doc fixes applied), Stage 6 (compiled findings report), Stage 7 (independent
final re-verification — 0 new discrepancies), and Stage 8 (final validation +
1 coherence fix + commit/push) confirm the section is accurate, internally
coherent, and its CLI behavior matches documentation. If further stages are
assigned against this task (e.g. periodic re-audit, or auditing other
diagnostics.md sections), start a new Stage entry rather than appending to
this one.
