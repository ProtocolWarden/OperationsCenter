# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Shared fixtures for collector hardening tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_artifact_dir(tmp_path: Path) -> Path:
    """Temporary directory for test artifacts (pytest-managed)."""
    return tmp_path


@pytest.fixture
def valid_outcome() -> dict[str, Any]:
    """Valid control_outcome.json payload."""
    return {
        "task_id": "test-task-123",
        "status": "executed",
        "outcome_reason": "completed",
        "worker_role": "executor",
        "attempt": 1,
    }


@pytest.fixture
def valid_request() -> dict[str, Any]:
    """Valid request.json payload."""
    return {
        "task": {
            "id": "test-task-123",
            "type": "integration_test",
            "repo_key": "test_repo",
        },
        "priority": 50,
        "run_id": "run-001",
    }
