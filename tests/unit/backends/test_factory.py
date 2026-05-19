# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for canonical backend factory cutover behavior."""

from __future__ import annotations

from types import SimpleNamespace

from operations_center.backends import factory as backend_factory


def test_canonical_registry_does_not_thread_switchboard_proxy_transport(monkeypatch) -> None:
    captured: list[tuple[str, object]] = []

    class FakeDirectLocalAdapter:
        def __init__(self, settings) -> None:
            captured.append(("direct_local", settings))

    class FakeAiderLocalAdapter:
        def __init__(self, settings) -> None:
            captured.append(("aider_local", settings))

    monkeypatch.setattr(backend_factory, "DirectLocalBackendAdapter", FakeDirectLocalAdapter)
    monkeypatch.setattr(backend_factory, "AiderLocalBackendAdapter", FakeAiderLocalAdapter)

    settings = SimpleNamespace(
        team_executor=object(),
        aider=object(),
        aider_local=object(),
        spec_director=SimpleNamespace(switchboard_url="http://sb:20401"),
    )

    backend_factory.CanonicalBackendRegistry.from_settings(settings)

    assert ("direct_local", settings.aider) in captured
    assert ("aider_local", settings.aider_local) in captured
