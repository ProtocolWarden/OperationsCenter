# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the synchronous capability-owner check (Phase C2, surface 1).

Most cases exercise the mechanism against a fake registry plus the degrade/no-op
paths that govern its behavior. With the capabilities-plane pin now in OC's deps
(plane-bearing platform-manifest + override-pinned plane-bearing repograph), the
guard is LIVE: ``TestLiveRegistryActivation`` loads the REAL registry and asserts
board_unblock proceeds against it (skipped where the plane is absent, e.g. an
unbumped venv, so the activation contract is checked where it can be and never
false-fails where it can't).
"""

from __future__ import annotations

import types
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


def test_owner_match_is_convention_insensitive():
    # The registry uses RepoGraph's repo_id casing (operations_center); OC's gate
    # passes self_repo_key ("OperationsCenter"). Same repo — the gate must NOT refuse
    # on the casing/separator difference (else require_capability_owner halts
    # board_unblock every cycle).
    reg = _registry(("board_unblock", "operations_center", "owns"))
    assert (
        verify_owner_or_degrade(
            "board_unblock", required=True, expected_owner="OperationsCenter", registry=reg
        )
        is True
    )


def test_owner_normalization_does_not_overmatch():
    # A genuinely different repo still refuses — normalization collapses convention,
    # not identity.
    reg = _registry(("board_unblock", "Custodian", "owns"))
    assert (
        verify_owner_or_degrade(
            "board_unblock", required=True, expected_owner="OperationsCenter", registry=reg
        )
        is False
    )


def test_load_activates_when_loader_present(monkeypatch):
    # Activation contract: if an importable module exposes load_capability_registry,
    # the guard USES it and leaves dormancy — guards against the rot where the probe
    # can never see the plane (asserting a degenerate "None forever" would mask that).
    from operations_center import capability_ownership

    sentinel = object()
    fake = types.SimpleNamespace(load_capability_registry=lambda: sentinel)
    monkeypatch.setattr("importlib.import_module", lambda name: fake)
    assert capability_ownership.load_capability_registry() is sentinel


def test_load_dormant_when_loader_absent(monkeypatch):
    # The other half of the contract: an importable module WITHOUT the loader symbol
    # degrades to None (dormant) — which is the real state in OC's env today.
    from operations_center import capability_ownership

    fake = types.SimpleNamespace()  # no load_capability_registry attribute
    monkeypatch.setattr("importlib.import_module", lambda name: fake)
    assert capability_ownership.load_capability_registry() is None


def test_load_dormant_when_module_absent(monkeypatch):
    # repograph not importable at all → None (never raises into the caller).
    from operations_center import capability_ownership

    def _raise(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("importlib.import_module", _raise)
    assert capability_ownership.load_capability_registry() is None


# ── probe order: platform_manifest.capabilities first, repograph fallback ────────
#
# The real registry ships through platform_manifest.capabilities.load_capabilities,
# not bare repograph. The probe tries that surface FIRST (fail-open) and falls back.
# Live activation requires the operator to ship the plane into OC's deps; these tests
# pin the activation contract so it cannot silently rot to "None forever".


def test_both_surfaces_absent_returns_none(monkeypatch):
    # Neither platform_manifest.capabilities nor repograph's loader present (today's
    # real env) → None, so the guard degrades.
    from operations_center import capability_ownership

    def _raise(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("importlib.import_module", _raise)
    assert capability_ownership.load_capability_registry() is None


def test_uses_platform_manifest_capabilities_first(monkeypatch):
    # When platform_manifest.capabilities.load_capabilities yields a registry with
    # .edges, the probe USES it (and never consults repograph).
    from operations_center import capability_ownership

    sentinel_reg = types.SimpleNamespace(edges=[])
    cap_mod = types.SimpleNamespace(load_capabilities=lambda: sentinel_reg)

    def _import(name):
        if name == "platform_manifest.capabilities":
            return cap_mod
        raise AssertionError(f"repograph fallback should not be reached, got {name!r}")

    monkeypatch.setattr("importlib.import_module", _import)
    assert capability_ownership.load_capability_registry() is sentinel_reg


def test_platform_manifest_present_but_no_edges_falls_back(monkeypatch):
    # A load_capabilities that returns a non-registry (no .edges) is not trusted; the
    # probe degrades past it to the repograph fallback (which is also absent → None).
    from operations_center import capability_ownership

    cap_mod = types.SimpleNamespace(load_capabilities=lambda: object())

    def _import(name):
        if name == "platform_manifest.capabilities":
            return cap_mod
        raise ModuleNotFoundError(name)  # repograph fallback absent

    monkeypatch.setattr("importlib.import_module", _import)
    assert capability_ownership.load_capability_registry() is None


def test_platform_manifest_load_raises_falls_back(monkeypatch):
    # load_capabilities raising must not propagate — degrade to the fallback (absent).
    from operations_center import capability_ownership

    def _boom():
        raise RuntimeError("registry build failed")

    cap_mod = types.SimpleNamespace(load_capabilities=_boom)

    def _import(name):
        if name == "platform_manifest.capabilities":
            return cap_mod
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("importlib.import_module", _import)
    assert capability_ownership.load_capability_registry() is None


def test_falls_back_to_repograph_when_plane_absent(monkeypatch):
    # platform_manifest.capabilities absent but legacy repograph loader present →
    # the fallback path still activates the guard.
    from operations_center import capability_ownership

    sentinel = object()
    repograph_mod = types.SimpleNamespace(load_capability_registry=lambda: sentinel)

    def _import(name):
        if name == "platform_manifest.capabilities":
            raise ModuleNotFoundError(name)
        if name == "repograph":
            return repograph_mod
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("importlib.import_module", _import)
    assert capability_ownership.load_capability_registry() is sentinel


def test_resolves_owner_from_live_platform_manifest_registry(monkeypatch):
    # End-to-end: a real-shaped registry from the platform_manifest surface flows
    # through verify_owner_or_degrade and the single-owner resolves → proceed.

    reg = _registry(("board_unblock", "OperationsCenter", "owns"))
    cap_mod = types.SimpleNamespace(load_capabilities=lambda: reg)

    def _import(name):
        if name == "platform_manifest.capabilities":
            return cap_mod
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("importlib.import_module", _import)
    assert (
        verify_owner_or_degrade(
            "board_unblock", required=True, expected_owner="OperationsCenter"
        )
        is True
    )


# ── live activation: the REAL plane is pinned into OC's deps ─────────────────────
#
# These do NOT monkeypatch the loader — they exercise the registry that actually
# ships in OC's environment via platform_manifest.capabilities. Skipped (not
# failed) where the plane is absent, so the suite stays green on an unbumped venv
# while still pinning the live activation where the plane is present.


def _live_registry():
    from operations_center.capability_ownership import load_capability_registry

    return load_capability_registry()


class TestLiveRegistryActivation:
    def test_real_registry_loads_with_edges(self):
        reg = _live_registry()
        if reg is None:
            pytest.skip("capabilities plane not installed in this environment")
        assert hasattr(reg, "edges")
        assert len(reg.edges) > 0

    def test_board_unblock_proceeds_against_real_registry(self):
        # The exact gate the live board_unblock self-heal lane runs:
        # action_id='board_unblock', expected_owner=self_repo_key ('OperationsCenter').
        # Must PROCEED (the registry owns board_unblock as operations_center, which
        # matches convention-insensitively).
        reg = _live_registry()
        if reg is None:
            pytest.skip("capabilities plane not installed in this environment")
        assert (
            verify_owner_or_degrade(
                "board_unblock", required=True, expected_owner="OperationsCenter"
            )
            is True
        )

    def test_board_unblock_owner_is_operations_center(self):
        reg = _live_registry()
        if reg is None:
            pytest.skip("capabilities plane not installed in this environment")
        # Convention-insensitive: registry uses repo_id 'operations_center'.
        from operations_center.capability_ownership import _norm_owner

        assert _norm_owner(resolve_owner(reg, "board_unblock")) == _norm_owner(
            "OperationsCenter"
        )

    def test_wrong_owner_refuses_against_real_registry(self):
        # The gate is genuinely load-bearing, not a no-op: a different expected
        # owner must REFUSE even against the real registry.
        reg = _live_registry()
        if reg is None:
            pytest.skip("capabilities plane not installed in this environment")
        assert (
            verify_owner_or_degrade(
                "board_unblock", required=True, expected_owner="Custodian"
            )
            is False
        )


# ── enforcement default: require_capability_owner is now ON ──────────────────────


def test_require_capability_owner_default_is_true():
    # The capabilities plane is pinned into OC's deps, so the synchronous owner
    # check is enabled by default. Fail-open by construction: an unavailable
    # registry still degrades (proceeds), so a True default cannot deadlock the
    # board_unblock lane. Asserted via the declared field default (Settings has
    # required plane/git fields, so we check the default without instantiating).
    from operations_center.config.settings import Settings

    assert Settings.model_fields["require_capability_owner"].default is True


def test_require_capability_owner_true_on_constructed_settings():
    # Belt-and-suspenders: a fully constructed Settings carries the True default.
    from operations_center.config.settings import GitSettings, PlaneSettings, Settings

    s = Settings(
        plane=PlaneSettings(
            base_url="http://x",
            api_token_env="T",
            workspace_slug="w",
            project_id="p",
        ),
        git=GitSettings(),
    )
    assert s.require_capability_owner is True
