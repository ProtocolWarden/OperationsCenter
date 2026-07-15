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


def test_council_guardrail_paths_populated_by_default():
    """The council is a security control: guardrail_paths ships POPULATED with
    the COUNCIL_VERDICT.md §G1 set so cross-family adjudication is LIVE for OC's
    control-plane surfaces. A regression back to [] would silently disable the
    gate (fall through to single self-review on guardrail PRs)."""
    from operations_center.config.settings import CouncilSettings

    paths = CouncilSettings().guardrail_paths
    assert isinstance(paths, list) and paths, "guardrail_paths must be non-empty (council ON)"
    # The council must guard its OWN code and the loop bridge (control plane).
    assert "src/operations_center/entrypoints/pr_review_watcher/**" in paths
    assert "src/operations_center/entrypoints/loop_bridge/**" in paths
    # And the operator-authority surfaces.
    assert ".hooks/**" in paths
    assert "scripts/operations-center.sh" in paths
