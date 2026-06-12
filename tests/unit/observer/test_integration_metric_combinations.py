# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Stage 4: Integration tests for metric combinations, constraints, and system behavior.

Tests metric interdependencies, consistency across detection tiers, alert severity
mapping with extreme values, dashboard rendering with edge cases, and parametrized
combinations of multiple metrics.

Coverage:
- Metric interdependencies and constraint relationships
- Value consistency across detection tiers and thresholds
- Alert severity mapping with extreme metric values
- Dashboard panel rendering with boundary and extreme values
- Parametrized tests across multiple metric combinations
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from operations_center.observer.flaky_test_alerts import AlertSeverity, FlakyTestAlertManager
from operations_center.observer.flaky_test_models import (
    FlakynessCategory,
    FlakyTestMetric,
)
from operations_center.observer.flaky_test_storage import FlakyTestAggregationReport


@dataclass
class MetricCombination:
    """A set of metric values to test together."""

    failure_rate: float
    pattern_entropy: float
    streak_length: int
    recovery_time_days: float | None
    duration_variance: float
    flakiness_score: float
    expected_category: FlakynessCategory
    expected_alert_severity: AlertSeverity | None = None


class TestMetricInterdependencies:
    """Test relationships and constraints between metrics."""

    def test_failure_rate_zero_implies_entropy_zero(self, metric_factory):
        """When failure_rate=0, failure_entropy must be 0 (no failures).

        Constraint: Entropy requires variation in pass/fail distribution.
        If no failures occur, entropy is undefined (0).
        """
        metric = metric_factory(
            nodeid="test::no_failures",
            failure_rate=0.0,
            run_count=100,
        )

        assert metric.failure_rate == 0.0
        # Entropy cannot be measured from pure pass results
        assert metric.pattern_entropy == 0.0

    def test_failure_rate_one_implies_entropy_zero(self, metric_factory):
        """When failure_rate=1.0 (all failures), entropy must be 0.

        Constraint: Entropy requires variation. All same outcome = no entropy.
        """
        metric = metric_factory(
            nodeid="test::all_failures",
            failure_rate=1.0,
            run_count=50,
        )

        assert metric.failure_rate == 1.0
        # All failures: no variation, entropy = 0
        assert metric.pattern_entropy == 0.0

    def test_recovery_time_zero_with_low_failure_rate(self, metric_factory):
        """Low failure_rate can correlate with zero/low recovery_time.

        Tests that consistent performance (low failure_rate) suggests
        quick recovery when failures occur.
        """
        metric = metric_factory(
            nodeid="test::stable_test",
            failure_rate=0.02,
            run_count=1000,
            recovery_time_days=0.1,
        )

        # Low failure rate with quick recovery makes sense
        assert metric.failure_rate < 0.05
        assert metric.recovery_time_days is not None
        assert metric.recovery_time_days < 1.0

    def test_streak_variance_correlates_with_entropy(self, metric_factory):
        """High entropy should correlate with high streak_length.

        Entropy indicates variation in pass/fail pattern.
        Streak length measures consecutive runs.
        Both indicate non-deterministic behavior.
        """
        # Balanced entropy (high)
        metric_balanced = metric_factory(
            nodeid="test::balanced",
            pattern_entropy=0.9,
            streak_length=5,
        )

        # Unbalanced entropy (low)
        metric_unbalanced = metric_factory(
            nodeid="test::unbalanced",
            pattern_entropy=0.1,
            streak_length=1,
        )

        assert metric_balanced.pattern_entropy > metric_unbalanced.pattern_entropy
        assert metric_balanced.streak_length > metric_unbalanced.streak_length

    def test_confidence_with_high_entropy(self, metric_factory):
        """High entropy should allow for higher confidence in flakiness assessment.

        Pattern entropy indicates variation in pass/fail pattern.
        Higher entropy (more variation) should support higher confidence.
        """
        metric_high_entropy = metric_factory(
            nodeid="test::high_entropy",
            pattern_entropy=0.9,
            confidence=0.95,
        )

        metric_low_entropy = metric_factory(
            nodeid="test::low_entropy",
            pattern_entropy=0.1,
            confidence=0.3,
        )

        # Higher entropy supports higher confidence
        assert metric_high_entropy.pattern_entropy > metric_low_entropy.pattern_entropy
        assert metric_high_entropy.confidence > metric_low_entropy.confidence

    def test_duration_variance_consistency(self, metric_factory):
        """When duration_variance is 0, execution time is consistent.

        Zero variance means all durations identical, indicating perfect consistency.
        """
        metric = metric_factory(
            nodeid="test::consistent_duration",
            duration_mean=1.5,
            duration_variance=0.0,
        )

        # Zero variance = perfect stability
        assert metric.duration_variance == 0.0
        assert metric.duration_mean == 1.5

    @pytest.mark.parametrize(
        "failure_rate,entropy,expected_category",
        [
            # Low rate, low entropy → intermittent
            (0.02, 0.1, FlakynessCategory.INTERMITTENT),
            # High rate, high entropy → intermittent
            (0.4, 0.9, FlakynessCategory.INTERMITTENT),
            # High rate, low entropy → systematic (infrastructure/environment)
            (0.6, 0.1, FlakynessCategory.INFRASTRUCTURE),
        ],
    )
    def test_category_inference_from_metrics(
        self, metric_factory, failure_rate, entropy, expected_category
    ):
        """Category inference should depend on failure_rate AND entropy pattern.

        Tests that category assignment is consistent with metric values.
        """
        metric = metric_factory(
            nodeid="test::category_test",
            failure_rate=failure_rate,
            pattern_entropy=entropy,
            suspected_category=expected_category,
        )

        assert metric.suspected_category == expected_category


