# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import typer

import operations_center.entrypoints.audit.main as main
from operations_center.audit_dispatch import (
    AuditDispatchConfigError,
    RepoLockAlreadyHeldError,
)
from operations_center.audit_toolset import (
    ArtifactManifestPathMissingError,
    RunStatusContractError,
    RunStatusNotFoundError,
)


# --------------------------------------------------------------------------
# Helpers (module-level non-test helpers must be underscore-prefixed: N2)
# --------------------------------------------------------------------------
def _make_result(
    *,
    succeeded: bool = True,
    run_id: str | None = "run-1",
    failure_kind=None,
    process_exit_code: int | None = 0,
    error: str | None = None,
):
    """Build a fake dispatch result object with the attributes main.py reads."""
    return SimpleNamespace(
        succeeded=succeeded,
        repo_id="example_managed_repo",
        audit_type="representative",
        run_id=run_id,
        status=SimpleNamespace(value="completed" if succeeded else "failed"),
        failure_kind=failure_kind,
        process_exit_code=process_exit_code,
        duration_seconds=1.25,
        run_status_path="/tmp/rs.json",
        artifact_manifest_path="/tmp/manifest.json",
        stdout_path="/tmp/out.log",
        stderr_path="/tmp/err.log",
        error=error,
        model_dump_json=lambda indent=2: '{"ok": true}',
    )


def _make_run_status(values: dict | None = None):
    """Fake run-status pydantic-ish object."""
    vals = values if values is not None else {"a": 1, "b": None, "c": "x"}
    return SimpleNamespace(
        model_dump=lambda: vals,
        model_dump_json=lambda indent=2: '{"run": "status"}',
    )


def _make_payload(
    *,
    is_alive: bool = False,
    oc_pid_alive: bool = False,
    audit_pid_alive: bool = False,
    started_at: str = "2026-06-02T00:00:00+00:00",
    audit_pid: int | None = 42,
    run_status_path: str = "/tmp/run-dir",
):
    return SimpleNamespace(
        repo_id="example_managed_repo",
        audit_type="representative",
        run_id="run-1",
        oc_pid=111,
        audit_pid=audit_pid,
        started_at=started_at,
        expected_run_status_path=run_status_path,
        is_alive=lambda: is_alive,
        liveness_summary=lambda: {
            "oc_pid_alive": oc_pid_alive,
            "audit_pid_alive": audit_pid_alive,
        },
        to_json=lambda: {"repo_id": "example_managed_repo", "run_id": "run-1"},
    )


def _exit_code(excinfo) -> int:
    return excinfo.value.exit_code


def _run(monkeypatch, **overrides):
    """Call cmd_run with every option explicitly set (typer defaults are sentinels)."""
    kwargs = {
        "repo": "r",
        "audit_type": "t",
        "allow_unverified": False,
        "timeout": None,
        "requested_by": None,
        "log_dir": None,
        "json_output": False,
    }
    kwargs.update(overrides)
    return main.cmd_run(**kwargs)


# --------------------------------------------------------------------------
# _validate_executor_catalog_if_requested / callback
# --------------------------------------------------------------------------
@pytest.mark.parametrize("val", ["", "0", "no", "random"])
def test_validate_catalog_disabled_no_import(monkeypatch, val):
    monkeypatch.setenv("OC_VALIDATE_CATALOG_AT_STARTUP", val)
    called = mock.Mock()
    # Ensure that if it tried to import startup, we'd notice — but it should not.
    with mock.patch.dict(
        "sys.modules",
        {"operations_center.executors.startup": SimpleNamespace(initialize_catalog=called)},
    ):
        assert main._validate_executor_catalog_if_requested() is None
    called.assert_not_called()


def test_validate_catalog_unset_returns_none(monkeypatch):
    monkeypatch.delenv("OC_VALIDATE_CATALOG_AT_STARTUP", raising=False)
    assert main._validate_executor_catalog_if_requested() is None


