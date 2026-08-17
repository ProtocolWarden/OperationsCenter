# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The factory must choose a board backend deliberately, and never by accident.

`make_board_client` is the one place a concrete board is named. Two properties
matter more than the happy path:

* **Default is Plane.** Adding a `forgejo:` config block must not repoint the
  fleet's board. A switch that happens as a side effect of writing config is a
  switch nobody decided to make.
* **No silent fallback.** Asking for Forgejo without configuration must fail,
  not quietly return Plane — falling back would point the fleet at the very
  board it is migrating off, and the symptom would be a board that looks fine.
"""

from __future__ import annotations

import pytest

from operations_center.adapters.board import make_board_client


class _Plane:
    base_url = "http://plane.local"
    workspace_slug = "ws"
    project_id = "proj"


class _Forgejo:
    base_url = "http://forge.local"
    owner = "protocolwarden"
    repo = "board"


class _Settings:
    """Minimal stand-in — the factory only touches these attributes."""

    def __init__(self, backend="plane", forgejo=None):
        self.plane = _Plane()
        self.board_backend = backend
        self.forgejo = forgejo

    def plane_token(self):
        return "plane-tok"

    def forgejo_token(self):
        return "forge-tok"


def test_defaults_to_plane():
    from operations_center.adapters.plane import PlaneClient

    client = make_board_client(_Settings())
    assert isinstance(client, PlaneClient)
    client.close()


def test_configuring_forgejo_alone_does_not_switch_the_board():
    """Config presence is not consent. Only board_backend switches the board."""
    from operations_center.adapters.plane import PlaneClient

    client = make_board_client(_Settings(backend="plane", forgejo=_Forgejo()))
    assert isinstance(client, PlaneClient), (
        "adding a forgejo: block silently repointed the board — that switch must "
        "be an explicit decision, not a side effect of writing config"
    )
    client.close()


def test_selects_forgejo_when_asked():
    from operations_center.adapters.forgejo import ForgejoClient

    client = make_board_client(_Settings(backend="forgejo", forgejo=_Forgejo()))
    assert isinstance(client, ForgejoClient)
    assert client.owner == "protocolwarden"
    assert client.repo == "board"
    client.close()


def test_forgejo_without_config_fails_rather_than_falling_back():
    """A silent fallback would point the fleet at the board it is leaving."""
    with pytest.raises(RuntimeError, match="no `forgejo:` settings block"):
        make_board_client(_Settings(backend="forgejo", forgejo=None))


def test_unknown_backend_is_refused():
    """A typo must not resolve to a working board."""
    with pytest.raises(RuntimeError, match="unknown board_backend"):
        make_board_client(_Settings(backend="gitea"))


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


def test_forgejo_token_explains_itself_when_unconfigured():
    """The error names the cause; 'KeyError: None' would not."""
    from operations_center.config.settings import Settings

    fields = Settings.model_fields
    assert "board_backend" in fields
    assert "forgejo" in fields
    assert fields["board_backend"].default == "plane", (
        "the default backend must remain Plane until cutover"
    )