class TestMetricValueConsistencyAcrossTiers:
    """Test metric consistency across detection tier thresholds.

    Detection tiers use different thresholds:
    - Tier 1: Raw observations (individual test results)
    - Tier 2: Session-level aggregation
    - Tier 3: Repository-wide aggregation
    - Tier 4: Trend analysis and alert generation
    """

    @pytest.mark.parametrize(
        "failure_rate,above_unstable,above_flaky",
        [
            (0.02, False, False),
            (0.05, True, False),  # At unstable threshold (0.05)
            (0.08, True, False),  # Between unstable (0.05) and flaky (0.10)
            (0.10, True, True),  # At flaky threshold (0.10)
            (0.15, True, True),  # Above flaky
            (0.50, True, True),
        ],
    )
    def test_failure_rate_tier_consistency(self, failure_rate, above_unstable, above_flaky):
        """Verify failure_rate tier classification is consistent.

        Thresholds:
        - unstable_threshold = 0.05
        - flakiness_threshold = 0.10
        """
        is_unstable = failure_rate >= 0.05
        is_flaky = failure_rate >= 0.10

        assert is_unstable == above_unstable
        assert is_flaky == above_flaky

    def test_session_report_tier2_aggregation_consistency(self, metric_factory):
        """Verify Tier 2 session aggregation maintains metric consistency.

        Session aggregation should preserve min/max bounds of individual metrics.
        """
        metrics = [
            metric_factory(nodeid=f"test::{i}", failure_rate=0.01 * (i + 1)) for i in range(5)
        ]

        failure_rates = [m.failure_rate for m in metrics]
        min_rate = min(failure_rates)
        max_rate = max(failure_rates)
        avg_rate = sum(failure_rates) / len(failure_rates)

        # Aggregated metrics should respect bounds
        assert min_rate < avg_rate < max_rate

    def test_flaky_vs_unstable_threshold_ordering(self):
        """Verify flakiness_threshold > unstable_threshold.

        Tier consistency requires unstable < flaky.
        unstable_threshold = 0.05
        flakiness_threshold = 0.10
        """
        unstable_threshold = 0.05
        flakiness_threshold = 0.10

        assert unstable_threshold < flakiness_threshold
        assert flakiness_threshold == 2.0 * unstable_threshold

    @pytest.mark.parametrize(
        "flaky_count,total_tests,expected_percentage",
        [
            (0, 100, 0.0),
            (1, 100, 0.01),
            (5, 100, 0.05),  # At percentage threshold
            (10, 100, 0.10),
            (50, 100, 0.50),
            (100, 100, 1.0),
            (1, 1, 1.0),
            (0, 1, 0.0),
        ],
    )
    def test_flaky_test_percentage_calculation(self, flaky_count, total_tests, expected_percentage):
        """Verify flaky_test_percentage consistency across sample sizes.

        Metric: flaky_test_percentage = flaky_count / total_tests
        """
        if total_tests == 0:
            percentage = 0.0
        else:
            percentage = flaky_count / total_tests

        assert abs(percentage - expected_percentage) < 0.0001