@pytest.mark.parametrize("val", ["1", "true", "YES", "True"])
def test_validate_catalog_enabled_calls_initialize(monkeypatch, val):
    monkeypatch.setenv("OC_VALIDATE_CATALOG_AT_STARTUP", val)
    init = mock.Mock()
    with mock.patch.dict(
        "sys.modules",
        {"operations_center.executors.startup": SimpleNamespace(initialize_catalog=init)},
    ):
        main._validate_executor_catalog_if_requested()
    init.assert_called_once_with(fail_fast=True)


def test_callback_invokes_validation(monkeypatch):
    seen = mock.Mock()
    monkeypatch.setattr(main, "_validate_executor_catalog_if_requested", seen)
    main._audit_app_callback()
    seen.assert_called_once_with()


# --------------------------------------------------------------------------
# cmd_run
# --------------------------------------------------------------------------
def test_cmd_run_success_table(monkeypatch):
    result = _make_result(succeeded=True)
    dispatch = mock.Mock(return_value=result)
    monkeypatch.setattr(main, "dispatch_managed_audit", dispatch)
    printed = mock.Mock()
    monkeypatch.setattr(main, "_print_dispatch_result", printed)

    with pytest.raises(typer.Exit) as ei:
        _run(monkeypatch)
    assert _exit_code(ei) == 0
    printed.assert_called_once_with(result)
    # request was built and log_dir defaulted to None
    req = dispatch.call_args.args[0]
    assert req.repo_id == "r"
    assert req.audit_type == "t"
    assert dispatch.call_args.kwargs["log_dir"] is None


def test_cmd_run_failure_exit_code_1(monkeypatch):
    result = _make_result(succeeded=False)
    monkeypatch.setattr(main, "dispatch_managed_audit", mock.Mock(return_value=result))
    monkeypatch.setattr(main, "_print_dispatch_result", mock.Mock())
    with pytest.raises(typer.Exit) as ei:
        _run(monkeypatch)
    assert _exit_code(ei) == 1


def test_cmd_run_json_output(monkeypatch):
    result = _make_result(succeeded=True)
    monkeypatch.setattr(main, "dispatch_managed_audit", mock.Mock(return_value=result))
    ps = mock.Mock()
    monkeypatch.setattr(main, "print_structured", ps)
    with pytest.raises(typer.Exit) as ei:
        _run(monkeypatch, json_output=True)
    assert _exit_code(ei) == 0
    ps.assert_called_once_with(main.console, result)


def test_cmd_run_log_dir_passed(monkeypatch):
    dispatch = mock.Mock(return_value=_make_result())
    monkeypatch.setattr(main, "dispatch_managed_audit", dispatch)
    monkeypatch.setattr(main, "_print_dispatch_result", mock.Mock())
    with pytest.raises(typer.Exit):
        _run(monkeypatch, log_dir="/var/logs", timeout=5.0, requested_by="me")
    assert dispatch.call_args.kwargs["log_dir"] == Path("/var/logs")
    req = dispatch.call_args.args[0]
    assert req.timeout_seconds == 5.0
    assert req.requested_by == "me"
    assert req.allow_unverified_command is False


def test_cmd_run_lock_conflict_exit_2(monkeypatch):
    monkeypatch.setattr(
        main,
        "dispatch_managed_audit",
        mock.Mock(side_effect=RepoLockAlreadyHeldError("held")),
    )
    with pytest.raises(typer.Exit) as ei:
        _run(monkeypatch)
    assert _exit_code(ei) == 2


def test_cmd_run_config_error_exit_3(monkeypatch):
    monkeypatch.setattr(
        main,
        "dispatch_managed_audit",
        mock.Mock(side_effect=AuditDispatchConfigError("bad config")),
    )
    with pytest.raises(typer.Exit) as ei:
        _run(monkeypatch)
    assert _exit_code(ei) == 3


# --------------------------------------------------------------------------
# cmd_status
# --------------------------------------------------------------------------
def test_cmd_status_table(monkeypatch):
    rs = _make_run_status({"field_a": "v", "field_b": None})
    monkeypatch.setattr(main, "load_run_status_entrypoint", mock.Mock(return_value=rs))
    # Should not raise and should not call typer.echo (table path)
    echo = mock.Mock()
    monkeypatch.setattr(main.typer, "echo", echo)
    main.cmd_status(run_status_path="/tmp/rs.json", json_output=False)
    echo.assert_not_called()


