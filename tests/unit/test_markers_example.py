# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Example tests demonstrating pytest markers for smoke and slow tests."""
import pytest

from operations_center.contracts.enums import ExecutionMode


@pytest.mark.smoke
def test_smoke_execution_mode_enum():
    """Smoke: ExecutionMode enum is importable and has expected members."""
    assert ExecutionMode.AUTONOMOUS in ExecutionMode


@pytest.mark.slow
def test_slow_long_running_operation():
    """Long-running test excluded from PR checks."""
    total = 0
    for i in range(1000000):
        total += i
    assert total > 0


def test_regular_unit_test():
    """Regular test runs in all CI contexts."""
    assert 2 + 2 == 4