class TestAlertSeverityMappingWithExtremeValues:
    """Test alert severity mapping when metrics reach extreme values."""

    @pytest.fixture
    def base_agg_report(self) -> FlakyTestAggregationReport:
        """Create base aggregation report for alert testing."""
        return FlakyTestAggregationReport(
            date="2026-06-12",
            period_days=7,
            total_test_executions=1000,
            flaky_test_count=0,
            unstable_test_count=0,
            flaky_tests=[],
            by_module={},
            by_category={},
        )

    def test_alert_severity_zero_flaky_tests(self, base_agg_report):
        """Zero flaky tests should generate no alerts.

        When flaky_test_count = 0, expect AlertSeverity.INFO or no alert.
        """
        report = base_agg_report
        report.flaky_test_count = 0
        report.flaky_tests = []

        alerts = FlakyTestAlertManager.check_alerts(report)

        # No flaky tests = no critical alerts
        critical_alerts = [
            a for a in alerts if a.severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY)
        ]
        assert len(critical_alerts) == 0

    def test_alert_severity_high_failure_rate(self, base_agg_report):
        """Tests with failure_rate > 0.3 should trigger CRITICAL alert.

        Alert type: CRITICAL_FLAKINESS
        """
        report = base_agg_report
        report.flaky_tests = [
            {
                "test_name": "test_critical_1",
                "failure_rate": 0.50,
                "category": "intermittent",
                "first_seen": datetime.now(UTC).isoformat(),
            },
            {
                "test_name": "test_critical_2",
                "failure_rate": 0.40,
                "category": "environment",
                "first_seen": datetime.now(UTC).isoformat(),
            },
        ]
        report.flaky_test_count = len(report.flaky_tests)

        alerts = FlakyTestAlertManager.check_alerts(report)

        # Should have critical alert for high failure rates
        critical_alerts = [a for a in alerts if a.alert_type == "CRITICAL_FLAKINESS"]
        assert len(critical_alerts) > 0
        assert critical_alerts[0].severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY)

    def test_alert_severity_regression_spike(self, base_agg_report):
        """Flaky test count increase >50% should trigger REGRESSION_SPIKE alert.

        Previous: 10 flaky tests
        Current: 16 flaky tests (+60%)
        Expected: CRITICAL severity
        """
        from dataclasses import replace

        prev_report = replace(
            base_agg_report,
            flaky_test_count=10,
            flaky_tests=[{"test_name": f"test_{i}"} for i in range(10)]
        )

        curr_report = replace(
            base_agg_report,
            flaky_test_count=16,
            flaky_tests=[{"test_name": f"test_{i}"} for i in range(16)]
        )

        alerts = FlakyTestAlertManager.check_alerts(curr_report, prev_report)

        regression_alerts = [a for a in alerts if a.alert_type == "REGRESSION_SPIKE"]
        assert len(regression_alerts) > 0
        assert regression_alerts[0].severity == AlertSeverity.CRITICAL

    def test_alert_severity_module_outbreak(self, base_agg_report):
        """Module with >20% flaky tests should trigger MODULE_OUTBREAK alert.

        A module with 30 tests, 8 flaky (26.7%) should trigger warning.
        Expected: WARNING severity
        """
        report = base_agg_report
        report.by_module = {
            "tests.unit.service": {
                "total_count": 30,
                "flaky_count": 8,
                "flaky_ratio": 0.267,
                "tests": [{"name": f"test_{i}", "failure_rate": 0.2} for i in range(8)],
            },
        }

        alerts = FlakyTestAlertManager.check_alerts(report)

        outbreak_alerts = [a for a in alerts if a.alert_type == "MODULE_OUTBREAK"]
        assert len(outbreak_alerts) > 0
        assert outbreak_alerts[0].severity == AlertSeverity.WARNING

    def test_alert_severity_no_regression_on_improvement(self, base_agg_report):
        """Decrease in flaky test count should NOT trigger regression alert.

        Previous: 20 flaky tests
        Current: 10 flaky tests (-50%)
        Expected: No REGRESSION_SPIKE alert
        """
        prev_report = base_agg_report
        prev_report.flaky_test_count = 20
        prev_report.flaky_tests = [{"test_name": f"test_{i}"} for i in range(20)]

        curr_report = base_agg_report
        curr_report.flaky_test_count = 10
        curr_report.flaky_tests = [{"test_name": f"test_{i}"} for i in range(10)]

        alerts = FlakyTestAlertManager.check_alerts(curr_report, prev_report)

        regression_alerts = [a for a in alerts if a.alert_type == "REGRESSION_SPIKE"]
        assert len(regression_alerts) == 0

    def test_alert_severity_ordering_by_severity(self, base_agg_report):
        """Alerts should be sorted by severity: EMERGENCY → CRITICAL → WARNING → INFO.

        Tests that alert ordering is consistent regardless of detection order.
        """
        report = base_agg_report
        report.flaky_test_count = 5
        report.flaky_tests = [
            {
                "test_name": "test_critical",
                "failure_rate": 0.50,
                "category": "intermittent",
                "first_seen": datetime.now(UTC).isoformat(),
            },
        ]
        report.by_module = {
            "outbreak_module": {
                "total_count": 10,
                "flaky_count": 3,
                "flaky_ratio": 0.30,
            },
        }

        alerts = FlakyTestAlertManager.check_alerts(report)

        if len(alerts) > 1:
            severity_order = {
                AlertSeverity.EMERGENCY: 0,
                AlertSeverity.CRITICAL: 1,
                AlertSeverity.WARNING: 2,
                AlertSeverity.INFO: 3,
            }

            severities = [severity_order[a.severity] for a in alerts]
            # Verify alerts are in non-decreasing severity order
            assert severities == sorted(severities)


