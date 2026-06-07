# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.observer.models import (
    CoverageSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal,
    TodoSignal,
)
from operations_center.observer.query import (
    CoverageTrend,
    FailureSummary,
    StatusTrend,
    TestSignalQuery,
    TimeRange,
)


@pytest.fixture
def tmp_snapshot_root(tmp_path: Path) -> Path:
    """Create a temporary snapshot root directory."""
    root = tmp_path / "observer"
    root.mkdir()
    return root


@pytest.fixture
def query(tmp_snapshot_root: Path) -> TestSignalQuery:
    """Create a TestSignalQuery instance pointing to temp snapshots."""
    return TestSignalQuery(root=tmp_snapshot_root)


def create_test_snapshot(
    run_id: str,
    observed_at: datetime,
    status: str = "passing",
    passed_count: int = 100,
    failed_count: int = 0,
    coverage_percent: float | None = 85.0,
    failure_category: str | None = None,
    execution_time_ms: int | None = 5000,
    test_count: int = 100,
    root: Path | None = None,
) -> Path:
    """Helper to create and write a test snapshot."""
    snapshot = RepoStateSnapshot(
        run_id=run_id,
        observed_at=observed_at,
        source_command="test observe",
        repo=RepoContextSnapshot(
            name="test-repo",
            path=Path("/test"),
            current_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=TestSignal(
                status=status,
                test_count=test_count,
                passed_count=passed_count,
                failed_count=failed_count,
                coverage_percent=coverage_percent,
                failure_category=failure_category,
                execution_time_ms=execution_time_ms,
                summary=f"{passed_count} passed, {failed_count} failed",
            ),
            dependency_drift=DependencyDriftSignal(status="unavailable"),
            todo_signal=TodoSignal(),
        ),
    )

    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / "repo_state_snapshot.json"
    json_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    return json_path


# TimeRange tests

class TestTimeRange:
    def test_last_hours(self) -> None:
        now = datetime.now(UTC)
        tr = TimeRange.last_hours(24)
        assert tr.end - tr.start == timedelta(hours=24)

    def test_last_days(self) -> None:
        tr = TimeRange.last_days(7)
        assert tr.end - tr.start == timedelta(days=7)

    def test_since(self) -> None:
        start = datetime.now(UTC) - timedelta(hours=12)
        tr = TimeRange.since(start)
        assert tr.start == start

    def test_contains(self) -> None:
        now = datetime.now(UTC)
        tr = TimeRange(start=now - timedelta(hours=1), end=now + timedelta(hours=1))
        assert tr.contains(now)
        assert not tr.contains(now - timedelta(hours=2))
        assert not tr.contains(now + timedelta(hours=2))


# Single-signal query tests

