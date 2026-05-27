# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Shared fixtures for collector hardening tests."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_artifact_dir(tmp_path: Path) -> Path:
    """Temporary directory for test artifacts (pytest-managed)."""
    return tmp_path


@pytest.fixture
def valid_outcome():
    """Valid control_outcome.json payload."""
    return {
        "task_id": "test-task-123",
        "status": "executed",
        "outcome_reason": "completed",
        "worker_role": "executor",
        "attempt": 1,
    }


@pytest.fixture
def valid_request():
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