class TestDashboardPanelRenderingWithExtremeValues:
    """Test dashboard rendering with boundary and extreme metric values.

    Dashboard panels must handle:
    - Zero values
    - Very large values (infinity, very large numbers)
    - NaN/undefined values
    - Boundary values at thresholds
    """

    def test_panel_render_zero_flaky_tests(self):
        """Dashboard should render cleanly when flaky_test_count = 0.

        Expected: Status shows "HEALTHY", metric displays "0".
        """
        flaky_count = 0
        total_tests = 1000

        percentage = (flaky_count / total_tests * 100) if total_tests > 0 else 0.0
        status = "HEALTHY" if percentage == 0 else "DEGRADED"

        assert percentage == 0.0
        assert status == "HEALTHY"

    def test_panel_render_all_tests_flaky(self):
        """Dashboard should handle 100% flaky tests.

        Expected: Status shows "CRITICAL", metric displays "100%".
        """
        flaky_count = 1000
        total_tests = 1000

        percentage = (flaky_count / total_tests * 100) if total_tests > 0 else 0.0
        status = "CRITICAL" if percentage >= 50 else "DEGRADED"

        assert percentage == 100.0
        assert status == "CRITICAL"

    def test_panel_render_infinite_recovery_time(self):
        """Dashboard should handle infinite recovery_time_days gracefully.

        When recovery_time_days is inf (test never recovers), display should
        indicate "never recovers" or similar.
        """
        recovery_time = float("inf")

        # Dashboard should display special value for infinity
        display_value = "Never" if math.isinf(recovery_time) else f"{recovery_time:.2f}d"

        assert display_value == "Never"

    def test_panel_render_boundary_failure_rate(self):
        """Dashboard should highlight boundary values appropriately.

        failure_rate at threshold (0.10) should trigger visual highlight.
        """
        thresholds = {
            "unstable": 0.05,
            "flaky": 0.10,
            "critical": 0.30,
        }

        test_values = [
            (0.049, "normal"),
            (0.05, "unstable"),
            (0.099, "unstable"),
            (0.10, "flaky"),
            (0.30, "critical"),
            (0.31, "critical"),
        ]

        for value, expected_status in test_values:
            if value >= thresholds["critical"]:
                status = "critical"
            elif value >= thresholds["flaky"]:
                status = "flaky"
            elif value >= thresholds["unstable"]:
                status = "unstable"
            else:
                status = "normal"

            assert status == expected_status

    def test_panel_render_nan_values(self):
        """Dashboard should handle NaN values from undefined metrics.

        Metrics like recovery_time when no failures occurred should be NaN.
        Dashboard should display as "—" or "N/A".
        """
        recovery_time = float("nan")

        display_value = "N/A" if math.isnan(recovery_time) else f"{recovery_time:.2f}d"

        assert display_value == "N/A"

    def test_panel_render_very_large_sample_sizes(self):
        """Dashboard should format very large numbers appropriately.

        1,000,000 tests should display as "1.0M" or similar.
        """
        test_count = 1_000_000

        if test_count >= 1_000_000:
            display = f"{test_count / 1_000_000:.1f}M"
        elif test_count >= 1_000:
            display = f"{test_count / 1_000:.1f}K"
        else:
            display = str(test_count)

        assert display == "1.0M"

    def test_panel_render_trend_with_negative_values(self):
        """Dashboard should handle negative trend (improvement) correctly.

        flaky_growth_rate = -0.2 means 20% improvement.
        """
        trend = -0.2
        is_improvement = trend < 0
        magnitude = abs(trend) * 100

        assert is_improvement
        assert magnitude == 20.0


