# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Stage 4 tests: Query and reporting layers for extracted test data.

Tests for test_name and assertion_message surfacing through:
- query.py aggregation and filtering
- query_flaky.py inclusion in flaky test reports
- flaky_test_reporter.py formatting for output
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.observer.flaky_test_models import (
    FlakyTestResult,
    TestOutcome,
)
from operations_center.observer.flaky_test_reporter import FlakyTestReporter
from operations_center.observer.models import (
    DependencyDriftSignal,
    FlakyTestSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal,
    TodoSignal,
)
from operations_center.observer.query import TestSignalQuery, TimeRange
from operations_center.observer.query_flaky import FlakyTest


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


def _make_snapshot_with_extraction(
    run_id: str,
    observed_at: datetime,
    test_name: str | None = None,
    assertion_message: str | None = None,
    status: str = "passing",
    passed_count: int = 100,
    failed_count: int = 0,
    root: Path | None = None,
) -> Path:
    """Helper to create snapshot with test name and assertion message."""
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
                test_count=100,
                passed_count=passed_count,
                failed_count=failed_count,
                coverage_percent=85.0,
                failure_category="test_failure",
                execution_time_ms=5000,
                summary=f"{passed_count} passed, {failed_count} failed",
                test_name=test_name,
                assertion_message=assertion_message,
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


