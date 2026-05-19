---
campaign_id: f83c2e19-47a1-4d8b-b6c5-1e0d9a37f5b2
slug: proposer-subsystem-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - proposer
  - tests/unit/proposer
status: active
created_at: 2026-05-18T21:00:00Z
---

## Overview

The `proposer/` package (9 modules, 39 functions, 22 classes) owns proposal generation, backlog promotion, guardrail enforcement, and rejection tracking — yet has zero unit tests. Every public class is pure-data or takes protocol-injected dependencies, making them testable without I/O. This campaign adds focused unit tests for the four functional layers: data models, promotion logic, guardrail/rejection, and candidate integration.

## Goals

1. **Add unit tests for `backlog_promoter.py` (priority — highest complexity):** Cover `BacklogPromoterService.promote()` end-to-end with a stub `PlaneClientProtocol`. Test: tier parsing from labels (`_parse_source_family`, `_family_from_labels`, `_parse_recorded_tier`), promotion eligibility filtering, `PromotedTask` vs `SkippedTask` result shapes, and the empty-backlog no-op path. Place tests in `tests/unit/proposer/test_backlog_promoter.py`.

2. **Add unit tests for `candidate_mapper.py` and `candidate_loader.py`:** Cover `ProposalCandidateMapper.map()` producing correct `PlaneTaskDraft` fields from a `ProposalCandidate`, and `ProposalCandidateLoader.load()` reading artifact files. Test: mapping with all fields populated, mapping with optional fields absent, loader with missing/corrupt artifact file.

3. **Add unit tests for `guardrail_adapter.py` and `rejection_store.py`:** Cover `ProposerGuardrailAdapter` returning `GuardrailResult.blocked` vs `.allowed` based on policy violations, and `ProposalRejectionStore` persisting and retrieving rejection records. Test: multiple guardrail violation types, rejection dedup, and store round-trip.

4. **Add unit tests for `provenance.py`, `result_models.py`, and `artifact_writer.py`:** Cover `build_provenance()` constructing correct `ProposalProvenance` from inputs, Pydantic validation on `CreatedProposalResult`/`SkippedProposalResult`/`FailedProposalResult`, and `ProposerArtifactWriter` serialization. Test: provenance with full/partial inputs, result model required-field enforcement, artifact round-trip.

## Constraints

- Tests must be pure unit tests — no filesystem I/O, no network, no subprocess. Use `tmp_path` for store tests, protocol stubs for Plane client.
- Do not modify any production code in `proposer/`. If a class is hard to test due to coupling, note it as a follow-up refactor in the test file docstring.
- Follow existing test conventions: `tests/unit/proposer/` directory, `test_<module>.py` naming, `pytest` style (no `unittest.TestCase`).
- The `candidate_integration.py` module depends on `DecisionEngineService` — stub it via `CandidateLoaderProtocol` rather than importing the real decision layer.

## Success Criteria

- `tests/unit/proposer/` exists with ≥4 test files covering all 4 goals.
- Every public class and function in `proposer/` has at least one positive-path and one negative/edge-case test.
- Full test suite (`pytest tests/unit/proposer/`) passes with 0 failures.
- No regression in the existing 3350-test suite.