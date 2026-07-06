# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pin the reviewer's fail-closed defaults (audit Track A2).

The self-merge gate default is a security posture, not a tuning knob: the
fleet self-issues its own reviewer-verdict then merges via REST, so branch
protection is the only external constraint. A regression back to False would
silently restore blind-trust self-merge.
"""

from operations_center.config.settings import ReviewerSettings


def test_require_branch_protection_defaults_on():
    assert ReviewerSettings().require_branch_protection is True
