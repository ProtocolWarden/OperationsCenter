# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest fixtures for .console/ reconciliation integration tests.

Provides convenient access to fixture repositories for testing R1/R2 detectors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.console_malformed import FIXTURES


@pytest.fixture
def console_fixture_dir(request: pytest.FixtureRequest) -> Path:
    """Provide access to a console fixture directory.

    Usage: request a fixture parameter matching a fixture name:
        def test_r1_detector(fixture_r1_missing_console_dir):
            # fixture_r1_missing_console_dir is the Path to that fixture
            assert not (fixture_r1_missing_console_dir / ".console").exists()
    """
    # This is a helper; actual fixtures are auto-generated below


# Auto-generate pytest fixtures for each fixture repository
for _fixture_name, _fixture_path in FIXTURES.items():

    def _make_fixture(_name: str, _path: Path):
        @pytest.fixture(name=_name)
        def _fixture() -> Path:
            """Provide access to a console fixture repository."""
            assert _path.exists(), f"Fixture not found: {_path}"
            return _path

        return _fixture

    # Register the fixture with pytest
    globals()[_fixture_name] = _make_fixture(_fixture_name, _fixture_path)