def test_cmd_status_json(monkeypatch):
    rs = _make_run_status()
    monkeypatch.setattr(main, "load_run_status_entrypoint", mock.Mock(return_value=rs))
    ps = mock.Mock()
    monkeypatch.setattr(main, "print_structured", ps)
    main.cmd_status(run_status_path="/tmp/rs.json", json_output=True)
    ps.assert_called_once_with(main.console, rs)


def test_cmd_status_not_found_exit_1(monkeypatch):
    monkeypatch.setattr(
        main,
        "load_run_status_entrypoint",
        mock.Mock(side_effect=RunStatusNotFoundError("nope")),
    )
    with pytest.raises(typer.Exit) as ei:
        main.cmd_status(run_status_path="/missing.json", json_output=False)
    assert _exit_code(ei) == 1


def test_cmd_status_contract_error_exit_2(monkeypatch):
    monkeypatch.setattr(
        main,
        "load_run_status_entrypoint",
        mock.Mock(side_effect=RunStatusContractError("bad")),
    )
    with pytest.raises(typer.Exit) as ei:
        main.cmd_status(run_status_path="/bad.json", json_output=False)
    assert _exit_code(ei) == 2


# --------------------------------------------------------------------------
# cmd_resolve_manifest
# --------------------------------------------------------------------------
def test_resolve_manifest_success(monkeypatch):
    rs = _make_run_status()
    monkeypatch.setattr(main, "load_run_status_entrypoint", mock.Mock(return_value=rs))
    resolve = mock.Mock(return_value=Path("/abs/manifest.json"))
    monkeypatch.setattr(main, "resolve_artifact_manifest_path", resolve)
    echo = mock.Mock()
    monkeypatch.setattr(main.typer, "echo", echo)
    main.cmd_resolve_manifest(run_status_path="/tmp/rs.json", base_dir=None)
    echo.assert_called_once_with("/abs/manifest.json")
    assert resolve.call_args.kwargs["base_dir"] is None


def test_resolve_manifest_with_base_dir(monkeypatch):
    rs = _make_run_status()
    monkeypatch.setattr(main, "load_run_status_entrypoint", mock.Mock(return_value=rs))
    resolve = mock.Mock(return_value=Path("/abs/m.json"))
    monkeypatch.setattr(main, "resolve_artifact_manifest_path", resolve)
    monkeypatch.setattr(main.typer, "echo", mock.Mock())
    main.cmd_resolve_manifest(run_status_path="/tmp/rs.json", base_dir="/base")
    assert resolve.call_args.kwargs["base_dir"] == Path("/base")


def test_resolve_manifest_not_found_exit_1(monkeypatch):
    monkeypatch.setattr(
        main,
        "load_run_status_entrypoint",
        mock.Mock(side_effect=RunStatusNotFoundError("x")),
    )
    with pytest.raises(typer.Exit) as ei:
        main.cmd_resolve_manifest(run_status_path="/x.json", base_dir=None)
    assert _exit_code(ei) == 1


def test_resolve_manifest_contract_exit_2(monkeypatch):
    monkeypatch.setattr(
        main,
        "load_run_status_entrypoint",
        mock.Mock(side_effect=RunStatusContractError("x")),
    )
    with pytest.raises(typer.Exit) as ei:
        main.cmd_resolve_manifest(run_status_path="/x.json", base_dir=None)
    assert _exit_code(ei) == 2


def test_resolve_manifest_missing_path_exit_3(monkeypatch):
    monkeypatch.setattr(
        main, "load_run_status_entrypoint", mock.Mock(return_value=_make_run_status())
    )
    monkeypatch.setattr(
        main,
        "resolve_artifact_manifest_path",
        mock.Mock(side_effect=ArtifactManifestPathMissingError("missing")),
    )
    with pytest.raises(typer.Exit) as ei:
        main.cmd_resolve_manifest(run_status_path="/x.json", base_dir=None)
    assert _exit_code(ei) == 3


