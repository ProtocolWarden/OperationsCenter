# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Boundary validation tests for evidence contracts (RuleEvidence, EvidenceType)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from operations_center.contracts.enums import EvidenceType
from operations_center.contracts.evidence import RuleEvidence


class TestEvidenceType:
    def test_three_members_present(self):
        values = {m.value for m in EvidenceType}
        assert values == {"changed_files", "validation", "rule"}

    def test_round_trip_from_string(self):
        assert EvidenceType("rule") is EvidenceType.RULE
        assert EvidenceType("changed_files") is EvidenceType.CHANGED_FILES
        assert EvidenceType("validation") is EvidenceType.VALIDATION

    def test_json_serialises_as_string(self):
        data = json.dumps({"kind": EvidenceType.RULE})
        assert '"rule"' in data

    def test_unknown_value_raises(self):
        with pytest.raises(ValueError):
            EvidenceType("unknown_kind")

    def test_is_str_subclass(self):
        assert isinstance(EvidenceType.RULE, str)


class TestRuleEvidence:
    def test_minimal_required_fields(self):
        ev = RuleEvidence(rule_id="R001", kind="policy", matched=True)
        assert ev.rule_id == "R001"
        assert ev.kind == "policy"
        assert ev.matched is True
        assert ev.severity is None
        assert ev.detail is None

    def test_full_construction(self):
        ev = RuleEvidence(
            rule_id="G42",
            kind="guardrail",
            matched=True,
            severity="high",
            detail="Budget ceiling exceeded",
        )
        assert ev.severity == "high"
        assert ev.detail == "Budget ceiling exceeded"

    def test_matched_false_allowed(self):
        ev = RuleEvidence(rule_id="R002", kind="lint", matched=False)
        assert ev.matched is False

    def test_missing_rule_id_raises(self):
        with pytest.raises(ValidationError):
            RuleEvidence(kind="policy", matched=True)  # type: ignore[call-arg]

    def test_missing_kind_raises(self):
        with pytest.raises(ValidationError):
            RuleEvidence(rule_id="R001", matched=True)  # type: ignore[call-arg]

    def test_missing_matched_raises(self):
        with pytest.raises(ValidationError):
            RuleEvidence(rule_id="R001", kind="policy")  # type: ignore[call-arg]

    def test_model_is_frozen(self):
        ev = RuleEvidence(rule_id="R001", kind="policy", matched=True)
        with pytest.raises(Exception):
            ev.rule_id = "mutated"  # type: ignore[misc]

    def test_json_round_trip(self):
        ev = RuleEvidence(
            rule_id="X9",
            kind="guardrail",
            matched=False,
            severity="low",
            detail="minor deviation",
        )
        data = json.loads(ev.model_dump_json())
        restored = RuleEvidence(**data)
        assert restored == ev

    def test_importable_from_contracts_package(self):
        from operations_center.contracts import EvidenceType as ET
        from operations_center.contracts import RuleEvidence as RE

        assert ET.RULE.value == "rule"
        assert RE(rule_id="r", kind="k", matched=False).matched is False

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            RuleEvidence(rule_id="R1", kind="k", matched=True, unexpected_field="x")
