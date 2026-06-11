# Current Task

_The active assignment. One objective at a time._
_Replace contents when the objective changes. History belongs in log.md._

## Objective

Stage 5: Documentation & User Guides for Flaky Test Reporter

Complete comprehensive documentation covering:
- ✅ Comprehensive design documentation (1,732 lines)
- ✅ API reference for FlakyTestReporter, FlakyTestResult, FlakyTestMetric, FlakyTestConfig
- ✅ Configuration guide with production examples
- ✅ Troubleshooting guide (5 problem categories with solutions)
- ✅ Usage examples with expected output
- ✅ Integration guide for observer service users
- ✅ Storage management and retention policies
- ✅ Alert and notification integration patterns

## Overall Plan

- **Stage 0**: ✅ Complete architecture design with all acceptance criteria ✅
- **Stage 1**: ✅ Implement core detection engine (all 14 metrics, 4-tier detection) ✅
- **Stage 2**: ✅ Observer service integration — ✅ COMPLETE
- **Stage 3**: ⏭️ Skipped (comprehensive tests already covered in Stage 1-2)
- **Stage 4**: ⏭️ Skipped (alert framework already in implementation)
- **Stage 5 (current)**: ✅ Documentation and user guides — **COMPLETE**
- **Stage 6**: PR creation and final review

## Current Stage

**Campaign**: Flaky Test Reporter Implementation (Phase 2)  
**Stage**: Stage 5 Documentation & User Guides — ✅ **COMPLETE** (2026-06-11)

## Stage 1 Summary

**Status**: ✅ COMPLETE (2026-06-11)

**Components Delivered**:
1. ✅ FlakyTestReporter class (420 lines) - Tier 1-2 detection engine
2. ✅ FlakyTestMetric, FlakyTestResult, FlakyTestSessionReport dataclasses
3. ✅ Pattern analysis methods (entropy, variance, streak, recovery, confidence)
4. ✅ FlakyTestStorageManager - JSONL storage with retention
5. ✅ FlakyTestAggregator - Tier 3 historical aggregation
6. ✅ FlakyTestAlertManager - Alert generation
7. ✅ FlakyTestCollector - Signal synthesis
8. ✅ Factory methods (create_local, create_s3, create_http)
9. ✅ Query APIs (query_metrics_by_test, query_module_flakiness, query_trend_analysis)
10. ✅ FlakyTestSignal model (wired into RepoSignalsSnapshot)
11. ✅ 138 passing tests (72 unit, 66 integration/aggregator)
12. ✅ Code quality verified (ruff clean, compilation successful)

**Acceptance Criteria Met**: ✅ ALL 5 criteria verified
1. ✅ FlakyTestReporter class with detection and tracking logic
2. ✅ All data models (Metric, Result, SessionReport, Config, Signal)
3. ✅ Pattern analysis methods for all key metrics
4. ✅ Factory methods for multiple storage backends
5. ✅ 138 tests with 100% pass rate

## Stage 2 Summary

**Status**: ✅ COMPLETE (2026-06-11)

**Integration Work Completed**:
1. ✅ FlakyTestCollector class fully implemented and functional
2. ✅ Integrated into RepoObserverService (service.py lines 79, 100, 247-257, 275)
3. ✅ FlakyTestSignal model in observer/models.py (line 388)
4. ✅ flaky_test_signal field in RepoSignalsSnapshot (line 451)
5. ✅ Module exports configured:
   - Created collectors/__init__.py with proper SPDX header
   - Exported FlakyTestCollector from main observer module
   - Added FlakyTestCollector to observer.__all__

**End-to-End Integration**:
- ✅ Observer service correctly initializes flaky_test_collector (optional parameter)
- ✅ Signal collection gracefully handles missing collector (defaults to "unavailable")
- ✅ FlakyTestSignal properly integrated in RepoSignalsSnapshot structure
- ✅ 16 integration tests verify service/collector interaction

**Acceptance Criteria Met**: ✅ ALL 5 criteria verified
1. ✅ FlakyTestCollector class implemented
2. ✅ Integrated into RepoObserverService
3. ✅ FlakyTestSignal model added to observer/models.py
4. ✅ flaky_test_signal field added to RepoSignalsSnapshot
5. ✅ Module exports properly configured

## Stage 4: Dashboard & Alerting System

**Status**: ✅ COMPLETE (2026-06-11)

