# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from operations_center.application.validation import ValidationRunner
from operations_center.domain import ValidationResult


def _fake_completed(returncode: int = 0, stdout: str = "out", stderr: str = "err"):
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_run_empty_commands_returns_empty_list(tmp_path: Path) -> None:
    runner = ValidationRunner()
    with mock.patch("operations_center.application.validation.subprocess.run") as run_mock:
        results = runner.run([], cwd=tmp_path)
    assert results == []
    run_mock.assert_not_called()


def test_run_single_success_command(tmp_path: Path) -> None:
    runner = ValidationRunner()
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        return_value=_fake_completed(0, "hello", ""),
    ) as run_mock:
        results = runner.run(["echo hi"], cwd=tmp_path)
    assert len(results) == 1
    res = results[0]
    assert isinstance(res, ValidationResult)
    assert res.command == "echo hi"
    assert res.exit_code == 0
    assert res.stdout == "hello"
    assert res.stderr == ""
    assert res.duration_ms >= 0
    # Verify subprocess invoked with expected kwargs.
    _, kwargs = run_mock.call_args
    assert kwargs["cwd"] == tmp_path
    assert kwargs["shell"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 300
    assert kwargs["env"] is None


def test_run_passes_env_and_custom_timeout(tmp_path: Path) -> None:
    runner = ValidationRunner()
    env = {"FOO": "bar"}
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        return_value=_fake_completed(0),
    ) as run_mock:
        runner.run(["cmd"], cwd=tmp_path, env=env, timeout_seconds=42)
    _, kwargs = run_mock.call_args
    assert kwargs["env"] == env
    assert kwargs["timeout"] == 42


def test_run_nonzero_exit_code_captured(tmp_path: Path) -> None:
    runner = ValidationRunner()
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        return_value=_fake_completed(2, "partial", "boom"),
    ):
        results = runner.run(["false"], cwd=tmp_path)
    assert results[0].exit_code == 2
    assert results[0].stderr == "boom"


def test_run_multiple_commands_preserve_order(tmp_path: Path) -> None:
    runner = ValidationRunner()
    procs = [
        _fake_completed(0, "a", ""),
        _fake_completed(1, "b", "e"),
        _fake_completed(0, "c", ""),
    ]
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        side_effect=procs,
    ) as run_mock:
        results = runner.run(["one", "two", "three"], cwd=tmp_path)
    assert [r.command for r in results] == ["one", "two", "three"]
    assert [r.exit_code for r in results] == [0, 1, 0]
    assert run_mock.call_count == 3


def test_run_duration_computed_from_monotonic(tmp_path: Path) -> None:
    runner = ValidationRunner()
    with (
        mock.patch(
            "operations_center.application.validation.subprocess.run",
            return_value=_fake_completed(0),
        ),
        mock.patch(
            "operations_center.application.validation.time.monotonic",
            side_effect=[100.0, 100.5],
        ),
    ):
        results = runner.run(["cmd"], cwd=tmp_path)
    assert results[0].duration_ms == 500


def test_run_timeout_string_stdout(tmp_path: Path) -> None:
    runner = ValidationRunner()
    exc = subprocess.TimeoutExpired(cmd="slow", timeout=5, output="captured-out")
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        side_effect=exc,
    ):
        results = runner.run(["slow"], cwd=tmp_path, timeout_seconds=5)
    res = results[0]
    assert res.exit_code == 124
    assert res.stdout == "captured-out"
    assert res.stderr == "Command timed out after 5s: slow"
    assert res.command == "slow"
    assert res.duration_ms >= 0


def test_run_timeout_bytes_stdout_decoded(tmp_path: Path) -> None:
    runner = ValidationRunner()
    exc = subprocess.TimeoutExpired(cmd="slow", timeout=3, output=b"by\xfftes")
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        side_effect=exc,
    ):
        results = runner.run(["slow"], cwd=tmp_path, timeout_seconds=3)
    res = results[0]
    assert res.exit_code == 124
    # \xff is undecodable in utf-8 -> replaced.
    assert res.stdout == "by�tes"


def test_run_timeout_none_stdout_becomes_empty(tmp_path: Path) -> None:
    runner = ValidationRunner()
    exc = subprocess.TimeoutExpired(cmd="slow", timeout=1, output=None)
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        side_effect=exc,
    ):
        results = runner.run(["slow"], cwd=tmp_path, timeout_seconds=1)
    assert results[0].stdout == ""


def test_run_timeout_then_success_continues(tmp_path: Path) -> None:
    runner = ValidationRunner()
    exc = subprocess.TimeoutExpired(cmd="slow", timeout=1, output="")
    with mock.patch(
        "operations_center.application.validation.subprocess.run",
        side_effect=[exc, _fake_completed(0, "done", "")],
    ):
        results = runner.run(["slow", "fast"], cwd=tmp_path, timeout_seconds=1)
    assert len(results) == 2
    assert results[0].exit_code == 124
    assert results[1].exit_code == 0
    assert results[1].stdout == "done"


def test_passed_all_zero_true() -> None:
    results = [
        ValidationResult(command="a", exit_code=0, stdout="", stderr="", duration_ms=1),
        ValidationResult(command="b", exit_code=0, stdout="", stderr="", duration_ms=2),
    ]
    assert ValidationRunner.passed(results) is True


def test_passed_any_nonzero_false() -> None:
    results = [
        ValidationResult(command="a", exit_code=0, stdout="", stderr="", duration_ms=1),
        ValidationResult(command="b", exit_code=1, stdout="", stderr="", duration_ms=2),
    ]
    assert ValidationRunner.passed(results) is False


def test_passed_empty_is_true() -> None:
    assert ValidationRunner.passed([]) is True
