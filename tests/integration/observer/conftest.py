# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Pytest fixtures for snapshot validation integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from operations_center.observer.models import (
    CheckSignal,
    CoverageSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TodoSignal,
)
from operations_center.observer.snapshot_manager import SnapshotManager
from operations_center.observer.snapshot_validator import SnapshotValidator


@pytest.fixture
def repo_path() -> Path:
    """Return the current repository path."""
    return Path.cwd()


@pytest.fixture
def snapshot_manager(tmp_path: Path) -> SnapshotManager:
    """Create a snapshot manager with local storage."""
    return SnapshotManager.create_local(root=tmp_path)


@pytest.fixture
def minimal_snapshot() -> RepoStateSnapshot:
    """Create a minimal valid snapshot for testing."""
    now = datetime.now(timezone.utc)

    repo_context = RepoContextSnapshot(
        name="test-repo",
        path=Path.cwd(),
        current_branch="main",
        base_branch="main",
        is_dirty=False,
    )

    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(
            status="passing",
            test_count=100,
            source="pytest",
            observed_at=now,
            summary="100 passed",
        ),
        dependency_drift=DependencyDriftSignal(
            status="healthy",
            critical_issues=0,
            observed_at=now,
            summary="No critical issues",
        ),
        todo_signal=TodoSignal(count=5, summary="5 todos/fixmes"),
    )

    snapshot = RepoStateSnapshot(
        run_id=f"test_{uuid4().hex[:8]}",
        observed_at=now,
        observer_version=1,
        source_command="pytest",
        repo=repo_context,
        signals=signals,
    )

    return snapshot


@pytest.fixture
def snapshot_with_errors() -> RepoStateSnapshot:
    """Create a snapshot with test failures and coverage gaps."""
    now = datetime.now(timezone.utc)

    repo_context = RepoContextSnapshot(
        name="test-repo",
        path=Path.cwd(),
        current_branch="main",
        base_branch="main",
        is_dirty=True,
    )

    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(
            status="failing",
            test_count=150,
            failed_count=5,
            source="pytest",
            observed_at=now,
            summary="145 passed, 5 failed",
        ),
        dependency_drift=DependencyDriftSignal(
            status="degraded",
            critical_issues=2,
            observed_at=now,
            summary="2 critical vulnerabilities",
        ),
        todo_signal=TodoSignal(count=50, summary="50 todos/fixmes"),
    )

    snapshot = RepoStateSnapshot(
        run_id=f"test_{uuid4().hex[:8]}",
        observed_at=now,
        observer_version=1,
        source_command="pytest",
        repo=repo_context,
        signals=signals,
        collector_errors={"test_collector": "Timeout", "coverage_collector": "Missing config"},
    )

    return snapshot


@pytest.fixture
def snapshot_with_limited_signals() -> RepoStateSnapshot:
    """Create a snapshot with only minimal required signals."""
    now = datetime.now(timezone.utc)

    repo_context = RepoContextSnapshot(
        name="test-repo",
        path=Path.cwd(),
        current_branch="main",
        base_branch="main",
        is_dirty=False,
    )

    # Minimal signals - only required ones
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(
            status="unavailable",
            source="pytest",
            summary="Not run",
        ),
        dependency_drift=DependencyDriftSignal(
            status="unavailable",
            summary="Not analyzed",
        ),
        todo_signal=TodoSignal(count=0, summary="None"),
    )

    snapshot = RepoStateSnapshot(
        run_id=f"test_{uuid4().hex[:8]}",
        observed_at=now,
        observer_version=1,
        source_command="pytest",
        repo=repo_context,
        signals=signals,
    )

    return snapshot


@pytest.fixture
def snapshot_with_inconsistent_signals() -> RepoStateSnapshot:
    """Create a snapshot with inconsistent signal data."""
    now = datetime.now(timezone.utc)

    repo_context = RepoContextSnapshot(
        name="test-repo",
        path=Path.cwd(),
        current_branch="main",
        base_branch="main",
        is_dirty=False,
    )

    # Test signal claims passing but has 0 tests (inconsistent)
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(
            status="passing",
            test_count=0,  # Inconsistent: passing but no tests
            source="pytest",
            observed_at=now,
            summary="0 passed",
        ),
        dependency_drift=DependencyDriftSignal(
            status="healthy",
            critical_issues=5,  # Inconsistent: healthy but has critical issues
            observed_at=now,
            summary="5 critical issues",
        ),
        todo_signal=TodoSignal(count=10, summary="10 todos"),
    )

    snapshot = RepoStateSnapshot(
        run_id=f"test_{uuid4().hex[:8]}",
        observed_at=now,
        observer_version=1,
        source_command="pytest",
        repo=repo_context,
        signals=signals,
    )

    return snapshot


@pytest.fixture
def snapshot_validator(minimal_snapshot: RepoStateSnapshot, repo_path: Path) -> SnapshotValidator:
    """Create a snapshot validator with minimal snapshot."""
    return SnapshotValidator(minimal_snapshot, repo_path=repo_path)


@pytest.fixture
def validator_with_errors(
    snapshot_with_errors: RepoStateSnapshot, repo_path: Path
) -> SnapshotValidator:
    """Create a snapshot validator with error snapshot."""
    return SnapshotValidator(snapshot_with_errors, repo_path=repo_path)


@pytest.fixture
def validator_with_limited_signals(
    snapshot_with_limited_signals: RepoStateSnapshot, repo_path: Path
) -> SnapshotValidator:
    """Create a snapshot validator with limited signals."""
    return SnapshotValidator(snapshot_with_limited_signals, repo_path=repo_path)


@pytest.fixture
def validator_with_inconsistent_signals(
    snapshot_with_inconsistent_signals: RepoStateSnapshot, repo_path: Path
) -> SnapshotValidator:
    """Create a snapshot validator with inconsistent signals."""
    return SnapshotValidator(snapshot_with_inconsistent_signals, repo_path=repo_path)


@pytest.fixture
def baseline_snapshot() -> RepoStateSnapshot:
    """Create a baseline snapshot for regression testing."""
    now = datetime.now(timezone.utc)

    repo_context = RepoContextSnapshot(
        name="test-repo",
        path=Path.cwd(),
        current_branch="main",
        base_branch="main",
        is_dirty=False,
    )

    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(
            status="passing",
            test_count=7587,
            source="pytest",
            observed_at=now,
            summary="7587 passed",
        ),
        dependency_drift=DependencyDriftSignal(
            status="healthy",
            critical_issues=0,
            observed_at=now,
            summary="No critical issues",
        ),
        todo_signal=TodoSignal(count=10, summary="10 todos"),
        coverage_signal=CoverageSignal(
            status="good",
            total_coverage_pct=85.0,
            observed_at=now,
            summary="85% coverage",
        ),
    )

    snapshot = RepoStateSnapshot(
        run_id=f"baseline_{uuid4().hex[:8]}",
        observed_at=now - timedelta(days=7),
        observer_version=1,
        source_command="baseline",
        repo=repo_context,
        signals=signals,
    )

    return snapshot
