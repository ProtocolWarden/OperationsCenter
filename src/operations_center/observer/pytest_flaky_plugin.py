# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest plugin for flaky test detection (minimal stub)."""


def pytest_configure(config):
    """Pytest hook: register flaky test marker."""
    config.addinivalue_line("markers", "flaky: mark test as flaky (may fail intermittently)")
