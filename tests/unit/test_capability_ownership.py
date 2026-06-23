# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the synchronous capability-owner check (Phase C2, surface 1).

The registry is dormant-by-environment in OC today, so these exercise the
mechanism against a fake registry plus the degrade/no-op paths that govern its
real behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from operations_center.capability_ownership import (
    AmbiguousOwnerError,
    resolve_owner,
    verify_owner_or_degrade,
)


@dataclass
class _Edge:
    source_id: str
    target_id: str
    kind: str  # "owns" | "targets" | ...


class _Registry:
    def __init__(self, edges):
        self.edges = edges


def _registry(*edges):
    return _Registry([_Edge(*e) for e in edges])


def test_resolve_single_owner():
    reg = _registry(("board_unblock", "OperationsCenter", "owns"))
    assert resolve_owner(reg, "board_unblock") == "OperationsCenter"


def test_resolve_ignores_non_owns_edges():
    reg = _registry(
        ("board_unblock", "OperationsCenter", "owns"),
        ("board_unblock", "RepoGraph", "targets"),
    )
    assert resolve_owner(reg, "board_unblock") == "OperationsCenter"


def test_zero_owners_raises():
    reg = _registry(("board_unblock", "X", "targets"))
    with pytest.raises(AmbiguousOwnerError):
        resolve_owner(reg, "board_unblock")


def test_multiple_owners_raises():
    reg = _registry(
        ("board_unblock", "OperationsCenter", "owns"),
        ("board_unblock", "Rogue", "owns"),
    )
    with pytest.raises(AmbiguousOwnerError):
        resolve_owner(reg, "board_unblock")


# ── the guard ──────────────────────────────────────────────────────────────────


def test_disabled_is_noop():
    # required=False → proceed without even consulting a registry
    assert verify_owner_or_degrade("board_unblock", required=False, registry=None) is True


def test_unavailable_registry_degrades(monkeypatch):
    # required + no registry available → DEGRADE (proceed), never halt
    monkeypatch.setattr(
        "operations_center.capability_ownership.load_capability_registry", lambda: None
    )
    assert verify_owner_or_degrade("board_unblock", required=True) is True


def test_available_single_owner_proceeds():
    reg = _registry(("board_unblock", "OperationsCenter", "owns"))
    assert verify_owner_or_degrade("board_unblock", required=True, registry=reg) is True


def test_available_ambiguous_owner_refuses():
    reg = _registry(
        ("board_unblock", "OperationsCenter", "owns"),
        ("board_unblock", "Rogue", "owns"),
    )
    assert verify_owner_or_degrade("board_unblock", required=True, registry=reg) is False


def test_owner_mismatch_refuses():
    reg = _registry(("board_unblock", "SomeoneElse", "owns"))
    assert (
        verify_owner_or_degrade(
            "board_unblock", required=True, expected_owner="OperationsCenter", registry=reg
        )
        is False
    )


def test_owner_match_proceeds():
    reg = _registry(("board_unblock", "OperationsCenter", "owns"))
    assert (
        verify_owner_or_degrade(
            "board_unblock", required=True, expected_owner="OperationsCenter", registry=reg
        )
        is True
    )


def test_load_returns_none_in_this_environment():
    # OC's pinned repograph wheel has no capabilities plane → None (dormant).
    from operations_center.capability_ownership import load_capability_registry

    assert load_capability_registry() is None
