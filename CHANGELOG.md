# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.4] - 2026-05-27

### Added

- **Collector JSON Hardening** — Comprehensive validation framework (`src/operations_center/observer/validation.py`) with 5 specialized validator classes that harden the system against malformed JSON payloads
  - `ArtifactValidator` base class with structured error logging (parse_error, structure_error, io_error)
  - `ExecutionOutcomeValidator`, `RequestValidator`, `ValidationHistoryValidator`, `DependencyReportValidator`, `LintItemValidator` — per-collector validation
  - `ParseErrorMetadata` dataclass for error tracking in signal models
  - All 6 JSON-parsing collectors now implement three-stage validation: File I/O → JSON Parse → Structure
  - 101 comprehensive tests covering all 26 documented malformation scenarios (P1-P10 parse, S1-S10 structure, E1-E6+ edge cases)

- **Deployment Documentation** — Complete stage documentation including:
  - `STAGE_0_ANALYSIS.md` — Vulnerability analysis of 8 JSON parse sites, 57+ tests, failure modes catalog
  - `STAGE_1_DESIGN.md` — Design specification for validation approach, error response format, 26 malformations with examples
  - `STAGE_3_IMPLEMENTATION.md` — Implementation specification with 10 sections covering architecture, failure modes, test coverage, logging
  - `STAGE_3_VERIFICATION.md` — Independent verification of acceptance criteria against actual implementation
  - `STAGE_6_DEPLOYMENT.md` — Deployment guide with error handling examples, checklist, release notes

### Fixed

- **LintItemValidator format compatibility** — Corrected validator to accept ruff's actual JSON format (`location.row/column` vs `location.start.line/column`), fixing all 39 lint signal tests

- **JSON parse crash vulnerability** — All json.loads() calls (12 total across 6 collectors) now protected by try/except blocks:
  - `OSError` caught and logged as WARNING
  - `UnicodeDecodeError` caught and logged as DEBUG
  - `json.JSONDecodeError` caught and logged as DEBUG with line/column information
  - All error paths return safe signals with `status="not_available"` instead of crashing

### Changed

- **Graceful error handling for all collectors** — Malformed artifacts now gracefully skipped with clear error logging rather than crashing the entire run:
  - DependencyDriftCollector: Returns `status="not_available"` on malformed dependency_report.json
  - ExecutionHealthCollector: Returns `status="not_available"` on malformed outcome/request/validation.json
  - ValidationHistoryCollector: Returns `status="not_available"` on malformed artifacts
  - LintSignalCollector: Returns empty violations list on malformed ruff.json, continues processing remaining violations
  - CheckSignal and other collectors: Continue processing without interruption on artifact errors

### Verified

- **Zero regressions** — All 3479 existing tests still passing without modification
- **Performance impact:** <10ms per-artifact validation overhead, 101 hardening tests in 0.34s
- **Thread safety:** All validation uses static methods only, fully concurrent-safe
- **Backward compatibility:** All existing APIs unchanged, signal models only add optional `parse_errors` field

### Documentation

- Error handling behavior documented with 8+ examples per error type (File I/O, JSON parse, structure validation)
- Deployment checklist with pre-deployment, production, and rollback procedures
- Release notes with performance impact, testing instructions, and backward compatibility verification
- Known limitations identified for Phase 2 (resource limits, parse timeouts, alert integration)

---
