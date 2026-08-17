---
campaign_id: dd2bdbb2-b0e8-4fd9-9e18-576ee0dfb892
slug: scene-timing-audit-test-hardening
phases:
  - implement
  - test
  - improve
repos:
  - managed-video-project
area_keywords:
  - workflow/long_form/stages
  - tools/audit
  - domain/video/logging
status: cancelled
created_at: 2026-05-18T16:11:30+00:00
---

## Overview

The scene-timing stage and its audit emission layer in the managed video project have accumulated 7+ point-fix commits in recent history (tail trim buffer, boundary gap clamp, reconcile mode emission, CPS recalibration, word-reveal row drop). Each fix addressed a production-discovered defect that existing tests did not catch. This campaign adds targeted unit tests to cover the specific code paths those fixes touched, preventing regression and reducing future fix churn.

## Goals

1. **Add reconciliation→audit emission tests** — Verify that `build_scene_duration_reconcile_audit()` produces correct `delta_before_s`, `delta_after_s`, and `reconcile_mode` values for each reconciliation outcome (`pad_audio`, `trim_audio`, `use_audio_duration`, `use_timeline_duration`). Test the full chain from `_reconcile()` through audit dict emission, not just the round-trip serialisation that exists today. Target file: `test/unit/tools/audit/test_reconcile_audit_emission.py`.

2. **Add `_clamp_windows` defensive-floor and edge-case tests** — Cover the interaction between `_clamp_windows()` and the `_CONT_MIN_WINDOW_S` continuation floor that was added in the recent fix. Include cases: zero-duration window, window start beyond `final_duration_s`, and multiple continuation lines in the same scene. Target file: `test/unit/workflow/long_form/stages/test_scene_timing_clamp_windows.py`.

3. **Add boundary-overflow metric accumulation tests** — Extend `test_boundary_overflow_metrics.py` with cases that exercise the actual clamping path that produces `overflow_spilled_ms` values, including multi-boundary accumulation within a single scene and interaction with `_MIN_BOUNDARY_TRAIL_RESERVE_MS`. Verify `_boundary_gap_clamp_count()` and `_scene_overflow_ms_total()` agree after a realistic multi-scene payload.

4. **Add CPS / narration-duration calibration edge-case tests** — Test the expected-CPS threshold (recently changed from 9.0 to 14.0) against realistic line lengths: very short lines (< 10 chars), very long lines (> 300 chars), and lines with high punctuation density. Verify `pad_overflow` is not raised for lines within the calibrated CPS range. Target file: `test/unit/workflow/long_form/stages/test_voice_over_cps_calibration.py`.

## Constraints

- All new tests are **unit tests** — no network, no ffmpeg subprocess, no WAV file I/O. Mock any file-system or subprocess dependency.
- Do not modify production source files; this campaign is test-only.
- Follow the existing test layout convention: unit tests under `test/unit/` mirroring the `src/` package path.
- Use `pytest` with the project's existing fixtures and conftest infrastructure.
- Each goal targets a specific test file to keep PRs reviewable and independent.
- Do not duplicate coverage that already exists in `test_scene_timing_reconcile.py`, `test_scene_timing_continuation_window.py`, or `test_boundary_overflow_metrics.py` — extend, don't repeat.

## Success Criteria

- All four new/extended test files pass under `pytest` with zero failures.
- Each goal adds ≥ 3 new test cases covering distinct code paths.
- The reconciliation audit emission tests exercise the actual `build_scene_duration_reconcile_audit()` function (not just the dataclass constructor).
- The `_clamp_windows` tests cover the `_CONT_MIN_WINDOW_S` floor path that was missing before the recent fix.
- The boundary-overflow tests include at least one multi-scene, multi-boundary scenario.
- The CPS tests validate behaviour at both the old (9.0) and new (14.0) threshold boundaries.
- No production code changes are introduced.