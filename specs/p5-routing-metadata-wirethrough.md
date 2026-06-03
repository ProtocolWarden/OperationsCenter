---
campaign_id: d3a7e1f4-8b2c-4e91-a6d0-3c5f9a8e2b17
slug: p5-routing-metadata-wirethrough
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - contracts
  - routing
  - backends
status: active
created_at: 2026-05-19T00:00:00Z
---

## Overview

SwitchBoard now emits a `metadata` dict on `LaneDecision` (including `worker_backend`), but OC's `OcRoutingDecision` has no `metadata` field. The CxRP mapper silently drops every metadata key except `policy_rule_matched` and `switchboard_version`. This campaign adds a first-class `metadata` field to OC's routing contract and wires `worker_backend` through to the execution coordinator so backend selection can use the SwitchBoard-emitted hint.

## Goals

1. **Add `metadata` field to `OcRoutingDecision`** — Add `metadata: dict[str, str] = Field(default_factory=dict)` to `src/operations_center/contracts/routing.py`. Preserve the existing top-level `switchboard_version` and `policy_rule_matched` fields for backward compatibility; the new `metadata` dict carries pass-through keys that OC does not need to destructure (e.g. `worker_backend`).

2. **Update CxRP mapper to round-trip metadata** — In `src/operations_center/contracts/cxrp_mapper.py`, update `to_cxrp_lane_decision` to merge `oc.metadata` into the outbound CxRP `metadata` dict (existing `policy_rule_matched` / `switchboard_version` entries take precedence). Update `from_cxrp_lane_decision` to populate `OcRoutingDecision.metadata` with all wire metadata keys that are not already destructured into top-level fields.

3. **Surface `worker_backend` in execution coordinator routing block** — In the execution coordinator (or wherever routing provenance is written into `ExecutionRecord.metadata`), include `decision.metadata.get("worker_backend")` so audit consumers and run-memory queries can filter by backend hint. Update `tests/unit/execution/test_coordinator_routing_metadata.py` accordingly.

4. **Add targeted tests for metadata round-trip** — Add or extend tests in `tests/unit/contracts/test_routing.py` and `tests/unit/contracts/test_cxrp_mapper.py` to verify: (a) metadata field defaults to empty dict, (b) to/from CxRP round-trips `worker_backend` and arbitrary extra keys, (c) top-level `switchboard_version` / `policy_rule_matched` still take precedence over metadata dict entries with the same keys.

## Constraints

- Do **not** remove or rename the existing top-level `switchboard_version` or `policy_rule_matched` fields — they are consumed by downstream audit code and must remain first-class.
- The `metadata` field must use `dict[str, str]` (string values only) to stay compatible with CxRP's metadata contract.
- Keep `OcRoutingDecision` frozen (`model_config = {"frozen": True}`); the metadata dict is set at construction time only.
- Do not modify SwitchBoard or CxRP — this campaign is OC-only, consuming what SwitchBoard already emits.
- All changes must pass `ruff check` and existing tests (`pytest tests/unit/contracts tests/unit/routing tests/unit/execution`).

## Success Criteria

- `OcRoutingDecision(proposal_id="x", selected_lane=..., selected_backend=..., metadata={"worker_backend": "claude_code"})` constructs without error and `.metadata["worker_backend"]` returns `"claude_code"`.
- A CxRP wire payload containing `"metadata": {"worker_backend": "codex_cli", "policy_rule_matched": "rule_7", "switchboard_version": "0.4.1"}` round-trips through `from_cxrp_lane_decision` → `to_cxrp_lane_decision` preserving all three keys.
- `ExecutionRecord.metadata["routing"]` includes `"worker_backend"` when the decision carries one.
- `pytest tests/unit/contracts tests/unit/routing tests/unit/execution` passes with zero failures and no new warnings.
