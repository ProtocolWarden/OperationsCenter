# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Consolidation test fixtures (inherits from parent verdicts conftest)."""

from __future__ import annotations


# Re-export fixtures from parent conftest so they're available in consolidation tests
from tests.verdicts.conftest import (  # noqa: F401
    audit_verdict_builder,
    lane_verdict_builder,
    merge_decision_builder,
)
