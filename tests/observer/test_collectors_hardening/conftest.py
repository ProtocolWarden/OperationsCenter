# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Shared fixtures for collector hardening tests."""
import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_artifact_dir():
    """Temporary directory for test artifacts."""
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


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


@pytest.fixture
def valid_validation():
    """Valid validation.json payload."""
    return {
        "passed": True,
        "errors": [],
        "warnings": [],
    }


@pytest.fixture
def valid_dependency_report():
    """Valid dependency_report.json payload."""
    return {
        "statuses": [
            {
                "package": "requests",
                "version": "2.28.0",
                "severity": "info",
                "notes": "Update available",
            },
            {
                "package": "pytest",
                "version": "7.0.0",
                "severity": "warning",
                "notes": "Security update recommended",
            },
        ],
        "created_task_ids": ["task-001", "task-002"],
    }


@pytest.fixture
def malformed_json_cases():
    """Collection of malformed JSON strings for testing."""
    return {
        "truncated_brace": '{"key": "value"',
        "extra_comma": '{"key": "value",}',
        "single_quotes": "{'key': 'value'}",
        "invalid_escape": '{"key": "value\\x"}',
        "empty": "",
        "whitespace": "   \n\t   ",
        "null_bytes": 'null\x00invalid',
    }