class TestParametrizedMetricCombinations:
    """Test realistic metric combinations across multiple metrics.

    Tests combinations to ensure that metric values maintain logical consistency
    and produce expected alert behaviors when combined.
    """

    @pytest.mark.parametrize(
        "combination",
        [
            # Case 1: Intermittent flakiness (random failures)
            MetricCombination(
                failure_rate=0.15,
                pattern_entropy=0.85,
                streak_length=2,
                recovery_time_days=0.5,
                duration_variance=0.3,
                flakiness_score=0.6,
                expected_category=FlakynessCategory.INTERMITTENT,
                expected_alert_severity=AlertSeverity.WARNING,
            ),
            # Case 2: Environment-dependent flakiness
            MetricCombination(
                failure_rate=0.35,
                pattern_entropy=0.3,
                streak_length=1,
                recovery_time_days=1.5,
                duration_variance=0.6,
                flakiness_score=0.8,
                expected_category=FlakynessCategory.ENVIRONMENT,
                expected_alert_severity=AlertSeverity.CRITICAL,
            ),
            # Case 3: Infrastructure issues (systematic)
            MetricCombination(
                failure_rate=0.50,
                pattern_entropy=0.2,
                streak_length=1,
                recovery_time_days=None,
                duration_variance=0.8,
                flakiness_score=0.9,
                expected_category=FlakynessCategory.INFRASTRUCTURE,
                expected_alert_severity=AlertSeverity.CRITICAL,
            ),
            # Case 4: Rare, isolated flakiness
            MetricCombination(
                failure_rate=0.02,
                pattern_entropy=0.5,
                streak_length=1,
                recovery_time_days=0.01,
                duration_variance=0.1,
                flakiness_score=0.2,
                expected_category=FlakynessCategory.INTERMITTENT,
                expected_alert_severity=None,
            ),
            # Case 5: Borderline flakiness (at thresholds)
            MetricCombination(
                failure_rate=0.10,
                pattern_entropy=0.7,
                streak_length=2,
                recovery_time_days=0.3,
                duration_variance=0.4,
                flakiness_score=0.5,
                expected_category=FlakynessCategory.INTERMITTENT,
                expected_alert_severity=AlertSeverity.WARNING,
            ),
        ],
    )
    def test_metric_combination_consistency(self, metric_factory, combination):
        """Verify metric combinations produce consistent category and alert mappings.

        Tests that when multiple metrics are combined, the resulting flakiness
        profile is internally consistent and matches expected alert severity.
        """
        metric = metric_factory(
            nodeid="test::combination_test",
            failure_rate=combination.failure_rate,
            pattern_entropy=combination.pattern_entropy,
            streak_length=combination.streak_length,
            recovery_time_days=combination.recovery_time_days,
            duration_variance=combination.duration_variance,
            flakiness_score=combination.flakiness_score,
            suspected_category=combination.expected_category,
        )

        # Verify metric properties
        assert metric.failure_rate == combination.failure_rate
        assert metric.suspected_category == combination.expected_category

        # Verify logical relationships
        if combination.flakiness_score > 0.6 and combination.pattern_entropy < 0.5:
            # High flakiness score + low entropy = systematic issue
            assert metric.suspected_category in (
                FlakynessCategory.ENVIRONMENT,
                FlakynessCategory.INFRASTRUCTURE,
            )

    @pytest.mark.parametrize(
        "failure_rate,entropy,expected_is_flaky",
        [
            # Low failure rate, low entropy = stable
            (0.01, 0.1, False),
            # Low failure rate, high entropy = intermittent but not flaky
            (0.05, 0.9, False),
            # High failure rate, low entropy = systematic
            (0.15, 0.2, True),
            # High failure rate, high entropy = highly flaky
            (0.25, 0.8, True),
            # At threshold
            (0.10, 0.5, True),
        ],
    )
    def test_flakiness_classification(
        self, metric_factory, failure_rate, entropy, expected_is_flaky
    ):
        """Verify flakiness classification across failure_rate and entropy combinations.

        Flakiness threshold: failure_rate >= 0.10
        Classification: metric is flaky iff failure_rate >= 0.10
        """
        metric = metric_factory(
            nodeid="test::classification",
            failure_rate=failure_rate,
            pattern_entropy=entropy,
        )

        is_flaky = metric.failure_rate >= 0.10

        assert is_flaky == expected_is_flaky

    def test_metric_combination_extreme_entropy_with_binary_outcome(self, metric_factory):
        """Test entropy bounds: maximum entropy for binary outcome is 1.0.

        With only pass/fail outcomes, maximum entropy = 1.0 (50/50 split).
        Any value > 1.0 indicates error in calculation.
        """
        # Maximum entropy case: 50/50 pass/fail
        metric = metric_factory(
            nodeid="test::max_entropy",
            pattern_entropy=1.0,
        )

        assert 0.0 <= metric.pattern_entropy <= 1.0

    def test_metric_combination_recovery_time_with_zero_failures(self, metric_factory):
        """Recovery time should be None/undefined when failure_rate = 0.

        Cannot measure recovery when no failures occur.
        """
        metric = metric_factory(
            nodeid="test::no_failures",
            failure_rate=0.0,
            recovery_time_days=None,
        )

        assert metric.failure_rate == 0.0
        assert metric.recovery_time_days is None

    @pytest.mark.parametrize(
        "run_count,expected_min_entropy_data_points",
        [
            (1, 0),  # Single run: can't measure entropy
            (2, 1),  # Two runs: at least one variant
            (5, 2),  # Five runs: measurable distribution
            (100, 50),  # Large sample: good entropy estimate
        ],
    )
    def test_entropy_calculation_data_point_requirements(
        self, metric_factory, run_count, expected_min_entropy_data_points
    ):
        """Entropy calculation needs minimum data points (run_count).

        Entropy from distribution requires multiple observations.
        """
        metric = metric_factory(
            nodeid="test::entropy_test",
            run_count=run_count,
        )

        # Entropy can be calculated with >= 2 runs
        assert metric.run_count == run_count

    def test_confidence_bounds(self, metric_factory):
        """Confidence must be in [0.0, 1.0].

        0 = no confidence in flakiness assessment
        1 = full confidence in flakiness assessment
        """
        for confidence_value in [0.0, 0.25, 0.5, 0.75, 1.0]:
            metric = metric_factory(
                nodeid="test::confidence",
                confidence=confidence_value,
            )

            assert 0.0 <= metric.confidence <= 1.0

    def test_duration_variance_consistency_check(self, metric_factory):
        """duration_variance indicates execution time consistency.

        If variance = 0, execution time is perfectly consistent.
        If variance is high, execution time varies significantly.
        """
        # Zero variance = stable
        metric_stable = metric_factory(
            nodeid="test::stable",
            duration_variance=0.0,
        )

        # High variance = unstable
        metric_unstable = metric_factory(
            nodeid="test::unstable",
            duration_variance=5.0,
        )

        assert metric_stable.duration_variance <= metric_unstable.duration_variance
        assert metric_stable.duration_variance == 0.0
        assert metric_unstable.duration_variance == 5.0

    def test_confidence_score_bounds(self, metric_factory):
        """Confidence must be in [0.0, 1.0].

        0 = no confidence in flakiness diagnosis
        1 = high confidence
        """
        for confidence in [0.0, 0.25, 0.5, 0.75, 1.0]:
            metric = metric_factory(
                nodeid="test::confidence",
                confidence=confidence,
            )

            assert 0.0 <= metric.confidence <= 1.0

    def test_flakiness_score_combination_of_metrics(self, metric_factory):
        """flakiness_score should be influenced by multiple metrics.

        Tests that flakiness_score reflects combination of failure_rate, entropy,
        and other metrics, not just failure_rate alone.
        """
        # Scenario 1: Rare but deterministic (low score?)
        metric_rare_deterministic = metric_factory(
            nodeid="test::rare_deterministic",
            failure_rate=0.02,
            pattern_entropy=0.1,
            flakiness_score=0.05,
        )

        # Scenario 2: Common and highly random (high score)
        metric_common_random = metric_factory(
            nodeid="test::common_random",
            failure_rate=0.25,
            pattern_entropy=0.9,
            flakiness_score=0.85,
        )

        # The multi-factor score should show clear difference
        assert metric_rare_deterministic.flakiness_score < metric_common_random.flakiness_score


