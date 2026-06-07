# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest fixtures for .console/ reconciliation integration tests.

Provides convenient access to fixture repositories for testing R1/R2 detectors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.console_malformed import FIXTURES


# Auto-generate pytest fixtures for each fixture repository.
# T4 exclusion: dynamic fixture generation — fixture names are resolved at
# runtime via FIXTURES dict; static analysis cannot see them as "requested".
for _fixture_name, _fixture_path in FIXTURES.items():

    def _make_fixture(_name: str, _path: Path):
        @pytest.fixture(name=_name)
        def _generated() -> Path:
            """Provide access to a console fixture repository."""
            assert _path.exists(), f"Fixture not found: {_path}"
            return _path

        return _generated

    # Register the fixture with pytest
    globals()[_fixture_name] = _make_fixture(_fixture_name, _fixture_path)
