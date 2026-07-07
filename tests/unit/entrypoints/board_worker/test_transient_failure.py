# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for is_transient_failure's reason-pattern matching.

Workspace-prep git-clone failures surface as failure_category=backend_error,
so a real host-side ssh/permissions flake (e.g. a racy read-only /etc bind
hitting a transiently-misowned ssh_config symlink) must be recognized as
transient — otherwise board_worker never retries it and a passing retry
opportunity is lost.
"""

from operations_center.entrypoints.board_worker._subprocess import is_transient_failure


def test_ssh_bad_owner_or_permissions_is_transient() -> None:
    result = {
        "failure_category": "backend_error",
        "failure_reason": (
            "Workspace preparation failed: git clone failed: Cloning into '.'...\n"
            "Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf\n"
            "fatal: Could not read from remote repository.\n"
        ),
    }
    assert is_transient_failure(result) is True


def test_could_not_read_from_remote_repository_is_transient() -> None:
    result = {
        "failure_category": "backend_error",
        "failure_reason": "fatal: Could not read from remote repository.",
    }
    assert is_transient_failure(result) is True


def test_non_transient_reason_is_not_retried() -> None:
    result = {
        "failure_category": "backend_error",
        "failure_reason": "AssertionError: expected 1 but got 2",
    }
    assert is_transient_failure(result) is False


def test_transient_reason_wrong_category_is_not_retried() -> None:
    result = {
        "failure_category": "policy_blocked",
        "failure_reason": "Bad owner or permissions on /etc/ssh/ssh_config.d/foo",
    }
    assert is_transient_failure(result) is False