def test_resolve_manifest_generic_exception_exit_3(monkeypatch):
    monkeypatch.setattr(
        main, "load_run_status_entrypoint", mock.Mock(return_value=_make_run_status())
    )
    monkeypatch.setattr(
        main,
        "resolve_artifact_manifest_path",
        mock.Mock(side_effect=ValueError("boom")),
    )
    with pytest.raises(typer.Exit) as ei:
        main.cmd_resolve_manifest(run_status_path="/x.json", base_dir=None)
    assert _exit_code(ei) == 3


# --------------------------------------------------------------------------
# cmd_dispatch (positional alias)
# --------------------------------------------------------------------------
def test_cmd_dispatch_delegates_to_run(monkeypatch):
    captured = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)
        raise typer.Exit(code=0)

    monkeypatch.setattr(main, "cmd_run", _fake_run)
    with pytest.raises(typer.Exit) as ei:
        main.cmd_dispatch(
            repo_id="repo",
            audit_type="type",
            allow_unverified=True,
            timeout=3.0,
            requested_by="caller",
            log_dir="/l",
            json_output=True,
        )
    assert _exit_code(ei) == 0
    assert captured == {
        "repo": "repo",
        "audit_type": "type",
        "allow_unverified": True,
        "timeout": 3.0,
        "requested_by": "caller",
        "log_dir": "/l",
        "json_output": True,
    }


# --------------------------------------------------------------------------
# cmd_list_active
# --------------------------------------------------------------------------
def _patch_store(monkeypatch, active):
    store = SimpleNamespace(
        list_active=lambda: active,
        read=lambda repo: None,
        release=lambda repo: None,
    )
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    return store


def test_list_active_json(monkeypatch):
    payload = _make_payload(oc_pid_alive=True, audit_pid_alive=False)
    _patch_store(monkeypatch, [payload])
    ps = mock.Mock()
    monkeypatch.setattr(main, "print_structured", ps)
    main.cmd_list_active(json_output=True)
    ps.assert_called_once()
    out = ps.call_args.args[1]
    assert out == [{**payload.to_json(), **payload.liveness_summary()}]
    assert "oc_pid_alive" in out[0]


def test_list_active_empty_text(monkeypatch):
    _patch_store(monkeypatch, [])
    printed = []
    monkeypatch.setattr(main.console, "print", lambda *a, **k: printed.append(a))
    main.cmd_list_active(json_output=False)
    assert any("no active audit locks" in str(p) for p in printed)


def test_list_active_table(monkeypatch):
    alive = _make_payload(oc_pid_alive=True, audit_pid_alive=True)
    _patch_store(monkeypatch, [alive])
    captured = []
    monkeypatch.setattr(main.console, "print", lambda obj=None, *a, **k: captured.append(obj))
    main.cmd_list_active(json_output=False)
    # A rich Table should have been printed
    assert any(isinstance(obj, main.Table) for obj in captured)


def test_list_active_table_bad_started_at(monkeypatch):
    # Invalid started_at triggers ValueError -> age "?"; and audit_pid None branch.
    bad = _make_payload(started_at="not-a-date", audit_pid=None)
    _patch_store(monkeypatch, [bad])
    captured = []
    monkeypatch.setattr(main.console, "print", lambda obj=None, *a, **k: captured.append(obj))
    main.cmd_list_active(json_output=False)
    assert any(isinstance(obj, main.Table) for obj in captured)


# --------------------------------------------------------------------------
# cmd_watch
# --------------------------------------------------------------------------
def test_watch_no_lock_exit_1(monkeypatch):
    store = SimpleNamespace(read=lambda repo: None)
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(main.console, "print", mock.Mock())
    with pytest.raises(typer.Exit) as ei:
        main.cmd_watch(repo="r")
    assert _exit_code(ei) == 1


