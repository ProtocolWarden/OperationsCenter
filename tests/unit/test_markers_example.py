"""Example tests demonstrating pytest markers for smoke and slow tests."""
import pytest


@pytest.mark.smoke
def test_smoke_critical_feature():
    """Quick smoke test for critical functionality."""
    assert True


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