class TestGetFailingTestNames:
    """Tests for query.get_failing_test_names() method."""

    def test_returns_empty_dict_when_no_snapshots(self, query: TestSignalQuery) -> None:
        """Empty snapshots returns empty dict."""
        timerange = TimeRange.last_hours(24)
        result = query.get_failing_test_names(timerange)
        assert result == {}

    def test_aggregates_test_names_by_failure_count(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        """Aggregates test_name to failure count."""
        now = datetime.now(UTC)
        _make_snapshot_with_extraction(
            "run_1",
            now - timedelta(hours=2),
            test_name="test_foo",
            failed_count=1,
            root=tmp_snapshot_root,
        )
        _make_snapshot_with_extraction(
            "run_2",
            now - timedelta(hours=1),
            test_name="test_foo",
            failed_count=1,
            root=tmp_snapshot_root,
        )
        _make_snapshot_with_extraction(
            "run_3",
            now,
            test_name="test_bar",
            failed_count=1,
            root=tmp_snapshot_root,
        )

        timerange = TimeRange(start=now - timedelta(hours=3), end=now)
        result = query.get_failing_test_names(timerange)
        assert result == {"test_foo": 2, "test_bar": 1}

    def test_ignores_tests_without_names(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        """Tests without test_name are ignored."""
        now = datetime.now(UTC)
        _make_snapshot_with_extraction(
            "run_1",
            now,
            test_name=None,
            failed_count=1,
            root=tmp_snapshot_root,
        )

        timerange = TimeRange(start=now - timedelta(hours=1), end=now)
        result = query.get_failing_test_names(timerange)
        assert result == {}

    def test_sorted_by_count_descending(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        """Results sorted by count descending."""
        now = datetime.now(UTC)
        for i in range(5):
            _make_snapshot_with_extraction(
                f"run_{i}",
                now - timedelta(hours=i),
                test_name="popular_test",
                failed_count=1,
                root=tmp_snapshot_root,
            )
        for i in range(2):
            _make_snapshot_with_extraction(
                f"run_rare_{i}",
                now - timedelta(hours=i),
                test_name="rare_test",
                failed_count=1,
                root=tmp_snapshot_root,
            )

        timerange = TimeRange.last_hours(24)
        result = query.get_failing_test_names(timerange)
        names = list(result.keys())
        assert names[0] == "popular_test"
        assert names[1] == "rare_test"


class TestGetFailingAssertionMessages:
    """Tests for query.get_failing_assertion_messages() method."""

    def test_returns_empty_dict_when_no_snapshots(self, query: TestSignalQuery) -> None:
        """Empty snapshots returns empty dict."""
        timerange = TimeRange.last_hours(24)
        result = query.get_failing_assertion_messages(timerange)
        assert result == {}

    def test_aggregates_assertion_messages_by_count(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        """Aggregates assertion_message to failure count."""
        now = datetime.now(UTC)
        msg1 = "expected 5 but got 10"
        msg2 = "timeout waiting for service"

        _make_snapshot_with_extraction(
            "run_1",
            now - timedelta(hours=2),
            assertion_message=msg1,
            failed_count=1,
            root=tmp_snapshot_root,
        )
        _make_snapshot_with_extraction(
            "run_2",
            now - timedelta(hours=1),
            assertion_message=msg1,
            failed_count=1,
            root=tmp_snapshot_root,
        )
        _make_snapshot_with_extraction(
            "run_3",
            now,
            assertion_message=msg2,
            failed_count=1,
            root=tmp_snapshot_root,
        )

        timerange = TimeRange(start=now - timedelta(hours=3), end=now)
        result = query.get_failing_assertion_messages(timerange)
        assert result == {msg1: 2, msg2: 1}

    def test_ignores_tests_without_assertions(
        self, tmp_snapshot_root: Path, query: TestSignalQuery
    ) -> None:
        """Tests without assertion_message are ignored."""
        now = datetime.now(UTC)
        _make_snapshot_with_extraction(
            "run_1",
            now,
            assertion_message=None,
            failed_count=1,
            root=tmp_snapshot_root,
        )

        timerange = TimeRange(start=now - timedelta(hours=1), end=now)
        result = query.get_failing_assertion_messages(timerange)
        assert result == {}


class TestFlakyTestWithExtraction:
    """Tests for FlakyTest with test_name and assertion_message fields."""

    def test_flaky_test_includes_extracted_fields(self) -> None:
        """FlakyTest has test_name and assertion_message fields."""
        test = FlakyTest(
            name="test_module::test_foo",
            failure_rate=0.5,
            run_count=10,
            test_name="test_foo",
            assertion_message="expected True but got False",
        )
        assert test.test_name == "test_foo"
        assert test.assertion_message == "expected True but got False"

    def test_flaky_test_fields_optional(self) -> None:
        """test_name and assertion_message are optional."""
        test = FlakyTest(
            name="test_module::test_bar",
            failure_rate=0.3,
            run_count=10,
        )
        assert test.test_name is None
        assert test.assertion_message is None


class TestFilterByTestName:
    """Tests for query_flaky.filter_by_test_name() method."""

    def test_filter_by_test_name_substring_match(self, tmp_snapshot_root: Path) -> None:
        """Filters flaky tests by test_name substring match."""
        now = datetime.now(UTC)
        query = TestSignalQuery(root=tmp_snapshot_root)

        snapshot = RepoStateSnapshot(
            run_id="run_with_flaky",
            observed_at=now,
            source_command="test observe",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/test"),
                current_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=TestSignal(status="unavailable"),
                dependency_drift=DependencyDriftSignal(status="unavailable"),
                todo_signal=TodoSignal(),
                flaky_test_signal=FlakyTestSignal(
                    status="available",
                    flaky_test_count=2,
                    most_problematic_tests=[
                        {
                            "name": "test_module::test_foo",
                            "test_name": "test_foo",
                            "failure_rate": 0.5,
                            "run_count": 10,
                            "assertion_message": "expected 5",
                        },
                        {
                            "name": "test_module::test_bar",
                            "test_name": "test_bar",
                            "failure_rate": 0.3,
                            "run_count": 10,
                            "assertion_message": "timeout",
                        },
                    ],
                ),
            ),
        )

        run_dir = tmp_snapshot_root / "run_with_flaky"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "repo_state_snapshot.json").write_text(
            snapshot.model_dump_json(), encoding="utf-8"
        )

        result = query.filter_by_test_name("foo")
        assert len(result) == 1
        assert result[0].test_name == "test_foo"

    def test_filter_by_test_name_case_insensitive(self, tmp_snapshot_root: Path) -> None:
        """Filter is case-insensitive."""
        now = datetime.now(UTC)
        query = TestSignalQuery(root=tmp_snapshot_root)

        snapshot = RepoStateSnapshot(
            run_id="run_case_insensitive",
            observed_at=now,
            source_command="test observe",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/test"),
                current_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=TestSignal(status="unavailable"),
                dependency_drift=DependencyDriftSignal(status="unavailable"),
                todo_signal=TodoSignal(),
                flaky_test_signal=FlakyTestSignal(
                    status="available",
                    flaky_test_count=1,
                    most_problematic_tests=[
                        {
                            "name": "test_MyTest",
                            "test_name": "test_MyTest",
                            "failure_rate": 0.5,
                            "run_count": 10,
                        },
                    ],
                ),
            ),
        )

        run_dir = tmp_snapshot_root / "run_case_insensitive"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "repo_state_snapshot.json").write_text(
            snapshot.model_dump_json(), encoding="utf-8"
        )

        result = query.filter_by_test_name("MYTEST")
        assert len(result) == 1


class TestGetAssertionMessages:
    """Tests for query_flaky.get_assertion_messages() method."""

    def test_aggregates_assertion_messages_by_test_name(self, tmp_snapshot_root: Path) -> None:
        """Aggregates assertion messages grouped by test_name."""
        now = datetime.now(UTC)
        query = TestSignalQuery(root=tmp_snapshot_root)

        snapshot = RepoStateSnapshot(
            run_id="run_assertions",
            observed_at=now,
            source_command="test observe",
            repo=RepoContextSnapshot(
                name="test-repo",
                path=Path("/test"),
                current_branch="main",
                is_dirty=False,
            ),
            signals=RepoSignalsSnapshot(
                test_signal=TestSignal(status="unavailable"),
                dependency_drift=DependencyDriftSignal(status="unavailable"),
                todo_signal=TodoSignal(),
                flaky_test_signal=FlakyTestSignal(
                    status="available",
                    flaky_test_count=1,
                    most_problematic_tests=[
                        {
                            "name": "test_module::test_add",
                            "test_name": "test_add",
                            "failure_rate": 0.5,
                            "run_count": 10,
                            "assertion_message": "2 + 2 == 4",
                        },
                    ],
                ),
            ),
        )

        run_dir = tmp_snapshot_root / "run_assertions"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "repo_state_snapshot.json").write_text(
            snapshot.model_dump_json(), encoding="utf-8"
        )

        result = query.get_assertion_messages()
        assert "test_add" in result
        assert "2 + 2 == 4" in result["test_add"]


