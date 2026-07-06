# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""board_worker unit-test defaults.

Containment is default-on + required (audit Track A3), so any test that
exercises dispatch/spec-author/run_executor paths would otherwise raise
ContainmentRequiredError / EgressContainmentRequiredError on a host without
bwrap/pasta/proxy. Unit tests exercise the orchestration logic, not the
containment posture — so containment is pinned OFF here, and the containment
tests (test_sandbox.py / test_netns.py) re-enable it explicitly per test.
"""

import pytest


@pytest.fixture(autouse=True)
def _containment_off(monkeypatch):
    monkeypatch.setenv("OC_BWRAP_SANDBOX", "0")
    monkeypatch.setenv("OC_EGRESS_NETNS", "0")
    monkeypatch.setenv("OC_SANDBOX_REQUIRED", "0")
    monkeypatch.setenv("OC_EGRESS_REQUIRED", "0")