def test_watch_streams_until_terminal(monkeypatch):
    payload = _make_payload()
    store = SimpleNamespace(read=lambda repo: payload)
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    printed = []
    monkeypatch.setattr(main.console, "print", lambda *a, **k: printed.append(a))

    snaps = [
        SimpleNamespace(status="running", current_phase="p1", path="/p", is_terminal=False),
        SimpleNamespace(status="done", current_phase=None, path="/p", is_terminal=True),
        SimpleNamespace(status="extra", current_phase="x", path="/p", is_terminal=False),
    ]
    poll = mock.Mock(return_value=iter(snaps))
    fake_watcher = SimpleNamespace(poll_run_status=poll)
    with mock.patch.dict(
        "sys.modules",
        {"operations_center.audit_dispatch.watcher": fake_watcher},
    ):
        main.cmd_watch(repo="r", poll_interval=0.1, timeout=1.0)
    # Stopped at terminal snapshot: "extra" should never be printed.
    flat = " ".join(str(p) for p in printed)
    assert "running" in flat
    assert "done" in flat
    assert "extra" not in flat
    # poll called with discovered output dir + run id
    assert poll.call_args.args[0] == Path("/tmp/run-dir")
    assert poll.call_args.args[1] == "run-1"


# --------------------------------------------------------------------------
# cmd_unlock
# --------------------------------------------------------------------------
def test_unlock_no_lock_exit_0(monkeypatch):
    store = SimpleNamespace(read=lambda repo: None, release=mock.Mock())
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(main.console, "print", mock.Mock())
    with pytest.raises(typer.Exit) as ei:
        main.cmd_unlock(repo="r", force=False)
    assert _exit_code(ei) == 0
    store.release.assert_not_called()


def test_unlock_force_releases(monkeypatch):
    payload = _make_payload(is_alive=True)
    release = mock.Mock()
    store = SimpleNamespace(read=lambda repo: payload, release=release)
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(main.console, "print", mock.Mock())
    with pytest.raises(typer.Exit) as ei:
        main.cmd_unlock(repo="r", force=True)
    assert _exit_code(ei) == 0
    release.assert_called_once_with("r")


def test_unlock_alive_blocks_exit_1(monkeypatch):
    payload = _make_payload(is_alive=True, oc_pid_alive=True)
    release = mock.Mock()
    store = SimpleNamespace(read=lambda repo: payload, release=release)
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(main.console, "print", mock.Mock())
    with pytest.raises(typer.Exit) as ei:
        main.cmd_unlock(repo="r", force=False)
    assert _exit_code(ei) == 1
    release.assert_not_called()


def test_unlock_stale_releases(monkeypatch):
    payload = _make_payload(is_alive=False)
    release = mock.Mock()
    store = SimpleNamespace(read=lambda repo: payload, release=release)
    monkeypatch.setattr(main, "get_global_registry", lambda: SimpleNamespace(store=store))
    printed = []
    monkeypatch.setattr(main.console, "print", lambda *a, **k: printed.append(a))
    # No --force, stale lock -> releases without raising Exit
    main.cmd_unlock(repo="r", force=False)
    release.assert_called_once_with("r")
    assert any("Released stale lock" in str(p) for p in printed)


# --------------------------------------------------------------------------
# _print_dispatch_result
# --------------------------------------------------------------------------
def test_print_dispatch_result_success(monkeypatch):
    captured = []
    monkeypatch.setattr(main.console, "print", lambda obj=None, *a, **k: captured.append(obj))
    main._print_dispatch_result(_make_result(succeeded=True))
    assert any(isinstance(o, main.Table) for o in captured)


def test_print_dispatch_result_failure_with_error(monkeypatch):
    captured = []
    monkeypatch.setattr(main.console, "print", lambda obj=None, *a, **k: captured.append(obj))
    res = _make_result(
        succeeded=False,
        run_id=None,
        failure_kind=SimpleNamespace(value="timeout"),
        process_exit_code=None,
        error="kaboom",
    )
    main._print_dispatch_result(res)
    assert any(isinstance(o, main.Table) for o in captured)


# --------------------------------------------------------------------------
# Module wiring sanity
# --------------------------------------------------------------------------
def test_index_commands_mounted():
    names = {c.name for c in main.app.registered_commands}
    # flat-mounted artifact-index commands present
    assert {"index", "index-show", "get-artifact"} <= names


def test_local_commands_registered():
    names = {c.name for c in main.app.registered_commands}
    assert {
        "run",
        "status",
        "resolve-manifest",
        "dispatch",
        "list-active",
        "watch",
        "unlock",
    } <= names