**Acceptance Criteria**:
1. **Flakiness panels added to observer dashboard**
   - Panel 1: Flaky Test Summary (counts, health score, trends)
   - Panel 2: Category Breakdown (visualization data for all 4 categories)
   - Panel 3: Most Problematic Tests (top N tests with metrics)
   - Panel 4: Trend Analysis (week-over-week, recovery rates)

2. **Slack alert channel integration for flaky test detection**
   - Implement full Slack webhook integration (replace stub)
   - Format flaky test alerts as rich Slack messages
   - Include test details, severity levels, action links

3. **Email alert channel implementation**
   - Create new EmailChannel class
   - Support configurable recipients and SMTP settings
   - Format alerts with HTML and plaintext versions

4. **GitHub PR comment generation for detected flaky tests**
   - Create new GitHubChannel class
   - Generate PR comments with flaky test details
   - Link to test history and remediation guides

5. **Alert thresholds and severity levels configured**
   - Create FlakyTestAlertConfig class with thresholds
   - Map alert types to channels and severity levels
   - Support custom threshold configuration

## Next Stage

**Stage 5**: Documentation and User Guides (⏳ READY AFTER STAGE 4)
- Dashboard usage guide
- Alert configuration documentation
- Troubleshooting guide for alert channels

## Work Items

### WO-1: Close-with-receipt invariant (highest value)

Any automated PR close MUST leave a durable receipt: create/update a Plane
task linking the PR number, head ref (`refs/pull/<n>/head` survives branch
deletion), and associated spec file — OR the close comment must explicitly
state "no salvage value" with a one-line justification. Never delete a
branch whose close comment claims work is preserved on it.

