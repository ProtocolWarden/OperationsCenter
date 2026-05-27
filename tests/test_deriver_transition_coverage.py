# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Comprehensive transition coverage tests for derivers.

Tests all bidirectional transitions using parameterized test patterns.
Covers forward and reverse transitions across three derivers:
  - DependencyDriftDeriver: available ↔ not_available
  - LintDriftDeriver: clean ↔ violations, violation count increase ↔ decrease
  - TypeHealthDeriver: clean ↔ errors, error count increase ↔ decrease
"""
from __future__ import annotations

import pytest

from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
from operations_center.insights.derivers.lint_drift import LintDriftDeriver
from operations_center.insights.derivers.type_health import TypeHealthDeriver
from operations_center.insights.normalizer import InsightNormalizer
from tests.fixtures.deriver_transitions.helpers import TransitionFixture


class TestDependencyDriftTransitions:
    """Test all dependency_drift status transitions."""

    def _normalizer(self) -> InsightNormalizer:
        return InsightNormalizer()

    @pytest.mark.parametrize(
        "from_status,to_status,expected_insight_count",
        [
            ("available", "available", 2),  # current + persistent
            ("available", "not_available", 1),  # transition
            ("not_available", "available", 2),  # current + recovery
            ("not_available", "not_available", 0),  # no insights
        ],
    )
    def test_transitions_bidirectional(
        self,
        from_status: str,
        to_status: str,
        expected_insight_count: int,
    ) -> None:
        """Test all transition pairs: forward and reverse."""
        curr, prev = TransitionFixture.dependency_drift_pair(from_status, to_status)
        deriver = DependencyDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) == expected_insight_count, (
            f"Expected {expected_insight_count} insights for {from_status}→{to_status}, "
            f"got {len(insights)}: {[i.dedup_key for i in insights]}"
        )
        kinds = {i.kind for i in insights}
        assert "dependency_drift_continuity" in kinds or len(insights) == 0, (
            f"Expected insight kind 'dependency_drift_continuity' for {from_status}→{to_status}"
        )

    def test_available_to_not_available_transition_detected(self) -> None:
        """Forward transition: available → not_available generates transition insight."""
        curr, prev = TransitionFixture.dependency_drift_pair("available", "not_available")
        deriver = DependencyDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) == 1
        assert "not_available" in insights[0].dedup_key
        assert "transition" in insights[0].dedup_key
        assert insights[0].evidence["previous_status"] == "available"
        assert insights[0].evidence["current_status"] == "not_available"

    def test_not_available_to_available_recovery_detected(self) -> None:
        """Reverse transition: not_available → available generates recovery insight."""
        curr, prev = TransitionFixture.dependency_drift_pair("not_available", "available")
        deriver = DependencyDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        # Recovery transition generates both current and recovery insights
        assert len(insights) == 2
        recovery = [i for i in insights if "recovery" in i.dedup_key][0]
        assert "available" in recovery.dedup_key
        assert "recovery" in recovery.dedup_key
        assert recovery.evidence["previous_status"] == "not_available"
        assert recovery.evidence["current_status"] == "available"

    def test_available_persistent_across_snapshots(self) -> None:
        """Persistence: available state across multiple snapshots."""
        curr, prev = TransitionFixture.dependency_drift_pair("available", "available")
        deriver = DependencyDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) == 2
        dedup_keys = {i.dedup_key for i in insights}
        assert any("current" in k for k in dedup_keys)
        assert any("persistent" in k for k in dedup_keys)

    def test_recovery_then_persistent(self) -> None:
        """Recovery followed by persistence: not_available → available → available."""
        from datetime import UTC, datetime

        ts2 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)

        snap0, snap1 = TransitionFixture.dependency_drift_pair("available", "available", 86400)
        snap2 = TransitionFixture._base_snapshot(ts2, dependency_drift_status="not_available")

        deriver = DependencyDriftDeriver(self._normalizer())
        insights = deriver.derive([snap0, snap1, snap2])

        # Should see current (available) and persistent (2 consecutive available)
        assert len(insights) == 2
        dedup_keys = {i.dedup_key for i in insights}
        assert any("current" in k for k in dedup_keys)
        assert any("persistent" in k for k in dedup_keys)


class TestLintDriftTransitions:
    """Test all lint_drift status and count transitions."""

    def _normalizer(self) -> InsightNormalizer:
        return InsightNormalizer()

    @pytest.mark.parametrize(
        "from_status,to_status,from_count,to_count,has_insight",
        [
            # Clean → Clean (no violations → no violations)
            ("clean", "clean", 0, 0, False),
            # Clean → Violations
            ("clean", "violations", 0, 5, True),
            # Violations → Clean (improvement)
            ("violations", "clean", 5, 0, True),
            # Violations → Violations (with increase)
            ("violations", "violations", 3, 7, True),
            # Violations → Violations (with decrease)
            ("violations", "violations", 7, 3, True),
        ],
    )
    def test_lint_transitions_bidirectional(
        self,
        from_status: str,
        to_status: str,
        from_count: int,
        to_count: int,
        has_insight: bool,
    ) -> None:
        """Test lint transitions with both status and count changes."""
        curr, prev = TransitionFixture.lint_signal_pair(
            from_status, to_status, from_count, to_count
        )
        deriver = LintDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        if has_insight:
            assert len(insights) > 0, (
                f"Expected insight for {from_status}({from_count})→"
                f"{to_status}({to_count}), got none"
            )
        else:
            assert len(insights) == 0, (
                f"Expected no insights for {from_status}({from_count})→"
                f"{to_status}({to_count}), got {len(insights)}"
            )

    def test_clean_to_violations_regression(self) -> None:
        """Forward transition: clean → violations generates regression insight."""
        curr, prev = TransitionFixture.lint_signal_pair("clean", "violations", 0, 5)
        deriver = LintDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("present" in i.dedup_key for i in insights)

    def test_violations_to_clean_resolved(self) -> None:
        """Reverse transition: violations → clean generates resolved insight."""
        curr, prev = TransitionFixture.lint_signal_pair("violations", "clean", 5, 0)
        deriver = LintDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("resolved" in i.dedup_key for i in insights)

    def test_violations_count_increase_worsened(self) -> None:
        """Forward transition: violation count increase generates worsened insight."""
        curr, prev = TransitionFixture.lint_signal_pair(
            "violations", "violations", 3, 7
        )
        deriver = LintDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("worsened" in i.dedup_key for i in insights)
        worsened = [i for i in insights if "worsened" in i.dedup_key][0]
        assert worsened.evidence["delta"] == 4

    def test_violations_count_decrease_improved(self) -> None:
        """Reverse transition: violation count decrease generates improved insight."""
        curr, prev = TransitionFixture.lint_signal_pair(
            "violations", "violations", 7, 3
        )
        deriver = LintDriftDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("improved" in i.dedup_key for i in insights)
        improved = [i for i in insights if "improved" in i.dedup_key][0]
        assert improved.evidence["delta"] == 4

    def test_improvement_then_regression(self) -> None:
        """Improvement followed by regression: violations(7)→violations(3)→violations(5)."""
        from datetime import UTC, datetime

        ts0 = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)

        snap0 = TransitionFixture._base_snapshot(ts0, lint_status="violations", lint_count=5)
        _, snap1 = TransitionFixture.lint_signal_pair("violations", "violations", 7, 3)
        snap2 = TransitionFixture._base_snapshot(ts2, lint_status="violations", lint_count=7)

        deriver = LintDriftDeriver(self._normalizer())
        # Process most recent first
        insights = deriver.derive([snap0, snap1, snap2])

        # Should detect the current state has violations and the most recent transition is regressing
        assert len(insights) > 0


class TestTypeHealthTransitions:
    """Test all type_health status and count transitions."""

    def _normalizer(self) -> InsightNormalizer:
        return InsightNormalizer()

    @pytest.mark.parametrize(
        "from_status,to_status,from_count,to_count,has_insight",
        [
            # Clean → Clean (no errors → no errors)
            ("clean", "clean", 0, 0, False),
            # Clean → Errors
            ("clean", "errors", 0, 5, True),
            # Errors → Clean (improvement)
            ("errors", "clean", 5, 0, True),
            # Errors → Errors (with increase)
            ("errors", "errors", 3, 7, True),
            # Errors → Errors (with decrease)
            ("errors", "errors", 7, 3, True),
        ],
    )
    def test_type_transitions_bidirectional(
        self,
        from_status: str,
        to_status: str,
        from_count: int,
        to_count: int,
        has_insight: bool,
    ) -> None:
        """Test type transitions with both status and count changes."""
        curr, prev = TransitionFixture.type_signal_pair(
            from_status, to_status, from_count, to_count
        )
        deriver = TypeHealthDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        if has_insight:
            assert len(insights) > 0, (
                f"Expected insight for {from_status}({from_count})→"
                f"{to_status}({to_count}), got none"
            )
        else:
            assert len(insights) == 0, (
                f"Expected no insights for {from_status}({from_count})→"
                f"{to_status}({to_count}), got {len(insights)}"
            )

    def test_clean_to_errors_regression(self) -> None:
        """Forward transition: clean → errors generates regression insight."""
        curr, prev = TransitionFixture.type_signal_pair("clean", "errors", 0, 5)
        deriver = TypeHealthDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("present" in i.dedup_key for i in insights)

    def test_errors_to_clean_resolved(self) -> None:
        """Reverse transition: errors → clean generates resolved insight."""
        curr, prev = TransitionFixture.type_signal_pair("errors", "clean", 5, 0)
        deriver = TypeHealthDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("resolved" in i.dedup_key for i in insights)

    def test_errors_count_increase_worsened(self) -> None:
        """Forward transition: error count increase generates worsened insight."""
        curr, prev = TransitionFixture.type_signal_pair("errors", "errors", 3, 7)
        deriver = TypeHealthDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("worsened" in i.dedup_key for i in insights)
        worsened = [i for i in insights if "worsened" in i.dedup_key][0]
        assert worsened.evidence["delta"] == 4

    def test_errors_count_decrease_improved(self) -> None:
        """Reverse transition: error count decrease generates improved insight."""
        curr, prev = TransitionFixture.type_signal_pair("errors", "errors", 7, 3)
        deriver = TypeHealthDeriver(self._normalizer())
        insights = deriver.derive([curr, prev])

        assert len(insights) >= 1
        assert any("improved" in i.dedup_key for i in insights)
        improved = [i for i in insights if "improved" in i.dedup_key][0]
        assert improved.evidence["delta"] == 4

    def test_improvement_then_regression(self) -> None:
        """Improvement followed by regression: errors(7)→errors(3)→errors(5)."""
        from datetime import UTC, datetime

        ts0 = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)

        snap0 = TransitionFixture._base_snapshot(ts0, type_status="errors", type_count=5)
        _, snap1 = TransitionFixture.type_signal_pair("errors", "errors", 7, 3)
        snap2 = TransitionFixture._base_snapshot(ts2, type_status="errors", type_count=7)

        deriver = TypeHealthDeriver(self._normalizer())
        insights = deriver.derive([snap0, snap1, snap2])

        assert len(insights) > 0
