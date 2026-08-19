# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The factory must choose a board backend deliberately, and never by accident.

`make_board_client` is the one place a concrete board is named. Plane was
removed at the 2026-08-18 cutover, so the properties worth pinning changed:
the default is now Forgejo, an unconfigured Forgejo must fail rather than fall
back to anything, and a config still naming the retired backend must be told
so plainly instead of being reported as a typo.
"""

from __future__ import annotations

import pytest

from operations_center.adapters.board import make_board_client


class _Forgejo:
    base_url = "http://forge.local"
    owner = "protocolwarden"
    repo = "board"


class _Settings:
    """Minimal stand-in — the factory only touches these attributes."""

    _UNSET = object()

    def __init__(self, backend="forgejo", forgejo=_UNSET):
        self.board_backend = backend
        # `None` must stay None — it is the case under test. Defaulting it away
        # made test_forgejo_without_config assert against a configured board.
        self.forgejo = _Forgejo() if forgejo is _Settings._UNSET else forgejo

    def forgejo_token(self):
        return "forge-tok"


def test_defaults_to_forgejo():
    from operations_center.adapters.forgejo import ForgejoClient

    class _NoBackendField:
        forgejo = _Forgejo()

        def forgejo_token(self):
            return "forge-tok"

    client = make_board_client(_NoBackendField())
    assert isinstance(client, ForgejoClient)
    client.close()


def test_selects_forgejo_when_asked():
    from operations_center.adapters.forgejo import ForgejoClient

    client = make_board_client(_Settings(backend="forgejo", forgejo=_Forgejo()))
    assert isinstance(client, ForgejoClient)
    assert client.owner == "protocolwarden"
    assert client.repo == "board"
    client.close()


def test_forgejo_without_config_fails_rather_than_falling_back():
    """A board the fleet cannot reach must be an error, not an empty queue."""
    with pytest.raises(RuntimeError, match="no `forgejo:` settings block"):
        make_board_client(_Settings(backend="forgejo", forgejo=None))


def test_unknown_backend_is_refused():
    """A typo must not resolve to a working board."""
    with pytest.raises(RuntimeError, match="unknown board_backend"):
        make_board_client(_Settings(backend="gitea"))


def test_the_retired_backend_says_it_was_removed():
    """An old config asking for Plane gets the reason, not "unknown"."""
    with pytest.raises(RuntimeError, match="was removed"):
        make_board_client(_Settings(backend="plane"))


def test_settings_model_accepts_a_forgejo_block():
    """The real Settings model, not the stub — the config shape must round-trip."""
    from operations_center.config.settings import ForgejoSettings

    cfg = ForgejoSettings(
        base_url="http://forge.local",
        api_token_env="FORGEJO_API_TOKEN",
        owner="protocolwarden",
        repo="board",
    )
    assert cfg.owner == "protocolwarden"


def test_settings_default_backend_is_forgejo():
    from operations_center.config.settings import Settings

    fields = Settings.model_fields
    assert "board_backend" in fields
    assert "forgejo" in fields
    assert fields["board_backend"].default == "forgejo"
    assert "plane" not in fields, "the retired backend is still a settings field"
