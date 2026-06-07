# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Fixture repositories for .console/ reconciliation testing.

This package contains 7 fixture repositories with various malformed .console/
configurations to test R1 (presence validator) and R2 (budget/structure validator)
detectors across all violation categories.

See README.md for detailed fixture descriptions.
"""

from __future__ import annotations

from pathlib import Path

# Directory containing all fixture repositories
FIXTURES_DIR = Path(__file__).parent

# Fixture registry: maps fixture names to their paths
FIXTURES = {
    "r1_missing_console_dir": FIXTURES_DIR / "fixture_r1_missing_console_dir",
    "r1_console_is_file": FIXTURES_DIR / "fixture_r1_console_is_file",
    "r1_missing_task_md": FIXTURES_DIR / "fixture_r1_missing_task_md",
    "r1_missing_workers_yaml": FIXTURES_DIR / "fixture_r1_missing_workers_yaml",
    "r2_oversized_task_md": FIXTURES_DIR / "fixture_r2_oversized_task_md",
    "r2_missing_task_section": FIXTURES_DIR / "fixture_r2_missing_task_section",
    "r2_invalid_workers_yaml": FIXTURES_DIR / "fixture_r2_invalid_workers_yaml",
}


def get_fixture_path(fixture_name: str) -> Path:
    """Get the path to a fixture repository by name.

    Args:
        fixture_name: Name of the fixture (key in FIXTURES dict)

    Returns:
        Path to the fixture repository

    Raises:
        KeyError: If fixture_name is not in FIXTURES
    """
    if fixture_name not in FIXTURES:
        raise KeyError(f"Unknown fixture: {fixture_name}. Available: {sorted(FIXTURES.keys())}")
    return FIXTURES[fixture_name]


def list_fixtures() -> list[str]:
    """List all available fixture names."""
    return sorted(FIXTURES.keys())


__all__ = ["FIXTURES_DIR", "FIXTURES", "get_fixture_path", "list_fixtures"]
