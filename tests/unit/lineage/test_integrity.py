# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the lineage integrity stack (Phase D1, surface 9).

Pins the two properties that let a lineage edge ever become steerable: a
tamper-evident per-lineage hash chain, and authorship binding that rejects a
lane writing another lane's lineage.
"""

from __future__ import annotations

import pytest

from operations_center.lineage.integrity import (
    AuthorshipError,
    LineageLedger,
    chained_trust,
    entry_hash,
)
from operations_center.lineage.models import Integrity, default_trust
from operations_center.lineage.models import Provenance


def test_chain_links_and_verifies():
    led = LineageLedger()
    led.append("lin-a", "goal-lane", {"step": "proposed"})
    led.append("lin-a", "goal-lane", {"step": "executed"})
    assert led.verify()
    # second entry commits to the first
    assert led.entries[1].prior_hash == led.entries[0].this_hash


def test_independent_lineages_chain_separately():
    led = LineageLedger()
    a = led.append("lin-a", "goal-lane", {"x": 1})
    b = led.append("lin-b", "test-lane", {"y": 2})
    # lin-b starts from genesis, not from lin-a's tip
    assert b.prior_hash == "0" * 64
    assert a.this_hash != b.this_hash
    assert led.verify()


def test_authorship_binding_rejects_foreign_writer():
    led = LineageLedger()
    led.append("lin-a", "goal-lane", {"x": 1})
    with pytest.raises(AuthorshipError):
        led.append("lin-a", "ATTACKER-lane", {"x": "forged"})
    # the forged write is quarantined, not chained
    assert led.quarantined and led.quarantined[0]["author"] == "ATTACKER-lane"
    assert all(e.author == "goal-lane" for e in led.entries)
    assert led.verify()


def test_owner_is_first_writer():
    led = LineageLedger()
    led.append("lin-z", "spec-lane", {})
    assert led.owner_of("lin-z") == "spec-lane"


def test_tamper_is_detected():
    led = LineageLedger()
    led.append("lin-a", "goal-lane", {"step": "one"})
    led.append("lin-a", "goal-lane", {"step": "two"})
    # mutate a stored payload after the fact → chain must fail
    led.entries[0].payload["step"] = "tampered"
    assert not led.verify()


def test_entry_hash_is_deterministic():
    h1 = entry_hash("0" * 64, lineage_id="l", author="a", payload={"k": 1})
    h2 = entry_hash("0" * 64, lineage_id="l", author="a", payload={"k": 1})
    assert h1 == h2
    h3 = entry_hash("0" * 64, lineage_id="l", author="a", payload={"k": 2})
    assert h1 != h3


def test_chained_trust_only_upgrades_integrity():
    base = default_trust(provenance=Provenance.CODE_COMPUTED)
    assert base.integrity is Integrity.UNVERIFIED
    up = chained_trust(base)
    assert up.integrity is Integrity.CHAINED
    # other dimensions unchanged (still not steerable until order is causal)
    assert up.provenance is base.provenance
    assert up.completeness is base.completeness
    assert up.order is base.order
    assert not up.is_steerable()  # order still host-relative
