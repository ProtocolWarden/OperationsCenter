# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Complementary coverage tests for the per-backend artifact loaders.

These exercise branches not covered by ``test_artifacts.py``: empty/missing
files, type-shape rejections, optional-field defaults and full happy-path
construction for each loader.
"""

from __future__ import annotations

import pytest

from operations_center.executors._artifacts import (
    AuditArtifactError,
    AuditOutcome,
    AuditVerdict,
    CapabilityCard,
    ContractGap,
    GapStatus,
    PhaseClassification,
    RuntimeSupportCard,
    load_audit_verdict,
    load_capability_card,
    load_contract_gaps,
    load_runtime_support,
)

_FULL_PHASES = (
    "per_phase:\n"
    "  runtime_control: PASS\n"
    "  capability_control: PARTIAL\n"
    "  drift_detection: FAIL\n"
    "  failure_observability: PASS\n"
    "  internal_routing: 'N/A'\n"
)


# ── load_contract_gaps ──────────────────────────────────────────────────


class TestLoadContractGaps:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_contract_gaps(tmp_path / "nope.yaml") == []

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text("")
        assert load_contract_gaps(p) == []

    def test_top_level_not_a_list(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text("id: g\n")
        with pytest.raises(AuditArtifactError, match="top-level must be a list"):
            load_contract_gaps(p)

    def test_entry_not_a_dict(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text("- just_a_string\n")
        with pytest.raises(AuditArtifactError, match="each gap must be a dict"):
            load_contract_gaps(p)

    def test_missing_required_fields(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text("- id: g\n  gap: x\n")
        with pytest.raises(AuditArtifactError, match="missing fields"):
            load_contract_gaps(p)

    def test_full_gap_with_patch_deadline(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text(
            "- id: g1\n"
            "  gap: missing thing\n"
            "  discovered_at: 2026-01-01\n"
            "  backend_version: 1.2.3\n"
            "  impact: high\n"
            "  workaround: none\n"
            "  fork_threshold: never\n"
            "  status: patched_upstream\n"
            "  patch_deadline: 2026-12-31\n"
        )
        gaps = load_contract_gaps(p)
        assert len(gaps) == 1
        g = gaps[0]
        assert isinstance(g, ContractGap)
        assert g.id == "g1"
        assert g.backend_version == "1.2.3"
        assert g.status is GapStatus.PATCHED_UPSTREAM
        assert g.patch_deadline == "2026-12-31"

    def test_defaults_backend_version_and_no_deadline(self, tmp_path):
        # backend_version omitted -> "unknown"; patch_deadline absent -> None;
        # also covers the falsy patch_deadline branch (empty string).
        p = tmp_path / "g.yaml"
        p.write_text(
            "- id: g2\n"
            "  gap: x\n"
            "  discovered_at: t\n"
            "  impact: i\n"
            "  workaround: w\n"
            "  fork_threshold: f\n"
            "  status: open\n"
            "  patch_deadline: ''\n"
        )
        (g,) = load_contract_gaps(p)
        assert g.backend_version == "unknown"
        assert g.patch_deadline is None
        assert g.status is GapStatus.OPEN


# ── load_capability_card ────────────────────────────────────────────────


class TestLoadCapabilityCard:
    def test_empty_file_yields_defaults(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text("")
        card = load_capability_card(p)
        assert isinstance(card, CapabilityCard)
        assert card.backend_id == ""
        assert card.backend_version == "unknown"
        assert card.advertised_capabilities == []
        assert card.measured_constraints == {}
        assert card.known_capability_gaps == []

    def test_not_a_dict(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text("- a\n- b\n")
        with pytest.raises(AuditArtifactError, match="must be a dict"):
            load_capability_card(p)

    def test_full_valid_card(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text(
            "backend_id: my_backend\n"
            "backend_version: 9.9\n"
            "advertised_capabilities: [repo_patch, network_access]\n"
            "measured_constraints:\n  max_tokens: 100\n"
            "known_capability_gaps: [some_gap]\n"
        )
        card = load_capability_card(p)
        assert card.backend_id == "my_backend"
        assert card.backend_version == "9.9"
        assert "repo_patch" in card.advertised_capabilities
        assert card.measured_constraints == {"max_tokens": 100}
        assert card.known_capability_gaps == ["some_gap"]


# ── load_runtime_support ────────────────────────────────────────────────


class TestLoadRuntimeSupport:
    def test_empty_file_yields_defaults(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("")
        rs = load_runtime_support(p)
        assert isinstance(rs, RuntimeSupportCard)
        assert rs.backend_id == ""
        assert rs.backend_version == "unknown"
        assert rs.supported_runtime_kinds == []
        assert rs.supported_selection_modes == []
        assert rs.known_runtime_gaps == []

    def test_not_a_dict(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("- a\n")
        with pytest.raises(AuditArtifactError, match="must be a dict"):
            load_runtime_support(p)

    def test_full_valid_card(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(
            "backend_id: rb\n"
            "backend_version: 2.0\n"
            "supported_runtime_kinds: [cli_subscription, backend_default]\n"
            "supported_selection_modes: [explicit_request, backend_default]\n"
            "known_runtime_gaps: [g]\n"
        )
        rs = load_runtime_support(p)
        assert rs.backend_id == "rb"
        assert rs.backend_version == "2.0"
        assert "cli_subscription" in rs.supported_runtime_kinds
        assert "explicit_request" in rs.supported_selection_modes
        assert rs.known_runtime_gaps == ["g"]


# ── load_audit_verdict ──────────────────────────────────────────────────


class TestLoadAuditVerdict:
    def test_empty_file_missing_phase(self, tmp_path):
        # Empty -> per_phase {} -> first required phase missing.
        p = tmp_path / "v.yaml"
        p.write_text("")
        with pytest.raises(AuditArtifactError, match="missing required phase"):
            load_audit_verdict(p)

    def test_not_a_dict(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text("- a\n")
        with pytest.raises(AuditArtifactError, match="must be a dict"):
            load_audit_verdict(p)

    def test_per_phase_not_a_dict(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text("outcome: adapter_only\nper_phase:\n  - a\n  - b\n")
        with pytest.raises(AuditArtifactError, match="per_phase must be a dict"):
            load_audit_verdict(p)

    def test_missing_outcome_key(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text(_FULL_PHASES)
        with pytest.raises(AuditArtifactError, match="invalid or missing outcome"):
            load_audit_verdict(p)

    def test_invalid_outcome_value(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text("outcome: teleport\n" + _FULL_PHASES)
        with pytest.raises(AuditArtifactError, match="invalid or missing outcome"):
            load_audit_verdict(p)

    def test_full_valid_verdict_with_review(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text(
            "backend_id: vb\n"
            "audited_at: 2026-01-02\n"
            "audited_against_cxrp_version: '0.5'\n"
            "backend_version: 3.1\n"
            "outcome: fork_required\n"
            "gap_refs: [g1, g2]\n"
            "next_review_by: 2026-06-01\n" + _FULL_PHASES
        )
        v = load_audit_verdict(p)
        assert isinstance(v, AuditVerdict)
        assert v.backend_id == "vb"
        assert v.audited_at == "2026-01-02"
        assert v.audited_against_cxrp_version == "0.5"
        assert v.backend_version == "3.1"
        assert v.outcome is AuditOutcome.FORK_REQUIRED
        assert v.gap_refs == ["g1", "g2"]
        assert v.next_review_by == "2026-06-01"
        assert v.per_phase["runtime_control"] is PhaseClassification.PASS
        assert v.per_phase["drift_detection"] is PhaseClassification.FAIL
        assert v.per_phase["internal_routing"] is PhaseClassification.NA

    def test_defaults_and_no_next_review(self, tmp_path):
        # Minimal: omit metadata to hit default branches; next_review_by absent -> None.
        p = tmp_path / "v.yaml"
        p.write_text("outcome: adapter_only\n" + _FULL_PHASES)
        v = load_audit_verdict(p)
        assert v.backend_id == ""
        assert v.audited_at == ""
        assert v.audited_against_cxrp_version == ""
        assert v.backend_version == "unknown"
        assert v.gap_refs == []
        assert v.next_review_by is None
        assert v.outcome is AuditOutcome.ADAPTER_ONLY