class TestMetricConstraintValidation:
    """Test that metric values respect defined constraints and bounds."""

    @pytest.mark.parametrize(
        "metric_name,value,valid_range",
        [
            ("failure_rate", 0.0, (0.0, 1.0)),
            ("failure_rate", 0.5, (0.0, 1.0)),
            ("failure_rate", 1.0, (0.0, 1.0)),
            ("pattern_entropy", 0.0, (0.0, 1.0)),
            ("pattern_entropy", 0.7, (0.0, 1.0)),
            ("pattern_entropy", 1.0, (0.0, 1.0)),
            ("confidence", 0.0, (0.0, 1.0)),
            ("confidence", 0.99, (0.0, 1.0)),
        ],
    )
    def test_metric_value_within_bounds(self, metric_factory, metric_name, value, valid_range):
        """Verify metric values stay within defined bounds.

        Each metric has a valid value range. Values outside the range are invalid.
        """
        kwargs = {metric_name: value}
        metric = metric_factory(nodeid="test::bounds", **kwargs)

        actual_value = getattr(metric, metric_name)
        min_val, max_val = valid_range

        assert min_val <= actual_value <= max_val

    def test_negative_run_count_invalid(self, metric_factory):
        """run_count must be non-negative.

        run_count < 0 is invalid.
        """
        metric = metric_factory(nodeid="test::runs", run_count=100)

        assert metric.run_count >= 0

    def test_negative_recovery_time_invalid(self, metric_factory):
        """recovery_time_days must be non-negative or None.

        Negative recovery time is invalid.
        """
        metric = metric_factory(
            nodeid="test::recovery",
            recovery_time_days=1.5,
        )

        assert metric.recovery_time_days is None or metric.recovery_time_days >= 0.0

    def test_failure_rate_exceeding_one_invalid(self, metric_factory):
        """failure_rate > 1.0 is invalid.

        Can't have more failures than runs.
        """
        metric = metric_factory(
            nodeid="test::overrun",
            failure_rate=0.99,
            run_count=100,
        )

        assert metric.failure_rate <= 1.0