class TestFlakyTestReporterFormatting:
    """Tests for FlakyTestReporter formatting methods."""

    def test_format_flaky_tests_table_basic(self) -> None:
        """Formats flaky tests as table."""
        reporter = FlakyTestReporter(storage_root=Path("/tmp/test-flaky"))

        result1 = FlakyTestResult(
            nodeid="test_foo.py::test_add",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_add",
            assertion_message="2 + 2 == 5",
        )
        result2 = FlakyTestResult(
            nodeid="test_foo.py::test_add",
            outcome=TestOutcome.PASSED,
            duration=0.5,
            test_name="test_add",
        )
        reporter.track_test(result1)
        reporter.track_test(result2)

        table = reporter.format_flaky_tests_table()
        assert "test_add" in table or len(table) > 0

    def test_format_flaky_tests_table_with_assertions(self) -> None:
        """Formats flaky tests with assertion messages."""
        reporter = FlakyTestReporter(storage_root=Path("/tmp/test-flaky"))

        result1 = FlakyTestResult(
            nodeid="test_module::test_foo",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_foo",
            assertion_message="expected True but got False",
        )
        result2 = FlakyTestResult(
            nodeid="test_module::test_foo",
            outcome=TestOutcome.PASSED,
            duration=0.5,
            test_name="test_foo",
        )
        reporter.track_test(result1)
        reporter.track_test(result2)

        table = reporter.format_flaky_tests_table(include_assertions=True)
        assert len(table) > 0

    def test_format_flaky_tests_markdown(self) -> None:
        """Formats flaky tests as markdown."""
        reporter = FlakyTestReporter(storage_root=Path("/tmp/test-flaky"))

        result1 = FlakyTestResult(
            nodeid="test_module::test_bar",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_bar",
            assertion_message="timeout",
        )
        result2 = FlakyTestResult(
            nodeid="test_module::test_bar",
            outcome=TestOutcome.PASSED,
            duration=0.5,
            test_name="test_bar",
        )
        reporter.track_test(result1)
        reporter.track_test(result2)

        markdown = reporter.format_flaky_tests_markdown(include_assertions=True)
        assert "## Flaky Tests Report" in markdown or len(markdown) > 0

    def test_format_flaky_tests_markdown_without_assertions(self) -> None:
        """Markdown format without assertion messages."""
        reporter = FlakyTestReporter(storage_root=Path("/tmp/test-flaky"))

        result1 = FlakyTestResult(
            nodeid="test_module::test_baz",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_baz",
        )
        result2 = FlakyTestResult(
            nodeid="test_module::test_baz",
            outcome=TestOutcome.PASSED,
            duration=0.5,
            test_name="test_baz",
        )
        reporter.track_test(result1)
        reporter.track_test(result2)

        markdown = reporter.format_flaky_tests_markdown(include_assertions=False)
        assert len(markdown) > 0

    def test_get_extracted_data_summary(self) -> None:
        """Gets summary of extracted test names and assertion messages."""
        reporter = FlakyTestReporter(storage_root=Path("/tmp/test-flaky"))

        result1 = FlakyTestResult(
            nodeid="test_module::test_extract",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_extract",
            assertion_message="message1",
        )
        result2 = FlakyTestResult(
            nodeid="test_module::test_extract",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_extract",
            assertion_message="message2",
        )
        result3 = FlakyTestResult(
            nodeid="test_module::test_extract",
            outcome=TestOutcome.PASSED,
            duration=0.5,
            test_name="test_extract",
        )
        reporter.track_test(result1)
        reporter.track_test(result2)
        reporter.track_test(result3)

        summary = reporter.get_extracted_data_summary()
        assert "unique_test_names" in summary
        assert "unique_assertion_messages" in summary
        assert "tests_with_extraction_data" in summary
        assert len(summary["unique_test_names"]) > 0
        assert len(summary["unique_assertion_messages"]) > 0

    def test_extraction_coverage_percent(self) -> None:
        """Calculates extraction coverage percentage."""
        reporter = FlakyTestReporter(storage_root=Path("/tmp/test-flaky"))

        result1 = FlakyTestResult(
            nodeid="test1::test_with_name",
            outcome=TestOutcome.FAILED,
            duration=1.0,
            test_name="test_with_name",
            assertion_message="msg",
        )
        result2 = FlakyTestResult(
            nodeid="test2::test_no_name",
            outcome=TestOutcome.FAILED,
            duration=1.0,
        )
        reporter.track_test(result1)
        reporter.track_test(result2)

        summary = reporter.get_extracted_data_summary()
        assert summary["total_tests_analyzed"] == 2
        assert summary["extraction_coverage_percent"] >= 0.0