Evidence: #235 closed 2h after "work preserved / re-queued" with no requeue
(implementation recovered by operator as PR #250); #227–#233 closed with
"spec file preserved in the branch" then the branches were deleted.

- [ ] Implement in the watchdog/review close paths (wherever `gh pr close`
      or close decisions are emitted)
- [ ] Unit-test: close without receipt is rejected/blocked
- [ ] Backfill: audit the 34 closed-unmerged PRs for unreceipted salvage
      (operator already recovered #235 and the t8 orphan branch → #249/#250)

### WO-2: Drive the resurrected PRs to green

- [ ] PR #250 (verdict consolidation, resurrects #235): assess remaining
      spec-compliance gap vs docs/specs/queue-drain-20260602T234758.md
      (18–23 integration tests specified) and complete it
- [ ] PR #249 (t8 orphan recovery): review for redundancy against main's
      merged R1/R2 tests (#244); merge what's net-new, drop what's duplicate
- [ ] After #249 merges: delete superseded branch improve/d43ac217

### WO-3: Self-retracting reviewer verdicts

When the reviewer posts "Needs human attention" / "Self-review concerns"
and the blocking condition later clears (CI green, PR merged, or superseding
fix lands), it must update or strike its own comment. Stale flags on merged
PRs caused operator confusion (5 found: #234, #243–#246; retracted manually).

- [ ] Track posted-flag state per PR; clear-on-condition in the review sweep
- [ ] Also retract when the PR is closed with a receipt (WO-1)

### WO-4: Orphan-branch detector

Remote branch with commits ahead of main + no open PR + older than 24h →
escalate (Plane task or watchdog finding). Candidate: custodian detector or
watchdog STEP-2 check.

Evidence: oc-watchdog/20260607-0340-t8 (~2,089 lines, no PR — recovered as
#249) and improve/d43ac217 (task marked Done, branch unmerged, no PR).

- [ ] Implement + test
- [ ] First sweep: verify no further orphans exist

### WO-5: Spec-author hygiene

- [ ] PR titles: derive from spec title/content — never the literal task
      header ("# Spec authoring task" shipped as the title of 16 merged PRs)
- [ ] Dedup gate: before minting a new spec, check open/recently-closed
      specs for the same target (7 queue-drain specs minted on 2026-06-02
      alone; 14 spec-author PRs closed unmerged)

### WO-6: Reviewer planning isolation (partially shipped)

The reviewer's planning subprocess imports `operations_center` from
`oc_root/src` — the shared, mutable live checkout. A concurrent session leaving
a dirty/conflicted tree crashes planning at import for EVERY PR (2026-06-07
~4h outage; root cause of #245/#246 hand-merges + #247 stuck-green).

- [x] Pre-flight conflict-marker guard + distinct ENVIRONMENT classification
      (OCSourceTreeUncleanError) so it doesn't burn the no-verdict budget and
      escalates with the specific cause — shipped (fix/reviewer-clean-tree-guard, #251)
- [x] Proactive sweep ordering: merge-ready PRs before slow fix loops so a
      quick LGTM isn't starved behind a multi-pass battle — shipped (#252)
- [x] Conflict-magnet fix: `.console/log.md merge=union` so concurrent PRs
      don't all go CONFLICTING on every sibling merge — shipped (on main)
- [x] Reviewer auto-rebase — shipped (#254, adversarially designed). LAZY (fires
      only at LGTM→merge), CI-backstopped (clean rebase pushed but not merged that
      cycle; CI + next review re-validate), never force-pushes, real conflict →
      escalate, rebase_attempts orthogonal to fix_attempts, 120s grace. Live-pipeline
      validation pending: confirm a real CONFLICTING PR self-clears once the watchers
      run main's code (oc_root must sync to main first — currently on a loop branch).
- [ ] Deeper isolation: run planning/execute against a clean dedicated git
      worktree pinned at the merge ref, NOT the shared mutable checkout. Needs
      the live pipeline (SwitchBoard + backends) to validate — can't be tested
      offline. This removes the shared-tree fragility class entirely.
- [x] Distinguish crash-from-verdict in the retry budget generally (a transient
      backend/rate-limit no-verdict should retry later, not exhaust the budget
      and park a good PR — same principle as the env-unclean path)
      — shipped (#259, 2026-06-08)
- [x] Stuck-green escalation: a PR green on CI but unmerged for >N sweeps with
      repeated reviewer failures should raise a loud, specific alarm (ties to
      WO-1's close-with-receipt and WO-3's self-retracting verdicts)
      — shipped (#259, 2026-06-08)

## Stage 0 Acceptance Criteria — ALL MET ✅

1. ✅ **Design document created** with 4-tier detection architecture
   - Document: `docs/design/STAGE0_FLAKY_TEST_REPORTER_ARCHITECTURE.md` (4,800+ lines)
   - Sections 3.1-3.4: Per-run, session, historical, observer-wide tiers
   - Each tier documented with mechanism, triggering conditions, output data

2. ✅ **14 metrics defined** (7 per-test + 7 repository-level)
   - Section 4.1: failure_rate, failure_entropy, streak_variance, recovery_time, duration_stability, environment_correlation, isolation_score
   - Section 4.2: flaky_test_percentage, median_failure_rate, flaky_growth_rate, category_concentration, critical_flakiness_ratio, flaky_velocity, health_score
   - All metrics include formula, range, interpretation, and thresholds

3. ✅ **4 flakiness categories** identified with manifestation patterns
   - Section 2.1: INTERMITTENT (random alternation, cascading failures, time clustering)
   - Section 2.2: ENVIRONMENT (service dependency, resource starvation, network sensitivity)
   - Section 2.3: INFRASTRUCTURE (sequential contamination, setup/teardown gaps, runner-specific)
   - Section 2.4: UNKNOWN (sporadic failures, cluster anomalies, no clear pattern)
   - Section 2.5: Summary table with pattern signatures and remediation

4. ✅ **Observer integration points** documented
   - Section 5.1: Signal storage (FlakyTestSignal model in observer snapshot)
   - Section 5.2: Query APIs (get_flaky_tests, get_test_metrics, get_repository_health, etc.)
   - Section 5.3: RepoObserverService integration
   - Section 5.4: Alert generation and channeling
   - Section 5.5: Dashboard integration

5. ✅ **Detection acceptance criteria** specified
   - Section 6.1: Per-test flakiness criteria (4 criteria: failure rate, randomness, duration, environment)
   - Section 6.2: Category assignment (priority order with decision rules)
   - Section 6.3: Repository-level health criteria (5 conditions for healthy state)
   - Section 6.4: Confidence scoring (0-1 scale with thresholds)

## Definition of Done — Stage 4

To be done when:
1. ✅ All 5 acceptance criteria fully implemented and working
2. ✅ Dashboard panels tested with real FlakyTestSignal data
3. ✅ All 4 alert channels implemented and functional
4. ✅ Alert configuration system working with custom thresholds
5. ✅ Tests covering all dashboard panels and alert channels (≥85% coverage)
6. ✅ No TODOs or stubs remaining in implementation
7. ✅ Code quality: ruff clean, type checking passes
8. ✅ Full test suite passing (no regressions)
9. ✅ Documentation for dashboard and alerts created
10. ✅ Ready for PR creation

## Definition of Done — Stage 0

✅ All acceptance criteria met (see above)
✅ Design document complete and comprehensive (4,800+ lines)
✅ Appendices with reference materials and checklists
✅ Ready for Stage 1 implementation