class TestGetLatestTestSignal:
    def test_returns_none_when_no_snapshots(self, query: TestSignalQuery) -> None:
        assert query.get_latest_test_signal() is None

    def test_returns_latest_signal(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot(
            "run_1", now - timedelta(hours=2), status="passing", passed_count=100, root=tmp_snapshot_root
        )
        create_test_snapshot(
            "run_2",
            now - timedelta(hours=1),
            status="failing",
            passed_count=95,
            failed_count=5,
            root=tmp_snapshot_root,
        )
        signal = query.get_latest_test_signal()
        assert signal is not None
        assert signal.status == "failing"
        assert signal.failed_count == 5

    def test_skips_unavailable_signals(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now, status="unavailable", root=tmp_snapshot_root)
        assert query.get_latest_test_signal() is None

    def test_returns_none_on_corrupted_json(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        run_dir = tmp_snapshot_root / "run_corrupt"
        run_dir.mkdir(parents=True)
        (run_dir / "repo_state_snapshot.json").write_text("invalid json")
        assert query.get_latest_test_signal() is None


class TestGetSignalByRunId:
    def test_returns_signal_for_valid_run_id(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_abc123", now, status="passing", passed_count=50, root=tmp_snapshot_root)
        signal = query.get_signal_by_run_id("run_abc123")
        assert signal is not None
        assert signal.passed_count == 50

    def test_returns_none_for_missing_run_id(self, query: TestSignalQuery) -> None:
        assert query.get_signal_by_run_id("nonexistent") is None

    def test_returns_none_for_unavailable_status(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_unavail", now, status="unavailable", root=tmp_snapshot_root)
        assert query.get_signal_by_run_id("run_unavail") is None


class TestListTestSignalHistory:
    def test_empty_window_returns_empty_list(self, query: TestSignalQuery) -> None:
        timerange = TimeRange.last_hours(1)
        assert query.list_test_signal_history(timerange) == []

    def test_returns_signals_in_order(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        times = [now - timedelta(hours=i) for i in range(3, 0, -1)]
        for i, t in enumerate(times):
            create_test_snapshot(f"run_{i}", t, passed_count=100 - i * 5, root=tmp_snapshot_root)

        timerange = TimeRange(start=now - timedelta(hours=4), end=now)
        results = query.list_test_signal_history(timerange)
        assert len(results) == 3
        assert results[0][1].passed_count == 95  # Oldest first
        assert results[2][1].passed_count == 85  # Newest last

    def test_filters_by_time_range(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_old", now - timedelta(days=10), root=tmp_snapshot_root)
        create_test_snapshot("run_recent", now - timedelta(hours=6), root=tmp_snapshot_root)

        timerange = TimeRange.last_hours(12)
        results = query.list_test_signal_history(timerange)
        assert len(results) == 1
        assert results[0][0] == "run_recent"

    def test_skips_unavailable_signals(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_good", now - timedelta(hours=1), status="passing", root=tmp_snapshot_root)
        create_test_snapshot("run_bad", now, status="unavailable", root=tmp_snapshot_root)

        timerange = TimeRange.last_hours(2)
        results = query.list_test_signal_history(timerange)
        assert len(results) == 1
        assert results[0][1].status == "passing"


# Trend analysis tests

class TestTestStatusTrend:
    def test_returns_none_with_fewer_than_2_snapshots(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now, root=tmp_snapshot_root)
        assert query.test_status_trend(count=5) is None

    def test_detects_stable_status(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        for i in range(5):
            create_test_snapshot(f"run_{i}", now - timedelta(hours=5 - i), status="passing", root=tmp_snapshot_root)

        trend = query.test_status_trend(count=5)
        assert trend is not None
        assert trend.is_stable
        assert trend.change_count == 0
        assert trend.current_status == "passing"

    def test_detects_status_transitions(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        statuses = ["passing", "passing", "failing", "failing", "passing"]
        for i, status in enumerate(statuses):
            create_test_snapshot(
                f"run_{i}", now - timedelta(hours=5 - i), status=status, root=tmp_snapshot_root
            )

        trend = query.test_status_trend(count=5)
        assert trend is not None
        assert trend.change_count == 2
        assert not trend.is_stable

    def test_counts_status_occurrences(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        statuses = ["passing", "passing", "failing", "passing", "failing"]
        for i, status in enumerate(statuses):
            create_test_snapshot(
                f"run_{i}", now - timedelta(hours=5 - i), status=status, root=tmp_snapshot_root
            )

        trend = query.test_status_trend(count=5)
        assert trend is not None
        assert trend.status_history == {"passing": 3, "failing": 2}
        assert trend.dominant_status == "passing"

    def test_returns_none_when_all_unavailable(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now, status="unavailable", root=tmp_snapshot_root)
        create_test_snapshot("run_2", now - timedelta(hours=1), status="unavailable", root=tmp_snapshot_root)
        assert query.test_status_trend(count=5) is None


class TestCoverageChangeRate:
    def test_returns_none_with_fewer_than_2_measurements(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now, coverage_percent=85.0, root=tmp_snapshot_root)
        timerange = TimeRange.last_hours(1)
        assert query.coverage_change_rate(timerange) is None

    def test_detects_improving_coverage(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now - timedelta(days=7), coverage_percent=80.0, root=tmp_snapshot_root)
        create_test_snapshot("run_2", now, coverage_percent=85.0, root=tmp_snapshot_root)

        trend = query.coverage_change_rate(TimeRange.last_days(8))
        assert trend is not None
        assert trend.trend_direction == "improving"
        assert trend.change_percent == pytest.approx(5.0)
        assert trend.current_percent == 85.0

    def test_detects_regressing_coverage(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now - timedelta(days=7), coverage_percent=85.0, root=tmp_snapshot_root)
        create_test_snapshot("run_2", now, coverage_percent=80.0, root=tmp_snapshot_root)

        trend = query.coverage_change_rate(TimeRange.last_days(8))
        assert trend is not None
        assert trend.trend_direction == "regressing"
        assert trend.change_percent == pytest.approx(-5.0)

    def test_detects_stable_coverage(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now - timedelta(days=7), coverage_percent=85.0, root=tmp_snapshot_root)
        create_test_snapshot("run_2", now, coverage_percent=85.05, root=tmp_snapshot_root)

        trend = query.coverage_change_rate(TimeRange.last_days(8))
        assert trend is not None
        assert trend.trend_direction == "stable"

    def test_calculates_coverage_statistics(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        percents = [80.0, 82.0, 85.0, 83.0, 87.0]
        for i, pct in enumerate(percents):
            create_test_snapshot(
                f"run_{i}",
                now - timedelta(days=5 - i),
                coverage_percent=pct,
                root=tmp_snapshot_root,
            )

        trend = query.coverage_change_rate(TimeRange.last_days(6))
        assert trend is not None
        assert trend.min_percent == 80.0
        assert trend.max_percent == 87.0
        assert trend.average_percent == pytest.approx(83.4)

    def test_skips_none_coverage_values(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now - timedelta(hours=2), coverage_percent=None, root=tmp_snapshot_root)
        create_test_snapshot("run_2", now - timedelta(hours=1), coverage_percent=85.0, root=tmp_snapshot_root)
        create_test_snapshot("run_3", now, coverage_percent=86.0, root=tmp_snapshot_root)

        trend = query.coverage_change_rate(TimeRange.last_hours(3))
        assert trend is not None
        assert len(trend.measurements) == 2


class TestFailureReasonSummary:
    def test_returns_none_with_no_snapshots(self, query: TestSignalQuery) -> None:
        timerange = TimeRange.last_hours(24)
        assert query.failure_reason_summary(timerange) is None

    def test_returns_none_with_no_failures(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now - timedelta(hours=1), status="passing", root=tmp_snapshot_root)
        create_test_snapshot("run_2", now, status="passing", root=tmp_snapshot_root)

        summary = query.failure_reason_summary(TimeRange.last_hours(2))
        assert summary is None

    def test_categorizes_failures(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot(
            "run_1",
            now - timedelta(hours=2),
            status="failing",
            failed_count=5,
            failure_category="assertion",
            root=tmp_snapshot_root,
        )
        create_test_snapshot(
            "run_2",
            now - timedelta(hours=1),
            status="failing",
            failed_count=3,
            failure_category="timeout",
            root=tmp_snapshot_root,
        )
        create_test_snapshot(
            "run_3", now, status="passing", root=tmp_snapshot_root
        )

        summary = query.failure_reason_summary(TimeRange.last_hours(3))
        assert summary is not None
        assert summary.failure_counts == {"assertion": 1, "timeout": 1}
        assert summary.most_common in ("assertion", "timeout")
        assert summary.total_failing_runs == 2
        assert summary.failing_rate == pytest.approx(2 / 3)
        assert summary.is_concerning

    def test_identifies_most_common_failure(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        for i in range(4):
            create_test_snapshot(
                f"run_{i}",
                now - timedelta(hours=4 - i),
                status="failing",
                failed_count=1,
                failure_category="assertion",
                root=tmp_snapshot_root,
            )
        create_test_snapshot(
            "run_4",
            now - timedelta(hours=0),
            status="failing",
            failed_count=1,
            failure_category="timeout",
            root=tmp_snapshot_root,
        )

        summary = query.failure_reason_summary(TimeRange.last_hours(5))
        assert summary is not None
        assert summary.most_common == "assertion"

    def test_calculates_failure_rate(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        # 5 passing, 2 failing
        for i in range(5):
            create_test_snapshot(f"run_pass_{i}", now - timedelta(hours=7 - i), status="passing", root=tmp_snapshot_root)
        for i in range(2):
            create_test_snapshot(
                f"run_fail_{i}",
                now - timedelta(hours=2 - i),
                status="failing",
                failed_count=1,
                failure_category="assertion",
                root=tmp_snapshot_root,
            )

        summary = query.failure_reason_summary(TimeRange.last_hours(8))
        assert summary is not None
        assert summary.failing_rate == pytest.approx(2 / 7)
        assert not summary.is_concerning

    def test_skips_unavailable_signals(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_good", now - timedelta(hours=1), status="passing", root=tmp_snapshot_root)
        create_test_snapshot("run_unavail", now, status="unavailable", root=tmp_snapshot_root)

        summary = query.failure_reason_summary(TimeRange.last_hours(2))
        assert summary is None


# Snapshot-level API tests

class TestSnapshotAPI:
    def test_get_snapshot(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_complete", now, status="passing", root=tmp_snapshot_root)

        snapshot = query.get_snapshot("run_complete")
        assert snapshot is not None
        assert snapshot.run_id == "run_complete"
        assert snapshot.signals.test_signal.status == "passing"

    def test_get_snapshot_returns_none_for_missing(self, query: TestSignalQuery) -> None:
        assert query.get_snapshot("nonexistent") is None

    def test_list_snapshot_run_ids(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        now = datetime.now(UTC)
        create_test_snapshot("run_1", now - timedelta(hours=2), root=tmp_snapshot_root)
        create_test_snapshot("run_2", now - timedelta(hours=1), root=tmp_snapshot_root)
        create_test_snapshot("run_3", now, root=tmp_snapshot_root)

        ids = query.list_snapshot_run_ids(TimeRange.last_hours(3))
        assert ids == ["run_1", "run_2", "run_3"]

    def test_list_snapshot_run_ids_empty_range(self, query: TestSignalQuery) -> None:
        assert query.list_snapshot_run_ids(TimeRange.last_hours(1)) == []


# Integration tests

class TestQueryIntegration:
    def test_autonomy_workflow_failure_investigation(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        """Simulate autonomy system investigating failures."""
        now = datetime.now(UTC)
        # Create realistic scenario: mostly passing with recent failures
        for i in range(8):
            create_test_snapshot(
                f"run_{i}",
                now - timedelta(hours=8 - i),
                status="passing",
                passed_count=100,
                root=tmp_snapshot_root,
            )
        create_test_snapshot(
            "run_fail_1",
            now - timedelta(hours=1),
            status="failing",
            failed_count=5,
            failure_category="timeout",
            root=tmp_snapshot_root,
        )
        create_test_snapshot(
            "run_fail_2",
            now,
            status="failing",
            failed_count=3,
            failure_category="timeout",
            root=tmp_snapshot_root,
        )

        # Autonomy would do:
        latest = query.get_latest_test_signal()
        assert latest is not None
        assert latest.failed_count == 3

        trend = query.test_status_trend(count=10)
        assert trend is not None
        assert trend.change_count == 2

        summary = query.failure_reason_summary(TimeRange.last_hours(12))
        assert summary is not None
        assert summary.most_common == "timeout"
        assert summary.is_concerning

    def test_coverage_monitoring_workflow(self, tmp_snapshot_root: Path, query: TestSignalQuery) -> None:
        """Simulate autonomy system monitoring coverage trends."""
        now = datetime.now(UTC)
        # Create steady coverage improvement
        for i in range(5):
            pct = 80.0 + (i * 1.0)
            create_test_snapshot(
                f"run_{i}",
                now - timedelta(days=5 - i),
                coverage_percent=pct,
                root=tmp_snapshot_root,
            )

        trend = query.coverage_change_rate(TimeRange.last_days(6))
        assert trend is not None
        assert trend.trend_direction == "improving"
        assert trend.change_percent == pytest.approx(4.0)

        history = query.list_test_signal_history(TimeRange.last_days(6))
        assert len(history) == 5