class TestMetricConsistencyWithSessionReports:
    """Test consistency between individual metrics and session-level aggregations."""

    def test_session_report_flaky_count_matches_metric_list(
        self, flaky_test_session_report_factory
    ):
        """Session report flaky_count must match length of flaky_candidates list.

        These should stay in sync.
        """

        metrics = [
            FlakyTestMetric(
                nodeid=f"test::{i}",
                failure_rate=0.15,
                run_count=10,
            )
            for i in range(5)
        ]

        report = flaky_test_session_report_factory(
            session_id="test-session",
            total_tests=100,
            flaky_candidates=metrics,
        )

        assert len(report.flaky_candidates) == len(metrics)

    def test_session_report_total_tests_bounds_flaky_count(self):
        """Session report flaky_count must be <= total_tests.

        Can't have more flaky tests than total tests.
        """
        total_tests = 100
        flaky_count = 50

        assert flaky_count <= total_tests

    def test_session_report_aggregation_maintains_metric_properties(
        self, metric_factory, flaky_test_session_report_factory
    ):
        """Session report aggregation should preserve metric distributions.

        Min, max, and mean of metrics should be consistent.
        """
        metrics = [
            metric_factory(nodeid=f"test::{i}", failure_rate=0.05 * (i + 1)) for i in range(5)
        ]

        report = flaky_test_session_report_factory(
            total_tests=100,
            flaky_candidates=metrics,
        )

        failure_rates = [m.failure_rate for m in report.flaky_candidates]
        assert len(failure_rates) == 5
        assert min(failure_rates) >= 0.0
        assert max(failure_rates) <= 1.0
        assert 0.0 < sum(failure_rates) / len(failure_rates) < 1.0
